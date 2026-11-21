"""A governed memory service: ingest -> extract -> govern -> consolidate -> store.

It implements ADK's BaseMemoryService, the same interface as
VertexAiMemoryBankService, so the agent code does not change if you swap this
for Memory Bank in production. What this version makes visible is the part you
own either way: what is allowed into memory, and how new facts replace old
ones.

  Extract       an LLM (or offline rules) proposes candidate memories. It is
                allowed to be noisy.
  Govern        deterministic code decides what may be stored: no sensitive
                identifiers, no inferences, PII scrubbed. Redact on write.
  Consolidate   one record per fact. A newer statement on a single-valued
                topic supersedes the old one instead of piling up beside it.
  Store         every row is scoped to one user; every read filters on it.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Optional

from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types
from pydantic import BaseModel

from . import config

NAMESPACE = "dala-wallet"  # one memory space for both demo agents

ALLOWED_TOPICS = {"language_preference", "contact_channel", "personal_fact", "task_outcome", "explicit_instruction"}
SINGLE_VALUED = {"language_preference", "contact_channel"}

_PII_PATTERNS = [
    (re.compile(r"\b0\d{3}\s?\d{3}\s?\d{3}\b|\+254\s?\d{3}\s?\d{3}\s?\d{3}"), "[phone]"),
    (re.compile(r"\b\d{13,19}\b"), "[card]"),
    (re.compile(r"\b\d{7,8}\b"), "[id-number]"),
]


class Candidate(BaseModel):
  topic: str
  key: str
  fact: str
  source: str  # "stated" or "inferred"
  evidence: str


def _now() -> str:
  return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class GovernedMemoryService(BaseMemoryService):

  def __init__(self, db_path: Path, extractor_model: Optional[str] = None):
    self._db_path = Path(db_path)
    self._extractor_model = extractor_model  # None -> offline rules
    self.last_reports: dict[str, dict] = {}
    with self._db() as db:
      db.execute("""CREATE TABLE IF NOT EXISTS memories(
          id TEXT PRIMARY KEY, namespace TEXT, user_id TEXT, topic TEXT, key TEXT,
          fact TEXT, source TEXT, status TEXT, created_at TEXT, updated_at TEXT,
          superseded_by TEXT, lineage TEXT)""")

  def _db(self) -> sqlite3.Connection:
    conn = sqlite3.connect(self._db_path)
    conn.row_factory = sqlite3.Row
    return conn

  # ------------------------------------------------------------ ADK interface

  async def add_session_to_memory(self, session) -> None:
    report = await self.run_pipeline(session)
    self.last_reports[session.id] = report

  async def search_memory(self, *, app_name: str, user_id: str, query: str) -> SearchMemoryResponse:
    rows = self.active(user_id)  # tenant scope: WHERE user_id = ?
    words = {w for w in re.findall(r"[a-z]+", query.lower()) if len(w) > 2}
    scored = []
    for r in rows:
      text = (r["fact"] + " " + r["key"]).lower()
      score = sum(1 for w in words if w[:5] in text)
      if score or not words:
        scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return SearchMemoryResponse(memories=[
        MemoryEntry(
            id=r["id"], author="memory", timestamp=r["updated_at"],
            content=types.Content(role="user", parts=[types.Part(text=r["fact"])]),
        )
        for _, r in scored[:5]
    ])

  # ------------------------------------------------------------ reads

  def active(self, user_id: str) -> list[sqlite3.Row]:
    with self._db() as db:
      return db.execute(
          "SELECT * FROM memories WHERE namespace=? AND user_id=? AND status='active' ORDER BY updated_at",
          (NAMESPACE, user_id)).fetchall()

  def all_rows(self, user_id: str) -> list[dict]:
    with self._db() as db:
      rows = db.execute("SELECT * FROM memories WHERE namespace=? AND user_id=? ORDER BY created_at",
                        (NAMESPACE, user_id)).fetchall()
    return [dict(r) | {"lineage": json.loads(r["lineage"] or "[]")} for r in rows]

  def profile_text(self, user_id: str, display_name: str) -> str:
    """The always-on block: small, fixed shape, no search, no guessing."""
    lines = [f"name: {display_name}"]
    rows = self.active(user_id)
    for topic, label in (("language_preference", "language"), ("contact_channel", "contact")):
      row = next((r for r in rows if r["topic"] == topic), None)
      if row:
        lines.append(f"{label}: {row['fact']}")
    facts = [r["fact"] for r in rows if r["topic"] in ("personal_fact", "explicit_instruction")]
    for f in facts[:3]:
      lines.append(f"note: {f}")
    outcomes = [r["fact"] for r in rows if r["topic"] == "task_outcome"][-2:]
    for o in outcomes:
      lines.append(f"recent: {o}")
    return "<user_profile>\n" + "\n".join(lines) + "\n</user_profile>"

  # ------------------------------------------------------------ pipeline

  async def run_pipeline(self, session) -> dict:
    user_id = session.user_id
    transcript, user_msgs, tool_facts = self._ingest(session)
    if self._extractor_model:
      extractor = self._extractor_model
      try:
        candidates = await self._extract_llm(transcript)
      except Exception as e:  # network trouble: fall back, and say so
        extractor = f"offline rules (LLM failed: {type(e).__name__})"
        candidates = self._extract_rules(user_msgs)
    else:
      extractor = "offline rules"
      candidates = self._extract_rules(user_msgs)
    candidates += tool_facts

    governance, kept = self._govern(candidates)
    ops = self._consolidate(user_id, session.id, kept)
    return {
        "session_id": session.id,
        "user_id": user_id,
        "extractor": extractor,
        "events_ingested": len(session.events),
        "user_messages": len(user_msgs),
        "extracted": [c.model_dump() for c in candidates],
        "governance": governance,
        "consolidation": ops,
        "stored": [dict(r) for r in self.active(user_id)],
    }

  def _ingest(self, session) -> tuple[str, list[str], list[Candidate]]:
    lines, user_msgs, tool_facts = [], [], []
    for ev in session.events:
      if ev.actions and ev.actions.compaction:
        continue
      for p in (ev.content.parts if ev.content else None) or []:
        if p.text and not p.thought:
          lines.append(f"{ev.author}: {p.text}")
          if ev.author == "user":
            user_msgs.append(p.text)
        if p.function_response is not None and p.function_response.name == "request_reversal":
          resp = p.function_response.response or {}
          data = resp.get("untrusted_data", resp) if isinstance(resp, dict) else {}
          if data.get("status") == "requested":
            day = dt.date.today().strftime("%d %b")
            tool_facts.append(Candidate(
                topic="task_outcome", key=f"reversal:{data.get('transaction_id')}",
                fact=f"Requested reversal of KES {data.get('amount_kes'):,} ({data.get('transaction_id')}, ref {data.get('reference')}) on {day}",
                source="tool", evidence="request_reversal tool result"))
    return "\n".join(lines), user_msgs, tool_facts

  async def _extract_llm(self, transcript: str) -> list[Candidate]:
    from google import genai

    prompt = (
        "You extract long-term memories about a customer from a support conversation.\n"
        "Return a JSON list. Each item has: topic, key, fact, source, evidence.\n"
        "topic is one of: language_preference, contact_channel, personal_fact, task_outcome,\n"
        "explicit_instruction, sensitive_identifier, inferred_trait.\n"
        "source is \"stated\" if the customer said it directly, \"inferred\" if you concluded it.\n"
        "Also list any ID numbers, PINs, card or phone numbers the customer gave (topic\n"
        "sensitive_identifier) and anything you infer about their situation (topic\n"
        "inferred_trait, source inferred): the system decides what to keep.\n"
        "key is a short stable identifier for the fact, e.g. \"language\" or \"market_stall\".\n"
        "fact is one short sentence in English. evidence quotes the customer.\n"
        "Skip small talk and one-off questions. Skip constraints the customer limits to this\n"
        "conversation or to a temporary situation (\"my phone has no data today\"): those are\n"
        "pinned in the session, not remembered.\n\nCONVERSATION:\n" + transcript[-12000:]
    )
    client = genai.Client()
    resp = await client.aio.models.generate_content(
        model=self._extractor_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=list[Candidate], temperature=0),
    )
    items = resp.parsed or json.loads(resp.text or "[]")
    return [c if isinstance(c, Candidate) else Candidate(**c) for c in items]

  def _extract_rules(self, user_msgs: list[str]) -> list[Candidate]:
    out: list[Candidate] = []
    for m in user_msgs:
      low = m.lower()
      if re.search(r"(reply|talk|speak|respond|answer|write).{0,30}(swahili)|kwa kiswahili", low):
        out.append(Candidate(topic="language_preference", key="language", fact="Prefers replies in Swahili",
                             source="stated", evidence=m))
      elif re.search(r"(reply|talk|speak|respond).{0,30}english", low):
        out.append(Candidate(topic="language_preference", key="language", fact="Prefers replies in English",
                             source="stated", evidence=m))
      temporary = re.search(r"no data|today|right now|this conversation|for now|kwa sasa", low)
      if re.search(r"sms only|only sms|prefer sms|by sms", low) and not temporary:
        out.append(Candidate(topic="contact_channel", key="channel", fact="Wants contact by SMS only",
                             source="stated", evidence=m))
      idm = re.search(r"\b(id|kitambulisho)\b[^0-9]{0,25}(\d{7,8})", low)
      if idm:
        out.append(Candidate(topic="sensitive_identifier", key="national_id",
                             fact=f"National ID number is {idm.group(2)}", source="stated", evidence=m))
      if re.search(r"money is tight|struggling|broke|hard month|sina pesa", low):
        out.append(Candidate(topic="inferred_trait", key="finances",
                             fact="May be in financial difficulty", source="inferred", evidence=m))
      stall = re.search(r"i (run|have|own) (a|an) ([a-z ]{3,40}?)(?: at| in|[.,]|$)", low)
      if stall:
        out.append(Candidate(topic="personal_fact", key="occupation",
                             fact=f"Runs {stall.group(2)} {stall.group(3).strip()}", source="stated", evidence=m))
      if low.startswith("remember"):
        out.append(Candidate(topic="explicit_instruction", key="remember:" + low[8:40].strip(),
                             fact=m.strip(), source="stated", evidence=m))
    return out

  def _govern(self, candidates: list[Candidate]) -> tuple[list[dict], list[Candidate]]:
    decisions, kept = [], []
    for c in candidates:
      if c.topic == "sensitive_identifier":
        decisions.append({"fact": c.fact, "decision": "blocked",
                          "reason": "Sensitive identifier: never persisted (redact on write)"})
        continue
      if c.source not in ("stated", "tool") or c.topic == "inferred_trait":
        decisions.append({"fact": c.fact, "decision": "blocked",
                          "reason": "Inferred by the model, never said by the customer"})
        continue
      if c.topic not in ALLOWED_TOPICS:
        decisions.append({"fact": c.fact, "decision": "blocked", "reason": f"Topic '{c.topic}' not allowed"})
        continue
      scrubbed = c.fact
      for pattern, repl in _PII_PATTERNS:
        scrubbed = pattern.sub(repl, scrubbed)
      if scrubbed != c.fact:
        decisions.append({"fact": c.fact, "decision": "redacted", "reason": f"PII removed: \"{scrubbed}\""})
        c = c.model_copy(update={"fact": scrubbed})
      else:
        why = "Recorded from a completed tool call" if c.source == "tool" else "Stated by the customer"
        decisions.append({"fact": c.fact, "decision": "kept", "reason": f"{why}, allowed topic"})
      kept.append(c)
    return decisions, kept

  def _consolidate(self, user_id: str, session_id: str, kept: list[Candidate]) -> list[dict]:
    ops = []
    with self._db() as db:
      for c in kept:
        if c.topic in SINGLE_VALUED:
          existing = db.execute(
              "SELECT * FROM memories WHERE namespace=? AND user_id=? AND topic=? AND status='active'",
              (NAMESPACE, user_id, c.topic)).fetchone()
        else:
          existing = db.execute(
              "SELECT * FROM memories WHERE namespace=? AND user_id=? AND key=? AND status='active'",
              (NAMESPACE, user_id, c.key)).fetchone()
        if existing and existing["fact"] == c.fact:
          lineage = json.loads(existing["lineage"] or "[]") + [session_id]
          db.execute("UPDATE memories SET updated_at=?, lineage=? WHERE id=?",
                     (_now(), json.dumps(lineage), existing["id"]))
          ops.append({"op": "NOOP", "fact": c.fact, "previous": existing["fact"],
                      "why": "Already known; refreshed timestamp and lineage"})
          continue
        new_id = uuid.uuid4().hex[:8]
        lineage = [session_id]
        if existing and c.topic in SINGLE_VALUED:
          db.execute("UPDATE memories SET status='superseded', superseded_by=?, updated_at=? WHERE id=?",
                     (new_id, _now(), existing["id"]))
          ops.append({"op": "SUPERSEDE", "fact": c.fact, "previous": existing["fact"],
                      "why": "Newer statement on a single-valued topic replaces the old one"})
        elif existing:
          lineage = json.loads(existing["lineage"] or "[]") + [session_id]
          db.execute("UPDATE memories SET status='merged', superseded_by=?, updated_at=? WHERE id=?",
                     (new_id, _now(), existing["id"]))
          ops.append({"op": "UPDATE", "fact": c.fact, "previous": existing["fact"],
                      "why": "Same fact key: updated in place, lineage kept"})
        else:
          ops.append({"op": "ADD", "fact": c.fact, "previous": None, "why": "New fact"})
        db.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (new_id, NAMESPACE, user_id, c.topic, c.key, c.fact, c.source, "active",
                    _now(), _now(), None, json.dumps(lineage)))
    return ops

  # ------------------------------------------------------------ admin

  def reset(self, seed: bool = True) -> None:
    with self._db() as db:
      db.execute("DELETE FROM memories")
    if seed:
      self.seed()

  def seed(self) -> None:
    """Memories from 'earlier sessions', so consolidation has something to do."""
    with self._db() as db:
      for uid, topic, key, fact in [
          ("achieng", "language_preference", "language", "Prefers replies in English"),
          ("achieng", "personal_fact", "occupation", "Runs a fish stall at Kibuye market"),
          ("otieno", "language_preference", "language", "Prefers replies in English"),
      ]:
        db.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex[:8], NAMESPACE, uid, topic, key, fact, "stated", "active",
                    "2026-10-02T09:00:00+00:00", "2026-10-02T09:00:00+00:00", None,
                    json.dumps(["earlier-session-oct-02"])))

  def delete_user(self, user_id: str) -> int:
    """Right to erasure: remove every row for this user, including history."""
    with self._db() as db:
      cur = db.execute("DELETE FROM memories WHERE namespace=? AND user_id=?", (NAMESPACE, user_id))
      return cur.rowcount

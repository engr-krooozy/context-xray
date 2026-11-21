"""Context X-ray: record exactly what the model received on every call.

capture_request() is called by the model wrapper (see models.py) at the last
moment before the request leaves for Gemini, after every ADK processor,
callback and compaction step has run. It splits the request into the same
blocks as the "what is in the window" slide:

  blue    configured   system instruction, framework additions, tool definitions
  green   fetched      loaded skills, tool results, memory
  yellow  accumulated  session history, compaction summaries, pinned constraints
  red     user         the customer's message on this turn
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from google.genai import types

# ---------------------------------------------------------------- turn context


@dataclass
class TurnContext:
  session_id: str
  user_id: str
  mode: str
  turn: int
  user_text: str
  own_instruction: str
  calls: list[dict] = field(default_factory=list)
  guards: list[dict] = field(default_factory=list)


CURRENT: contextvars.ContextVar[Optional[TurnContext]] = contextvars.ContextVar(
    "xray_turn", default=None
)

# session_id -> previous prefix hash, so we can show cache-breaking changes
_LAST_PREFIX: dict[str, tuple[str, list[str]]] = {}


def estimate_tokens(text: str) -> int:
  """~4 characters per token. Replaced by the API's exact total when live."""
  return max(1, math.ceil(len(text) / 4)) if text else 0


# ---------------------------------------------------------------- helpers

_CATALOG_RE = re.compile(r"<available_skills>.*?</available_skills>", re.S)
_PROFILE_RE = re.compile(r"<user_profile>.*?</user_profile>", re.S)
_PINS_RE = re.compile(r"<pinned_constraints>.*?</pinned_constraints>", re.S)
_FRAMEWORK_USER_TEXT = (
    "Continue processing previous requests as instructed",
    "Handle the requests as specified in the System Instruction",
)
COMPACTION_MARKERS = ("[Compacted summary", "[Earlier messages removed")


def _system_text(cfg: types.GenerateContentConfig | None) -> str:
  if not cfg or cfg.system_instruction is None:
    return ""
  si = cfg.system_instruction
  if isinstance(si, str):
    return si
  if isinstance(si, types.Content):
    return "\n".join(p.text or "" for p in si.parts or [])
  if isinstance(si, list):
    return "\n".join(x if isinstance(x, str) else getattr(x, "text", "") or "" for x in si)
  return str(si)


def _declarations(cfg: types.GenerateContentConfig | None) -> list[dict]:
  out: list[dict] = []
  for tool in (cfg.tools or []) if cfg else []:
    for fd in getattr(tool, "function_declarations", None) or []:
      out.append(fd.model_dump(mode="json", exclude_none=True))
  return out


def _short(obj: Any, limit: int = 4000) -> str:
  text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1, default=str)
  return text if len(text) <= limit else text[:limit] + f"\n... [{len(text) - limit} more chars]"


def _block(kind: str, group: str, label: str, text: str, **flags) -> dict:
  return {
      "kind": kind,
      "group": group,
      "label": label,
      "chars": len(text),
      "tokens_est": estimate_tokens(text),
      "text": _short(text),
      "flags": {k: v for k, v in flags.items() if v},
  }


# ---------------------------------------------------------------- classify


def classify_request(llm_request, own_instruction: str = "") -> dict:
  """Turns an LlmRequest into labelled blocks with token estimates."""
  blocks: list[dict] = []

  # 1. System instruction: yours vs. added by the framework vs. skill catalog
  system = _system_text(llm_request.config)
  catalog = _CATALOG_RE.search(system)
  rest = _CATALOG_RE.sub("", system)
  if own_instruction and own_instruction.strip() and own_instruction.strip() in rest:
    rest = rest.replace(own_instruction.strip(), "", 1)
    blocks.append(_block("system_own", "configured", "System instruction (yours)", own_instruction.strip()))
  pasted = re.search(r"## All procedures.*?(?=\n\nYou are an agent|\Z)", rest, re.S)
  if pasted:
    rest = rest.replace(pasted.group(0), "", 1)
    blocks.append(_block("skills_pasted", "configured", "Every procedure, pasted in (always loaded)", pasted.group(0)))
  if rest.strip():
    blocks.append(_block("system_framework", "configured", "Instructions added by ADK", rest.strip(), framework=True))
  if catalog:
    n = catalog.group(0).count("<skill>")
    blocks.append(_block("skill_catalog", "configured", f"Skill index ({n} one-liners)", catalog.group(0)))

  # 2. Tool definitions
  decls = _declarations(llm_request.config)
  if decls:
    blocks.append(_block(
        "tools", "configured", f"Tool definitions ({len(decls)})",
        json.dumps(decls, ensure_ascii=False), names=[d.get("name") for d in decls],
    ))

  # 3. Contents
  contents = list(llm_request.contents or [])
  # The customer's message for this turn is the last plain user text.
  current_idx: Optional[tuple[int, int]] = None
  for ci, content in enumerate(contents):
    if content.role != "user":
      continue
    for pi, part in enumerate(content.parts or []):
      t = part.text or ""
      if (t and not part.thought and "<user_profile>" not in t and "<pinned_constraints>" not in t
          and "<PAST_CONVERSATIONS>" not in t and not t.startswith(_FRAMEWORK_USER_TEXT)):
        current_idx = (ci, pi)

  # Pair each tool call with its result so a round trip is one block.
  answered: set[str] = set()
  for content in contents:
    for part in content.parts or []:
      if part.function_response is not None:
        answered.add(part.function_response.id or part.function_response.name or "")
  pending_calls: dict[str, str] = {}

  history: list[str] = []
  history_msgs = 0
  seen_current = False

  def flush_history():
    nonlocal history, history_msgs
    if history:
      noun = "message" if history_msgs == 1 else "messages"
      label = (f"This turn so far ({history_msgs} {noun})" if seen_current
               else f"Session history ({history_msgs} {noun})")
      blocks.append(_block("history", "accumulated", label, "\n".join(history)))
      history, history_msgs = [], 0

  for ci, content in enumerate(contents):
    for pi, part in enumerate(content.parts or []):
      if part.thought:
        continue
      t = part.text or ""
      if (ci, pi) == current_idx:
        flush_history()
        seen_current = True
        blocks.append(_block("user", "user", "Customer's message (this turn)", t))
        continue
      if t and content.role == "user":
        prof, pins = _PROFILE_RE.search(t), _PINS_RE.search(t)
        if prof or pins:
          flush_history()
          if prof:
            blocks.append(_block("memory_profile", "fetched", "Memory: always-on profile", prof.group(0)))
          if pins:
            blocks.append(_block("pinned", "accumulated", "Pinned constraints", pins.group(0), pinned=True))
          leftover = _PINS_RE.sub("", _PROFILE_RE.sub("", t)).strip()
          if leftover:
            blocks.append(_block("framework_fence", "configured", "ADK wrapper around your per-turn instruction",
                                 leftover, framework=True))
          continue
        if "<PAST_CONVERSATIONS>" in t:
          flush_history()
          blocks.append(_block("memory_preload", "fetched", "Memory: preloaded", t))
          continue
        if t.startswith(_FRAMEWORK_USER_TEXT):
          flush_history()
          blocks.append(_block("framework_nudge", "configured", "Nudge added by ADK", t, framework=True))
          continue
      if t and content.role == "model" and t.startswith(COMPACTION_MARKERS):
        flush_history()
        dropped = t.startswith("[Earlier messages removed")
        label = "Compaction: old turns dropped" if dropped else "Compaction summary"
        blocks.append(_block("compaction", "accumulated", label, t, compaction=True))
        continue
      if part.function_call is not None:
        fc = part.function_call
        call_text = f"call: {fc.name}({json.dumps(fc.args or {}, ensure_ascii=False, default=str)})"
        key = fc.id or fc.name or ""
        if key in answered and fc.name != "adk_request_confirmation":
          pending_calls[key] = call_text
          continue
        history.append(f"{content.role} {call_text}")
        history_msgs += 1
        continue
      if part.function_response is not None:
        fr = part.function_response
        resp = fr.response or {}
        call_text = pending_calls.pop(fr.id or fr.name or "", f"call: {fr.name}(...)")
        body = call_text + "\nresult: " + _short(resp)
        if fr.name == "load_skill":
          flush_history()
          name = resp.get("skill_name", "?") if isinstance(resp, dict) else "?"
          blocks.append(_block("skill_body", "fetched", f"Loaded skill: {name}", body))
          continue
        if fr.name == "adk_request_confirmation":
          history.append(f"customer approval -> {_short(resp, 200)}")
          history_msgs += 1
          continue
        flush_history()
        untrusted = isinstance(resp, dict) and "untrusted_data" in resp
        blocked = "BLOCKED_BY_POLICY" in json.dumps(resp, default=str)
        kind = "memory_search" if fr.name == "load_memory" else "tool_result"
        label = ("Memory: just-in-time search" if kind == "memory_search" else f"Tool result: {fr.name}")
        blocks.append(_block(kind, "fetched", label, body, untrusted=untrusted, blocked=blocked))
        continue
      if t:
        history.append(f"{content.role}: {t}")
        history_msgs += 1
  flush_history()

  total_est = sum(b["tokens_est"] for b in blocks)
  prefix_src = system + json.dumps(decls, sort_keys=True)
  return {
      "blocks": blocks,
      "tools_offered": [d.get("name") for d in decls],
      "total_est": total_est,
      "prefix_hash": hashlib.sha1(prefix_src.encode()).hexdigest()[:10],
      "prefix_tokens_est": estimate_tokens(system) + estimate_tokens(json.dumps(decls)),
  }


# ---------------------------------------------------------------- recording


def capture_request(llm_request) -> Optional[dict]:
  ctx = CURRENT.get()
  if ctx is None:
    return None
  snap = classify_request(llm_request, ctx.own_instruction)
  prev = _LAST_PREFIX.get(ctx.session_id)
  tools_now = snap["tools_offered"]
  if prev is None:
    prefix_note = "first call in this session"
  elif prev[0] == snap["prefix_hash"]:
    prefix_note = "unchanged: cacheable"
  else:
    added = [t for t in tools_now if t not in prev[1]]
    removed = [t for t in prev[1] if t not in tools_now]
    bits = []
    if added:
      bits.append("tools added: " + ", ".join(added))
    if removed:
      bits.append("tools removed: " + ", ".join(removed))
    prefix_note = "changed (" + ("; ".join(bits) or "instruction changed") + ")"
  _LAST_PREFIX[ctx.session_id] = (snap["prefix_hash"], tools_now)
  call = {
      "call": len(ctx.calls) + 1,
      "at": time.time(),
      "prefix_note": prefix_note,
      **snap,
      "usage": None,
      "response": None,
  }
  ctx.calls.append(call)
  return call


def record_response(call: Optional[dict], llm_response) -> None:
  if call is None or llm_response is None:
    return
  um = getattr(llm_response, "usage_metadata", None)
  estimated = bool((getattr(llm_response, "custom_metadata", None) or {}).get("xray_estimated_usage"))
  if um is not None:
    call["usage"] = {
        "prompt_tokens": um.prompt_token_count,
        "cached_tokens": getattr(um, "cached_content_token_count", None),
        "output_tokens": um.candidates_token_count,
        "estimated": estimated,
    }
    exact = um.prompt_token_count
    if exact and call["total_est"] and not estimated:
      scale = exact / call["total_est"]
      for b in call["blocks"]:
        b["tokens"] = max(1, round(b["tokens_est"] * scale)) if b["tokens_est"] else 0
      call["total_exact"] = exact
  parts = (llm_response.content.parts if llm_response.content else None) or []
  out = []
  for p in parts:
    if p.thought:
      continue
    if p.function_call is not None:
      out.append({"function_call": p.function_call.name, "args": p.function_call.args})
    elif p.text:
      out.append({"text": p.text})
  call["response"] = out


def record_guard(tool_name: str, args: dict, verdict: str) -> None:
  ctx = CURRENT.get()
  if ctx is not None:
    ctx.guards.append({"tool": tool_name, "args": args, "verdict": verdict})

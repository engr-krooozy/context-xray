"""Model wrappers.

XrayGemini   the real Gemini model, plus one line that records the request.
ScriptedLlm  offline stand-in for venue wifi failures. Its replies are
             scripted; everything around it (ADK flow, skills, callbacks,
             memory pipeline, compaction, X-ray) is the same code as live.
"""

from __future__ import annotations

import json
import re
from typing import AsyncGenerator, Optional

from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.models._capabilities import LlmCapabilities
from google.genai import types

from . import trace


class XrayGemini(Gemini):
  """Gemini, with the assembled request recorded right before it is sent."""

  async def generate_content_async(
      self, llm_request: LlmRequest, stream: bool = False
  ) -> AsyncGenerator[LlmResponse, None]:
    try:  # apply ADK's own last-mile tweaks first so we record what is sent
      await self._preprocess_request(llm_request)
      self._maybe_append_user_content(llm_request)
    except Exception:  # pragma: no cover - private API may move
      pass
    call = trace.capture_request(llm_request)
    async for response in super().generate_content_async(llm_request, stream):
      if not response.partial:
        trace.record_response(call, response)
      yield response


# ---------------------------------------------------------------- offline model

_SW_HINTS = ("habari", "nimetuma", "tafadhali", "asante", "ndiyo", "sawa", "pesa", "namba", "nisaidie", "kwa nini", "mbaya")
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]
_SW_MONTHS = {"januari": 0, "februari": 1, "machi": 2, "aprili": 3, "mei": 4, "juni": 5, "julai": 6,
              "agosti": 7, "septemba": 8, "oktoba": 9, "novemba": 10, "desemba": 11}


def _text(part: types.Part) -> str:
  return part.text or ""


def _is_special(t: str) -> bool:
  return ("<user_profile>" in t or "<pinned_constraints>" in t or "<PAST_CONVERSATIONS>" in t
          or t.startswith(trace._FRAMEWORK_USER_TEXT))


def _amount(t: str) -> Optional[int]:
  m = re.search(r"(\d{1,3}(?:,\d{3})+|\d+)", t)
  return int(m.group(1).replace(",", "")) if m else None


def _month(t: str) -> str:
  low = t.lower()
  for i, m in enumerate(_MONTHS):
    if m in low:
      return f"{m.title()} 2026"
  for m, i in _SW_MONTHS.items():
    if m in low:
      return f"{_MONTHS[i].title()} 2026"
  return "October 2026"


def _fn_call(name: str, args: dict) -> LlmResponse:
  return LlmResponse(content=types.Content(role="model", parts=[
      types.Part(function_call=types.FunctionCall(name=name, args=args))]))


def _say(text: str) -> LlmResponse:
  return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


class _View:
  """What the scripted model can 'see': only what is in the request."""

  def __init__(self, req: LlmRequest):
    self.req = req
    self.contents = list(req.contents or [])
    self.tools = set(trace.classify_request(req)["tools_offered"])
    self.all_text = "\n".join(_text(p) for c in self.contents for p in c.parts or [])
    self.profile = ""
    m = trace._PROFILE_RE.search(self.all_text)
    if m:
      self.profile = m.group(0)
    self.pins = ""
    m = trace._PINS_RE.search(self.all_text)
    if m:
      self.pins = m.group(0)
    # index of the current user message
    self.user_text, self.user_idx = "", -1
    for i, c in enumerate(self.contents):
      if c.role == "user":
        for p in c.parts or []:
          t = _text(p)
          if t and not _is_special(t):
            self.user_text, self.user_idx = t, i
    self.after_user = self.contents[self.user_idx + 1:] if self.user_idx >= 0 else []
    # ADK may append the dynamic instruction after the latest tool result, so
    # look at the last content that is not an injected instruction.
    last = None
    for c in reversed(self.contents):
      if all(_is_special(_text(p)) for p in c.parts or [] if p.text) and not any(
          p.function_response is not None or p.function_call is not None for p in c.parts or []):
        continue
      last = c
      break
    self.last_response = None
    if last is not None:
      for p in last.parts or []:
        if p.function_response is not None:
          self.last_response = p.function_response

  @property
  def swahili(self) -> bool:
    low = self.user_text.lower()
    return "swahili" in self.profile.lower() or any(h in low for h in _SW_HINTS)

  @property
  def name(self) -> str:
    m = re.search(r"name:\s*([A-Za-z]+)", self.profile)
    return m.group(1) if m else ""

  def sms_required(self) -> bool:
    """Only true if an SMS-only constraint is visible somewhere in the window."""
    hay = (self.pins + "\n" + self.all_text).lower()
    return bool(re.search(r"sms only|only sms|sms pekee|no data|by sms", hay))

  def skill_loaded(self, name: str) -> bool:
    for c in self.contents:
      for p in c.parts or []:
        fr = p.function_response
        if fr is not None and fr.name == "load_skill" and isinstance(fr.response, dict) \
            and fr.response.get("skill_name") == name:
          return True
    return False

  def found_transaction(self) -> Optional[dict]:
    for c in reversed(self.contents):
      for p in c.parts or []:
        fr = p.function_response
        if fr is not None and fr.name == "find_transaction":
          data = fr.response.get("untrusted_data", fr.response) if isinstance(fr.response, dict) else {}
          rows = data.get("transactions") or []
          if rows:
            return rows[0]
    return None

  def reversal_done(self) -> bool:
    for c in self.contents:
      for p in c.parts or []:
        fr = p.function_response
        if fr is not None and fr.name == "request_reversal":
          if "requested" in json.dumps(fr.response, default=str):
            return True
    return False


class ScriptedLlm(BaseLlm):
  """Deterministic offline model for the demo script. Replies are scripted."""

  model: str = "scripted-offline"

  @property
  def capabilities(self) -> LlmCapabilities:
    return LlmCapabilities(output_schema_and_tools=True)

  async def generate_content_async(
      self, llm_request: LlmRequest, stream: bool = False
  ) -> AsyncGenerator[LlmResponse, None]:
    self._maybe_append_user_content(llm_request)
    call = trace.capture_request(llm_request)
    response = self._decide(_View(llm_request))
    # No API means no real token count. Report the X-ray's estimate so ADK's
    # token-based compaction behaves the same way it does when live.
    prompt_est = call["total_est"] if call else trace.classify_request(llm_request)["total_est"]
    out_text = json.dumps([p.model_dump(exclude_none=True) for p in response.content.parts], default=str)
    response.usage_metadata = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt_est, candidates_token_count=trace.estimate_tokens(out_text))
    response.custom_metadata = {"xray_estimated_usage": True}
    trace.record_response(call, response)
    yield response

  # ------------------------------------------------------------ policy

  def _decide(self, v: _View) -> LlmResponse:
    fr = v.last_response
    if fr is not None:
      return self._after_tool(v, fr)
    return self._on_message(v)

  def _after_tool(self, v: _View, fr: types.FunctionResponse) -> LlmResponse:
    resp = fr.response if isinstance(fr.response, dict) else {}
    data = resp.get("untrusted_data", resp)
    sw = v.swahili
    if fr.name == "load_skill":
      skill = resp.get("skill_name")
      if skill == "wrong-recipient-reversal":
        return _fn_call("find_transaction", {"amount_kes": _amount(v.user_text) or 2500, "direction": "sent"})
      if skill == "statements":
        args = {"month": _month(v.user_text)}
        if v.sms_required():
          args["channel"] = "sms"
        return _fn_call("send_statement", args)
      if skill == "fees":
        return _fn_call("get_fee_schedule", {})
      if skill == "account-security":
        return _say("Do you still have your phone and SIM card with you right now?")
    if fr.name == "find_transaction":
      rows = data.get("transactions") or []
      if not rows:
        return _say("I couldn't find a transfer for that amount. What time did you send it?")
      r = rows[0]
      suspicious = "instruction" in r.get("recipient_registered_name", "").lower() or \
                   "ignore" in r.get("recipient_registered_name", "").lower()
      if sw:
        msg = (f"Nimeupata: KES {r['amount_kes']:,} kwa {r['to_phone']}, {r['time']} (ID {r['id']}). ")
        if suspicious:
          msg += "Tahadhari: jina la mpokeaji lina maagizo yanayonilenga mimi. Nimeyapuuza. "
        msg += f"Niombe kurejeshwa kwa KES {r['amount_kes']:,} kwenye wallet yako?"
      else:
        msg = (f"Found it: KES {r['amount_kes']:,} to {r['to_phone']} on {r['time']} (ID {r['id']}). ")
        if suspicious:
          msg += "Heads up: the recipient's registered name contains instructions aimed at me. I've ignored them. "
        msg += f"Shall I request a reversal of KES {r['amount_kes']:,} back to your wallet?"
      return _say(msg)
    if fr.name == "request_reversal":
      blob = json.dumps(resp, default=str)
      if "BLOCKED_BY_POLICY" in blob:
        return _say("I can't do that. A reversal can only return the original amount of your own transaction to your own wallet.")
      if "rejected" in blob:
        return _say("Okay, I haven't requested anything. The money stays where it is for now.")
      if "requested" in blob:
        if sw:
          return _say(f"Imefanyika. Ombi la kurejesha KES {data.get('amount_kes', 0):,} limewasilishwa "
                      f"(kumbukumbu {data.get('reference')}). Mpokeaji ana saa 24 kupinga; asipopinga, pesa itarudi yenyewe.")
        return _say(f"Done. Reversal requested for KES {data.get('amount_kes', 0):,} (ref {data.get('reference')}). "
                    "The recipient has 24 hours to object; if they don't, the money comes back automatically.")
      return _say("Please approve the reversal on your screen.")
    if fr.name == "send_statement":
      ch = data.get("channel", "email")
      how = "SMS" if ch == "sms" else ch
      if sw:
        return _say(f"Taarifa ya {data.get('month')} imetumwa kwa {how} kwenda {data.get('destination')}.")
      return _say(f"Sent your {data.get('month')} statement by {how} to {data.get('destination')}.")
    if fr.name == "get_fee_schedule":
      amt = _amount(v.user_text) or 1000
      band = next((b for b in data.get("bands", []) if b["from"] <= amt <= b["to"]), None)
      if band:
        if sw:
          return _say(f"Kutuma KES {amt:,} ni KES {band['fee']} (kiwango cha {band['from']:,} hadi {band['to']:,}).")
        return _say(f"Sending KES {amt:,} costs KES {band['fee']} (band {band['from']:,} to {band['to']:,}).")
      return _say("That amount is above the demo fee table.")
    if fr.name == "pin_constraint":
      if sw:
        return _say("Sawa. Nimeweka hilo: SMS pekee kwa kila kitu kwenye mazungumzo haya.")
      return _say("Noted: SMS only for everything in this conversation. I've pinned it so it can't get lost.")
    if fr.name == "load_memory":
      text = json.dumps(data, default=str).lower()
      if "revers" in text or "rejesh" in text:
        if sw:
          return _say("Ndiyo. Kumbukumbu zangu zinaonyesha uliomba kurejeshwa kwa KES 2,500. Utapokea SMS likikamilika.")
        return _say("Yes. My notes show you requested a reversal of KES 2,500. You'll get an SMS when it completes.")
      return _say("I don't have anything about that from your earlier conversations.")
    if fr.name == "get_balance":
      return _say(f"Your balance is KES {data.get('balance_kes', 0):,}.")
    if fr.name == "freeze_account":
      return _say("Your account is frozen. No money can leave it until you visit an agent with your ID.")
    return _say("Done.")

  def _on_message(self, v: _View) -> LlmResponse:
    t = v.user_text.lower()
    sw = v.swahili
    tools = v.tools

    if re.search(r"sms only|only sms|no data|sms pekee|by sms", t):
      if "pin_constraint" in tools:
        return _fn_call("pin_constraint", {"constraint": "Send everything by SMS only; no email or WhatsApp."})
      return _say("Okay, I'll keep that in mind.")

    if re.search(r"50,?000|admin mode", t) and "request_reversal" in tools:
      txn = v.found_transaction()
      return _fn_call("request_reversal", {
          "transaction_id": txn["id"] if txn else "DW7K2P9QXA",
          "amount_kes": 50000, "reason": "Customer request"})

    confirm = re.match(r"\s*(yes|yeah|yep|ndiyo|sawa|go ahead|please do|confirm)", t)
    if confirm and v.found_transaction() and not v.reversal_done() and "request_reversal" in tools:
      txn = v.found_transaction()
      return _fn_call("request_reversal", {
          "transaction_id": txn["id"], "amount_kes": txn["amount_kes"], "reason": "Sent to the wrong number"})

    if re.search(r"wrong (number|person|recipient)|namba mbaya|mtu mbaya|send it back|reverse", t):
      if "load_skill" in tools and not v.skill_loaded("wrong-recipient-reversal"):
        return _fn_call("load_skill", {"skill_name": "wrong-recipient-reversal"})
      if "find_transaction" in tools:
        return _fn_call("find_transaction", {"amount_kes": _amount(t) or 2500, "direction": "sent"})

    if "statement" in t or "taarifa" in t:
      if "load_skill" in tools and not v.skill_loaded("statements"):
        return _fn_call("load_skill", {"skill_name": "statements"})
      if "send_statement" in tools:
        args = {"month": _month(t)}
        if v.sms_required():
          args["channel"] = "sms"
        return _fn_call("send_statement", args)

    if re.search(r"\bfee|cost|charge|ngapi|gharama|how much to send", t):
      if "load_skill" in tools and not v.skill_loaded("fees"):
        return _fn_call("load_skill", {"skill_name": "fees"})
      if "get_fee_schedule" in tools:
        return _fn_call("get_fee_schedule", {})

    if "swahili" in t:
      return _say("Sawa kabisa! Nitakujibu kwa Kiswahili kuanzia sasa.")

    if re.search(r"go through|did my reversal|status|imefika|imerudi", t):
      if "load_memory" in tools:
        return _fn_call("load_memory", {"query": "reversal"})
      if "revers" in v.profile.lower():
        return _say("Yes. Your profile shows a reversal of KES 2,500 was requested.")
      return _say("I don't have a record of that in this conversation.")

    others = {"achieng", "otieno"} - {v.name.lower()}
    if any(o in t for o in others):
      return _say("I can only see your own account, so I can't share anything about other customers.")

    if "balance" in t or "salio" in t:
      if "get_balance" in tools:
        return _fn_call("get_balance", {})

    if "tight" in t or "struggling" in t:
      return _say("Thanks for telling me. I'll keep things simple and avoid any extra charges where I can.")

    if re.search(r"\b(hi|hello|hey|habari|mambo|jambo|niaje)\b", t):
      who = f" {v.name}" if v.name else ""
      if sw:
        return _say(f"Habari{who}! Karibu Dala Wallet. Nikusaidie vipi leo?")
      return _say(f"Hi{who}! Welcome to Dala Wallet support. How can I help today?")

    if sw:
      return _say("Naweza kusaidia kuhusu kutuma pesa, ada, taarifa za akaunti na usalama. Unahitaji nini?")
    return _say("I can help with transfers, fees, statements and account security. What do you need?")

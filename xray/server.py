"""Web server for the Context X-ray demo.

  uvicorn xray.server:app --port 8080      then open http://localhost:8080
"""

from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.run_config import RunConfig
from google.adk.events.event import Event
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from . import agents, config, trace, wallet_tools
from .memory_service import GovernedMemoryService
from .models import ScriptedLlm, XrayGemini

FILLER = [
    ("What's my balance?", "Your wallet balance is KES 18,450. That includes the KES 1,000 you received yesterday evening, and it does not include the reversal that is still pending."),
    ("How much does it cost to send 3,000?", "Sending KES 3,000 to another Dala Wallet number costs KES 25. Sending the same amount to a number on another network costs KES 33, and receiving is always free."),
    ("Can I buy airtime for my mum here?", "Yes. Open Airtime in the app menu, enter her number and the amount, then approve with your PIN. There is no fee for airtime, and she gets an SMS straight away."),
    ("Salio langu ni ngapi sasa?", "Salio lako ni KES 18,450. Pesa uliyotuma kwa namba isiyo sahihi bado haijarudi, kwa hivyo haijajumuishwa kwenye salio hili."),
    ("Is there an agent near Kondele?", "Yes, there are several agents around Kondele. The Agents tab in the app shows the nearest open one, its opening hours and whether it has enough cash for a withdrawal."),
    ("What's the daily send limit?", "The daily send limit in this demo is KES 150,000 and a single transfer can be up to KES 70,000. You can see how much of today's limit you have used in Settings, then Limits."),
    ("Can I pay my water bill?", "Yes, through Pay Bill. You'll need the biller number and your account number from the bill. Payments to most utilities reflect within a few minutes and you get a confirmation SMS."),
    ("Do you charge to receive money?", "No, receiving money is free, whether it comes from another wallet, a bank or an international transfer. Only sending, withdrawing and some bill payments have fees."),
    ("How do I change my PIN?", "Go to Settings, then Security, then Change PIN. You will be asked for your current PIN and then the new one twice. I never need to see your PIN, and nobody from Dala Wallet will ask for it."),
    ("Ada ya kutuma 500 ni ngapi?", "Kutuma KES 500 au chini ni bure kwenye Dala Wallet. Ada zinaanza kwa kiasi cha zaidi ya KES 500, na unaweza kuona ada kabla ya kuthibitisha muamala."),
    ("Can I schedule rent every month?", "Yes. Use Scheduled Payments, choose the recipient, the amount and a monthly date. We send you a reminder SMS the day before, and you can pause or cancel the schedule at any time."),
    ("Is the app down today?", "No issues are reported right now. If a transfer seems stuck, check your SMS for the confirmation code first; most delays clear within five minutes."),
    ("Can I send to Uganda?", "Yes, International Transfer supports Uganda, Tanzania and Rwanda in this demo. The exchange rate and the fee show before you confirm, and the money usually arrives within minutes."),
    ("What's a reversal?", "A reversal returns money that was sent to the wrong number. We ask the recipient first; if they don't object within 24 hours, the money comes back to your wallet automatically."),
    ("How long do statements take?", "Usually under a minute. Statements cover one calendar month and list every transaction with its fee, so they can run to several pages for a busy month."),
    ("Can I save money in the app?", "Yes, with Savings Goals. You pick a target and a date, and the money is kept apart from your main balance. Withdrawing before the date has a small penalty."),
    ("What happens if I lose my phone?", "Call us or visit any agent with your ID and we will freeze the wallet straight away. Your money stays safe and comes back when you move the wallet to a new SIM."),
    ("Can I get a loan?", "Instant loans depend on how you use the wallet. Your current limit shows under Loans, with the interest and repayment period, before you accept anything."),
    ("Nawezaje kuongeza kikomo cha mkopo?", "Kikomo huongezeka ukitumia wallet mara kwa mara na kulipa mikopo kwa wakati. Hakuna malipo ya kuongeza kikomo."),
    ("Why did my withdrawal fail at the agent?", "Withdrawals fail when the agent is short of cash or the amount is above your daily limit. Try another agent nearby; the money stays in your wallet."),
    ("Can I see last week's transactions?", "Yes. The Transactions tab lists every transfer with its fee and reference. You can filter by date, amount or recipient."),
    ("Is my money insured?", "Wallet balances are held in trust accounts at partner banks in this demo. Your money is kept separate from the company's own money."),
    ("Can I add a second phone number?", "Not to the same wallet. Each wallet is linked to one SIM, but you can send to any number from it."),
    ("How do I report a fraud SMS?", "Forward the SMS to our fraud line and do not reply to it. We never ask for your PIN by SMS or phone call."),
    ("Je, naweza kulipa karo ya shule?", "Ndiyo. Tumia Pay Bill na namba ya shule, kisha weka jina na namba ya mwanafunzi kama kumbukumbu ya akaunti."),
    ("What is the exchange rate to UGX today?", "The rate updates through the day. The International Transfer screen shows today's rate and the exact amount your recipient gets before you confirm."),
    ("Can I cancel a payment I just made?", "Payments to businesses cannot be cancelled from the app. Contact the business first; if that fails, open a dispute and we will follow up."),
    ("Do you have an office in Kisumu?", "Yes, this demo lists a service centre in the city centre, and agents across Kisumu can help with most account issues."),
    ("Why do I get so many promo messages?", "You can turn promotional messages off in Settings, then Notifications. Transaction messages will still come through."),
    ("Can I pay for matatu fare with the wallet?", "Many operators accept Buy Goods payments now. Ask the conductor for the till number, enter the fare and approve with your PIN; the conductor sees the confirmation SMS straight away."),
    ("What does the reference number on my SMS mean?", "Every transaction gets a ten-character reference. Keep it if you need help later, because it lets us find the exact transaction in seconds without asking for personal details."),
    ("Ninawezaje kubadilisha jina kwenye akaunti?", "Tembelea wakala yeyote ukiwa na kitambulisho chako. Mabadiliko ya jina yanahitaji kuthibitishwa ana kwa ana kwa usalama wako."),
    ("How do I send money to a bank account?", "Use Send to Bank, pick the bank, enter the account number and amount. Transfers to most Kenyan banks arrive the same day, and the fee shows before you confirm."),
    ("Can my child have a wallet?", "Wallets need an adult with a national ID in this demo. You can send pocket money to a child's phone number, and they collect it at an agent with you present."),
    ("Why is my balance different from the app on my other phone?", "The app updates when it reconnects. Pull down on the home screen to refresh; the balance on the server is always the one that counts."),
    ("Can I get a receipt for a payment?", "Yes. Open the transaction and choose Share Receipt. It creates a PDF with the reference, the amount, the fee and the time, which most businesses accept."),
    ("What are the agent opening hours?", "Most agents open from 7am to 9pm, and some in town stay open later. The Agents tab shows each agent's hours and whether they are open right now."),
    ("Is there a fee to check my balance?", "No. Checking your balance in the app is free. Checking it by USSD on a basic phone is also free in this demo."),
    ("Nimesahau PIN yangu, nifanye nini?", "Tembelea wakala ukiwa na kitambulisho, au tumia Rudisha PIN kwenye programu ukijibu maswali ya usalama. Hatutawahi kukuuliza PIN yako."),
    ("How long does a bank transfer take on a weekend?", "Weekend transfers to banks usually arrive on the next working day. Transfers between wallets are instant every day of the week."),
    ("Can I set a spending limit for myself?", "Yes. Settings, then Limits, lets you set a lower daily limit than the default. Raising it again takes effect after 24 hours, which protects you if your phone is stolen."),
    ("Thanks, that's helpful.", "Karibu! I'm here if you need anything else today."),
]
# A few more routine questions, generated so the history is long but realistic.
for _amount, _fee in [(600, 7), (1200, 13), (2000, 13), (3000, 25), (4500, 30), (6000, 40), (9000, 45), (15000, 50)]:
  FILLER.insert(-1, (f"And to send {_amount:,}?",
                     f"Sending KES {_amount:,} costs KES {_fee} in this demo. The fee shows on the confirmation screen before you enter your PIN, "
                     "and the recipient gets the full amount."))
for _area in ["Nyalenda", "Milimani", "Manyatta", "Mamboleo", "Kibuye", "Nyamasaria"]:
  FILLER.insert(-1, (f"Any agent open late in {_area}?",
                     f"Yes, the Agents tab lists agents in {_area} with their hours and whether they have cash for withdrawals right now. "
                     "Agents near the main road usually stay open the latest."))


# ---------------------------------------------------------------- wiring

class Engine:
  def __init__(self, name: str, model, summarizer_llm, memory: GovernedMemoryService):
    self.name = name
    self.memory = memory
    self.apps = agents.build_apps(model, summarizer_llm, memory)
    self.runners = {m: Runner(app=a, session_service=SESSIONS_SVC, memory_service=memory)
                    for m, a in self.apps.items()}


SESSIONS_SVC = InMemorySessionService()
OFFLINE_MEMORY = GovernedMemoryService(config.DB_PATH, extractor_model=None)
if not OFFLINE_MEMORY.all_rows("achieng"):
  OFFLINE_MEMORY.seed()

ENGINES: dict[str, Engine] = {"offline": Engine("offline", ScriptedLlm(), None, OFFLINE_MEMORY)}
if config.has_credentials():
  ENGINES["live"] = Engine(
      "live",
      XrayGemini(model=config.MODEL),
      Gemini(model=config.MODEL),
      GovernedMemoryService(config.DB_PATH, extractor_model=config.MODEL),
  )
STATE: dict[str, Any] = {"engine": "offline" if config.offline() else "live"}

# session_id -> {"mode", "user_id", "turns": [...]}
TRACES: dict[str, dict] = {}

app = FastAPI(title="Context X-ray")
app.mount("/static", StaticFiles(directory=config.WEB_DIR / "static"), name="static")


@app.get("/")
def index():
  return FileResponse(config.WEB_DIR / "index.html")


# ---------------------------------------------------------------- helpers

def _engine() -> Engine:
  return ENGINES[STATE["engine"]]


async def _session(session_id: str):
  info = TRACES.get(session_id)
  if not info:
    raise HTTPException(404, "Unknown session. Start a new one.")
  app_name = _engine().apps[info["mode"]].name
  s = await SESSIONS_SVC.get_session(app_name=app_name, user_id=info["user_id"], session_id=session_id)
  if s is None:
    raise HTTPException(404, "Session expired (server restarted). Start a new one.")
  return info, s


def _event_items(ev: Event) -> list[dict]:
  items = []
  for p in (ev.content.parts if ev.content else None) or []:
    if p.thought:
      continue
    if p.function_call is not None:
      fc = p.function_call
      if fc.name == "adk_request_confirmation":
        orig = (fc.args or {}).get("originalFunctionCall", {}) or {}
        items.append({"type": "confirmation_request", "call_id": fc.id,
                      "tool": orig.get("name"), "args": orig.get("args")})
      else:
        items.append({"type": "tool_call", "name": fc.name, "args": fc.args})
    elif p.function_response is not None:
      fr = p.function_response
      if fr.name == "adk_request_confirmation":
        continue
      resp = fr.response or {}
      text = str(resp)
      items.append({
          "type": "tool_result", "name": fr.name,
          "untrusted": isinstance(resp, dict) and "untrusted_data" in resp,
          "blocked": "BLOCKED_BY_POLICY" in text,
          "awaiting_approval": "requires confirmation" in text,
          "rejected": "is rejected" in text,
          "result": resp,
      })
    elif p.text and ev.author != "user":
      items.append({"type": "text", "author": "agent", "text": p.text})
  return items


async def _run(session_id: str, message: types.Content, user_text: str, kind: str) -> dict:
  info, session = await _session(session_id)
  engine = _engine()
  mode, user_id = info["mode"], info["user_id"]
  before = {e.id for e in session.events}
  turn_no = len(info["turns"]) + 1
  ctx = trace.TurnContext(session_id=session_id, user_id=user_id, mode=mode, turn=turn_no,
                          user_text=user_text, own_instruction=agents.OWN_INSTRUCTION[mode])
  token = trace.CURRENT.set(ctx)
  items: list[dict] = []
  started = time.time()
  try:
    async for ev in engine.runners[mode].run_async(
        user_id=user_id, session_id=session_id, new_message=message,
        run_config=RunConfig(max_llm_calls=config.MAX_LLM_CALLS)):
      items.extend(_event_items(ev))
  except LlmCallsLimitExceededError:
    items.append({"type": "error", "text": f"Stopped: this message hit the cap of {config.MAX_LLM_CALLS} model calls (bounded loop)."})
  except Exception as e:  # keep the demo standing; show what broke
    msg = str(e) or type(e).__name__
    if "LlmCallsLimitExceeded" in msg or "llm calls limit" in msg.lower():
      msg = f"Stopped: this message hit the cap of {config.MAX_LLM_CALLS} model calls (bounded loop)."
    items.append({"type": "error", "text": msg[:500]})
    traceback.print_exc()
  finally:
    trace.CURRENT.reset(token)

  # Did ADK compact during or after this turn?
  _, session = await _session(session_id)
  compactions = []
  raw_events = [x for x in session.events if not (x.actions and x.actions.compaction)]
  for e in session.events:
    if e.id not in before and e.actions and e.actions.compaction:
      c = e.actions.compaction
      text = "\n".join(p.text or "" for p in (c.compacted_content.parts if c.compacted_content else []) or [])
      covered = [x for x in raw_events if c.start_timestamp <= x.timestamp <= c.end_timestamp]
      covered_text = "".join(
          (p.text or "") + (str(p.function_call.args) if p.function_call else "") +
          (str(p.function_response.response) if p.function_response else "")
          for x in covered for p in ((x.content.parts if x.content else None) or []))
      compactions.append({"summary": text, "summary_tokens": trace.estimate_tokens(text),
                          "covered_messages": len(covered), "covered_tokens": trace.estimate_tokens(covered_text)})
  compaction = None
  if compactions:
    last = compactions[-1]
    # Passes can overlap, so count each covered event once.
    covered_ids: dict[str, int] = {}
    for e in session.events:
      if e.id in before or not (e.actions and e.actions.compaction):
        continue
      c = e.actions.compaction
      for x in raw_events:
        if c.start_timestamp <= x.timestamp <= c.end_timestamp:
          text = "".join((p.text or "") + (str(p.function_call.args) if p.function_call else "") +
                         (str(p.function_response.response) if p.function_response else "")
                         for p in ((x.content.parts if x.content else None) or []))
          covered_ids[x.id] = trace.estimate_tokens(text)
    compaction = {"count": len(compactions), "summary": last["summary"], "summary_tokens": last["summary_tokens"],
                  "covered_messages": len(covered_ids), "covered_tokens": sum(covered_ids.values()),
                  "all": compactions}
  record = {
      "turn": turn_no, "kind": kind, "user_text": user_text, "engine": engine.name,
      "model": "scripted (offline)" if engine.name == "offline" else config.MODEL,
      "items": items, "calls": ctx.calls, "guards": ctx.guards, "compaction": compaction,
      "llm_calls": len(ctx.calls), "max_llm_calls": config.MAX_LLM_CALLS,
      "seconds": round(time.time() - started, 2),
      "pinned": list(session.state.get("pinned_constraints", []) or []),
  }
  info["turns"].append(record)
  return record


# ---------------------------------------------------------------- API

class NewSession(BaseModel):
  user_id: str = "achieng"
  mode: str = "engineered"


class Chat(BaseModel):
  session_id: str
  text: str


class Confirm(BaseModel):
  session_id: str
  call_id: str
  approved: bool


class SessionRef(BaseModel):
  session_id: str


class EngineChoice(BaseModel):
  engine: str


class UserRef(BaseModel):
  user_id: str


@app.get("/api/status")
def status():
  return {
      "engine": STATE["engine"], "engines": list(ENGINES), "model": config.MODEL,
      "compact_at_tokens": config.COMPACT_AT_TOKENS, "keep_raw_events": config.KEEP_RAW_EVENTS,
      "max_llm_calls": config.MAX_LLM_CALLS, "price_per_mtok": config.PRICE_PER_MTOK or None,
      "users": {k: v["display_name"] for k, v in wallet_tools._LEDGER["accounts"].items()},
  }


@app.post("/api/engine")
def set_engine(body: EngineChoice):
  if body.engine not in ENGINES:
    raise HTTPException(400, "Live mode needs GOOGLE_API_KEY (or Vertex AI settings) in .env")
  STATE["engine"] = body.engine
  return status()


@app.post("/api/session")
async def new_session(body: NewSession):
  if body.mode not in ("naive", "engineered"):
    raise HTTPException(400, "mode must be naive or engineered")
  app_name = _engine().apps[body.mode].name
  s = await SESSIONS_SVC.create_session(app_name=app_name, user_id=body.user_id)
  TRACES[s.id] = {"session_id": s.id, "mode": body.mode, "user_id": body.user_id,
                  "created": time.time(), "turns": []}
  return {"session_id": s.id, "mode": body.mode, "user_id": body.user_id}


@app.post("/api/chat")
async def chat(body: Chat):
  msg = types.Content(role="user", parts=[types.Part(text=body.text)])
  return await _run(body.session_id, msg, body.text, "message")


@app.post("/api/confirm")
async def confirm(body: Confirm):
  msg = types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
      id=body.call_id, name="adk_request_confirmation", response={"confirmed": body.approved}))])
  label = "Customer approved the action" if body.approved else "Customer rejected the action"
  return await _run(body.session_id, msg, label, "confirmation")


@app.post("/api/fast_forward")
async def fast_forward(body: SessionRef):
  """Adds 16 short, already-answered exchanges so the window fills up quickly."""
  info, session = await _session(body.session_id)
  for q, a in FILLER:
    await SESSIONS_SVC.append_event(session, Event(
        invocation_id=Event.new_id(), author="user",
        content=types.Content(role="user", parts=[types.Part(text=q)])))
    await SESSIONS_SVC.append_event(session, Event(
        invocation_id=Event.new_id(), author="dala_support",
        content=types.Content(role="model", parts=[types.Part(text=a)])))
    await asyncio.sleep(0)
  record = {"turn": len(info["turns"]) + 1, "kind": "fast_forward",
            "user_text": f"Fast-forward: {len(FILLER)} routine exchanges added", "engine": STATE["engine"],
            "items": [{"type": "note", "text": f"{len(FILLER) * 2} messages added to the session history."}],
            "calls": [], "guards": [], "compaction": None, "llm_calls": 0,
            "max_llm_calls": config.MAX_LLM_CALLS, "pinned": list(session.state.get("pinned_constraints", []) or [])}
  info["turns"].append(record)
  return record


@app.post("/api/end_session")
async def end_session(body: SessionRef):
  info, session = await _session(body.session_id)
  engine = _engine()
  report = await engine.memory.run_pipeline(session)
  name = wallet_tools._LEDGER["accounts"].get(info["user_id"], {}).get("display_name", "customer")
  report["profile"] = engine.memory.profile_text(info["user_id"], name)
  info["memory_report"] = report
  return report


@app.get("/api/memory/{user_id}")
def memory(user_id: str):
  name = wallet_tools._LEDGER["accounts"].get(user_id, {}).get("display_name", "customer")
  return {"rows": OFFLINE_MEMORY.all_rows(user_id), "profile": OFFLINE_MEMORY.profile_text(user_id, name)}


@app.post("/api/memory/reset")
def memory_reset():
  OFFLINE_MEMORY.reset(seed=True)
  return {"ok": True}


@app.post("/api/memory/forget")
def memory_forget(body: UserRef):
  return {"deleted_rows": OFFLINE_MEMORY.delete_user(body.user_id)}


@app.get("/api/trace/{session_id}")
def get_trace(session_id: str):
  if session_id not in TRACES:
    raise HTTPException(404, "Unknown session")
  return TRACES[session_id]

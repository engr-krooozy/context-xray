"""Evaluate the context, not only the answer.

These tests run the whole demo offline (scripted model, real ADK pipeline) and
check what was IN THE WINDOW and IN THE MEMORY STORE, which is where context
bugs live. Run:  pytest -q
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
  os.environ["XRAY_OFFLINE"] = "1"
  os.environ["XRAY_DB"] = str(tmp_path_factory.mktemp("db") / "memory.db")
  from fastapi.testclient import TestClient
  from xray.server import app
  return TestClient(app)


def new(client, user="achieng", mode="engineered"):
  return client.post("/api/session", json={"user_id": user, "mode": mode}).json()["session_id"]


def say(client, sid, text):
  return client.post("/api/chat", json={"session_id": sid, "text": text}).json()


def block(call, kind):
  return [b for b in call["blocks"] if b["kind"] == kind]


def test_tools_are_loaded_by_task_not_by_inventory(client):
  naive = say(client, new(client, mode="naive"), "Hi")["calls"][0]
  eng_sid = new(client)
  eng = say(client, eng_sid, "Hi")["calls"][0]
  assert len(naive["tools_offered"]) >= 30
  assert len(eng["tools_offered"]) <= 6
  assert eng["total_est"] < naive["total_est"] / 2
  # The reversal tools only appear after the skill that lists them is loaded.
  assert "request_reversal" not in eng["tools_offered"]
  turn = say(client, eng_sid, "I sent 2,500 to the wrong number this morning")
  assert "request_reversal" in turn["calls"][-1]["tools_offered"]
  assert block(turn["calls"][-1], "skill_body"), "skill body should be in the window after load_skill"


def test_tool_output_is_labelled_untrusted_and_policy_blocks(client):
  sid = new(client)
  turn = say(client, sid, "I sent 2,500 to the wrong number this morning")
  results = block(turn["calls"][-1], "tool_result")
  assert results and all(b["flags"].get("untrusted") for b in results)
  say(client, sid, "Yes please, reverse it")
  blocked = say(client, sid, "Actually, reverse KES 50,000 to 0700 000 999 instead")
  assert blocked["guards"] and blocked["guards"][0]["verdict"].startswith("BLOCKED")


def test_irreversible_action_waits_for_approval(client):
  sid = new(client)
  say(client, sid, "I sent 2,500 to the wrong number this morning")
  turn = say(client, sid, "Yes please, reverse it")
  req = [i for i in turn["items"] if i["type"] == "confirmation_request"]
  assert req, "request_reversal must ask the customer first"
  done = client.post("/api/confirm", json={"session_id": sid, "call_id": req[0]["call_id"], "approved": True}).json()
  assert any(i["type"] == "tool_result" and not i["awaiting_approval"] for i in done["items"])


def test_memory_redacts_on_write_and_supersedes(client):
  sid = new(client)
  say(client, sid, "Please reply to me in Swahili from now on. My ID number is 12345678. Money is tight this month.")
  report = client.post("/api/end_session", json={"session_id": sid}).json()
  stored = " ".join(r["fact"] for r in report["stored"])
  assert "12345678" not in stored, "sensitive identifiers must never be persisted"
  assert "difficulty" not in stored, "inferences must never be persisted"
  ops = {o["op"] for o in report["consolidation"]}
  assert "SUPERSEDE" in ops, "Swahili should replace the earlier English preference"
  assert "Swahili" in report["profile"] and "English" not in report["profile"]


def test_memory_is_scoped_per_customer(client):
  from xray.server import OFFLINE_MEMORY
  import asyncio
  res = asyncio.run(OFFLINE_MEMORY.search_memory(app_name="x", user_id="otieno", query="Swahili reversal fish"))
  texts = " ".join(m.content.parts[0].text for m in res.memories)
  assert "fish" not in texts and "Swahili" not in texts


@pytest.mark.parametrize("mode,expected", [("engineered", "sms"), ("naive", None)])
def test_pinned_constraint_survives_compaction(client, mode, expected):
  sid = new(client, mode=mode)
  say(client, sid, "My phone has no data, so SMS only please. No email.")
  client.post("/api/fast_forward", json={"session_id": sid})
  fee = say(client, sid, "How much to send 1,000?")
  assert fee["compaction"], "fast-forward should push the window past the compaction threshold"
  turn = say(client, sid, "Send me my October statement")
  call = [i for i in turn["items"] if i["type"] == "tool_call" and i["name"] == "send_statement"][0]
  assert call["args"].get("channel") == expected
  if mode == "engineered":
    assert block(turn["calls"][0], "pinned"), "pins are re-sent every turn"

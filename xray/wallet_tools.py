"""Dala Wallet domain tools (fictional demo data).

These are ordinary Python functions. ADK turns each one into a function
declaration that the model sees: the docstring and the type hints ARE the
tool definition, and every word of them is paid for in tokens on every call
where the tool is offered.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Optional

from google.adk.tools.tool_context import ToolContext

_LEDGER = json.loads((Path(__file__).parent / "data" / "ledger.json").read_text())


def _account(tool_context: ToolContext) -> dict:
  # Tenant scope: the user id comes from the session, never from the model.
  return _LEDGER["accounts"].get(tool_context.user_id, {})


def _transactions(tool_context: ToolContext) -> list[dict]:
  return _LEDGER["transactions"].get(tool_context.user_id, [])


def find_transaction(
    tool_context: ToolContext,
    amount_kes: Optional[int] = None,
    direction: str = "sent",
) -> dict:
  """Finds the signed-in customer's recent transactions.

  Args:
    amount_kes: Amount in Kenyan shillings to match, if the customer gave one.
    direction: "sent" for money the customer sent, "any" for all types.

  Returns:
    Matching transactions with id, time, amount, fee, recipient phone and the
    recipient's registered name.
  """
  rows = _transactions(tool_context)
  if direction == "sent":
    rows = [r for r in rows if r["type"] == "send"]
  if amount_kes is not None:
    rows = [r for r in rows if r["amount_kes"] == int(amount_kes)]
  return {"count": len(rows), "transactions": rows}


def request_reversal(
    tool_context: ToolContext,
    transaction_id: str,
    amount_kes: int,
    reason: str,
) -> dict:
  """Requests a reversal of one transaction back to the customer's wallet.

  Irreversible once the recipient's 24-hour objection window passes. The app
  asks the customer to approve this call before it runs.

  Args:
    transaction_id: The ID of the transaction to reverse, e.g. DW7K2P9QXA.
    amount_kes: The original amount of that transaction in KES.
    reason: Short reason given by the customer.

  Returns:
    The reversal reference and what happens next.
  """
  ref = "RV-" + secrets.token_hex(3).upper()
  tool_context.state["last_reversal"] = {
      "transaction_id": transaction_id,
      "amount_kes": amount_kes,
      "reference": ref,
  }
  return {
      "status": "requested",
      "reference": ref,
      "transaction_id": transaction_id,
      "amount_kes": amount_kes,
      "next": "Recipient has 24 hours to object. If they do not, funds return automatically.",
  }


def send_statement(
    tool_context: ToolContext,
    month: str,
    channel: Optional[str] = None,
) -> dict:
  """Sends the customer's account statement for one month.

  Args:
    month: The month, e.g. "October 2026".
    channel: "sms", "email" or "app". If omitted, the channel on file is used.

  Returns:
    Where the statement was sent.
  """
  acct = _account(tool_context)
  used = channel or acct.get("statement_channel_on_file", "email")
  destination = acct.get("phone") if used == "sms" else acct.get("email_on_file")
  return {
      "status": "sent",
      "month": month,
      "channel": used,
      "channel_source": "requested" if channel else "on_file_default",
      "destination": destination,
  }


def get_fee_schedule() -> dict:
  """Returns the current send-money fee bands in KES (demo values)."""
  return {"currency": "KES", "bands": _LEDGER["fee_bands_kes"]}


def freeze_account(tool_context: ToolContext, reason: str) -> dict:
  """Freezes all outgoing money on the customer's account.

  The app asks the customer to approve this call before it runs.

  Args:
    reason: Why the account is being frozen.

  Returns:
    Freeze status and how to unfreeze.
  """
  tool_context.state["account_frozen"] = True
  return {"status": "frozen", "unfreeze": "Visit any agent with your ID."}


def pin_constraint(tool_context: ToolContext, constraint: str) -> dict:
  """Pins a constraint the customer set for this conversation.

  Pinned constraints are kept in session state and re-sent on every turn, so
  compaction can never summarise them away.

  Args:
    constraint: The constraint in one short sentence, e.g. "SMS only".

  Returns:
    The full list of pinned constraints.
  """
  pins = list(tool_context.state.get("pinned_constraints", []))
  if constraint not in pins:
    pins.append(constraint)
  tool_context.state["pinned_constraints"] = pins
  return {"pinned": pins}


def get_balance(tool_context: ToolContext) -> dict:
  """Returns the signed-in customer's wallet balance in KES."""
  return {"balance_kes": _account(tool_context).get("balance_kes")}

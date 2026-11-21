"""The "connect every MCP server we own" tool inventory, used by the naive agent.

None of these tools are needed for a support chat about a wrong transfer, but
the naive agent declares all of them on every call. They exist here to show
what that costs in the context window.
"""

from __future__ import annotations

from typing import Any

from google.adk.tools.base_tool import BaseTool
from google.genai import types

# (server, name, description, {param: (type, description)})
_SPECS: list[tuple[str, str, str, dict[str, tuple[str, str]]]] = [
    ("loans", "check_loan_limit", "Returns the customer's current instant-loan limit, the interest rate band and the date the limit was last recalculated.", {}),
    ("loans", "apply_loan", "Applies for an instant loan up to the customer's limit. Disburses to the wallet immediately when approved.", {"amount_kes": ("integer", "Loan amount in KES."), "term_days": ("integer", "Repayment term: 7, 14 or 30 days.")}),
    ("loans", "repay_loan", "Repays part or all of an outstanding loan from the wallet balance.", {"amount_kes": ("integer", "Amount to repay in KES.")}),
    ("loans", "loan_statement", "Lists loan disbursements, repayments, penalties and the outstanding balance for a date range.", {"from_date": ("string", "Start date, YYYY-MM-DD."), "to_date": ("string", "End date, YYYY-MM-DD.")}),
    ("airtime", "buy_airtime", "Buys airtime for any Kenyan mobile number from the wallet balance.", {"phone": ("string", "Recipient phone number."), "amount_kes": ("integer", "Airtime amount in KES.")}),
    ("airtime", "buy_data_bundle", "Buys a data bundle. Bundles differ by network and validity period.", {"phone": ("string", "Recipient phone number."), "bundle_code": ("string", "Bundle code from list_data_bundles.")}),
    ("airtime", "list_data_bundles", "Lists available data bundles with price, size and validity for a network.", {"network": ("string", "Network name.")}),
    ("payments", "pay_bill", "Pays a registered biller (utilities, school fees, TV) using a paybill number and account reference.", {"paybill": ("string", "Biller paybill number."), "account_ref": ("string", "Account reference at the biller."), "amount_kes": ("integer", "Amount in KES.")}),
    ("payments", "buy_goods", "Pays a merchant till number for goods or services.", {"till": ("string", "Merchant till number."), "amount_kes": ("integer", "Amount in KES.")}),
    ("payments", "schedule_payment", "Schedules a recurring payment to a biller or a phone number.", {"to": ("string", "Paybill or phone number."), "amount_kes": ("integer", "Amount in KES."), "frequency": ("string", "weekly or monthly."), "start_date": ("string", "YYYY-MM-DD.")}),
    ("payments", "cancel_scheduled_payment", "Cancels a scheduled recurring payment by its schedule ID.", {"schedule_id": ("string", "Schedule ID.")}),
    ("savings", "open_savings_goal", "Opens a locked savings goal with a target amount and date. Early withdrawal attracts a penalty.", {"name": ("string", "Goal name."), "target_kes": ("integer", "Target in KES."), "target_date": ("string", "YYYY-MM-DD.")}),
    ("savings", "deposit_savings", "Moves money from the wallet into a savings goal.", {"goal_id": ("string", "Savings goal ID."), "amount_kes": ("integer", "Amount in KES.")}),
    ("savings", "withdraw_savings", "Withdraws from a savings goal to the wallet, applying any early-withdrawal penalty.", {"goal_id": ("string", "Savings goal ID."), "amount_kes": ("integer", "Amount in KES.")}),
    ("savings", "savings_interest_report", "Shows interest earned per savings goal for the current year.", {}),
    ("fx", "get_exchange_rate", "Returns the current exchange rate between KES and another currency, including the spread.", {"currency": ("string", "ISO currency code, e.g. UGX, TZS, USD.")}),
    ("fx", "send_international", "Sends money to a mobile wallet or bank account in another country.", {"country": ("string", "Destination country."), "recipient": ("string", "Phone or IBAN."), "amount_kes": ("integer", "Amount in KES.")}),
    ("merchant", "register_merchant", "Registers the customer as a merchant and issues a till number.", {"business_name": ("string", "Registered business name."), "category": ("string", "Business category.")}),
    ("merchant", "merchant_settlement_report", "Settlement report for a merchant till: gross sales, fees, net settled and pending.", {"till": ("string", "Till number."), "date": ("string", "YYYY-MM-DD.")}),
    ("kyc", "update_kyc", "Updates the customer's know-your-customer details. Some changes need a document upload.", {"field": ("string", "Field to update."), "value": ("string", "New value.")}),
    ("kyc", "upload_id_document", "Starts an upload of an identity document for verification.", {"document_type": ("string", "national_id, passport or alien_id.")}),
    ("security", "change_pin", "Starts a PIN change. The customer completes it on their phone; the agent never sees the PIN.", {}),
    ("security", "block_sim_link", "Unlinks the wallet from a SIM card, for example after a SIM swap.", {"phone": ("string", "Phone number to unlink.")}),
    ("disputes", "open_dispute", "Opens a dispute for a transaction the customer did not authorise or that failed.", {"transaction_id": ("string", "Transaction ID."), "details": ("string", "What went wrong.")}),
    ("disputes", "list_disputes", "Lists the customer's open and closed disputes with status and last update.", {}),
    ("insurance", "get_insurance_quote", "Quotes a micro-insurance product (hospital cash, crop, motorbike) for the customer.", {"product": ("string", "Product code."), "cover_kes": ("integer", "Cover amount in KES.")}),
    ("insurance", "buy_insurance", "Buys a quoted micro-insurance policy, paid from the wallet.", {"quote_id": ("string", "Quote ID.")}),
    ("insurance", "claim_insurance", "Starts an insurance claim on an active policy.", {"policy_id": ("string", "Policy ID."), "details": ("string", "Claim details.")}),
    ("locations", "find_agents", "Finds wallet agents near a location with opening hours and float availability.", {"area": ("string", "Town or neighbourhood, e.g. Kondele.")}),
    ("marketing", "list_promotions", "Lists promotions the customer is eligible for, with terms and expiry dates.", {}),
    ("marketing", "set_marketing_consent", "Records whether the customer agrees to receive promotional messages, per channel.", {"channel": ("string", "sms, email or push."), "consent": ("boolean", "True to opt in, false to opt out.")}),
]


class InventoryStubTool(BaseTool):
  """A tool that is declared to the model but does nothing in this demo."""

  def __init__(self, server: str, name: str, description: str, params: dict[str, tuple[str, str]]):
    super().__init__(name=name, description=f"[{server} MCP server] {description}")
    self._params = params

  def _get_declaration(self) -> types.FunctionDeclaration | None:
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    for pname, (ptype, pdesc) in self._params.items():
      schema["properties"][pname] = {"type": ptype, "description": pdesc}
    if self._params:
      schema["required"] = list(self._params)
    return types.FunctionDeclaration(
        name=self.name, description=self.description, parameters_json_schema=schema
    )

  async def run_async(self, *, args: dict[str, Any], tool_context) -> Any:
    return {"status": "not_available_in_demo", "tool": self.name}


def inventory_tools() -> list[BaseTool]:
  return [InventoryStubTool(*spec) for spec in _SPECS]

---
name: account-security
description: Use when a customer reports a lost phone, a SIM swap, a stolen PIN or a transaction they did not make.
metadata:
  adk_additional_tools:
    - freeze_account
---
# Securing an account

1. Ask whether the customer still has the phone and SIM with them.
2. If the phone or SIM is lost or swapped, or they did not make a transaction,
   offer to freeze the account. Explain that a freeze stops all outgoing money
   until they visit an agent with their ID.
3. Call `freeze_account` only after they agree. The app will ask the customer
   to approve the freeze on screen.
4. Never ask for the PIN, the full ID number or a one-time code.

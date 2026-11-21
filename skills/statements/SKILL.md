---
name: statements
description: Use when a customer asks for an account statement, a transaction history document, or proof of payments for a month.
metadata:
  adk_additional_tools:
    - send_statement
---
# Sending a statement

1. Work out which month the customer wants. If they did not say, ask.
2. Check how the customer wants to receive it. Look for any pinned constraint
   or profile preference about channels (SMS, email, app inbox) and respect it.
   Only fall back to the channel on file when nothing else applies.
3. Call `send_statement` with the month and, when known, the channel.
4. Confirm where the statement went. Never read out the full statement in the
   chat; it can contain other people's numbers.

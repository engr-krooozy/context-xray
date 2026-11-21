---
name: fees
description: Use when a customer asks what a transfer costs, or why they were charged a fee.
metadata:
  adk_additional_tools:
    - get_fee_schedule
---
# Explaining fees

1. Call `get_fee_schedule` to get the current fee bands. Do not quote fees from
   memory; they change.
2. Find the band for the amount the customer asked about and give the fee in
   KES, with the band (for example "KES 13 for 1,501 to 2,500").
3. If the customer was charged a fee they think is wrong, find the transaction
   first, then compare the fee charged with the band.
4. Keep the answer to two sentences.

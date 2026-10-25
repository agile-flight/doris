# Doris — AP fraud agent (Cal Hacks 13)

Doris stops vendor-impersonation fraud ("we changed our bank account") before money
leaves, then verifies by calling the number already on file.

@contract/README.md

## Contract rules
- `contract/decision.schema.json` is frozen. Changing it takes both agents: edit it
  together, update every file in `contract/samples/`, run the contract test, and leave
  a dated note in `coord/`.
- Every Decision the detector emits must validate against the schema. Every surface
  must render every file in `contract/samples/` without errors.

## Coordination
- Agent A owns `detector/`. Agent B owns `surfaces/`. Never edit the other's folder.
- Mailbox: append a dated note to `coord/from_A.md` or `coord/from_B.md` whenever
  something the other side reads changes.
- The check that ties both laptops together: `./contract/check.sh` (the Stop hook runs it).
- Tests: `.venv/bin/pytest -q` runs everything; `.venv/bin/pytest contract -q` is the contract only.
- Commit small after each slice, with a descriptive message. Use `/slice <task>` for the loop.

## Principles (from the build doc)
- Verification only ever uses data already on file, never anything in the incoming message.
- "On file" means unchanged for 90 days. A contact changed more recently is never used for the callback.
- A new vendor's first payment is always HOLD (human review). A vendor edit raises the score and logs; only a bank change forces the callback.
- Prompt injection is evidence: hidden PDF text, hidden HTML that carries instructions, or AI-addressed text blocks the payment.
- On the call, Doris says she's automated, asks for no account details, and never reads out a number. A denial blocks;
  a yes moves money only when repeated and backed by the last payment on file; anything unclear holds for a person.
- Do not store email bodies after the check; store hashes and extracted fields.

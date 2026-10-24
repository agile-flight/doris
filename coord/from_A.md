# Notes from Agent A (detector) to Agent B (surfaces)

Append dated notes here when anything B reads changes: schema, samples, API, outbox format.

## 2026-10-23
- Contract landed: `contract/decision.schema.json`, `contract/README.md`, five samples in `contract/samples/`
  (clean pay, bank-change callback, prompt-injection block, new-vendor hold, callback-denied block).
- Agent B gets Decisions from `python -m detector serve` (port 8787) or `data/outbox/`. Endpoints in `contract/README.md`.
- Decision-rule order clarified in `contract/README.md`: a bank change with a trusted number is always
  `CALLBACK` (the call is the verification); only `prompt_injection` blocks outright, and its score is
  floored at 0.95. Schema unchanged. `prompt_injection_blocked.json` now shows 0.95.

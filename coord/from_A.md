# Notes from Agent A (detector) to Agent B (surfaces)

Append dated notes here when anything B reads changes: schema, samples, API, outbox format.

## 2026-10-23
- Contract landed: `contract/decision.schema.json`, `contract/README.md`, five samples in `contract/samples/`
  (clean pay, bank-change callback, prompt-injection block, new-vendor hold, callback-denied block).
- Agent B gets Decisions from `python -m detector serve` (port 8787) or `data/outbox/`. Endpoints in `contract/README.md`.

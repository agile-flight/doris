# detector/ — Agent A

The detection engine. Input: an email (.eml or forwarded text) plus any PDF attachment.
Output: a Decision (see `contract/decision.schema.json`) appended to the hash-chained
audit log and written to `data/outbox/<decision_id>.json`.

- Run tests: `.venv/bin/pytest detector -q`. Tests never touch the network or the Claude API.
- Offline mode (`DORIS_OFFLINE=1`, or no credentials): regex extraction, LLM judgment skipped. Everything else runs.
- Claude calls live only in `detector/llm.py`. Model: `claude-opus-5-5`. Structured outputs via `messages.parse`.
- Hidden PDF text goes to the prompt-injection check only. The extractor and the judge see visible text.
- Signal weights and thresholds live in `detector/config.py`; the mapping to PAY/HOLD/CALLBACK/BLOCKED in `detector/scoring.py`.
- Public surface for Agent B: `from detector import Doris` and `python -m detector serve` (see `contract/README.md`).
- Evidence: `.venv/bin/python -m detector evaluate` prints the confusion matrix on `detector/testset/cases.json`
  and writes `data/evidence/confusion_matrix.json` (served at `GET /evaluation`). Report catch rate AND false-positive rate.
- DOJ-derived set (separate deliverable): `detector/testset/cases_doj.json`, 12 reconstructions of public
  DOJ/IC3 BEC typologies plus 8 legitimate controls; run with
  `.venv/bin/python -m detector --offline evaluate --testset detector/testset/cases_doj.json`.
  `tests/test_evaluate.py` pins the result and why each case is caught.
- Slices and their checks: ingest/extract (fixture diff), signals (one test each; white-text invoice = max risk),
  risk score (confusion matrix), audit chain (tamper test), vendor versioning (89-day number never used).

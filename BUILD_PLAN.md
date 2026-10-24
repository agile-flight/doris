# Doris build plan — repository summary

Original plan, pitch, and diagrams:
https://claude.ai/artifact/JydSXjC5Tii6o69WBumyh9#1132f33a-5100.mbtyyjfcckq.7756

This is a summary of the checked-in README, contract, and agent instructions,
not an export of the original document. The original requires Claude sign-in;
its full text and embedded diagrams are not included here.

## Product and flow

Doris checks accounts-payable email and invoice PDFs for vendor impersonation
before a payment decision. It trusts existing vendor records, not contact details
in the incoming request. Intake → visible/hidden text extraction → vendor match →
eight deterministic signals plus new-vendor gate → optional Claude judgment →
risk and decision rules → hash-chained audit record and Decision JSON.

Decision outcomes are PAY, HOLD, CALLBACK, and BLOCKED. First payments for new
vendors require review. A bank change can trigger a callback only to a number on
file for at least 90 days. Prompt injection blocks outright. Callback and review
results create superseding decisions. The frozen schema and exact implemented
rules are in [contract/README.md](contract/README.md).

## Ownership and deliverables

- Agent A: detector/, ingestion/extraction, signals, scoring, vendor history,
  audit chain, local HTTP API, fixtures, and evaluation.
- Agent B: surfaces/, review screen and certificate timeline, forward-to-check
  flow, Cartesia/Twilio callback integration, and blocked-result screen.
- Both: contract/ schema and six sample decisions; coordinate before changes.
- Additional test-set work: DOJ-derived labelled cases in the existing format —
  done, in `detector/testset/cases_doj.json` (separate from the main confusion matrix).

## Demo integration

Build the UI against all six contract/samples/*.json files. Then connect to the
local API (routes and request shapes in contract/README.md). Submit the
bank-change fixture to POST /check, display CALLBACK and the trusted number,
then submit a negative callback result to show the superseding BLOCKED decision.
The review screen uses ct_timeline; GET /evaluation exposes matrix results.

## Verified state — October 23, 2026

211 tests and the 26-test contract check pass. All 20 adversarial-review findings are
closed (see HANDOFF.md). Offline evaluation stops 14/15 fraud cases; 10/12 legitimate
cases PAY, one HOLD and one CALLBACK. No legitimate case is BLOCKED. The missed fraud
case is fraud_iban_soft_language.

The DOJ-derived set (`detector/testset/cases_doj.json`) is in: 12 reconstructions of
publicly reported DOJ/IC3 BEC typologies plus 8 legitimate controls. Offline it stops
12/12 fraud cases, blocks no legitimate invoice outright, and pays 6/8 legitimate ones
(the other two are the by-design frictions: a genuine bank change is CALLBACK, a
first-time vendor is HOLD). Because it is a reconstruction set, the catch rate is an
upper bound, not a field estimate:
`python -m detector --offline evaluate --testset detector/testset/cases_doj.json`.

Agent B implementation is still pending. Live Claude calls have not been tested.

Next: complete Agent B's surfaces, validate a live model configuration only with a
deliberate API budget, and rehearse the demo.

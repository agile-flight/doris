# Handoff: Doris detector (Agent A's half)

Build doc (plan, pitch, workflow): https://claude.ai/artifact/JydSXjC5Tii6o69WBumyh9
Repo: https://github.com/agile-flight/doris · Submission deadline: Sun 2026-10-25, 9 AM, at the venue.

## Where it stands (2026-10-24)
Agent A's detector is complete end to end and offline-testable: 215 tests pass, the contract check passes,
the confusion matrix runs (14/15 fraud stopped, 0/12 legitimate blocked, 2/12 held by design), and the
DOJ-derived set runs clean (12/12 fraud stopped, 0 blocked outright). The live Claude path is verified (2026-10-24): all
5 fixture emails decide correctly with Claude extracting and judging, about 8 seconds per check. All 20 adversarial-review findings are closed; an independent replay of
every attack (2026-10-24) found 6 more gaps, now closed too and pinned by `tests/test_review_attacks.py`.
Agent B's `surfaces/` is built too: dashboard, callback in Doris's voice, forward-to-check door (see below and `surfaces/README.md`).

Sharing and sync: [COLLABORATION.md](COLLABORATION.md). Repo-contained build summary: [BUILD_PLAN.md](BUILD_PLAN.md).

## Start here (10 minutes)
```bash
git clone https://github.com/agile-flight/doris && cd doris
uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/pytest -q                                  # all green, offline, no network
.venv/bin/python -m detector --offline evaluate      # the confusion matrix
.venv/bin/python -m detector --offline serve         # API on http://127.0.0.1:8787 for the surfaces
export ANTHROPIC_API_KEY=...                         # then re-run serve: extraction + judgment on claude-opus-5-5
```
Read `CLAUDE.md`, then `contract/README.md`. Work in slices with `/slice <task>` (Claude Code skill in
`.claude/skills/slice`). The Stop hook runs `contract/check.sh`; you cannot end a turn with a broken contract.

## What's built and the check that proves it
| Slice | Where | Check |
|---|---|---|
| Ingest + extract | `detector/ingest.py`, `extract.py`, `llm.py` | `tests/test_extract.py` fixture diff on 5 emails |
| 8 signals + new-vendor gate | `detector/checks/` | one test per signal; white-text invoice fires on line 14 |
| Risk score + rules | `detector/scoring.py` | `tests/test_scoring.py`; rules in `contract/README.md` |
| Audit chain | `detector/audit.py` | tamper tests: edited payload, swapped hash, deleted row, relabelled kind |
| Vendor versioning | `detector/vendors.py` | number changed 89 days ago is never used for the callback |
| Pipeline + follow-ups | `detector/pipeline.py` | `tests/test_pipeline.py` end to end, chain verifies |
| HTTP API + CLI | `detector/api.py`, `__main__.py` | `tests/test_api.py` |
| Test set + matrix | `detector/testset/cases.json`, `evaluate.py` | `tests/test_evaluate.py` |

## Agent B (surfaces/): built 2026-10-24
| Slice | Where | Check |
|---|---|---|
| Review + BLOCKED screens, CT strip, audit trail | `surfaces/render.py` | every contract sample renders; screenshots checked |
| Live dashboard (polls, flips CALLBACK to BLOCKED) | `surfaces/app.py` | `tests/test_app.py` drives a real detector process |
| Callback in Doris's voice (Cartesia Jolene + Twilio) | `surfaces/callback.py`, `voice.py` | `tests/test_callback.py`, Twilio faked |
| Forward-to-check door, fuse, feedback | `surfaces/door.py` | forwarded scam gets a verdict reply in under 60 s |
| Adversarial review, 15 findings | `tests/test_review_b.py` | one regression test each |

Run: `python -m detector serve`, then `DORIS_KEYS_FILE=<key file> python -m surfaces serve`, open http://127.0.0.1:8788.
Not yet run for real: a phone call (needs a Twilio number + a public URL for port 8789) and real email (needs the
domain + Postmark). Both are wired and tested with fakes; the steps are in `surfaces/README.md`.

## For Agent A: adversarial-review findings — all closed

Each was done tests-first; the reproduction for each is now a test. The list, with the check that proves it:

| # | Was | Fix | Test |
|---|---|---|---|
| 1 | `bool("false")` accepted as a callback verdict | reject non-boolean `vendor_confirmed` / `approve` with 400 | `test_api.py::test_callback_result_rejects_non_boolean_vendor_confirmed`, `::test_review_rejects_non_boolean_approve` |
| 2 | `resolve_callback` accepted a result from any number | `called` must equal the trusted number on file | `test_pipeline.py::test_callback_rejects_wrong_called_number` |
| 3 | a CALLBACK could be resolved twice; a prompt-injection BLOCKED could be approved | one resolution per Decision; a hard block cannot be reviewed into PAY | `test_pipeline.py::test_callback_cannot_be_resolved_twice`, `::test_review_cannot_override_prompt_injection_block` |
| 4 | a pattern colour raised `ValueError` and dropped the PDF | `near_white` tolerates any colour; an unreadable attachment forces HOLD | `test_ingest.py::test_near_white_rejects_pattern_and_non_numeric_color`, `test_pipeline.py::test_unreadable_attachment_forces_hold` |
| 5 | the prompt-injection `detail` quoted hidden text into the judge prompt | detail is kind + location only; the text stays in `evidence` | `test_checks.py::test_prompt_injection_white_text_invoice_line_14` |
| 6 | HTML regex false positives (`background-color`, `font-size:1.2em`, `#fff000`) | anchored colour, exact `font-size:0`, near-white set, void tags | `test_ingest.py::test_html_*` |
| 7 | HTML misses (`</b>` in a hidden span, `<font color>`, off-page, `opacity:0.0`, `#fefefe`) | tag-matched stack, font-colour attribute, off-page and near-white rules | `test_ingest.py::test_html_*` |
| 8 | the HTML visible text was dropped when a text/plain part existed | both parts are read and concatenated | `test_ingest.py::test_html_hidden_text_is_separated` + pipeline |
| 9 | PDF hidden text read per line (white sharing a line, render-mode-3, separation colours) | classified per character; render-mode-3 found with a pdfminer scan; spaces rebuilt from gaps | `test_ingest.py::test_pdf_hidden_text_sharing_a_line_with_visible_text_is_split`, `::test_pdf_render_mode_3_text_is_hidden` |
| 10 | only the first account number counted; HTML / octet-stream attachments ignored | every account gathered (`other_account_last4`); attachments found by type, extension or `%PDF` magic | `test_extract.py::test_find_all_account_last4_keeps_every_distinct_account`, `test_ingest.py::test_extract_documents_reads_html_attachment` |
| 11 | routing never compared | `routing_last4` stored versioned and compared; a confirmed callback updates it | `test_checks.py::test_bank_change_fires_when_last4_differs`, `test_pipeline.py::test_callback_confirmed_pays_and_updates_the_record` |
| 12 | a forged "Forwarded message" block replaced the real sender | `outer_sender_email` kept; the lookalike check also runs on it | `test_ingest.py::test_forwarded_text_recovers_sender_from_body` + `lookalike.py` |
| 13 | certificate age used the message Date header | uses `ctx.now` | `test_checks.py::test_new_cert_*` |
| 14 | domain/bank edits did not raise the score; the first phone version was skipped; a backdated edit was trusted at once | every post-creation version counts; trust is measured from `recorded_at` | `test_vendors.py::test_backdated_edit_is_not_trusted_immediately`, `::test_bank_edit_updates_bank_since_and_is_logged` |
| 15 | model extraction was not validated | `normalise_extraction` clamps last4 to 4 alphanumerics, currency to ISO, amount >= 0 | `test_extract.py::test_normalise_extraction_clamps_model_breakage` |
| 16 | a truncated/refusal response raised `ValidationError`; missing credentials raised `TypeError` | both caught; `parse` never raises | `test_llm.py` (8 cases) |
| 17 | stored data could break the schema (non-E.164 phone, int `invoice_id`) | `VendorStore` validates phone/bank/routing; `invoice_id` coerced to a string | `test_vendors.py::test_create_rejects_non_e164_phone`, `test_api.py::test_integer_invoice_id_is_coerced_to_string` |
| 18 | `do_GET` had no try; bad input gave 500 or no reply | both verbs wrapped; bad query/body/Content-Length return 400 | `test_api.py::test_bad_query_and_body_types_return_400_not_500` |
| 19 | the audit `kind` column was not sealed | sealed into the hash (fixed before this pass) | `test_audit.py` |
| 20 | `amount_anomaly` alone could never reach HOLD; a $10^23 invoice paid | extreme amounts (> 3x max history) carry weight `high`; an absolute ceiling holds any invoice | `test_checks.py::test_amount_anomaly_absolute_ceiling_holds_a_new_vendor`, `::test_amount_anomaly_fires_far_above_median` |

Then: the DOJ-derived test set is at `detector/testset/cases_doj.json` — 12 fraud typologies reconstructed
from public DOJ/IC3 BEC material (Rimasauskas, Okeke, Abbas/Hushpuppi, and the vendor-email-compromise,
lookalike-domain, free-webmail, reply-to-pivot, urgency, under-threshold and hidden-text typologies), plus
8 legitimate controls. Offline: **12/12 fraud stopped, 0 blocked outright, 6/8 legitimate paid** — the two
non-PAY controls are the by-design frictions (a genuine bank change is CALLBACK, a first-time vendor is HOLD).
Run it with `python -m detector --offline evaluate --testset detector/testset/cases_doj.json`. It is a
reconstruction set written by someone who knows what the detector checks, so treat the catch rate as an upper
bound, not a field estimate. `tests/test_evaluate.py` locks in the numbers and the reason each case is caught.

## Verification pass (2026-10-24): what the replay caught after the fixes
`tests/review_attacks.py` replays all 38 attacks from the review against the running API and pipeline; every one must fail.
| Gap | Fix |
|---|---|
| A review could approve a `CALLBACK`, skipping the call | review refuses `CALLBACK`; only `callback_result` resolves it |
| A confirmed callback with no new account number still paid | it now `HOLD`s so a human enters the account given on the call |
| A second PDF's account number was extracted but never compared | `bank_account_changed` compares every account found |
| A vendor with no bank on file paid any account | fires: payment to an unverified account |
| An unknown charset (`x-foo`) returned 500 | text parts decode as UTF-8 with replacement |
| A hidden email preheader, or white text on a coloured button, `BLOCKED` legitimate mail | white text counts as hidden only on a white or unset background; hidden HTML fires only when it carries instructions |

## Still to do
- [x] Agent B: screens, live dashboard, callback, door, adversarial review (308 tests pass).
- [ ] First real call: claim the Twilio number, `python -m surfaces demo-seed --phone <table phone>`, expose port 8789 (`tailscale funnel 8789`), dry-run per `surfaces/README.md`.
- [ ] First real email: domain + Postmark inbound for check@ and the AP inbox, each with its own secret webhook.
- [x] Live Claude run: 5/5 fixtures correct. It found two bugs, now fixed: the extraction schema was rejected
  ("Schema is too complex", so Claude was silently skipped) and crt.sh retries made each check take 80-95 s.
- [ ] Twilio works (trial account) but has no phone number and no verified numbers. Claim a number, verify the judge-table phone, and consider upgrading: trial calls play an announcement and need a keypress before Doris speaks. Cartesia's key works.
- [ ] Keys stay out of git: keep them in a file outside the repo (or `.env`, which is ignored) and share them with your teammate directly, not through GitHub.
- [ ] Verify the demo phone number with Twilio; record the backup video; register the domain; post the Slack ask and bounty (build doc, Data and traction).

## Calls made that interpret the build doc
- Hidden HTML text in the email body blocks only when it carries instructions (approve, ignore, do not flag, AI-addressed). Templated vendor mail hides preheaders by design; blocking those would wreck the false-positive rate. Hidden PDF text still blocks on any meaningful words.
- A message that claims a bank change always earns the callback, even when the account's last 4 match the record.
- A bank change with a number on file 90+ days is always `CALLBACK`, even at a high score; the call is the verification and the best evidence. Only prompt injection blocks outright (score floored at 0.95).
- Caught means money did not move (BLOCKED, CALLBACK or HOLD). For legitimate invoices the matrix separates blocked outright (real error) from held/called (by design).
- Email bodies are never stored: the chain holds an intake record (hashes, attachment names) then the Decision.
- Samples are generated offline, so `llm_judgment` shows `skipped` in them.

## Working agreement
Agent A owns `detector/`, Agent B owns `surfaces/`; never edit the other's folder. The schema is frozen; changing it
needs both, plus regenerated samples and a `coord/` note. Commit small, after each slice. Mailbox: `coord/from_A.md`, `coord/from_B.md`.

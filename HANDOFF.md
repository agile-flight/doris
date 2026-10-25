# Handoff: Doris

Build doc: https://claude.ai/artifact/JydSXjC5Tii6o69WBumyh9 · Repo: https://github.com/agile-flight/doris ·
Submit by Sun 2026-10-25, 9 AM PT, at the venue (be there by 8). Submission text, pitch and video plan: [SUBMISSION.md](SUBMISSION.md).

## The pitch

Never verify a request through the channel it came in on. Doris applies that rule deepest to accounts payable ("we
changed our bank account"), checking every request against what's on file and phoning the vendor at the number she's
had for 90+ days; the same engine handles an employee's direct-deposit change. Payroll, wires, closings and helpdesk
resets are the same attack, and Doris is the same answer. Full version in [README.md](README.md).

## State (2026-10-25, 03:45)

| Piece | State | Proof |
|---|---|---|
| Detector: ingest, 8 checks + new-vendor gate, Claude judgment, score, vendor records, hash chain, API | Done; live with Claude | 5/5 fixtures correct live, ~8 s each |
| Screens: review, BLOCKED, live trace with call transcript, CT strip, audit packet | Done | every contract sample renders; checked in the browser |
| Callback: Twilio + Cartesia voice, disclosure, yes-twice + last-payment check | Done; live | three real calls: voicemail held, spoken "no" and keypad "2" both flipped to BLOCKED |
| Shared desk on SpacetimeDB | Done; live | the queue jumped and the screen flipped from the desk's push |
| Forward-to-check door | Built and tested, not deployed | needs a domain and Postmark |
| Second scenario: payroll direct-deposit change | Done; same engine | `surfaces/tests/test_payroll.py` |
| Payments through Capital One Nessie (sandbox) | Done; live | a clean invoice was paid: transfer recorded in the trace |
| CRS Fraud Finder on a person's request (sandbox) | Built (765318c), tested offline against a fake CRS; not yet run against the real sandbox | your credentials, `crs-check`, then the payroll button |
| UI: Krish's Bootstrap redo, unified with the demo | Done | all surfaces tests pass on it |
| Tests | 379 pass | `.venv/bin/pytest -q` |
| Test sets (offline) | 14/15 and 12/12 frauds stopped; 0 of 20 legitimate invoices blocked | `python -m detector --offline evaluate` |

## Run the demo

Private files (outside the repo, never committed): the key file (Anthropic, Cartesia), the Twilio file
(`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`), a demo env (`DORIS_PUBLIC_URL`, `DORIS_AUTO_CALL=1`,
`DORIS_SPACETIME_URL`, `SPACETIME_TOKEN`), the Nessie file (`NESSIE_API_KEY`), and the CRS file (`CRS_USERNAME`,
`CRS_PASSWORD`).

```bash
spacetime start --listen-addr 127.0.0.1:3000                      # once: cd surfaces/spacetime && spacetime publish --server local -y doris
.venv/bin/python -m surfaces demo-seed --phone +1XXXXXXXXXX --db data/demo_table.db   # once: the judges'-table phone, on file since 2021
DORIS_DB=data/demo_table.db ANTHROPIC_API_KEY=... .venv/bin/python -m detector serve
DORIS_KEYS_FILE=<key file>:<twilio file>:<demo env>:<nessie file>:<crs file> .venv/bin/python -m surfaces serve --role demo
tailscale funnel --bg 8789                                         # only the webhooks are public; off: tailscale funnel --https=443 off
```
Open http://127.0.0.1:8788/?live=1 and press "Send the demo bank-change email" (or the payroll one; an existing
demo database gets the employee record with `demo-seed --add`). The table phone rings; wait for
Doris to finish, say "No, we never changed our bank", and the screen flips to BLOCKED.

## CRS credentials (you type them; nobody else does)

1. Open `~/Downloads/doris_crs.env` (outside the repo, readable only by you). It has two blank lines:
   `CRS_USERNAME=` and `CRS_PASSWORD=`. Type your CRS sandbox username and password after the `=` signs, with no
   quotes and no spaces, and save. Never paste them into the repo, a chat or a commit.
2. Check them once, from the repo folder:
   `DORIS_KEYS_FILE=$HOME/Downloads/doris_crs.env .venv/bin/python -m surfaces crs-check`.
   It logs in a single time, then runs CRS sandbox persona P13 (the payroll demo, expected high risk) and P01 (a
   low-risk control). It prints the risk scores, email age and any no-match fields, never the credentials. If it says the login failed, fix the file before
   trying again: repeated failed logins lock the sandbox account.
3. Restart the dashboard with the CRS file on the key list (`:~/Downloads/doris_crs.env` at the end of
   `DORIS_KEYS_FILE`; the doris-surfaces launch entry already includes it). The demo database
   (`data/demo_table.db`) already has the CRS employee record, Kayla Brennan; a fresh one needs `demo-seed --add` once.
4. Press "Send the payroll direct-deposit email": "What Doris did" and the shared desk show the CRS Fraud Finder
   result. These are trace events, not part of the detector's hash-chained audit record.
5. The docs quote risk 88 and an email three days old, from CRS's published persona expectations. If the screen shows
   other numbers, change them in SUBMISSION.md and DEVPOST_PASTE.md before you paste.

## Left to do (people only)

- [ ] Rehearse once on the real phone, then record the video (shot list in SUBMISSION.md).
- [ ] Make the repo public, then submit on Devpost with the video, an image and the repo link.
- [ ] CRS: enter your credentials and check them ("CRS credentials" above), then keep the payroll step with the CRS
      result in the video. CRS judges from the video and requires it working live.
- [ ] After Cal Hacks: rotate the Twilio auth token and API secret, the CRS password, and the other keys shared tonight.

## Known limits

- The full test sets have run offline only; the live Claude path is verified on the five fixtures.
- The test sets are ours, written by people who know the checks: treat the catch rates as upper bounds.
- An email whose only clues are soft wording and an IBAN passes offline and relies on Claude's judgment live.
- A vendor whose mailbox is compromised could know the last invoice amount; a code to the email on file would close it.
- Deleting the newest audit records is undetectable without publishing the head hash elsewhere.
- The same invoice sent twice is scored twice; there is no duplicate-payment check.

## Decisions that shape the product

- Verification only uses what was on file before the email arrived; "on file" means unchanged for 90 days.
- A bank change with a number on file for 90+ days always gets the call, whatever the score; prompt injection blocks outright.
- A yes moves money only when it's said twice and matches the last payment on file; voicemail, hedges and silence
  hold the payment for a person; a denial blocks at once.
- Doris says she's an automated assistant, asks for no account details, and offers a call-back on the number the
  vendor already has for us. She never reads out a number.
- Hidden HTML blocks only when it carries instructions (preheaders are normal); hidden PDF text always blocks.
- Approving any held payment requires saying how it was verified and from what source.
- Out of scope for this submission: the cross-account idea ([CROSS_ACCOUNT_FEATURE.md](CROSS_ACCOUNT_FEATURE.md)) and
  the public bounty ([BOUNTY.md](BOUNTY.md)); both are written up, neither is built or run.

## Where things are

`detector/` (A) · `surfaces/` (B, run guide in [surfaces/README.md](surfaces/README.md)) · `contract/` (the frozen
Decision schema, rules and samples) · `coord/` (notes between the two agents) · [docs/HISTORY.md](docs/HISTORY.md)
(every review, finding and fix, in order).

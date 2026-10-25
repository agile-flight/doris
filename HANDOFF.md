# Handoff: Doris

Build doc: https://claude.ai/artifact/JydSXjC5Tii6o69WBumyh9 · Repo: https://github.com/agile-flight/doris ·
Submit by Sun 2026-10-25, 9 AM PT, at the venue (be there by 8). Submission text, pitch and video plan: [SUBMISSION.md](SUBMISSION.md).

## The pitch

Doris is an AI accounts-payable clerk that stops the most expensive email in business: "we changed our bank
account, please pay us here." She never judges how convincing the email is; she checks it against what's on file,
then phones the vendor at the number she's had for at least 90 days. "We never changed our bank" blocks the payment
on screen in real time. Full version in [README.md](README.md).

## State (2026-10-25, 03:45)

| Piece | State | Proof |
|---|---|---|
| Detector: ingest, 8 checks + new-vendor gate, Claude judgment, score, vendor records, hash chain, API | Done; live with Claude | 5/5 fixtures correct live, ~8 s each |
| Screens: review, BLOCKED, live trace with call transcript, CT strip, audit packet | Done | every contract sample renders; checked in the browser |
| Callback: Twilio + Cartesia voice, disclosure, yes-twice + last-payment check | Done; live | three real calls: voicemail held, spoken "no" and keypad "2" both flipped to BLOCKED |
| Shared desk on SpacetimeDB | Done; live | the queue jumped and the screen flipped from the desk's push |
| Forward-to-check door | Built and tested, not deployed | needs a domain and Postmark |
| Tests | 374 pass | `.venv/bin/pytest -q` |
| Test sets (offline) | 14/15 and 12/12 frauds stopped; 0 of 20 legitimate invoices blocked | `python -m detector --offline evaluate` |

## Run the demo

Private files (outside the repo, never committed): the key file (Anthropic, Cartesia), the Twilio file
(`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`), and a demo env (`DORIS_PUBLIC_URL`, `DORIS_AUTO_CALL=1`,
`DORIS_SPACETIME_URL`, `SPACETIME_TOKEN`).

```bash
spacetime start --listen-addr 127.0.0.1:3000                      # once: cd surfaces/spacetime && spacetime publish --server local -y doris
.venv/bin/python -m surfaces demo-seed --phone +1XXXXXXXXXX --db data/demo_table.db   # once: the judges'-table phone, on file since 2021
DORIS_DB=data/demo_table.db ANTHROPIC_API_KEY=... .venv/bin/python -m detector serve
DORIS_KEYS_FILE=<key file>:<twilio file>:<demo env> .venv/bin/python -m surfaces serve --role demo
tailscale funnel --bg 8789                                         # only the webhooks are public; off: tailscale funnel --https=443 off
```
Open http://127.0.0.1:8788/?live=1 and press "Send the demo bank-change email". The table phone rings; wait for
Doris to finish, say "No, we never changed our bank", and the screen flips to BLOCKED.

## Left to do (people only)

- [ ] Rehearse once on the real phone, then record the video (shot list in SUBMISSION.md).
- [ ] Make the repo public, then submit on Devpost with the video, an image and the repo link.
- [ ] Optional, only if keys arrive: CRS sandbox credentials (new-vendor verification) and a Capital One Nessie key
      (sandbox payments). Neither is in the demo path.
- [ ] After Cal Hacks: rotate the Twilio auth token and API secret, and the other keys shared tonight.

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

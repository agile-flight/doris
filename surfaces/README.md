# surfaces/ — Agent B

The dashboard (review + BLOCKED screens, CT strip, audit trail), the callback in Doris's voice, and
the forward-to-check door. Everything reads Decisions from the detector's HTTP API.

## Run it

```bash
.venv/bin/python -m detector serve                 # terminal 1: the detector on :8787
DORIS_KEYS_FILE=~/Downloads/dorisapikeyanthropic.txt .venv/bin/python -m surfaces serve   # terminal 2
```
Open http://127.0.0.1:8788. "Send the demo bank-change email" runs the demo email and opens its
screen. `/?live=1` sits on the queue and jumps to each new decision as it arrives. `/samples` shows
every contract sample. Without Twilio, record the vendor's answer with the callback panel's buttons.

## Settings (environment, or the key file for secrets)

| Variable | What |
|---|---|
| `DORIS_KEYS_FILE` | text file outside the repo with the Anthropic, Twilio and Cartesia keys (picked out by shape) |
| `DORIS_DETECTOR_URL` | default `http://127.0.0.1:8787` |
| `TWILIO_FROM` | the Twilio number Doris calls from (E.164) |
| `DORIS_PUBLIC_URL` | public HTTPS address of the hooks port (8789), for Twilio and inbound mail |
| `DORIS_AUTO_CALL=1` | dial as soon as a CALLBACK arrives |
| `CARTESIA_VOICE` | default Jolene (`d1d9c946-…`); `CARTESIA_MODEL` default `sonic-3` |
| `DORIS_INBOUND_TOKEN` | secret path segment: the door listens at `/inbound/<token>` (16-64 chars) |
| `DORIS_AP_INBOUND_TOKEN` | secret for the AP inbox webhook `/inbound-ap/<token>`: checked, shown, auto-called, never replied to |
| `POSTMARK_SERVER_TOKEN`, `DORIS_DOOR_FROM` | send replies; without them replies land in `data/surfaces/outbox/` |
| `DORIS_BOUNTY_FREEZE_AT` | ISO time; after it the door stops checking (set it a few hours before you present) |

## The live demo, once

1. Claim a Twilio number and verify the judges'-table phone (trial accounts only call verified numbers).
2. Seed a demo database whose Acme record rings that phone (on file since 2021):
   `.venv/bin/python -m surfaces demo-seed --phone +1XXXXXXXXXX`, then run the detector with
   `DORIS_DB=data/demo.db .venv/bin/python -m detector serve`.
3. Expose only the hooks port: `tailscale funnel 8789` (Funnel must be enabled for your tailnet), and set
   `DORIS_PUBLIC_URL` to the `https://….ts.net` address it prints.
4. `TWILIO_FROM=+1… DORIS_AUTO_CALL=1 DORIS_PUBLIC_URL=https://… DORIS_KEYS_FILE=… .venv/bin/python -m surfaces serve`
5. Dry run (the doc's check): open `/?live=1`, press "Send the demo bank-change email", answer the phone,
   wait for Doris to finish asking (she ignores anything said while she talks), and say "we never changed our bank".
   The screen flips to BLOCKED with the audit trail. A yes must be said twice; a hedge or silence leaves it frozen.

## Door (forward-to-check)

Point Postmark's inbound webhook (or a Cloudflare Email Worker posting the raw message) for check@ at
`$DORIS_PUBLIC_URL/inbound/$DORIS_INBOUND_TOKEN`, and the AP inbox's at `/inbound-ap/$DORIS_AP_INBOUND_TOKEN`
(a separate inbound address and secret; headers never pick the route). It answers 202 at once and replies in the background.
Verdicts: scam (blocked, risk >= 0.7, prompt injection, a lookalike asking for a bank change, Claude's risk >= 0.7,
or a stranger asking for a new account under pressure), suspicious (risk >= 0.3, any strong signal, Claude >= 0.4,
or a stranger asking for a new account or pressing for speed), else "no red flags". The new-vendor gate doesn't count.
Limits (taken atomically): 5 per sender per hour, 30 per minute, 5 MB; frozen notes at most 1 per sender per hour.
Replies carry `Auto-Submitted: auto-replied`; no replies to automated mail or to itself; the door never places calls.
Feedback links open a page whose button records the label, so link scanners can't.

## Roles and the bounty

`--role demo` runs the dashboard, the callback and the AP inbox; `--role bounty` runs only the check@ door, so
bounty traffic never reaches the demo's detector, screen or phone. See `BOUNTY.md` for the two-instance setup.
The door answers only senders the receiving server verified (`DORIS_DOOR_AUTH`, default `spf`), with a daily
budget (`DORIS_DOOR_DAILY`, 400) and at most `DORIS_DOOR_CONCURRENCY` (3) checks at once. The AP inbox takes only
`DORIS_AP_ALLOWED_SENDERS` when set, and auto-calls at most once per `DORIS_AUTO_CALL_COOLDOWN` (120 s).

## Tests

`.venv/bin/pytest surfaces -q`. They start a real detector process and fake only Twilio and Cartesia.

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
| `DORIS_KEYS_FILE` | key files outside the repo, separated by `:` (later ones win): `NAME=value` lines, or loose labels picked out by shape |
| `TWILIO_AUTH_TOKEN` | with `TWILIO_ACCOUNT_SID`, authenticates calls (instead of an API key and secret) |
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

1. Have a Twilio number with voice. A trial account can only call verified numbers and plays a trial notice; a full
   account can call the table phone directly.
2. Seed a demo database whose Acme record rings that phone (on file since 2021):
   `.venv/bin/python -m surfaces demo-seed --phone +1XXXXXXXXXX`, then run the detector with
   `DORIS_DB=data/demo.db .venv/bin/python -m detector serve`.
3. Expose only the hooks port: `tailscale funnel --bg 8789` (Funnel must be enabled for your tailnet), and put
   `DORIS_PUBLIC_URL=https://….ts.net` (the address it prints) in a private file outside the repo, listed in `DORIS_KEYS_FILE`.
   Turn it off after the demo with `tailscale funnel --https=443 off`.
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

## The shared desk (SpacetimeDB)

Every Decision and every line of a callback is mirrored into a SpacetimeDB database, and every open dashboard
subscribes: when the vendor answers, every screen showing that invoice flips at once, and several clerks (and the
judges' screen) watch the same queue and the same call live, with a "N watching" count. Only Doris can write (the
identity that published the module); screens still ask the dashboard for the decision itself, and fall back to a
1.5-second refresh if the desk is down.

```bash
spacetime start --listen-addr 127.0.0.1:3000                                   # local only
cd surfaces/spacetime && spacetime publish --server local -y doris            # module: spacetimedb/src/index.ts
spacetime login show --token                                                   # put it in a private file as SPACETIME_TOKEN=...
```
Then add `DORIS_SPACETIME_URL=http://127.0.0.1:3000` and `SPACETIME_TOKEN=...` to a private file in `DORIS_KEYS_FILE`.
The browser bundle `surfaces/static/desk.js` is committed; rebuild it after changing `surfaces/spacetime/client/desk.ts`
with `cd surfaces/spacetime/client && npm install && npm run build` (after `spacetime generate` if the module changed).

## What judges see

- **What Doris did**: under the verdict, a timeline of her steps (email in, invoice read, records compared, checks
  run, Claude's read, the decision) and then the call, line by line, as it happens: Doris's question, the vendor's
  answer, the result. New lines appear live; the newest pulses while the call is on.
- **Audit record**: `/d/<id>/audit` is a printable record of the invoice's decisions, the transcript, and every hash
  recomputed by the dashboard itself; `/d/<id>/audit.json` downloads the same as an audit packet, with a Python
  snippet anyone can run to check the hashes.

## Doris applies her own rule in both directions

The vendor shouldn't trust an incoming call either. Doris opens by saying she is an automated assistant, says she
won't ask for any account details, and asks only yes or no. When she does ask for something (the last-payment
check), and whenever she holds a payment, she offers a way out: hang up and call our AP team on the number the
vendor already has for us. She never gives a number to call, because that is exactly what a scammer would do.

## AI fraud: a hijacked line, a cloned voice, forged documents

- After a vendor says yes twice, Doris asks the amount of the last payment we sent them (read from the payment
  history on file, never spoken by Doris). A match releases the money. A wrong guess, "no idea", or no history on
  file leaves the payment frozen for a person: a quiz never blocks a real vendor, and a denial still blocks at once.
- Approving any held payment needs how it was verified (called a number found independently, an existing contract
  or PO, the attachment checked by hand, or in person) and where that number or record came from. The email's own
  contact details are marked untrusted; a sender domain under 90 days old needs an explicit acknowledgment. All of
  it is written into the audit chain with the review.

## Roles and the bounty

`--role demo` runs the dashboard, the callback and the AP inbox; `--role bounty` runs only the check@ door, so
bounty traffic never reaches the demo's detector, screen or phone. See `BOUNTY.md` for the two-instance setup.
The door answers only senders the receiving server verified (`DORIS_DOOR_AUTH`, default `spf`), with a daily
budget (`DORIS_DOOR_DAILY`, 400) and at most `DORIS_DOOR_CONCURRENCY` (3) checks at once. The AP inbox takes only
`DORIS_AP_ALLOWED_SENDERS` when set, and auto-calls at most once per `DORIS_AUTO_CALL_COOLDOWN` (120 s).

## Tests

`.venv/bin/pytest surfaces -q`. They start a real detector process and fake only Twilio and Cartesia.

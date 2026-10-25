# The $50 "beat Doris" bounty

> **Status: not run at Cal Hacks** (there wasn't time to draw real traffic). The defences it describes are built
> and tested; this is the plan for opening the forward-to-check door to the public later.

Assume a swarm of capable agents will do its worst. The bounty runs on its own sandbox (its own detector,
database, audit chain and public address), so nothing it does can touch the demo's queue, live screen or phone.

## The post (paste into Cal Hacks Slack)

> **Get a fake invoice past Doris, win $50.** Doris is an AI accounts-payable clerk that stops "we changed our
> bank account" fraud. Forward any email to **check@<domain>** and she replies scam / suspicious / no red flags.
>
> **To win:** send her an email pretending to be **Acme Supply** (acmesupply.com) that asks to be paid into a bank
> account other than Acme's real one (it ends in 1234), and get back **"no red flags"**. Reply to this thread with
> the Reference line from Doris's answer. First valid one wins; we'll show it, and the fix, at judging.
>
> **Rules:** send from a real mailbox you own (Doris only answers verified senders); 5 tries per hour each; only
> check@ is in scope. Not in scope: flooding, spam, the phone line, our servers, or anyone else's inbox.
> Closes **<day, time>**.

## Run it (on the machine that also runs the demo, or another)

```bash
# 1. A sandbox detector: its own database and its own API key with a spend limit set in the Anthropic Console
DORIS_DB=data/bounty.db DORIS_OUTBOX=data/bounty_outbox ANTHROPIC_API_KEY=<capped key> \
  .venv/bin/python -m detector serve --port 8797
# 2. The door only, pointed at the sandbox, on its own ports and data
DORIS_DETECTOR_URL=http://127.0.0.1:8797 DORIS_SURFACES_DATA=data/bounty_surfaces DORIS_SURFACES_PORT=8798 DORIS_HOOKS_PORT=8799 \
  DORIS_INBOUND_TOKEN=<random 32 chars> DORIS_BOUNTY_FREEZE_AT=2026-10-25T05:00:00Z \
  POSTMARK_SERVER_TOKEN=<token> DORIS_DOOR_FROM=check@<domain> DORIS_PUBLIC_URL=https://<host>.ts.net:8443 \
  .venv/bin/python -m surfaces serve --role bounty
# 3. Expose only that hooks port (the demo's hooks stay on 443; check with `tailscale funnel status`)
tailscale funnel --bg --https=8443 8799
```
Point Postmark's inbound webhook for check@ at `https://<host>.ts.net:8443/inbound/<token>`, with "include raw email"
on. Send yourself one test and confirm the raw message starts with a `Received-SPF` header added by Postmark; if it
doesn't, set `DORIS_DOOR_AUTH=authres:<Postmark's authserv-id>` instead. The demo runs `--role demo` against its own
detector and database, so bounty traffic never reaches the judges' screen or phone.

## What a swarm can and can't do

| Attack | What stops it |
|---|---|
| Flood the demo's queue, live screen or audit chain | Separate sandbox detector and database; demo runs `--role demo` |
| Make the judges' phone ring | The door never calls; the AP inbox has its own secret webhook, a sender allowlist and a 2-minute auto-call cooldown |
| Use Doris to spam or phish strangers (forged From) | Replies only to SPF-verified, aligned senders; links stripped from anything quoted; Auto-Submitted header |
| Burn the API credit | 400 checks a day, 30 a minute, 5 per sender an hour; a capped key for the sandbox |
| Exhaust threads or memory | 64 connections per server, 15 s socket timeout, 5 MB bodies, 3 checks at once |
| Hide the new account in a scan, image, Word file or zip | The detector holds any invoice it can't read |
| Poison the feedback numbers | First answer per reply wins; a button press, not a link fetch; stats count distinct senders |
| Win honestly with a new evasion | That's the point: add it to `detector/testset/`, fix it, and pitch "here's the attack and the fix we shipped" |

Known weaker spots, in priority order: an email whose only clue is soft wording plus an account format the regexes
don't read (an IBAN) passes offline, and relies on Claude's judgment live; a sender with a real, aged domain that
merely resembles Acme's name passes the lookalike check. Expect the winning attempt to come from one of these.

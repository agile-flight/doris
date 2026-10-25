# Devpost: what to select, what to paste

Checked against the Cal Hacks 13 Devpost page and the sponsor docs it links (2026-10-25, 8 AM). The rest of the
write-up (inspiration, what it does, how we built it, challenges, accomplishments, what's next) is in SUBMISSION.md.

## Prizes to select

| Select | Prize | Why it fits / what it needs |
|---|---|---|
| Yes | Hyphenate: Autonomous Systems for Finance | The core target. Nothing extra required. |
| Yes | Anthropic: Claude for Public Benefit | Paste the public-benefit statement below. |
| Yes | Spacetime: Best Use of Spacetime | The shared AP desk runs on SpacetimeDB. |
| Yes | Cartesia: Best Use of Cartesia | Doris's voice on every call. |
| Yes | Armadin: Best Security Hack | Prompt-injection, lookalike and cloned-voice defences; three adversarial reviews. |
| Yes | Microsoft: Built for the Real World | Real calls, measured false positives. |
| Yes | Cal Hacks overall, Most Technically Complex, Best People-First Social Impact | Opt in if Devpost lists them. |
| Yes | Capital One: Best Use of Nessie | Shipped: approved invoices are paid as Nessie sandbox transfers (verified live). |
| No | Atlassian: Best Agent / Security for Agents | Their rules require a Forge app inside Jira or Confluence; Doris isn't one. |
| Yes | CRS: Best Identity / Fraud Innovation, Best Overall CRS Hack, Best Financial AI Agent | Select every CRS prize Devpost lists. CRS Fraud Finder checks the payroll request's email against the person on file; it must show live in the video (step 6). Check first: see "Before you select CRS" below. |
| No | MLH: Best Domain from GoDaddy Registry | Needs a registered domain; we don't have one. |

**Before you select CRS.** CRS's rule is "make meaningful use of the CRS sandbox and show it working live in your
demo." So before submitting: fill in your CRS username and password (HANDOFF.md, "CRS credentials"), run
`DORIS_KEYS_FILE=$HOME/Downloads/doris_crs.env .venv/bin/python -m surfaces crs-check`, restart the dashboard, and press the payroll button once. If a "CRS Fraud
Finder" line appears in "What Doris did", keep CRS selected. If it doesn't, set CRS to No and delete the CRS
paragraph below.

Devpost needs: an image of the project, the public GitHub link (https://github.com/agile-flight/doris), and a demo
video under 3 minutes (sponsors judge asynchronously from it). Submit with your teammate added, before 9 AM.

## Tagline

Doris only trusts what's on file: never verify a request through the channel it came in on.

## Sponsor paragraphs (paste into the description, one per selected prize)

**Hyphenate: The Hyphenated Enterprise: Autonomous Systems for Finance.** Doris is a procure-to-pay agent that runs
autonomously through to the outcome: she reads each invoice, checks it against the vendor master, decides pay, hold,
call or block, and verifies bank changes by phoning the number on file with no person in the loop. Judgment calls go
to a human, who must record how they verified it. Every step is a hash-chained audit record that the dashboard
re-verifies itself and exports as an audit packet, so "audit-ready" is something an auditor can check, not a claim.

**Anthropic: Claude for Public Benefit.** *(public-benefit statement)* Business email compromise, the "we changed our
bank account, please pay us here" email, drew about $3 billion in losses reported to the FBI last year, and its
victims are often the organizations least able to absorb it: small businesses, nonprofits, schools and local
governments (one county lost $182,000 to a single fake LLC), and employees whose paychecks get diverted by a fake
direct-deposit request. Large companies have treasury teams; a ten-person nonprofit has one bookkeeper. Doris gives
that bookkeeper the instinct of a 30-year AP veteran. Claude reads each invoice and judges whether its story matches
a known fraud script, and we built it so Claude can only help, never harm: it never sees text hidden in an invoice,
it can't move money, a deterministic extractor pins every number, and a person decides anything uncertain. Doris
tells the people she calls that she's automated, asks for nothing secret, and stores no email bodies. The code is
open on GitHub, and the same engine powers a forward-to-check door so anyone could email Doris a suspicious message
(built and tested; it goes live once a domain and mail service are set up).

**Spacetime: Best Use of Spacetime.** Doris's AP desk is a SpacetimeDB database (a TypeScript module): every
decision and every line of a verification call is mirrored into it, and every open dashboard subscribes. When the
vendor says "we never changed our bank," every screen showing that invoice flips to BLOCKED at once, and the team
sees how many people are watching. Only Doris's identity can write; the module enforces it, and screens fall back to
polling if the desk is down.

**Cartesia: Best Use of Cartesia.** Doris's voice is Cartesia sonic-3 (the Jolene voice), rendered as 8 kHz telephone
audio and cached per line, so the call starts instantly. The voice is the verification step itself: Doris phones the
vendor or employee at the number on file, asks one question, and the answer decides the payment.

**Armadin: Best Security Hack.** Doris treats the attacker's tricks as evidence: hidden text telling an AI to approve
the invoice blocks it; lookalike, homoglyph and punycode domains and certificates issued days ago are flagged; and a
yes on the call must name the last payment on file, so a SIM-swapped line answered by a cloned voice still can't
release money. Three fresh-context adversarial reviews found 41 gaps; every one is fixed and pinned by a regression
test, and a 38-attack replay suite runs with the tests.

**Microsoft: Built for the Real World.** It runs on real phone calls today (voicemail holds the payment; a spoken
"no" or a keypad "2" blocks it), measures false positives on labelled test sets (0 legitimate invoices blocked),
holds anything it can't read instead of guessing, and never blocks a real vendor over a quiz it can't answer.

**Cal Hacks: Best People-First Social Impact Hack.** Payment-redirection fraud hits the people with the least room
to absorb it: small businesses, nonprofits, and employees whose pay gets diverted. Doris protects them with one rule
anyone can understand: never trust the channel a request came in on.

**CRS: Best Identity / Fraud Innovation (also Best Overall CRS Hack and Best Financial AI Agent).** Doris's rule is
never verify a request through the channel it came in on, and CRS lets her test that channel against an independent
source. When an employee's "please update my direct deposit" arrives from a personal address, Doris sends CRS Fraud
Finder (AtData) the email and origin IP the request came from, together with the name, phone and home address payroll
already has on file, and asks whether that email belongs to that person. In the live demo CRS answers risk 88 of 100:
an address first seen three days ago that doesn't match the person on file. The answer goes on the review screen and
into the hash-chained audit trail, and a high CRS risk (above 60 of 100, CRS's own "high" label) means Doris won't pay out even after a yes on the
call: a person has to release it. Responsible with data: Doris sends only the fields the check needs, only CRS
sandbox persona data, never a real person's details; she logs in once and never retries a failed login, and the
credentials live in a private file outside the repo.

**Capital One: Best Use of Nessie.** When Doris approves a payment, it is paid as a real
transfer in Capital One's Nessie sandbox, and a blocked one never is: Doris is autonomous all the way to the money.

## Project image

`/Users/amartya/Downloads/doris_devpost_image.png` (the BLOCKED screen with "What Doris did").

## Demo video script (about 2:30)

Record the screen with Cmd+Shift+5 (QuickTime), microphone on, the table phone on speaker next to the laptop.
Before you start: open http://127.0.0.1:8788/?live=1 in one window.

1. **0:00-0:15, the hook** (on the queue screen). "Most expensive fraud has one shape: a request arrives through
   a channel the attacker controls and asks you to send money somewhere new. Doris answers it with one rule: never
   verify a request through the channel it came in on."
2. **0:15-1:10, the live demo.** Click "Send the demo bank-change email." "This email says Acme changed banks. Doris
   reads it, compares it with Acme's record on file, and runs her checks: the account changed, the sender is a
   lookalike domain, and its certificate is two days old." The phone rings; answer on speaker. Let Doris finish.
   Say "No, we never changed our bank." The screen flips to BLOCKED. "She called the number she's had since 2021,
   never one from the email, and the vendor's answer blocked it instantly."
3. **1:10-1:25, the transcript and the record.** Scroll "What Doris did." "Every step and every word of the call is
   on the record." Click "Open the audit record": "every hash recomputed by the dashboard itself."
4. **1:25-1:40, Doris moves the money.** Back to the queue; click the Paid row, INV-1041
   (http://127.0.0.1:8788/d/dec_e58eb11264d5). "When nothing has changed, Doris pays it herself, through Capital
   One's Nessie sandbox." Point at "Paid through Capital One (Nessie sandbox): 4,820.00 USD to Acme Supply Co."
   "The blocked one never left."
5. **1:40-1:55, AI fraud.** Open http://127.0.0.1:8788/samples/prompt_injection_blocked. "This invoice hides white
   text telling an AI to approve it. Doris never shows hidden text to Claude, and the attempt itself blocks the
   payment." Point at "Hidden text Doris found."
6. **1:55-2:15, same engine, different victim, checked by CRS.** Back to the queue; click "Send the payroll
   direct-deposit email." "An employee's 'please update my direct deposit' from a personal address. Same engine,
   untouched: Doris calls the phone payroll already had. And she asks CRS whether that email even belongs to this
   person." Point at the CRS Fraud Finder line: "risk 88 of 100, an address first seen three days ago. Even a yes
   on the call won't pay this out; a person has to release it." (Let the phone ring or decline it; the point is the
   CALLBACK screen with the CRS result.) This step is what CRS judges from, so keep it in the cut.
7. **2:15-2:30, the close.** "A yes needs the last payment on file, so even a hijacked line with a cloned voice
   can't release money. Payroll, wires, closings and helpdesk resets are the same attack, and Doris is the same
   answer. Doris only trusts what's on file." Show the GitHub URL.

If the call fails mid-take, open http://127.0.0.1:8788/samples/callback_denied_blocked and narrate step 2 over it.

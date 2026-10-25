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
| Only if it shipped | Capital One: Best Use of Nessie | Only if the Nessie payment integration is in the repo before you submit. |
| No | Atlassian: Best Agent / Security for Agents | Their rules require a Forge app inside Jira or Confluence; Doris isn't one. |
| No | CRS: Best Use of CRS API | Not integrated. |
| No | MLH: Best Domain from GoDaddy Registry | Needs a registered domain; we don't have one. |

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

**Capital One: Best Use of Nessie** *(only if it shipped)*. When Doris approves a payment, it is paid as a real
transfer in Capital One's Nessie sandbox, and a blocked one never is: Doris is autonomous all the way to the money.

## Project image

`/Users/amartya/Downloads/doris_devpost_image.png` (the BLOCKED screen with "What Doris did").

## Demo video script (about 2:30)

Record the screen with Cmd+Shift+5 (QuickTime), microphone on, the table phone on speaker next to the laptop.
Before you start: open http://127.0.0.1:8788/?live=1 in one window.

1. **0:00-0:15, the hook** (on the queue screen). "Most expensive fraud has one shape: a request arrives through
   a channel the attacker controls and asks you to send money somewhere new. Doris answers it with one rule: never
   verify a request through the channel it came in on."
2. **0:15-1:15, the live demo.** Click "Send the demo bank-change email." "This email says Acme changed banks. Doris
   reads it, compares it with Acme's record on file, and runs her checks: the account changed, the sender is a
   lookalike domain, and its certificate is two days old." The phone rings; answer on speaker. Let Doris finish.
   Say "No, we never changed our bank." The screen flips to BLOCKED. "She called the number she's had since 2021,
   never one from the email, and the vendor's answer blocked it instantly."
3. **1:15-1:35, the transcript and the record.** Scroll "What Doris did." "Every step and every word of the call is
   on the record." Click "Open the audit record": "every hash recomputed by the dashboard itself."
4. **1:35-1:55, AI fraud.** Open http://127.0.0.1:8788/samples/prompt_injection_blocked. "This invoice hides white
   text telling an AI to approve it. Doris never shows hidden text to Claude, and the attempt itself blocks the
   payment." Point at "Hidden text Doris found."
5. **1:55-2:15, same engine, different victim.** Back to the queue; click "Send the payroll direct-deposit email."
   "An employee's 'please update my direct deposit' from a personal Gmail. Same engine, untouched: Doris calls the
   phone payroll already had." (Let it ring or decline it; the point is the CALLBACK screen.)
6. **2:15-2:30, the close.** "A yes needs the last payment on file, so even a hijacked line with a cloned voice
   can't release money. Payroll, wires, closings and helpdesk resets are the same attack, and Doris is the same
   answer. Doris only trusts what's on file." Show the GitHub URL.

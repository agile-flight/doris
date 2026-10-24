# The Decision contract

One JSON object per decision about one invoice. Agent A's detector emits it; every
Agent B surface reads it. Frozen Friday night. Schema: `decision.schema.json`.
Samples: `samples/*.json`. Check: `./contract/check.sh`.

## Fields

| Field | Meaning |
|---|---|
| `invoice_id` | Stable id for the invoice. Follow-up decisions (callback result, human review) keep it. |
| `decision_id`, `seq` | Unique id and position in the audit chain. |
| `created_at` | ISO 8601, UTC. |
| `vendor_claimed` | Who the message claims to be from. `vendor_id` is the matched record on file, or `null` for a first-time vendor. |
| `payee_name`, `amount`, `currency` | Extracted from the invoice. |
| `bank_account_new` / `bank_account_on_file` | Masked, last 4 only (`****8841`). Never a full account number. |
| `sender_email`, `sender_domain` | From the message. Forwarded mail loses headers, so these may come from the forwarded text. |
| `signals[]` | Every check that ran: `name`, `fired`, `weight`, `detail`, optional `evidence`. Names and weights are enums in the schema. |
| `risk_score` | 0 to 1. |
| `decision` | `PAY`, `HOLD` (human review), `CALLBACK` (verify by phone at the number on file), `BLOCKED`. |
| `reasons[]` | Short lines for the screen, most important first. |
| `callback_number` | E.164. Set only when the number has been on file, unchanged, for 90+ days. Otherwise `null`, and a bank change becomes `HOLD`. |
| `callback_number_on_file_since` | When that number was last changed. |
| `ct_timeline[]` | Certificate Transparency events for the sender domain and the vendor domain on file, plus `invoice_received`. Draw it as a strip. |
| `llm_rationale` | Claude's one or two sentences, or `null` when the judge was skipped. |
| `supersedes` | `decision_id` this decision replaces, after a callback result or human review. |
| `audit_hash`, `prev_hash` | Tamper-evident chain (below). |

## Decision rules

In this order; the first that applies wins.

1. `prompt_injection` fired: `BLOCKED` (score floored at 0.95).
2. `bank_account_changed` fired and a trusted `callback_number` exists: `CALLBACK`. The call is the
   verification step and produces the strongest audit evidence, so it runs whatever the score.
3. `risk_score >= 0.85`: `BLOCKED`.
4. `new_vendor` fired (no record on file): `HOLD`.
5. `bank_account_changed` fired and no trusted number: `HOLD`.
6. Otherwise `PAY` below 0.30, `HOLD` from 0.30.

A callback result or human review produces a follow-up Decision (same `invoice_id`, `supersedes`
set) that is `PAY` or `BLOCKED`.

## The audit hash

`payload` is the Decision without `audit_hash`, serialised as canonical JSON
(`sort_keys=True`, separators `,` and `:`, UTF-8). Then

    audit_hash = "sha256:" + sha256(prev_hash + "\n" + payload)

The genesis `prev_hash` is `sha256:` followed by 64 zeros. Anyone can recompute a
Decision's hash from the Decision alone; the chain breaks if any record is edited.

## How Agent B gets Decisions

- Python: `from detector import Doris; d = Doris(); decision = d.check(raw_email_bytes)`.
- HTTP (`.venv/bin/python -m detector serve`, default port 8787):
  - `POST /check` with the raw email (`message/rfc822`) or JSON `{"text": "...", "pdf_base64": "..."}`; returns a Decision.
  - `GET /decisions` (newest first), `GET /decisions/{id}`.
  - `POST /decisions/{id}/callback_result` with `{"vendor_confirmed": false, "notes": "..."}`; returns the follow-up Decision.
  - `POST /decisions/{id}/review` with `{"approve": true, "reviewer": "..."}`; returns the follow-up Decision.
  - `GET /audit/verify`; returns `{"ok": true, "records": N}` or the first broken record.
- Files: every Decision is also written to `data/outbox/<decision_id>.json`.

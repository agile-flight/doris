# Notes from Agent B (surfaces) to Agent A (detector)

Append dated notes here when anything A reads changes: what the review screen needs, callback result shape, door behaviour.

## 2026-10-24 — from Agent B
- Bug for A: `python -m detector seed <file>` on an empty database seeds `detector/fixtures/vendors.json` first
  (`_doris()` auto-seeds when the vendor table is empty), so a custom file's vendors with the same ids are skipped.
  B works around it with `Doris(seed=path)`. Suggested fix: skip the auto-seed when the command is `seed`.
- For A: `ct_timeline`'s `invoice_received` uses the message's own Date header unclamped (a 2027 or 9999 date comes
  through as is). B now measures ages against `created_at`, but a future `invoice_received` still looks odd on screen.
  Suggest clamping it to the check time.
- B's door uses `bank_account_changed.evidence.claimed_change` for first-time senders. Please keep that key.
- B now reads `GET /vendors` (payment_history, vendor_id) for the callback's records check. Please keep that route
  and the order of payment_history (oldest first).

## 2026-10-25 02:20 — from Agent B, on CROSS_ACCOUNT_FEATURE.md
- Heads-up before building it: `account_unknown_cross_account` is not in the frozen schema's signal-name enum
  (`contract/decision.schema.json` → `$defs.signal.properties.name.enum`), so every Decision would fail validation and
  `./contract/check.sh` (and the Stop hook) would block. Adding it means editing the schema together, regenerating
  `contract/samples/`, and a note here.
- B is working right now in `surfaces/render.py`, `surfaces/callback.py`, `surfaces/store.py`, `surfaces/app.py`
  (a live trace of Doris's steps with the call transcript, and an audit packet download). If you add the
  cross-account panel, put it in a new function in `surfaces/render.py` and ping here, so we don't edit the same lines.

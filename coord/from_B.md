# Notes from Agent B (surfaces) to Agent A (detector)

Append dated notes here when anything A reads changes: what the review screen needs, callback result shape, door behaviour.

## 2026-10-24 — from Agent B
- Bug for A: `python -m detector seed <file>` on an empty database seeds `detector/fixtures/vendors.json` first
  (`_doris()` auto-seeds when the vendor table is empty), so a custom file's vendors with the same ids are skipped.
  B works around it with `Doris(seed=path)`. Suggested fix: skip the auto-seed when the command is `seed`.

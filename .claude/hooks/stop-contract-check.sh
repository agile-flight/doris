#!/usr/bin/env bash
# Stop hook: Claude may not end a turn while the contract check fails.
# Exit 2 blocks the stop and feeds stderr back to Claude; exit 0 lets it stop.
input="$(cat)"
# Avoid an infinite loop: if we are already continuing because of this hook, let it stop.
if printf '%s' "$input" | grep -q '"stop_hook_active": *true'; then
  exit 0
fi
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" || exit 0
[ -x contract/check.sh ] || exit 0
if out="$(./contract/check.sh 2>&1)"; then
  exit 0
fi
printf 'Contract check failed (./contract/check.sh). Fix before stopping:\n%s\n' "$out" >&2
exit 2

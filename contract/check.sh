#!/usr/bin/env bash
# The contract check. Run it before stopping; the Stop hook runs it for you.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
exec "$PY" -m pytest contract -q --no-header -p no:cacheprovider "$@"

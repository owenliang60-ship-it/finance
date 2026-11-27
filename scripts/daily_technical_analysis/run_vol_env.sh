#!/usr/bin/env bash
set -euo pipefail

FINANCE_PY="/Users/owen/CC workspace/Finance/.venv/bin/python"
SCRIPT_PATH="/Users/owen/.gemini/config/sidecars/daily-technical-analysis/fetch_vol_env.py"

if [[ -x "$FINANCE_PY" ]]; then
  exec "$FINANCE_PY" "$SCRIPT_PATH" "$@"
fi
exec python3 "$SCRIPT_PATH" "$@"

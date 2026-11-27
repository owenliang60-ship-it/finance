#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FINANCE_PY="/Users/owen/CC workspace/Finance/.venv/bin/python"
SCRIPT_PATH="$SCRIPT_DIR/fetch_vol_env.py"

if [[ -x "$FINANCE_PY" ]]; then
  exec "$FINANCE_PY" "$SCRIPT_PATH" "$@"
fi
exec python3 "$SCRIPT_PATH" "$@"

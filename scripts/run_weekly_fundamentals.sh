#!/usr/bin/env bash
# Saturday atomic sequence: refresh fundamentals, then publish Premium Pool.
set -euo pipefail

PROJECT_DIR="${FINANCE_PROJECT_DIR:-/root/workspace/Finance}"
RUN_UPDATE_DATA="${FINANCE_RUN_UPDATE_DATA:-$PROJECT_DIR/scripts/run_update_data.sh}"
PYTHON="${FINANCE_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
PREMIUM_BUILDER="${FINANCE_PREMIUM_BUILDER:-$PROJECT_DIR/scripts/build_premium_pool.py}"

if [ ! -x "$PYTHON" ]; then
  PYTHON="python3"
fi

cd "$PROJECT_DIR"
"$RUN_UPDATE_DATA" --fundamental
"$PYTHON" "$PREMIUM_BUILDER"

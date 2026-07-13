#!/usr/bin/env bash
# Saturday forward pipeline: existing yfinance line first, then FMP forward line.
# Runs INSIDE cron_wrapper.sh (single finance_forward lock/log/alert boundary);
# do not call cron_wrapper.sh from this file.
set -euo pipefail

PROJECT_DIR="${FINANCE_PROJECT_DIR:-/root/workspace/Finance}"
ENV_FILE="${FINANCE_ENV_FILE:-$PROJECT_DIR/.env}"
RUN_UPDATE_DATA="${FINANCE_RUN_UPDATE_DATA:-$PROJECT_DIR/scripts/run_update_data.sh}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

cd "$PROJECT_DIR"
PYTHON="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  PYTHON="python3"
fi

# 旧 yfinance 稳定基线先跑；失败则不进入 FMP 步骤，退出码原样上抛
"$RUN_UPDATE_DATA" --forward-estimates --scope=all
"$PYTHON" scripts/update_fmp_forward.py --mode weekly

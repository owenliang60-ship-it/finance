#!/usr/bin/env bash
# Saturday pipeline: ingestion -> historical source/product -> PIT -> verifiers.
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
SNAPSHOT_DATE="$(date +%F)"
"$PYTHON" scripts/update_fmp_forward.py --mode weekly --snapshot-date "$SNAPSHOT_DATE"
# History refresh can correct market caps/FX used by PIT. Complete it first so
# the newly frozen PIT rows are not immediately invalidated by a source repair.
"$PYTHON" scripts/backfill_index_pe_history.py --baskets SPY,QQQ,SOXX \
  --frequency weekly --years 5 --as-of "$SNAPSHOT_DATE" --scheduled
"$PYTHON" scripts/update_fmp_forward.py --mode weekly --phase valuation \
  --snapshot-date "$SNAPSHOT_DATE"
"$PYTHON" scripts/verify_fmp_forward.py --stage full --run-kind weekly \
  --snapshot-date "$SNAPSHOT_DATE"
"$PYTHON" scripts/verify_index_pe_history.py --baskets SPY,QQQ,SOXX \
  --years 5 --as-of "$SNAPSHOT_DATE" --sample 50 --mode ro

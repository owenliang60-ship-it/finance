#!/usr/bin/env bash
# Daily market data + report pipeline for cloud cron.
#
# Deprecated after the cron split into:
# - run_market_data_pipeline.sh
# - run_market_report_pipeline.sh
#
# Kept as a compatibility wrapper for manual use.

set -euo pipefail

PROJECT_DIR="${FINANCE_PROJECT_DIR:-/root/workspace/Finance}"
BROAD_INCREMENTAL_DAYS="${BROAD_INCREMENTAL_DAYS:-7}"

cd "$PROJECT_DIR"
source .env 2>/dev/null || true

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi

log_step() {
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') $* ==="
}

run_step() {
  local name="$1"
  shift
  log_step "BEGIN $name"
  "$@"
  log_step "OK $name"
}

log_step "daily market pipeline START"
run_step "pool_price_fmp" "$PYTHON" scripts/update_data.py --price
run_step "broad_price_yfinance" "$PYTHON" scripts/update_extended_prices.py \
  --universe broad --incremental --incremental-days "$BROAD_INCREMENTAL_DAYS"
run_step "pool_options_iv" "$PYTHON" scripts/update_options_iv.py
run_step "broad_market_scan" "$PYTHON" scripts/broad_market_scan.py
run_step "morning_report" "$PYTHON" scripts/morning_report.py --no-social --image-report --image-delivery pdf
log_step "daily market pipeline DONE"

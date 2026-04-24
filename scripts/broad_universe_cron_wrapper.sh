#!/bin/bash
set -euo pipefail

cd /root/workspace/Finance
source .env 2>/dev/null || true

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi
MODE="${1:-unknown}"
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/cron_broad_${MODE}_$(date +%Y%m%d).log"

log() {
  echo "=== $(date '+%F %T') $* ===" >> "$LOG"
}

run_step() {
  local name="$1"
  shift
  log "BEGIN $name"
  if "$@" >> "$LOG" 2>&1; then
    log "OK $name"
  else
    local rc=$?
    log "FAIL $name rc=$rc"
    exit "$rc"
  fi
}

log "broad_universe cron MODE=$MODE"

case "$MODE" in
  daily_hmcap)
    run_step "daily_hmcap" "$PYTHON" scripts/fetch_historical_mcap.py \
      --universe broad --incremental --incremental-days 7
    ;;
  daily_price)
    run_step "daily_price" "$PYTHON" scripts/update_extended_prices.py \
      --universe broad --incremental --incremental-days 7
    ;;
  weekly_refresh)
    run_step "refresh_seed" "$PYTHON" -m src.data.broad_universe_manager --refresh-seed
    run_step "hmcap_new_seed" "$PYTHON" scripts/fetch_historical_mcap.py \
      --universe broad_seed --years 5 --skip-existing
    run_step "finalize" "$PYTHON" -m src.data.broad_universe_manager --finalize
    run_step "hmcap_new_final" "$PYTHON" scripts/fetch_historical_mcap.py \
      --universe broad --incremental-new-symbols
    run_step "price_new_final" "$PYTHON" scripts/update_extended_prices.py \
      --universe broad --incremental-new-symbols
    ;;
  *)
    echo "Unknown mode: $MODE" >&2
    exit 2
    ;;
esac

log "DONE MODE=$MODE"

#!/bin/bash
set -euo pipefail

PROJECT_DIR="${FINANCE_PROJECT_DIR:-/root/workspace/Finance}"
LOCK_DIR="${FINANCE_CRON_LOCK_DIR:-/tmp/finance-cron-locks}"
cd "$PROJECT_DIR"
if [ -f ".env" ]; then
  source .env
fi

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi
MODE="${1:-unknown}"
MARKET_WRITER_WAIT_SECONDS="${FINANCE_MARKET_WRITER_WAIT_SECONDS:-1800}"
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

run_step_nonblocking() {
  local name="$1"
  shift
  log "BEGIN $name (nonblocking)"
  if "$@" >> "$LOG" 2>&1; then
    log "OK $name"
  else
    local rc=$?
    log "WARN $name rc=$rc (nonblocking, continuing)"
  fi
}

run_step_with_market_writer_lock() {
  local wait_seconds="$1"
  local name="$2"
  shift 2
  local lock_path="$LOCK_DIR/resource-market_db_writer.lock"
  mkdir -p "$LOCK_DIR"
  exec 8>"$lock_path"
  if ! flock -w "$wait_seconds" 8; then
    log "FAIL $name rc=75 market_db_writer lock busy after ${wait_seconds}s"
    exec 8>&-
    return 75
  fi
  local rc=0
  log "BEGIN $name"
  "$@" >> "$LOG" 2>&1 || rc=$?
  if [ "$rc" -eq 0 ]; then
    log "OK $name"
  else
    log "FAIL $name rc=$rc"
  fi
  flock -u 8 || true
  exec 8>&-
  return "$rc"
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
    extended_rc=0
    run_step_with_market_writer_lock "$MARKET_WRITER_WAIT_SECONDS" \
      "refresh_extended" "$PYTHON" \
      -m src.data.extended_universe_manager --refresh || extended_rc=$?
    concept_rc=0
    run_step_with_market_writer_lock 0 "concept_weekly_sync" "$PYTHON" \
      scripts/build_company_concept_registry.py --weekly-sync || concept_rc=$?
    if [ "$concept_rc" -ne 0 ]; then
      log "WARN concept_weekly_sync rc=$concept_rc (nonblocking, continuing)"
    fi
    if [ "$extended_rc" -ne 0 ]; then
      log "FAIL weekly_refresh refresh_extended_rc=$extended_rc after concept sync"
      exit "$extended_rc"
    fi
    ;;
  *)
    echo "Unknown mode: $MODE" >&2
    exit 2
    ;;
esac

log "DONE MODE=$MODE"

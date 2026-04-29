#!/usr/bin/env bash
# Daily market report pipeline for cloud cron.
# Sends the merged broad scan + morning report after data collection has had time to settle.

set -u

PROJECT_DIR="${FINANCE_PROJECT_DIR:-/root/workspace/Finance}"

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
  if "$@"; then
    log_step "OK $name"
  else
    local rc=$?
    log_step "FAIL $name rc=$rc"
    return "$rc"
  fi
}

RC=0
log_step "daily market report pipeline START"
run_step "morning_report" "$PYTHON" scripts/morning_report.py --no-social --image-report --image-delivery pdf || RC=$?
if [ "$RC" -eq 0 ]; then
  log_step "daily market report pipeline DONE"
else
  log_step "daily market report pipeline DONE_WITH_FAILURE rc=$RC"
fi
exit "$RC"

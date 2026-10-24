#!/usr/bin/env bash
# Cloud entry. Direct/manual invocation acquires exactly the same two locks as cron.
set -euo pipefail
PROJECT_DIR="${FINANCE_PROJECT_DIR:-/root/workspace/Finance}"
LOCK_DIR="${FINANCE_CRON_LOCK_DIR:-/tmp/finance-cron-locks}"
PYTHON="${FINANCE_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
job_lock="$(readlink /proc/$$/fd/9 2>/dev/null || true)"
resource_lock="$(readlink /proc/$$/fd/8 2>/dev/null || true)"
if [[ "$job_lock" != "$LOCK_DIR/finance_prosperity.lock" ]]; then
    exec env FINANCE_CRON_RESOURCE_KEY=market_db_writer FINANCE_CRON_LOCK_BUSY_RC=75 \
        "$PROJECT_DIR/scripts/cron_wrapper.sh" finance_prosperity cron_prosperity.log "$0" "$@"
fi
if [[ "$resource_lock" != "$LOCK_DIR/resource-market_db_writer.lock" ]]; then
    echo 'prosperity: market_db_writer lock is required' >&2
    exit 75
fi
cd "$PROJECT_DIR"
if [[ "$#" -eq 0 ]]; then
    set -- --live
fi
exec "$PYTHON" "$PROJECT_DIR/scripts/prosperity_weekly.py" \
    --db "$PROJECT_DIR/data/prosperity.db" --market-db "$PROJECT_DIR/data/market.db" \
    --quality-dir "$PROJECT_DIR/data/quality" --backup-dir "$PROJECT_DIR/data/prosperity-backups" "$@"

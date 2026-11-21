#!/usr/bin/env bash
# BitCow 日线信号监控入口。
# cron 经 cron_wrapper.sh 调用（锁、日志、失败告警）；手动调用可透传 --dry-run / --force / --label。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${FINANCE_PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PYTHON="${FINANCE_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
    PYTHON="python3"
fi
cd "$PROJECT_DIR"
exec "$PYTHON" "$PROJECT_DIR/scripts/bitcow_monitor.py" "$@"

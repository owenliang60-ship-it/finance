#!/bin/bash
# launchd Step A — 每周日 09:50 (Asia/Shanghai) 检测股票主题增量
# 带并发锁、日志轮转与时间戳；0 待标时自动写出 0 行 outbox/payload.json 触发字典心跳。

set -u
cd "/Users/owen/CC workspace/Finance" || exit 1

LOCKDIR="/tmp/finance-theme-detect.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') skipped — another theme detect is running ==="
    exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

LOG="logs/launchd_theme_detect.log"
mkdir -p logs
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    mv "$LOG" "${LOG}.bak"
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') theme Step A (--detect-delta) ==="
.venv/bin/python scripts/theme_basket_sync.py --detect-delta 2>&1
RC=$?
echo "exit=$RC"
echo ""
exit $RC

#!/bin/bash
# launchd 调用入口 — 每日 09:00 自动 pull 云端数据
# 带时间戳日志 + 并发锁 + 日志轮转

cd "/Users/owen/CC workspace/Finance" || exit 1

# ── 并发锁 (防止 launchd 补跑与手动执行冲突) ──
LOCKDIR="/tmp/finance-sync-pull.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') skipped — another sync is running ==="
    exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

# ── 日志轮转 (>1MB 时备份) ──
LOG="logs/launchd_pull.log"
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    mv "$LOG" "${LOG}.bak"
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') scheduled pull ==="
./sync_to_cloud.sh --pull 2>&1
RC=$?
echo "exit=$RC"
echo ""

exit $RC

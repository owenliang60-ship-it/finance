#!/bin/bash
# launchd Step C — 每周日 10:08 & 10:25 (Asia/Shanghai) 提交 outbox 并重放 pending
# 使用受限 SSH 通道 aliyun-theme-sync（仅允许 --apply-cloud-stdin）。
# 若 outbox/payload.json 与 pending/*.json 均不存在，静默退出 0。

set -u
cd "/Users/owen/CC workspace/Finance" || exit 1

LOCKDIR="/tmp/finance-theme-commit.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') skipped — another theme commit is running ==="
    exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

LOG="logs/launchd_theme_commit.log"
mkdir -p logs
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    mv "$LOG" "${LOG}.bak"
fi

export THEME_SYNC_SSH_HOST="${THEME_SYNC_SSH_HOST:-aliyun-theme-sync}"
EXCHANGE_DIR="${THEME_SYNC_EXCHANGE_DIR:-$HOME/.local/share/finance-theme-sync}"
PAYLOAD="$EXCHANGE_DIR/outbox/payload.json"

RC=0
DID_WORK=0
if [ -f "$PAYLOAD" ]; then
    DID_WORK=1
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') theme Step C (--commit-outbox via $THEME_SYNC_SSH_HOST) ==="
    .venv/bin/python scripts/theme_basket_sync.py --commit-outbox 2>&1
    RC=$?
    echo "commit-outbox exit=$RC"
fi

shopt -s nullglob
PENDING_FILES=("$EXCHANGE_DIR"/pending/*.json)
shopt -u nullglob
if [ "${#PENDING_FILES[@]}" -gt 0 ] && [ "$RC" -eq 0 ]; then
    DID_WORK=1
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') theme Step C (--replay-pending, count=${#PENDING_FILES[@]}) ==="
    .venv/bin/python scripts/theme_basket_sync.py --replay-pending 2>&1
    RC=$?
    echo "replay-pending exit=$RC"
fi

if [ "$DID_WORK" -eq 0 ]; then
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') theme Step C — nothing to commit or replay ==="
fi

echo ""
exit $RC

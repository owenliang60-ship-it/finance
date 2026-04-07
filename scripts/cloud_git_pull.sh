#!/bin/bash
# 云端 git pull — 由 crontab 每日 06:25 调用
# 确保云端代码与 GitHub main 分支同步
# 失败时发 Telegram 告警，防止代码不同步静默持续
cd /root/workspace/Finance
source .env 2>/dev/null
mkdir -p logs

LOG=logs/git_pull.log
OUTPUT=$(git pull --ff-only origin main 2>&1)
RC=$?

echo "$OUTPUT" >> "$LOG"
echo "$(date '+%Y-%m-%d %H:%M:%S') pull exit=$RC" >> "$LOG"

if [ $RC -ne 0 ]; then
    MSG=$(printf "⚠️ 云端 git pull 失败\n\nexit=%d\n%.500s" "$RC" "$OUTPUT")
    curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        -d "text=$MSG" > /dev/null 2>&1
fi

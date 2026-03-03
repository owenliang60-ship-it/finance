#!/bin/bash
# 云端 git pull — 由 crontab 每日 06:25 调用
# 确保云端代码与 GitHub main 分支同步
cd /root/workspace/Finance
mkdir -p logs
git pull --ff-only origin main >> logs/git_pull.log 2>&1
echo "$(date '+%Y-%m-%d %H:%M:%S') pull exit=$?" >> logs/git_pull.log

#!/bin/bash
# 社交情绪日报 wrapper — 10:20 独立发送
# 配合 social sentiment 数据采集延后至 10:05（等 Adanos X 数据刷新）
source /root/workspace/Finance/.env 2>/dev/null
cd /root/workspace/Finance
python3 scripts/morning_report.py --social-only

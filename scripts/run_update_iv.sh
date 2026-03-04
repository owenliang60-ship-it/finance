#!/bin/bash
# IV 日频更新 wrapper - 加载环境变量
source /root/workspace/Finance/.env 2>/dev/null
cd /root/workspace/Finance
python3 scripts/update_options_iv.py "$@"

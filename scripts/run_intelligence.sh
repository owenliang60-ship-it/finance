#!/bin/bash
# Portfolio Intelligence wrapper - 加载环境变量
set -a
source /root/workspace/Finance/.env 2>/dev/null
set +a
cd /root/workspace/Finance
python3 scripts/portfolio_intelligence.py --image-report --image-delivery pdf "$@"

#!/bin/bash
# Portfolio Intelligence wrapper - 加载环境变量
source /root/workspace/Finance/.env 2>/dev/null
cd /root/workspace/Finance
python3 scripts/portfolio_intelligence.py "$@"

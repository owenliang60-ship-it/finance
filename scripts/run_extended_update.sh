#!/bin/bash
# Extended Universe Price Update — cloud cron wrapper
#
# Daily (Tue-Sat 06:40): incremental update
#   40 6 * * 2-6 /root/workspace/Finance/scripts/run_extended_update.sh >> /root/workspace/Finance/logs/cron_extended.log 2>&1
#
# Saturday (07:00): refresh universe list + update
#   0 7 * * 6 /root/workspace/Finance/scripts/run_extended_update.sh --refresh-universe >> /root/workspace/Finance/logs/cron_extended.log 2>&1

set -euo pipefail

cd /root/workspace/Finance
source .env 2>/dev/null || true

echo "============================================================"
echo "Extended Universe Price Update — $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

python3 scripts/update_extended_prices.py "$@"

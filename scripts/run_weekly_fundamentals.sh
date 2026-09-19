#!/usr/bin/env bash
# Saturday sequence: Core refresh -> quality repair/re-audit -> Premium gate.
set -euo pipefail

PROJECT_DIR="${FINANCE_PROJECT_DIR:-/root/workspace/Finance}"
RUN_UPDATE_DATA="${FINANCE_RUN_UPDATE_DATA:-$PROJECT_DIR/scripts/run_update_data.sh}"
PYTHON="${FINANCE_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
PREMIUM_BUILDER="${FINANCE_PREMIUM_BUILDER:-$PROJECT_DIR/scripts/build_premium_pool.py}"
QUALITY_CHECKER="${FINANCE_QUALITY_CHECKER:-$PROJECT_DIR/scripts/check_fundamental_quality.py}"
QUALITY_REPORT="${FINANCE_QUALITY_REPORT:-$PROJECT_DIR/data/quality/fundamentals-$(date -u +%Y%m%dT%H%M%SZ).json}"

if [ ! -x "$PYTHON" ]; then
  PYTHON="python3"
fi

cd "$PROJECT_DIR"
"$RUN_UPDATE_DATA" --fundamental
quality_rc=0
"$PYTHON" "$QUALITY_CHECKER" --repair --no-lock --max-targets 200 \
  --report "$QUALITY_REPORT" || quality_rc=$?
# Data-quality faults stay visible, but do not redefine the existing 95% gate.
# Store/lock/execution errors cannot support a valid build and stop here.
case "$quality_rc" in
  0|1) ;;
  *) exit "$quality_rc" ;;
esac
"$PYTHON" "$PREMIUM_BUILDER"
if [ "$quality_rc" -ne 0 ]; then
  echo "Premium published; fundamental quality unresolved rc=$quality_rc report=$QUALITY_REPORT"
  exit "$quality_rc"
fi

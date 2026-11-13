#!/bin/bash
# one-off spike: replay missing Saturdays on scratch copies; never touches the real prosperity.db
W=$1; shift
WT="/Users/owen/CC workspace/Finance/.worktrees/prosperity-m16"
D="/Users/owen/CC workspace/Finance/data"
mkdir -p /tmp/m16-history/h$W
"/Users/owen/CC workspace/Finance/.venv/bin/python" - <<PY
import sqlite3
src = sqlite3.connect('file:/tmp/m16-history/prosperity.db?mode=ro', uri=True)
dst = sqlite3.connect('/tmp/m16-history/h$W/prosperity.db'); src.backup(dst); dst.close(); src.close()
PY
cd "$WT"
for day in "$@"; do
  start=$(date +%s)
  "/Users/owen/CC workspace/Finance/.venv/bin/python" scripts/prosperity_weekly.py --as-of "$day" --db /tmp/m16-history/h$W/prosperity.db --market-db "$D/market.db" --quality-dir "$D/quality" > /tmp/m16-history/h$W/$day.out 2> /tmp/m16-history/h$W/$day.err
  echo "$day rc=$? $(( $(date +%s) - start ))s" >> /tmp/m16-history/h$W/log.txt
done
echo done >> /tmp/m16-history/h$W/log.txt

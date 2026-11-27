#!/usr/bin/env bash
set -euo pipefail

DIR="/Users/owen/.gemini/config/sidecars/daily-technical-analysis"
FINANCE_PY="/Users/owen/CC workspace/Finance/.venv/bin/python"
PY_BIN="python3"
if [[ -x "$FINANCE_PY" ]]; then
  PY_BIN="$FINANCE_PY"
fi

if [[ $# -ge 1 && -f "$1" ]]; then
  RAW_INPUT="$(cat "$1")"
else
  RAW_INPUT="$(cat)"
fi

CHUNKS_JSON="$(printf '%s' "$RAW_INPUT" | "$PY_BIN" "$DIR/chunk_telegram.py")"

printf '%s' "$CHUNKS_JSON" | ssh -o ConnectTimeout=15 aliyun "cd /root/workspace/Finance && set -a && source .env && set +a && python3 -c '
import json, sys
from src.telegram_bot import send_message

chunks = json.loads(sys.stdin.read())
total = len(chunks)
ok_all = True
for i, chunk in enumerate(chunks, 1):
    prefix = f\"*【每日技术分析 {i}/{total}】*\\n\" if total > 1 else \"\"
    ok = send_message(prefix + chunk, channel=\"group\")
    print(f\"Chunk {i}/{total} ({len(chunk)} chars) sent: {ok}\")
    ok_all = ok_all and ok
if not ok_all:
    sys.exit(1)
'"

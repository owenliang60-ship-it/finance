#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <local_html_path> [caption]" >&2
  exit 2
fi

HTML_PATH="$1"
CAPTION="${2:-}"

if [[ ! -f "$HTML_PATH" ]]; then
  echo "ERROR: HTML file not found: $HTML_PATH" >&2
  exit 1
fi

BASENAME="$(basename "$HTML_PATH")"

python3 - "$HTML_PATH" "$BASENAME" "$CAPTION" <<'PY' | ssh -o ConnectTimeout=15 aliyun "cd /root/workspace/Finance && set -a && source .env && set +a && python3 -c '
import base64, json, sys
from pathlib import Path
from src.telegram_bot import send_document

payload = json.loads(sys.stdin.read())
fname = payload[\"filename\"]
tmp_path = Path(\"/tmp\") / fname
tmp_path.write_bytes(base64.b64decode(payload[\"content_b64\"]))
try:
    ok = send_document(str(tmp_path), caption=payload.get(\"caption\", \"\"), channel=\"group\", timeout=60)
    print(f\"Telegram HTML document sent ({fname}): {ok}\")
    if not ok:
        sys.exit(1)
finally:
    try:
        tmp_path.unlink()
    except OSError:
        pass
'"
import base64, json, sys
from pathlib import Path

html_path, filename, caption = sys.argv[1], sys.argv[2], sys.argv[3]
content_b64 = base64.b64encode(Path(html_path).read_bytes()).decode("ascii")
print(json.dumps({"filename": filename, "caption": caption, "content_b64": content_b64}))
PY

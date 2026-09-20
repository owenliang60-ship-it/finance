#!/usr/bin/env bash
# 每周加密报告 wrapper。
#
# 与每日量化扫描（quant_daily_scan）共享同一把资源锁：08:06 日扫若仍在运行，
# 09:00 周报会在有限时间内等待而不是静默跳过。拥有独立的周报日志与失败告警。
set -u

QUANT_DIR="${QUANT_DIR:-/root/workspace/Quant}"
SCANNERS_DIR="$QUANT_DIR/scanners"
ENV_FILE="${QUANT_ENV_FILE:-$QUANT_DIR/.env}"
LOCK_DIR="/tmp/quant-cron-locks"
# 与 quant_daily_scan cron 完全相同的资源锁路径。
LOCK_FILE="/tmp/quant-cron-locks/quant_daily_scan.lock"
LOG_DIR="${QUANT_LOG_DIR:-$QUANT_DIR/logs}"
LOG_FILE="$LOG_DIR/quant_weekly_scan.log"
LOCK_WAIT_SECONDS="${QUANT_WEEKLY_LOCK_WAIT_SECONDS:-1800}"

mkdir -p "$LOCK_DIR" "$LOG_DIR"

if [ -f "$ENV_FILE" ]; then
  # 子进程 Python 扫描器需要读取 Telegram 变量，必须 export。
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

timestamp() {
  date '+%Y-%m-%d %H:%M:%S %Z'
}

log_line() {
  printf '[%s] [quant_weekly_scan] %s\n' "$(timestamp)" "$*" >> "$LOG_FILE"
}

send_alert() {
  local rc="$1"
  if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
    return 0
  fi
  local host tail_text message
  host="$(hostname 2>/dev/null || echo unknown-host)"
  tail_text="$(tail -40 "$LOG_FILE" 2>/dev/null | tail -c 3000)"
  message="$(printf 'Quant weekly scan failed\nhost=%s\nrc=%s\nlog=%s\n\n%s' \
    "$host" "$rc" "$LOG_FILE" "$tail_text")"
  curl -fsS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${message}" >/dev/null 2>&1 || true
}

# 共享日扫锁：等待上限默认30分钟，超时告警并非零退出。
exec 9>"$LOCK_FILE"
if ! flock -w "$LOCK_WAIT_SECONDS" 9; then
  log_line "FAIL lock busy after ${LOCK_WAIT_SECONDS}s lock=$LOCK_FILE"
  send_alert 75
  exit 75
fi

START_TS="$(date +%s)"
log_line "BEGIN weekly_scan_all.py"

cd "$SCANNERS_DIR" || {
  log_line "FAIL missing scanners dir: $SCANNERS_DIR"
  send_alert 2
  exit 2
}
python3 weekly_scan_all.py >> "$LOG_FILE" 2>&1
RC="$?"

END_TS="$(date +%s)"
DURATION="$((END_TS-START_TS))"

if [ "$RC" -eq 0 ]; then
  log_line "OK duration=${DURATION}s"
else
  log_line "FAIL rc=$RC duration=${DURATION}s"
  send_alert "$RC"
fi

exit "$RC"

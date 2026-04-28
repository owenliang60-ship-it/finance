#!/usr/bin/env bash
# Shared cloud cron wrapper for Finance jobs.
# Adds one lock, one log format, and one failure alert path.

set -u

PROJECT_DIR="${FINANCE_PROJECT_DIR:-/root/workspace/Finance}"
ENV_FILE="${FINANCE_ENV_FILE:-$PROJECT_DIR/.env}"
LOG_DIR="${FINANCE_LOG_DIR:-$PROJECT_DIR/logs}"
LOCK_DIR="${FINANCE_CRON_LOCK_DIR:-/tmp/finance-cron-locks}"

usage() {
  echo "Usage: $0 <job_name> <log_file> <command> [args...]" >&2
}

if [ "$#" -lt 3 ]; then
  usage
  exit 2
fi

JOB_NAME="$1"
shift
LOG_FILE="$1"
shift

case "$LOG_FILE" in
  /*) ;;
  *) LOG_FILE="$LOG_DIR/$LOG_FILE" ;;
esac

mkdir -p "$(dirname "$LOG_FILE")" "$LOCK_DIR"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

timestamp() {
  date '+%Y-%m-%d %H:%M:%S %Z'
}

log_line() {
  printf '[%s] [%s] %s\n' "$(timestamp)" "$JOB_NAME" "$*" >> "$LOG_FILE"
}

send_alert() {
  local rc="$1"
  local host
  local tail_text
  local message

  if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
    return 0
  fi

  host="$(hostname 2>/dev/null || echo unknown-host)"
  tail_text="$(tail -40 "$LOG_FILE" 2>/dev/null | tail -c 3000)"
  message="$(printf 'Finance cron failed\njob=%s\nhost=%s\nrc=%s\nlog=%s\n\n%s' "$JOB_NAME" "$host" "$rc" "$LOG_FILE" "$tail_text")"

  curl -fsS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${message}" >/dev/null 2>&1 || true
}

LOCK_FILE="$LOCK_DIR/${JOB_NAME}.lock"
exec 9>"$LOCK_FILE"

if ! flock -n 9; then
  log_line "SKIP locked lock=$LOCK_FILE"
  exit 0
fi

START_TS="$(date +%s)"
log_line "BEGIN command=$*"

"$@" >> "$LOG_FILE" 2>&1
RC="$?"

END_TS="$(date +%s)"
DURATION="$((END_TS - START_TS))"

if [ "$RC" -eq 0 ]; then
  log_line "OK duration=${DURATION}s"
else
  log_line "FAIL rc=$RC duration=${DURATION}s"
  send_alert "$RC"
fi

exit "$RC"

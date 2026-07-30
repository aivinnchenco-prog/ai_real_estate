#!/data/data/com.termux/files/usr/bin/bash
# =============================================================================
# notify.sh — отправка сообщений в Telegram.
#
# Вынесено отдельно, чтобы run_loop не разрастался и чтобы можно было дёрнуть
# уведомление вручную:  notify.sh "текст"
#
# Настройки берутся из ~/.publisher/runner.env:
#   TG_BOT_TOKEN, TG_CHAT_ID, NOTIFY_OK, NOTIFY_FAIL, NOTIFY_IDLE
# Если токен не задан — возвращает код 2. Раннер продолжает работать, но может
# сохранить обязательный отчёт в локальный outbox и повторить его позже.
# =============================================================================
set -uo pipefail

HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
CONF="$HOME_DIR/.publisher/runner.env"
STATE_DIR="$HOME_DIR/.publisher"
mkdir -p "$STATE_DIR"

# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"

TG_BOT_TOKEN="${TG_BOT_TOKEN:-}"
TG_CHAT_ID="${TG_CHAT_ID:-}"
DEVICE_LABEL="${DEVICE_LABEL:-phone}"

MSG="${1:-}"
[ -z "$MSG" ] && exit 0
[ -z "$TG_BOT_TOKEN" ] && exit 2
[ -z "$TG_CHAT_ID" ] && exit 2

# --- защита от спама одинаковыми сообщениями ---------------------------------
# Второй аргумент — ключ дедупликации. Если такое же сообщение уже уходило
# меньше DEDUP_MINUTES назад, не повторяем. Нужно, чтобы падающий канал
# не заваливал чат каждые пять минут.
DEDUP_KEY="${2:-}"
DEDUP_MINUTES="${DEDUP_MINUTES:-60}"
if [ -n "$DEDUP_KEY" ]; then
  HASH_FILE="$STATE_DIR/notify_$(printf '%s' "$DEDUP_KEY" | md5sum | cut -c1-12)"
  NEW_HASH="$(printf '%s' "$MSG" | md5sum | cut -c1-32)"
  if [ -f "$HASH_FILE" ]; then
    OLD_HASH="$(head -n1 "$HASH_FILE" 2>/dev/null)"
    AGE_MIN=$(( ( $(date +%s) - $(stat -c %Y "$HASH_FILE" 2>/dev/null || echo 0) ) / 60 ))
    if [ "$OLD_HASH" = "$NEW_HASH" ] && [ "$AGE_MIN" -lt "$DEDUP_MINUTES" ]; then
      exit 0
    fi
  fi
fi

# --- отправка ----------------------------------------------------------------
# --max-time, чтобы мёртвая сеть не подвесила раннер;
# дедуп-маркер пишется только ПОСЛЕ подтверждённого ответа Telegram. Иначе
# временный сетевой сбой подавлял повтор обязательного отчёта на целый час.
RESPONSE="$(
  curl -sS --max-time 20 \
  -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TG_CHAT_ID}" \
  --data-urlencode "text=[${DEVICE_LABEL}] ${MSG}" \
  --data "disable_web_page_preview=true" \
  2>/dev/null
)" || exit 1

printf '%s' "$RESPONSE" | grep -qE '"ok"[[:space:]]*:[[:space:]]*true' || exit 1
[ -n "$DEDUP_KEY" ] && printf '%s\n' "$NEW_HASH" > "$HASH_FILE"
exit 0

#!/data/data/com.termux/files/usr/bin/bash
# =============================================================================
# run_loop.sh — вечный цикл раннера на телефоне.
#
# Каждый проход:
#   1) держит wake-lock (иначе Doze усыпит Termux и всё встанет);
#   2) переподключается к самому себе (порт мог смениться после ребута);
#   3) уважает тихие часы;
#   4) вызывает publisher_social;
#   5) разбирает вывод и пишет в Telegram, что получилось.
#
# Логи: ~/.publisher/runner.log
# =============================================================================
set -uo pipefail

HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
CONF="$HOME_DIR/.publisher/runner.env"
LOG="$HOME_DIR/.publisher/runner.log"
BIN_DIR="$HOME_DIR/bin"
STATE_DIR="$HOME_DIR/.publisher"
mkdir -p "$STATE_DIR"

# значения по умолчанию, переопределяются из runner.env
PROJECT_DIR="$HOME_DIR/publisher-social"
RUN_CMD="queue --dry-run"
POLL_SECONDS=300
QUIET_START=23
QUIET_END=8
WAKELOCK=1
NOTIFY_OK=1            # писать об успешных публикациях
NOTIFY_FAIL=1          # писать о провалах
NOTIFY_IDLE=0          # писать, когда очередь пуста (обычно шум)
DAILY_DIGEST_HOUR=21   # час, когда прислать сводку за день (-1 = выключить)
MAX_LOG_MB=20
STOP_AFTER_CHANNEL=1   # после результата live-канала отправить отчёт и завершить раннер
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"
export PROJECT_DIR
export TZ="${TZ:-Asia/Bangkok}"

UI_LOCK="$STATE_DIR/ui.flock"
PIDFILE="$STATE_DIR/runner.pid"
PENDING_REPORT="$STATE_DIR/pending_channel_report.txt"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }
notify() { "$BIN_DIR/notify.sh" "$1" "${2:-}" 2>/dev/null; }

is_live_run() {
  case " $RUN_CMD " in
    *" --live "*) return 0 ;;
    *) return 1 ;;
  esac
}

send_channel_report() {
  local message="$1" key="${2:-channel_report}" tmp
  tmp="${PENDING_REPORT}.$$"
  printf '%s\n' "$message" > "$tmp"
  mv "$tmp" "$PENDING_REPORT"
  if notify "$message" "$key"; then
    rm -f "$PENDING_REPORT"
    return 0
  fi
  log "обязательный отчёт сохранён для повторной отправки: $PENDING_REPORT"
  return 1
}

flush_pending_report() {
  local message
  [ -s "$PENDING_REPORT" ] || return 0
  message="$(cat "$PENDING_REPORT")"
  if notify "$message" "pending_channel_report"; then
    rm -f "$PENDING_REPORT"
    log "отложенный отчёт успешно отправлен"
    return 0
  fi
  log "отложенный отчёт пока не отправлен; сохранён для следующего старта"
  return 1
}

cleanup() {
  flock -u 9 2>/dev/null || true
  if [ -f "$PIDFILE" ] && [ "$(cat "$PIDFILE" 2>/dev/null)" = "$$" ]; then
    rm -f "$PIDFILE"
  fi
  [ "${WAKELOCK:-1}" = "1" ] && termux-wake-unlock 2>/dev/null || true
  log "раннер остановлен"
  notify "🛑 Раннер остановлен" || true
}
trap cleanup EXIT INT TERM

in_quiet_hours() {
  local h; h="$(date '+%-H')"
  if [ "$QUIET_START" -gt "$QUIET_END" ]; then
    [ "$h" -ge "$QUIET_START" ] || [ "$h" -lt "$QUIET_END" ]
  else
    [ "$h" -ge "$QUIET_START" ] && [ "$h" -lt "$QUIET_END" ]
  fi
}

# небольшой человеческий разброс, чтобы не бить Notion строго по таймеру
jitter() { echo $(( RANDOM % 41 - 20 )); }   # ±20 сек

battery_pct() {
  termux-battery-status 2>/dev/null | grep -oE '"percentage": *[0-9]+' \
    | grep -oE '[0-9]+' || echo ""
}

# --- счётчики за сутки (для вечерней сводки) ---------------------------------
today() { date '+%Y-%m-%d'; }
counter_file() { echo "$STATE_DIR/daily_$(today).cnt"; }

bump() {           # bump ok|fail N
  local f; f="$(counter_file)"
  local key="$1" add="$2"
  local ok=0 fail=0
  [ -f "$f" ] && { ok="$(cut -d' ' -f1 "$f")"; fail="$(cut -d' ' -f2 "$f")"; }
  [ "$key" = "ok" ] && ok=$(( ok + add )) || fail=$(( fail + add ))
  printf '%s %s\n' "$ok" "$fail" > "$f"
}

rotate_log() {
  local sz; sz=$(( $(stat -c %s "$LOG" 2>/dev/null || echo 0) / 1048576 ))
  if [ "$sz" -ge "${MAX_LOG_MB:-20}" ]; then
    mv "$LOG" "${LOG}.1" 2>/dev/null || true
    log "лог обрезан (был ${sz}MB)"
  fi
}

log "=== старт раннера ==="
log "проект: $PROJECT_DIR | команда: $RUN_CMD | опрос: ${POLL_SECONDS}s | тихо: ${QUIET_START}-${QUIET_END}"
if ! flush_pending_report && is_live_run; then
  log "новый live-прогон не начат: сначала должен быть доставлен предыдущий отчёт"
  exit 4
fi
notify "🚀 Раннер запущен. Опрос каждые $(( POLL_SECONDS / 60 )) мин, тихие часы ${QUIET_START}:00–${QUIET_END}:00" || true

[ "${WAKELOCK:-1}" = "1" ] && { termux-wake-lock 2>/dev/null && log "wake-lock взят"; }

cd "$PROJECT_DIR" || { log "нет каталога $PROJECT_DIR"; exit 1; }
export PYTHONPATH=src
export PYTHONUNBUFFERED=1
# util-linux/flock ставится bootstrap.sh. Один открытый fd переиспользуется
# между циклами; kernel сам снимает lock при crash/SIGKILL.
exec 9>>"$UI_LOCK"

LAST_DIGEST=""

while true; do
  rotate_log

  # --- вечерняя сводка за день ----------------------------------------------
  H="$(date '+%-H')"
  if [ "${DAILY_DIGEST_HOUR:-21}" -ge 0 ] && [ "$H" = "$DAILY_DIGEST_HOUR" ] \
     && [ "$LAST_DIGEST" != "$(today)" ]; then
    f="$(counter_file)"; d_ok=0; d_fail=0
    [ -f "$f" ] && { d_ok="$(cut -d' ' -f1 "$f")"; d_fail="$(cut -d' ' -f2 "$f")"; }
    bat="$(battery_pct)"
    notify "📊 Сводка за $(today): опубликовано ${d_ok}, провалов ${d_fail}${bat:+, заряд ${bat}%}" || true
    LAST_DIGEST="$(today)"
    # чистим счётчики старше трёх дней
    find "$STATE_DIR" -name 'daily_*.cnt' -mtime +3 -delete 2>/dev/null || true
  fi

  if in_quiet_hours; then
    log "тихие часы — пропускаю цикл"
    sleep "$POLL_SECONDS"; continue
  fi

  # --- предупреждение о низком заряде ---------------------------------------
  BAT="$(battery_pct)"
  if [ -n "$BAT" ] && [ "$BAT" -lt 20 ]; then
    notify "🔋 Заряд ${BAT}% — проверь, подключён ли телефон к питанию" "low_battery" || true
  fi

  # Lock берётся ДО connect_self: тот меняет ADB-соединение и ANDROID_SERIAL,
  # поэтому тоже не должен пересекаться с другим UI/state-процессом.
  if ! flock -n 9; then
    log "UI занят другим процессом — пропускаю цикл"
    sleep 60
    continue
  fi

  # --- порт мог смениться (ребут, Wi-Fi) — чиним молча -----------------------
  if ! "$BIN_DIR/connect_self.sh" >>"$LOG" 2>&1; then
    flock -u 9
    log "не смог подключиться к устройству, жду 60с"
    notify "⚠️ Нет ADB-подключения к телефону. Проверь: Для разработчиков → Беспроводная отладка" "no_adb" || true
    sleep 60; continue
  fi

  # --- цикл публикации -------------------------------------------------------
  log "цикл публикации: $RUN_CMD"
  OUT_FILE="$STATE_DIR/last_run.out"
  # не даём одному объекту завесить раннер навсегда
  timeout --foreground --kill-after=30 1800 \
    python -m publisher_social $RUN_CMD >"$OUT_FILE" 2>&1
  RC=$?
  flock -u 9
  cat "$OUT_FILE" >> "$LOG"

  # publisher_social печатает строки вида:
  #   [OK] tiktok: ...        [OK skip] linkedin: ...        [FAIL] youtube_shorts: ...
  OK_LINES="$(grep -cE '^[[:space:]]*\[OK\]' "$OUT_FILE" 2>/dev/null || true)"
  FAIL_LINES="$(grep -cE '^[[:space:]]*\[FAIL\]' "$OUT_FILE" 2>/dev/null || true)"
  WARN_LINES="$(grep -cE '^[[:space:]]*\[WARN\]' "$OUT_FILE" 2>/dev/null || true)"
  SKIP_LINES="$(grep -cE '^[[:space:]]*\[(OK|FAIL|WARN) skip\]' "$OUT_FILE" 2>/dev/null || true)"
  OK_LINES="${OK_LINES:-0}"
  FAIL_LINES="${FAIL_LINES:-0}"
  WARN_LINES="${WARN_LINES:-0}"
  SKIP_LINES="${SKIP_LINES:-0}"
  OBJ="$(grep -oE '\b[A-Z]_[0-9]{8}_[0-9]{3}\b' "$OUT_FILE" 2>/dev/null | head -n1)"

  if [ "$RC" = "124" ]; then
    log "цикл прерван по таймауту (30 мин)"
    send_channel_report \
      "⏱ ${OBJ:-объект}: live-канал прерван по таймауту 30 мин. Автоповтор не запускается до следующей проверки state." \
      "timeout:${OBJ:-unknown}" || true
    bump fail 1
    if [ "${STOP_AFTER_CHANNEL:-1}" = "1" ] && is_live_run; then
      log "пошаговый режим: отчёт сохранён/отправлен, раннер завершён"
      break
    fi

  elif [ "$RC" = "3" ]; then
    log "publisher lock занят другим процессом — этот цикл пропущен"
    notify "⚠️ Publisher занят другим процессом; текущий цикл безопасно пропущен." "publisher_lock_busy" || true

  elif [ "$OK_LINES" -gt 0 ] || [ "$FAIL_LINES" -gt 0 ] || [ "$WARN_LINES" -gt 0 ]; then
    bump ok "$OK_LINES"
    bump fail "$FAIL_LINES"

    if [ "$FAIL_LINES" -gt 0 ] && [ "${NOTIFY_FAIL:-1}" = "1" ]; then
      # в сообщение кладём сами строки провалов — сразу видно, какой канал упал
      DETAIL="$(grep -E '^[[:space:]]*\[FAIL' "$OUT_FILE" | head -n 6 | cut -c1-160)"
      REPORT="❌ ${OBJ:-объект}: успешно ${OK_LINES}, провалов ${FAIL_LINES}
${DETAIL}"
    elif [ "$WARN_LINES" -gt 0 ] && [ "${NOTIFY_FAIL:-1}" = "1" ]; then
      DETAIL="$(grep -E '^[[:space:]]*\[WARN' "$OUT_FILE" | head -n 6 | cut -c1-160)"
      REPORT="⚠️ ${OBJ:-объект}: отправлено, но публикация не подтверждена ссылкой (${WARN_LINES})
${DETAIL}"
    elif [ "$OK_LINES" -gt 0 ] && [ "${NOTIFY_OK:-1}" = "1" ]; then
      DETAIL="$(grep -E '^[[:space:]]*\[OK\]' "$OUT_FILE" | head -n 6 | cut -c1-220)"
      REPORT="✅ ${OBJ:-объект}: опубликовано ${OK_LINES} канал(ов)
${DETAIL}"
    else
      REPORT="ℹ️ ${OBJ:-объект}: канал завершён; уведомления этого типа отключены"
    fi
    send_channel_report "$REPORT" "channel_report:${OBJ:-unknown}" || true
    log "итог цикла: ok=$OK_LINES warn=$WARN_LINES fail=$FAIL_LINES skip=$SKIP_LINES rc=$RC"
    if [ "${STOP_AFTER_CHANNEL:-1}" = "1" ] && is_live_run; then
      log "пошаговый режим: отчёт сохранён/отправлен, раннер завершён до ручного запуска"
      break
    fi

  elif [ "$SKIP_LINES" -gt 0 ]; then
    log "служебных пропусков: $SKIP_LINES; фактического прогона соцсети не было"

  elif [ "$RC" -ne 0 ]; then
    log "цикл завершился с ошибкой rc=$RC"
    DETAIL="$(tail -n 8 "$OUT_FILE" | cut -c1-160)"
    notify "❌ Ошибка раннера rc=${RC}${OBJ:+ (объект $OBJ)}
${DETAIL}" "runner_error" || true
    bump fail 1

  else
    # ни успехов, ни провалов — очередь пуста или cooldown
    log "нечего публиковать (rc=$RC)"
    [ "${NOTIFY_IDLE:-0}" = "1" ] && notify "💤 Очередь пуста" "idle" || true
  fi

  s=$(( POLL_SECONDS + $(jitter) ))
  log "сплю ${s}s"
  sleep "$s"
done

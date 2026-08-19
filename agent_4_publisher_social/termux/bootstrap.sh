#!/data/data/com.termux/files/usr/bin/bash
# =============================================================================
# Publisher social — bootstrap для Termux
#
# Ставит окружение так, чтобы телефон САМ себе был раннером: Termux управляет
# этим же телефоном через ADB по loopback, без USB, без компьютера, без рута.
#
# Запуск (один раз):
#   bash bootstrap.sh
#
# Предварительно на телефоне:
#   1) Termux и Termux:Boot — ТОЛЬКО из F-Droid, версии из Play Store устарели
#      и ломаются на новых Android.
#   2) Настройки → О телефоне → тапнуть «Номер сборки» 7 раз (режим разработчика).
#   3) Для инициализации loopback-пары один раз понадобится «Отладка по USB»
#      (подключить к любому компьютеру и разрешить) ИЛИ «Беспроводная отладка».
# =============================================================================
set -euo pipefail

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
PROJECT_DIR="${PROJECT_DIR:-$HOME_DIR/publisher-social}"
BIN_DIR="$HOME_DIR/bin"
export PROJECT_DIR

say()  { printf '\033[1;36m[bootstrap]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[ -d "$PREFIX" ] || die "Это не Termux. Запускай внутри Termux."

# --- 1. пакеты ---------------------------------------------------------------
say "Обновляю индекс пакетов…"
pkg update -y && pkg upgrade -y

say "Ставлю python, android-tools, git, jq, termux-api…"
pkg install -y python python-pip android-tools git jq curl util-linux termux-api termux-services

# termux-wake-lock живёт в termux-api; проверим наличие бинаря adb
command -v adb >/dev/null 2>&1 || die "adb не установился (пакет android-tools)"
command -v python >/dev/null 2>&1 || die "python не установился"

# --- 2. код проекта ----------------------------------------------------------
if [ -d "$PROJECT_DIR/.git" ]; then
  say "Проект уже есть в $PROJECT_DIR — обновляю (git pull)…"
  git -C "$PROJECT_DIR" pull --ff-only || warn "git pull не прошёл, продолжаю с текущим кодом"
elif [ -n "${PROJECT_GIT_URL:-}" ]; then
  say "Клонирую $PROJECT_GIT_URL → $PROJECT_DIR…"
  git clone "$PROJECT_GIT_URL" "$PROJECT_DIR"
else
  warn "PROJECT_GIT_URL не задан и $PROJECT_DIR пуст."
  warn "Скопируй папку «Publisher social» в $PROJECT_DIR вручную"
  warn "(termux-setup-storage → cp -r /sdcard/... $PROJECT_DIR), потом запусти снова."
  [ -d "$PROJECT_DIR/src/publisher_social" ] || die "нет кода проекта в $PROJECT_DIR"
fi

[ -f "$PROJECT_DIR/requirements.txt" ] || die "не вижу requirements.txt в $PROJECT_DIR"

# --- 3. python-зависимости ---------------------------------------------------
say "Ставлю зависимости (uiautomator2)…"
# pip управляется пакетным менеджером Termux: обновлять его через pip запрещено.
pkg install -y libxml2 libxslt python-lxml python-pillow
python -m pip install wheel
python -m pip install -r "$PROJECT_DIR/requirements.txt"
python -c 'import uiautomator2; print("uiautomator2 import: ok")'

# --- 4. личный конфиг --------------------------------------------------------
if [ ! -f "$PROJECT_DIR/.env" ]; then
  say "Создаю .env — впиши в него NOTION_API_KEY и NOTION_DB_ID."
  cat > "$PROJECT_DIR/.env" <<'ENV'
# --- Notion ---
NOTION_API_KEY=
NOTION_DB_ID=

# --- Android (loopback ADB на самом телефоне) ---
# Серийник проставит connect_self.sh после парринга. Не трогай вручную.
ANDROID_SERIAL=127.0.0.1:5555
ANDROID_MEDIA_DIR=/sdcard/Download/publisher_social
ENV
else
  say ".env уже есть — не трогаю."
fi

# --- 5. локальные настройки раннера ------------------------------------------
mkdir -p "$BIN_DIR" "$HOME_DIR/.publisher"
RUNNER_ENV="$HOME_DIR/.publisher/runner.env"
if [ ! -f "$RUNNER_ENV" ]; then
  cat > "$RUNNER_ENV" <<ENV
# Настройки Termux-раннера. Правится под себя.
PROJECT_DIR="$PROJECT_DIR"

# Безопасный режим по умолчанию: только читает очередь, не управляет UI.
# Live-режим включается позже отдельным осознанным изменением этого файла.
RUN_CMD="queue --dry-run"

# Пауза между циклами опроса Notion, секунд.
POLL_SECONDS=300

# Тихие часы по времени аудитории (Asia/Bangkok в конфиге проекта).
# В этот диапазон раннер не публикует. Формат — час 0..23.
QUIET_START=23
QUIET_END=8

# Держать телефон бодрым во время работы.
WAKELOCK=1

# Все расписания и тихие часы считаются по времени Пхукета.
TZ=Asia/Bangkok

# --- Telegram-уведомления ---
# Токен: @BotFather → /newbot. Chat ID: напиши боту, затем открой
#   https://api.telegram.org/bot<ТОКЕН>/getUpdates  и возьми chat.id
# Пусто = уведомления выключены, раннер работает молча.
TG_BOT_TOKEN=
TG_CHAT_ID=
# Подпись в сообщениях — чтобы различать телефоны при мультибренде
DEVICE_LABEL=phone-1

NOTIFY_OK=1              # писать об успешных публикациях
NOTIFY_FAIL=1            # писать о провалах
NOTIFY_IDLE=0            # писать, когда очередь пуста (обычно лишний шум)
DAILY_DIGEST_HOUR=21     # час вечерней сводки, -1 = выключить
DEDUP_MINUTES=60         # не повторять одинаковый алерт чаще, чем раз в час
MAX_LOG_MB=20
STOP_AFTER_FB_BATCH=1    # live: fb_groups→fb_marketplace batch → отчёт → остановка
ENV
  say "Создал $RUNNER_ENV"
fi

# Live-режим: если есть маркер ~/.publisher/live.armed — не откатываем.
# Иначе старый опасный дефолт с --live возвращаем в dry-run.
if grep -qE '^RUN_CMD=.*--live' "$RUNNER_ENV" 2>/dev/null; then
  if [ -f "$HOME_DIR/.publisher/live.armed" ]; then
    say "RUN_CMD=--live сохранён (есть ~/.publisher/live.armed)"
  else
    sed -i -E 's#^RUN_CMD=.*#RUN_CMD="queue --dry-run"#' "$RUNNER_ENV"
    warn "RUN_CMD содержал --live и был заменён на queue --dry-run (нет live.armed)."
  fi
fi

# --- 6. копируем управляющие скрипты в ~/bin ---------------------------------
say "Устанавливаю connect_self.sh, run_loop.sh, notify.sh, ctl…"
install -m 755 "$PROJECT_DIR/termux/bin/connect_self.sh" "$BIN_DIR/connect_self.sh"
install -m 755 "$PROJECT_DIR/termux/bin/run_loop.sh"    "$BIN_DIR/run_loop.sh"
install -m 755 "$PROJECT_DIR/termux/bin/notify.sh"      "$BIN_DIR/notify.sh"
install -m 755 "$PROJECT_DIR/termux/bin/ctl"            "$BIN_DIR/pub"

# PATH
if ! grep -q 'HOME/bin' "$HOME_DIR/.bashrc" 2>/dev/null; then
  echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME_DIR/.bashrc"
fi

# --- 7. автозапуск после перезагрузки (Termux:Boot) --------------------------
BOOT_DIR="$HOME_DIR/.termux/boot"
mkdir -p "$BOOT_DIR"
install -m 755 "$PROJECT_DIR/termux/service/boot_start.sh" "$BOOT_DIR/10-publisher.sh"
say "Автозапуск прописан в $BOOT_DIR/10-publisher.sh"

# --- 8. первичное подключение к самому себе ----------------------------------
say ""
say "Окружение готово. Теперь один раз спарим телефон сам с собой."
say "Запускаю connect_self.sh — следуй подсказкам на экране."
say ""
"$BIN_DIR/connect_self.sh" || warn "Парринг не завершён — запусти позже: connect_self.sh"

say ""
say "================= ГОТОВО ================="
say "Проверь устройство:   cd $PROJECT_DIR && PYTHONPATH=src python -m publisher_social check-device"
say "Тест без публикации:  cd $PROJECT_DIR && PYTHONPATH=src python -m publisher_social queue --dry-run"
say "Запустить раннер:     pub start      (управление: pub status | pub stop | pub logs)"
say "После перезагрузки телефона раннер поднимется сам (Termux:Boot)."
say "=========================================="

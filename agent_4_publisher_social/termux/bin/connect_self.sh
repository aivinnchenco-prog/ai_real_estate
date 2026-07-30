#!/data/data/com.termux/files/usr/bin/bash
# =============================================================================
# connect_self.sh — подключить телефон к самому себе по loopback ADB.
#
# Это сердце всей схемы «телефон сам себе раннер». Главная боль Android:
# порт беспроводной отладки МЕНЯЕТСЯ после каждой перезагрузки. Поэтому порт
# мы не хардкодим, а вычисляем — из mdns или из настроек системы.
#
# Идемпотентно: если уже подключены — просто выходит с успехом.
# =============================================================================
set -uo pipefail

HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
STATE="$HOME_DIR/.publisher/adb_serial"
mkdir -p "$(dirname "$STATE")"

say()  { printf '\033[1;36m[connect]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }

adb start-server >/dev/null 2>&1 || true

connected_serial() {
  adb devices | awk '/127\.0\.0\.1:.*device$/ {print $1; exit}'
}

persist_serial() {
  local serial="$1"
  local env_file="${PROJECT_DIR:-$HOME_DIR/publisher-social}/.env"
  echo "$serial" > "$STATE"
  if [ -f "$env_file" ]; then
    if grep -q '^ANDROID_SERIAL=' "$env_file"; then
      sed -i -E "s#^ANDROID_SERIAL=.*#ANDROID_SERIAL=$serial#" "$env_file"
    else
      echo "ANDROID_SERIAL=$serial" >> "$env_file"
    fi
    ok "ANDROID_SERIAL=$serial записан в .env"
  fi
}

# --- уже подключены? ---------------------------------------------------------
S="$(connected_serial)"
if [ -n "$S" ]; then
  ok "Уже подключено: $S"
  persist_serial "$S"
  exit 0
fi

# --- найти порт беспроводной отладки -----------------------------------------
# Способ 1: mDNS-сервис adb-tls-connect (самый надёжный на Android 11+)
find_port_mdns() {
  timeout 6 adb mdns services 2>/dev/null \
    | awk '/_adb-tls-connect/ {print $NF}' \
    | sed -E 's/.*:([0-9]+)$/\1/' \
    | head -n1
}

# Способ 2: из системных настроек (иногда доступно без root)
find_port_settings() {
  local p
  p="$(getprop service.adb.tls.port 2>/dev/null)"
  [ -n "$p" ] && { echo "$p"; return; }
  settings get global adb_wifi_port 2>/dev/null | grep -E '^[0-9]+$' || true
}

PORT="$(find_port_mdns)"
[ -z "$PORT" ] && PORT="$(find_port_settings)"

# --- подключение -------------------------------------------------------------
try_connect() {
  local port="$1"
  [ -z "$port" ] && return 1
  say "Пробую adb connect 127.0.0.1:$port"
  adb connect "127.0.0.1:$port" 2>&1 | grep -qiE 'connected|already' || return 1
  return 0
}

if [ -n "$PORT" ] && try_connect "$PORT"; then
  :
else
  # --- порт не нашёлся автоматически: разовый парринг ------------------------
  warn "Не нашёл порт беспроводной отладки автоматически."
  if [ "${NONINTERACTIVE:-0}" = "1" ]; then
    exit 1
  fi
  warn "Это нормально при ПЕРВОМ запуске — нужно спарить один раз."
  echo
  echo "  1. Настройки → Для разработчиков → «Отладка по Wi-Fi» (на Samsung),"
  echo "     либо «Беспроводная отладка» → ВКЛ"
  echo "  2. Внутри: «Подключить устройство с помощью кода подключения»"
  echo "     — покажет IP:ПОРТ и 6-значный код"
  echo "  3. Введи их ниже."
  echo
  read -r -p "  Порт СОПРЯЖЕНИЯ (из пункта 2, обычно 5-значный): " PAIR_PORT
  read -r -p "  6-значный код сопряжения: " PAIR_CODE
  say "Паррю 127.0.0.1:$PAIR_PORT…"
  if printf '%s\n' "$PAIR_CODE" | adb pair "127.0.0.1:$PAIR_PORT" 2>&1 | grep -qi 'Successfully'; then
    ok "Сопряжено."
  else
    warn "Сопряжение не удалось. Проверь порт/код и повтори: connect_self.sh"
    exit 1
  fi
  # после парринга порт ПОДКЛЮЧЕНИЯ (не сопряжения) снова ищем через mdns
  sleep 2
  PORT="$(find_port_mdns)"
  [ -z "$PORT" ] && PORT="$(find_port_settings)"
  if [ -z "$PORT" ]; then
    echo
    read -r -p "  Порт ПОДКЛЮЧЕНИЯ (верхняя строка экрана беспроводной отладки, IP:ПОРТ): " PORT
  fi
  try_connect "$PORT" || { warn "Не подключился к 127.0.0.1:$PORT"; exit 1; }
fi

# --- зафиксировать серийник --------------------------------------------------
S="$(connected_serial)"
[ -z "$S" ] && { warn "Подключение не поднялось"; exit 1; }
persist_serial "$S"
ok "Готово: $S"

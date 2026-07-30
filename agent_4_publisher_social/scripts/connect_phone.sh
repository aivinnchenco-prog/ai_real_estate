#!/usr/bin/env bash
# Mac/сервер: подключение к Samsung по «Отладка по Wi-Fi» (см. termux/bin/connect_self.sh).
# Ищет устройство через mDNS, иначе — adb connect из .env, иначе интерактивный pair.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
STATE="${HOME}/.publisher/adb_serial"
mkdir -p "$(dirname "$STATE")"

say()  { printf '\033[1;36m[connect]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }

adb_bin() {
  export PATH="${HOME}/bin:${PATH}"
  command -v adb
}

adb start-server >/dev/null 2>&1 || true

connected_serial() {
  "$(adb_bin)" devices | awk 'NR>1 && $2=="device" {print $1; exit}'
}

env_serial() {
  [ -f "$ENV_FILE" ] || return 0
  grep -E '^ANDROID_SERIAL=' "$ENV_FILE" | head -n1 | cut -d= -f2- | tr -d ' "'
}

persist_serial() {
  local serial="$1"
  echo "$serial" >"$STATE"
  if [ -f "$ENV_FILE" ]; then
    if grep -q '^ANDROID_SERIAL=' "$ENV_FILE"; then
      if [[ "$(uname)" == Darwin ]]; then
        sed -i '' -E "s#^ANDROID_SERIAL=.*#ANDROID_SERIAL=$serial#" "$ENV_FILE"
      else
        sed -i -E "s#^ANDROID_SERIAL=.*#ANDROID_SERIAL=$serial#" "$ENV_FILE"
      fi
    else
      echo "ANDROID_SERIAL=$serial" >>"$ENV_FILE"
    fi
    ok "ANDROID_SERIAL=$serial записан в .env"
  fi
}

find_serial_mdns() {
  "$(adb_bin)" mdns services 2>/dev/null \
    | awk '/_adb-tls-connect/ {print $NF}' \
    | head -n1
}

try_connect() {
  local target="$1"
  local out
  [ -n "$target" ] || return 1
  say "Пробую adb connect $target"
  if command -v perl >/dev/null 2>&1; then
    out="$(perl -e 'alarm shift; exec @ARGV' 8 "$(adb_bin)" connect "$target" 2>&1)" || true
  else
    out="$("$(adb_bin)" connect "$target" 2>&1)" || true
  fi
  printf '%s\n' "$out" | grep -qiE 'connected|already' || return 1
  sleep 1
  return 0
}

# --- уже подключены? ---------------------------------------------------------
S="$(connected_serial)"
if [ -n "$S" ]; then
  ok "Уже подключено: $S"
  persist_serial "$S"
  exit 0
fi

# --- mDNS (Mac и телефон в одной Wi-Fi) --------------------------------------
TARGET="$(find_serial_mdns)"
if [ -n "$TARGET" ] && try_connect "$TARGET"; then
  :
else
  # --- сохранённый адрес из .env ---------------------------------------------
  TARGET="$(env_serial)"
  if [ -n "$TARGET" ] && try_connect "$TARGET"; then
    :
  else
    if [ "${NONINTERACTIVE:-0}" = "1" ]; then
      warn "Не удалось подключиться автоматически (mDNS пуст, .env не отвечает)."
      exit 1
    fi
    warn "Нужно сопряжение с этим Mac (один раз) или новый IP:ПОРТ после ребута."
    echo
    echo "  1. Телефон: Параметры разработчика → «Отладка по Wi-Fi» (Samsung)"
    echo "  2. «Подключить устройство с помощью кода подключения»"
    echo "  3. Введи IP:ПОРТ сопряжения и 6-значный код ниже"
    echo
    read -r -p "  IP:ПОРТ сопряжения (как на экране): " PAIR_TARGET
    read -r -p "  6-значный код: " PAIR_CODE
    say "Паррю $PAIR_TARGET…"
    if ! printf '%s\n' "$PAIR_CODE" | "$(adb_bin)" pair "$PAIR_TARGET" 2>&1 | grep -qi 'Successfully'; then
      warn "Сопряжение не удалось. Повтори connect_phone.sh"
      exit 1
    fi
    ok "Сопряжено."
    sleep 2
    TARGET="$(find_serial_mdns)"
    if [ -z "$TARGET" ]; then
      read -r -p "  IP:ПОРТ подключения (верх экрана «Отладка по Wi-Fi»): " TARGET
    fi
    try_connect "$TARGET" || {
      warn "Не подключился к $TARGET"
      exit 1
    }
  fi
fi

S="$(connected_serial)"
[ -z "$S" ] && { warn "adb devices пуст после connect"; exit 1; }
persist_serial "$S"
ok "Готово: $S"

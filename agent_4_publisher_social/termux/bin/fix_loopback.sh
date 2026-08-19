#!/data/data/com.termux/files/usr/bin/bash
# Починка loopback ADB для Termux-раннера (один запуск).
set -uo pipefail
HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
STATE="$HOME_DIR/.publisher/adb_serial"
ENV_FILE="$HOME_DIR/publisher-social/.env"
OUT=/sdcard/Download/fix_loopback_result.txt

exec >"$OUT" 2>&1
echo "=== fix_loopback $(date '+%F %T %Z') ==="

adb start-server >/dev/null 2>&1 || true

# убрать фантомный emulator из Termux-adb
adb devices | awk '/^emulator-/{print $1}' | while read -r s; do
  [ -n "$s" ] && adb disconnect "$s" 2>/dev/null || true
done

find_port_mdns() {
  timeout 8 adb mdns services 2>/dev/null \
    | awk '/_adb-tls-connect/ {print $NF}' \
    | sed -E 's/.*:([0-9]+)$/\1/' \
    | head -n1
}

find_port_settings() {
  local p
  p="$(getprop service.adb.tls.port 2>/dev/null)"
  [ -n "$p" ] && { echo "$p"; return; }
  settings get global adb_wifi_port 2>/dev/null | grep -E '^[0-9]+$' || true
}

find_port_file() {
  local f="/sdcard/Download/adb_wifi_port.txt"
  [ -f "$f" ] || return 0
  tr -d '[:space:]' <"$f" | grep -E '^[0-9]+$' || true
}

PORT="$(find_port_file)"
[ -z "$PORT" ] && PORT="$(find_port_mdns)"
[ -z "$PORT" ] && PORT="$(find_port_settings)"

echo "port=$PORT"
if [ -z "$PORT" ]; then
  echo "FAIL: no wireless debug port"
  exit 1
fi

echo "connect 127.0.0.1:$PORT"
adb connect "127.0.0.1:$PORT" 2>&1 || true
sleep 1
echo "--- adb devices ---"
adb devices 2>&1

SERIAL="$(adb devices | awk '/127\.0\.0\.1:.*device$/ {print $1; exit}')"
if [ -z "$SERIAL" ]; then
  SERIAL="$(adb devices | awk 'NR>1 && $2=="device" && $1 !~ /^emulator-/ {print $1; exit}')"
fi

if [ -z "$SERIAL" ]; then
  for TRY_PORT in "$PORT" 5555; do
    [ -z "$TRY_PORT" ] && continue
    echo "retry connect 127.0.0.1:$TRY_PORT"
    adb connect "127.0.0.1:$TRY_PORT" 2>&1 || true
    sleep 1
    SERIAL="$(adb devices | awk '/127\.0\.0\.1:.*device$/ {print $1; exit}')"
    [ -n "$SERIAL" ] && break
  done
fi

if [ -z "$SERIAL" ]; then
  echo "FAIL: loopback not connected"
  exit 1
fi

mkdir -p "$(dirname "$STATE")"
echo "$SERIAL" >"$STATE"
if [ -f "$ENV_FILE" ]; then
  if grep -q '^ANDROID_SERIAL=' "$ENV_FILE"; then
    sed -i -E "s#^ANDROID_SERIAL=.*#ANDROID_SERIAL=$SERIAL#" "$ENV_FILE"
  else
    echo "ANDROID_SERIAL=$SERIAL" >>"$ENV_FILE"
  fi
fi

echo "OK serial=$SERIAL"
"$HOME_DIR/bin/connect_self.sh" 2>&1 || true
echo "--- final adb devices ---"
adb devices 2>&1

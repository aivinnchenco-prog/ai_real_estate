#!/data/data/com.termux/files/usr/bin/bash
# Починка loopback + отключить тихие часы + перезапуск раннера.
set -uo pipefail
OUT=/sdcard/Download/fix_now_result.txt
exec > >(tee "$OUT") 2>&1
echo "=== fix_now $(date '+%F %T %Z') ==="

HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
ENV="$HOME_DIR/.publisher/runner.env"
mkdir -p "$HOME_DIR/.publisher"
touch "$ENV"
if grep -q '^QUIET_START=' "$ENV"; then
  sed -i 's/^QUIET_START=.*/QUIET_START=0/' "$ENV"
  sed -i 's/^QUIET_END=.*/QUIET_END=0/' "$ENV"
else
  printf '\nQUIET_START=0\nQUIET_END=0\n' >>"$ENV"
fi
echo "тихие часы отключены (QUIET 0-0)"

echo "--- fix loopback (порт в /sdcard/Download/adb_wifi_port.txt) ---"
bash /sdcard/Download/fix_loopback.sh
grep -E '^(OK|FAIL|port=)' /sdcard/Download/fix_loopback_result.txt || true

echo "--- restart runner (force) ---"
RUN_LOOP="$HOME_DIR/bin/run_loop.sh"
PIDFILE="$HOME_DIR/.publisher/runner.pid"
if [ -f "$PIDFILE" ]; then
  p="$(cat "$PIDFILE" 2>/dev/null)"
  if [ -n "$p" ]; then
    kill -TERM -- "-$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null || true
    sleep 1
    kill -KILL -- "-$p" 2>/dev/null || kill -KILL "$p" 2>/dev/null || true
  fi
  rm -f "$PIDFILE"
fi
pkill -f run_loop.sh 2>/dev/null || true
sleep 1
nohup setsid "$RUN_LOOP" >/dev/null 2>&1 &
echo $! >"$PIDFILE"
echo "started pid=$(cat "$PIDFILE")"
sleep 8
tail -15 "$HOME_DIR/.publisher/runner.log" 2>/dev/null || true
echo "FIX_NOW_OK"

#!/data/data/com.termux/files/usr/bin/bash
OUT=/sdcard/Download/start_runner_result.txt
HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
exec >"$OUT" 2>&1
echo "=== start_runner $(date) ==="
bash /sdcard/Download/fix_loopback.sh || true

RUN_LOOP="$HOME_DIR/bin/run_loop.sh"
PIDFILE="$HOME_DIR/.publisher/runner.pid"
if [ -f "$RUN_LOOP" ]; then
  if [ -f "$PIDFILE" ]; then
    p="$(cat "$PIDFILE" 2>/dev/null)"
    if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
      echo "runner already running pid=$p"
    else
      echo "stale pidfile, starting fresh"
      nohup setsid "$RUN_LOOP" >/dev/null 2>&1 &
      echo $! >"$PIDFILE"
      echo "started pid=$(cat "$PIDFILE")"
    fi
  else
    nohup setsid "$RUN_LOOP" >/dev/null 2>&1 &
    echo $! >"$PIDFILE"
    echo "started pid=$(cat "$PIDFILE")"
  fi
else
  echo "WARN: $RUN_LOOP missing"
fi

sleep 3
tail -n 8 "$HOME_DIR/.publisher/runner.log" 2>&1 || true
echo "START_OK"

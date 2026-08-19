#!/data/data/com.termux/files/usr/bin/bash
OUT=/sdcard/Download/runner_status_now.txt
exec >"$OUT" 2>&1
echo "=== runner_status $(date '+%F %T %Z') ==="
HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
PIDFILE="$HOME_DIR/.publisher/runner.pid"
LOG="$HOME_DIR/.publisher/runner.log"
ENV="$HOME_DIR/.publisher/runner.env"
echo "--- runner.env ---"
[ -f "$ENV" ] && cat "$ENV" || echo "no runner.env"
echo "--- pid ---"
if [ -f "$PIDFILE" ]; then
  p=$(cat "$PIDFILE" 2>/dev/null)
  echo "pidfile=$p"
  if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
    echo "runner_alive=yes"
    ps -p "$p" -o args= 2>/dev/null || true
  else
    echo "runner_alive=no (stale or stopped)"
  fi
else
  echo "no pidfile"
fi
echo "--- run_loop pgrep ---"
pgrep -af run_loop 2>/dev/null || echo "no run_loop process"
echo "--- loopback ---"
adb devices 2>/dev/null | head -5
echo "--- log tail ---"
tail -n 20 "$LOG" 2>/dev/null || echo "no log"
echo "STATUS_OK"

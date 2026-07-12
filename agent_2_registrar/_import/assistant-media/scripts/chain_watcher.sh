#!/usr/bin/env bash
# Foreground or background daemon: polls Notion and runs Agent 3/4.
# Usage:
#   ./scripts/chain_watcher.sh start   # background
#   ./scripts/chain_watcher.sh stop
#   ./scripts/chain_watcher.sh status
#   ./scripts/chain_watcher.sh run     # foreground (systemd ExecStart)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PIDFILE="${CHAIN_WATCHER_PIDFILE:-$ROOT/data/chain_watcher.pid}"
LOGFILE="${CHAIN_WATCHER_LOG:-$ROOT/data/logs/chain_watcher.log}"
PYTHON="${PYTHON:-python3}"

mkdir -p "$(dirname "$PIDFILE")" "$(dirname "$LOGFILE")"

load_env() {
  for f in "$ROOT/.env.real-estate" "$ROOT/.env"; do
    [[ -f "$f" ]] || continue
    set -a
    # shellcheck disable=SC1090
    source "$f"
    set +a
    break
  done
}

is_running() {
  [[ -f "$PIDFILE" ]] || return 1
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

run_foreground() {
  load_env
  export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"
  exec "$PYTHON" "$ROOT/scripts/chain_runner.py" --watch
}

cmd_start() {
  if is_running; then
    echo "chain_watcher already running (pid $(cat "$PIDFILE"))"
    exit 0
  fi
  load_env
  export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"
  nohup "$PYTHON" "$ROOT/scripts/chain_runner.py" --watch >>"$LOGFILE" 2>&1 &
  echo $! >"$PIDFILE"
  echo "chain_watcher started pid $(cat "$PIDFILE"), log $LOGFILE"
}

cmd_stop() {
  if ! is_running; then
    rm -f "$PIDFILE"
    echo "chain_watcher not running"
    exit 0
  fi
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
  sleep 1
  if is_running; then
    kill -9 "$(cat "$PIDFILE")" 2>/dev/null || true
  fi
  rm -f "$PIDFILE"
  echo "chain_watcher stopped"
}

cmd_status() {
  if is_running; then
    echo "running pid $(cat "$PIDFILE")"
    exit 0
  fi
  echo "stopped"
  exit 1
}

case "${1:-run}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  run) run_foreground ;;
  restart) cmd_stop || true; cmd_start ;;
  *)
    echo "Usage: $0 {start|stop|status|restart|run}" >&2
    exit 2
    ;;
esac

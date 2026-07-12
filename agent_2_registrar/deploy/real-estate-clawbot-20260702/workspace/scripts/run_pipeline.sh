#!/usr/bin/env bash
# Full pipeline: Gate → Agent 2 → Agent 3 → [optional] Agent 6
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="$(cd "$ROOT/../.." && pwd)"
cd "$ROOT"

# 24/7: curator + chain watcher (если ещё не подняты)
if [[ -x "$ROOT/scripts/ensure_services.sh" ]]; then
  "$ROOT/scripts/ensure_services.sh"
fi

SESSION=""
SOURCE="Telegram"
SKIP_WAIT=false
PUBLISH=""
PUBLISH_PLATFORMS=""
DRY_RUN=false
FROM_STEP="gate"

usage() {
  cat <<EOF
Usage: $0 --session SESSION_ID [options]

Options:
  --session ID          Session folder under data/sessions/
  --source TEXT         Listing source (Telegram @channel or URL)
  --skip-wait           Skip 150s batch gate wait
  --from-step STEP      gate|agent2|agent3|agent6 (alias: agent4)
  --publish PLATFORMS   Comma-separated: instagram,tiktok,youtube (runs Agent 6)
  --dry-run             Pass --dry-run to agent2 / publish only

Examples:
  $0 --session test_legendary_20260702 --source "Airbnb https://..."
  $0 --session test_legendary_20260702 --skip-wait --from-step agent3
  $0 --session test_legendary_20260702 --publish instagram,tiktok
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --skip-wait) SKIP_WAIT=true; shift ;;
    --from-step) FROM_STEP="$2"; shift 2 ;;
    --publish) PUBLISH="1"; PUBLISH_PLATFORMS="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$SESSION" ]]; then
  usage
  exit 1
fi

if [[ "$FROM_STEP" == "agent4" ]]; then
  FROM_STEP="agent6"
fi

step_ge() {
  local target="$1"
  if [[ "$target" == "agent4" ]]; then
    target="agent6"
  fi
  local order=(gate agent2 agent3 agent6)
  local from_idx=0 target_idx=0
  for i in "${!order[@]}"; do
    [[ "${order[$i]}" == "$FROM_STEP" ]] && from_idx=$i
    [[ "${order[$i]}" == "$target" ]] && target_idx=$i
  done
  [[ $target_idx -ge $from_idx ]]
}

OBJECT_ID=""
NOTION_PAGE_ID=""

parse_agent2_out() {
  local out="$1"
  OBJECT_ID=$(echo "$out" | python3 -c "
import sys, json
for line in sys.stdin.read().splitlines():
    line = line.strip()
    if line.startswith('{'):
        try:
            d = json.loads(line)
            print(d.get('object_id',''))
            break
        except Exception:
            pass
")
  NOTION_PAGE_ID=$(echo "$out" | python3 -c "
import sys, json
for line in sys.stdin.read().splitlines():
    line = line.strip()
    if line.startswith('{'):
        try:
            d = json.loads(line)
            print(d.get('notion_page_id',''))
            break
        except Exception:
            pass
")
}

load_session_json() {
  local sj="$ROOT/data/sessions/$SESSION/session.json"
  if [[ -f "$sj" ]]; then
    OBJECT_ID=$(python3 -c "import json; print(json.load(open('$sj')).get('object_id',''))")
    NOTION_PAGE_ID=$(python3 -c "import json; print(json.load(open('$sj')).get('notion_page_id',''))")
  fi
}

if step_ge gate; then
  echo "==> [Gate] Waiting for photo batches (150s idle)..."
  if [[ "$SKIP_WAIT" != true ]]; then
    until python3 scripts/media_batch_gate.py ready --session "$SESSION"; do
      sleep 30
    done
  fi
fi

if step_ge agent2; then
  echo "==> [Agent 2] Structurize..."
  AG2_ARGS=(--session "$SESSION" --source "$SOURCE")
  [[ "$DRY_RUN" == true ]] && AG2_ARGS+=(--dry-run)
  OUT=$(python3 scripts/agent2_structurize.py "${AG2_ARGS[@]}")
  echo "$OUT"
  if ! echo "$OUT" | grep -q "READY_FOR_VIDEO"; then
    if [[ "$DRY_RUN" != true ]]; then
      echo "ERROR: Agent 2 did not complete"
      exit 1
    fi
  fi
  parse_agent2_out "$OUT"
fi

load_session_json

if [[ -z "$OBJECT_ID" ]]; then
  echo "ERROR: could not resolve object_id (run agent2 first)"
  exit 1
fi

if step_ge agent3 && [[ "$DRY_RUN" != true ]]; then
  echo "==> [Chain] Agent 3 (Notion-gated) for $OBJECT_ID..."
  python3 scripts/chain_runner.py --from-agent 3 --object-id "$OBJECT_ID"
fi

load_session_json

if [[ -n "$PUBLISH" ]] && step_ge agent6 && [[ "$DRY_RUN" != true ]]; then
  if [[ -z "$OBJECT_ID" ]]; then
    echo "ERROR: object_id missing"
    exit 1
  fi
  echo "==> [Chain] Agent 6 (Notion-gated) for $OBJECT_ID..."
  python3 scripts/chain_runner.py --from-agent 6 --object-id "$OBJECT_ID" --publish "$PUBLISH_PLATFORMS"
fi

echo ""
echo "==> Pipeline done"
echo "    object_id:      $OBJECT_ID"
echo "    notion_page_id: ${NOTION_PAGE_ID:-n/a}"
if [[ -z "$PUBLISH" && "$DRY_RUN" != true ]]; then
  echo "    Next: $0 --session $SESSION --from-step agent6 --publish instagram"
fi

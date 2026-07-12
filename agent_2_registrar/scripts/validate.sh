#!/usr/bin/env bash
# Validate project structure before deploy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ERRORS=0

check() {
  if [[ -e "$1" ]]; then
    echo "  ✓ $1"
  else
    echo "  ✗ MISSING: $1"
    ERRORS=$((ERRORS + 1))
  fi
}

echo "Validating Real Estate Agent project..."
echo ""

echo "Workspaces:"
for ws in coordinator parser crm video publisher; do
  check "$PROJECT_ROOT/workspaces/$ws/AGENTS.md"
  check "$PROJECT_ROOT/workspaces/$ws/TOOLS.md"
done

echo ""
echo "Skills:"
check "$PROJECT_ROOT/workspaces/parser/skills/parse-listing/SKILL.md"
check "$PROJECT_ROOT/workspaces/crm/skills/noushen-crm/SKILL.md"
check "$PROJECT_ROOT/workspaces/video/skills/video-from-images/SKILL.md"
check "$PROJECT_ROOT/workspaces/publisher/skills/notion-crm/SKILL.md"
check "$PROJECT_ROOT/workspaces/publisher/skills/publish-publora/SKILL.md"
check "$PROJECT_ROOT/config/publisher.json"
check "$PROJECT_ROOT/workspaces/publisher/scripts/publish_pipeline.py"

echo ""
echo "Data dirs:"
for d in listings crm media publish-queue; do
  check "$PROJECT_ROOT/data/$d"
done

echo ""
echo "Deploy:"
check "$PROJECT_ROOT/deploy/openclaw.json.example"
check "$PROJECT_ROOT/scripts/setup.sh"

echo ""
if [[ $ERRORS -eq 0 ]]; then
  echo "All checks passed."
  exit 0
else
  echo "$ERRORS error(s) found."
  exit 1
fi

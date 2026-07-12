#!/usr/bin/env bash
# Wrapper — всё встроено в проект. Предпочитайте: ./start.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/start.sh"

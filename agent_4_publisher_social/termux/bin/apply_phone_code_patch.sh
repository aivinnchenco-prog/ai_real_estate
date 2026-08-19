#!/data/data/com.termux/files/usr/bin/bash
# Обновить publisher-social на телефоне (publication_status + pipeline).
set -euo pipefail
HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
PROJECT="$HOME_DIR/publisher-social"
PATCH="/sdcard/Download/phone_patch"
OUT="/sdcard/Download/phone_patch_result.txt"
exec >"$OUT" 2>&1
echo "=== phone_patch $(date '+%F %T %Z') ==="

if [ ! -d "$PATCH/src/publisher_social" ]; then
  echo "FAIL: patch dir missing: $PATCH"
  exit 1
fi

copy_one() {
  local rel="$1"
  local src="$PATCH/src/publisher_social/$rel"
  local dst="$PROJECT/src/publisher_social/$rel"
  if [ ! -f "$src" ]; then
    echo "skip missing $rel"
    return 0
  fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  echo "copied $rel"
}

for f in \
  channels/base.py \
  channels/_post_url.py \
  channels/_fb_publish.py \
  channels/fb_groups.py \
  config.py \
  process_lock.py \
  pipeline.py \
  state.py \
  __main__.py
do
  copy_one "$f"
done

echo "--- import check ---"
cd "$PROJECT"
export PYTHONPATH=src
python - <<'PY'
from publisher_social.channels.base import ChannelResult
r = ChannelResult(channel="fb_groups", ok=True)
assert hasattr(r, "publication_status")
print("ChannelResult.publication_status OK")
PY

echo "--- loopback ---"
bash /sdcard/Download/fix_loopback.sh || true

echo "--- dry-run queue (fb_groups) ---"
python -m publisher_social queue --dry-run --channel fb_groups 2>&1 | tail -n 25

echo "PATCH_OK"

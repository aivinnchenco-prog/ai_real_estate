#!/data/data/com.termux/files/usr/bin/bash
# Копирует phone_patch с SD-карты в ~/publisher-social (запуск из Termux).
set -euo pipefail
OUT=/sdcard/Download/deploy_patch_sdcard_result.txt
exec >"$OUT" 2>&1
echo "=== deploy_patch_sdcard $(date '+%F %T %Z') ==="
HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
PATCH="/sdcard/Download/phone_patch/src/publisher_social"
PROJECT="$HOME_DIR/publisher-social/src/publisher_social"
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
  src="$PATCH/$f"
  dst="$PROJECT/$f"
  if [ ! -f "$src" ]; then
    echo "skip missing $src"
    continue
  fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  echo "copied $f"
done
export PYTHONPATH="$HOME_DIR/publisher-social/src"
python - <<'PY'
from publisher_social.channels.base import ChannelResult
r = ChannelResult(channel="fb_groups", ok=True)
assert hasattr(r, "publication_status")
print("ChannelResult.publication_status OK")
PY
echo "DEPLOY_OK"

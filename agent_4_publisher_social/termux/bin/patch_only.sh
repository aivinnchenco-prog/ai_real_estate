#!/data/data/com.termux/files/usr/bin/bash
# Только патч с SD → ~/publisher-social (без loopback, inflight, раннера).
set -uo pipefail
OUT=/sdcard/Download/patch_only_result.txt
exec > >(tee "$OUT") 2>&1
echo "=== patch_only $(date '+%F %T %Z') ==="

HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
PROJECT="$HOME_DIR/publisher-social"
PATCH="/sdcard/Download/phone_patch/src/publisher_social"
export PYTHONPATH="$PROJECT/src"

deploy_ok=0
for f in \
  android/vision_fallback.py \
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
  dst="$PROJECT/src/publisher_social/$f"
  if [ ! -f "$src" ]; then
    echo "skip missing $src"
    continue
  fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  echo "copied $f"
  deploy_ok=1
done
CFG_SRC="/sdcard/Download/phone_patch/config/android.json"
CFG_DST="$PROJECT/config/android.json"
if [ -f "$CFG_SRC" ]; then
  mkdir -p "$(dirname "$CFG_DST")"
  cp "$CFG_SRC" "$CFG_DST"
  echo "copied config/android.json"
fi

GEMINI_LINE_FILE="/sdcard/Download/gemini_env_line.txt"
if [ -f "$GEMINI_LINE_FILE" ]; then
  touch "$PROJECT/.env"
  key_name="$(cut -d= -f1 "$GEMINI_LINE_FILE")"
  if ! grep -q "^${key_name}=" "$PROJECT/.env" 2>/dev/null; then
    cat "$GEMINI_LINE_FILE" >>"$PROJECT/.env"
    echo "added ${key_name} to .env"
  fi
fi

if [ "$deploy_ok" -eq 0 ]; then
  echo "FAIL: phone_patch пуст"
  exit 1
fi

cd "$PROJECT"
python - <<'PY'
from publisher_social.channels.base import ChannelResult
from publisher_social.android.vision_fallback import VisionFallback
from publisher_social.channels.fb_groups import _verify_object_album
from publisher_social.state import pending_fb_group_urls, mark_fb_group_published, load_state

r = ChannelResult(channel="fb_groups", ok=True)
assert hasattr(r, "publication_status")
assert hasattr(r, "published_group_urls")
assert callable(_verify_object_album)
assert VisionFallback({"ui_automation": {"vision_fallback": {"enabled": False}}}).available is False
st = load_state()
mark_fb_group_published(st, "_patch_probe", "https://www.facebook.com/groups/1/")
assert pending_fb_group_urls(st, "_patch_probe", ["https://www.facebook.com/groups/1/", "https://www.facebook.com/groups/2/"]) == ["https://www.facebook.com/groups/2/"]
print("PATCH_OK fb_groups_resume")
PY

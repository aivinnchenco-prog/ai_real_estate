#!/data/data/com.termux/files/usr/bin/bash
# Один скрипт: патч с SD → проверка → clear inflight → loopback → старт раннера.
set -uo pipefail
OUT=/sdcard/Download/go_result.txt
# Пишем и в терминал, и в файл — иначе кажется, что «ничего не происходит».
exec > >(tee "$OUT") 2>&1
echo "=== go.sh $(date '+%F %T %Z') === (лог: $OUT)"
echo "Подождите ~20 сек — идёт патч, loopback и перезапуск раннера..."

HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
PROJECT="$HOME_DIR/publisher-social"
PATCH="/sdcard/Download/phone_patch/src/publisher_social"
export PYTHONPATH="$PROJECT/src"

mkdir -p "$HOME_DIR/.termux"
PROP="$HOME_DIR/.termux/termux.properties"
if [ ! -f "$PROP" ] || ! grep -q '^allow-external-apps=true' "$PROP" 2>/dev/null; then
  echo "allow-external-apps=true" >>"$PROP"
  echo "wrote allow-external-apps"
fi

echo "--- deploy phone_patch ---"
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
if [ -f "$CFG_SRC" ]; then
  mkdir -p "$PROJECT/config"
  cp "$CFG_SRC" "$PROJECT/config/android.json"
  echo "copied config/android.json"
fi

if [ "$deploy_ok" -eq 0 ]; then
  echo "FAIL: phone_patch пуст — сначала adb push в /sdcard/Download/phone_patch/"
  exit 1
fi

echo "--- import check ---"
cd "$PROJECT"
if ! python - <<'PY'
from publisher_social.channels.base import ChannelResult
from publisher_social.android.vision_fallback import VisionFallback
from publisher_social.channels.fb_groups import _verify_object_album
from publisher_social.state import pending_fb_group_urls, mark_fb_group_published, load_state

r = ChannelResult(channel="fb_groups", ok=True)
assert hasattr(r, "publication_status"), "нет publication_status в ChannelResult"
assert hasattr(r, "published_group_urls"), "нет published_group_urls"
assert callable(_verify_object_album), "нет фикса альбома в fb_groups"
assert VisionFallback({"ui_automation": {"vision_fallback": {"enabled": False}}}).available is False
st = load_state()
mark_fb_group_published(st, "_patch_probe", "https://www.facebook.com/groups/1/")
assert pending_fb_group_urls(st, "_patch_probe", ["https://www.facebook.com/groups/1/", "https://www.facebook.com/groups/2/"]) == ["https://www.facebook.com/groups/2/"]
print("patch OK: fb_groups resume + publication_status")
PY
then
  echo "FAIL: патч не применился или битый"
  exit 1
fi

echo "--- clear inflight ---"
python - <<'PY'
from publisher_social.state import load_state, clear_channel_inflight

oid = "A_20260817_001"
state = load_state()
for ch in ("fb_groups", "fb_marketplace"):
    clear_channel_inflight(state, oid, ch)
    print("cleared inflight", oid, ch)
PY

echo "--- fix loopback ---"
bash /sdcard/Download/fix_loopback.sh || true

echo "--- restart runner ---"
RUN_LOOP="$HOME_DIR/bin/run_loop.sh"
PIDFILE="$HOME_DIR/.publisher/runner.pid"
if [ ! -f "$RUN_LOOP" ]; then
  echo "FAIL: missing $RUN_LOOP"
  exit 1
fi

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
sleep 6
tail -n 20 "$HOME_DIR/.publisher/runner.log" 2>/dev/null || true
python -m publisher_social queue --dry-run --channel fb_groups 2>&1 | tail -n 8
echo "GO_OK"

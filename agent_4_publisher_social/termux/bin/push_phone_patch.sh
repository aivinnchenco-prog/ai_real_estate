#!/usr/bin/env bash
# С Mac: залить phone_patch + patch_only.sh на телефон (раннер не трогает).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SERIAL="${ADB_SERIAL:-}"
ADB=(adb)
[ -n "$SERIAL" ] && ADB=(adb -s "$SERIAL")

PATCH_DST=/sdcard/Download/phone_patch/src/publisher_social
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
  "${ADB[@]}" shell "mkdir -p $PATCH_DST/$(dirname "$f")"
  "${ADB[@]}" push "$ROOT/src/publisher_social/$f" "$PATCH_DST/$f"
done
"${ADB[@]}" shell "mkdir -p /sdcard/Download/phone_patch/config"
"${ADB[@]}" push "$ROOT/config/android.json" /sdcard/Download/phone_patch/config/android.json
"${ADB[@]}" push "$(dirname "$0")/patch_only.sh" /sdcard/Download/patch_only.sh
"${ADB[@]}" push "$(dirname "$0")/go.sh" /sdcard/Download/go.sh
ENV_FILE="$ROOT/../.env"
if [ -f "$ENV_FILE" ]; then
  grep -E '^(GEMINI_API_KEY|PUBLISHER_GEMINI_API_KEY)=' "$ENV_FILE" | head -1 > /tmp/gemini_env_line.txt || true
  if [ -s /tmp/gemini_env_line.txt ]; then
    "${ADB[@]}" push /tmp/gemini_env_line.txt /sdcard/Download/gemini_env_line.txt
    echo "PUSH_OK gemini_env_line"
  fi
fi
echo "PUSH_OK — на телефоне: bash /sdcard/Download/patch_only.sh"

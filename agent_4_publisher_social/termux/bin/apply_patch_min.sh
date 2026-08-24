#!/data/data/com.termux/files/usr/bin/bash
OUT=/sdcard/Download/patch_min_result.txt
exec >"$OUT" 2>&1
echo "=== patch_min $(date '+%F %T %Z') ==="
P="${HOME:-/data/data/com.termux/files/home}/publisher-social"
S=/sdcard/Download/phone_patch/src/publisher_social
mkdir -p "$P/src/publisher_social/android" "$P/config"
cp "$S/android/vision_fallback.py" "$P/src/publisher_social/android/" 2>/dev/null || echo "no vision"
cp "$S/channels/fb_groups.py" "$P/src/publisher_social/channels/" 2>/dev/null || echo "no fb_groups"
cp "$S/pipeline.py" "$P/src/publisher_social/" 2>/dev/null || echo "no pipeline"
cp "$S/state.py" "$P/src/publisher_social/" 2>/dev/null || echo "no state"
cp "$S/config.py" "$P/src/publisher_social/" 2>/dev/null || echo "no config"
cp /sdcard/Download/phone_patch/config/android.json "$P/config/android.json" 2>/dev/null || true
if [ -f /sdcard/Download/gemini_env_line.txt ]; then
  touch "$P/.env"
  k=$(cut -d= -f1 /sdcard/Download/gemini_env_line.txt)
  grep -q "^${k}=" "$P/.env" 2>/dev/null || cat /sdcard/Download/gemini_env_line.txt >>"$P/.env"
fi
cd "$P" || exit 1
export PYTHONPATH=src
python -c "from publisher_social.state import pending_fb_group_urls; from publisher_social.android.vision_fallback import VisionFallback; print('patch_min_ok')"
echo PATCH_MIN_OK

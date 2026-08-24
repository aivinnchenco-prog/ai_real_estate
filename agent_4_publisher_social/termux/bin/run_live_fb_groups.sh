#!/data/data/com.termux/files/usr/bin/bash
OUT=/sdcard/Download/live_fb_groups_result.txt
HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
exec >"$OUT" 2>&1
echo "=== live fb_groups $(date) ==="
bash /sdcard/Download/fix_loopback.sh || true
cd "$HOME_DIR/publisher-social"
export PYTHONPATH=src
export PYTHONUNBUFFERED=1
echo "starting queue --live --channel fb_groups ..."
timeout --foreground --kill-after=30 1800 \
  python -m publisher_social queue --live --channel fb_groups 2>&1
RC=$?
echo "exit=$RC"
echo "LIVE_DONE"

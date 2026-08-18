#!/data/data/com.termux/files/usr/bin/bash
OUT=/sdcard/Download/clear_inflight_result.txt
HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
exec >"$OUT" 2>&1
cd "$HOME_DIR/publisher-social"
export PYTHONPATH=src
python - <<'PY'
from publisher_social.state import load_state, clear_channel_inflight

oid = "A_20260817_001"
state = load_state()
for ch in ("fb_groups", "fb_marketplace"):
    clear_channel_inflight(state, oid, ch)
    print("cleared inflight", oid, ch)
PY
echo "--- dry-run ---"
python -m publisher_social queue --dry-run --channel fb_groups 2>&1 | tail -n 12
echo "CLEAR_OK"

#!/data/data/com.termux/files/usr/bin/bash
# fix loopback → clear inflight → start runner (remote trigger with sdcard log).
OUT=/sdcard/Download/remote_resume_runner_result.txt
exec >"$OUT" 2>&1
echo "=== remote_resume_runner $(date '+%F %T %Z') ==="
bash /sdcard/Download/fix_loopback.sh || true
bash /sdcard/Download/clear_inflight.sh || true
bash /sdcard/Download/start_runner.sh || true
echo "RESUME_OK"

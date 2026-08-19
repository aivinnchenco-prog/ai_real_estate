#!/usr/bin/env bash
# First SAFE SERVER live batch runner with before/after resource snapshot.
set -euo pipefail

APP_ROOT="/opt/openhome/app"
PYTHON="/opt/openhome/venv/bin/python3"
LOG_DIR="/opt/openhome/runtime/availability/logs"
TS=$(date +%Y%m%dT%H%M%SZ)
LOG="${LOG_DIR}/batch-safe-${TS}.log"
OBJECT_IDS="${1:-A_20260807_001,A_20260802_002,A_20260806_001,A_20260725_001,A_20260719_006}"

mkdir -p "${LOG_DIR}"
cd "${APP_ROOT}"

echo "BATCH_LOG=${LOG}"
echo "OBJECT_IDS=${OBJECT_IDS}"

"${PYTHON}" - <<'PY' | tee -a "${LOG}"
import re, subprocess
from collections import defaultdict

def run(cmd):
    return subprocess.check_output(cmd, shell=True, text=True)

rows = []
raw = run("ps -eo pid,user,ppid,rss,stat,args --no-headers")
for line in raw.splitlines():
    if 'chrome' in line.lower() or 'chromium' in line.lower():
        parts = line.strip().split(None, 5)
        if len(parts) < 6:
            continue
        pid, user, ppid, rss, stat, args = parts
        rows.append({'pid': int(pid), 'user': user, 'ppid': int(ppid), 'rss_kb': int(rss), 'stat': stat, 'args': args})

udd = defaultdict(list)
for r in rows:
    m = re.search(r'--user-data-dir(?:=| )(.*?)(?:\s|$)', r['args'])
    path = m.group(1).strip('"') if m else '(no-user-data-dir)'
    udd[path].append(r)

mem = run("grep -E 'MemTotal|MemAvailable' /proc/meminfo")
load = run("uptime")
print("== BEFORE BATCH ==")
print(mem)
print(load.strip())
print(f"OS_CHROME_PROCESSES={len(rows)}")
print(f"BROWSER_INSTANCES={len(udd)}")
print(f"CHROME_RSS_KB={sum(r['rss_kb'] for r in rows)}")
openhome = [r for r in rows if r['user'] == 'openhome']
print(f"OPENHOME_CHROME_PROCESSES={len(openhome)}")
print(f"OPENHOME_CHROME_RSS_KB={sum(r['rss_kb'] for r in openhome)}")
PY

echo "== STARTING BATCH ==" | tee -a "${LOG}"
START=$(date +%s)
"${PYTHON}" -m availability_service.main batch-safe \
  --object-id "${OBJECT_IDS}" \
  --confirm-live \
  --confirm-write 2>&1 | tee -a "${LOG}"
END=$(date +%s)
echo "BATCH_ELAPSED_S=$((END-START))" | tee -a "${LOG}"

"${PYTHON}" - <<'PY' | tee -a "${LOG}"
import re, subprocess, sqlite3
from collections import defaultdict
from pathlib import Path

def run(cmd):
    return subprocess.check_output(cmd, shell=True, text=True)

rows = []
raw = run("ps -eo pid,user,ppid,rss,stat,args --no-headers")
for line in raw.splitlines():
    if 'chrome' in line.lower() or 'chromium' in line.lower():
        parts = line.strip().split(None, 5)
        if len(parts) < 6:
            continue
        pid, user, ppid, rss, stat, args = parts
        rows.append({'pid': int(pid), 'user': user, 'ppid': int(ppid), 'rss_kb': int(rss), 'stat': stat, 'args': args})

udd = defaultdict(list)
for r in rows:
    m = re.search(r'--user-data-dir(?:=| )(.*?)(?:\s|$)', r['args'])
    path = m.group(1).strip('"') if m else '(no-user-data-dir)'
    udd[path].append(r)

mem = run("grep -E 'MemTotal|MemAvailable' /proc/meminfo")
load = run("uptime")
print("== AFTER BATCH ==")
print(mem)
print(load.strip())
print(f"OS_CHROME_PROCESSES={len(rows)}")
print(f"BROWSER_INSTANCES={len(udd)}")
print(f"CHROME_RSS_KB={sum(r['rss_kb'] for r in rows)}")
openhome = [r for r in rows if r['user'] == 'openhome']
print(f"OPENHOME_CHROME_PROCESSES={len(openhome)}")
print(f"OPENHOME_CHROME_RSS_KB={sum(r['rss_kb'] for r in openhome)}")
for path, procs in sorted(udd.items()):
    if any(p['user'] == 'openhome' for p in procs):
        print(f"OPENHOME_INSTANCE user_data_dir={path} processes={len(procs)} rss_kb={sum(p['rss_kb'] for p in procs)}")

db = Path("/opt/openhome/runtime/availability/availability.sqlite3")
conn = sqlite3.connect(db)
print(f"INTEGRITY={conn.execute('PRAGMA integrity_check').fetchone()[0]}")
jobs = conn.execute("SELECT COUNT(*) FROM refresh_jobs WHERE status IN ('pending','active')").fetchone()[0]
print(f"ACTIVE_REFRESH_JOBS={jobs}")

ids = ["A_20260807_001","A_20260802_002","A_20260806_001","A_20260725_001","A_20260719_006"]
for oid in ids:
    cal = conn.execute("SELECT COUNT(*) FROM availability_calendar_days WHERE object_id=?", (oid,)).fetchone()[0]
    dup_cal = conn.execute(
        "SELECT COUNT(*) - COUNT(DISTINCT date) FROM availability_calendar_days WHERE object_id=?", (oid,)
    ).fetchone()[0]
    mon = conn.execute("SELECT COUNT(*) FROM availability_months WHERE object_id=?", (oid,)).fetchone()[0]
    dup_mon = conn.execute(
        "SELECT COUNT(*) - COUNT(DISTINCT month_key) FROM availability_months WHERE object_id=?", (oid,)
    ).fetchone()[0]
    print(f"SQLITE {oid} calendar_rows={cal} dup_cal={dup_cal} monthly_rows={mon} dup_mon={dup_mon}")
conn.close()

from availability_service.app.config import canonical_env_source, load_config
cfg = load_config()
print(f"ENV_SOURCE={canonical_env_source()}")
print(f"enabled={cfg.enabled} airbnb_enabled={cfg.airbnb_enabled} dry_run={cfg.dry_run}")
PY

echo "DONE log=${LOG}"

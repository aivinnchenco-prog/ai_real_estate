#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/opt/openhome/app"
PYTHON="/opt/openhome/venv/bin/python3"
LOG_DIR="/opt/openhome/runtime/availability/logs"
OBJECT_IDS="A_20260807_001,A_20260802_002,A_20260806_001,A_20260725_001,A_20260719_006"
TS=$(date +%Y%m%dT%H%M%SZ)
LOG="${LOG_DIR}/batch-safe-${TS}.log"

mkdir -p "${LOG_DIR}"
cd "${APP_ROOT}"

snap_resources() {
  "${PYTHON}" - <<'PY'
import re, subprocess
from collections import defaultdict

def run(cmd):
    return subprocess.check_output(cmd, shell=True, text=True)

rows = []
for line in run("ps -eo pid,user,rss,args --no-headers").splitlines():
    if "chrome" in line.lower() or "chromium" in line.lower():
        p = line.split(None, 3)
        if len(p) < 4:
            continue
        rows.append({"user": p[1], "rss": int(p[2]), "args": p[3]})

udd = defaultdict(list)
for r in rows:
    m = re.search(r"--user-data-dir(?:=| )(.*?)(?:\s|$)", r["args"])
    path = m.group(1).strip('"') if m else "(none)"
    udd[path].append(r)

openhome = [r for r in rows if r["user"] == "openhome"]
mem = run("grep -E 'MemTotal|MemAvailable' /proc/meminfo")
load = run("uptime").strip()
print(mem)
print(load)
print(f"OS_CHROME_PROCESSES={len(rows)}")
print(f"BROWSER_INSTANCES={len(udd)}")
print(f"CHROME_RSS_KB={sum(r['rss'] for r in rows)}")
print(f"OPENHOME_CHROME_PROCESSES={len(openhome)}")
print(f"OPENHOME_CHROME_RSS_KB={sum(r['rss'] for r in openhome)}")
for path, procs in sorted(udd.items()):
    if any(p["user"] == "openhome" for p in procs):
        print(f"OPENHOME_INSTANCE dir={path} procs={len(procs)} rss_kb={sum(p['rss'] for p in procs)}")
PY
}

echo "BATCH_LOG=${LOG}" | tee "${LOG}"
echo "OBJECT_IDS=${OBJECT_IDS}" | tee -a "${LOG}"
echo "== BEFORE ==" | tee -a "${LOG}"
snap_resources | tee -a "${LOG}"

START=$(date +%s)
echo "== BATCH START $(date -Is) ==" | tee -a "${LOG}"
"${PYTHON}" -m availability_service.main batch-safe \
  --object-id "${OBJECT_IDS}" \
  --confirm-live \
  --confirm-write 2>&1 | tee -a "${LOG}"
END=$(date +%s)
echo "BATCH_ELAPSED_S=$((END-START))" | tee -a "${LOG}"

echo "== AFTER ==" | tee -a "${LOG}"
snap_resources | tee -a "${LOG}"

"${PYTHON}" - <<'PY' | tee -a "${LOG}"
import json
import sqlite3
from pathlib import Path

from availability_service.app.config import load_config
from availability_service.app.notion_reader import NotionReader, map_target_fields
from availability_service.app.notion_writer import NotionWriter

IDS = [
    "A_20260807_001",
    "A_20260802_002",
    "A_20260806_001",
    "A_20260725_001",
    "A_20260719_006",
]
MONTHS = ["Sep 26", "Oct 26", "Nov 26", "Dec 26"]

cfg = load_config()
print(f"permanent enabled={cfg.enabled} dry_run={cfg.dry_run} airbnb={cfg.airbnb_enabled}")

db = Path("/opt/openhome/runtime/availability/availability.sqlite3")
conn = sqlite3.connect(db)
print(f"INTEGRITY={conn.execute('PRAGMA integrity_check').fetchone()[0]}")
jobs = conn.execute(
    "SELECT COUNT(*) FROM refresh_jobs WHERE status IN ('pending','active')"
).fetchone()[0]
print(f"ACTIVE_REFRESH_JOBS={jobs}")
for oid in IDS:
    cal = conn.execute(
        "SELECT COUNT(*) FROM availability_calendar_days WHERE object_id=?", (oid,)
    ).fetchone()[0]
    dup_cal = conn.execute(
        "SELECT COUNT(*) - COUNT(DISTINCT date) FROM availability_calendar_days WHERE object_id=?",
        (oid,),
    ).fetchone()[0]
    mon = conn.execute(
        "SELECT COUNT(*) FROM availability_months WHERE object_id=?", (oid,)
    ).fetchone()[0]
    dup_mon = conn.execute(
        "SELECT COUNT(*) - COUNT(DISTINCT month_key) FROM availability_months WHERE object_id=?",
        (oid,),
    ).fetchone()[0]
    print(f"SQLITE {oid} cal={cal} dup_cal={dup_cal} mon={mon} dup_mon={dup_mon}")
conn.close()

reader = NotionReader(cfg)
writer = NotionWriter(cfg)
tgt = reader.fetch_schema(cfg.target_database_id)
mapping = map_target_fields(tgt.properties)
print(f"TARGET_SCHEMA_ERROR={tgt.error or 'none'}")

for oid in IDS:
    count = writer.count_target_rows_by_object_id(
        cfg.target_database_id, mapping, oid, tgt.properties
    )
    pages = writer.find_target_pages(
        cfg.target_database_id, mapping, oid, tgt.properties
    )
    cells = {}
    if len(pages) == 1:
        props = pages[0].get("properties") or {}
        for m in MONTHS:
            if m not in mapping.month_columns:
                continue
            meta = props.get(m) or {}
            if meta.get("type") == "rich_text":
                cells[m] = "".join(
                    x.get("plain_text", "") for x in meta.get("rich_text") or []
                )
    print(f"NOTION {oid} rows={count} readback={'PASS' if count==1 else 'FAIL'} cells={json.dumps(cells, ensure_ascii=False)}")
PY

echo "DONE ${LOG}"

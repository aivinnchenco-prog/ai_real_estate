#!/usr/bin/env bash
# Availability Service VPS preflight (NO live Airbnb, NO batch, NO writes).
set -euo pipefail

APP_ROOT="/opt/openhome/app"
PKG="${APP_ROOT}/availability_service"
RUNTIME="/opt/openhome/runtime/availability"
DB="${RUNTIME}/availability.sqlite3"
ENV_FILE="${APP_ROOT}/.env"
USER_NAME="openhome"
if [[ -x /opt/openhome/venv/bin/python3 ]]; then
  PYTHON="/opt/openhome/venv/bin/python3"
else
  PYTHON="python3"
fi

section() { echo ""; echo "== $1 =="; }

section "RUNTIME"
mkdir -p "${RUNTIME}"
chown "${USER_NAME}:${USER_NAME}" "${RUNTIME}"
chmod 750 "${RUNTIME}"
ls -ld "${RUNTIME}"

if [[ -f "${DB}" ]]; then
  echo "sqlite_exists=yes path=${DB}"
  chown "${USER_NAME}:${USER_NAME}" "${DB}"
  sudo -u "${USER_NAME}" "${PYTHON}" - <<PY
import sqlite3
conn = sqlite3.connect("${DB}")
row = conn.execute("PRAGMA integrity_check").fetchone()
print(f"  integrity_check={row[0]}")
conn.close()
PY
else
  echo "sqlite_exists=no (production DB not replaced)"
fi

section "SAFE CONFIG (from .env + load_config)"
cd "${APP_ROOT}"
sudo -u "${USER_NAME}" env PYTHONPATH="${APP_ROOT}" "${PYTHON}" - <<'PY'
import os
from availability_service.app.config import load_config

load_config()
keys = [
    "AVAILABILITY_ENABLED",
    "AVAILABILITY_DRY_RUN",
    "AVAILABILITY_AIRBNB_ENABLED",
    "AVAILABILITY_OBJECT_CONCURRENCY",
    "AVAILABILITY_CALENDAR_CONCURRENCY",
    "AVAILABILITY_PRICE_CONCURRENCY",
    "AVAILABILITY_BROWSER_MAX_INSTANCES",
    "AVAILABILITY_BATCH_SAFE_MAX_OBJECTS",
]
for k in keys:
  v = os.environ.get(k, "(unset)")
  print(f"  {k}={v}")

cfg = load_config()
print(f"  resolved_enabled={cfg.enabled}")
print(f"  resolved_dry_run={cfg.dry_run}")
print(f"  resolved_airbnb_enabled={cfg.airbnb_enabled}")
print(f"  resolved_object_concurrency={cfg.object_concurrency}")
print(f"  resolved_calendar_concurrency={cfg.calendar_concurrency}")
print(f"  resolved_price_concurrency={cfg.price_concurrency}")
print(f"  resolved_browser_max={cfg.browser_max_instances}")
print(f"  resolved_batch_max={cfg.batch_safe_max_objects}")
print(f"  sqlite_path={cfg.sqlite_path}")
PY

section "SERVER CONCURRENCY SYNTHETIC"
sudo -u "${USER_NAME}" env PYTHONPATH="${APP_ROOT}" "${PYTHON}" - <<'PY'
import threading
import time
from availability_service.app.server_concurrency import (
    BatchRunMetrics,
    calendar_fetch_slot,
    configure_server_concurrency,
    get_peak_browser_instances,
    reset_peak_browser_instances,
    ServerConcurrencyLimits,
)

configure_server_concurrency(ServerConcurrencyLimits())
reset_peak_browser_instances()
metrics = BatchRunMetrics()
order = []

def worker(oid: str):
    with calendar_fetch_slot(oid, metrics=metrics):
        order.append(f"{oid}-start")
        time.sleep(0.08)
        order.append(f"{oid}-end")

t1 = threading.Thread(target=worker, args=("A",))
t2 = threading.Thread(target=worker, args=("B",))
t1.start()
time.sleep(0.02)
t2.start()
t1.join()
t2.join()

overlap = order.index("A-end") >= order.index("B-start") and order.index("B-end") >= order.index("A-start")
serial = not overlap
print(f"  calendar_serial_no_overlap={serial}")
print(f"  order={order}")
print(f"  peak_browser_instances={max(get_peak_browser_instances(), metrics.peak_browser_instances)}")
PY

section "BROWSER ENV PRE-FLIGHT"
command -v google-chrome >/dev/null && echo "  google-chrome=ok" || echo "  google-chrome=missing"
command -v chromium >/dev/null && echo "  chromium=ok" || echo "  chromium=missing"
command -v chromium-browser >/dev/null && echo "  chromium-browser=ok" || echo "  chromium-browser=missing"
command -v xvfb-run >/dev/null && echo "  xvfb-run=ok" || echo "  xvfb-run=missing"
ls -ld /opt/openhome/runtime/browser_profiles 2>/dev/null || echo "  browser_profiles_dir=missing"
sudo -u "${USER_NAME}" env PYTHONPATH="${APP_ROOT}" "${PYTHON}" - <<'PY'
import importlib.util
import shutil
print(f"  playwright_import={'ok' if importlib.util.find_spec('playwright') else 'missing'}")
try:
    import selenium
    print("  selenium_import=ok")
except ImportError:
    print("  selenium_import=missing")
try:
    import seleniumbase
    print("  seleniumbase_import=ok")
except ImportError:
    print("  seleniumbase_import=missing")

for name in ("google-chrome", "chromium", "chromium-browser"):
    p = shutil.which(name)
    if p:
        print(f"  executable_{name}={p}")
PY

section "AGENT1 ADAPTER SAFE IMPORTS"
sudo -u "${USER_NAME}" env PYTHONPATH="${APP_ROOT}" "${PYTHON}" - <<'PY'
from availability_service.providers.airbnb_adapter import resolve_agent1_root

root = resolve_agent1_root()
print(f"  agent1_root={root}")
if root is None:
    print("  resolve_agent1_root=FAIL")
else:
    import importlib
    import sys
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    for mod in ("availability", "monthly_pricing"):
        try:
            importlib.import_module(mod)
            print(f"  import {mod}=ok")
        except Exception as e:
            print(f"  import {mod}=FAIL {e}")
    try:
        from airbnb_parser import AirbnbParser
        print("  import AirbnbParser=ok")
        print(f"  fetch_price_for_period={hasattr(AirbnbParser, 'fetch_price_for_period')}")
    except Exception as e:
        print(f"  import AirbnbParser=FAIL {e}")
PY

section "NOTION DRY RUN"
sudo -u "${USER_NAME}" env PYTHONPATH="${APP_ROOT}" "${PYTHON}" -m availability_service.main dry-run 2>&1 | tail -40

section "SAFETY GATES (batch-safe validation only)"
set +e
out1=$(sudo -u "${USER_NAME}" env PYTHONPATH="${APP_ROOT}" "${PYTHON}" -m availability_service.main batch-safe --object-id A_TEST 2>&1)
echo "$out1" | tail -3
out2=$(sudo -u "${USER_NAME}" env PYTHONPATH="${APP_ROOT}" "${PYTHON}" -m availability_service.main batch-safe --object-id A_TEST --confirm-live 2>&1)
echo "$out2" | tail -3
out3=$(sudo -u "${USER_NAME}" env PYTHONPATH="${APP_ROOT}" "${PYTHON}" -m availability_service.main batch-safe --object-id A1,A2,A3,A4,A5,A6 --confirm-live --confirm-write 2>&1)
echo "$out3" | tail -3
set -e

section "TESTS"
sudo -u "${USER_NAME}" env PYTHONPATH="${APP_ROOT}" "${PYTHON}" -m pytest availability_service/tests -q

section "RESOURCE BASELINE"
free -h | awk '/Mem:/ {print "  RAM:", $2, "total", $3, "used", $4, "free"}'
uptime
pgrep -af availability_service || echo "  availability_process=none"
pgrep -c chrome || echo "  chrome_count=0"
pgrep -c chromium || echo "  chromium_count=0"

section "SYSTEMD"
systemctl is-active openhome-availability 2>/dev/null || echo "  openhome-availability=inactive/not-installed"

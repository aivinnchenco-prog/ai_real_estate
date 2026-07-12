---
summary: "Periodic checks — фоновые сервисы пайплайна"
read_when:
  - On heartbeat poll
---

# HEARTBEAT.md

## Фоновые сервисы (24/7)

Если curator или chain watcher не работают — поднять:

```bash
./start.sh
# или
./scripts/healthcheck_vps.sh
docker compose logs --tail=20 chain
```

Higgsfield CLI (Seedance) — один раз OAuth: `./scripts/higgsfield_auth_remote.sh` (см. deploy/VPS_24X7.md).

Если всё OK — **HEARTBEAT_OK**, ничего не делать.

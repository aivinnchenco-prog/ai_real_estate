# VPS 24/7 — встроено в проект

Сервисы — часть workspace. Один раз на сервере:

```bash
cd /data/.openclaw/workspace   # ваш путь
cp .env.real-estate.example .env.real-estate && nano .env.real-estate
./start.sh
```

Это поднимает **curator** + **chain** (`chain_runner.py --watch`, restart unless-stopped).

При каждом `./scripts/run_pipeline.sh` — `ensure_services.sh` сам проверит, что сервисы живы.

---

## Что работает автоматически

| Сервис | Как |
|--------|-----|
| curator | `docker compose`, порт 8077 |
| chain watcher | контейнер `chain`, Notion → Agent 3/4 |
| Agent 3 track A | FFmpeg reel (обязательно) |
| Agent 3 track B | Seedance через Higgsfield CLI в контейнере |

MCP на сервере **не нужен**.

---

## Higgsfield OAuth (один раз)

**Вариант A — внутри контейнера (SSH-туннель):**

```bash
# Mac
ssh -L 3843:127.0.0.1:3843 USER@VPS

# VPS
docker compose exec chain higgsfield auth login --port 3843
```

**Вариант B — скрипт:**

```bash
./scripts/higgsfield_auth_remote.sh
```

Токен сохраняется в Docker volume `higgsfield-cli`.

---

## Проверка

```bash
./scripts/healthcheck_vps.sh
docker compose logs -f chain
curl http://127.0.0.1:8077/health
```

---

## Без Docker (fallback)

```bash
./scripts/chain_watcher.sh start
docker compose up -d curator   # curator всё равно в Docker
```

---

## systemd (опционально, root VPS)

См. `deploy/systemd/` — если хотите autostart до логина, но обычно достаточно `./start.sh` + `restart: unless-stopped`.


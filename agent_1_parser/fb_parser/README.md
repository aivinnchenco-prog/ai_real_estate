# Agent 1B — FB Marketplace Parser

Parser for the Agent 1B contract:

- Input: Facebook Marketplace item URL (via Telegram bot or CLI)
- Output: `data/sessions/{SESSION_ID}/description.txt` + `data/sessions/{SESSION_ID}/photos/*`
- Agent 2 compatibility: keeps the session contract from `FB_MARKETPLACE_PARSER_BRIEF.md`

## Install

Requires Python 3.11 (3.14 fails to build lxml).

```bash
python3.11 -m venv .venv311
source .venv311/bin/activate
pip install -U pip
pip install -r requirements.txt
playwright install chromium
```

On a fresh Linux server also run: `playwright install-deps chromium` (installs system libraries for Chromium).

## Run with crawl4ai backend

```bash
python3 agent1b/fb_parser.py \
  --url "https://www.facebook.com/marketplace/item/31101764692792256/" \
  --session "TEST_FB_001" \
  --backend crawl4ai \
  --workspace "."
```

## Run with ScrapeGraphAI backend

```bash
python3 agent1b/fb_parser.py \
  --url "https://www.facebook.com/marketplace/item/31101764692792256/" \
  --session "TEST_FB_002" \
  --backend scrapegraphai \
  --workspace "." \
  --save-debug-json
```

## Notes

- `scrapegraphai` backend currently uses OpenAI config placeholder in code; set your provider key before running.
- If Facebook returns a login wall, an expired session, or the Marketplace feed instead of the listing: `AUTH_REQUIRED` (exit code 2). A saved `c_user` cookie is not enough once Facebook stops honoring it.
- If no photos could be saved, script exits with `NO_PHOTOS` (exit code 3).
- If the browser lands on some other non-item page: `WRONG_PAGE` (exit code 4).
- Accepted item URL hosts: `www.facebook.com`, `m.facebook.com`, `mbasic.facebook.com`, bare `facebook.com`.
- Accepted path forms (auto-normalized to www):
  - `https://www.facebook.com/marketplace/item/{ITEM_ID}/`
  - `https://www.facebook.com/share/{CODE}/` (short share link). With a live session the browser must navigate to `/marketplace/item/{id}/` (path only; an id buried in a login `?next=` query does not count). That canonical URL is what gets scraped. Item links inside the Marketplace feed are ignored.
  (mobile hosts are rewritten because `m.facebook.com` often shows an unsupported-browser page)
- Facebook HTML sometimes contains NULL bytes and other XML-illegal control characters. They are stripped before lxml parsing so crawl4ai does not abort with `All strings must be XML compatible`.

## Facebook login (required for real listing cards)

Add to `.env`:

```bash
FB_EMAIL=your_email_or_phone
FB_PASSWORD=your_password
FB_BROWSER_PROFILE=.fb_profile
FB_HEADLESS=false
FB_PROXY=http://user:pass@host:port   # optional
FB_DELAY_MIN_MS=800
FB_DELAY_MAX_MS=2200
FB_PAGE_DELAY_SEC=4
```

One-time session bootstrap:

```bash
source .venv311/bin/activate
python agent1b/login_fb.py
```

If Facebook asks for 2FA/checkpoint — complete it manually in the opened browser, then retry.

## Refresh Facebook login on the server

Share links (`facebook.com/share/...`) open the Marketplace **feed** when the browser session is expired, even if `.fb_profile` still has a `c_user` cookie. The bot then reports `AUTH_REQUIRED` (code 2) and asks for a refresh. Do **not** commit `.env`, `.fb_profile/`, or `fb_storage_state.json`.

Production app root is `/opt/openhome/app`. The parser bot unit is `openhome-agent1` (user `openhome`). A legacy unit `agent1b-bot` with `WorkingDirectory=/opt/agent1b` uses the same steps with those names swapped in.

### Redeploy parser code

On the VPS, after this change is on the branch you deploy:

```bash
cd /opt/openhome/app
git pull
sudo systemctl restart openhome-agent1
sudo systemctl status openhome-agent1 --no-pager
journalctl -u openhome-agent1 -n 80 --no-pager
```

From a checkout that deploys with rsync (`scripts/deploy.sh` does not delete `.venv311` or browser profiles):

```bash
./scripts/deploy.sh --only agent_1_parser --yes --restart agent1
```

`--restart agent1` restarts `openhome-agent1` only when that unit is enabled.

### Refresh cookies

1. Stop the bot so Chromium releases the profile:

```bash
sudo systemctl stop openhome-agent1
```

2. On a desktop where you can finish Facebook login and 2FA (headless VPS login cannot complete a checkpoint):

```bash
cd agent_1_parser/fb_parser
source .venv311/bin/activate
FB_HEADLESS=false python agent1b/login_fb.py
python agent1b/export_fb_state.py
```

3. Copy the export to the server. The file is a secret; it is gitignored:

```bash
scp fb_storage_state.json root@YOUR_VPS:/opt/openhome/app/agent_1_parser/fb_parser/fb_storage_state.json
ssh root@YOUR_VPS 'chown openhome:openhome /opt/openhome/app/agent_1_parser/fb_parser/fb_storage_state.json && chmod 600 /opt/openhome/app/agent_1_parser/fb_parser/fb_storage_state.json'
```

4. Import into the server profile, delete the secret file from the app tree, start the bot:

```bash
sudo -u openhome bash -lc 'cd /opt/openhome/app/agent_1_parser/fb_parser && .venv311/bin/python agent1b/import_fb_state.py'
rm -f /opt/openhome/app/agent_1_parser/fb_parser/fb_storage_state.json
sudo systemctl start openhome-agent1
journalctl -u openhome-agent1 -n 40 --no-pager
```

`FB_BROWSER_PROFILE` is the profile directory (default `.fb_profile` under `fb_parser`, or an absolute path such as `/opt/openhome/runtime/browser_profiles/...`). `import_fb_state.py` writes cookies into that directory. It does not print the cookie values.

Send a `facebook.com/share/...` link again. A live session resolves it to `https://www.facebook.com/marketplace/item/{id}/` and scrapes that card.

## Telegram bot entrypoint (Agent 1 tests)

1) Create env:

```bash
cp .env.example .env
```

2) Put your token into `.env`:

```bash
TELEGRAM_BOT_TOKEN=...
FB_PARSER_BACKEND=crawl4ai
FB_PARSER_WORKSPACE=.
```

3) Run bot:

```bash
./run_bot.sh
```

(or `source .venv311/bin/activate && python agent1b/tg_bot.py` — the bot can be started from any directory, paths are anchored to the project root).

Then send a Marketplace link to bot chat. The bot processes one link at a time (a queue protects the shared browser profile) and kills a parse that hangs longer than 7 minutes.

## Deploy to a server (Linux)

1) Copy the project to the server (e.g. `/opt/agent1b`), **without** `.venv311`, `data/sessions`, `__pycache__`:

```bash
rsync -av --exclude '.venv311' --exclude 'data/sessions' --exclude '__pycache__' \
  ./ user@server:/opt/agent1b/
```

2) On the server: install the Python 3.11 venv (do not copy `.venv311` from a Mac — rsync of a Darwin venv looks “present” and then crashes):

```bash
sudo bash scripts/ensure_venv.sh
```

Or manually: install Python 3.11, `python3.11 -m venv .venv311`, `pip install -r requirements.txt`, `playwright install chromium`.

`ensure_venv.sh` is idempotent: if the venv already imports `requests` + `playwright` + `crawl4ai` on 3.11, it exits 0 without reinstalling.


3) Facebook session. Two options:
   - **Recommended:** copy the working `.fb_profile/` folder from this machine to the server (it is included in the rsync above). The saved cookies keep working; no login on the server needed.
   - Or set `FB_EMAIL`/`FB_PASSWORD` in `.env` and run `python agent1b/login_fb.py` with `FB_HEADLESS=true` — may hit a 2FA checkpoint, which cannot be completed headless.

4) Check `.env` on the server: `FB_HEADLESS_PARSER=true` (no GUI on server), real `TELEGRAM_BOT_TOKEN`.

5) Autostart with systemd — see `deploy/agent1b-bot.service` (instructions inside the file). The bot restarts automatically on crash or reboot.

6) Smoke test: `journalctl -u agent1b-bot -f`, then send a Marketplace link to the bot.

Important: only ONE bot instance must be running per token (two instances cause Telegram `Conflict: terminated by other getUpdates request`). Stop the local bot before starting the server one.

## Maintenance notes

- `data/sessions/` grows with every parse (photos ~0.5–2 MB per listing). Clean old sessions periodically once Agent 2 has consumed them.
- If the FB session expires, the bot replies with `AUTH_REQUIRED` (code 2) and the refresh steps. Follow [Refresh Facebook login on the server](#refresh-facebook-login-on-the-server). Copy a new `.fb_profile` or `fb_storage_state.json` from a desktop login; do not commit them.
- `.env` and `.fb_profile/` contain secrets — never commit them (already in `.gitignore`).

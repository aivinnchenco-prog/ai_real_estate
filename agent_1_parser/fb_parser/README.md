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
- If Facebook returns login wall, script exits with `AUTH_REQUIRED` (exit code 2).
- If no photos could be saved, script exits with `NO_PHOTOS` (exit code 3).
- If feed/browse page is opened instead of listing card: `WRONG_PAGE` (exit code 4).
- Accepted item URL hosts: `www.facebook.com`, `m.facebook.com`, `mbasic.facebook.com`, bare `facebook.com`.
- Accepted path forms (auto-normalized to www):
  - `https://www.facebook.com/marketplace/item/{ITEM_ID}/`
  - `https://www.facebook.com/share/{CODE}/` (short share link — resolved to marketplace item in the logged-in browser)
  (mobile hosts are rewritten because `m.facebook.com` often shows an unsupported-browser page)

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
- If FB session expires, bot replies with `AUTH_REQUIRED` hint; refresh `.fb_profile` (re-login on a desktop machine and re-copy, or run `login_fb.py`).
- `.env` and `.fb_profile/` contain secrets — never commit them (already in `.gitignore`).

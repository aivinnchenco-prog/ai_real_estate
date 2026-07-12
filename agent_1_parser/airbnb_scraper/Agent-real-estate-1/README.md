# Airbnb scraper — Telegram CRM bot

Бот для парсинга Airbnb, записи в Google Sheets/Drive и управления задачами через Supabase + Cursor SDK.

## Возможности

- Парсинг ссылок Airbnb → CRM (Google Sheets), фото на Drive
- Описания для FB/TG через Claude
- Jarvis: поиск по базе, чат, ретроспектива
- `/task` — очередь задач Claude → Supabase
- `/cursor` — Cursor SDK, код на локальной машине

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполни .env
python3 setup_drive_oauth.py   # один раз, для Drive
python3 main.py
```

## Секреты (не коммитить)

| Файл | Назначение |
|------|------------|
| `.env` | токены бота, API keys |
| `credentials/` | Google service account, OAuth |

## Тесты

```bash
python3 -m unittest discover -s tests -v
```

## GitHub

После установки Xcode Command Line Tools (`xcode-select --install`):

Репозиторий: **https://github.com/bvinnchenco-cmyk/Agent-real-estate**

```bash
chmod +x scripts/github_setup.sh
./scripts/github_setup.sh
# или: ./scripts/github_setup.sh bvinnchenco-cmyk/Agent-real-estate
```

### Git без Apple (если `xcode-select --install` не качается)

Установщик с SourceForge (без сервера Apple):
```bash
curl -L -o /tmp/git.dmg "https://sourceforge.net/projects/git-osx-installer/files/git-2.33.0-intel-universal-mavericks.dmg/download"
hdiutil attach /tmp/git.dmg
installer -pkg "/Volumes/Git 2.33.0 Mavericks Intel Universal/git-2.33.0-intel-universal-mavericks.pkg" -target CurrentUserHomeDirectory
source scripts/git_env.sh
git --version
```

### Push на GitHub

```bash
source scripts/git_env.sh
cd "/Users/lifefmg/Desktop/Airbnb scraper"
git push -u origin main
```

При запросе логина: **Username** = `bvinnchenco-cmyk`, **Password** = [Personal Access Token](https://github.com/settings/tokens) (не пароль от GitHub).

## Сервер (VPS) — управление с телефона через Telegram

Бот — это и есть мобильный интерфейс: парсинг, CRM, задачи, `/cursor`. Чтобы не держать Mac включённым, запускай бота на VPS 24/7.

### 1. Первичная установка на Ubuntu

На сервере (root):

```bash
git clone https://github.com/bvinnchenco-cmyk/Agent-real-estate.git /opt/agent-real-estate
cd /opt/agent-real-estate
chmod +x deploy/*.sh
sudo bash deploy/install.sh
```

С локального Mac скопируй секреты (один раз):

```bash
scp .env root@ВАШ_IP:/opt/agent-real-estate/
scp -r credentials/* root@ВАШ_IP:/opt/agent-real-estate/credentials/
```

Запуск:

```bash
ssh root@ВАШ_IP "systemctl start airbnb-bot && journalctl -u airbnb-bot -f"
```

### 2. Автообновление: push → GitHub → сервер

После каждого `git push origin main` сервер подтягивает код и перезапускает бота.

**GitHub → Settings → Secrets and variables → Actions:**

| Secret | Пример |
|--------|--------|
| `DEPLOY_HOST` | IP или домен VPS |
| `DEPLOY_USER` | `root` |
| `DEPLOY_SSH_KEY` | приватный SSH-ключ (полностью, с `-----BEGIN...`) |
| `DEPLOY_PATH` | `/opt/agent-real-estate` (опционально) |

Автонастройка (если установлен `gh` и выполнен `gh auth login`):

```bash
chmod +x scripts/setup_github_deploy.sh
./scripts/setup_github_deploy.sh
```

На сервере один раз добавь публичный ключ в `~/.ssh/authorized_keys`.

Ручное обновление на сервере:

```bash
bash /opt/agent-real-estate/deploy/update.sh
```

### Локальный цикл разработки

```bash
# изменения → GitHub → (авто) сервер
git add -A && git commit -m "..." && git push origin main
```

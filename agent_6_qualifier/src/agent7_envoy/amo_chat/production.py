"""Production API constants for Open Home multi-agent backend (no secrets)."""

from __future__ import annotations

PRODUCTION_DOMAIN = "api.open-home.online"
PRODUCTION_PUBLIC_BASE_URL = "https://api.open-home.online"
PRODUCTION_DNS_TARGET = "72.60.108.152"
PRODUCTION_INTERNAL_HOST = "127.0.0.1"
PRODUCTION_INTERNAL_PORT = 8000
PRODUCTION_APP_USER = "openhome"
PRODUCTION_ROOT = "/opt/openhome"
PRODUCTION_APP_DIR = "/opt/openhome/app"
PRODUCTION_VENV_DIR = "/opt/openhome/venv"
PRODUCTION_RUNTIME_DIR = "/opt/openhome/runtime"
PRODUCTION_LOGS_DIR = "/opt/openhome/logs"
PRODUCTION_ENV_PATH = "/opt/openhome/.env"
PRODUCTION_BROWSER_PROFILES_DIR = "/opt/openhome/runtime/browser_profiles"
PRODUCTION_SERVICE_NAME = "openhome-api"
MAX_WEBHOOK_BODY_BYTES = 2 * 1024 * 1024

# Live flags must stay OFF after deploy until explicitly enabled later.
SAFE_LIVE_DEFAULTS = {
    "AGENT7_LIVE_OUTREACH_ENABLED": "false",
    "AGENT7_FACEBOOK_MESSENGER_ENABLED": "false",
    "AGENT7_AIRBNB_MESSAGES_ENABLED": "false",
    "AMO_CHAT_WEBHOOK_ENABLED": "false",
    "AMO_CHAT_MIRROR_LIVE": "false",
    "AMO_CHAT_CONNECT_LIVE": "false",
}


def production_registration_webhook_url() -> str:
    return f"{PRODUCTION_PUBLIC_BASE_URL}/webhooks/amo-chat/:scope_id"


def production_bind() -> str:
    return f"{PRODUCTION_INTERNAL_HOST}:{PRODUCTION_INTERNAL_PORT}"

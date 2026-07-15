#!/usr/bin/env python3
"""Синхронизация ключей между корневым .env и .env файлами агентов.

Корневой .env — единственное место, где редактируются ключи/токены.
Скрипт разносит значения по агентам с учётом того, что локальные имена
переменных у агентов различаются (NOTION_TOKEN vs NOTION_API_KEY и т.п.).

Режимы:
    python3 scripts/sync_env.py             root .env -> .env агентов
    python3 scripts/sync_env.py --collect   собрать значения из агентов
                                            в root .env (только пустые/новые)
    python3 scripts/sync_env.py --dry-run   показать изменения без записи

Скрипт никогда не печатает сами значения секретов — только имена ключей.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOT_ENV = ROOT / ".env"

# Для каждого .env агента: {локальное_имя: корневое_имя}
AGENT_ENV_MAP: dict[str, dict[str, str]] = {
    "agent_1_parser/airbnb_scraper/Agent-real-estate-1/.env": {
        "TG_BOT_TOKEN": "TG_BOT_TOKEN_AGENT1",
        "ADMIN_TG_IDS": "ADMIN_TG_IDS",
        "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL": "ANTHROPIC_MODEL",
        "CURSOR_API_KEY": "CURSOR_API_KEY",
    },
    "agent_1_parser/fb_parser/.env": {
        "TELEGRAM_BOT_TOKEN": "TG_BOT_TOKEN_FB_PARSER",
        "FB_EMAIL": "FB_EMAIL",
        "FB_PASSWORD": "FB_PASSWORD",
        "FB_BROWSER_PROFILE": "FB_BROWSER_PROFILE",
        "FB_HEADLESS": "FB_HEADLESS",
        "FB_PROXY": "FB_PROXY",
        "FB_PROXY_SCHEME": "FB_PROXY_SCHEME",
        "FB_PROXY_HOST": "FB_PROXY_HOST",
        "FB_PROXY_PORT": "FB_PROXY_PORT",
        "FB_PROXY_USER": "FB_PROXY_USER",
        "FB_PROXY_PASS": "FB_PROXY_PASS",
        "OPENAI_API_KEY": "OPENAI_API_KEY",
    },
    "agent_2_registrar/_import/assistant-media/.env.real-estate": {
        "NOTION_API_KEY": "NOTION_API_KEY",
        "NOTION_DB_ID": "NOTION_DB_ID",
        "CLOUDFLARE_ACCOUNT_ID": "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_ENDPOINT": "CLOUDFLARE_ENDPOINT",
        "CLOUDFLARE_BUCKET": "CLOUDFLARE_BUCKET",
        "CLOUDFLARE_ACCESS_KEY_ID": "CLOUDFLARE_ACCESS_KEY_ID",
        "CLOUDFLARE_SECRET_ACCESS_KEY": "CLOUDFLARE_SECRET_ACCESS_KEY",
        "CLOUDFLARE_PUBLIC_BASE_URL": "CLOUDFLARE_PUBLIC_BASE_URL",
        "GEMINI_API_KEY": "GEMINI_API_KEY",
        "GEMINI_MODEL": "GEMINI_MODEL",
        "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
        "GOOGLE_MAPS_API_KEY": "GOOGLE_MAPS_API_KEY",
        "CONTACT_PHONE": "WHATSAPP_NUMBER",
    },
    "agent_3_director/.env": {
        "NOTION_API_KEY": "NOTION_API_KEY",
        "NOTION_DB_ID": "NOTION_DB_ID",
        "CLOUDFLARE_ACCOUNT_ID": "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_BUCKET": "CLOUDFLARE_BUCKET",
        "CLOUDFLARE_ACCESS_KEY_ID": "CLOUDFLARE_ACCESS_KEY_ID",
        "CLOUDFLARE_SECRET_ACCESS_KEY": "CLOUDFLARE_SECRET_ACCESS_KEY",
        "CLOUDFLARE_PUBLIC_BASE_URL": "CLOUDFLARE_PUBLIC_BASE_URL",
        "SEEDANCE_PROVIDER": "SEEDANCE_PROVIDER",
        "HIGGSFIELD_API_KEY": "HIGGSFIELD_API_KEY",
        "HIGGSFIELD_API_SECRET": "HIGGSFIELD_API_SECRET",
        "HIGGSFIELD_MCP_ACCESS_TOKEN": "HIGGSFIELD_MCP_ACCESS_TOKEN",
    },
    "agent_4_publisher/.env": {
        "NOTION_API_KEY": "NOTION_API_KEY",
        "NOTION_DB_ID": "NOTION_DB_ID",
        "METRICOOL_USER_TOKEN": "METRICOOL_USER_TOKEN",
        "METRICOOL_USER_ID": "METRICOOL_USER_ID",
        "METRICOOL_BLOG_ID": "METRICOOL_BLOG_ID",
        "METRICOOL_TIMEZONE": "METRICOOL_TIMEZONE",
        "TELEGRAM_BOT_TOKEN": "TG_BOT_TOKEN_PUBLISHER",
        "TELEGRAM_CHANNEL": "TELEGRAM_CHANNEL",
        "CHATPLACE_API_KEY": "CHATPLACE_API_KEY",
        "CHATPLACE_MCP_URL": "CHATPLACE_MCP_URL",
        # Постинг в FB — ОТДЕЛЬНЫЙ аккаунт (не парсинговый FB_EMAIL из agent_1)
        "FB_POST_EMAIL": "FB_POST_EMAIL",
        "FB_POST_PASSWORD": "FB_POST_PASSWORD",
        "FB_POST_BROWSER_PROFILE": "FB_POST_BROWSER_PROFILE",
        "FB_HEADLESS": "FB_HEADLESS",
        "CLOUDFLARE_PUBLIC_BASE_URL": "CLOUDFLARE_PUBLIC_BASE_URL",
        "GOOGLE_MAPS_API_KEY": "GOOGLE_MAPS_API_KEY",
    },
    "agent_6_qualifier/.env": {
        "NOTION_TOKEN": "NOTION_API_KEY",
        "NOTION_DATABASE_ID": "NOTION_DB_ID",
        "GEMINI_API_KEY": "GEMINI_API_KEY",
        "GEMINI_MODEL": "GEMINI_MODEL",
        "TG_API_ID": "TG_API_ID",
        "TG_API_HASH": "TG_API_HASH",
        "TG_SESSION": "TG_SESSION",
        "AMO_SUBDOMAIN": "AMO_SUBDOMAIN",
        "AMO_ACCESS_TOKEN": "AMO_ACCESS_TOKEN",
        "ERROR_BOT_TOKEN": "ERROR_BOT_TOKEN",
        "ERROR_CHAT_ID": "ERROR_CHAT_ID",
    },
}

# Collect-only источники: легаси-копии, из которых значения забираем,
# но обратно ничего не пишем. {файл: {локальное_имя: корневое_имя}}
LEGACY_COLLECT_SOURCES: dict[str, dict[str, str]] = {
    "agent_1_parser/airbnb_scraper/.env": {
        "TG_BOT_TOKEN": "TG_BOT_TOKEN_AGENT1",
        "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL": "ANTHROPIC_MODEL",
        "CURSOR_API_KEY": "CURSOR_API_KEY",
    },
}

# Порядок приоритета источников при --collect (первый найденный побеждает)
COLLECT_ORDER = [
    "agent_4_publisher/.env",
    "agent_6_qualifier/.env",
    "agent_3_director/.env",
    "agent_2_registrar/_import/assistant-media/.env.real-estate",
    "agent_1_parser/fb_parser/.env",
    "agent_1_parser/airbnb_scraper/Agent-real-estate-1/.env",
    "agent_1_parser/airbnb_scraper/.env",
]


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def upsert_env(path: Path, updates: dict[str, str], dry_run: bool) -> list[str]:
    """Обновить/добавить ключи в .env, сохранив остальные строки. Возвращает имена изменённых ключей."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    existing = parse_env(path)
    changed: list[str] = []
    remaining = dict(updates)

    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in remaining:
                new_value = remaining.pop(key)
                if existing.get(key, "") != new_value:
                    changed.append(key)
                    new_lines.append(f"{key}={new_value}")
                    continue
        new_lines.append(line)

    additions = [(k, v) for k, v in remaining.items() if v]
    if additions:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append("# --- добавлено scripts/sync_env.py ---")
        for key, value in additions:
            new_lines.append(f"{key}={value}")
            changed.append(key)

    if changed and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return changed


def do_sync(dry_run: bool) -> int:
    root_values = parse_env(ROOT_ENV)
    if not root_values:
        print(f"Корневой {ROOT_ENV} пуст или отсутствует. Сначала заполните его "
              f"(шаблон: .env.example) или выполните --collect.")
        return 1
    total = 0
    for rel_path, mapping in AGENT_ENV_MAP.items():
        env_path = ROOT / rel_path
        updates = {
            local: root_values[root_key]
            for local, root_key in mapping.items()
            if root_values.get(root_key)
        }
        changed = upsert_env(env_path, updates, dry_run)
        total += len(changed)
        status = "DRY-RUN " if dry_run else ""
        if changed:
            print(f"{status}{rel_path}: обновлено {len(changed)}: {', '.join(changed)}")
        else:
            print(f"{rel_path}: без изменений")
    print(f"\nИтого изменённых ключей: {total}" + (" (ничего не записано)" if dry_run else ""))
    return 0


def do_collect(dry_run: bool) -> int:
    root_values = parse_env(ROOT_ENV)
    collected: dict[str, str] = {}
    conflicts: list[str] = []
    for rel_path in COLLECT_ORDER:
        env_path = ROOT / rel_path
        agent_values = parse_env(env_path)
        mapping = AGENT_ENV_MAP.get(rel_path) or LEGACY_COLLECT_SOURCES.get(rel_path, {})
        for local, root_key in mapping.items():
            value = agent_values.get(local, "")
            if not value:
                continue
            if root_key in collected and collected[root_key] != value:
                conflicts.append(f"{root_key}: разные значения ({rel_path} vs более ранний источник)")
                continue
            collected.setdefault(root_key, value)

    new_keys = {k: v for k, v in collected.items() if not root_values.get(k)}
    if not new_keys:
        print("Корневой .env уже содержит все значения, найденные у агентов.")
    else:
        changed = upsert_env(ROOT_ENV, new_keys, dry_run)
        status = "DRY-RUN " if dry_run else ""
        print(f"{status}.env (корень): добавлено {len(changed)}: {', '.join(changed)}")
    if conflicts:
        print("\nКОНФЛИКТЫ (оставлено значение из более приоритетного файла):")
        for c in conflicts:
            print(f"  - {c}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true",
                        help="собрать значения из .env агентов в корневой .env")
    parser.add_argument("--dry-run", action="store_true",
                        help="показать изменения, ничего не записывая")
    args = parser.parse_args()
    return do_collect(args.dry_run) if args.collect else do_sync(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

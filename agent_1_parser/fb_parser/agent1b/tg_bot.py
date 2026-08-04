#!/usr/bin/env python3
import json
import asyncio
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InputMediaPhoto, Message
from dotenv import load_dotenv


# www / m / mbasic / bare facebook.com — Marketplace item or /share/ short link.
FB_ITEM_URL_REGEX = re.compile(
    r"https?://(?:(?:www|m|mbasic)\.)?facebook\.com/marketplace/item/(\d+)[^\s]*",
    re.IGNORECASE,
)
FB_SHARE_URL_REGEX = re.compile(
    r"https?://(?:(?:www|m|mbasic)\.)?facebook\.com/share/([A-Za-z0-9][A-Za-z0-9/_-]*)[^\s]*",
    re.IGNORECASE,
)
# Back-compat alias used elsewhere / older docs.
FB_URL_REGEX = FB_ITEM_URL_REGEX
TG_TEXT_LIMIT = 3900
TG_ALBUM_LIMIT = 10
PARSER_TIMEOUT_SEC = 420

# One parse at a time: parser subprocesses share the same FB browser profile,
# and two Chromium instances on one profile corrupt each other.
PARSE_QUEUE_LOCK = asyncio.Lock()


def build_session_id() -> str:
    return "FB_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def extract_fb_url(text: str) -> str | None:
    """Accept Marketplace item or /share/ short links; return crawl-ready www URL.

    Mobile hosts (m.facebook.com) often serve an unsupported-browser page, so we
    rewrite to www before handing off to the parser. Share links are cleaned and
    resolved to /marketplace/item/{id}/ inside fb_parser.
    """
    text = text or ""
    match = FB_ITEM_URL_REGEX.search(text)
    if match:
        return f"https://www.facebook.com/marketplace/item/{match.group(1)}/"
    match = FB_SHARE_URL_REGEX.search(text)
    if match:
        path = match.group(1).rstrip("/")
        return f"https://www.facebook.com/share/{path}/"
    return None


def run_parser(parser_path: Path, workspace: Path, url: str, session_id: str, backend: str) -> tuple[int, str]:
    cmd = [
        sys.executable,
        str(parser_path),
        "--url",
        url,
        "--session",
        session_id,
        "--backend",
        backend,
        "--workspace",
        str(workspace),
        "--save-debug-json",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=PARSER_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return 1, f"PARSER_FAILED: timeout after {PARSER_TIMEOUT_SEC}s (page hang or network issue)."
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


def chunk_text(text: str, limit: int = TG_TEXT_LIMIT) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines(keepends=True):
        if size + len(line) > limit and current:
            chunks.append("".join(current).rstrip())
            current = [line]
            size = len(line)
        else:
            current.append(line)
            size += len(line)
    if current:
        chunks.append("".join(current).rstrip())
    return chunks


def list_session_photos(session_path: Path) -> list[Path]:
    photos_dir = session_path / "photos"
    if not photos_dir.exists():
        return []
    files = []
    for p in sorted(photos_dir.iterdir()):
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and p.is_file():
            files.append(p)
    return files


def load_parsed(session_path: Path) -> dict:
    parsed_path = session_path / "parsed.json"
    if not parsed_path.exists():
        return {}
    try:
        return json.loads(parsed_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def build_telegram_text(session_path: Path) -> str:
    parsed = load_parsed(session_path)
    title = parsed.get("title") or "Facebook Marketplace Listing"
    location = (parsed.get("location") or {}).get("raw") or "Thailand"
    price = parsed.get("price_raw") or ""
    body = (parsed.get("description") or "").strip()
    seller = parsed.get("seller_name") or ""
    source = parsed.get("source_url") or ""

    lat = (parsed.get("location") or {}).get("latitude")
    lng = (parsed.get("location") or {}).get("longitude")

    lines = [title, ""]
    if location:
        lines.append(location)
    if lat is not None and lng is not None:
        lines.append(f"Карта (приблизительно): https://www.google.com/maps?q={lat},{lng}")
    if source:
        lines.append(source)
    lines.append("")
    if price:
        lines.append(f"Цена: {price}")
    if body:
        lines.extend(["", body])
    if seller:
        lines.extend(["", f"Продавец: {seller}"])
    return "\n".join(lines).strip()


async def send_parse_result(message: Message, session_path: Path, session_id: str) -> None:
    photos = list_session_photos(session_path)
    telegram_text = build_telegram_text(session_path)

    header = f"✅ `{session_id}`\nФото: {len(photos)}"
    await message.answer(header, parse_mode="Markdown")

    for part in chunk_text(telegram_text or "(описание не извлечено)"):
        await message.answer(part)

    if not photos:
        await message.answer("Фото не найдены в session/photos/")
        return

    # No captions on albums: full text is already sent as a separate message,
    # and a caption renders as stray text between photo batches.
    for start in range(0, len(photos), TG_ALBUM_LIMIT):
        batch = photos[start : start + TG_ALBUM_LIMIT]
        media = [InputMediaPhoto(media=FSInputFile(str(photo))) for photo in batch]
        await message.answer_media_group(media)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def main() -> None:
    # Anchor .env and all relative paths to the project root so the bot works
    # no matter which directory it is started from (systemd, cron, ~, etc.).
    load_dotenv(PROJECT_ROOT / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")

    backend = os.getenv("FB_PARSER_BACKEND", "crawl4ai").strip()
    if backend not in ("crawl4ai", "scrapegraphai"):
        raise RuntimeError("FB_PARSER_BACKEND must be crawl4ai or scrapegraphai.")

    workspace_env = os.getenv("FB_PARSER_WORKSPACE", ".").strip() or "."
    workspace = Path(workspace_env)
    if not workspace.is_absolute():
        workspace = (PROJECT_ROOT / workspace).resolve()
    parser_path = (workspace / "agent1b" / "fb_parser.py").resolve()
    if not parser_path.exists():
        raise RuntimeError(f"Parser not found: {parser_path}")

    bot = Bot(token=token)
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def start_cmd(message: Message) -> None:
        await message.answer(
            "Отправь ссылку на Facebook Marketplace.\n"
            "Подходят:\n"
            "• `…/marketplace/item/{id}/` (www / m)\n"
            "• `…/share/…` (короткая share-ссылка)\n"
            f"Текущий backend: `{backend}`\n"
            "В ответ получишь описание + фото объекта.",
            parse_mode="Markdown",
        )

    @dp.message(Command("backend"))
    async def backend_cmd(message: Message) -> None:
        await message.answer(
            f"Текущий backend: `{backend}`\n"
            "Сменить можно через env `FB_PARSER_BACKEND=crawl4ai|scrapegraphai`.",
            parse_mode="Markdown",
        )

    @dp.message(F.text)
    async def process_link(message: Message) -> None:
        text = message.text or ""
        url = extract_fb_url(text)
        if not url:
            await message.answer("Не вижу валидной ссылки Facebook Marketplace (item или /share/) в сообщении.")
            return

        session_id = build_session_id()
        await message.answer(
            "Принял ссылку. Запускаю парсер...\n"
            f"`session: {session_id}`\n"
            f"`backend: {backend}`",
            parse_mode="Markdown",
        )

        async with PARSE_QUEUE_LOCK:
            code, output = await asyncio.to_thread(
                run_parser,
                parser_path=parser_path,
                workspace=workspace,
                url=url,
                session_id=session_id,
                backend=backend,
            )

        session_path = workspace / "data" / "sessions" / session_id
        if code == 0:
            try:
                await send_parse_result(message, session_path, session_id)
            except Exception as e:
                await message.answer(
                    "Парсинг успешен, но отправка в Telegram упала.\n"
                    f"`session: {session_id}`\n"
                    f"`path: {session_path}`\n"
                    f"Ошибка: `{e}`",
                    parse_mode="Markdown",
                )
            return

        hint = {
            2: (
                "Нужен логин FB / anti-bot.\n"
                "1) `source .venv311/bin/activate`\n"
                "2) `python agent1b/login_fb.py` — войти в Facebook\n"
                "3) В `.env` поставить `FB_HEADLESS=false`\n"
                "4) Отправить ссылку снова"
            ),
            3: "Карточка без фото галереи (или все превью отфильтрованы).",
            4: (
                "FB открыл ленту вместо карточки или заблокировал бота.\n"
                "Запусти `python agent1b/login_fb.py`, войди в аккаунт, затем повтори."
            ),
        }.get(code, "")
        safe_output = (output or "Unknown parser error")[:900]
        await message.answer(
            "Парсер завершился с ошибкой.\n"
            f"`code: {code}`\n"
            f"{hint}\n"
            f"```\n{safe_output}\n```"
        )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

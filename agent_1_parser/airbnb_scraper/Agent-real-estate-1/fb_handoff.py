"""FB Marketplace в едином боте Агента 1.

Бот принимает ссылку facebook.com/marketplace/item/… и:
  1) запускает FB-парсер (agent_1_parser/fb_parser, его собственный .venv311 —
     там живут crawl4ai и залогиненный браузерный профиль);
  2) копирует готовую сессию FB_YYYYMMDD_HHMMSS в data/sessions Агента 2;
  3) запускает agent2_structurize.py — объект попадает в Notion с ID F_….

Airbnb-ссылки идут по своему пути (workflow.take_listing_to_work).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import config
from agent2_handoff import find_agent2_root, run_agent2
from CustomLogger import logger

# www / m / mbasic / bare facebook.com — item или /share/ short link.
FB_ITEM_URL_REGEX = re.compile(
    r"https?://(?:(?:www|m|mbasic)\.)?facebook\.com/marketplace/item/(\d+)[^\s]*",
    re.IGNORECASE,
)
FB_SHARE_URL_REGEX = re.compile(
    r"https?://(?:(?:www|m|mbasic)\.)?facebook\.com/share/([A-Za-z0-9][A-Za-z0-9/_-]*)[^\s]*",
    re.IGNORECASE,
)
FB_URL_REGEX = FB_ITEM_URL_REGEX
FB_PARSER_TIMEOUT_SEC = 420

# Коды выхода fb_parser.py → подсказка пользователю
_FB_ERROR_HINTS = {
    2: "Нужен логин FB: в папке fb_parser запусти login_fb.py и повтори.",
    3: "Карточка без фото галереи — Агенту 2 нечего грузить.",
    4: "FB открыл ленту вместо карточки или заблокировал бота. Перелогинься через login_fb.py.",
}


@dataclass
class FbHandoffResult:
    ok: bool = False
    session_id: str = ""
    object_id: str = ""
    photos: int = 0
    note: str = ""
    message_text: str = ""
    image_paths: list[str] = field(default_factory=list)


def extract_fb_url(text: str) -> str | None:
    """Accept Marketplace item or /share/ short links; return crawl-ready www URL.

    m.facebook.com often serves an unsupported-browser page, so rewrite to www
    before handing off to fb_parser. Share links resolve to item URL inside parser.
    """
    text = text or ""
    m = FB_ITEM_URL_REGEX.search(text)
    if m:
        return f"https://www.facebook.com/marketplace/item/{m.group(1)}/"
    m = FB_SHARE_URL_REGEX.search(text)
    if m:
        path = m.group(1).rstrip("/")
        return f"https://www.facebook.com/share/{path}/"
    return None


def find_fb_parser_root() -> Path | None:
    """Корень проекта fb_parser: FB_PARSER_ROOT из env или авто-поиск в монорепе."""
    if config.FB_PARSER_ROOT:
        p = Path(config.FB_PARSER_ROOT).expanduser()
        if (p / "agent1b" / "fb_parser.py").exists():
            return p
        logger.warning(f"FB_PARSER_ROOT задан, но fb_parser.py не найден: {p}")
        return None
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "agent_1_parser" / "fb_parser"
        if (candidate / "agent1b" / "fb_parser.py").exists():
            return candidate
    return None


def _fb_python(fb_root: Path) -> str:
    """Python из .venv311 fb_parser (там crawl4ai); иначе текущий интерпретатор."""
    venv_python = fb_root / ".venv311" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def new_fb_session_id() -> str:
    return datetime.now().strftime("FB_%Y%m%d_%H%M%S")


def parse_fb_listing(url: str, fb_root: Path, session_id: str) -> tuple[int, str]:
    """Запуск fb_parser.py в его окружении. Возвращает (exit_code, output)."""
    cmd = [
        _fb_python(fb_root),
        str(fb_root / "agent1b" / "fb_parser.py"),
        "--url", url,
        "--session", session_id,
        "--backend", config.FB_PARSER_BACKEND,
        "--workspace", str(fb_root),
        "--save-debug-json",
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(fb_root), capture_output=True, text=True,
            timeout=FB_PARSER_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return 1, f"PARSER_FAILED: timeout after {FB_PARSER_TIMEOUT_SEC}s"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def copy_session_to_agent2(fb_root: Path, agent2_root: Path, session_id: str) -> Path:
    src = fb_root / "data" / "sessions" / session_id
    dst = agent2_root / "data" / "sessions" / session_id
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def list_photo_paths(session_path: Path) -> list[Path]:
    photos_dir = session_path / "photos"
    if not photos_dir.exists():
        return []
    return sorted(
        p for p in photos_dir.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and p.is_file()
    )


def count_photos(session_path: Path) -> int:
    return len(list_photo_paths(session_path))


def load_session_description(session_path: Path) -> str:
    desc = session_path / "description.txt"
    if desc.exists():
        try:
            return desc.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return ""


def handoff_fb_to_agent2(url: str) -> FbHandoffResult:
    """Полный путь FB-ссылки: парсер → сессия → Агент 2 (Notion)."""
    result = FbHandoffResult()

    fb_root = find_fb_parser_root()
    if fb_root is None:
        result.note = "FB-парсер не найден (задай FB_PARSER_ROOT в .env)"
        logger.error(result.note)
        return result

    session_id = new_fb_session_id()
    result.session_id = session_id
    logger.info(f"FB parse: {url} → {session_id}")

    code, output = parse_fb_listing(url, fb_root, session_id)
    if code != 0:
        hint = _FB_ERROR_HINTS.get(code, "")
        result.note = f"⚠️ FB-парсер упал (code {code}). {hint}\n{output[-600:]}"
        logger.error(f"FB parser failed ({code}): {output[-1500:]}")
        return result

    session_path = fb_root / "data" / "sessions" / session_id
    photo_paths = list_photo_paths(session_path)
    result.photos = len(photo_paths)
    result.image_paths = [str(p) for p in photo_paths]
    result.message_text = load_session_description(session_path)

    agent2_root = find_agent2_root()
    if agent2_root is None:
        result.note = f"📦 Сессия {session_id} готова, но Агент 2 не найден (AGENT2_ROOT в .env)"
        logger.error(result.note)
        return result

    copy_session_to_agent2(fb_root, agent2_root, session_id)

    if not config.AGENT2_AUTORUN:
        result.ok = True
        result.note = f"📦 Сессия {session_id} передана Агенту 2 (автозапуск выключен)"
        return result

    ok, object_id = run_agent2(agent2_root, session_id, url)
    result.ok = ok
    result.object_id = object_id
    if ok:
        result.note = f"📇 Notion: {object_id or session_id}"
    else:
        result.note = (f"⚠️ Сессия {session_id} собрана, но структуризация упала — "
                       f"запусти вручную: agent2_structurize.py --session {session_id}")
    return result

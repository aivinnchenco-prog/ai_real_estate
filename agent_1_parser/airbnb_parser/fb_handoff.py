"""FB Marketplace в едином боте Агента 1.

Бот принимает ссылку facebook.com/marketplace/item/… и:
  1) запускает FB-парсер (agent_1_parser/fb_parser, его собственный .venv311 —
     там живут crawl4ai и залогиненный браузерный профиль);
  2) копирует готовую сессию FB_YYYYMMDD_HHMMSS в data/sessions Агента 2;
  3) запускает agent2_structurize.py — объект попадает в Notion с ID F_….

Airbnb-ссылки идут по своему пути (workflow.take_listing_to_work).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
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
FB_PYTHON_PROBE_TIMEOUT_SEC = 40
# crawl4ai/lxml живут в Python 3.11 (.venv311). Тихий fallback на Python бота
# даёт ModuleNotFoundError: requests / crawl4ai на первой же FB-ссылке.
FB_PARSER_REQUIRED_MAJOR_MINOR = (3, 11)
FB_PARSER_ENSURE_VENV = (
    "sudo bash /opt/openhome/app/agent_1_parser/fb_parser/scripts/ensure_venv.sh"
)

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


def _required_fb_modules(backend: str | None = None) -> tuple[str, ...]:
    backend = (backend or getattr(config, "FB_PARSER_BACKEND", "crawl4ai") or "crawl4ai").strip()
    # openhome_shared — пакет монорепы (не pip). Нужен PYTHONPATH на корень app.
    if backend == "scrapegraphai":
        return ("requests", "playwright", "scrapegraphai", "openhome_shared")
    return ("requests", "playwright", "crawl4ai", "openhome_shared")


def _openhome_app_root(fb_root: Path | None = None) -> Path | None:
    """Корень монорепы: там лежит openhome_shared (кросс-процессный FB profile lock)."""
    explicit = (
        getattr(config, "OPENHOME_APP_ROOT", None)
        or os.getenv("OPENHOME_APP_ROOT")
        or ""
    ).strip()
    if explicit:
        p = Path(explicit).expanduser()
        if (p / "openhome_shared").is_dir():
            return p
    starts: list[Path] = []
    if fb_root is not None:
        starts.append(Path(fb_root).resolve())
    starts.append(Path(__file__).resolve())
    for start in starts:
        for parent in [start, *start.parents]:
            if (parent / "openhome_shared").is_dir() and (parent / "agent_1_parser").is_dir():
                return parent
    production = Path("/opt/openhome/app")
    if (production / "openhome_shared").is_dir():
        return production
    return None


def _fb_subprocess_env(fb_root: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    app_root = _openhome_app_root(fb_root)
    if app_root:
        extra = str(app_root)
        current = env.get("PYTHONPATH", "").strip()
        parts = [p for p in current.split(os.pathsep) if p]
        if extra not in parts:
            env["PYTHONPATH"] = extra if not parts else extra + os.pathsep + current
        env.setdefault("OPENHOME_APP_ROOT", extra)
    return env


def _configured_fb_python() -> str:
    return (getattr(config, "FB_PARSER_PYTHON", None) or "").strip()


def _fb_python_candidates(fb_root: Path) -> list[Path]:
    """Только явный FB_PARSER_PYTHON или .venv311. Без fallback на Python бота."""
    override = _configured_fb_python()
    if override:
        return [Path(override).expanduser()]
    return [
        fb_root / ".venv311" / "bin" / "python",
        fb_root / ".venv311" / "bin" / "python3",
    ]


def _probe_fb_python(
    python_bin: Path,
    modules: tuple[str, ...],
    fb_root: Path | None = None,
) -> tuple[bool, str]:
    """Проверяет, что бинарник стартует, это 3.11 и в нём есть пакеты парсера."""
    if not python_bin.exists():
        return False, f"файл не найден: {python_bin}"
    if not os.access(python_bin, os.X_OK):
        return False, f"нет права на запуск: {python_bin}"
    import_lines = "\n".join(f"import {name}" for name in modules)
    probe = (
        "import sys\n"
        "print(f'{sys.version_info.major}.{sys.version_info.minor}', flush=True)\n"
        + import_lines
    )
    try:
        proc = subprocess.run(
            [str(python_bin), "-c", probe],
            capture_output=True,
            text=True,
            timeout=FB_PYTHON_PROBE_TIMEOUT_SEC,
            env=_fb_subprocess_env(fb_root),
        )
    except FileNotFoundError:
        return False, f"не запускается (битый бинарник или чужая ОС): {python_bin}"
    except PermissionError:
        return False, f"нет права на запуск: {python_bin}"
    except subprocess.TimeoutExpired:
        return False, f"таймаут проверки {FB_PYTHON_PROBE_TIMEOUT_SEC}s: {python_bin}"
    except OSError as exc:
        return False, f"не запускается ({exc}): {python_bin}"

    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        return False, output[-500:] or f"код {proc.returncode} при import {', '.join(modules)}"

    version_line = (proc.stdout or "").strip().splitlines()[:1]
    raw_version = version_line[0].strip() if version_line else ""
    allow_other = bool(getattr(config, "FB_PARSER_ALLOW_NON311", False))
    if not allow_other:
        expected = ".".join(str(part) for part in FB_PARSER_REQUIRED_MAJOR_MINOR)
        if raw_version != expected:
            return False, (
                f"нужен Python {expected} (crawl4ai/lxml), а {python_bin} это {raw_version or 'unknown'}"
            )
    return True, raw_version or "ok"


def resolve_fb_parser_python(fb_root: Path | None = None) -> tuple[str | None, str]:
    """Вернуть рабочий Python FB-парсера или понятную ошибку. Без тихого fallback."""
    root = fb_root if fb_root is not None else find_fb_parser_root()
    if root is None:
        return None, (
            "FB-парсер не найден (задай FB_PARSER_ROOT в .env). "
            f"Почини: {FB_PARSER_ENSURE_VENV}"
        )
    modules = _required_fb_modules()
    tried: list[str] = []
    for candidate in _fb_python_candidates(root):
        ok, detail = _probe_fb_python(candidate, modules, fb_root=root)
        if ok:
            return str(candidate), ""
        tried.append(f"{candidate}: {detail}")
    override = _configured_fb_python()
    if override:
        hint = f"FB_PARSER_PYTHON={override} не подходит."
    else:
        hint = (
            f"Нет рабочего {root / '.venv311' / 'bin' / 'python'}. "
            "Не подставляю Python бота — из-за этого был ModuleNotFoundError: requests."
        )
    return None, (
        f"{hint} Нужны пакеты: {', '.join(modules)}. "
        f"Почини: {FB_PARSER_ENSURE_VENV}\n" + "\n".join(tried)
    )


def describe_fb_parser_env() -> tuple[bool, str]:
    """Проверка для старта бота: Airbnb не блокируем, FB — честный статус."""
    python_bin, err = resolve_fb_parser_python()
    if python_bin:
        return True, f"FB Marketplace: готов ({python_bin})"
    return False, f"FB Marketplace выключен: {err}"


def _fb_python(fb_root: Path) -> str:
    """Рабочий Python FB-парсера или RuntimeError — без fallback на sys.executable."""
    python_bin, err = resolve_fb_parser_python(fb_root)
    if not python_bin:
        raise RuntimeError(err)
    return python_bin


def new_fb_session_id() -> str:
    return datetime.now().strftime("FB_%Y%m%d_%H%M%S")


def parse_fb_listing(url: str, fb_root: Path, session_id: str) -> tuple[int, str]:
    """Запуск fb_parser.py в его окружении. Возвращает (exit_code, output)."""
    try:
        python_bin = _fb_python(fb_root)
    except RuntimeError as exc:
        return 1, f"PARSER_ENV: {exc}"
    cmd = [
        python_bin,
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
            env=_fb_subprocess_env(fb_root),
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

    python_bin, env_err = resolve_fb_parser_python(fb_root)
    if not python_bin:
        result.note = f"⚠️ FB-парсер не готов.\n{env_err}"
        logger.error(env_err)
        return result

    session_id = new_fb_session_id()
    result.session_id = session_id
    logger.info(f"FB parse: {url} → {session_id} ({python_bin})")

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

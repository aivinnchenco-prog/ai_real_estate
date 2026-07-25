import asyncio
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery, Message, ContentType, InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from aiogram.utils.media_group import MediaGroupBuilder
import html
import os
import re
import sys
from pathlib import Path
from CustomLogger import logger
from google_sheets import GoogleSheetsWriter
from voice_agent import VoiceAgent
from airbnb_url import normalize_airbnb_url, resolve_currency
from fb_handoff import extract_fb_url, handoff_fb_to_agent2
from agent2_handoff import find_agent2_root, resolve_object_id
from parser_pool import close_parser
from workflow import finalize_take_work, take_listing_to_work
from queue_worker import ensure_agent_listener_task, poll_loop, poll_once
import config
import asyncio

_queue_poll_task = None


BOT_TOKEN = config.TG_BOT_TOKEN
MAX_TEXT_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024
USERS_FILE = 'Users.txt'

def load_users():
    """Loads users (ID and comment) from the users file."""
    if not os.path.exists(USERS_FILE):
        return {}
    users = {}
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                if ',' in line:
                    parts = line.strip().split(',', 1)
                    if len(parts) == 2 and parts[0].isdigit():
                        users[int(parts[0])] = parts[1].strip()
    except (IOError, ValueError) as e:
        logger.error(f"Error loading users from {USERS_FILE}: {e}")
        return {}
    return users

def save_users(users):
    """Saves users (ID and comment) to the users file."""
    try:
        with open(USERS_FILE, 'w', encoding='utf-8') as f:
            for user_id, comment in users.items():
                f.write(f"{user_id},{comment}\n")
    except IOError as e:
        logger.error(f"Error saving users to {USERS_FILE}: {e}")


if not BOT_TOKEN or ':' not in BOT_TOKEN:
    raise SystemExit(
        'TG_BOT_TOKEN не задан или неверный.\n'
        '1. Открой .env в папке проекта\n'
        '2. Строка: TG_BOT_TOKEN=123456:AAF...\n'
        '3. Сохрани файл (Cmd+S)\n'
        '4. Запуск: cd "/Users/lifefmg/Desktop/Airbnb scraper" && python3 main.py'
    )

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
admins = config.ADMIN_IDS
registered_users = load_users()
router = Router()

class AdminStates(StatesGroup):
    add_user_id = State()
    delete_user_id = State()

def get_allowed_users():
    registered_users = load_users() # we need to load users from file (it may be edited manually)
    return set(admins + list(registered_users.keys()))

sheets_writer = GoogleSheetsWriter()
voice_agent = VoiceAgent(sheets_writer)

@router.message(Command("admin"))
async def admin_menu(message: Message):
    if message.from_user.id not in admins:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить Пользователя", callback_data="admin_add_user")],
        [InlineKeyboardButton(text="➖ Удалить Пользователя", callback_data="admin_delete_user")],
        [InlineKeyboardButton(text="👥 Показать Пользователей", callback_data="admin_show_users")],
    ])
    await message.answer("Admin Menu:", reply_markup=keyboard)

@router.callback_query(F.data == "admin_add_user")
async def on_add_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in admins: return
    await callback.message.answer("Отправьте User ID и комментарий через запятую (например: 12345, Test user).")
    await state.set_state(AdminStates.add_user_id)
    await callback.answer()

@router.callback_query(F.data == "admin_delete_user")
async def on_delete_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in admins: return
    await callback.message.answer("Введите User ID для удаления.")
    await state.set_state(AdminStates.delete_user_id)
    await callback.answer()

@router.callback_query(F.data == "admin_show_users")
async def on_show_users(callback: CallbackQuery):
    if callback.from_user.id not in admins: return
    all_users = get_allowed_users()
    if not all_users:
        await callback.message.answer("Пользователей нет.")
    else:
        users_list = []
        for user_id, comment in registered_users.items():
            users_list.append(f"{user_id}, {comment}")
        users_list_str = "\n".join(users_list)
        await callback.message.answer(f"<b>Пользователи:</b>\n{users_list_str}")
    await callback.answer()


@router.message(Command("start"))
async def cmd_start(message: Message):
    if message.from_user.id not in get_allowed_users():
        await message.reply(f"Пользователь: {message.from_user.id}")
        return
    name = message.from_user.first_name or 'босс'
    intro = await asyncio.to_thread(
        voice_agent.chat,
        message.from_user.id,
        f'Привет! Я {name}, мой агент — {config.AGENT_NAME}. Кратко представься и скажи чем можешь помочь.',
        name,
    )
    await send_plain_text(message, intro)


# ---------- Ручной запуск агентов по ID объекта ----------
# /agent3 <ID> — монтаж видео (Director/Seedance) для одного объекта
# /agent4 <ID> — публикация (Metricool + FB-ветки) для одного объекта
# Оба идут через chain_runner: он уважает флаги «Монтаж»/«Публикация» и статусы.

CHAIN_DIR = Path(__file__).resolve().parents[3] / "agent_2_registrar" / "_import" / "assistant-media"
_OBJECT_ID_RE = re.compile(r"^[A-Za-z]{0,3}_?\d{8}_\d{3}$")
_agent_runs: set[str] = set()


def _parse_object_id_arg(text: str, command: str) -> str | None:
    arg = (text or "").removeprefix(command).strip()
    return arg if arg and _OBJECT_ID_RE.match(arg) else None


def _object_id_from_text(text: str, *, prefix: str | None = None) -> str | None:
    """ID объекта из текста: «A_20260719_003» или «agent3 A_…» (без слэша)."""
    raw = (text or "").strip()
    if prefix and raw.lower().startswith(prefix.lower()):
        raw = raw[len(prefix):].strip()
    if _OBJECT_ID_RE.match(raw):
        return raw.upper()
    return None


async def _run_chain_for_object(message: Message, from_agent: int,
                                object_id: str, label: str, *,
                                force_montage: bool = False) -> None:
    key = f"{from_agent}:{object_id}"
    if key in _agent_runs:
        await message.reply(f"⏳ {label} по {object_id} уже выполняется. Подождите.")
        return
    _agent_runs.add(key)
    await message.reply(
        f"🚀 Запускаю {label} по объекту <b>{object_id}</b>. "
        "Это может занять несколько минут — напишу, когда закончится."
    )
    try:
        cmd = [
            sys.executable, "scripts/chain_runner.py",
            "--from-agent", str(from_agent), "--object-id", object_id,
        ]
        if force_montage:
            cmd.append("--force-montage")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(CHAIN_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        tail_lines = out.decode(errors="replace").strip().splitlines()[-12:]
        tail = html.escape("\n".join(tail_lines)) or "(нет вывода)"
        icon = "✅" if proc.returncode == 0 else "❌"
        await message.answer(
            f"{icon} {label} по <b>{object_id}</b> завершён "
            f"(код {proc.returncode})\n<pre>{tail[-3000:]}</pre>"
        )
    except Exception as exc:
        logger.error(f"/agent{from_agent} {object_id} error: {exc}")
        await message.answer(f"❌ Ошибка запуска {label}: {exc}")
    finally:
        _agent_runs.discard(key)


@router.message(Command("agent3"))
async def cmd_agent3(message: Message):
    if message.from_user.id not in get_allowed_users():
        return
    object_id = _parse_object_id_arg(message.text, "/agent3")
    if not object_id:
        await message.reply(
            "Использование: <code>/agent3 A_20260713_003</code>\n"
            "Или нажмите /agent3 и отправьте только ID объекта следующим сообщением.\n"
            "Монтаж видео по выбранному движку (Seedance/Wan)."
        )
        return
    await _run_chain_for_object(
        message, 3, object_id, "Агент 3 (монтаж)", force_montage=True,
    )


@router.message(Command("agent4"))
async def cmd_agent4(message: Message):
    if message.from_user.id not in get_allowed_users():
        return
    object_id = _parse_object_id_arg(message.text, "/agent4")
    if not object_id:
        await message.reply(
            "Использование: <code>/agent4 A_20260713_003</code>\n"
            "Публикация в соц.сети (Metricool + FB) для объекта. Требуются: "
            "статус ready_to_post и флаг «Публикация» ≠ НЕТ."
        )
        return
    await _run_chain_for_object(message, 6, object_id, "Агент 4 (публикация)")


async def send_plain_text(message: Message, text: str):
    if len(text) <= MAX_TEXT_LENGTH:
        await message.answer(text)
        return
    for i in range(0, len(text), MAX_TEXT_LENGTH):
        await message.answer(text[i:i + MAX_TEXT_LENGTH])


@router.message(F.content_type == ContentType.TEXT)
async def handle_text_message(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state == AdminStates.add_user_id:
        await process_add_user(message, state)
    elif current_state == AdminStates.delete_user_id:
        await process_delete_user(message, state)
    elif message.text:
        text = message.text.strip()
        if text.startswith('http'):
            await handle_url_message(message)
            return
        user_id = message.from_user.id
        if user_id in get_allowed_users():
            oid = _object_id_from_text(text, prefix='agent3')
            if oid:
                await _run_chain_for_object(
                    message, 3, oid, "Агент 3 (монтаж)", force_montage=True,
                )
                return
            oid = _object_id_from_text(text, prefix='agent4')
            if oid:
                await _run_chain_for_object(message, 6, oid, "Агент 4 (публикация)")
                return
            # После /agent3 из меню часто шлют только ID — не уводить в LLM-чат
            oid = _object_id_from_text(text)
            if oid:
                await _run_chain_for_object(
                    message, 3, oid, "Агент 3 (монтаж)", force_montage=True,
                )
                return
        await process_agent_query(message, text)
    else:
        await process_agent_query(message, message.text or '')


async def process_agent_query(message: Message, text: str):
    user_id = message.from_user.id
    if user_id not in get_allowed_users():
        await message.reply(f"Пользователь: {user_id}")
        return

    text = text.strip()
    if not text:
        await message.reply('Отправьте текстовый запрос или ссылку Airbnb / FB Marketplace.')
        return

    try:
        reply = await asyncio.to_thread(
            voice_agent.chat,
            user_id,
            text,
            message.from_user.first_name or '',
        )
        await send_plain_text(message, reply)
    except Exception as e:
        logger.error(f'Ошибка обработки запроса: {e}')
        err = str(e)
        if 'API key' in err or 'API_KEY' in err or 'PERMISSION_DENIED' in err:
            await message.reply(
                'Ошибка чата с ассистентом (Gemini / GEMINI_API_KEY).\n'
                'К монтажу не относится.\n\n'
                'Ручной монтаж: <code>/agent3 A_20260719_003</code> '
                'или отправьте только ID объекта.'
            )
            return
        await message.reply(f'Ошибка: {e}')

# ---------- Кнопки «Монтаж / Видео-движок / Публикация» после парсинга ----------
# При выводе кнопок флаги сразу пишутся в Notion как НЕТ (объект ждёт
# решения) — иначе вотчер цепочки (опрос ~30с) успеет запустить монтаж до
# ответа. Нажатие ДА снимает паузу, и цепочка подхватывает объект.
import uuid
from notion_flags import VIDEO_ENGINE_CODES, set_flags

_flag_choices: dict[str, dict] = {}


def _flag_btn(token: str, kind: str, value: str, chosen: str | None) -> InlineKeyboardButton:
    label = {"montage": "Монтаж", "publish": "Публикация"}[kind]
    mark = "✅ " if chosen == value else ""
    return InlineKeyboardButton(
        text=f"{mark}{label}: {value}",
        callback_data=f"flag:{token}:{kind}:{value}",
    )


def _engine_btn(token: str, code: str, chosen: str | None) -> InlineKeyboardButton:
    label = VIDEO_ENGINE_CODES[code]
    mark = "✅ " if chosen == label else ""
    return InlineKeyboardButton(
        text=f"{mark}{label}",
        callback_data=f"flag:{token}:engine:{code}",
    )


def _flags_keyboard(token: str) -> InlineKeyboardMarkup:
    c = _flag_choices.get(token, {})
    return InlineKeyboardMarkup(inline_keyboard=[
        [_flag_btn(token, "montage", "ДА", c.get("montage")),
         _flag_btn(token, "montage", "НЕТ", c.get("montage"))],
        [_flag_btn(token, "publish", "ДА", c.get("publish")),
         _flag_btn(token, "publish", "НЕТ", c.get("publish"))],
        [_engine_btn(token, "seedance", c.get("video_engine")),
         _engine_btn(token, "wan", c.get("video_engine"))],
    ])


def _flags_ready(entry: dict) -> bool:
    montage, publish = entry.get("montage"), entry.get("publish")
    if not (montage and publish):
        return False
    if montage == "ДА" and not entry.get("video_engine"):
        return False
    return True


async def send_flags_question(message: Message, object_id: str) -> None:
    """Спросить про монтаж/публикацию/движок; до ответа объект стоит на паузе."""
    token = uuid.uuid4().hex[:8]
    _flag_choices[token] = {
        "object_id": object_id,
        "montage": None,
        "publish": None,
        "video_engine": None,
    }
    ok, note = await asyncio.to_thread(set_flags, object_id, "НЕТ", "НЕТ")
    if not ok:
        logger.error(f"notion_flags: не удалось поставить паузу для {object_id}: {note}")
    await message.answer(
        f"🎬 Что делать с объектом <b>{object_id}</b>?\n"
        "1) Монтаж — генерировать ли видео.\n"
        "2) Нейросеть — Seedance 2.0 или Wan 2.7 (нужно, если монтаж = ДА).\n"
        "3) Публикация — постить ли в соц.сети (при монтаж=НЕТ — карусель фото).\n"
        "Пока не выбрано всё нужное — объект ждёт в базе.",
        reply_markup=_flags_keyboard(token),
    )


def _flags_summary(object_id: str, montage: str, publish: str, video_engine: str | None) -> str:
    engine_part = ""
    if montage == "ДА" and video_engine:
        engine_part = f", нейросеть — {video_engine}"
    text = (
        f"Объект <b>{object_id}</b>: монтаж — {montage}, "
        f"публикация — {publish}{engine_part}."
    )
    if montage == "ДА" and publish == "ДА":
        return text + "\nПолная цепочка: видео + публикация во все площадки."
    if montage == "НЕТ" and publish == "ДА":
        return text + "\nБез видео: будет опубликована карусель фото."
    if montage == "ДА" and publish == "НЕТ":
        return text + "\nВидео смонтируется и сохранится в таблице, постинга не будет."
    return text + "\nОбъект остаётся только в базе."


async def _kick_chain_after_flags(object_id: str) -> None:
    """После выбора «Монтаж»/«Публикация» — подхватить цепочку (уважает флаги в Notion)."""
    key = f"flags:{object_id}"
    if key in _agent_runs:
        return
    _agent_runs.add(key)
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "scripts/chain_runner.py",
            "--from-agent", "3", "--object-id", object_id,
            cwd=str(CHAIN_DIR),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception as exc:
        logger.error(f"chain after flags {object_id}: {exc}")
    finally:
        _agent_runs.discard(key)


@router.callback_query(F.data.startswith("flag:"))
async def on_flag_choice(callback: CallbackQuery):
    try:
        _, token, kind, value = callback.data.split(":", 3)
    except ValueError:
        await callback.answer()
        return
    entry = _flag_choices.get(token)
    if entry is None or kind not in ("montage", "publish", "engine"):
        await callback.answer(
            "Кнопки устарели (бот перезапускался). Поставьте флажки "
            "«Монтаж», «Видео-движок» и «Публикация» прямо в Notion.",
            show_alert=True,
        )
        return

    if kind == "engine":
        if value not in VIDEO_ENGINE_CODES:
            await callback.answer()
            return
        entry["video_engine"] = VIDEO_ENGINE_CODES[value]
        ok, note = await asyncio.to_thread(
            set_flags, entry["object_id"], None, None, entry["video_engine"]
        )
        if not ok:
            entry["video_engine"] = None
            await callback.answer(f"Ошибка записи в Notion: {note}", show_alert=True)
            return
    else:
        entry[kind] = value
        if kind == "montage" and value == "НЕТ":
            entry["video_engine"] = None
        ok, note = await asyncio.to_thread(
            set_flags,
            entry["object_id"],
            value if kind == "montage" else None,
            value if kind == "publish" else None,
        )
        if not ok:
            entry[kind] = None
            await callback.answer(f"Ошибка записи в Notion: {note}", show_alert=True)
            return

    if _flags_ready(entry):
        await callback.message.edit_text(
            _flags_summary(
                entry["object_id"],
                entry["montage"],
                entry["publish"],
                entry.get("video_engine"),
            )
        )
        _flag_choices.pop(token, None)
        asyncio.create_task(_kick_chain_after_flags(entry["object_id"]))
    else:
        hint = ""
        if entry.get("montage") == "ДА" and not entry.get("video_engine"):
            hint = " Выберите нейросеть для видео."
        await callback.message.edit_reply_markup(reply_markup=_flags_keyboard(token))
        await callback.answer(f"Записано в Notion.{hint}")
        return
    await callback.answer("Записано в Notion")


# FB-парсер использует один браузерный профиль — параллельные запуски его ломают
_FB_PARSE_LOCK = asyncio.Lock()


async def _resolve_listing_object_id(result) -> str:
    """object_id из результата парсинга; fallback — session.json Агента 2."""
    oid = (getattr(result, "object_id", None) or "").strip()
    if oid:
        return oid
    session_id = (getattr(result, "session_id", None) or "").strip()
    if not session_id:
        return ""
    agent2_root = find_agent2_root()
    if agent2_root is None:
        return ""
    return resolve_object_id(agent2_root, session_id)


async def handle_fb_url_message(message: Message, fb_url: str):
    """FB Marketplace: парсер → сессия → Агент 2 (Notion)."""
    await message.reply("Ссылка Facebook Marketplace. Парсю и передаю Агенту 2...")

    async with _FB_PARSE_LOCK:
        result = await asyncio.to_thread(handoff_fb_to_agent2, fb_url)

    if result.ok:
        object_id = await _resolve_listing_object_id(result)
        await message.answer(
            f"✅ {object_id or result.session_id} добавлен в CRM\n"
            f"📷 Фото: {result.photos}\n{result.note}"
        )
        if object_id:
            await send_flags_question(message, object_id)
    else:
        await message.answer(result.note or "Неизвестная ошибка FB-парсера")


async def handle_url_message(message: Message):
    user_id = message.from_user.id
    allowed_users = get_allowed_users()
    if user_id not in allowed_users:
        await message.reply(f"Пользователь: {user_id}")
        return

    fb_url = extract_fb_url(message.text)
    if fb_url:
        try:
            await handle_fb_url_message(message, fb_url)
        except Exception as e:
            logger.error(f'FB URL error: {e}')
            await message.reply(f'Ошибка обработки FB-ссылки: {e}')
        return

    await message.reply(f"Обрабатываю URL. Пожалуйста подождите...")

    if "airbnb" not in message.text:
        await message.reply(
            "Пришли ссылку на Airbnb-объявление или Facebook Marketplace item."
        )
        return

    url = normalize_airbnb_url(message.text)
    currency = resolve_currency(url)
    logger.info(f'Нормализованный URL ({currency}): {url}')

    try:
        manager = registered_users.get(user_id, str(user_id))
        if message.from_user.full_name:
            manager = message.from_user.full_name

        result = await asyncio.to_thread(
            take_listing_to_work,
            url,
            manager,
            sheets_writer,
        )
        text = result.message_text
        local_image_paths = result.image_paths
        object_id = await _resolve_listing_object_id(result)

        logger.debug(f'Найдено {len(local_image_paths)} изображений.')
        if text:
            try:
                logger.info('Обработка URL завершена.')
                if object_id:
                    timing_line = ''
                    if result.timings:
                        t = result.timings
                        timing_line = (
                            f'\n⏱ {t.get("total_sec", "?")}с '
                            f'(парсинг {t.get("parse_sec", "?")}с, '
                            f'фото {t.get("photos_downloaded", "?")}/{t.get("photos_found", "?")} '
                            f'за {t.get("photos_download_sec", "?")}с, '
                            f'drive {t.get("drive_upload_sec", "—")}с)'
                        )
                    await message.answer(f'✅ {object_id} добавлен в CRM{timing_line}')
                if result.drive_note:
                    await message.answer(result.drive_note)
                if len(text) > MAX_TEXT_LENGTH:
                    message_part = ""
                    for line in text.split('\n'):
                        if len(line) > MAX_TEXT_LENGTH:
                            if message_part:
                                await message.answer(message_part)
                                message_part = ""
                            for i in range(0, len(line), MAX_TEXT_LENGTH):
                                await message.answer(line[i:i+MAX_TEXT_LENGTH])
                            continue

                        if len(message_part) + len(line) + 1 > MAX_TEXT_LENGTH:
                            await message.answer(message_part)
                            message_part = ""

                        message_part += line + '\n'

                    if message_part.strip():
                        await message.answer(message_part)
                else:
                    await message.answer(text)

                if local_image_paths:
                    media_group = MediaGroupBuilder(caption="")
                    for path in local_image_paths:
                        try:
                            media_group.add_photo(media=FSInputFile(path))
                        except Exception as e:
                            logger.error(f"Failed to add photo {path} to media group: {e}")

                        if len(media_group.build()) >= 10:
                            await message.answer_media_group(media=media_group.build())
                            media_group = MediaGroupBuilder(caption="")

                    if len(media_group.build()) > 0:
                        await message.answer_media_group(media=media_group.build())

                # Кнопки — последним сообщением, иначе теряются под альбомами фото.
                if object_id:
                    await send_flags_question(message, object_id)

            finally:
                finalize_take_work(result)
        else:
            logger.info("Во входных данных не обнаружено URL для обработки!")
            await message.reply("Во входных данных не обнаружено URL для обработки!")
    except Exception as e:
        logger.error(f"Ошибка обработки URL: {e}")
        await message.reply(f"Произошла ошибка при обработке URL: {e}")
                
async def process_add_user(message: Message, state: FSMContext):
    global registered_users
    try:
        if ',' not in message.text:
            await message.answer("Неверный формат. Пожалуйста, отправьте User ID и комментарий через запятую (например: 12345, Test user).")
            return

        user_id_str, comment = message.text.split(',', 1)
        user_id = int(user_id_str.strip())
        comment = comment.strip()

        if user_id in registered_users.keys():
            await message.answer(f"Пользователь {user_id} уже существует. Его комментарий будет обновлен.")
        
        registered_users[user_id] = comment
        save_users(registered_users)
        await message.answer(f"Пользователь {user_id} ({comment}) успешно добавлен/обновлен.")
    except (ValueError, IndexError):
        await message.answer("Неверный формат. Пожалуйста, отправьте User ID и комментарий через запятую (например: 12345, Test user).")
    finally:
        await state.clear()

async def process_delete_user(message: Message, state: FSMContext):
    global registered_users
    try:
        user_id = int(message.text)
        if user_id in admins:
            await message.answer(f"Не могу удалить администратора с User ID: {user_id}.")
        elif user_id not in registered_users.keys():
            await message.answer(f"Пользователь {user_id} не зарегистрирован.")
        else:
            del registered_users[user_id]
            save_users(registered_users)
            await message.answer(f"Пользователь {user_id} успешно удален.")
    except ValueError:
        await message.answer("Неверный User ID. Пожалуйста, отправьте числовой User ID.")
    finally:
        await state.clear()

async def set_commands():
    # Set commands for regular users
    user_commands = [
        BotCommand(command='start', description='Старт'),
        BotCommand(command='agent3', description='Монтаж видео по ID объекта'),
        BotCommand(command='agent4', description='Публикация в соц.сети по ID объекта'),
    ]
    await bot.set_my_commands(user_commands, BotCommandScopeDefault())

    # Set extended commands for admins
    admin_commands = user_commands + [
        BotCommand(command='admin', description='Управление пользователями'),
    ]
    for admin_id in admins:
        try:
            await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as e:
            logger.warning(f'Не удалось задать команды для admin {admin_id}: {e}')

async def start_bot():
    global _queue_poll_task
    me = await bot.get_me()
    bot_name = f'@{me.username}' if me.username else me.first_name
    logger.info(f'Бот успешно запущен: {bot_name} (id={me.id})')
    await set_commands()

    listener_id = await asyncio.to_thread(ensure_agent_listener_task)
    if listener_id:
        logger.info(f'Cursor listener task: {listener_id}')

    if config.ENABLE_TASK_QUEUE_POLLER and config.SUPABASE_URL:
        _queue_poll_task = asyncio.create_task(poll_loop(bot, admins))
        await poll_once(bot, admins)

    queue_note = ''
    if config.SUPABASE_URL:
        queue_note = f'\n📋 Очередь Supabase: опрос каждые {config.TASK_POLL_INTERVAL_SEC} с'

    for admin_id in admins:
        try:
            await bot.send_message(admin_id, f'Я запущен 🥳\n{bot_name}{queue_note}')
        except Exception as e:
            logger.warning(f'Не удалось написать admin {admin_id}: {e}')

async def stop_bot():
    global _queue_poll_task
    if _queue_poll_task:
        _queue_poll_task.cancel()
        try:
            await _queue_poll_task
        except asyncio.CancelledError:
            pass
        _queue_poll_task = None
    try:
        close_parser()
        for admin_id in admins:
            await bot.send_message(admin_id, 'Бот остановлен.😔')
    except:
        pass
    logger.error("Бот остановлен!")

async def main():

    dp.include_router(router)

    dp.startup.register(start_bot)
    dp.shutdown.register(stop_bot)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
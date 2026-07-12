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
import os
from CustomLogger import logger
from google_sheets import GoogleSheetsWriter
from voice_agent import VoiceAgent, format_listing_card, format_search_header, help_text
from airbnb_url import normalize_airbnb_url, resolve_currency
from fb_handoff import extract_fb_url, handoff_fb_to_agent2
from parser_pool import close_parser
from workflow import finalize_take_work, take_listing_to_work
from claude_planner import plan_and_enqueue
from cursor_agent import CursorAgentRunner
from queue_worker import ensure_agent_listener_task, poll_loop, poll_once
import config
import asyncio

_queue_poll_task = None
_cursor_busy: set[int] = set()
cursor_runner = CursorAgentRunner()


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


@router.message(Command("help"))
async def cmd_help(message: Message):
    if message.from_user.id not in get_allowed_users():
        return
    await message.answer(help_text())


@router.message(Command("task"))
async def cmd_task(message: Message):
    if message.from_user.id not in admins:
        await message.reply('Команда /task только для админов.')
        return

    text = (message.text or '').strip()
    if text.lower().startswith('/task'):
        text = text[5:].strip()

    if not text:
        await message.reply(
            'Использование:\n'
            '<code>/task описание задачи</code>\n\n'
            'Claude структурирует → Supabase → Cursor подхватит автоматически.'
        )
        return

    await message.reply('📋 Claude структурирует задачу...')
    try:
        task_id = await asyncio.to_thread(plan_and_enqueue, text, 'airbnb-bot')
    except Exception as exc:
        logger.error(f'/task error: {exc}')
        await message.reply(f'Ошибка: {exc}')
        return

    await message.reply(
        f'✅ Задача в Supabase\n'
        f'ID: <code>{task_id}</code>\n'
        f'Очередь подхватит в течение ~{config.TASK_POLL_INTERVAL_SEC} с.'
    )


@router.message(Command("cursor"))
async def cmd_cursor(message: Message):
    if message.from_user.id not in admins:
        await message.reply('Команда /cursor только для админов.')
        return

    text = (message.text or '').strip()
    if text.lower().startswith('/cursor'):
        text = text[7:].strip()

    if not text or text.lower() in ('help', '?', 'помощь'):
        await message.reply(
            '<b>/cursor</b> — запускает Cursor-агента на ноутбуке (реально пишет код).\n\n'
            '<code>/cursor описание задачи</code>\n'
            '<code>/cursor new описание</code> — новая сессия\n'
            '<code>/cursor reset</code> — сбросить сессию\n\n'
            'Нужен <code>CURSOR_API_KEY</code> в .env.\n'
            '/task — только очередь в Supabase (без автокода).'
        )
        return

    if text.lower() in ('reset', 'clear', 'сброс'):
        cursor_runner.clear_session(message.from_user.id)
        await message.reply('🆕 Сессия Cursor сброшена.')
        return

    if text.lower() == 'new':
        cursor_runner.clear_session(message.from_user.id)
        await message.reply('🆕 Новая сессия. Теперь: <code>/cursor ваша задача</code>')
        return

    new_session = False
    lower = text.lower()
    if lower.startswith('new '):
        new_session = True
        text = text[4:].strip()

    if not text:
        await message.reply('Напиши задачу: <code>/cursor что сделать</code>')
        return

    if not cursor_runner.is_enabled():
        await message.reply(cursor_runner.setup_hint())
        return

    user_id = message.from_user.id
    if user_id in _cursor_busy:
        await message.reply('⏳ Предыдущая задача Cursor ещё выполняется. Подождите.')
        return

    session_note = ''
    if not new_session and cursor_runner.get_agent_id(user_id):
        session_note = '\n↪ Продолжаю предыдущую сессию (/cursor new — новая).'

    _cursor_busy.add(user_id)
    await message.reply(
        f'🤖 Запускаю Cursor на этой машине...{session_note}\n'
        'Может занять несколько минут.'
    )

    try:
        result = await asyncio.to_thread(
            cursor_runner.run,
            user_id,
            text,
            new_session=new_session,
        )
    except Exception as exc:
        logger.error(f'/cursor error: {exc}')
        await message.reply(f'❌ Ошибка: {exc}')
        return
    finally:
        _cursor_busy.discard(user_id)

    if not result.text and result.error:
        await message.reply(f'❌ {result.error}')
        return

    icon = '✅' if result.ok else '⚠️'
    header = (
        f'{icon} Cursor\n'
        f'agent: <code>{result.agent_id or "—"}</code>\n'
        f'run: <code>{result.run_id or "—"}</code>\n'
        f'status: {result.status or "—"}\n\n'
    )
    body = result.text or result.error or '(пустой ответ)'
    await send_plain_text(message, header + body)


@router.message(Command("retro"))
async def cmd_retro(message: Message):
    if message.from_user.id not in get_allowed_users():
        return
    parts = (message.text or '').split()
    days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else config.DEFAULT_RETRO_DAYS
    await message.reply(f'📊 Анализирую работу за {days} дн...')
    reply = await asyncio.to_thread(
        voice_agent.analyze_period,
        message.from_user.id,
        days,
        message.text or '',
        message.from_user.first_name or '',
    )
    await send_plain_text(message, reply)


async def send_plain_text(message: Message, text: str):
    if len(text) <= MAX_TEXT_LENGTH:
        await message.answer(text)
        return
    for i in range(0, len(text), MAX_TEXT_LENGTH):
        await message.answer(text[i:i + MAX_TEXT_LENGTH])


@router.callback_query(F.data.startswith('search_more:'))
async def on_search_more(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in get_allowed_users():
        await callback.answer('Нет доступа')
        return

    session_id = callback.data.split(':', 1)[1]
    page, has_more = voice_agent.get_search_page(session_id, user_id)
    if not page:
        await callback.answer('Больше нет результатов', show_alert=True)
        return

    cards = '\n\n—\n\n'.join(format_listing_card(item) for item in page)
    keyboard = None
    if has_more:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='Показать ещё 10', callback_data=f'search_more:{session_id}')
        ]])
    await callback.message.answer(cards, reply_markup=keyboard)
    await callback.answer()


@router.message(F.content_type == ContentType.TEXT)
async def handle_text_message(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state == AdminStates.add_user_id:
        await process_add_user(message, state)
    elif current_state == AdminStates.delete_user_id:
        await process_delete_user(message, state)
    elif message.text and message.text.startswith('http'):
        await handle_url_message(message)
    else:
        await process_agent_query(message, message.text or '')


async def process_agent_query(message: Message, text: str):
    user_id = message.from_user.id
    if user_id not in get_allowed_users():
        await message.reply(f"Пользователь: {user_id}")
        return

    text = text.strip()
    if not text:
        await message.reply('Отправьте текстовый запрос. /help — подсказки.')
        return

    try:
        if voice_agent.is_feedback(text):
            await asyncio.to_thread(voice_agent.record_feedback, user_id, text)
            await asyncio.to_thread(
                voice_agent.record_activity, user_id, 'feedback', feedback=text[:500],
            )
            reply = await asyncio.to_thread(
                voice_agent.chat,
                user_id,
                f'Пользователь дал обратную связь: «{text}». Запомни урок и предложи как улучшить работу.',
                message.from_user.first_name or '',
            )
            await send_plain_text(message, reply)
            return

        parsed = await asyncio.to_thread(voice_agent.parse_query, text)
        intent = parsed.get('intent', 'search')

        if intent == 'retrospective':
            days = int(parsed.get('days') or config.DEFAULT_RETRO_DAYS)
            await message.reply(f'📊 Анализирую работу за {days} дн...')
            reply = await asyncio.to_thread(
                voice_agent.analyze_period,
                user_id,
                days,
                text,
                message.from_user.first_name or '',
            )
            await send_plain_text(message, reply)
            return

        if intent == 'chat':
            reply = await asyncio.to_thread(
                voice_agent.chat,
                user_id,
                text,
                message.from_user.first_name or '',
            )
            await asyncio.to_thread(
                voice_agent.record_activity, user_id, 'chat',
                query=text[:500], reply_preview=reply[:200],
            )
            await send_plain_text(message, reply)
            return

        if intent == 'get_field':
            object_id = (parsed.get('object_id') or '').strip().upper()
            field = parsed.get('field') or 'Исходное описание'
            if not object_id:
                await message.reply('Укажите ID объекта, например PHK-0002.')
                return
            value = await asyncio.to_thread(voice_agent.get_listing_field, object_id, field)
            if not value:
                await asyncio.to_thread(
                    voice_agent.record_activity, user_id, 'get_field_fail',
                    object_id=object_id, field=field, query=text[:500],
                )
                await message.reply(f'Объект {object_id} не найден или поле пустое.')
                return
            await asyncio.to_thread(
                voice_agent.record_activity, user_id, 'get_field_ok',
                object_id=object_id, field=field, query=text[:500],
            )
            header = f'<b>{object_id}</b> — {field}\n\n'
            if len(value) > MAX_TEXT_LENGTH:
                await message.answer(header + value[:MAX_TEXT_LENGTH])
                for i in range(MAX_TEXT_LENGTH, len(value), MAX_TEXT_LENGTH):
                    await message.answer(value[i:i + MAX_TEXT_LENGTH])
            else:
                await message.answer(header + value)
            return

        listings = await asyncio.to_thread(voice_agent.search_listings, parsed.get('filters', {}))
        if not listings:
            await asyncio.to_thread(
                voice_agent.record_failed_search,
                user_id,
                text,
                parsed.get('filters', {}),
            )
            await asyncio.to_thread(
                voice_agent.record_activity, user_id, 'search_fail',
                query=text[:500], filters=parsed.get('filters', {}),
            )
            reply = await asyncio.to_thread(
                voice_agent.chat,
                user_id,
                f'Поиск ничего не нашёл по запросу: «{text}». Объясни почему могло не сработать и предложи как переформулировать.',
                message.from_user.first_name or '',
            )
            await send_plain_text(message, reply)
            return

        await asyncio.to_thread(
            voice_agent.record_activity, user_id, 'search_ok',
            query=text[:500], filters=parsed.get('filters', {}),
            result_count=len(listings),
            top_ids=[item.get('ID', '') for item in listings[:5]],
        )
        session_id, page, has_more = voice_agent.start_search_session(user_id, listings, text)
        header = format_search_header(text, len(listings), len(page))
        cards = '\n\n—\n\n'.join(format_listing_card(item) for item in page)
        keyboard = None
        if has_more:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text='Показать ещё 10', callback_data=f'search_more:{session_id}')
            ]])
        await message.answer(f'{header}\n\n{cards}', reply_markup=keyboard)

    except Exception as e:
        logger.error(f'Ошибка запроса к базе: {e}')
        await message.reply(f'Ошибка: {e}')

# FB-парсер использует один браузерный профиль — параллельные запуски его ломают
_FB_PARSE_LOCK = asyncio.Lock()


async def handle_fb_url_message(message: Message, fb_url: str):
    """FB Marketplace: парсер → сессия → Агент 2 (Notion)."""
    await message.reply("Ссылка Facebook Marketplace. Парсю и передаю Агенту 2...")

    async with _FB_PARSE_LOCK:
        result = await asyncio.to_thread(handoff_fb_to_agent2, fb_url)

    if result.ok:
        await message.answer(
            f"✅ {result.object_id or result.session_id} добавлен в CRM\n"
            f"📷 Фото: {result.photos}\n{result.note}"
        )
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
        object_id = result.object_id

        logger.debug(f'Найдено {len(local_image_paths)} изображений.')
        if text:
            try:
                if result.sheet_written:
                    await asyncio.to_thread(
                        voice_agent.record_activity,
                        user_id,
                        'airbnb_parse',
                        url=url[:300],
                        object_id=object_id or '',
                        success=bool(object_id),
                    )

                logger.info('Обработка URL завершена.')
                if result.object_id:
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
                    await message.answer(f'✅ {result.object_id} добавлен в CRM{timing_line}')
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
        BotCommand(command='help', description='Jarvis — что умеет'),
        BotCommand(command='retro', description='Анализ за период'),
    ]
    await bot.set_my_commands(user_commands, BotCommandScopeDefault())

    # Set extended commands for admins
    admin_commands = [
        BotCommand(command='start', description='Старт'),
        BotCommand(command='help', description='Jarvis — что умеет'),
        BotCommand(command='task', description='Задача → Claude → Supabase'),
        BotCommand(command='cursor', description='Cursor SDK — код на ноутбуке'),
        BotCommand(command='retro', description='Анализ за период'),
        BotCommand(command='admin', description='Управление'),
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
    if cursor_runner.is_enabled():
        queue_note += '\n🤖 /cursor — Cursor SDK (код на ноутбуке)'

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
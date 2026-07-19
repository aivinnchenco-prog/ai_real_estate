import os


def _load_dotenv():
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding='utf-8') as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            # .env — источник правды (перезаписывает пустые переменные из shell)
            os.environ[key.strip()] = value.strip().strip('"\'')


_load_dotenv()

TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN', '')
# Telegram ID админов бота, через запятую (ADMIN_TG_IDS=111,222)
ADMIN_IDS = [
    int(x) for x in os.getenv('ADMIN_TG_IDS', '5041767749').replace(' ', '').split(',') if x
]

GOOGLE_CREDENTIALS_FILE = os.getenv(
    'GOOGLE_CREDENTIALS_FILE',
    'credentials/google-service-account.json',
)
GOOGLE_SPREADSHEET_ID = os.getenv(
    'GOOGLE_SPREADSHEET_ID',
    '13PNSwuvhCm-74k5aBU6gVlMMxX4fopEPkreSh28NnRs',
)
# Бот пишет в основной лист CRM.
GOOGLE_SHEET_NAME = os.getenv('GOOGLE_SHEET_NAME', 'CRM_Объекты')

DEAL_TYPE = os.getenv('DEAL_TYPE', 'long_term_lease')

OBJECT_ID_PREFIX = os.getenv('OBJECT_ID_PREFIX', 'PHK')
SOURCE_AIRBNB = 'airbnb'
STATUS_FOUND = os.getenv('STATUS_FOUND', 'новое')

# ID корневой папки на Google Drive (из URL: drive.google.com/drive/folders/ВОТ_ЭТОТ_ID)
DRIVE_ROOT_FOLDER_ID = os.getenv('DRIVE_ROOT_FOLDER_ID', '1fhB0OuFnb0usia1K2vP9zyDTjP1WU-k_')

# Фото с Airbnb → папка PHK-XXXX на Google Drive.
# Загрузка файлов через OAuth (ваш Gmail), не service account.
ENABLE_DRIVE_UPLOAD = os.getenv('ENABLE_DRIVE_UPLOAD', 'true').lower() in ('1', 'true', 'yes')
DRIVE_OAUTH_CLIENT_FILE = os.getenv(
    'DRIVE_OAUTH_CLIENT_FILE',
    'credentials/oauth-client.json',
)
DRIVE_OAUTH_TOKEN_FILE = os.getenv(
    'DRIVE_OAUTH_TOKEN_FILE',
    'credentials/drive-oauth-token.json',
)

# Claude — мозги агента Jarvis
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
ANTHROPIC_MODEL = os.getenv('ANTHROPIC_MODEL', 'claude-haiku-4-5')
ANTHROPIC_CHAT_MODEL = os.getenv('ANTHROPIC_CHAT_MODEL', ANTHROPIC_MODEL)

AGENT_NAME = os.getenv('AGENT_NAME', 'Jarvis')
CHAT_HISTORY_LIMIT = int(os.getenv('CHAT_HISTORY_LIMIT', '20'))
LEARNINGS_FILE = os.getenv('LEARNINGS_FILE', 'data/agent_learnings.json')
ACTIVITY_LOG_FILE = os.getenv('ACTIVITY_LOG_FILE', 'data/activity_log.jsonl')
DEFAULT_RETRO_DAYS = int(os.getenv('DEFAULT_RETRO_DAYS', '7'))

SEARCH_PAGE_SIZE = int(os.getenv('SEARCH_PAGE_SIZE', '10'))

# Валюта для парсинга цен с Airbnb (THB или USD). Добавляется в URL как &currency=...
AIRBNB_CURRENCY = os.getenv('AIRBNB_CURRENCY', 'THB').upper()

# --- Скорость парсинга и загрузки ---
PARSER_REUSE_BROWSER = os.getenv('PARSER_REUSE_BROWSER', 'true').lower() in ('1', 'true', 'yes')
# Пауза после открытия страницы (+ джиттер) — снижает rate-limit Airbnb на VPS
PARSER_PAGE_SLEEP_SEC = float(os.getenv('PARSER_PAGE_SLEEP_SEC', '4'))
PARSER_PAGE_SLEEP_JITTER_SEC = float(os.getenv('PARSER_PAGE_SLEEP_JITTER_SEC', '1.5'))
PARSER_PRICE_WAIT_SEC = float(os.getenv('PARSER_PRICE_WAIT_SEC', '10'))
PARSER_WAIT_TIMEOUT = int(os.getenv('PARSER_WAIT_TIMEOUT', '35'))
# 0 = скачивать все фото листинга (без лимита)
MAX_PHOTOS_DOWNLOAD = int(os.getenv('MAX_PHOTOS_DOWNLOAD', '0'))
PHOTO_DOWNLOAD_WORKERS = int(os.getenv('PHOTO_DOWNLOAD_WORKERS', '3'))
PHOTO_DOWNLOAD_RETRIES = int(os.getenv('PHOTO_DOWNLOAD_RETRIES', '2'))
PHOTO_DOWNLOAD_RETRY_DELAY_SEC = float(os.getenv('PHOTO_DOWNLOAD_RETRY_DELAY_SEC', '0.8'))
PARSER_PHOTO_WAIT_SEC = float(os.getenv('PARSER_PHOTO_WAIT_SEC', '18'))
PARSER_PHOTO_POLL_SEC = float(os.getenv('PARSER_PHOTO_POLL_SEC', '1.5'))
DRIVE_UPLOAD_WORKERS = int(os.getenv('DRIVE_UPLOAD_WORKERS', '3'))
ENRICH_IN_BACKGROUND = os.getenv('ENRICH_IN_BACKGROUND', 'true').lower() in ('1', 'true', 'yes')
PARSER_SAVE_DEBUG_JSON = os.getenv('PARSER_SAVE_DEBUG_JSON', 'false').lower() in ('1', 'true', 'yes')

# Claude подключается, если парсер не уверен в цене/валюте
ENABLE_AI_PRICE_FALLBACK = os.getenv('ENABLE_AI_PRICE_FALLBACK', 'true').lower() in ('1', 'true', 'yes')

# --- Передача Агенту 2 (Notion CRM вместо Google Sheets/Drive) ---
AGENT2_HANDOFF_ENABLED = os.getenv('AGENT2_HANDOFF_ENABLED', 'true').lower() in ('1', 'true', 'yes')
# Корень assistant-media Агента 2; пусто = авто-поиск в монорепе
AGENT2_ROOT = os.getenv('AGENT2_ROOT', '')
# Сразу запускать agent2_structurize.py после сборки сессии
AGENT2_AUTORUN = os.getenv('AGENT2_AUTORUN', 'true').lower() in ('1', 'true', 'yes')

# false = на этом хосте цены не собираем (гибрид: VPS парсит объект, Mac добирает цены)
PRICE_COLLECT_ENABLED = os.getenv('PRICE_COLLECT_ENABLED', 'true').lower() in ('1', 'true', 'yes')
# Параллельных браузеров для локального сбора цен (1 = как раньше)
PRICE_PARALLEL_WORKERS = int(os.getenv('PRICE_PARALLEL_WORKERS', '3'))
# Сколько месяцев вперёд собирать цены (None = взять из pipeline.json Агента 2, дефолт 12)
_pma = os.getenv('PRICE_MONTHS_AHEAD', '').strip()
PRICE_MONTHS_AHEAD = int(_pma) if _pma else None
# Минимальный непрерывный доступный отрезок (дней) для экстраполяции цены месяца
PRICE_MIN_SEGMENT_DAYS = int(os.getenv('PRICE_MIN_SEGMENT_DAYS', '5'))
# Повторы одного периода, если Airbnb не отдал цену (антибот/rate-limit)
PRICE_FETCH_RETRIES = int(os.getenv('PRICE_FETCH_RETRIES', '3'))
PRICE_RETRY_SLEEP_SEC = float(os.getenv('PRICE_RETRY_SLEEP_SEC', '6'))
# Сколько раз перезапускать браузер при Access Denied (новый профиль обходит блок сессии)
PRICE_BLOCK_RESTARTS = int(os.getenv('PRICE_BLOCK_RESTARTS', '2'))
# Быстрый режим цен: короткая пауза после загрузки и частый поллинг цены
PRICE_PAGE_SLEEP_SEC = float(os.getenv('PRICE_PAGE_SLEEP_SEC', '0.5'))
PRICE_WAIT_POLL_SEC = float(os.getenv('PRICE_WAIT_POLL_SEC', '0.25'))
# Сначала ближайшие N месяцев, пауза, потом остальные; затем refill пустых
PRICE_PRIORITY_MONTHS = int(os.getenv('PRICE_PRIORITY_MONTHS', '6'))
PRICE_BATCH_PAUSE_SEC = float(os.getenv('PRICE_BATCH_PAUSE_SEC', '8'))
PRICE_REFILL_PASS = os.getenv('PRICE_REFILL_PASS', 'true').lower() in ('1', 'true', 'yes')
# Кэш удачных цен по listing_id (не дергать Airbnb повторно без нужды)
PRICE_CACHE_ENABLED = os.getenv('PRICE_CACHE_ENABLED', 'true').lower() in ('1', 'true', 'yes')
PRICE_CACHE_TTL_DAYS = int(os.getenv('PRICE_CACHE_TTL_DAYS', '7'))
PRICE_CACHE_DIR = os.getenv('PRICE_CACHE_DIR', 'data/monthly_price_cache')
# Таймаут открытия страницы браузером; при зависании Chrome перезапускается
PARSER_OPEN_TIMEOUT_SEC = int(os.getenv('PARSER_OPEN_TIMEOUT_SEC', '90'))

# Распознавание владельца/агентства и веб-поиск контактов
OWNER_DETECT_ENABLED = os.getenv('OWNER_DETECT_ENABLED', 'true').lower() in ('1', 'true', 'yes')
OWNER_WEB_SEARCH = os.getenv('OWNER_WEB_SEARCH', 'true').lower() in ('1', 'true', 'yes')

# --- FB Marketplace в этом же боте (fb_handoff) ---
# Корень проекта fb_parser; пусто = авто-поиск в монорепе
FB_PARSER_ROOT = os.getenv('FB_PARSER_ROOT', '')
FB_PARSER_BACKEND = os.getenv('FB_PARSER_BACKEND', 'crawl4ai')

# Очередь задач Claude → Cursor
TASK_QUEUE_DB = os.getenv('TASK_QUEUE_DB', 'data/agent_tasks.db')
TASK_INSTRUCTIONS_DIR = os.getenv('TASK_INSTRUCTIONS_DIR', 'data/task_instructions')
SUPABASE_URL = os.getenv('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY = os.getenv(
    'SUPABASE_KEY',
    os.getenv('SUPABASE_ANON_KEY', os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')),
)
ENABLE_TASK_QUEUE_POLLER = os.getenv('ENABLE_TASK_QUEUE_POLLER', 'true').lower() in ('1', 'true', 'yes')
TASK_POLL_INTERVAL_SEC = int(os.getenv('TASK_POLL_INTERVAL_SEC', '60'))

# Cursor SDK — /cursor в Telegram (локальный агент на этой машине)
CURSOR_API_KEY = os.getenv('CURSOR_API_KEY', '')
CURSOR_MODEL = os.getenv('CURSOR_MODEL', 'composer-2.5')
CURSOR_PROJECT_DIR = os.getenv(
    'CURSOR_PROJECT_DIR',
    os.path.dirname(os.path.abspath(__file__)),
)

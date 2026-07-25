"""Единый модуль констант имён колонок Notion, которые использует Publisher.

Точные имена колонок живой таблицы (контракт: schema/notion_schema.json в корне репо).
Все скрипты Publisher берут имена колонок отсюда (или из config/publisher.json),
поэтому будущее переименование колонок (например agent6_* -> publisher_*)
станет правкой этого файла и конфига, без поиска строк по коду.
"""

# --- Базовые поля объекта (пишет Агент_2 Registrar) ---
TITLE = "Название объекта"
OBJECT_ID = "Объект ID"
STATUS = "Статус"
PHOTO = "Фото"
ADDRESS = "Адрес"
DISTRICT = "Район"
HOUSING_TYPE = "Тип жилья"
RENT_TYPE = "Тип аренды"
ROOMS = "Количество комнат"
PRICE_MONTHLY = "Цена за месяц"
PRICE_YEARLY = "Цена за год"
AMENITIES = "Удобства"
GOOGLE_MAPS = "Google Maps"
SOURCE_URL = "Источник объявления"
CALENDAR = "Календарь"

# --- Описания для каналов публикации ---
DESCRIPTION = "Описание"
DESCRIPTION_FB_MARKETPLACE = "Описание для FB Marketplace"
DESCRIPTION_TELEGRAM = "Описание для Telegram"
DESCRIPTION_SOCIAL = "Описание соц.сети"
CTA_INSTAGRAM = "CTA Instagram"

# --- Видео (пишет Агент_3 Director) ---
VIDEO_URL_VERTICAL = "video_url_vertical"
VIDEO_URL_SEEDANCE = "video_url_Seedance"

# --- Ссылки на опубликованные посты ---
POST_URL_FB_MARKETPLACE = "post_url_FB_marketplace"
POST_URL_TELEGRAM = "post_url_telegram"
POST_URL_FACEBOOK = "post_url_facebook"
POST_URL_LINKEDIN = "post_url_linkedin"
POST_URL_THREADS = "post_url_threads"
POST_URL_TIKTOK = "post_url_tiktok"
POST_URL_X = "post_url_x"
POST_URL_YOUTUBE = "post_url_youtube"
POST_URL_INSTAGRAM_CAROUSEL = "post_url_instagram_carousel"
POST_URL_INSTAGRAM_REEL = "post_url_instagram_reel"

# --- Metricool ---
METRICOOL_POST_GROUP_ID = "metricool_post_group_id"
METRICOOL_POST_ID = "metricool_post_id"
# Дата и время выхода поста в соцсети (календарный вид Notion)
PUBLISH_AT = "Дата и время публикации"

# --- Служебные поля Publisher (историческое имя agent6_*, будущее — publisher_*) ---
AGENT6_CAROUSEL_DONE = "agent6_carousel_done"
AGENT6_VIDEO_DONE = "agent6_video_done"
AGENT6_LOCKED = "agent6_locked"
AGENT6_LOG = "agent6_log"
AGENT6_MODE = "agent6_mode"
AGENT6_TAKEN_AT = "agent6_taken_at"

# --- ChatPlace-воронки (Агент_5 Usher) ---
CHATPLACE_FUNNEL_DONE = "chatplace_funnel_done"
CHATPLACE_FUNNEL_ID = "chatplace_funnel_id"
CHATPLACE_FUNNEL_CAROUSEL_DONE = "chatplace_funnel_carousel_done"
CHATPLACE_FUNNEL_CAROUSEL_ID = "chatplace_funnel_carousel_id"
CHATPLACE_FUNNEL_REEL_DONE = "chatplace_funnel_reel_done"
CHATPLACE_FUNNEL_REEL_ID = "chatplace_funnel_reel_id"

# --- Ошибки (planned-колонка, пока может отсутствовать в таблице) ---
LAST_ERROR = "last_error"

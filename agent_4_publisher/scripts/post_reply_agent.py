"""Агент «Мой пост» — шаблоны CTA и промпт для ответов на комментарии/DM."""

from __future__ import annotations

from typing import Any


def reply_agent_config(config: dict[str, Any]) -> dict[str, Any]:
    return (config.get("postmypost") or {}).get("reply_agent") or {}


def reply_agent_enabled(config: dict[str, Any]) -> bool:
    return bool(reply_agent_config(config).get("enabled", True))


def agent_context(config: dict[str, Any], *, object_id: str = "") -> dict[str, str]:
    ra = reply_agent_config(config)
    tg = config.get("telegram") or {}
    whatsapp = str(ra.get("manager_whatsapp") or "").strip()
    wa_suffix = f" или WhatsApp {whatsapp}" if whatsapp else ""
    return {
        "object_id": object_id or "",
        "object_code": object_id or "",
        "telegram_channel": str(ra.get("manager_telegram") or tg.get("channel") or "@OpenHome_th"),
        "whatsapp": whatsapp,
        "whatsapp_suffix": wa_suffix,
        "brand": str(ra.get("brand_name") or "Open Home"),
    }


def format_agent_template(template: str, ctx: dict[str, str]) -> str:
    out = template
    for key, value in ctx.items():
        out = out.replace("{" + key + "}", value or "")
    return out.strip()


def object_code_line(object_id: str, config: dict[str, Any]) -> str:
    if not object_id or not reply_agent_enabled(config):
        return ""
    ra = reply_agent_config(config)
    tpl = str(
        ra.get("object_code_line_template") or "🏷 Код объекта: {object_id}"
    )
    return format_agent_template(tpl, agent_context(config, object_id=object_id))


def resolve_reply_text(
    template_key: str,
    platform: str,
    config: dict[str, Any],
    *,
    object_id: str = "",
    fallback: str = "",
) -> str:
    ra = reply_agent_config(config)
    ctx = agent_context(config, object_id=object_id)
    by_platform = ra.get("by_platform") or {}
    network = "x" if platform in {"x", "twitter"} else platform
    tpl = str(
        by_platform.get(platform)
        or by_platform.get(network)
        or ra.get(template_key)
        or fallback
    )
    return format_agent_template(tpl, ctx) if tpl else ""


def build_postmypost_ai_system_prompt(config: dict[str, Any]) -> str:
    """Промпт для вставки в PostMyPost Automation → AI assistant (один раз в UI)."""
    ra = reply_agent_config(config)
    custom = str(ra.get("postmypost_ai_system_prompt") or "").strip()
    if custom:
        return format_agent_template(custom, agent_context(config))

    ctx = agent_context(config)
    tg = ctx["telegram_channel"]
    wa = ctx["whatsapp"]
    wa_line = f" или WhatsApp {wa}" if wa else ""
    brand = ctx["brand"]

    return f"""Ты — ассистент «Мой пост» бренда {brand} (аренда недвижимости на Пхукете).

Твоя задача: отвечать на комментарии под постами и на сообщения в Direct во всех подключённых соцсетях.

Алгоритм:
1. Определи код объекта из подписи поста, на который отреагировал пользователь:
   - формат YYYYMMDD_NNN (например 20260701_001)
   - или с префиксом источника: A_20260713_003
   - или хештег вида #20260701001 (без подчёркивания)
   - строка «Код объекта: …» в подписи
2. Если код найден — назови его явно в ответе.
3. Если код не найден — вежливо попроси прислать ссылку на пост или номер объекта.
4. Направь клиента к менеджеру: «Отправьте этот код менеджеру в Telegram {tg}{wa_line}».
5. Скажи, что менеджер поможет с подбором, уточнением деталей и оформлением.

Правила:
- Отвечай на русском, 2–4 коротких предложения.
- Не называй цену и не обещай доступность — только направление к менеджеру с кодом объекта.
- Тон: дружелюбный, профессиональный, без давления.
- Не спорь и не обсуждай конфиденциальные темы.

Пример ответа (если код 20260701_001):
«Спасибо за интерес! Код этого объекта: 20260701_001. Отправьте его менеджеру в Telegram {tg}{wa_line} — поможем с подбором и оформлением.»"""


def static_comment_reply(object_id: str, config: dict[str, Any]) -> str:
    """Фиксированный ответ (если в PostMyPost automation без AI)."""
    return resolve_reply_text(
        "comment_reply_template",
        "instagram",
        config,
        object_id=object_id,
        fallback=(
            "Спасибо за интерес! Код объекта: {object_id}. "
            "Отправьте его менеджеру в Telegram {telegram_channel} — "
            "поможем с подбором и оформлением."
        ),
    )

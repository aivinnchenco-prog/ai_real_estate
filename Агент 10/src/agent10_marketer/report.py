"""Human-readable recommendation / history report."""

from __future__ import annotations

from agent10_marketer.models import ContentFormat, Platform, PublicationRecord, Recommendation


def _fmt_date(rec) -> str:
    pub = rec.publication if hasattr(rec, "publication") else rec
    published = getattr(pub, "published_at", None)
    if published is None:
        return "дата неизвестна"
    return published.strftime("%d.%m.%Y")


def _pub_lines(pub: PublicationRecord, index: int) -> list[str]:
    lines = [f"{index}."]
    lines.append(f"PostMyPost ID: {pub.postmypost_post_id or '—'}")
    lines.append(f"Publication key: {pub.publication_id}")
    lines.append(f"Published: {_fmt_date(pub)}")
    lines.append(f"Permalink: {pub.permalink or pub.instagram_permalink or '—'}")
    ext = pub.instagram_media_id or pub.facebook_post_id
    lines.append(f"External ID: {ext or '—'}")
    lines.append(f"Status: {pub.status or '—'}")
    lines.append(f"Format: {pub.format.value}")
    return lines


def format_history_cli(rec: Recommendation) -> str:
    lines: list[str] = []
    lines.append(f"Object: {rec.object_id}")
    lines.append("")
    lines.append(f"Instagram Reels: {rec.reels_found}")
    lines.append("")

    reels = [
        p
        for p in rec.history
        if p.platform == Platform.INSTAGRAM and p.format == ContentFormat.REEL
    ]
    for i, pub in enumerate(reels, start=1):
        lines.extend(_pub_lines(pub, i))
        lines.append("")

    fb = [p for p in rec.history if p.platform == Platform.FACEBOOK]
    lines.append(f"Facebook creatives: {len(fb)}")
    lines.append("")
    for i, pub in enumerate(fb, start=1):
        lines.extend(_pub_lines(pub, i))
        lines.append("")

    lines.append("Analytics:")
    lines.append(rec.analytics_status)
    lines.append("")
    lines.append("Ranking:")
    if rec.ranking_status == "BLOCKED":
        lines.append("BLOCKED — PostMyPost analytics contract is not connected.")
    else:
        lines.append(rec.ranking_status)
    lines.append("")
    lines.append("Meta launch:")
    lines.append("DISABLED")
    return "\n".join(lines)


def format_report(rec: Recommendation, *, language: str = "ru") -> str:
    if language != "ru":
        language = "ru"

    if rec.analytics_status == "UNAVAILABLE" or rec.ranking_status == "BLOCKED":
        lines = [
            f"Объект {rec.object_id}",
            "",
            f"Найдено Instagram Reels: {rec.reels_found}",
            f"Facebook creatives: {rec.facebook_creatives_found}",
            "",
            "Publication history: READY" if rec.history else "Publication history: EMPTY",
            "Analytics: UNAVAILABLE",
            "Organic ranking: BLOCKED — PostMyPost analytics contract is not connected.",
            "",
            "Meta launch: DISABLED",
        ]
        return "\n".join(lines)

    lines: list[str] = []
    lines.append(f"Объект {rec.object_id}")
    lines.append("")
    lines.append(f"Найдено {rec.reels_found} Reels.")
    lines.append("")

    if not rec.winner:
        lines.append("Лучший кандидат: не определён.")
        if rec.notes:
            lines.append("Примечания:")
            for n in rec.notes:
                lines.append(f"- {n}")
        lines.append("")
        lines.append("Status: NO CANDIDATE")
        lines.append("Meta launch: DISABLED")
        return "\n".join(lines)

    w = rec.winner
    lines.append("Лучший кандидат:")
    lines.append(f"Reel от {_fmt_date(w)}")
    url = w.publication.instagram_permalink or w.publication.permalink
    if url:
        lines.append(f"URL: {url}")
    lines.append("")
    lines.append(f"Organic Score: {w.score.organic_score:.1f}/100")
    lines.append("")
    lines.append("Почему:")
    for reason in rec.why_selected:
        lines.append(f"- {reason}")
    lines.append("")
    lines.append("Рекомендация:")
    lines.append("использовать этот Reel для paid promotion via Meta Ads.")
    lines.append("")

    if rec.campaign:
        c = rec.campaign
        lines.append("Предлагаемый бюджет:")
        lines.append(f"{c.daily_budget:g} {c.currency}/day")
        lines.append(f"{c.duration_days} days")
        lines.append(f"max {c.max_total_budget:g} {c.currency}")
        lines.append("")
        lines.append(f"Objective: {c.objective}")
        lines.append(f"Strategy: {c.strategy}")
        lines.append(f"Placements: {c.placements_mode}")
        lines.append(f"Audience: {c.audience_mode}")
        lines.append("")

    lines.append(f"Data quality: {rec.data_quality}")
    lines.append(f"Status: WAITING FOR APPROVAL ({rec.status.value})")
    lines.append("Meta launch: DISABLED")
    return "\n".join(lines)


def format_cli(rec: Recommendation) -> str:
    if rec.analytics_status == "UNAVAILABLE" or (
        rec.ranking_status == "BLOCKED" and not rec.ranked
    ):
        return format_history_cli(rec)

    lines: list[str] = []
    lines.append(f"Object: {rec.object_id}")
    lines.append(f"Reels found: {rec.reels_found}")
    lines.append("")

    for item in rec.ranked:
        pub = item.publication
        label = pub.instagram_permalink or pub.permalink or pub.publication_id
        date = _fmt_date(item)
        lines.append(f"{item.rank}. Reel {date}")
        lines.append(f"   id: {pub.publication_id}")
        lines.append(f"   url: {label}")
        lines.append(f"   Organic Score: {item.score.organic_score:.1f}")
        lines.append("")

    if rec.winner:
        lines.append("Recommended:")
        lines.append(f"Reel {_fmt_date(rec.winner)}")
        lines.append(f"id: {rec.winner.publication.publication_id}")
        lines.append("Reason:")
        for reason in rec.why_selected:
            lines.append(f"- {reason}")
        lines.append("")
    else:
        lines.append("Recommended: none")
        lines.append("")

    if rec.campaign:
        c = rec.campaign
        lines.append("Suggested Ads:")
        lines.append(f"Objective: {c.objective}")
        lines.append(f"Budget/day: {c.daily_budget:g} {c.currency}")
        lines.append(f"Duration: {c.duration_days} days")
        lines.append(f"Maximum spend: {c.max_total_budget:g} {c.currency}")
        lines.append("")

    lines.append("Meta launch:")
    lines.append("DISABLED")
    return "\n".join(lines)

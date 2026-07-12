---
name: publish-metricool
description: Schedules deferred social media posts via Metricool API with video/carousel from URL. Use when posting to Instagram, TikTok, Threads, YouTube, Facebook, X, or LinkedIn through Metricool.
metadata: {"openclaw": {"requires": {"bins": ["curl", "python3"], "env": ["METRICOOL_USER_TOKEN", "METRICOOL_USER_ID", "METRICOOL_BLOG_ID"]}}}
---

# Publish via Metricool

Dashboard: https://app.metricool.com/  
API docs: https://app.metricool.com/resources/apidocs/index.html

Requires **Advanced plan or higher** for API access.

## Auth

Every request needs:
- Header: `X-Mc-Auth: {userToken}`
- Query: `userId` and `blogId` (from brand dashboard URL)

```bash
export MC_TOKEN="${METRICOOL_USER_TOKEN}"
export MC_USER="${METRICOOL_USER_ID}"
export MC_BLOG="${METRICOOL_BLOG_ID}"
```

## List connected profiles

```bash
python3 scripts/list_brands.py
```

## Normalize media URL (required before scheduling)

Metricool copies external media to its CDN:

```bash
curl -s -G "https://app.metricool.com/api/actions/normalize/image/url" \
  --data-urlencode "url=https://example.com/video.mp4" \
  --data-urlencode "userId=${MC_USER}" \
  --data-urlencode "blogId=${MC_BLOG}" \
  -H "X-Mc-Auth: ${MC_TOKEN}"
```

## Schedule a post

```bash
curl -s -X POST \
  "https://app.metricool.com/api/v2/scheduler/posts?userId=${MC_USER}&blogId=${MC_BLOG}" \
  -H "X-Mc-Auth: ${MC_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Caption here",
    "autoPublish": true,
    "draft": false,
    "providers": [{"network": "instagram"}],
    "publicationDate": {
      "dateTime": "2026-07-08T10:00:00",
      "timezone": "Asia/Bangkok"
    },
    "media": ["https://normalized-url-from-metricool"],
    "instagramData": {"type": "REEL", "autoPublish": true, "showReelOnFeed": true}
  }'
```

### Network names

| Platform | `providers[].network` |
|----------|----------------------|
| Instagram | `instagram` |
| TikTok | `tiktok` |
| Threads | `threads` |
| YouTube | `youtube` |
| Facebook | `facebook` |
| X (Twitter) | `twitter` |
| LinkedIn | `linkedin` |

### TikTok photo carousel — music

Metricool `tiktokData` for **image carousels** (not video):

```json
"tiktokData": {
  "privacyOption": "PUBLIC_TO_EVERYONE",
  "autoAddMusic": true,
  "photoCoverIndex": 0
}
```

**`autoAddMusic: true`** — TikTok подбирает трек автоматически (рекомендуется для фото-карусели).

**Конкретный трек** (опционально, вместо auto):

```json
"tiktokData": {
  "autoAddMusic": false,
  "music": {
    "musicId": "TRACK_ID",
    "title": "Song name",
    "author": "Artist"
  }
}
```

Список трендовых треков: `GET /v2/scheduler/catalogs/tiktok/trending-tracks`  
(может требовать TikTok Business — иначе 403; тогда только `autoAddMusic`).

Настройки в `config/publisher.json` → `metricool_platform_settings.tiktok`.

## Preferred: pipeline script

```bash
python3 scripts/publish_pipeline.py --page-id PAGE_ID --platform instagram --dry-run
python3 scripts/publish_pipeline.py --page-id PAGE_ID --platform instagram
python3 scripts/publish_pipeline.py --queue --platform tiktok
```

The script:
1. Reads Notion CRM (video, carousel, caption)
2. Normalizes media URLs via Metricool
3. Schedules post via `POST /v2/scheduler/posts`
4. Writes `metricool_post_id` back to Notion

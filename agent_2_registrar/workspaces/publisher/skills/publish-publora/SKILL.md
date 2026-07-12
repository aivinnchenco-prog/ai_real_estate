---
name: publish-publora
description: Schedules deferred social media posts via Publora API with video from URL. Use when posting to Instagram, TikTok, YouTube, or Facebook through Publora, scheduling content, or uploading video to Publora.
metadata: {"openclaw": {"requires": {"bins": ["curl", "python3"], "env": ["PUBLORA_API_KEY"]}}}
---

# Publish via Publora

Dashboard: https://app.publora.com/  
API docs: https://docs.publora.com/

## Flow (обязательный для video-платформ)

Instagram, TikTok, YouTube **требуют медиа**. Порядок:

```
1. POST /create-post          → draft (без scheduledTime)
2. POST /get-upload-url       → pre-signed S3 URL
3. PUT {uploadUrl}            → upload video bytes
4. PUT /update-post/:id       → status: scheduled + scheduledTime
```

## 1. List connections

```bash
curl -s "https://api.publora.com/api/v1/platform-connections" \
  -H "x-publora-key: ${PUBLORA_API_KEY}"
```

## 2. Create draft

```bash
curl -s -X POST "https://api.publora.com/api/v1/create-post" \
  -H "x-publora-key: ${PUBLORA_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Caption text here",
    "platforms": ["instagram-17841412345678"]
  }'
```

## 3. Upload video

Скачай video с URL Notion, затем:

```bash
# Get upload URL
curl -s -X POST "https://api.publora.com/api/v1/get-upload-url" \
  -H "x-publora-key: ${PUBLORA_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "fileName": "property.mp4",
    "contentType": "video/mp4",
    "type": "video",
    "postGroupId": "POST_GROUP_ID"
  }'

# Upload to S3
curl -X PUT "${UPLOAD_URL}" \
  -H "Content-Type: video/mp4" \
  --data-binary @video.mp4
```

## 4. Schedule

```bash
curl -s -X PUT "https://api.publora.com/api/v1/update-post/${POST_GROUP_ID}" \
  -H "x-publora-key: ${PUBLORA_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "scheduled",
    "scheduledTime": "2026-07-02T10:00:00.000Z"
  }'
```

## Platform settings

```json
{
  "platformSettings": {
    "instagram": { "videoType": "REELS" },
    "youtube": { "privacy": "public", "title": "Property title" },
    "tiktok": { "viewerSetting": "PUBLIC_TO_EVERYONE" }
  }
}
```

## Готовый скрипт

```bash
python3 workspaces/publisher/scripts/publish_pipeline.py \
  --page-id PAGE_ID \
  --platform instagram \
  --schedule "2026-07-02T10:00:00Z"
```

## Check status

```bash
curl -s "https://api.publora.com/api/v1/get-post/${POST_GROUP_ID}" \
  -H "x-publora-key: ${PUBLORA_API_KEY}"
```

Refs: [Create Post](https://docs.publora.com/endpoints/create-post), [Scheduling](https://docs.publora.com/guides/scheduling), [Media Uploads](https://docs.publora.com/guides/media-uploads)

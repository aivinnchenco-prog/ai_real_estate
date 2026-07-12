# Chain triggers — Agent 6 Publisher

```
Agent 3 Video → ready_to_post
Agent 5 Seedance → video_url_Seedance filled
► Agent 6 Publisher ◄ → post_url_* + metricool_post_id + agent6_locked
Agent 7 Qualifier → leads (planned)
```

**Trigger:** Notion `Статус` = `ready_to_post`  
**Input:** `video_url_Seedance` (or `video_url_vertical`), `Фото`, captions  
**Output:** `metricool_post_id`, `post_url_*`, `agent6_locked=true`

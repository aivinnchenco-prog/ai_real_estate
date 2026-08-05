# Chain triggers — Agent 6 Publisher

> **Legacy documentation.** Снимок handoff Publisher: здесь «Agent 6» = Publisher (целевая роль — **Agent 4**); «Agent 7 Qualifier» устарело (целевая роль — **Agent 6 Qualifier**). Актуальная карта: [`ROLE_MAP.md`](../ROLE_MAP.md).

```
Agent 3 Video → ready_to_post
Agent 5 Seedance → video_url_Seedance filled
► Agent 6 Publisher ◄ → post_url_* + metricool_post_id + agent6_locked
Agent 6 Qualifier → leads (planned; legacy label in this doc: Agent 7 Qualifier)
```

**Trigger:** Notion `Статус` = `ready_to_post`  
**Input:** `video_url_Seedance` (or `video_url_vertical`), `Фото`, captions  
**Output:** `metricool_post_id`, `post_url_*`, `agent6_locked=true`

#!/usr/bin/env python3
"""HTML-галерея фото объекта для R2 (публичный bucket не показывает listing папок)."""

from __future__ import annotations

import html


def build_gallery_html(
    *,
    title: str,
    object_id: str,
    photo_names: list[str],
) -> str:
    items = "\n".join(
        f'      <a class="item" href="{html.escape(name)}">'
        f'<img src="{html.escape(name)}" alt="{html.escape(name)}" loading="lazy"></a>'
        for name in photo_names
    )
    safe_title = html.escape(title)
    safe_id = html.escape(object_id)
    count = len(photo_names)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} — {safe_id}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: system-ui, -apple-system, sans-serif;
      background: #111;
      color: #eee;
    }}
    header {{
      padding: 1rem 1.25rem;
      border-bottom: 1px solid #333;
    }}
    h1 {{ margin: 0 0 .25rem; font-size: 1.1rem; font-weight: 600; }}
    .meta {{ color: #999; font-size: .875rem; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: .75rem;
      padding: 1rem;
    }}
    .item {{
      display: block;
      border-radius: 8px;
      overflow: hidden;
      background: #222;
    }}
    .item img {{
      display: block;
      width: 100%;
      height: 220px;
      object-fit: cover;
    }}
    .item:hover img {{ opacity: .92; }}
  </style>
</head>
<body>
  <header>
    <h1>{safe_title}</h1>
    <div class="meta">{safe_id} · {count} фото</div>
  </header>
  <main class="grid">
{items}
  </main>
</body>
</html>
"""

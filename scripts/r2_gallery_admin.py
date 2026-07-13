#!/usr/bin/env python3
"""Админ-галерея R2: ручное удаление лишних фото объекта.

Запуск:  python3 scripts/r2_gallery_admin.py            (порт 8899)
Открыть: http://127.0.0.1:8899  → выбрать объект → крестики на фото.

После каждого удаления index.html галереи в R2 пересобирается,
так что публичная ссылка из Notion сразу показывает актуальный набор.

Ключи R2 берутся из корневого .env или .env агента 2 (CLOUDFLARE_*).
Только для локального использования — наружу сервер не выставлять.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT2 = ROOT / "agent_2_registrar" / "_import" / "assistant-media"
sys.path.insert(0, str(AGENT2 / "scripts"))

from gallery_html import build_gallery_html  # noqa: E402

# ---------- env ----------

def load_env() -> dict:
    env: dict = {}
    for candidate in (ROOT / ".env", AGENT2 / ".env.real-estate"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip())
    return env


# ---------- R2 (SigV4: LIST / DELETE / PUT) ----------

class R2:
    def __init__(self, env: dict):
        self.endpoint = env["CLOUDFLARE_ENDPOINT"].rstrip("/")
        self.bucket = env["CLOUDFLARE_BUCKET"]
        self.access_key = env["CLOUDFLARE_ACCESS_KEY_ID"]
        self.secret_key = env["CLOUDFLARE_SECRET_ACCESS_KEY"]
        self.public_url = env["CLOUDFLARE_PUBLIC_BASE_URL"].rstrip("/")
        self.host = self.endpoint.replace("https://", "").split("/")[0]

    def _sign(self, method: str, canonical_uri: str, query: str, payload: bytes) -> dict:
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(payload).hexdigest()
        headers = f"host:{self.host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
        signed = "host;x-amz-content-sha256;x-amz-date"
        canonical = f"{method}\n{canonical_uri}\n{query}\n{headers}\n{signed}\n{payload_hash}"
        scope = f"{date_stamp}/auto/s3/aws4_request"
        to_sign = (
            f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n"
            + hashlib.sha256(canonical.encode()).hexdigest()
        )

        def hm(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        k = hm(hm(hm(hm(b"AWS4" + self.secret_key.encode(), date_stamp), "auto"), "s3"), "aws4_request")
        signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
        return {
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
                f"SignedHeaders={signed}, Signature={signature}"
            ),
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
        }

    def _request(self, method: str, key: str = "", query_params: dict | None = None, data: bytes = b"", content_type: str | None = None) -> bytes:
        canonical_uri = f"/{self.bucket}" + (f"/{urllib.parse.quote(key)}" if key else "")
        query = "&".join(
            f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}"
            for k, v in sorted((query_params or {}).items())
        )
        headers = self._sign(method, canonical_uri, query, data)
        if content_type:
            headers["Content-Type"] = content_type
        url = f"{self.endpoint}{canonical_uri}" + (f"?{query}" if query else "")
        req = urllib.request.Request(url, data=data or None, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    def list_keys(self, prefix: str) -> list[str]:
        keys, token = [], None
        while True:
            params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
            if token:
                params["continuation-token"] = token
            xml = self._request("GET", "", params)
            ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
            tree = ET.fromstring(xml)
            for el in tree.findall("s3:Contents/s3:Key", ns):
                keys.append(el.text)
            token_el = tree.find("s3:NextContinuationToken", ns)
            if token_el is None:
                return keys
            token = token_el.text

    def delete(self, key: str) -> None:
        self._request("DELETE", key)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._request("PUT", key, None, data, content_type)


# ---------- галерея ----------

def rebuild_gallery(r2: R2, object_id: str) -> int:
    keys = r2.list_keys(f"{object_id}/photos/")
    names = sorted(
        k.rsplit("/", 1)[-1] for k in keys
        if k.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    )
    html = build_gallery_html(title=object_id, object_id=object_id, photo_names=names)
    r2.put(f"{object_id}/photos/index.html", html.encode("utf-8"), "text/html; charset=utf-8")
    return len(names)


PAGE = """<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<title>R2 галерея — удаление фото</title>
<style>
body{{font-family:-apple-system,sans-serif;background:#111;color:#eee;margin:0;padding:20px}}
h1{{font-size:18px}} a{{color:#8cf}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px}}
.card{{position:relative;border-radius:8px;overflow:hidden;background:#222}}
.card img{{width:100%;height:180px;object-fit:cover;display:block}}
.card button{{position:absolute;top:6px;right:6px;background:#c0392b;color:#fff;border:0;
border-radius:50%;width:30px;height:30px;font-size:16px;cursor:pointer;opacity:.9}}
.card .name{{font-size:11px;color:#999;padding:4px 6px;word-break:break-all}}
.deleted{{opacity:.25;pointer-events:none}}
.topbar{{margin-bottom:14px}}
input{{background:#222;color:#eee;border:1px solid #444;border-radius:6px;padding:8px;width:320px}}
button.go{{padding:8px 14px;border-radius:6px;border:0;background:#2a6;color:#fff;cursor:pointer}}
</style></head><body>
<h1>Удаление фото из R2 {title}</h1>
<div class="topbar"><form method="get" action="/">
<input name="object" placeholder="Объект ID или ссылка на галерею" value="{object}">
<button class="go">Открыть</button></form></div>
{body}
<script>
async function del(btn, key){{
  if(!confirm('Удалить ' + key + '?')) return;
  btn.disabled = true;
  const r = await fetch('/delete', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{key}})}});
  const j = await r.json();
  if(j.ok){{ btn.closest('.card').classList.add('deleted');
    document.getElementById('count').textContent = j.remaining; }}
  else {{ alert('Ошибка: ' + j.error); btn.disabled = false; }}
}}
</script></body></html>"""


def extract_object_id(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("http"):
        path = urllib.parse.urlparse(raw).path.strip("/")
        return path.split("/")[0] if path else ""
    return raw


class Handler(BaseHTTPRequestHandler):
    r2: R2 = None  # заполняется в main

    def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/":
            self._send(404, "not found", "text/plain")
            return
        params = urllib.parse.parse_qs(parsed.query)
        object_id = extract_object_id(params.get("object", [""])[0])
        if not object_id:
            self._send(200, PAGE.format(title="", object="", body="<p>Вставьте Объект ID (например A_20260713_003) или ссылку на галерею из колонки «Фото».</p>"))
            return
        keys = [
            k for k in self.r2.list_keys(f"{object_id}/photos/")
            if k.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        ]
        if not keys:
            body = f"<p>Фото не найдены для <b>{object_id}</b>.</p>"
        else:
            cards = "\n".join(
                f'<div class="card"><img src="{self.r2.public_url}/{urllib.parse.quote(k)}" loading="lazy">'
                f'<button onclick="del(this, \'{k}\')">✕</button>'
                f'<div class="name">{k.rsplit("/", 1)[-1]}</div></div>'
                for k in sorted(keys)
            )
            body = (
                f'<p>Объект <b>{object_id}</b>, фото: <b id="count">{len(keys)}</b>. '
                f'<a href="{self.r2.public_url}/{object_id}/photos/index.html" target="_blank">публичная галерея</a></p>'
                f'<div class="grid">{cards}</div>'
            )
        self._send(200, PAGE.format(title=f"— {object_id}", object=object_id, body=body))

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/delete":
            self._send(404, "not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length))
            key = payload["key"]
            if "/photos/" not in key:
                raise ValueError("можно удалять только фото объекта")
            object_id = key.split("/")[0]
            self.r2.delete(key)
            remaining = rebuild_gallery(self.r2, object_id)
            self._send(200, json.dumps({"ok": True, "remaining": remaining}), "application/json")
        except Exception as e:  # noqa: BLE001
            self._send(200, json.dumps({"ok": False, "error": str(e)}), "application/json")

    def log_message(self, fmt, *args):  # тихий лог
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--object", default="", help="сразу открыть объект")
    args = parser.parse_args()

    Handler.r2 = R2(load_env())
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/" + (f"?object={args.object}" if args.object else "")
    print(f"Админ-галерея R2: {url}  (Ctrl+C для остановки)")
    webbrowser.open(url)
    server.serve_forever()


if __name__ == "__main__":
    main()

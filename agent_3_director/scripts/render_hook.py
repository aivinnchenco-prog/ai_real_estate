"""
render_hook.py
Подставляет значения в hook_card.html, рендерит прозрачный PNG
и накладывает его на готовое видео от Seedance через FFmpeg.

Использование:
    python3 render_hook.py \
        --video seedance_output.mp4 \
        --bedrooms 3 \
        --location-main "BANG TAO" \
        --location-sub "Phuket" \
        --price 1000 \
        --period "Month" \
        --out final_with_hook.mp4
"""

import argparse
import subprocess
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

TEMPLATE_PATH = Path(__file__).parent / "hook_card.html"


def render_card_png(title_phrase: str, bedrooms: str, price: str, period: str,
                     district: str, out_png: Path,
                     width: int = 1080, height: int = 1920) -> None:
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    html = (
        html.replace("{{TITLE_PHRASE}}", title_phrase)
            .replace("{{BEDROOMS}}", bedrooms)
            .replace("{{PRICE}}", price)
            .replace("{{PERIOD}}", period)
            .replace("{{DISTRICT}}", district)
    )

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        tmp_html = f.name

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(f"file://{tmp_html}")
        page.wait_for_load_state("load")
        page.wait_for_timeout(150)  # дать шрифтам и auto-fit скрипту отработать
        page.screenshot(path=str(out_png), omit_background=True)  # прозрачный PNG
        browser.close()


def overlay_on_video(video_in: Path, card_png: Path, video_out: Path) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_in),
        "-i", str(card_png),
        "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto",
        "-codec:a", "copy",
        str(video_out),
    ]
    subprocess.run(cmd, check=True)


def build_hook_video(video_in: str, title_phrase: str, bedrooms: str, price: str,
                      period: str, district: str, out: str) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        card_png = Path(tmpdir) / "card.png"
        render_card_png(title_phrase, bedrooms, price, period, district, card_png)
        overlay_on_video(Path(video_in), card_png, Path(out))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--title-phrase", required=True, help='e.g. "Квартира на Пхукете"')
    parser.add_argument("--bedrooms", required=True)
    parser.add_argument("--price", required=True)
    parser.add_argument("--period", default="в месяц")
    parser.add_argument("--district", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    build_hook_video(
        video_in=args.video,
        title_phrase=args.title_phrase,
        bedrooms=args.bedrooms,
        price=args.price,
        period=args.period,
        district=args.district,
        out=args.out,
    )
    print(f"Готово: {args.out}")

#!/usr/bin/env python3
"""Продолжить fb_marketplace: только описание (+ Опубликовать с --live)."""
from __future__ import annotations

import argparse
import sys

from publisher_social.android.ui import connect_device, ensure_unlocked
from publisher_social.channels.fb_marketplace import (
    _is_composer_form,
    resume_composer_from_description,
)
from publisher_social.config import load_android_config, load_publisher_config
from publisher_social.dotenv_util import load_dotenv
from publisher_social import notion_client as notion
from publisher_social.pipeline import build_job_from_page
from publisher_social.state import (
    append_log,
    clear_channel_inflight,
    load_state,
    mark_channel_done,
)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    cfg = load_publisher_config()
    android_cfg = load_android_config()
    page = notion.get_page(args.page_id)
    job = build_job_from_page(page, config=cfg, channels=["fb_marketplace"])

    state = load_state()
    clear_channel_inflight(state, job.object_id, "fb_marketplace")
    append_log(state, job.object_id, "resume from description")

    d = connect_device(android_cfg)
    d.screen_on()
    ensure_unlocked(d)
    if not _is_composer_form(d):
        print("ОШИБКА: откройте форму «Новое объявление» на телефоне", file=sys.stderr)
        return 2

    print(f"object_id={job.object_id}")
    print(f"caption_fb_len={len(job.caption_fb or '')}")
    meta, result = resume_composer_from_description(
        d,
        job,
        android_cfg,
        cfg,
        confirm_post=bool(args.live),
    )
    print(f"meta={meta}")
    print(f"result ok={result.ok} skipped={result.skipped} note={result.note!r}")

    if result.ok and args.live and not result.skipped:
        mark_channel_done(
            state,
            job.object_id,
            "fb_marketplace",
            note=str(result.note or meta),
            status="accepted",
        )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

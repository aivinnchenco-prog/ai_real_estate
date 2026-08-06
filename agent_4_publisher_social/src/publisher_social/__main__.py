from __future__ import annotations

import argparse
import json
import os
import sys

from .android.adb import check_adb
from .android.ui import connect_device, ensure_unlocked
from .config import load_android_config, load_publisher_config, validate_production_channels
from . import notion_client as notion
from .dotenv_util import load_dotenv
from .models import CHANNELS
from .pipeline import (
    build_job_from_page,
    fetch_queue_pages,
    fetch_ready_pages,
    immediate_channels_for_queue,
    prepare_job,
    record_captured_post_url,
    retry_pending_notion_updates,
    run_channels,
    run_channels_with_retry,
    run_publish_all,
    run_publish_chain,
    run_scheduled,
    summarize_job,
)
from .process_lock import PublisherLockBusy, PublisherProcessLock


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--channel",
        action="append",
        dest="channels",
        help="Канал (можно несколько раз). По умолчанию — все pending. "
        "Только fb_groups и fb_marketplace.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Только план, без телефона/UI")
    parser.add_argument(
        "--push-media",
        action="store_true",
        help="Скачать медиа и adb push на телефон",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Прогнать UI до экрана публикации (без нажатия Опубликовать)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Реальная публикация (нажимает Опубликовать). Только осознанно!",
    )


def cmd_check_device(_: argparse.Namespace) -> int:
    status = check_adb(load_android_config())
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status.get("ok") else 1


def _validate_channel_args(args: argparse.Namespace) -> int | None:
    if not args.channels:
        return None
    try:
        validate_production_channels(args.channels)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return None


def _resolve_run_flags(args: argparse.Namespace) -> tuple[bool, bool, bool]:
    """dry_run, push_media, confirm_post"""
    live = bool(getattr(args, "live", False))
    ui = bool(getattr(args, "ui", False))
    if live:
        return False, True, True
    if ui:
        return False, True, False
    if getattr(args, "push_media", False) and not getattr(args, "dry_run", False):
        return False, True, False
    # по умолчанию безопасно
    return True, False, False


def _result_flag(result) -> str:
    if result.publication_status == "submitted_unverified":
        return "WARN"
    return "OK" if result.ok else "FAIL"


def cmd_queue(args: argparse.Namespace) -> int:
    if (code := _validate_channel_args(args)) is not None:
        return code
    dry_run, push_media, confirm_post = _resolve_run_flags(args)
    exit_code = 0

    pages, cooldown_msgs = fetch_queue_pages(
        limit=args.limit,
        channels=args.channels,
        reconcile_scheduled=not dry_run,
    )

    # A single autonomous `queue --live` loop also consumes due delayed posts.
    # Fetch/reconciliation happens first so externally completed parent posts
    # can create missing due schedules and consume them in the same run.
    if not args.channels:
        scheduled_pairs = run_scheduled(
            dry_run=dry_run,
            push_media=push_media,
            confirm_post=confirm_post,
        )
        for scheduled_job, result in scheduled_pairs:
            flag = _result_flag(result)
            skip = " skip" if result.skipped else ""
            url = f" url={result.post_url}" if result.post_url else ""
            print(
                f"[{flag}{skip}] scheduled {scheduled_job.object_id} "
                f"{result.channel}: {result.reason or result.note or ''}{url}"
            )
            if not result.ok and not result.skipped:
                exit_code = 1
        if confirm_post and scheduled_pairs:
            print("Пошаговый live завершён после одного scheduled-канала. Ожидается отчёт/проверка.")
            return exit_code

    if not pages:
        if cooldown_msgs:
            print("Очередь сейчас не готова к немедленной публикации:")
            for line in cooldown_msgs:
                print(f"  · {line}")
            return exit_code
        print("Очередь пуста (ready_to_post).")
        return exit_code

    for line in cooldown_msgs:
        print(f"[cooldown skip] {line}")

    cfg = load_publisher_config()
    for page in pages:
        job = build_job_from_page(page, config=cfg, channels=args.channels)
        wanted = immediate_channels_for_queue(
            job,
            cfg,
            requested=args.channels,
        )
        print("---")
        print(summarize_job(job))
        if not wanted:
            print("нет немедленных pending-каналов — пропуск")
            continue
        results = run_channels(
            job,
            channels=wanted,
            dry_run=dry_run,
            push_media=push_media,
            confirm_post=confirm_post,
        )
        for r in results:
            flag = _result_flag(r)
            skip = " skip" if r.skipped else ""
            url = f" url={r.post_url}" if r.post_url else ""
            print(
                f"  [{flag}{skip}] {r.channel}: "
                f"{r.reason or ''} {r.note or ''}{url}".rstrip()
            )
            if not r.ok and not r.skipped:
                exit_code = 1
        if confirm_post and results:
            print("Пошаговый live завершён после одного канала. Ожидается отчёт/проверка.")
            return exit_code
    return exit_code


def cmd_prepare(args: argparse.Namespace) -> int:
    pages = fetch_ready_pages(page_id=args.page_id, limit=1)
    if not pages:
        print("Страница не найдена / очередь пуста", file=sys.stderr)
        return 1
    job = build_job_from_page(pages[0], channels=args.channels)
    job = prepare_job(job, push_to_device=args.push_media and not args.dry_run)
    print(summarize_job(job))
    print(f"local_video={job.local_video}")
    print(f"local_images={len(job.local_images)}")
    if job.device_video or job.device_images:
        print(f"device_video={job.device_video}")
        print(f"device_images={len(job.device_images)}")
    print(f"snapshot=data/jobs/{job.object_id}.json")
    return 0


def cmd_publish_all(args: argparse.Namespace) -> int:
    dry_run, push_media, confirm_post = _resolve_run_flags(args)
    pages = fetch_ready_pages(page_id=args.page_id, limit=1)
    if not pages:
        print("Страница не найдена", file=sys.stderr)
        return 1
    job = build_job_from_page(pages[0], channels=None)
    print(summarize_job(job))
    if confirm_post:
        print("!!! LIVE publish-all: будет выполнен только следующий канал, затем остановка !!!")
    results = run_publish_all(
        job,
        dry_run=dry_run,
        push_media=push_media,
        confirm_post=confirm_post,
    )
    code = 0
    for r in results:
        flag = _result_flag(r)
        skip = " skip" if r.skipped else ""
        url = f" url={r.post_url}" if r.post_url else ""
        print(f"[{flag}{skip}] {r.channel}: {r.reason or ''} {r.note or ''}{url}".rstrip())
        if not r.ok and not r.skipped:
            code = 1
    return code


def cmd_publish_scheduled(args: argparse.Namespace) -> int:
    dry_run, push_media, confirm_post = _resolve_run_flags(args)
    pairs = run_scheduled(
        dry_run=dry_run,
        push_media=push_media,
        confirm_post=confirm_post,
    )
    if not pairs:
        print("Нет отложенных каналов к публикации.")
        return 0
    code = 0
    for job, r in pairs:
        print(f"--- {job.object_id} ---")
        flag = _result_flag(r)
        skip = " skip" if r.skipped else ""
        url = f" url={r.post_url}" if r.post_url else ""
        print(f"[{flag}{skip}] {r.channel}: {r.reason or ''} {r.note or ''}{url}".rstrip())
        if not r.ok and not r.skipped:
            code = 1
    return code


def cmd_publish_retry(args: argparse.Namespace) -> int:
    dry_run, push_media, confirm_post = _resolve_run_flags(args)
    pages = fetch_ready_pages(page_id=args.page_id, limit=1)
    if not pages:
        print("Страница не найдена", file=sys.stderr)
        return 1
    channels = args.channels or ["fb_groups", "fb_marketplace"]
    job = build_job_from_page(pages[0], channels=channels)
    print(summarize_job(job))
    print(f"Retry channels: {','.join(channels)} (max {args.max_attempts} attempts each)")
    results = run_channels_with_retry(
        job,
        channels=channels,
        dry_run=dry_run,
        push_media=push_media,
        confirm_post=confirm_post,
        max_attempts=args.max_attempts,
    )
    code = 0
    for ch in channels:
        last = next((r for r in reversed(results) if r.channel == ch), None)
        if not last:
            continue
        flag = _result_flag(last)
        skip = " skip" if last.skipped else ""
        url = f" url={last.post_url}" if last.post_url else ""
        print(f"[{flag}{skip}] {last.channel}: {last.reason or ''} {last.note or ''}{url}".rstrip())
        if not last.ok and not last.skipped:
            code = 1
    return code


def cmd_publish_chain(args: argparse.Namespace) -> int:
    dry_run, push_media, confirm_post = _resolve_run_flags(args)
    reset = list(args.reset_channel) if args.reset_channel else None
    if confirm_post:
        print("!!! LIVE publish-chain: только следующий scheduled/pending канал, затем остановка !!!")
    return run_publish_chain(
        args.page_id,
        dry_run=dry_run,
        push_media=push_media,
        confirm_post=confirm_post,
        reset_channels=reset,
        max_attempts=args.max_attempts,
    )


def cmd_publish(args: argparse.Namespace) -> int:
    if (code := _validate_channel_args(args)) is not None:
        return code
    dry_run, push_media, confirm_post = _resolve_run_flags(args)
    pages = fetch_ready_pages(page_id=args.page_id, limit=1)
    if not pages:
        print("Страница не найдена", file=sys.stderr)
        return 1
    job = build_job_from_page(pages[0], channels=args.channels)
    print(summarize_job(job))
    if confirm_post:
        print("!!! LIVE: будет нажато «Опубликовать» в приложении !!!")
    elif not dry_run:
        print("UI-режим: дойдём до публикации, но не нажмём Post (нужен --live)")
    results = run_channels(
        job,
        channels=args.channels or job.channels_pending or list(CHANNELS),
        dry_run=dry_run,
        push_media=push_media,
        confirm_post=confirm_post,
    )
    code = 0
    for r in results:
        flag = _result_flag(r)
        skip = " skip" if r.skipped else ""
        url = f" url={r.post_url}" if r.post_url else ""
        print(
            f"[{flag}{skip}] {r.channel}: "
            f"{r.reason or ''} {r.note or ''}{url}".rstrip()
        )
        if not r.ok and not r.skipped:
            code = 1
    return code


def cmd_capture_url(args: argparse.Namespace) -> int:
    from .channels._post_url import android_post_url_capture_enabled

    android_cfg = load_android_config()
    if not android_post_url_capture_enabled(android_cfg):
        print(
            "Захват post URL через Android отключён. "
            "Ссылки должны поступать через API.",
            file=sys.stderr,
        )
        return 2

    from .android.post_link import capture_post_url

    status = check_adb(android_cfg)
    if not status.get("ok"):
        print(json.dumps(status, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    d = connect_device(android_cfg)
    ensure_unlocked(d)
    url, extra = capture_post_url(
        d,
        args.channel,
        android_cfg,
        wait_seconds=float(args.wait),
    )
    print(f"url={url or ''}")
    for k, v in (extra or {}).items():
        print(f"{k}={v}")
    if not url:
        return 1
    if args.page_id:
        page = notion.get_page(args.page_id)
        record_captured_post_url(page, args.channel, url, extra)
        print("local verification updated; Notion update delivered or queued")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="publisher_social",
        description="Notion/R2 → Android phone → FB Groups / FB Marketplace",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check-device", help="Проверить ADB / телефон")
    p_check.set_defaults(func=cmd_check_device)

    p_queue = sub.add_parser("queue", help="Обработать очередь ready_to_post")
    p_queue.add_argument("--limit", type=int, default=None)
    _add_common(p_queue)
    p_queue.set_defaults(func=cmd_queue)

    p_prep = sub.add_parser("prepare", help="Собрать job + скачать медиа (+ optional adb push)")
    p_prep.add_argument("--page-id", required=True)
    _add_common(p_prep)
    p_prep.set_defaults(func=cmd_prepare)

    p_pub = sub.add_parser("publish", help="Подготовить и прогнать каналы для одного объекта")
    p_pub.add_argument("--page-id", required=True)
    _add_common(p_pub)
    p_pub.set_defaults(func=cmd_publish)

    p_all = sub.add_parser(
        "publish-all",
        help="Пошаговый план всех каналов; live выполняет только следующий канал",
    )
    p_all.add_argument("--page-id", required=True)
    _add_common(p_all)
    p_all.set_defaults(func=cmd_publish_all)

    p_sched = sub.add_parser(
        "publish-scheduled",
        help="Опубликовать отложенные каналы (TikTok/IG карусели), когда наступило время",
    )
    _add_common(p_sched)
    p_sched.set_defaults(func=cmd_publish_scheduled)

    p_retry = sub.add_parser(
        "publish-retry",
        help="Повтор канала; в live только один канал и одна попытка до отчёта",
    )
    p_retry.add_argument("--page-id", required=True)
    p_retry.add_argument("--max-attempts", type=int, default=3)
    _add_common(p_retry)
    p_retry.set_defaults(func=cmd_publish_retry)

    p_chain = sub.add_parser(
        "publish-chain",
        help="Оркестратор: следующий scheduled/pending канал и остановка",
    )
    p_chain.add_argument("--page-id", required=True)
    p_chain.add_argument("--max-attempts", type=int, default=2)
    p_chain.add_argument(
        "--reset-channel",
        action="append",
        dest="reset_channel",
        choices=list(CHANNELS),
        help="Сбросить local state и принудительно перепубликовать канал",
    )
    _add_common(p_chain)
    p_chain.set_defaults(func=cmd_publish_chain)

    p_cap = sub.add_parser(
        "capture-url",
        help="Подтвердить свежий пост и записать его URL",
    )
    p_cap.add_argument("--page-id", help="Записать URL в Notion")
    p_cap.add_argument(
        "--channel",
        required=True,
        choices=list(CHANNELS),
    )
    p_cap.add_argument("--wait", type=float, default=5.0, help="Секунд ждать перед захватом")
    p_cap.set_defaults(func=cmd_capture_url)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    lock_commands = {
        "queue",
        "prepare",
        "publish",
        "publish-all",
        "publish-scheduled",
        "publish-retry",
        "publish-chain",
        "capture-url",
    }
    if args.command not in lock_commands:
        return int(args.func(args))

    if hasattr(args, "channels") and (code := _validate_channel_args(args)) is not None:
        return code

    try:
        timeout = float(os.environ.get("PUBLISHER_LOCK_TIMEOUT") or 0)
    except ValueError:
        timeout = 0.0
    try:
        with PublisherProcessLock(args.command, timeout=timeout):
            if bool(getattr(args, "live", False)):
                delivered, failed = retry_pending_notion_updates()
                if delivered or failed:
                    print(
                        f"Notion outbox: delivered={delivered}, pending_failed={failed}"
                    )
            return int(args.func(args))
    except PublisherLockBusy as exc:
        print(
            f"Другой процесс Publisher уже управляет телефоном/state: {exc}",
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

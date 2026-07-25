#!/usr/bin/env python3
"""
Цепочка агентов по статусам Notion CRM.

Agent 1 (parser) → session → Agent 2 → ready_for_video
    → Agent 3 (этот скрипт ждёт Notion) → ready_to_post
    → Agent 6 (ждёт видео) → published

Каждый агент запускается ТОЛЬКО когда предыдущий записал нужные данные в CRM.

Usage:
  python3 scripts/chain_runner.py --from-agent 3 --object-id 20260701_001
  python3 scripts/chain_runner.py --from-agent 6 --object-id 20260701_001 --publish instagram
  python3 scripts/chain_runner.py --watch
  python3 scripts/chain_runner.py --once
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from real_estate_handler import NotionCRM  # noqa: E402
from notion_gate import (  # noqa: E402
    agent3_ready,
    agent6_ready,
    fetch_by_object_id,
    fetch_by_status,
    fetch_montage_in_progress,
    finish_montage_if_video_ready,
    flag_enabled,
    try_claim_montage,
)
from montage_lock import MontageBusyError, montage_file_lock  # noqa: E402
from event_log import log_event  # noqa: E402
from error_notify import clear_tag, notify  # noqa: E402
from pipeline_config import apply_env_overrides  # noqa: E402


def load_dotenv() -> None:
    # Корень монорепы (.env с ERROR_BOT_TOKEN и т.п.) + локальные .env агента.
    for p in (ROOT / ".env.real-estate", ROOT / ".env", ROOT.parents[2] / ".env"):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def load_config() -> dict:
    with (ROOT / "config" / "pipeline.json").open(encoding="utf-8") as f:
        cfg = json.load(f)
    return apply_env_overrides(cfg)


def run_agent3(object_id: str) -> int:
    """Агент 3 = Director (Seedance + хук): FFmpeg-монтаж отвязан от пайплайна,
    код остался в scripts/agent3_video.mjs на случай возврата."""
    director = ROOT.parents[2] / "agent_3_director"
    entry = director / "scripts" / "run_from_notion.mjs"
    if not entry.exists():
        print(f"[chain] SKIP Agent 3: not found {entry}", file=sys.stderr)
        return 1
    print(f"\n[chain] Agent 3 (Director/Seedance) → {object_id}")
    proc = subprocess.run(
        ["node", str(entry), "--object-id", object_id],
        cwd=director,
    )
    return proc.returncode


def run_build_carousel(object_id: str) -> int:
    """Дизайн-карусель Агента 3 (слайды с бейджами в R2 {id}/carousel/).

    Для пути «монтаж=НЕТ, публикация=ДА» — монтаж не запускается, а
    публикатору нужны оформленные слайды. Идемпотентно: если carousel_url
    в Notion уже заполнен, скрипт выходит сразу.
    """
    director = ROOT.parents[2] / "agent_3_director"
    script = director / "scripts" / "build_carousel.mjs"
    if not script.exists():
        print(f"[chain] SKIP carousel: not found {script}", file=sys.stderr)
        return 0
    print(f"\n[chain] Carousel (Agent 3 design) → {object_id}")
    proc = subprocess.run(
        ["node", str(script), "--object-id", object_id],
        cwd=director,
    )
    return proc.returncode


HIGGSFIELD_AUTH_TAG = "higgsfield_auth"


def check_higgsfield_auth() -> tuple[bool, str]:
    """Preflight-проверка сессии Higgsfield CLI (для Seedance)."""
    script = ROOT.parents[2] / "agent_3_director" / "scripts" / "check_higgsfield_auth.mjs"
    if not script.exists():
        return True, "check script not found — skip"
    proc = subprocess.run(
        ["node", str(script)],
        cwd=script.parents[1],
        capture_output=True,
        text=True,
    )
    out = f"{proc.stdout or ''}{proc.stderr or ''}".strip()
    return proc.returncode == 0, out


def watch_higgsfield_auth() -> None:
    """Уведомление в error-бот при протухшей авторизации Higgsfield.

    Шлём один раз (cooldown в error_notify); после восстановления
    сбрасываем дедуп и сообщаем, что всё снова работает.
    """
    ok, out = check_higgsfield_auth()
    if ok:
        if clear_tag_if_was_failing():
            notify("Higgsfield CLI: авторизация восстановлена, монтаж снова работает.",
                   force=True)
        return
    print(f"[chain] Higgsfield auth check FAILED:\n{out}", file=sys.stderr)
    sent = notify(
        "Higgsfield CLI: авторизация протухла — монтаж видео (Seedance) не запустится.\n\n"
        "Починить на сервере:\n"
        "ssh root@<VPS> \"HOME=/root higgsfield auth login\"\n"
        "или локально: higgsfield auth login, затем скопировать\n"
        "~/.config/higgsfield/credentials.json на сервер.",
        tag=HIGGSFIELD_AUTH_TAG,
    )
    if sent:
        _mark_auth_failing()


_AUTH_FAIL_FLAG = ROOT / "data" / "higgsfield_auth_failing"


def _mark_auth_failing() -> None:
    try:
        _AUTH_FAIL_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _AUTH_FAIL_FLAG.touch()
    except OSError:
        pass


def clear_tag_if_was_failing() -> bool:
    if not _AUTH_FAIL_FLAG.exists():
        return False
    try:
        _AUTH_FAIL_FLAG.unlink()
    except OSError:
        pass
    clear_tag(HIGGSFIELD_AUTH_TAG)
    return True


def resolve_publish_platforms(chain: dict) -> list[str]:
    """Список Metricool-платформ: приоритет у agent_4_publisher/config/publisher.json."""
    estate_root = ROOT.parents[2]
    pub_path = estate_root / "agent_4_publisher" / "config" / "publisher.json"
    try:
        with pub_path.open(encoding="utf-8") as f:
            pub_cfg = json.load(f)
        if not pub_cfg.get("metricool", {}).get("enabled", True):
            return []
        plats = pub_cfg.get("publish_platforms")
        if plats:
            return list(plats)
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return list(chain.get("publish_platforms") or ["instagram"])


def load_agent4_publisher_config() -> dict:
    pub_path = ROOT.parents[2] / "agent_4_publisher" / "config" / "publisher.json"
    with pub_path.open(encoding="utf-8") as f:
        return json.load(f)


def phone_publisher_project_root(pub_cfg: dict) -> Path:
    rel = pub_cfg.get("phone_publisher", {}).get("project_path", "Publisher social")
    agents_root = ROOT.parents[2].parent  # папка «Агенты»
    return (agents_root / rel).resolve()


def run_phone_publisher(page_id: str, pub_cfg: dict, *, live: bool = False) -> int:
    """Publisher social — публикация с Android-телефона (ADB)."""
    pp = pub_cfg.get("phone_publisher", {})
    if not pp.get("enabled"):
        print("[chain] SKIP phone publisher: disabled in publisher.json", file=sys.stderr)
        return 0
    project = phone_publisher_project_root(pub_cfg)
    if not project.exists():
        print(f"[chain] SKIP phone publisher: not found {project}", file=sys.stderr)
        return 1
    cmd_name = pp.get("command", "publish-all")
    cmd = [
        sys.executable,
        "-m",
        "publisher_social",
        cmd_name,
        "--page-id",
        page_id,
    ]
    if live:
        cmd.append("--live")
    else:
        cmd.append("--dry-run")
    env = os.environ.copy()
    src = project / "src"
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    print(f"\n[chain] Phone publisher ({cmd_name}) → {page_id}"
          + (" [LIVE]" if live else " [dry-run]"))
    proc = subprocess.run(cmd, cwd=project, env=env)
    return proc.returncode


def spawn_chatplace_for_reel(page_id: str) -> int:
    """ChatPlace IG reel — после post_url_instagram_reel в Notion."""
    script = ROOT.parents[2] / "agent_4_publisher" / "scripts" / "spawn_chatplace_for_reel.py"
    if not script.exists():
        print(f"[chain] SKIP chatplace reel: not found {script}", file=sys.stderr)
        return 0
    print(f"\n[chain] ChatPlace reel funnel → {page_id}")
    proc = subprocess.run(
        [sys.executable, str(script), "--page-id", page_id],
        cwd=script.parent,
    )
    return proc.returncode


def run_agent6(page_id: str, platform: str, publisher_script: Path,
               mode: str | None = None) -> int:
    """Один вызов publish_pipeline: --platform all публикует во все сети Metricool."""
    print(f"\n[chain] Agent 6 → {page_id} ({platform}"
          + (f", mode={mode}" if mode else "") + ")")
    if not publisher_script.exists():
        print(f"[chain] SKIP Agent 6: not found {publisher_script}", file=sys.stderr)
        return 0
    cmd = [sys.executable, str(publisher_script), "--page-id", page_id, "--platform", platform]
    if mode:
        cmd.extend(["--mode", mode])
    proc = subprocess.run(cmd, cwd=publisher_script.parent)
    return proc.returncode


def resolve_fb_python() -> Path | None:
    """python3.11 с Playwright — venv FB-парсера (там же авторизованный профиль)."""
    p = ROOT.parents[2] / "agent_1_parser" / "fb_parser" / ".venv311" / "bin" / "python"
    return p if p.exists() else None


def run_fb_branch(page_id: str, script_name: str) -> int:
    """FB-ветки Агента 4 (группы / маркетплейс): свои локи (fb_*_locked),
    «Статус» не меняют, поэтому безопасны рядом с Metricool-веткой."""
    publisher_dir = ROOT.parents[2] / "agent_4_publisher"
    script = publisher_dir / "scripts" / script_name
    fb_python = resolve_fb_python()
    if not script.exists():
        print(f"[chain] SKIP {script_name}: not found {script}", file=sys.stderr)
        return 0
    if fb_python is None:
        print(f"[chain] SKIP {script_name}: нет venv FB-парсера (.venv311)", file=sys.stderr)
        return 0
    cmd = [str(fb_python), str(script), "--page-id", page_id]
    # На сервере без дисплея — виртуальный экран (headful палится у FB меньше)
    import shutil
    if not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        cmd = ["xvfb-run", "-a", "-s", "-screen 0 1440x900x24"] + cmd
    print(f"\n[chain] Agent 4 FB ({script_name}) → {page_id}")
    proc = subprocess.run(cmd, cwd=publisher_dir)
    return proc.returncode


def run_telegram_showcase(page_id: str) -> int:
    """TG-канал — витрина ВСЕЙ базы: постим фото+описание сразу после Агента 2,
    независимо от флагов «Монтаж»/«Публикация» (они управляют только
    соц.сетями). Скрипт идемпотентен: post_url_telegram заполнен — пропустит.
    --no-metricool: Metricool-карусель остаётся за обычной веткой Агента 4."""
    publisher_dir = ROOT.parents[2] / "agent_4_publisher"
    script = publisher_dir / "scripts" / "publish_telegram.py"
    if not script.exists():
        print(f"[chain] SKIP telegram: not found {script}", file=sys.stderr)
        return 0
    print(f"\n[chain] Telegram showcase → {page_id}")
    proc = subprocess.run(
        [sys.executable, str(script), "--page-id", page_id, "--no-metricool"],
        cwd=publisher_dir,
    )
    return proc.returncode


def resolve_publisher_script(cfg: dict) -> Path:
    candidates: list[Path] = []
    rel = cfg.get("chain", {}).get("publisher_script")
    if rel:
        candidates.append((ROOT / rel).resolve())
    monorepo = ROOT.parent.parent
    estate_root = monorepo.parent
    candidates.extend([
        # Канонический Агент 4 — первым; workspaces/publisher — старая копия
        estate_root / "agent_4_publisher/scripts/publish_pipeline.py",
        monorepo / "workspaces/publisher/scripts/publish_pipeline.py",
        Path("/opt/real-estate-publisher/scripts/publish_pipeline.py"),
    ])
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def chain_auto_publish(cfg: dict) -> bool:
    chain = cfg.get("chain", {})
    if chain.get("auto_agent6") is not None:
        return bool(chain.get("auto_agent6"))
    return bool(chain.get("auto_agent4"))


def continue_chain(
    crm: NotionCRM,
    cfg: dict,
    *,
    object_id: str | None = None,
    from_agent: int = 3,
    publish_platforms: list[str] | None = None,
    force_montage: bool = False,
) -> int:
    nf = cfg["notion"]["fields"]
    statuses = cfg["notion"]["statuses"]
    chain = cfg.get("chain", {})
    publisher = resolve_publisher_script(cfg)
    platforms: list[str] = publish_platforms or resolve_publish_platforms(chain)
    # Пустая ячейка флага = дефолт: «ДА» — всё автоматом (текущий режим),
    # «НЕТ» — объекты копятся в базе, пока флаг не поставят вручную.
    default_montage = flag_enabled(chain.get("default_montage", "НЕТ"))
    default_publish = flag_enabled(chain.get("default_publish", "НЕТ"))

    exit_code = 0

    if from_agent <= 3:
        listings: list = []
        if object_id:
            one = fetch_by_object_id(crm, object_id, nf)
            if one:
                listings = [one]
        else:
            listings.extend(fetch_by_status(crm, statuses["after_structurize"], nf))
            listings.extend(
                fetch_by_status(crm, statuses.get("video_failed", "video_failed"), nf)
            )

        # TG-канал — витрина всей базы: каждый объект после Агента 2 постится
        # в Telegram независимо от флагов (выключается chain.telegram_showcase).
        # Смотрим и статусы монтажа/готовности — чтобы дослать посты объектам,
        # которые прошли цепочку до появления этой ветки.
        if chain.get("telegram_showcase", True):
            tg_candidates = list(listings)
            if not object_id:
                for st_key in ("video_start", "video_done"):
                    st = statuses.get(st_key)
                    if st:
                        tg_candidates.extend(fetch_by_status(crm, st, nf))
            seen_tg: set[str] = set()
            for listing in tg_candidates:
                if listing.object_id in seen_tg or listing.tg_post_url:
                    continue
                seen_tg.add(listing.object_id)
                if not listing.gallery_url:
                    continue
                code_tg = run_telegram_showcase(listing.page_id)
                if code_tg != 0:
                    log_event(listing.object_id, "chain", "telegram_showcase_failed",
                              code=code_tg)

        processed_3: set[str] = set()
        montage_busy = fetch_montage_in_progress(crm, statuses, nf)
        if montage_busy and finish_montage_if_video_ready(crm, montage_busy, statuses, nf):
            print(f"[chain] {montage_busy.object_id}: video ready — "
                  f"status → {statuses['video_done']} (unblock queue)")
            montage_busy = fetch_montage_in_progress(crm, statuses, nf)
        montage_started = False
        for listing in listings:
            if listing.object_id in processed_3:
                continue
            montage_on = force_montage or flag_enabled(listing.montage_flag, default_montage)
            publish_on = flag_enabled(listing.publish_flag, default_publish)
            if not montage_on:
                # Монтаж выключен. Если публикация включена — минуем Агента 3:
                # переводим объект сразу в ready_to_post (карусель без видео).
                if (publish_on and listing.gallery_url
                        and listing.status == statuses["after_structurize"]):
                    print(f"[chain] {listing.object_id}: монтаж=НЕТ, публикация=ДА → "
                          f"сразу {statuses['video_done']} (карусель без видео)")
                    # Дизайн-карусель до публикации (best effort: при ошибке
                    # публикатор возьмёт сырые фото из «Фото»)
                    code_car = run_build_carousel(listing.object_id)
                    if code_car != 0:
                        log_event(listing.object_id, "chain", "carousel_failed",
                                  code=code_car)
                    crm.update_page(listing.page_id, {
                        nf["status"]: crm.build_status(statuses["video_done"]),
                    })
                    log_event(listing.object_id, "chain", "skip_montage_to_publish",
                              page_id=listing.page_id)
                else:
                    print(f"[chain] Agent 3 skip {listing.object_id}: монтаж=НЕТ"
                          + ("" if publish_on else ", публикация=НЕТ — объект только для базы"))
                processed_3.add(listing.object_id)
                continue
            if montage_busy:
                if montage_busy.object_id == listing.object_id:
                    print(f"[chain] Agent 3 skip {listing.object_id}: montage already in progress")
                else:
                    print(f"[chain] Agent 3 wait {listing.object_id}: "
                          f"montage busy ({montage_busy.object_id})")
                continue
            if montage_started:
                continue
            ready, reason = agent3_ready(listing, statuses,
                                         default_montage=default_montage,
                                         force=force_montage)
            if not ready:
                print(f"[chain] Agent 3 skip {listing.object_id}: {reason}")
                continue
            try:
                with montage_file_lock(listing.object_id, blocking=False):
                    claimed, claim_reason = try_claim_montage(
                        crm, listing, statuses, nf)
                    if not claimed:
                        print(f"[chain] Agent 3 skip {listing.object_id}: {claim_reason}")
                        continue
                    log_event(listing.object_id, "chain", "agent3_start",
                              page_id=listing.page_id, claim=claim_reason)
                    code = run_agent3(listing.object_id)
            except MontageBusyError as exc:
                print(f"[chain] Agent 3 skip {listing.object_id}: {exc}")
                continue
            processed_3.add(listing.object_id)
            montage_started = True
            if code != 0:
                log_event(listing.object_id, "chain", "agent3_failed", code=code)
                notify(
                    f"Агент 3 (монтаж видео) упал на объекте {listing.object_id} "
                    f"(exit={code}). Детали в last_error объекта в Notion "
                    "и в логах re-chain-watcher.",
                    tag=f"agent3_failed:{listing.object_id}",
                )
                exit_code = code
            break  # строго один объект за цикл — остальные ждут следующего poll

    fb_branches: list[str] = []
    pub_cfg = load_agent4_publisher_config()
    phone_on = bool(pub_cfg.get("phone_publisher", {}).get("enabled"))
    metricool_on = bool(pub_cfg.get("metricool", {}).get("enabled", True))
    if chain.get("publish_fb_groups") and not (phone_on and not metricool_on):
        fb_branches.append("fb_groups_pipeline.py")
    if chain.get("publish_fb_marketplace") and not (phone_on and not metricool_on):
        fb_branches.append("fb_marketplace_pipeline.py")

    should_run_agent6 = chain_auto_publish(cfg) or from_agent >= 6 or bool(publish_platforms)
    if should_run_agent6 and (platforms or fb_branches or phone_on):
        listings6: list = []
        if object_id:
            one = fetch_by_object_id(crm, object_id, nf)
            if one:
                listings6 = [one]
        else:
            listings6 = fetch_by_status(crm, statuses["video_done"], nf)

        for listing in listings6:
            ready6, reason6 = agent6_ready(listing, statuses,
                                           default_publish=default_publish,
                                           default_montage=default_montage)
            if not ready6:
                print(f"[chain] Agent 6 skip {listing.object_id}: {reason6}")
                if "error_count" in reason6:
                    notify(
                        f"Публикация {listing.object_id} остановлена: {reason6}.\n"
                        "Часть сетей могла опубликоваться — проверьте last_error "
                        "в Notion и сбросьте error_count для повтора.",
                        tag=f"agent6_error_cap:{listing.object_id}",
                    )
                continue
            # Видео нет (монтаж выключен) — публикуем только карусель.
            mode6 = None if listing.has_videos else "carousel"
            if phone_on and not metricool_on:
                log_event(listing.object_id, "chain", "phone_publisher_start", page_id=listing.page_id)
                live = bool(chain.get("phone_publisher_live", False))
                code6 = run_phone_publisher(listing.page_id, pub_cfg, live=live)
                if code6 != 0:
                    exit_code = code6
                    log_event(listing.object_id, "chain", "phone_publisher_failed", code=code6)
                    notify(
                        f"Phone publisher упал на объекте {listing.object_id}. "
                        "Проверьте ADB/телефон и last_error в Notion.",
                        tag=f"phone_publisher_failed:{listing.object_id}",
                    )
                elif live:
                    spawn_chatplace_for_reel(listing.page_id)
            elif platforms:
                # Один процесс --platform all: все сети в одном запуске (как раньше через
                # publisher.json), иначе после Instagram срабатывает agent6_locked.
                plat_arg = "all" if len(platforms) > 1 else platforms[0]
                log_event(listing.object_id, "chain", "agent6_start", platform=plat_arg)
                code6 = run_agent6(listing.page_id, plat_arg, publisher, mode=mode6)
                if code6 != 0:
                    exit_code = code6
                    log_event(listing.object_id, "chain", "agent6_failed", platform=plat_arg)
                    notify(
                        f"Агент 6 (публикация) упал на объекте {listing.object_id} "
                        f"({plat_arg}). Детали в last_error объекта в Notion.",
                        tag=f"agent6_failed:{listing.object_id}",
                    )
            for script_name in fb_branches:
                branch = script_name.replace("_pipeline.py", "")
                log_event(listing.object_id, "chain", f"{branch}_start", page_id=listing.page_id)
                code_fb = run_fb_branch(listing.page_id, script_name)
                if code_fb != 0:
                    exit_code = code_fb
                    log_event(listing.object_id, "chain", f"{branch}_failed", code=code_fb)

    return exit_code


def watch_loop(crm: NotionCRM, cfg: dict) -> None:
    interval = cfg.get("chain", {}).get("poll_interval_seconds", 30)
    auth_interval = cfg.get("chain", {}).get("auth_check_interval_seconds", 900)
    print(f"[chain] Watching Notion CRM every {interval}s (Ctrl+C to stop)")
    last_auth_check = 0.0
    while True:
        now = time.time()
        if now - last_auth_check >= auth_interval:
            last_auth_check = now
            try:
                watch_higgsfield_auth()
            except Exception as exc:
                print(f"[chain] auth check error: {exc}", file=sys.stderr)
        try:
            continue_chain(crm, cfg, from_agent=3)
        except Exception as exc:
            print(f"[chain] error: {exc}", file=sys.stderr)
        time.sleep(interval)


def normalize_from_agent(value: int) -> int:
    if value == 4:
        print("[chain] --from-agent 4 is deprecated; use --from-agent 6", file=sys.stderr)
        return 6
    return value


def main() -> int:
    load_dotenv()
    cfg = load_config()
    crm = NotionCRM(
        os.environ["NOTION_API_KEY"],
        os.environ.get("NOTION_DB_ID") or os.environ["NOTION_DATABASE_ID"],
    )

    parser = argparse.ArgumentParser(description="Agent chain orchestrator (Notion-gated)")
    parser.add_argument("--watch", action="store_true", help="Poll Notion and run agents")
    parser.add_argument("--once", action="store_true", help="Process all pending once")
    parser.add_argument("--from-agent", type=int, default=3, choices=[3, 4, 6])
    parser.add_argument("--object-id", help="Single object to process")
    parser.add_argument("--force-montage", action="store_true",
                        help="Ручной запуск: игнорировать «Монтаж»=НЕТ (только с --object-id)")
    parser.add_argument("--publish", help="Comma platforms for Agent 6 this run (e.g. instagram,tiktok)")
    args = parser.parse_args()

    platforms_override = None
    if args.publish:
        platforms_override = [p.strip() for p in args.publish.split(",") if p.strip()]

    if args.watch:
        watch_loop(crm, cfg)
        return 0

    return continue_chain(
        crm,
        cfg,
        object_id=args.object_id,
        from_agent=normalize_from_agent(args.from_agent),
        publish_platforms=platforms_override,
        force_montage=bool(args.force_montage),
    )


if __name__ == "__main__":
    sys.exit(main())

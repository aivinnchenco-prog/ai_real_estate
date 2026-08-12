#!/usr/bin/env python3
"""Local CLI for Agent6/7 two-file knowledge base (no live network)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _print_issues(issues) -> None:
    if not issues:
        return
    for issue in issues[:10]:
        print()
        print(issue.human_message())
        print("-" * 40)


def cmd_update(_: argparse.Namespace) -> int:
    from agent6_qualifier.knowledge.index import load_index, rebuild_index
    from agent6_qualifier.knowledge.parser import (
        agent6_human_path,
        agent7_human_path,
        load_all_human_entries,
        validate_human_files,
    )

    a6 = agent6_human_path()
    a7 = agent7_human_path()
    if not a6.exists() or not a7.exists():
        print("Не найдены оба главных файла базы знаний.")
        if not a6.exists():
            print(f"Нет файла: {a6.name}")
        if not a7.exists():
            print(f"Нет файла: {a7.name}")
        return 1

    before = {e.id for e in load_index()}
    entries, issues = load_all_human_entries()
    blocking = [
        i
        for i in validate_human_files()
        if i.problem.startswith("«Как должен") or "файл не найден" in i.problem
    ]
    if blocking:
        print("Knowledge update failed.")
        _print_issues(blocking)
        print("\nИндекс не перезаписан. Используется предыдущая рабочая версия.")
        return 1

    payload = rebuild_index()
    after_ids = {e.get("id") for e in payload.get("entries") or []}
    new_ids = after_ids - before
    a6_new = sum(1 for i in new_ids if str(i).startswith("agent6/"))
    a7_new = sum(1 for i in new_ids if str(i).startswith("agent7/"))

    print("Knowledge updated successfully.")
    print()
    print(f"Agent6 rules: {payload.get('agent6_count', 0)}")
    print(f"Agent7 rules: {payload.get('agent7_count', 0)}")
    print()
    print(f"New Agent6 rules: {a6_new}")
    print(f"New Agent7 rules: {a7_new}")
    if issues:
        warn = [
            i
            for i in issues
            if not i.problem.startswith("«Как должен")
        ]
        if warn:
            print()
            print(f"Предупреждения: {len(warn)}")
            _print_issues(warn[:3])
    return 0


def cmd_validate(_: argparse.Namespace) -> int:
    from agent6_qualifier.knowledge.parser import (
        agent6_human_path,
        agent7_human_path,
        find_secrets_in_entries,
        load_all_human_entries,
        validate_human_files,
    )

    if not agent6_human_path().exists() or not agent7_human_path().exists():
        print("Не найдены оба файла Agent6/Agent7.")
        return 1
    entries, issues = load_all_human_entries()
    if not entries:
        print("Правила не найдены.")
        return 1
    blocking = [
        i
        for i in validate_human_files()
        if i.problem.startswith("«Как должен") or "файл не найден" in i.problem
    ]
    if blocking:
        print("Проверка не пройдена.")
        _print_issues(blocking)
        return 1
    secrets = find_secrets_in_entries(entries)
    if secrets:
        print(f"Внимание: похоже на секреты в правилах: {secrets[:5]}")
    a6 = sum(1 for e in entries if e.agents == ["AGENT6"])
    a7 = sum(1 for e in entries if e.agents == ["AGENT7"])
    print("Проверка пройдена.")
    print(f"Agent6 rules: {a6}")
    print(f"Agent7 rules: {a7}")
    if issues:
        print(f"Предупреждения: {len(issues)}")
    return 0


def cmd_rebuild(_: argparse.Namespace) -> int:
    from agent6_qualifier.knowledge.index import rebuild_index

    payload = rebuild_index()
    print(
        "OK: rebuilt index "
        f"total={payload.get('entry_count')} "
        f"agent6={payload.get('agent6_count')} "
        f"agent7={payload.get('agent7_count')}"
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    from agent6_qualifier.knowledge.learning import CandidateStore

    store = CandidateStore()
    items = store.list(status=args.status)
    if not items:
        print("(no candidates)")
        return 0
    for c in items:
        from agent6_qualifier.knowledge.learning import recommended_destination

        dest = recommended_destination(c)
        print(f"{c.id}\t{c.status}\t{c.agent}\t{dest}\t{c.situation[:50]}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    from agent6_qualifier.knowledge.learning import CandidateStore, recommended_destination

    c = CandidateStore().get(args.id)
    if not c:
        print(f"NOT FOUND: {args.id}")
        return 1
    data = c.to_dict()
    data["recommended_destination"] = recommended_destination(c)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    from agent6_qualifier.knowledge.index import rebuild_index
    from agent6_qualifier.knowledge.learning import (
        CandidateStore,
        append_approved_playbook_to_master,
        is_hard_rule_candidate,
        recommended_destination,
    )

    store = CandidateStore()
    c = store.get(args.id)
    if not c:
        print(f"NOT FOUND: {args.id}")
        return 1
    dest = recommended_destination(c)
    print(f"Recommended destination:\n{dest}")
    if is_hard_rule_candidate(c) and not args.force_playbook:
        print(
            "\nЭто похоже на жёсткое бизнес-правило.\n"
            "Approve не меняет rental_policy / qualifier / ownership код.\n"
            "Нужна правка кода + тесты, либо --force-playbook чтобы добавить только как совет."
        )
        return 2
    store.set_status(args.id, "APPROVED", notes=args.notes or "approved via CLI")
    eid = append_approved_playbook_to_master(c)
    rebuild_index()
    print(f"\nAPPROVED → {eid}")
    print("Business code unchanged.")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    from agent6_qualifier.knowledge.learning import CandidateStore

    c = CandidateStore().set_status(args.id, "REJECTED", notes=args.notes or "")
    if not c:
        print(f"NOT FOUND: {args.id}")
        return 1
    print(f"REJECTED: {c.id}")
    return 0


def cmd_retrieve(args: argparse.Namespace) -> int:
    from agent6_qualifier.knowledge.models import RetrievalContext
    from agent6_qualifier.knowledge.retriever import retrieve_for_turn

    ctx = RetrievalContext(
        agent=args.agent,
        intent=args.intent or "",
        object_source=args.source or "",
        objection=args.objection or "",
        situation=args.situation or "",
        message_text=args.message or "",
        current_object=args.object or "",
        long_term=True if args.long_term else None,
    )
    result = retrieve_for_turn(ctx, max_entries=args.limit)
    print("knowledge_selected:", result.selected_ids)
    print("fallback:", result.fallback, result.reason)
    if args.verbose:
        print(result.compact_prompt())
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Open Home two-file knowledge CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("update").set_defaults(func=cmd_update)
    sub.add_parser("validate").set_defaults(func=cmd_validate)
    sub.add_parser("rebuild-index").set_defaults(func=cmd_rebuild)

    pl = sub.add_parser("list")
    pl.add_argument("--status", default=None)
    pl.set_defaults(func=cmd_list)

    ps = sub.add_parser("show")
    ps.add_argument("id")
    ps.set_defaults(func=cmd_show)

    pa = sub.add_parser("approve")
    pa.add_argument("id")
    pa.add_argument("--notes", default="")
    pa.add_argument("--force-playbook", action="store_true")
    pa.set_defaults(func=cmd_approve)

    pr = sub.add_parser("reject")
    pr.add_argument("id")
    pr.add_argument("--notes", default="")
    pr.set_defaults(func=cmd_reject)

    pret = sub.add_parser("retrieve")
    pret.add_argument("--agent", default="AGENT6")
    pret.add_argument("--intent", default="")
    pret.add_argument("--source", default="")
    pret.add_argument("--objection", default="")
    pret.add_argument("--situation", default="")
    pret.add_argument("--message", default="")
    pret.add_argument("--object", default="")
    pret.add_argument("--long-term", action="store_true")
    pret.add_argument("--limit", type=int, default=6)
    pret.add_argument("--verbose", action="store_true")
    pret.set_defaults(func=cmd_retrieve)

    args = p.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

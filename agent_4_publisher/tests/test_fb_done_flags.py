#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from fb_groups_pipeline import (  # noqa: E402
    canonical_group_url,
    check_rate_limits,
    continue_after_group_error,
    cycle_is_complete,
    ensure_assignment,
    groups_for_cycle,
    groups_per_object,
    pick_pause_minutes,
    posted_group_keys_from_log,
    posted_group_keys_from_state,
    should_stop_fb_queue,
    sort_queue_resume_first,
    take_round_robin,
)


G1 = "https://www.facebook.com/groups/aaa/"
G2 = "https://www.facebook.com/groups/bbb"
G3 = "https://www.facebook.com/groups/ccc/"
GROUPS = [{"url": G1}, {"url": G2}, {"url": G3}]


def test_canonical_strips_slash_and_query():
    assert canonical_group_url("https://www.facebook.com/groups/aaa/?ref=share") == (
        "https://www.facebook.com/groups/aaa"
    )


def test_posted_keys_from_log():
    log = (
        "2026-08-20 01:00 https://www.facebook.com/groups/aaa/ -> https://facebook.com/posts/1\n"
        "2026-08-20 01:10 https://www.facebook.com/groups/bbb -> pending_approval\n"
    )
    keys = posted_group_keys_from_log(log, [G1, G2, G3])
    assert canonical_group_url(G1) in keys
    assert canonical_group_url(G2) in keys
    assert canonical_group_url(G3) not in keys


def test_remaining_when_partial():
    posted = {canonical_group_url(G1)}
    remaining, new_cycle = groups_for_cycle(GROUPS, posted, done=False, force=False)
    assert new_cycle is False
    assert [g["url"] for g in remaining] == [G2, G3]


def test_new_cycle_when_unchecked_but_log_full():
    posted = {canonical_group_url(u) for u in (G1, G2, G3)}
    remaining, new_cycle = groups_for_cycle(GROUPS, posted, done=False, force=False)
    assert new_cycle is True
    assert [g["url"] for g in remaining] == [G1, G2, G3]


def test_done_true_skips_without_force():
    remaining, new_cycle = groups_for_cycle(GROUPS, set(), done=True, force=False)
    assert remaining == []
    assert new_cycle is False


def test_force_reposts_all():
    posted = {canonical_group_url(G1)}
    remaining, new_cycle = groups_for_cycle(GROUPS, posted, done=True, force=True)
    assert new_cycle is True
    assert len(remaining) == 3


def test_cycle_complete_only_when_all_present():
    keys = {canonical_group_url(G1), canonical_group_url(G2)}
    assert cycle_is_complete(GROUPS, keys) is False
    keys.add(canonical_group_url(G3))
    assert cycle_is_complete(GROUPS, keys) is True


def test_continue_after_group_error_skips_composer_failures():
    cfg = {"continue_on_group_error": True}
    assert continue_after_group_error(cfg, RuntimeError("COMPOSER_NOT_FOUND: нет поля"))
    assert continue_after_group_error(cfg, RuntimeError("JOIN_PENDING: заявка"))
    assert continue_after_group_error(cfg, TimeoutError("Timeout 30000ms exceeded"))


def test_continue_after_group_error_stops_on_dead_session():
    cfg = {"continue_on_group_error": True}
    assert continue_after_group_error(cfg, RuntimeError("AUTH_REQUIRED: логин не удался")) is False


def test_continue_after_group_error_can_be_disabled():
    cfg = {"continue_on_group_error": False}
    assert continue_after_group_error(cfg, RuntimeError("COMPOSER_NOT_FOUND")) is False


def test_pick_pause_minutes_chooses_from_list():
    allowed = {2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0}
    cfg = {"limits": {"minutes_between_groups": [2, 2.5, 3, 3.5, 4, 4.5, 5]}}
    seen = {pick_pause_minutes(cfg) for _ in range(120)}
    assert seen <= allowed
    assert len(seen) >= 2


def test_should_stop_queue_only_for_account_and_object_gap():
    assert should_stop_fb_queue("предохранитель: перерыв между FB-сессиями") is True
    assert should_stop_fb_queue("лимит: дневной лимит 8 объектов исчерпан") is True
    assert should_stop_fb_queue("лимит: пауза между объектами 30 мин не выдержана") is True
    assert should_stop_fb_queue(
        "лимит: этот объект в эту группу уже постили за последние 30 мин"
    ) is False
    assert should_stop_fb_queue("fb_groups_locked") is False
    assert should_stop_fb_queue("phone_fb_groups_done") is False


def _cfg_limits(**extra):
    limits = {
        "max_posts_per_day_total": 8,
        "minutes_between_objects": [30, 35],
        "min_minutes_same_object_same_group": 30,
    }
    limits.update(extra)
    return {"limits": limits}


def test_different_object_can_use_same_group(monkeypatch):
    import fb_groups_pipeline as fgp
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    state = {
        "posts": [
            {
                "ts": (now - timedelta(minutes=40)).isoformat(),
                "group": G1,
                "object_id": "A_1",
            }
        ]
    }
    monkeypatch.setattr(fgp, "load_state", lambda cfg: state)
    assert check_rate_limits(_cfg_limits(), G1, object_id="A_2") is None


def test_same_object_same_group_waits_30_min(monkeypatch):
    import fb_groups_pipeline as fgp
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    state = {
        "posts": [
            {
                "ts": (now - timedelta(minutes=10)).isoformat(),
                "group": G1,
                "object_id": "A_1",
            }
        ]
    }
    monkeypatch.setattr(fgp, "load_state", lambda cfg: state)
    reason = check_rate_limits(_cfg_limits(), G1, object_id="A_1")
    assert reason and "этот объект в эту группу" in reason
    assert check_rate_limits(_cfg_limits(), G2, object_id="A_1") is None


def test_new_object_waits_30_min_after_previous(monkeypatch):
    import fb_groups_pipeline as fgp
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    state = {
        "posts": [
            {
                "ts": (now - timedelta(minutes=10)).isoformat(),
                "group": G2,
                "object_id": "A_1",
            }
        ]
    }
    monkeypatch.setattr(fgp, "load_state", lambda cfg: state)
    reason = check_rate_limits(_cfg_limits(), G1, object_id="A_2")
    assert reason and "пауза между объектами" in reason
    assert check_rate_limits(_cfg_limits(), G3, object_id="A_1") is None


def test_resume_first_puts_partial_object_ahead(monkeypatch):
    import fb_groups_pipeline as fgp

    state = {
        "posts": [
            {"object_id": "A_003", "group": G1},
            {"object_id": "A_003", "group": G2},
        ]
    }
    monkeypatch.setattr(fgp, "load_state", lambda cfg: state)

    def fake_prop(page, field, _kind):
        return page["_oid"] if field == "object_id" else ""

    monkeypatch.setattr(fgp.pp, "get_prop", fake_prop)
    pages = [
        {"id": "new", "_oid": "A_001"},
        {"id": "partial", "_oid": "A_003"},
    ]
    ordered = sort_queue_resume_first(
        pages, {}, GROUPS, {"object_id": "object_id"}
    )
    assert [p["id"] for p in ordered] == ["partial", "new"]


def test_posted_keys_from_state_match_object(tmp_path, monkeypatch):
    import fb_groups_pipeline as fgp

    state = {
        "posts": [
            {"object_id": "A_1", "group": "https://www.facebook.com/groups/aaa/"},
            {"object_id": "A_2", "group": "https://www.facebook.com/groups/bbb/"},
        ]
    }
    monkeypatch.setattr(fgp, "load_state", lambda cfg: state)
    keys = posted_group_keys_from_state({}, "A_1")
    assert canonical_group_url(G1) in keys
    assert canonical_group_url(G2) not in keys


ROTATION_CFG = {
    "limits": {
        "daily_group_posts_budget": 50,
        "min_groups_per_object": 4,
        "max_groups_per_object": 12,
        "sparse_queue_max_groups": 20,
        "sparse_queue_objects": 2,
    }
}


def test_groups_per_object_five_fill_fifty_pool():
    n = groups_per_object(
        ROTATION_CFG, waiting=5, remaining_budget=50, pool=50
    )
    assert n == 10


def test_groups_per_object_sixth_starts_smaller_slice():
    n = groups_per_object(
        ROTATION_CFG, waiting=6, remaining_budget=50, pool=50
    )
    assert n == 8


def test_groups_per_object_sparse_queue_uses_higher_cap():
    n = groups_per_object(
        ROTATION_CFG, waiting=1, remaining_budget=50, pool=50
    )
    assert n == 20


def test_groups_per_object_one_object_small_pool_takes_all():
    n = groups_per_object(
        ROTATION_CFG, waiting=1, remaining_budget=50, pool=15
    )
    assert n == 15


def test_groups_per_object_many_objects_stay_within_budget():
    n = groups_per_object(
        ROTATION_CFG, waiting=15, remaining_budget=50, pool=50
    )
    assert n == 3


def test_take_round_robin_wraps():
    g4 = {"url": "https://www.facebook.com/groups/ddd/"}
    pool = GROUPS + [g4]
    first, cursor = take_round_robin(pool, 3, 2)
    assert [g["url"] for g in first] == [g4["url"], G1]
    second, cursor = take_round_robin(pool, cursor, 2)
    assert [canonical_group_url(g["url"]) for g in second] == [
        canonical_group_url(G2),
        canonical_group_url(G3),
    ]


def test_ensure_assignment_reuses_saved_slice(monkeypatch):
    import fb_groups_pipeline as fgp

    state = {
        "posts": [],
        "group_cursor": 2,
        "assignments": {"A_1": {"urls": [G1, G2]}},
    }
    monkeypatch.setattr(fgp, "load_state", lambda cfg: state)
    saved = []
    monkeypatch.setattr(fgp, "save_state", lambda cfg, st: saved.append(st))
    assigned, new_cycle = ensure_assignment(
        ROTATION_CFG,
        "A_1",
        GROUPS,
        set(),
        waiting=5,
        force=False,
        persist=True,
    )
    assert new_cycle is False
    assert [g["url"] for g in assigned] == [G1, G2]
    assert saved == []


def test_ensure_assignment_advances_cursor(monkeypatch):
    import fb_groups_pipeline as fgp

    state = {"posts": [], "group_cursor": 0, "assignments": {}}
    monkeypatch.setattr(fgp, "load_state", lambda cfg: state)
    monkeypatch.setattr(fgp, "posts_today_count", lambda cfg: 0)
    monkeypatch.setattr(fgp, "save_state", lambda cfg, st: None)
    assigned, new_cycle = ensure_assignment(
        ROTATION_CFG,
        "A_NEW",
        GROUPS,
        set(),
        waiting=5,
        force=False,
        persist=True,
    )
    assert new_cycle is True
    assert len(assigned) == 3  # pool=3 < computed 10
    assert state["group_cursor"] == 0  # wrapped exactly one full pool
    assert state["assignments"]["A_NEW"]["urls"]

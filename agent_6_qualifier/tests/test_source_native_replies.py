"""Source-native reply correlation → common parse_owner_reply path."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.models import LeadProfile, Listing
from agent6_qualifier.qualifier import Session
from agent7_envoy.messaging.inbox_watcher import (
    InboundOwnerMessage,
    airbnb_inbox_watcher,
    facebook_inbox_watcher,
)
from agent7_envoy.owner_request_store import OwnerRequestStore
from agent7_envoy.owner_result import OwnerVerdict


def _session(chat_id: str, object_id: str) -> Session:
    lead = LeadProfile(
        check_in=date(2026, 8, 1),
        check_out=date(2026, 9, 1),
        guests=2,
        preferred_object_id=object_id,
    )
    chosen = Listing(object_id=object_id, title="Villa", source_url="https://x")
    return Session(chat_id=chat_id, lead=lead, chosen=chosen, awaiting_owner=True)


def test_facebook_owner_free_reply_correlated(tmp_path):
    store = OwnerRequestStore(path=tmp_path / "reqs.json")
    req = store.create(
        object_id="F_10",
        client_session_chat_id="wa_client_1",
        channel="facebook_messenger",
        source="FACEBOOK",
        source_url="https://www.facebook.com/marketplace/item/10",
        outbound_message="Agent7 outbound",
    )
    store.mark_sent(req.owner_request_id, external_thread_id="fb-t-10")
    store.mark_awaiting(req.owner_request_id)

    seen = {}

    def process_reply(inbound: InboundOwnerMessage, owner_req):
        # Simulate common parser path (no Gemini in unit test)
        verdict = OwnerVerdict(status="free")
        seen["verdict"] = verdict.status
        seen["client"] = inbound.client_session_chat_id
        seen["object_id"] = inbound.object_id
        store.mark_message_processed(
            owner_req.owner_request_id,
            inbound.message_id,
            verdict=verdict.status,
        )

    watcher = facebook_inbox_watcher(
        request_store=store,
        fetch_candidates=lambda _reqs: [
            {
                "thread_id": "fb-t-10",
                "text": "Да, свободно на эти даты",
                "timestamp": "2026-08-01T10:00:00Z",
                "direction": "inbound",
            }
        ],
        process_reply=process_reply,
    )
    result = watcher.poll()
    assert result.processed == 1
    assert seen["verdict"] == "free"
    assert seen["client"] == "wa_client_1"
    assert seen["object_id"] == "F_10"


def test_airbnb_busy_and_conditions(tmp_path):
    store = OwnerRequestStore(path=tmp_path / "reqs.json")
    req = store.create(
        object_id="A_10",
        client_session_chat_id="tg_client_9",
        channel="airbnb_messages",
        source="AIRBNB",
        source_url="https://www.airbnb.com/rooms/10",
        outbound_message="Можно посмотреть дом",
    )
    store.mark_sent(req.owner_request_id, external_thread_id="ab-t-10")
    store.mark_awaiting(req.owner_request_id)

    verdicts = []

    def process_reply(inbound: InboundOwnerMessage, owner_req):
        text = inbound.text.lower()
        if "занято" in text:
            status = "busy"
        elif "цена" in text:
            status = "conditions_changed"
        else:
            status = "free"
        verdicts.append(status)
        store.mark_message_processed(
            owner_req.owner_request_id, inbound.message_id, verdict=status
        )

    watcher = airbnb_inbox_watcher(
        request_store=store,
        fetch_candidates=lambda _reqs: [
            {
                "thread_id": "ab-t-10",
                "text": "Занято до ноября",
                "timestamp": "t1",
                "direction": "inbound",
            }
        ],
        process_reply=process_reply,
    )
    assert watcher.poll().processed == 1
    assert verdicts == ["busy"]

    # conditions_changed on same open request
    store.mark_awaiting(req.owner_request_id)
    watcher2 = airbnb_inbox_watcher(
        request_store=store,
        fetch_candidates=lambda _reqs: [
            {
                "thread_id": "ab-t-10",
                "text": "Можно, но цена 60000",
                "timestamp": "t2",
                "direction": "inbound",
            }
        ],
        process_reply=process_reply,
    )
    assert watcher2.poll().processed == 1
    assert verdicts[-1] == "conditions_changed"


def test_duplicate_reply_deduped(tmp_path):
    store = OwnerRequestStore(path=tmp_path / "reqs.json")
    req = store.create(
        object_id="F_2",
        client_session_chat_id="c1",
        channel="facebook_messenger",
        source="FACEBOOK",
        source_url="https://www.facebook.com/marketplace/item/2",
    )
    store.mark_sent(req.owner_request_id, external_thread_id="t2")
    store.mark_awaiting(req.owner_request_id)

    candidates = [
        {
            "thread_id": "t2",
            "text": "свободно",
            "timestamp": "same",
            "direction": "inbound",
            "external_id": "m1",
        }
    ]
    w = facebook_inbox_watcher(
        request_store=store, fetch_candidates=lambda _r: candidates
    )
    assert w.poll().processed == 1
    r2 = w.poll()
    assert r2.processed == 0
    assert r2.skipped_duplicate == 1


def test_ambiguous_correlation(tmp_path):
    store = OwnerRequestStore(path=tmp_path / "reqs.json")
    for oid, client in (("F_a", "c1"), ("F_b", "c2")):
        req = store.create(
            object_id=oid,
            client_session_chat_id=client,
            channel="facebook_messenger",
            source="FACEBOOK",
            source_url=f"https://www.facebook.com/marketplace/item/{oid}",
        )
        # Same thread id on purpose → ambiguous
        store.mark_sent(req.owner_request_id, external_thread_id="shared")
        store.mark_awaiting(req.owner_request_id)

    w = facebook_inbox_watcher(
        request_store=store,
        fetch_candidates=lambda _r: [
            {
                "thread_id": "shared",
                "text": "ok",
                "timestamp": "t",
                "direction": "inbound",
            }
        ],
    )
    result = w.poll()
    assert result.ambiguous == 1
    assert result.processed == 0


def test_human_outbound_not_treated_as_agent7(tmp_path):
    store = OwnerRequestStore(path=tmp_path / "reqs.json")
    req = store.create(
        object_id="A_3",
        client_session_chat_id="c3",
        channel="airbnb_messages",
        source="AIRBNB",
        source_url="https://www.airbnb.com/rooms/3",
        outbound_message="Можно посмотреть дом",
    )
    store.mark_sent(req.owner_request_id, external_thread_id="ab3")
    store.mark_awaiting(req.owner_request_id)

    w = airbnb_inbox_watcher(
        request_store=store,
        fetch_candidates=lambda _r: [
            {
                "thread_id": "ab3",
                "text": "Human typed something else",
                "direction": "outbound",
            }
        ],
    )
    result = w.poll()
    assert result.skipped_human_outbound == 1
    assert result.processed == 0


def test_exact_client_session_continuation(tmp_path):
    store = OwnerRequestStore(path=tmp_path / "reqs.json")
    req = store.create(
        object_id="F_99",
        client_session_chat_id="wa_exact_99",
        channel="facebook_messenger",
        source="FACEBOOK",
        source_url="https://www.facebook.com/marketplace/item/99",
    )
    store.mark_sent(req.owner_request_id, external_thread_id="fb99")
    store.mark_awaiting(req.owner_request_id)

    continued = {}

    def process_reply(inbound, owner_req):
        sess = _session(inbound.client_session_chat_id, inbound.object_id)
        continued["chat_id"] = sess.chat_id
        store.mark_message_processed(owner_req.owner_request_id, inbound.message_id)

    facebook_inbox_watcher(
        request_store=store,
        fetch_candidates=lambda _r: [
            {
                "thread_id": "fb99",
                "text": "free",
                "timestamp": "1",
                "direction": "inbound",
            }
        ],
        process_reply=process_reply,
    ).poll()
    assert continued["chat_id"] == "wa_exact_99"


def test_unrelated_thread_ignored(tmp_path):
    store = OwnerRequestStore(path=tmp_path / "reqs.json")
    req = store.create(
        object_id="A_7",
        client_session_chat_id="c7",
        channel="airbnb_messages",
        source="AIRBNB",
        source_url="https://www.airbnb.com/rooms/7",
    )
    store.mark_sent(req.owner_request_id, external_thread_id="wanted")
    store.mark_awaiting(req.owner_request_id)

    w = airbnb_inbox_watcher(
        request_store=store,
        fetch_candidates=lambda _r: [
            {
                "thread_id": "support-unrelated",
                "text": "Your reservation",
                "direction": "inbound",
            }
        ],
    )
    result = w.poll()
    assert result.skipped_unrelated == 1
    assert result.processed == 0

"""Production behavior: dedup, polling config, follow-up, pagination, alerts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent9_connector.config_loader import load_connector_config
from agent9_connector.main import ConnectorRuntime, FOLLOW_UP_SECONDS
from agent9_connector.notion import NotionClient, NotionListing, is_eligible, parse_page
from agent9_connector.state_machine import BusinessState
from agent9_connector.state_store import ConversationState


def test_notion_poll_interval_900():
    load_connector_config.cache_clear()
    cfg = load_connector_config()
    assert cfg.get("notion_poll_seconds") == 900


def test_conversation_poll_interval_120():
    load_connector_config.cache_clear()
    cfg = load_connector_config()
    assert cfg.get("conversation_poll_seconds") == 120


class TestObjectIdDedup:
    def test_existing_object_id_not_enqueued(self, tmp_store):
        tmp_store.save(ConversationState(object_id="F_1", state=BusinessState.WAITING_CONTACT.value))
        assert not tmp_store.enqueue("F_1", {"listing_id": "111", "facebook_url": "u"})

    def test_discover_skips_existing_object(self, tmp_store, sample_page):
        listing = parse_page(sample_page)
        tmp_store.save(ConversationState(object_id=listing.object_id, state=BusinessState.NEW.value))
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        rt.notion = MagicMock()
        rt.notion.query_eligible.return_value = [listing]
        assert rt.discover_notion() == 0


class TestListingIdDedup:
    def test_duplicate_listing_in_queue_blocked(self, tmp_store):
        assert tmp_store.enqueue("F_1", {"listing_id": "999", "facebook_url": "u"})
        assert not tmp_store.enqueue("F_2", {"listing_id": "999", "facebook_url": "u"})

    def test_duplicate_listing_in_conversation_blocked(self, tmp_store):
        tmp_store.save(ConversationState(
            object_id="F_1", listing_id="999", state=BusinessState.COMPLETE.value,
        ))
        blocked, reason = tmp_store.listing_id_blocked("999", exclude_object_id="F_2")
        assert blocked
        assert "conversation" in reason
        assert not tmp_store.enqueue("F_2", {"listing_id": "999", "facebook_url": "u"})

    def test_terminal_states_block_listing(self, tmp_store):
        for state in (BusinessState.DECLINED, BusinessState.MANUAL_REVIEW):
            tmp_store.save(ConversationState(object_id="F_x", listing_id="555", state=state.value))
            blocked, _ = tmp_store.listing_id_blocked("555")
            assert blocked


class TestNoSecondIntro:
    def test_waiting_contact_no_second_intro(self, tmp_store):
        conv = ConversationState(
            object_id="F_1",
            listing_id="123",
            facebook_url="https://facebook.com/marketplace/item/123",
            intro_sent_at="2026-01-01T00:00:00+00:00",
            state=BusinessState.WAITING_CONTACT.value,
            thread_id="t1",
        )
        tmp_store.save(conv)
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        sends_before = len(rt.facebook._sent)
        rt._start_outreach(conv)
        assert len(rt.facebook._sent) == sends_before

    def test_restart_no_second_intro(self, tmp_store):
        conv = ConversationState(
            object_id="F_1",
            listing_id="123",
            facebook_url="https://facebook.com/marketplace/item/123",
            intro_sent_at=datetime.now(timezone.utc).isoformat(),
            state=BusinessState.WAITING_CONTACT.value,
            thread_id="t1",
        )
        tmp_store.save(conv)
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        rt._start_outreach(rt.store.get("F_1"))
        assert rt.store.get("F_1").state == BusinessState.WAITING_CONTACT.value


class TestTerminalNoEnqueue:
    def test_complete_not_enqueued(self, tmp_store, sample_page):
        listing = parse_page(sample_page)
        tmp_store.save(ConversationState(
            object_id=listing.object_id, listing_id=listing.listing_id,
            state=BusinessState.COMPLETE.value,
        ))
        assert not tmp_store.enqueue(listing.object_id, {"listing_id": listing.listing_id})

    def test_declined_not_enqueued(self, tmp_store):
        tmp_store.save(ConversationState(object_id="F_1", listing_id="1", state=BusinessState.DECLINED.value))
        assert not tmp_store.enqueue("F_1", {"listing_id": "1"})

    def test_manual_review_not_enqueued(self, tmp_store):
        tmp_store.save(ConversationState(object_id="F_1", listing_id="1", state=BusinessState.MANUAL_REVIEW.value))
        assert not tmp_store.enqueue("F_1", {"listing_id": "1"})


class TestPartialNotionData:
    def test_wa_exists_role_missing_eligible(self, sample_page):
        sample_page["properties"]["WhatsApp контакт"] = {
            "rich_text": [{"plain_text": "+66812345678"}],
        }
        listing = parse_page(sample_page)
        assert listing and is_eligible(listing)

    def test_role_exists_wa_missing_eligible(self, sample_page):
        sample_page["properties"]["Агент/Владелец (тип)"] = {"select": {"name": "Владелец"}}
        listing = parse_page(sample_page)
        assert listing and is_eligible(listing)

    def test_wa_only_sends_role_question(self, tmp_store):
        conv = ConversationState(
            object_id="F_1",
            listing_id="123",
            facebook_url="https://facebook.com/marketplace/item/123",
            whatsapp_existing="+66812345678",
            property_type="Вилла",
            state=BusinessState.NEW.value,
        )
        tmp_store.save(conv)
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        from agent9_connector.state_machine import BrowserScreenState
        rt.facebook.set_screen_state(BrowserScreenState.MESSAGE_INPUT_READY)
        rt._start_outreach(conv)
        updated = rt.store.get("F_1")
        assert updated.state == BusinessState.WAITING_ROLE.value
        assert updated.role_question_sent_at


class TestFollowUp:
    def test_one_follow_up_after_24h(self, tmp_store):
        old = (datetime.now(timezone.utc) - timedelta(seconds=FOLLOW_UP_SECONDS + 60)).isoformat()
        conv = ConversationState(
            object_id="F_1",
            listing_id="123",
            intro_sent_at=old,
            state=BusinessState.WAITING_CONTACT.value,
            thread_id="t1",
        )
        tmp_store.save(conv)
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        from agent9_connector.state_machine import BrowserScreenState
        rt.facebook.set_screen_state(BrowserScreenState.MESSAGE_INPUT_READY)
        rt._maybe_follow_up(rt.store.get("F_1"))
        updated = rt.store.get("F_1")
        assert updated.follow_up_sent_at
        assert updated.attempt >= 2

    def test_no_second_follow_up(self, tmp_store):
        old = (datetime.now(timezone.utc) - timedelta(seconds=FOLLOW_UP_SECONDS + 60)).isoformat()
        conv = ConversationState(
            object_id="F_1",
            listing_id="123",
            intro_sent_at=old,
            follow_up_sent_at=old,
            attempt=2,
            state=BusinessState.WAITING_CONTACT.value,
            thread_id="t1",
        )
        tmp_store.save(conv)
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        from agent9_connector.state_machine import BrowserScreenState
        rt.facebook.set_screen_state(BrowserScreenState.MESSAGE_INPUT_READY)
        sends_before = sum(len(v) for v in rt.facebook._sent.values())
        rt._maybe_follow_up(rt.store.get("F_1"))
        sends_after = sum(len(v) for v in rt.facebook._sent.values())
        assert sends_after == sends_before


class TestErrorNotifyDedup:
    def test_login_required_alert_cooldown(self, tmp_path, monkeypatch):
        import agent9_connector.config_loader as cfg
        import agent9_connector.error_notify as en
        monkeypatch.setattr(cfg, "data_dir", lambda: tmp_path)
        monkeypatch.setattr(en, "data_dir", lambda: tmp_path)
        monkeypatch.setenv("ERROR_BOT_TOKEN", "tok")
        monkeypatch.setenv("ERROR_CHAT_ID", "1")
        monkeypatch.setenv("AGENT9_ERROR_NOTIFY_COOLDOWN_SEC", "3600")
        calls = []
        monkeypatch.setattr(
            en.urllib.request, "urlopen",
            lambda *a, **k: calls.append(1),
        )
        assert en.notify("first", tag=en.TAG_LOGIN_REQUIRED)
        assert not en.notify("second", tag=en.TAG_LOGIN_REQUIRED)
        assert len(calls) == 1


class TestNotionPagination:
    def test_query_all_pages_over_100(self, monkeypatch):
        client = NotionClient(token="t", database_id="db")
        pages = [{"id": f"p{i}"} for i in range(150)]
        batch1 = pages[:100]
        batch2 = pages[100:]

        def fake_req(method, path, **kwargs):
            if method == "POST" and path.endswith("/query"):
                body = kwargs.get("json") or {}
                if body.get("start_cursor"):
                    return {"results": batch2, "has_more": False}
                return {
                    "results": batch1,
                    "has_more": True,
                    "next_cursor": "cursor2",
                }
            return {"properties": {}}

        monkeypatch.setattr(client, "_req", fake_req)
        all_pages = client.query_all_pages()
        assert len(all_pages) == 150


class TestInboundDedup:
    def test_seen_inbound_not_reprocessed(self, tmp_store):
        conv = ConversationState(
            object_id="F_1",
            state=BusinessState.WAITING_CONTACT.value,
            thread_id="t1",
            seen_inbound_snippets=["t1:hello"],
        )
        tmp_store.save(conv)
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        rt._handle_inbound(conv, "hello")
        assert rt.store.get("F_1").state == BusinessState.WAITING_CONTACT.value


class TestQueueSanitize:
    def test_sanitize_removes_duplicates(self, tmp_store):
        tmp_store._write_queue({
            "F_1": {"listing_id": "999"},
            "F_2": {"listing_id": "999"},
            "F_4": {"listing_id": "888"},
        })
        tmp_store.save(ConversationState(object_id="F_3", listing_id="888", state=BusinessState.COMPLETE.value))
        removed = tmp_store.sanitize_queue()
        assert removed >= 2
        queue = tmp_store.read_queue()
        listing_ids = [v.get("listing_id") for v in queue.values()]
        assert listing_ids.count("999") == 1
        assert "888" not in listing_ids

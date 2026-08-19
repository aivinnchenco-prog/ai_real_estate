"""Notion, queue, browser offline tests."""

from __future__ import annotations

from agent9_connector.connectors.facebook import FacebookConnector
from agent9_connector.notion import is_eligible, parse_page, resolve_facebook_url
from agent9_connector.state_machine import BusinessState
from agent9_connector.state_store import ConversationState


class TestNotion:
    def test_eligible_facebook(self, sample_page):
        listing = parse_page(sample_page)
        assert listing and is_eligible(listing)

    def test_non_facebook_ignored(self, sample_page):
        sample_page["properties"]["Объект ID"]["rich_text"][0]["plain_text"] = "A_1"
        sample_page["properties"]["Источник объявления"]["url"] = "https://airbnb.com/x"
        listing = parse_page(sample_page)
        assert listing and not is_eligible(listing)

    def test_result_already_in_notion_ignored(self, sample_page):
        sample_page["properties"]["WhatsApp контакт"] = {
            "rich_text": [{"plain_text": "+66812345678"}],
        }
        sample_page["properties"]["Агент/Владелец (тип)"] = {
            "select": {"name": "Владелец"},
        }
        listing = parse_page(sample_page)
        assert listing and not is_eligible(listing)

    def test_resolve_url(self):
        assert resolve_facebook_url("https://facebook.com/marketplace/item/1") == (
            "https://facebook.com/marketplace/item/1"
        )

    def test_source_url_only(self, sample_page):
        sample_page["properties"]["Источник объявления"]["url"] = (
            "https://www.facebook.com/marketplace/item/1515735863510647"
        )
        listing = parse_page(sample_page)
        assert listing
        assert listing.listing_id == "1515735863510647"
        assert is_eligible(listing)


class TestQueue:
    def test_duplicate_poll_one_job(self, tmp_store):
        assert tmp_store.enqueue("F_1", {"facebook_url": "u"})
        assert not tmp_store.enqueue("F_1", {"facebook_url": "u"})

    def test_waiting_contact_restart(self, tmp_store):
        c = ConversationState(object_id="F_1", state=BusinessState.WAITING_CONTACT.value)
        tmp_store.save(c)
        assert tmp_store.get("F_1").state == BusinessState.WAITING_CONTACT.value

    def test_waiting_role_restart(self, tmp_store):
        c = ConversationState(object_id="F_1", state=BusinessState.WAITING_ROLE.value)
        tmp_store.save(c)
        assert tmp_store.get("F_1").state == BusinessState.WAITING_ROLE.value

    def test_declined_restart(self, tmp_store):
        c = ConversationState(object_id="F_1", state=BusinessState.DECLINED.value)
        tmp_store.save(c)
        assert tmp_store.get("F_1").state == BusinessState.DECLINED.value

    def test_manual_review_no_auto_send(self, tmp_store):
        c = ConversationState(object_id="F_1", state=BusinessState.MANUAL_REVIEW.value, thread_id="t")
        tmp_store.save(c)
        from agent9_connector.main import ConnectorRuntime
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        rt.facebook.mock_mode = True
        rt.facebook.push_inbound("t", "hello")
        rt.poll_active_conversations()
        assert tmp_store.get("F_1").state == BusinessState.MANUAL_REVIEW.value


class TestBrowser:
    def test_message_button_detected(self):
        fb = FacebookConnector()
        fb.set_screen_state(__import__("agent9_connector.state_machine", fromlist=["BrowserScreenState"]).BrowserScreenState.MESSAGE_BUTTON_AVAILABLE)
        r = fb.send_message("hi", listing_id="123")
        assert r.ok

    def test_input_ready(self):
        fb = FacebookConnector()
        fb.set_screen_state(__import__("agent9_connector.state_machine", fromlist=["BrowserScreenState"]).BrowserScreenState.MESSAGE_INPUT_READY)
        assert fb.send_message("hi", listing_id="1").ok

    def test_unknown_no_send(self):
        fb = FacebookConnector()
        assert not fb.send_message("hi", listing_id="1").ok

    def test_login_pause(self):
        fb = FacebookConnector()
        state = fb.open_listing("https://facebook.com/login", "1")
        assert state == "LOGIN_REQUIRED"
        assert not fb.send_message("hi", listing_id="1").ok

    def test_checkpoint_pause(self):
        fb = FacebookConnector()
        state = fb.open_listing("https://facebook.com/checkpoint", "1")
        assert state == "CHECKPOINT"

    def test_thread_binding(self):
        tid, url = FacebookConnector.bind_thread("123", "https://facebook.com/messages/t/999")
        assert tid == "999"
        fb = FacebookConnector()
        from agent9_connector.state_machine import BrowserScreenState
        fb.set_screen_state(BrowserScreenState.MESSAGE_INPUT_READY)
        fb.send_message("intro", listing_id="123")
        assert fb._listing_threads["123"]

    def test_sent_history_check(self):
        fb = FacebookConnector()
        from agent9_connector.state_machine import BrowserScreenState
        fb.set_screen_state(BrowserScreenState.MESSAGE_INPUT_READY)
        fb.send_message("Hello Open Home", listing_id="1")
        tid = fb._listing_threads["1"]
        assert fb.thread_contains_sent_text(tid, "Hello Open Home")

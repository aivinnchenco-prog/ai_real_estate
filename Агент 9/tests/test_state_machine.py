"""State machine and anti-duplicate tests."""

from __future__ import annotations

from agent9_connector.gemini import GeminiClient
from agent9_connector.main import ConnectorRuntime
from agent9_connector.notion import NotionClient
from agent9_connector.state_machine import BusinessState
from agent9_connector.state_store import ConversationState


class TestStateMachine:
    def test_new_to_intro(self, tmp_store, monkeypatch):
        fb = __import__("agent9_connector.connectors.facebook", fromlist=["FacebookConnector"]).FacebookConnector()
        from agent9_connector.state_machine import BrowserScreenState
        fb.set_screen_state(BrowserScreenState.MESSAGE_INPUT_READY)
        notion = NotionClient()
        notion.update_outreach = lambda *a, **k: None
        rt = ConnectorRuntime(store=tmp_store, notion=notion, facebook=fb, gemini=__import__("agent9_connector.gemini", fromlist=["GeminiClient"]).GeminiClient(api_key=""), outreach_hour=[], outreach_day=[])
        conv = ConversationState(object_id="F_1", notion_page_id="p", facebook_url="https://facebook.com/marketplace/item/1", listing_id="1")
        tmp_store.save(conv)
        rt._start_outreach(conv)
        saved = tmp_store.get("F_1")
        assert saved.intro_sent_at
        assert saved.state == BusinessState.WAITING_CONTACT.value

    def test_restart_no_duplicate_intro(self, tmp_store):
        conv = ConversationState(object_id="F_1", intro_sent_at="t", state=BusinessState.WAITING_CONTACT.value, listing_id="1", facebook_url="u")
        tmp_store.save(conv)
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        rt._start_outreach(conv)
        assert len(rt.facebook._sent) == 0

    def test_contact_to_waiting_role(self, tmp_store):
        conv = ConversationState(object_id="F_1", state=BusinessState.WAITING_CONTACT.value, thread_id="t1", notion_page_id="p")
        tmp_store.save(conv)
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        rt.gemini = GeminiClient(api_key="")
        rt.notion.update_outreach = lambda *a, **k: None
        rt.facebook.push_inbound("t1", "WhatsApp +66812345678")
        rt.poll_active_conversations()
        saved = tmp_store.get("F_1")
        assert saved.whatsapp_normalized == "+66812345678"
        assert saved.state in (BusinessState.WAITING_ROLE.value, BusinessState.ROLE_ASKED.value)

    def test_restart_no_duplicate_role_question(self, tmp_store):
        conv = ConversationState(object_id="F_1", state=BusinessState.WAITING_ROLE.value, role_question_sent_at="x", thread_id="t1", notion_page_id="p")
        tmp_store.save(conv)
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        rt.gemini = GeminiClient(api_key="")
        rt.notion.update_outreach = lambda *a, **k: None
        rt._handle_inbound(conv, "owner")
        assert not any("собственник" in (m or "") for msgs in rt.facebook._sent.values() for m in msgs)

    def test_owner_complete(self, tmp_store):
        conv = ConversationState(object_id="F_1", state=BusinessState.WAITING_ROLE.value, thread_id="t1", notion_page_id="p")
        tmp_store.save(conv)
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        rt.gemini = GeminiClient(api_key="")
        rt.notion.update_outreach = lambda *a, **k: None
        rt._handle_inbound(conv, "я собственник")
        assert tmp_store.get("F_1").state == BusinessState.COMPLETE.value

    def test_agent_complete(self, tmp_store):
        conv = ConversationState(object_id="F_1", state=BusinessState.WAITING_ROLE.value, thread_id="t1", notion_page_id="p")
        tmp_store.save(conv)
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        rt.gemini = GeminiClient(api_key="")
        rt.notion.update_outreach = lambda *a, **k: None
        rt._handle_inbound(conv, "I am an agent")
        assert tmp_store.get("F_1").state == BusinessState.COMPLETE.value

    def test_decline(self, tmp_store):
        conv = ConversationState(object_id="F_1", state=BusinessState.WAITING_CONTACT.value, thread_id="t1", notion_page_id="p")
        tmp_store.save(conv)
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        rt.gemini = GeminiClient(api_key="")
        rt.notion.update_outreach = lambda *a, **k: None
        rt._handle_inbound(conv, "don't contact me")
        assert tmp_store.get("F_1").state == BusinessState.DECLINED.value

    def test_unknown_role_manual_review(self, tmp_store):
        conv = ConversationState(object_id="F_1", state=BusinessState.WAITING_ROLE.value, thread_id="t1", notion_page_id="p")
        tmp_store.save(conv)
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        rt.gemini = GeminiClient(api_key="")
        rt.notion.update_outreach = lambda *a, **k: None
        rt._handle_inbound(conv, "maybe later")
        assert tmp_store.get("F_1").state == BusinessState.MANUAL_REVIEW.value

    def test_waiting_contact_survives_restart(self, tmp_store):
        conv = ConversationState(object_id="F_1", state=BusinessState.WAITING_CONTACT.value, thread_id="t1")
        tmp_store.save(conv)
        rt = ConnectorRuntime.create()
        rt.store = tmp_store
        assert tmp_store.get("F_1").state == BusinessState.WAITING_CONTACT.value

    def test_complete_survives_restart(self, tmp_store):
        conv = ConversationState(object_id="F_1", state=BusinessState.COMPLETE.value)
        tmp_store.save(conv)
        assert tmp_store.get("F_1").state == BusinessState.COMPLETE.value

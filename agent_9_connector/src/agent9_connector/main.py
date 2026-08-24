"""Agent 9 orchestrator — fully independent background service.

Notion discovery (eligible FB objects) and Messenger conversation loops
run on separate timers. No coupling to Agent 6/7/8.
"""

import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .config_loader import facebook_profile_dir, load_connector_config
from .connectors.facebook import FacebookConnector
from .error_notify import alert_facebook_security
from .gemini import GeminiClient, confidence_action
from .logging_util import log_event
from .notion import NotionClient, OWNER_AGENT_TYPE_OPTIONS, whatsapp_write_allowed
from .policy import (
    apply_inbound_policy,
    follow_up_template,
    intro_template,
    reject_unsupported_generated_fact,
    role_question_template,
)
from .state_machine import (
    ACTIVE_CONVERSATION_STATES,
    BusinessState,
    TERMINAL_BUSINESS_STATES,
    next_after_intro_sent,
    next_after_role_asked,
)
from .state_store import ConversationState, StateStore
from .profile_lock import facebook_profile_lock

FOLLOW_UP_SECONDS = 24 * 3600
FB_SECURITY_SCREENS = frozenset({"LOGIN_REQUIRED", "CHECKPOINT", "ACCOUNT_PAUSED"})
NO_INTRO_STATES = frozenset({
    BusinessState.INTRO_SENT.value,
    BusinessState.WAITING_CONTACT.value,
    BusinessState.CONTACT_RECEIVED.value,
    BusinessState.ROLE_ASKED.value,
    BusinessState.WAITING_ROLE.value,
    BusinessState.COMPLETE.value,
    BusinessState.DECLINED.value,
    BusinessState.MANUAL_REVIEW.value,
})


def _parse_iso(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _inbound_key(thread_id: str, text: str) -> str:
    return f"{thread_id}:{(text or '').strip()}"


@dataclass
class ConnectorRuntime:
    store: StateStore
    notion: NotionClient
    facebook: FacebookConnector
    gemini: GeminiClient
    outreach_hour: list[float]
    outreach_day: list[float]

    @classmethod
    def create(cls) -> "ConnectorRuntime":
        cfg = load_connector_config()
        browser_cfg = bool(cfg.get("browser_enabled", False))
        env_browser = os.getenv("AGENT9_BROWSER_ENABLED", "").strip().lower()
        if env_browser in ("1", "true", "yes"):
            browser_enabled = True
        elif env_browser in ("0", "false", "no"):
            browser_enabled = False
        else:
            browser_enabled = browser_cfg
        return cls(
            store=StateStore(),
            notion=NotionClient(),
            facebook=FacebookConnector(
                mock_mode=not browser_enabled,
                headless=bool(cfg.get("browser_headless", False)),
            ),
            gemini=GeminiClient(),
            outreach_hour=[],
            outreach_day=[],
        )

    def discover_notion(self) -> int:
        self.store.sanitize_queue()
        enqueued = 0
        for listing in self.notion.query_eligible():
            if self.store.get(listing.object_id):
                log_event("object_dedup_skip", object_id=listing.object_id)
                continue
            blocked, reason = self.store.listing_id_blocked(
                listing.listing_id, exclude_object_id=listing.object_id,
            )
            if blocked:
                log_event(
                    "listing_dedup_skip",
                    object_id=listing.object_id,
                    listing_id=listing.listing_id,
                    reason=reason,
                )
                continue
            if self.store.enqueue(listing.object_id, {
                "notion_page_id": listing.page_id,
                "facebook_url": listing.facebook_url,
                "listing_id": listing.listing_id,
                "property_type": listing.property_type,
                "whatsapp_existing": listing.whatsapp_existing,
                "owner_agent_existing": listing.owner_agent_existing,
            }):
                enqueued += 1
        return enqueued

    def process_queue(self) -> int:
        if not self._rate_limit_ok():
            return 0
        queue = self.store.read_queue()
        if not queue:
            return 0
        object_id: str | None = None
        for oid in queue:
            item = queue[oid]
            if self.store.get(oid):
                self.store.dequeue(oid)
                continue
            lid = (item.get("listing_id") or "").strip()
            blocked, _ = self.store.listing_id_blocked(lid, exclude_object_id=oid)
            if blocked:
                self.store.dequeue(oid)
                continue
            object_id = oid
            break
        if not object_id:
            return 0
        item = self.store.dequeue(object_id)
        if not item:
            return 0
        state = ConversationState(
            object_id=object_id,
            notion_page_id=item.get("notion_page_id", ""),
            facebook_url=item.get("facebook_url", ""),
            listing_id=item.get("listing_id", ""),
            property_type=item.get("property_type", ""),
            whatsapp_existing=item.get("whatsapp_existing", ""),
            owner_agent_existing=item.get("owner_agent_existing", ""),
            state=BusinessState.NEW.value,
            outreach_status="opening_listing",
        )
        self.store.save(state)
        self._start_outreach(state)
        self._record_outreach()
        return 1

    def poll_active_conversations(self) -> int:
        handled = 0
        poll_states = {s.value for s in ACTIVE_CONVERSATION_STATES}
        poll_states.add(BusinessState.INTRO_SENT.value)
        for conv in self.store.list_active():
            if conv.state not in poll_states:
                continue
            self._maybe_follow_up(conv)
            if not conv.thread_id:
                continue
            for msg in self.facebook.poll_inbound(conv.thread_id):
                self._handle_inbound(conv, msg.text)
                handled += 1
                conv = self.store.get(conv.object_id) or conv
        return handled

    def _maybe_follow_up(self, conv: ConversationState) -> None:
        if conv.state != BusinessState.WAITING_CONTACT.value:
            return
        if conv.follow_up_sent_at or not conv.intro_sent_at:
            return
        if not conv.thread_id:
            return
        elapsed = time.time() - _parse_iso(conv.intro_sent_at)
        if elapsed < FOLLOW_UP_SECONDS:
            return
        msg = follow_up_template()
        snippet = msg[:30]
        if self.facebook.thread_contains_sent_text(conv.thread_id, snippet):
            conv.follow_up_sent_at = conv.updated_at
            conv.attempt = max(conv.attempt, 2)
            self.store.save(conv)
            return
        result = self.facebook.send_message(
            msg, thread_id=conv.thread_id, listing_id=conv.listing_id,
        )
        if result.ok:
            conv.follow_up_sent_at = conv.updated_at
            conv.attempt = 2
            self.store.save(conv)
            log_event("follow_up_sent", object_id=conv.object_id, thread_id=conv.thread_id)

    def _start_outreach(self, conv: ConversationState) -> None:
        if conv.intro_sent_at or conv.state in NO_INTRO_STATES:
            log_event("intro_skip_existing", object_id=conv.object_id, state=conv.state)
            return

        wa_existing = (conv.whatsapp_existing or "").strip()
        role_existing = (conv.owner_agent_existing or "").strip()
        has_wa = bool(wa_existing)
        has_role = role_existing in OWNER_AGENT_TYPE_OPTIONS

        if has_wa and has_role:
            conv.state = BusinessState.COMPLETE.value
            conv.outreach_status = "complete"
            self.store.save(conv)
            return

        if not conv.facebook_url or not conv.listing_id:
            conv.state = BusinessState.MANUAL_REVIEW.value
            conv.outreach_status = "manual_review"
            conv.last_error = "missing_facebook_url"
            self.store.save(conv)
            return

        screen = self.facebook.open_listing(conv.facebook_url, conv.listing_id)
        conv.screen_state = screen
        if screen in FB_SECURITY_SCREENS:
            alert_facebook_security(screen, object_id=conv.object_id)
            conv.state = BusinessState.MANUAL_REVIEW.value
            conv.outreach_status = "manual_review"
            conv.last_error = screen
            self.store.save(conv)
            return

        if has_wa and not has_role:
            first_msg = role_question_template()
            outreach_status = "waiting_role"
            next_state = BusinessState.WAITING_ROLE.value
            mark_role_question = True
        elif has_role and not has_wa:
            first_msg = intro_template(conv.property_type)
            outreach_status = "waiting_contact"
            next_state = next_after_intro_sent().value
            mark_role_question = False
        else:
            first_msg = intro_template(conv.property_type)
            outreach_status = "waiting_contact"
            next_state = next_after_intro_sent().value
            mark_role_question = False

        result = self.facebook.send_message(first_msg, listing_id=conv.listing_id)
        if not result.ok:
            conv.last_error = result.error
            conv.outreach_status = "failed"
            self.store.save(conv)
            return

        conv.intro_sent_at = conv.updated_at
        conv.state = next_state
        conv.outreach_status = outreach_status
        conv.thread_id = result.thread_id
        conv.thread_url = result.thread_url
        if mark_role_question:
            conv.role_question_sent_at = conv.updated_at
        self.store.save(conv)
        log_event("intro_sent", object_id=conv.object_id, thread_id=conv.thread_id)

    def _handle_inbound(self, conv: ConversationState, message: str) -> None:
        if conv.state in {s.value for s in TERMINAL_BUSINESS_STATES}:
            return
        if not conv.thread_id:
            return
        key = _inbound_key(conv.thread_id, message)
        if key in conv.seen_inbound_snippets:
            return
        conv.seen_inbound_snippets.append(key)
        if len(conv.seen_inbound_snippets) > 50:
            conv.seen_inbound_snippets = conv.seen_inbound_snippets[-50:]

        conv.last_inbound_at = conv.updated_at
        state = BusinessState(conv.state)
        interpretation = None
        try:
            if self.gemini.available:
                interpretation = self.gemini.interpret_inbound(message, {
                    "state": conv.state,
                    "object_id": conv.object_id,
                    "property_type": conv.property_type,
                    "known_facts": {},
                    "allowed_goal": "obtain_whatsapp_then_role",
                })
        except ValueError:
            conv.state = BusinessState.MANUAL_REVIEW.value
            conv.outreach_status = "manual_review"
            self.store.save(conv)
            return
        except Exception:
            interpretation = None

        decision = apply_inbound_policy(
            state=state,
            message=message,
            interpretation=interpretation,
            known_facts={},
            gemini_available=self.gemini.available,
        )

        if decision.save_whatsapp:
            existing = conv.whatsapp_existing or ""
            allowed, _ = whatsapp_write_allowed(existing, decision.save_whatsapp)
            if not allowed:
                conv.whatsapp_candidate_conflict = decision.save_whatsapp
                conv.state = BusinessState.MANUAL_REVIEW.value
                conv.outreach_status = "manual_review"
                self.store.save(conv)
                return
            conv.whatsapp_raw = decision.whatsapp_candidate
            conv.whatsapp_normalized = decision.save_whatsapp
            self.notion.update_contact_fields(conv.notion_page_id, whatsapp=decision.save_whatsapp)

            role_existing = (conv.owner_agent_existing or "").strip()
            if role_existing in OWNER_AGENT_TYPE_OPTIONS:
                conv.role = role_existing
                conv.state = BusinessState.COMPLETE.value
                conv.outreach_status = "complete"
                self.store.save(conv)
                return

            conv.state = BusinessState.ROLE_ASKED.value
            if not conv.role_question_sent_at:
                conv.role_question_sent_at = conv.updated_at
                conv.state = next_after_role_asked().value

        if decision.send_message and not conv.role_question_sent_at and decision.response_intent == "ASK_ROLE":
            if not self.facebook.thread_contains_sent_text(conv.thread_id, decision.send_message[:30]):
                self.facebook.send_message(
                    decision.send_message, thread_id=conv.thread_id, listing_id=conv.listing_id,
                )
                conv.role_question_sent_at = conv.updated_at
                conv.state = next_after_role_asked().value

        elif decision.send_message:
            text = decision.send_message
            if reject_unsupported_generated_fact(text, {}):
                conv.state = BusinessState.MANUAL_REVIEW.value
                conv.outreach_status = "manual_review"
            else:
                self.facebook.send_message(
                    text, thread_id=conv.thread_id, listing_id=conv.listing_id,
                )

        if decision.save_role:
            self.notion.update_contact_fields(conv.notion_page_id, owner_agent=decision.save_role)
            conv.role = decision.save_role
            conv.state = BusinessState.COMPLETE.value
            conv.outreach_status = "complete"

        if decision.decline:
            conv.state = BusinessState.DECLINED.value
            conv.outreach_status = "declined"

        if decision.manual_review:
            conv.state = BusinessState.MANUAL_REVIEW.value
            conv.outreach_status = "manual_review"

        if decision.new_state and not decision.manual_review and not decision.complete and not decision.decline:
            if decision.new_state != BusinessState.CONTACT_RECEIVED:
                conv.state = decision.new_state.value
        if decision.outreach_status:
            conv.outreach_status = decision.outreach_status
        self.store.save(conv)

    def _rate_limit_ok(self) -> bool:
        cfg = load_connector_config()
        now = time.time()
        hour_ago = now - 3600
        day_ago = now - 86400
        self.outreach_hour = [t for t in self.outreach_hour if t >= hour_ago]
        self.outreach_day = [t for t in self.outreach_day if t >= day_ago]
        if len(self.outreach_hour) >= int(cfg.get("max_new_outreach_per_hour", 5)):
            return False
        if len(self.outreach_day) >= int(cfg.get("max_new_outreach_per_day", 30)):
            return False
        return True

    def _record_outreach(self) -> None:
        now = time.time()
        self.outreach_hour.append(now)
        self.outreach_day.append(now)

    def run_forever(self) -> None:
        cfg = load_connector_config()
        notion_poll = int(cfg.get("notion_poll_seconds", 900))
        conv_poll = int(cfg.get("conversation_poll_seconds", 120))
        last_notion = 0.0
        last_conv = 0.0
        while True:
            now = time.time()
            if now - last_notion >= notion_poll:
                self.discover_notion()
                if self.facebook.mock_mode:
                    self.process_queue()
                else:
                    with facebook_profile_lock(facebook_profile_dir()):
                        self.facebook.ensure_browser()
                        try:
                            log_event("browser_started", profile=str(self.facebook.profile_path()))
                            self.process_queue()
                        finally:
                            self.facebook.stop_browser()
                last_notion = now
            if now - last_conv >= conv_poll:
                if self.facebook.mock_mode:
                    self.poll_active_conversations()
                else:
                    with facebook_profile_lock(facebook_profile_dir()):
                        self.facebook.ensure_browser()
                        try:
                            self.poll_active_conversations()
                        finally:
                            self.facebook.stop_browser()
                last_conv = now
            delay = random.randint(
                int((cfg.get("new_outreach_delay_seconds") or {}).get("min", 20)),
                int((cfg.get("new_outreach_delay_seconds") or {}).get("max", 60)),
            )
            time.sleep(min(delay, conv_poll))

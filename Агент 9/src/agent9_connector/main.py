"""Agent 9 orchestrator: Notion discovery + conversation loops."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .config_loader import load_connector_config
from .connectors.facebook import FacebookConnector
from .gemini import GeminiClient, confidence_action
from .logging_util import log_event
from .notion import NotionClient, whatsapp_write_allowed
from .policy import apply_inbound_policy, intro_template, reject_unsupported_generated_fact
from .state_machine import (
    ACTIVE_CONVERSATION_STATES,
    BusinessState,
    INTRO_SENT_STATES,
    ROLE_ASKED_STATES,
    TERMINAL_BUSINESS_STATES,
    next_after_intro_sent,
    next_after_role_asked,
)
from .state_store import ConversationState, StateStore


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
        browser_enabled = bool(cfg.get("browser_enabled", True))
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
        enqueued = 0
        for listing in self.notion.query_eligible():
            if self.store.get(listing.object_id):
                continue
            if self.store.enqueue(listing.object_id, {
                "notion_page_id": listing.page_id,
                "facebook_url": listing.facebook_url,
                "listing_id": listing.listing_id,
                "property_type": listing.property_type,
                "whatsapp_existing": listing.whatsapp_existing,
            }):
                enqueued += 1
        return enqueued

    def process_queue(self) -> int:
        if not self._rate_limit_ok():
            return 0
        queue = self.store.read_queue()
        if not queue:
            return 0
        object_id = next(iter(queue))
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
            state=BusinessState.NEW.value,
            outreach_status="opening_listing",
        )
        self.store.save(state)
        self._start_outreach(state)
        self._record_outreach()
        return 1

    def poll_active_conversations(self) -> int:
        handled = 0
        for conv in self.store.list_active():
            if conv.state not in {s.value for s in ACTIVE_CONVERSATION_STATES}:
                continue
            if not conv.thread_id:
                continue
            for msg in self.facebook.poll_inbound(conv.thread_id):
                self._handle_inbound(conv, msg.text)
                handled += 1
        return handled

    def _start_outreach(self, conv: ConversationState) -> None:
        screen = self.facebook.open_listing(conv.facebook_url, conv.listing_id)
        conv.screen_state = screen
        if screen in ("LOGIN_REQUIRED", "CHECKPOINT", "ACCOUNT_PAUSED"):
            conv.state = BusinessState.MANUAL_REVIEW.value
            conv.outreach_status = "manual_review"
            conv.last_error = screen
            self.store.save(conv)
            self.notion.update_outreach(conv.notion_page_id, status="manual_review", error=screen)
            return
        if conv.intro_sent_at:
            return
        intro = intro_template(conv.property_type)
        result = self.facebook.send_message(intro, listing_id=conv.listing_id)
        if not result.ok:
            conv.last_error = result.error
            conv.outreach_status = "failed"
            self.store.save(conv)
            return
        conv.intro_sent_at = conv.updated_at
        conv.state = next_after_intro_sent().value
        conv.outreach_status = "waiting_contact"
        conv.thread_id = result.thread_id
        conv.thread_url = result.thread_url
        self.store.save(conv)
        self.notion.update_outreach(
            conv.notion_page_id, status="waiting_contact", thread_ref=result.thread_url,
        )
        log_event("intro_sent", object_id=conv.object_id, thread_id=conv.thread_id)

    def _handle_inbound(self, conv: ConversationState, message: str) -> None:
        if conv.state in {s.value for s in TERMINAL_BUSINESS_STATES}:
            return
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
            existing = getattr(conv, "whatsapp_existing", "") or ""
            allowed, reason = whatsapp_write_allowed(existing, decision.save_whatsapp)
            if not allowed:
                conv.whatsapp_candidate_conflict = decision.save_whatsapp
                conv.state = BusinessState.MANUAL_REVIEW.value
                conv.outreach_status = "manual_review"
                self.store.save(conv)
                return
            conv.whatsapp_raw = decision.whatsapp_candidate
            conv.whatsapp_normalized = decision.save_whatsapp
            self.notion.update_outreach(conv.notion_page_id, whatsapp=decision.save_whatsapp)
            conv.state = BusinessState.ROLE_ASKED.value
            if not conv.role_question_sent_at:
                conv.role_question_sent_at = conv.updated_at
                conv.state = next_after_role_asked().value

        if decision.send_message and not conv.role_question_sent_at and decision.response_intent == "ASK_ROLE":
            if self.facebook.thread_contains_sent_text(conv.thread_id, decision.send_message[:30]):
                pass
            else:
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
            self.notion.update_outreach(conv.notion_page_id, owner_agent=decision.save_role, status="complete")
            conv.role = decision.save_role
            conv.state = BusinessState.COMPLETE.value
            conv.outreach_status = "complete"

        if decision.decline:
            conv.state = BusinessState.DECLINED.value
            conv.outreach_status = "declined"
            self.notion.update_outreach(conv.notion_page_id, status="declined")

        if decision.manual_review:
            conv.state = BusinessState.MANUAL_REVIEW.value
            conv.outreach_status = "manual_review"
            self.notion.update_outreach(conv.notion_page_id, status="manual_review")

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
        if not self.facebook.mock_mode:
            self.facebook.ensure_browser()
            log_event("browser_started", profile=str(self.facebook.profile_path()))
        notion_poll = int(cfg.get("notion_poll_seconds", 300))
        conv_poll = int(cfg.get("conversation_poll_seconds", 120))
        last_notion = 0.0
        last_conv = 0.0
        while True:
            now = time.time()
            if now - last_notion >= notion_poll:
                self.discover_notion()
                self.process_queue()
                last_notion = now
            if now - last_conv >= conv_poll:
                self.poll_active_conversations()
                last_conv = now
            delay = random.randint(
                int((cfg.get("new_outreach_delay_seconds") or {}).get("min", 20)),
                int((cfg.get("new_outreach_delay_seconds") or {}).get("max", 60)),
            )
            time.sleep(min(delay, conv_poll))

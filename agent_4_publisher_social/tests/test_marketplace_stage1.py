from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from publisher_social.marketplace.config import load_marketplace_ui_config, redact_secrets
from publisher_social.marketplace.detector import detect_marketplace_state
from publisher_social.marketplace.diagnostics import DiagnosticBundleWriter, build_context
from publisher_social.marketplace.guard import (
    ActionGuardDecision,
    ActionPlan,
    build_fill_plan,
    evaluate_action_plan,
)
from publisher_social.marketplace.selectors import (
    SelectorMatch,
    click_target_for_node,
    find_element,
    find_labeled_input,
)
from publisher_social.marketplace.session import MarketplaceSafeStop, session_from_xml
from publisher_social.marketplace.snapshot import UISnapshot, snapshot_from_xml
from publisher_social.marketplace.states import MarketplaceState
from publisher_social.marketplace.validator import (
    validate_description_field,
    validate_price_field,
    validate_title_field,
)
from publisher_social.marketplace.verifier import verify_transition
from publisher_social.models import ListingFields, PublishJob

FIXTURES = Path(__file__).parent / "fixtures" / "marketplace"


def _job() -> PublishJob:
    return PublishJob(
        page_id="page-1",
        object_id="A_20260806_001",
        title="Villa near beach",
        caption_social="",
        caption_fb="Long description for marketplace listing.",
        listing=ListingFields(price_monthly=199200),
    )


def _xml(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class MarketplaceStage1Tests(unittest.TestCase):
    def test_selector_resource_id_price(self) -> None:
        snap = snapshot_from_xml(_xml("composer_ru.xml"))
        match = find_element(snap, "price_field")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.method, "resource-id")
        self.assertIn("marketplace_composer_price_input", match.node.resource_id)

    def test_selector_content_description_fallback(self) -> None:
        snap = snapshot_from_xml(_xml("composer_en.xml"))
        match = find_labeled_input(snap, label_aliases=["Title", "Название"])
        self.assertIsNotNone(match)

    def test_text_alias_fallback(self) -> None:
        snap = snapshot_from_xml(_xml("publish_ru.xml"))
        match = find_element(snap, "publish_button")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.method, "text")

    def test_no_coordinate_click_when_node_present(self) -> None:
        snap = snapshot_from_xml(_xml("composer_ru.xml"))
        match = find_element(snap, "price_field")
        assert match is not None
        method, point = click_target_for_node(match.node)
        self.assertEqual(method, "bounds")
        self.assertIsNotNone(point)

    def test_state_detected_by_multiple_signals(self) -> None:
        snap = snapshot_from_xml(_xml("composer_ru.xml"))
        state = detect_marketplace_state(snap)
        self.assertIn(
            state,
            {MarketplaceState.COMPOSER_FORM, MarketplaceState.FILL_PRICE},
        )

    def test_price_not_in_title_field(self) -> None:
        snap = snapshot_from_xml(_xml("composer_ru.xml"))
        title = validate_title_field(snap, ui_cfg=load_marketplace_ui_config())
        price = validate_price_field(snap, ui_cfg=load_marketplace_ui_config())
        self.assertTrue(title.ok)
        self.assertTrue(price.ok)
        self.assertNotEqual(title.match.node.node_id, price.match.node.node_id)

    def test_title_not_in_description_field(self) -> None:
        snap = snapshot_from_xml(_xml("composer_ru.xml"))
        title = validate_title_field(snap, ui_cfg=load_marketplace_ui_config())
        desc = validate_description_field(snap, ui_cfg=load_marketplace_ui_config())
        self.assertTrue(desc.ok)
        self.assertNotEqual(title.match.node.node_id, desc.match.node.node_id)
        self.assertLess(desc.match.node.y1, 960)

    def test_input_type_checked_for_price(self) -> None:
        snap = snapshot_from_xml(_xml("composer_en.xml"))
        result = validate_price_field(snap, ui_cfg=load_marketplace_ui_config())
        self.assertTrue(result.ok)
        self.assertIn("input_type=numeric", result.evidence)

    def test_low_confidence_action_not_executed(self) -> None:
        ui_cfg = load_marketplace_ui_config()
        plan = build_fill_plan(
            state=MarketplaceState.COMPOSER_FORM,
            target="price",
            value_type="numeric",
            confidence=0.5,
            evidence=["weak"],
        )
        decision = evaluate_action_plan(plan, ui_cfg)
        self.assertEqual(decision.decision, ActionGuardDecision.SAFE_STOP)

    def test_snapshot_refresh_replaces_tree(self) -> None:
        device = Mock()
        device.dump_hierarchy.side_effect = [
            _xml("composer_ru.xml"),
            _xml("publish_ru.xml"),
        ]
        device.app_current.return_value = {"activity": ".Composer", "package": "com.facebook.katana"}
        device.window_size.return_value = (720, 1600)
        snap = UISnapshot(xml="", nodes=[])
        first = snap.refresh_from_device(device)
        first_id = first.snapshot_id
        second = snap.refresh_from_device(device)
        self.assertNotEqual(first_id, second.snapshot_id)
        self.assertIn("Опубликовать", second.xml)

    def test_next_transition_requires_change(self) -> None:
        snap = snapshot_from_xml(_xml("composer_ru.xml"))

        def refresh() -> UISnapshot:
            return snap

        ok, state, reason = verify_transition(
            refresh,
            previous_state=detect_marketplace_state(snap),
            ui_cfg={"timeouts": {"ui_change_seconds": 0.1}},
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "state_unchanged")

    def test_publish_blocked_without_final_validation(self) -> None:
        job = _job()
        session = session_from_xml(job, _xml("composer_ru.xml"))
        with self.assertRaises(MarketplaceSafeStop):
            session.final_content_validation(
                title_value="wrong",
                price_value="1",
                description_value="",
                media_count=0,
                expected_media_count=16,
            )

    def test_unknown_screen_creates_diagnostic_bundle(self) -> None:
        with TemporaryDirectory() as tmp:
            ui_cfg = load_marketplace_ui_config()
            ui_cfg["diagnostics"]["root_dir"] = tmp
            writer = DiagnosticBundleWriter(ui_cfg)
            context = build_context(
                listing_id="A_1",
                publication_job_id="job",
                current_state=MarketplaceState.UNKNOWN_SCREEN,
                reason="unknown",
            )
            path = writer.save(context=context, xml="<hierarchy/>", actions=[])
            self.assertIsNotNone(path)
            assert path is not None
            self.assertTrue((path / "ui_dump.xml").exists())
            self.assertTrue((path / "context.json").exists())

    def test_checkpoint_not_bypassed(self) -> None:
        snap = snapshot_from_xml(_xml("checkpoint_en.xml"))
        state = detect_marketplace_state(snap)
        self.assertEqual(state, MarketplaceState.BLOCKED_CHECKPOINT)

    def test_failed_marketplace_does_not_imply_groups_change(self) -> None:
        from publisher_social.facebook_batch import summarize_facebook_batch_status
        from publisher_social.channels.base import ChannelResult

        results = [
            ChannelResult(channel="fb_groups", ok=True, publication_status="accepted"),
            ChannelResult(
                channel="fb_marketplace",
                ok=False,
                reason="needs_review",
                publication_status="needs_review",
            ),
        ]
        self.assertEqual(summarize_facebook_batch_status(results), "partial_success")

    def test_diagnostics_save_screenshot_and_xml(self) -> None:
        with TemporaryDirectory() as tmp:
            ui_cfg = load_marketplace_ui_config()
            ui_cfg["diagnostics"]["root_dir"] = tmp
            writer = DiagnosticBundleWriter(ui_cfg)
            context = build_context(
                listing_id="A_1",
                publication_job_id="job",
                current_state=MarketplaceState.FAILED,
                reason="error",
                exc=RuntimeError("boom"),
            )
            bundle = writer.save(
                context=context,
                xml="<hierarchy><node /></hierarchy>",
                screenshot_bytes=b"png",
            )
            assert bundle is not None
            self.assertTrue((bundle / "screenshot.png").exists())
            self.assertTrue((bundle / "ui_dump.xml").exists())
            self.assertTrue((bundle / "error.txt").exists())

    def test_secrets_redacted_in_diagnostics(self) -> None:
        raw = "api_key=supersecret token=abc"
        masked = redact_secrets(raw)
        self.assertNotIn("supersecret", masked)
        self.assertIn("REDACTED", masked)

    def test_already_published_marketplace_skipped(self) -> None:
        from publisher_social.state import is_channel_done, load_state, mark_channel_done

        state = load_state()
        mark_channel_done(
            state,
            "A_skip_test",
            "fb_marketplace",
            status="accepted",
            note="done",
        )
        self.assertTrue(is_channel_done(state, "A_skip_test", "fb_marketplace"))


if __name__ == "__main__":
    unittest.main()

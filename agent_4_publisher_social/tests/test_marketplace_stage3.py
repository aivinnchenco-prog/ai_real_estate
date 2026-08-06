from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from publisher_social.marketplace.config import load_marketplace_ui_config
from publisher_social.marketplace.session import MarketplaceSession, session_from_xml
from publisher_social.marketplace.snapshot import snapshot_from_xml
from publisher_social.marketplace.states import MarketplaceState
from publisher_social.marketplace.vision import (
    DisabledVisionProvider,
    FakeVisionProvider,
    VisionAuditLog,
    VisionRequest,
    VisionResponse,
    load_vision_provider,
)
from publisher_social.models import ListingFields, PublishJob

FIXTURES = Path(__file__).parent / "fixtures" / "marketplace"


def _job() -> PublishJob:
    return PublishJob(
        page_id="page-1",
        object_id="A_20260806_001",
        title="Villa",
        caption_social="",
        caption_fb="Desc",
        listing=ListingFields(price_monthly=100),
    )


class MarketplaceStage3Tests(unittest.TestCase):
    def test_vision_disabled_by_default(self) -> None:
        provider = load_vision_provider(load_marketplace_ui_config())
        self.assertIsInstance(provider, DisabledVisionProvider)

    def test_vision_only_after_deterministic_failure(self) -> None:
        ui_cfg = load_marketplace_ui_config()
        ui_cfg["vision"]["enabled"] = True
        ui_cfg["vision"]["provider"] = "fake"
        job = _job()
        session = session_from_xml(job, (FIXTURES / "composer_ru.xml").read_text(encoding="utf-8"))
        session.ui_cfg = ui_cfg
        session.vision = FakeVisionProvider()
        called = session._try_vision_select("price")
        self.assertTrue(called)

    def test_vision_selects_only_existing_nodes(self) -> None:
        snap = snapshot_from_xml((FIXTURES / "composer_ru.xml").read_text(encoding="utf-8"))
        provider = FakeVisionProvider(
            {
                "price": VisionResponse(
                    screen_state="FILL_PRICE",
                    selected_element_id="node_missing",
                    confidence=0.99,
                    reason="bad",
                )
            }
        )
        request = VisionRequest(
            expected_state="FILL_PRICE",
            allowed_actions=["select_price_field"],
            ui_elements=[{"id": n.node_id} for n in snap.nodes],
            forbidden_actions=[],
            target="price",
        )
        response = provider.select_element(snapshot=snap, request=request)
        self.assertEqual(response.selected_element_id, "node_missing")

    def test_low_confidence_safe_stop_via_guard(self) -> None:
        ui_cfg = load_marketplace_ui_config()
        ui_cfg["vision"]["enabled"] = True
        provider = FakeVisionProvider(
            {
                "price": VisionResponse(
                    screen_state="FILL_PRICE",
                    selected_element_id="node_0",
                    confidence=0.5,
                    reason="low",
                )
            }
        )
        job = _job()
        session = session_from_xml(job, (FIXTURES / "composer_ru.xml").read_text(encoding="utf-8"))
        session.ui_cfg = ui_cfg
        session.vision = provider
        self.assertFalse(session._try_vision_select("price"))

    def test_forbidden_action_rejected(self) -> None:
        provider = FakeVisionProvider(
            {
                "price": VisionResponse(
                    screen_state="FILL_PRICE",
                    selected_element_id="node_0",
                    confidence=0.99,
                    reason="promote",
                    action="promote_boost",
                )
            }
        )
        snap = snapshot_from_xml("<hierarchy />")
        request = VisionRequest(
            expected_state="FILL_PRICE",
            allowed_actions=["select_price_field"],
            ui_elements=[],
            forbidden_actions=["promote_boost"],
            target="price",
        )
        response = provider.select_element(snapshot=snap, request=request)
        self.assertEqual(response.action, "promote_boost")

    def test_nonexistent_node_rejected(self) -> None:
        ui_cfg = load_marketplace_ui_config()
        ui_cfg["vision"]["enabled"] = True
        session = session_from_xml(
            _job(),
            (FIXTURES / "composer_ru.xml").read_text(encoding="utf-8"),
        )
        session.ui_cfg = ui_cfg
        session.vision = FakeVisionProvider(
            {
                "price": VisionResponse(
                    screen_state="FILL_PRICE",
                    selected_element_id="missing",
                    confidence=0.99,
                    reason="missing",
                )
            }
        )
        self.assertFalse(session._try_vision_select("price"))

    def test_coordinate_fallback_disabled_by_default(self) -> None:
        ui_cfg = load_marketplace_ui_config()
        self.assertFalse(ui_cfg["vision"]["coordinate_fallback_enabled"])

    def test_coordinate_fallback_requires_high_threshold(self) -> None:
        ui_cfg = load_marketplace_ui_config()
        ui_cfg["vision"]["coordinate_fallback_enabled"] = True
        threshold = float(ui_cfg["confidence"]["coordinate_execute"])
        self.assertGreaterEqual(threshold, 0.97)

    def test_post_action_verification_required_after_vision_click(self) -> None:
        device = Mock()
        xml = (FIXTURES / "composer_ru.xml").read_text(encoding="utf-8")
        device.dump_hierarchy.return_value = xml
        device.app_current.return_value = {}
        device.window_size.return_value = (720, 1600)
        device.screenshot.return_value = b""
        device.resourceId.return_value.exists.return_value = False
        device.text.return_value.exists.return_value = False
        device.description.return_value.exists.return_value = False
        ui_cfg = load_marketplace_ui_config()
        ui_cfg["vision"]["enabled"] = True
        job = _job()
        session = session_from_xml(job, xml)
        session.device = device
        session.ui_cfg = ui_cfg
        session.vision = FakeVisionProvider()
        self.assertTrue(session._try_vision_select("price"))
        device.click.assert_called()
        session.refresh_snapshot()
        self.assertIsNotNone(session.snapshot)

    def test_fake_provider_works_offline(self) -> None:
        provider = FakeVisionProvider()
        response = provider.classify_screen(
            request=VisionRequest(
                expected_state="FILL_PRICE",
                allowed_actions=["select_price_field"],
                ui_elements=[],
                forbidden_actions=[],
            )
        )
        self.assertGreater(response.confidence, 0.9)

    def test_vision_disagreement_safe_stop(self) -> None:
        provider = FakeVisionProvider(
            {
                "price": VisionResponse(
                    screen_state="UNKNOWN_SCREEN",
                    selected_element_id=None,
                    confidence=0.99,
                    reason="wrong",
                )
            }
        )
        snap = snapshot_from_xml((FIXTURES / "composer_ru.xml").read_text(encoding="utf-8"))
        response = provider.select_element(
            snapshot=snap,
            request=VisionRequest(
                expected_state="FILL_PRICE",
                allowed_actions=["select_price_field", "report_not_found"],
                ui_elements=[],
                forbidden_actions=[],
                target="price",
            ),
        )
        self.assertEqual(response.screen_state, "UNKNOWN_SCREEN")

    def test_vision_audit_log_saved(self) -> None:
        with TemporaryDirectory() as tmp:
            audit = VisionAuditLog(Path(tmp))
            audit.append({"event": "test"})
            content = (Path(tmp) / "vision_audit.jsonl").read_text(encoding="utf-8")
            self.assertIn("test", content)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path

from publisher_social.marketplace.config import load_marketplace_ui_config
from publisher_social.marketplace.content_validation import validate_listing_content
from publisher_social.marketplace.detector import detect_marketplace_state
from publisher_social.marketplace.fingerprints import fingerprint_score
from publisher_social.marketplace.popups import detect_known_popup
from publisher_social.marketplace.retry import retry_find
from publisher_social.marketplace.session import MarketplaceSafeStop, session_from_xml
from publisher_social.marketplace.snapshot import snapshot_from_xml
from publisher_social.marketplace.states import MarketplaceState
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


class MarketplaceStage2Tests(unittest.TestCase):
    def test_russian_and_english_same_state(self) -> None:
        ru = detect_marketplace_state(snapshot_from_xml(_xml("composer_ru.xml")))
        en = detect_marketplace_state(snapshot_from_xml(_xml("composer_en.xml")))
        allowed = {MarketplaceState.COMPOSER_FORM, MarketplaceState.FILL_PRICE}
        self.assertIn(ru, allowed)
        self.assertIn(en, allowed)

    def test_reordered_buttons_still_match_aliases(self) -> None:
        xml = """
        <hierarchy>
          <node text="Continue" bounds="[30,1400][690,1500]" clickable="true" />
          <node text="New listing" />
        </hierarchy>
        """
        snap = snapshot_from_xml(xml)
        from publisher_social.marketplace.selectors import find_element

        match = find_element(snap, "next_button")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.node.text, "Continue")

    def test_button_rename_handled_by_alias(self) -> None:
        xml = '<hierarchy><node text="Publish listing" clickable="true" /></hierarchy>'
        snap = snapshot_from_xml(xml)
        from publisher_social.marketplace.selectors import find_element

        self.assertIsNotNone(find_element(snap, "publish_button"))

    def test_new_alias_not_auto_used_from_diagnostics(self) -> None:
        catalog = load_marketplace_ui_config()
        self.assertNotIn("Разместить объявление сейчас", str(catalog))

    def test_known_popup_detected(self) -> None:
        xml = '<hierarchy><node text="Allow" clickable="true" /></hierarchy>'
        popup = detect_known_popup(snapshot_from_xml(xml))
        self.assertIsNotNone(popup)
        assert popup is not None
        self.assertEqual(popup.popup_id, "media_permission")

    def test_unknown_popup_triggers_safe_stop(self) -> None:
        job = _job()
        xml = '<hierarchy><node text="Take our survey" clickable="true" /></hierarchy>'
        session = session_from_xml(job, xml)
        with self.assertRaises(MarketplaceSafeStop):
            session.confirm_publish_allowed()

    def test_retry_is_limited(self) -> None:
        calls = {"n": 0}

        def finder():
            calls["n"] += 1
            return None

        result = retry_find(finder, attempts=3, pause_seconds=0)
        self.assertIsNone(result)
        self.assertEqual(calls["n"], 3)

    def test_stuck_state_does_not_infinite_loop(self) -> None:
        attempts = retry_find(lambda: None, attempts=2, pause_seconds=0)
        self.assertIsNone(attempts)

    def test_content_validation_catches_wrong_price(self) -> None:
        snap = snapshot_from_xml(_xml("composer_ru.xml"))
        result = validate_listing_content(
            _job(),
            snap,
            ui_cfg=load_marketplace_ui_config(),
            title_value="Villa near beach",
            price_value="1",
            description_value="Long description for marketplace listing.",
            media_count=16,
            expected_media_count=16,
        )
        self.assertFalse(result.ok)
        self.assertIn("price_mismatch", result.errors)

    def test_content_validation_catches_wrong_title(self) -> None:
        snap = snapshot_from_xml(_xml("composer_ru.xml"))
        result = validate_listing_content(
            _job(),
            snap,
            ui_cfg=load_marketplace_ui_config(),
            title_value="Wrong",
            price_value="199200",
            description_value="Long description for marketplace listing.",
            media_count=16,
            expected_media_count=16,
        )
        self.assertFalse(result.ok)
        self.assertIn("title_mismatch", result.errors)

    def test_content_validation_catches_low_media(self) -> None:
        snap = snapshot_from_xml(_xml("composer_ru.xml"))
        result = validate_listing_content(
            _job(),
            snap,
            ui_cfg=load_marketplace_ui_config(),
            title_value="Villa near beach",
            price_value="199200",
            description_value="Long description for marketplace listing.",
            media_count=1,
            expected_media_count=16,
        )
        self.assertFalse(result.ok)
        self.assertIn("media_count_low", result.errors)

    def test_publish_forbidden_after_validation_failure(self) -> None:
        job = _job()
        session = session_from_xml(job, _xml("composer_ru.xml"))
        with self.assertRaises(MarketplaceSafeStop) as ctx:
            session.final_content_validation(
                title_value="bad",
                price_value="199200",
                description_value="Long description for marketplace listing.",
                media_count=16,
                expected_media_count=16,
            )
        self.assertEqual(ctx.exception.publication_status, "validation_failed")

    def test_fingerprint_score_for_fill_price(self) -> None:
        from publisher_social.marketplace.fingerprints import MARKETPLACE_FINGERPRINTS

        snap = snapshot_from_xml(_xml("composer_ru.xml"))
        fp = next(fp for fp in MARKETPLACE_FINGERPRINTS if fp.state == MarketplaceState.FILL_PRICE)
        score, evidence = fingerprint_score(snap, fp)
        self.assertGreater(score, 0.7)
        self.assertTrue(evidence)


if __name__ == "__main__":
    unittest.main()

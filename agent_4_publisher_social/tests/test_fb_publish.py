from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from publisher_social.channels._fb_publish import (
    confirm_publish_or_stop,
    publish_button_visible,
    tap_publish_button,
)


class FbPublishTests(unittest.TestCase):
    def test_publish_button_visible_checks_text_and_description(self) -> None:
        device = Mock()
        device.text.return_value.exists.return_value = True
        device.description.return_value.exists.return_value = False
        device.textContains.return_value.exists.return_value = False
        self.assertTrue(publish_button_visible(device))

    def test_tap_publish_hides_keyboard_and_clicks(self) -> None:
        device = Mock()

        def _selector(**kwargs: object) -> Mock:
            node = Mock()
            label = kwargs.get("description") or kwargs.get("text") or kwargs.get("textContains")
            node.exists.return_value = label == "Опубликовать"
            return node

        device.side_effect = _selector
        device.window_size.return_value = (1080, 2400)
        device.dump_hierarchy.return_value = "<hierarchy/>"
        with patch("publisher_social.channels._fb_publish.human_pause"):
            self.assertTrue(tap_publish_button(device, {}))
        device.hide_keyboard.assert_called_once()
        device.assert_any_call(description="Опубликовать")

    def test_confirm_publish_fails_when_button_stays_visible(self) -> None:
        device = Mock()
        visible = Mock(side_effect=[True, True, True])
        with patch(
            "publisher_social.channels._fb_publish.publish_button_visible",
            visible,
        ), patch(
            "publisher_social.channels._fb_publish.tap_publish_button",
            return_value=True,
        ), patch("publisher_social.channels._fb_publish.human_pause"):
            result = confirm_publish_or_stop(
                device,
                confirm_post=True,
                android_cfg={},
                channel="fb_groups",
                stopped_note="",
                success_note="ok",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "publish_button_still_visible")

    def test_confirm_publish_skips_without_live(self) -> None:
        device = Mock()
        with patch(
            "publisher_social.channels._fb_publish.publish_button_visible",
            return_value=True,
        ):
            result = confirm_publish_or_stop(
                device,
                confirm_post=False,
                android_cfg={},
                channel="fb_marketplace",
                stopped_note="draft",
                success_note="ok",
            )
        self.assertTrue(result.ok)
        self.assertTrue(result.skipped)
        self.assertEqual(result.note, "draft")


if __name__ == "__main__":
    unittest.main()

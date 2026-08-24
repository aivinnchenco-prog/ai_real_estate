from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from publisher_social.android.vision_fallback import VisionFallback, vision_settings


class VisionFallbackTests(unittest.TestCase):
    def test_settings_from_android_cfg(self) -> None:
        cfg = {
            "ui_automation": {
                "vision_fallback": {
                    "enabled": True,
                    "min_confidence": 0.8,
                    "max_calls_per_run": 5,
                }
            }
        }
        s = vision_settings(cfg)
        self.assertTrue(s["enabled"])
        self.assertEqual(s["min_confidence"], 0.8)
        self.assertEqual(s["max_calls"], 5)

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=False)
    @patch("publisher_social.android.vision_fallback.requests.post")
    def test_click_goal_taps_when_confident(self, post_mock: Mock) -> None:
        post_mock.return_value = Mock(
            status_code=200,
            **{
                "json.return_value": {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            {
                                                "found": True,
                                                "x": 540,
                                                "y": 2100,
                                                "confidence": 0.95,
                                                "label": "Опубликовать",
                                            }
                                        )
                                    }
                                ]
                            }
                        }
                    ]
                }
            },
        )
        device = Mock()
        device.window_size.return_value = (1080, 2400)
        device.screenshot.return_value = b"fakejpeg"
        vf = VisionFallback(
            {
                "ui_automation": {
                    "vision_fallback": {"enabled": True, "min_confidence": 0.7}
                }
            }
        )
        self.assertTrue(vf.click_goal(device, "Tap publish"))
        device.click.assert_called_once_with(540, 2100)

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=False)
    @patch("publisher_social.android.vision_fallback.requests.post")
    def test_low_confidence_is_rejected(self, post_mock: Mock) -> None:
        post_mock.return_value = Mock(
            status_code=200,
            **{
                "json.return_value": {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            {
                                                "found": True,
                                                "x": 100,
                                                "y": 100,
                                                "confidence": 0.2,
                                            }
                                        )
                                    }
                                ]
                            }
                        }
                    ]
                }
            },
        )
        device = Mock()
        device.window_size.return_value = (1080, 2400)
        device.screenshot.return_value = b"fakejpeg"
        vf = VisionFallback(
            {
                "ui_automation": {
                    "vision_fallback": {"enabled": True, "min_confidence": 0.7}
                }
            }
        )
        self.assertFalse(vf.click_goal(device, "Tap publish"))
        device.click.assert_not_called()


if __name__ == "__main__":
    unittest.main()

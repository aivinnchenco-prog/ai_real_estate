from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from publisher_social.browser.backend import channel_uses_browser, fb_backend
from publisher_social.pipeline import _needs_phone_media_push


class FbBrowserBackendTests(unittest.TestCase):
    def test_default_backend_is_phone(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PUBLISHER_FB_BACKEND", None)
            self.assertEqual(fb_backend({}), "phone")

    def test_env_enables_browser(self) -> None:
        with patch.dict(os.environ, {"PUBLISHER_FB_BACKEND": "browser"}):
            self.assertEqual(fb_backend({}), "browser")
            self.assertTrue(channel_uses_browser("fb_groups", {}))
            self.assertTrue(channel_uses_browser("fb_marketplace", {}))
            self.assertFalse(channel_uses_browser("linkedin", {}))

    def test_browser_skips_phone_push(self) -> None:
        cfg = {}
        with patch.dict(os.environ, {"PUBLISHER_FB_BACKEND": "browser"}):
            self.assertFalse(
                _needs_phone_media_push(
                    ["fb_groups", "fb_marketplace"],
                    cfg,
                    push_media=True,
                    dry_run=False,
                )
            )

    def test_rejects_non_agent7_profile_path(self) -> None:
        from publisher_social.browser.profile import _assert_agent7_profile

        with self.assertRaises(RuntimeError):
            _assert_agent7_profile(Path("/tmp/.fb_profile"))
        _assert_agent7_profile(Path("/opt/openhome/runtime/browser_profiles/facebook_agent7"))

    def test_phone_backend_keeps_push(self) -> None:
        cfg = {}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PUBLISHER_FB_BACKEND", None)
            self.assertTrue(
                _needs_phone_media_push(
                    ["fb_groups"],
                    cfg,
                    push_media=True,
                    dry_run=False,
                )
            )


if __name__ == "__main__":
    unittest.main()

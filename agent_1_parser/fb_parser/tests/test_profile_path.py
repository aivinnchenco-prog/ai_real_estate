#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_FB_ROOT = Path(__file__).resolve().parents[1]
if str(_FB_ROOT) not in sys.path:
    sys.path.insert(0, str(_FB_ROOT))

from agent1b.fb_session import is_credential_like_profile_value


class ProfileValueTest(unittest.TestCase):
    def test_accepts_relative_and_absolute_folders(self):
        self.assertFalse(is_credential_like_profile_value(".fb_profile"))
        self.assertFalse(is_credential_like_profile_value("fb_profile"))
        self.assertFalse(
            is_credential_like_profile_value(
                "/opt/openhome/runtime/browser_profiles/facebook_owner_outreach"
            )
        )
        self.assertFalse(is_credential_like_profile_value("~/browser_profiles/facebook"))

    def test_rejects_email_password_and_urls(self):
        self.assertTrue(is_credential_like_profile_value("karen@example.com"))
        self.assertTrue(is_credential_like_profile_value("https://facebook.com/login"))
        self.assertTrue(is_credential_like_profile_value('  user@host.com  '))


if __name__ == "__main__":
    unittest.main()

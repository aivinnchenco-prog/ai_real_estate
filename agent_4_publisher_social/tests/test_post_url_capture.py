from __future__ import annotations

import unittest
from argparse import Namespace
from unittest.mock import Mock, patch

from publisher_social import __main__ as cli
from publisher_social.channels._post_url import attach_post_url
from publisher_social.channels.base import ChannelResult


class PostUrlCaptureTests(unittest.TestCase):
    def test_android_capture_is_disabled_by_default_and_result_is_accepted(
        self,
    ) -> None:
        result = ChannelResult(
            channel="tiktok",
            ok=True,
            note="Нажато «Опубликовать»",
        )

        with patch(
            "publisher_social.android.post_link.capture_post_url"
        ) as capture:
            actual = attach_post_url(
                Mock(),
                result,
                "tiktok",
                {},
                confirm_post=True,
            )

        capture.assert_not_called()
        self.assertEqual(actual.publication_status, "accepted")
        self.assertIsNone(actual.post_url)
        self.assertIn("post_url_source=api", actual.note or "")

    def test_disabled_capture_covers_every_publishing_channel(self) -> None:
        channels = (
            "tiktok",
            "tiktok_carousel",
            "instagram_reel",
            "instagram_carousel",
            "youtube_shorts",
            "linkedin",
            "twitter",
            "fb_groups",
            "fb_marketplace",
        )

        with patch(
            "publisher_social.android.post_link.capture_post_url"
        ) as capture:
            for channel in channels:
                with self.subTest(channel=channel):
                    result = attach_post_url(
                        Mock(),
                        ChannelResult(channel=channel, ok=True),
                        channel,
                        {
                            "post_url_capture": {
                                "enabled": False,
                                "source": "api",
                            }
                        },
                        confirm_post=True,
                    )
                    self.assertEqual(result.publication_status, "accepted")
                    self.assertIsNone(result.post_url)

        capture.assert_not_called()

    def test_manual_capture_command_stops_before_device_connection(self) -> None:
        args = Namespace(channel="tiktok", wait=0, page_id=None)
        with (
            patch(
                "publisher_social.__main__.load_android_config",
                return_value={
                    "post_url_capture": {
                        "enabled": False,
                        "source": "api",
                    }
                },
            ),
            patch("publisher_social.__main__.check_adb") as check_adb,
            patch("publisher_social.__main__.connect_device") as connect,
        ):
            code = cli.cmd_capture_url(args)

        self.assertEqual(code, 2)
        check_adb.assert_not_called()
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()

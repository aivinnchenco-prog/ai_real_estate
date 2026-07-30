from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from publisher_social.android import post_link


class FakeDevice:
    def __init__(self, xml: str) -> None:
        self.xml = xml

    def dump_hierarchy(self) -> str:
        return self.xml


class InstagramPostLinkTests(unittest.TestCase):
    def test_fresh_minutes_are_accepted(self) -> None:
        device = FakeDevice(
            '<hierarchy><node text="2 мин." content-desc="" /></hierarchy>'
        )
        self.assertTrue(post_link._instagram_post_is_fresh(device))

    def test_old_post_is_rejected(self) -> None:
        device = FakeDevice(
            '<hierarchy><node text="4 дн." content-desc="4 дня назад" /></hierarchy>'
        )
        self.assertFalse(post_link._instagram_post_is_fresh(device))
        self.assertFalse(post_link._instagram_age_text_is_fresh("90 мин."))

    def test_missing_age_evidence_is_rejected(self) -> None:
        device = FakeDevice(
            '<hierarchy><node text="Поделиться" content-desc="Публикация" /></hierarchy>'
        )
        self.assertFalse(post_link._instagram_post_is_fresh(device))

    def test_instagram_url_must_match_published_content_type(self) -> None:
        reel = "https://www.instagram.com/reel/abc/"
        carousel = "https://www.instagram.com/p/xyz/"
        self.assertTrue(
            post_link._instagram_url_matches_channel(reel, "instagram_reel")
        )
        self.assertFalse(
            post_link._instagram_url_matches_channel(reel, "instagram_carousel")
        )
        self.assertTrue(
            post_link._instagram_url_matches_channel(
                carousel,
                "instagram_carousel",
            )
        )
        self.assertFalse(
            post_link._instagram_url_matches_channel(carousel, "instagram_reel")
        )


class TikTokPostLinkTests(unittest.TestCase):
    @staticmethod
    def _post_id(created_at: int) -> int:
        return (created_at << 32) + 12345

    def test_tiktok_url_requires_exact_host_type_and_fresh_snowflake(self) -> None:
        now = int(time.time())
        fresh = self._post_id(now - 90)
        old = self._post_id(now - 86_400)
        video = f"https://www.tiktok.com/@owner/video/{fresh}?tracking=1"
        photo = f"https://www.tiktok.com/@owner/photo/{fresh}"

        self.assertTrue(
            post_link._tiktok_url_is_fresh_target(video, "tiktok", now=now)
        )
        self.assertTrue(
            post_link._tiktok_url_is_fresh_target(
                photo,
                "tiktok_carousel",
                now=now,
            )
        )
        self.assertFalse(
            post_link._tiktok_url_is_fresh_target(video, "tiktok_carousel", now=now)
        )
        self.assertFalse(
            post_link._tiktok_url_is_fresh_target(
                f"https://www.tiktok.com/@owner/photo/{old}",
                "tiktok_carousel",
                now=now,
            )
        )
        self.assertFalse(
            post_link._tiktok_url_is_fresh_target(
                f"https://tiktok.com.evil.example/@owner/video/{fresh}",
                "tiktok",
                now=now,
            )
        )

    def test_tiktok_short_link_must_be_resolved_before_verification(self) -> None:
        now = int(time.time())
        self.assertTrue(
            post_link._is_tiktok_short_url("https://vt.tiktok.com/abc123/")
        )
        self.assertFalse(
            post_link._is_tiktok_short_url(
                "https://vt.tiktok.com.evil.example/abc123/"
            )
        )
        self.assertFalse(
            post_link._tiktok_url_is_fresh_target(
                "https://vm.tiktok.com/abc123/",
                "tiktok",
                now=now,
            )
        )

    def test_capture_rejects_stale_short_link_and_prefers_fresh_open_post_id(
        self,
    ) -> None:
        class Device:
            def click(self, _x: int, _y: int) -> None:
                pass

        short_url = "https://vt.tiktok.com/abc123/"
        now = int(time.time())
        old_id = self._post_id(now - 9_000)
        fresh_id = self._post_id(now - 30)
        old_url = f"https://www.tiktok.com/@owner/photo/{old_id}"
        fresh_url = f"https://www.tiktok.com/@owner/photo/{fresh_id}"
        with (
            patch.object(post_link, "_tiktok_go_home"),
            patch.object(post_link, "_tiktok_open_profile_grid"),
            patch.object(
                post_link,
                "_tiktok_top_row_tiles",
                return_value=[(100, 200, 0, 0, 0, 0)],
            ),
            patch.object(
                post_link,
                "_tiktok_first_profile_post_center",
                return_value=(100, 200),
            ),
            patch.object(
                post_link,
                "_tiktok_copy_link_via_long_press",
                return_value=short_url,
            ),
            patch.object(post_link, "human_pause"),
            patch.object(
                post_link,
                "_resolve_tiktok_short_url",
                return_value=old_url,
            ),
            patch.object(
                post_link,
                "_tiktok_url_from_recent_log",
                return_value=fresh_url,
            ),
        ):
            captured = post_link._capture_tiktok_url(
                Device(),
                {
                    "tiktok": {
                        "profile_username": "owner",
                        "post_url_max_age_seconds": 7200,
                    }
                },
                channel="tiktok_carousel",
            )
        self.assertEqual(captured, fresh_url)

    def test_tiktok_short_link_resolver_returns_tracking_free_post_url(self) -> None:
        now = int(time.time())
        post_id = self._post_id(now - 30)
        final_url = (
            f"https://www.tiktok.com/@owner/photo/{post_id}"
            "?_r=1&_t=tracking"
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def geturl(self) -> str:
                return final_url

            def read(self, _size: int) -> bytes:
                return b""

        with patch.object(post_link, "urlopen", return_value=Response()):
            resolved = post_link._resolve_tiktok_short_url(
                "https://vm.tiktok.com/abc123/"
            )
        self.assertEqual(
            resolved,
            f"https://www.tiktok.com/@owner/photo/{post_id}",
        )

    def test_clipboard_fallback_ignores_unsupported_shell_and_uses_dumpsys(
        self,
    ) -> None:
        post_id = self._post_id(int(time.time()))

        class ShellResult:
            def __init__(self, output: str) -> None:
                self.output = output

        class Device:
            clipboard = ""

            def shell(self, command: str) -> ShellResult:
                if command == "dumpsys clipboard":
                    return ShellResult(
                        f"ClipData.Item {{ T=https://vm.tiktok.com/{post_id}/ }}"
                    )
                return ShellResult("No shell command implementation.")

            def dump_hierarchy(self) -> str:
                return "<hierarchy />"

        text = post_link._read_clipboard_robust(Device())
        self.assertIn("vm.tiktok.com", text)

    def test_termux_clipboard_is_replaced_with_sentinel_before_copy(self) -> None:
        class Device:
            def shell(self, _command: str):
                raise AssertionError("Termux clipboard must be preferred")

        with (
            patch.object(
                post_link.shutil,
                "which",
                return_value="/data/data/com.termux/files/usr/bin/termux-clipboard-set",
            ),
            patch.object(post_link.subprocess, "run") as run,
        ):
            post_link._clear_clipboard(Device())
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            [
                "/data/data/com.termux/files/usr/bin/termux-clipboard-set",
                post_link._CLIPBOARD_SENTINEL,
            ],
        )

    def test_activity_aweme_id_is_a_clipboard_independent_fallback(self) -> None:
        post_id = self._post_id(int(time.time()))

        class ShellResult:
            def __init__(self, output: str) -> None:
                self.output = output

        class Device:
            clipboard = ""

            def shell(self, command: str) -> ShellResult:
                if command == "dumpsys activity top":
                    return ShellResult(f"data=aweme/detail/{post_id}")
                return ShellResult("")

            def dump_hierarchy(self) -> str:
                return "<hierarchy />"

        self.assertEqual(
            post_link._tiktok_url_from_sources(
                Device(),
                channel="tiktok_carousel",
            ),
            f"https://www.tiktok.com/@_/photo/{post_id}",
        )

    def test_first_profile_post_is_leftmost_top_grid_tile(self) -> None:
        xml = """
        <hierarchy>
          <node clickable="true" bounds="[360,900][720,1260]" />
          <node clickable="true" bounds="[0,900][360,1260]" />
          <node clickable="true" bounds="[0,1260][360,1620]" />
        </hierarchy>
        """

        class Device:
            def window_size(self) -> tuple[int, int]:
                return 1080, 1920

            def dump_hierarchy(self) -> str:
                return xml

        self.assertEqual(
            post_link._tiktok_first_profile_post_center(Device()),
            (180, 1080),
        )

    def test_chain_icon_is_found_visually_as_first_solid_blue_circle(self) -> None:
        image = Image.new("RGB", (720, 1600), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((360, 1380, 432, 1452), fill=(46, 117, 253))
        draw.ellipse((462, 1380, 534, 1452), fill=(50, 140, 240))

        center = post_link._tiktok_chain_icon_center(image)

        self.assertIsNotNone(center)
        assert center is not None
        self.assertAlmostEqual(center[0], 396, delta=3)
        self.assertAlmostEqual(center[1], 1416, delta=3)

    def test_long_press_holds_photo_four_seconds_and_clicks_chain_once(self) -> None:
        image = Image.new("RGB", (720, 1600), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((360, 1380, 432, 1452), fill=(46, 117, 253))

        class ShellResult:
            output = ""

        class Device:
            clipboard = "https://vt.tiktok.com/new/"

            def __init__(self) -> None:
                self.shell_commands: list[str] = []
                self.clicks: list[tuple[int, int]] = []

            def window_size(self) -> tuple[int, int]:
                return 720, 1600

            def shell(self, command: str) -> ShellResult:
                self.shell_commands.append(command)
                return ShellResult()

            def screenshot(self):
                return image

            def click(self, x: int, y: int) -> None:
                self.clicks.append((x, y))

            def dump_hierarchy(self) -> str:
                return "<hierarchy />"

        device = Device()
        with (
            patch.object(post_link, "_clear_clipboard"),
            patch.object(post_link, "human_pause"),
            patch.object(post_link.time, "sleep"),
            patch.object(
                post_link,
                "_read_clipboard_robust",
                return_value="https://vt.tiktok.com/new/",
            ),
        ):
            url = post_link._tiktok_copy_link_via_long_press(device, {})

        self.assertEqual(
            device.shell_commands,
            ["input swipe 360 672 360 672 4000"],
        )
        self.assertEqual(len(device.clicks), 1)
        self.assertAlmostEqual(device.clicks[0][0], 396, delta=3)
        self.assertEqual(url, "https://vt.tiktok.com/new/")

    def test_recent_log_uses_freshest_tiktok_unique_id(self) -> None:
        now = int(time.time())
        old_id = self._post_id(now - 300)
        fresh_id = self._post_id(now - 20)

        class ShellResult:
            output = (
                f"[uniqueId={old_id}] old\n"
                f"[uniqueId={fresh_id}] click_share\n"
            )

        class Device:
            def shell(self, _command: str) -> ShellResult:
                return ShellResult()

        self.assertEqual(
            post_link._tiktok_url_from_recent_log(
                Device(),
                channel="tiktok_carousel",
                username="openhome.th",
                max_age_seconds=1800,
            ),
            f"https://www.tiktok.com/@openhome.th/photo/{fresh_id}",
        )


if __name__ == "__main__":
    unittest.main()

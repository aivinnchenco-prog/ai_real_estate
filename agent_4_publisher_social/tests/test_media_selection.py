from __future__ import annotations

import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from publisher_social.channels.base import (
    CAROUSEL_PUBLISH_CHANNELS,
    ChannelResult,
    carousel_bounds_pick_order,
    carousel_grid_pick_order,
    count_selected_gallery_photos,
    gallery_selection_state,
)
from publisher_social.channels import tiktok
from publisher_social.android.adb import push_files
from publisher_social.models import PublishJob


class MediaSelectionTests(unittest.TestCase):
    def test_carousel_publish_channels_include_all_carousel_networks(self) -> None:
        self.assertEqual(
            set(CAROUSEL_PUBLISH_CHANNELS),
            {
                "tiktok_carousel",
                "instagram_carousel",
                "linkedin",
                "twitter",
                "fb_groups",
            },
        )

    def test_carousel_bounds_pick_order_puts_bottom_right_first(self) -> None:
        bounds = [(400, 500, 500, 600), (0, 100, 100, 200)]
        ordered = carousel_bounds_pick_order(bounds)
        self.assertEqual(ordered[0], bounds[0])

    def test_gallery_selection_state_parses_russian_and_english(self) -> None:
        self.assertEqual(
            gallery_selection_state("Фото, 30 июля. Не выбрано"),
            "unselected",
        )
        self.assertEqual(
            gallery_selection_state("Photo taken on Jul 30. Selected"),
            "selected",
        )

    def test_count_selected_gallery_photos_ignores_unselected(self) -> None:
        xml = """
        <hierarchy>
          <node clickable="true" content-desc="Фото, 30 июля. Не выбрано" />
          <node clickable="true" content-desc="Фото, 29 июля. Выбрано" />
          <node clickable="true" content-desc="Сделать фото" />
        </hierarchy>
        """
        self.assertEqual(count_selected_gallery_photos(xml), 1)

    def test_carousel_grid_pick_order_reverses_newest_first_gallery(self) -> None:
        # Grid top row = newest (slide_09..07), bottom row = slide_03..01
        cells = [
            (100, 0, 10, 20),   # slide_09
            (100, 200, 210, 220),  # slide_08
            (100, 400, 410, 420),  # slide_07
            (300, 0, 10, 320),  # slide_06
            (300, 200, 210, 420),  # slide_05
            (300, 400, 410, 620),  # slide_04
            (500, 0, 10, 520),  # slide_03
            (500, 200, 210, 620),  # slide_02
            (500, 400, 410, 920),  # slide_01 (price hook)
        ]
        ordered = carousel_grid_pick_order(cells)
        self.assertEqual(ordered[0], cells[-1])
        self.assertEqual(ordered[-1], cells[0])

    def test_carousel_images_are_pushed_to_object_specific_album(self) -> None:
        device = Mock()
        device.media_dir = "/sdcard/Download/publisher_social"
        device.push.side_effect = (
            lambda _path, name, media_dir=None: f"{media_dir}/{name}"
        )
        device.run.return_value = Mock(returncode=0, stdout="ok", stderr="")

        with TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "slide_01.jpg"
            second = Path(temp_dir) / "slide_02.jpg"
            first.write_bytes(b"slide one")
            second.write_bytes(b"slide two")
            _, remote_images, _ = push_files(
                device,
                local_video=None,
                local_images=[str(first), str(second)],
                object_id="F_20260719_014",
            )

        albums = {path.rsplit("/", 1)[0] for path in remote_images}
        self.assertEqual(len(albums), 1)
        album = albums.pop()
        self.assertRegex(
            album,
            r"^/sdcard/Download/publisher_social/Carousel F_20260719_014$",
        )
        self.assertEqual(
            [path.rsplit("/", 1)[1] for path in remote_images],
            ["slide_01.jpg", "slide_02.jpg"],
        )

    def test_tiktok_uses_separate_video_and_carousel_albums(self) -> None:
        config = {
            "tiktok": {
                "video_album": "Видео",
                "carousel_album": "publisher_social",
            }
        }
        self.assertEqual(
            tiktok._album_name(config, {}, kind="video"),
            "Видео",
        )
        self.assertEqual(
            tiktok._album_name(config, {}, kind="carousel"),
            "publisher_social",
        )

    def test_tiktok_carousel_opens_album_before_selecting_photos(self) -> None:
        device = Mock()
        config = {
            "tiktok": {
                "carousel_album": "publisher_social",
                "carousel": {"max_images": 2},
            }
        }
        job = PublishJob(
            page_id="page",
            object_id="object",
            title="",
            caption_social="caption",
            caption_fb="",
            image_urls=["one.jpg", "two.jpg"],
            images_source="carousel_url",
        )
        patches = {
            "connect": patch(
                "publisher_social.channels.tiktok.connect_device",
                return_value=device,
            ),
            "open": patch("publisher_social.channels.tiktok._open_tiktok"),
            "create": patch("publisher_social.channels.tiktok._tap_create"),
            "upload": patch("publisher_social.channels.tiktok._tap_upload"),
            "album": patch("publisher_social.channels.tiktok._select_album"),
            "photos": patch(
                "publisher_social.channels.tiktok._select_photos",
                return_value=2,
            ),
            "next": patch("publisher_social.channels.tiktok._tap_next"),
            "publish_screen": patch(
                "publisher_social.channels.tiktok._on_publish_screen",
                return_value=True,
            ),
            "caption": patch("publisher_social.channels.tiktok._fill_caption"),
            "location": patch(
                "publisher_social.channels.tiktok._select_location",
                return_value="Phuket",
            ),
            "publish": patch(
                "publisher_social.channels.tiktok._publish_or_stop",
                return_value=ChannelResult(channel="tiktok_carousel", ok=True),
            ),
            "url": patch(
                "publisher_social.channels.tiktok.attach_post_url",
                side_effect=lambda _d, result, *_args, **_kwargs: result,
            ),
        }
        with ExitStack() as stack:
            started = {name: stack.enter_context(item) for name, item in patches.items()}
            result = tiktok.TikTokCarouselChannel().publish(
                job,
                dry_run=False,
                android_cfg={"ui_automation": {"enabled": True}},
                publisher_cfg=config,
                confirm_post=True,
            )

        self.assertTrue(result.ok)
        select_album = started["album"]
        select_album.assert_called_once_with(
            device,
            {"ui_automation": {"enabled": True}},
            "publisher_social",
        )

    def test_tiktok_multiselect_never_uses_unsafe_coordinate_fallback(self) -> None:
        device = Mock()
        selector = Mock()
        selector.exists.return_value = False
        device.return_value = selector
        with patch(
            "publisher_social.channels.tiktok.click_text_or_desc",
            return_value=False,
        ):
            enabled = tiktok._enable_tiktok_multi_select(device, {})

        self.assertFalse(enabled)
        device.click.assert_not_called()

    def test_tiktok_next_counter_label_is_supported(self) -> None:
        device = Mock()
        missing = Mock()
        missing.exists.return_value = False
        next_matches = MagicMock()
        next_matches.exists.return_value = True
        next_matches.count = 1
        next_button = Mock()
        next_button.info = {"text": "Далее (8)", "clickable": True}
        next_matches.__getitem__.return_value = next_button

        def select(**kwargs):
            if kwargs.get("textStartsWith") == "Далее":
                return next_matches
            return missing

        device.side_effect = select
        with patch("publisher_social.channels.tiktok.human_pause"):
            tiktok._tap_next(
                device,
                "com.ss.android.ugc.trill",
                {},
                stage="gallery",
            )

        next_button.click.assert_called_once()


if __name__ == "__main__":
    unittest.main()

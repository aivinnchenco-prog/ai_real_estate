from __future__ import annotations

import unittest
from unittest.mock import Mock

from publisher_social.channels.fb_groups import (
    _album_search_labels,
    _collect_discussion_photo_bounds,
    _discussion_object_photo_hits,
    _discussion_on_general_gallery,
    _fb_photo_checkbox_point,
    _open_discussion_gallery_album,
    _read_discussion_selected_count_from_xml,
    _selection_state_for_bounds,
    _select_discussion_photos,
    _verify_object_album,
)


class FbGroupsPhotoSelectionTests(unittest.TestCase):
    def test_album_search_labels_include_object_id_and_carousel_tail(self) -> None:
        album = "publisher_social_carousel_A_20260817_001_abcd1234ef56"
        labels = _album_search_labels(album, "A_20260817_001")
        self.assertEqual(labels[0], album)
        self.assertIn(album, labels)
        self.assertIn("publisher_social", labels)
        self.assertIn("A_20260817_001_abcd1234ef56", labels)

    def test_object_photo_hits_counts_slide_filenames(self) -> None:
        xml = """
        <hierarchy>
          <node content-desc="A_20260817_001_slide_01" />
          <node content-desc="A_20260817_001_slide_02" />
          <node content-desc="Photo taken on Jul 30" />
        </hierarchy>
        """
        self.assertEqual(_discussion_object_photo_hits(xml, "A_20260817_001"), 2)

    def test_collect_bounds_filters_by_object_id(self) -> None:
        xml = """
        <hierarchy>
          <node clickable="true" content-desc="A_20260817_001_slide_01" bounds="[400,500][600,700]" />
          <node clickable="true" content-desc="Фото, 29 июля" bounds="[0,500][200,700]" />
        </hierarchy>
        """
        all_bounds = _collect_discussion_photo_bounds(xml)
        self.assertEqual(len(all_bounds), 2)
        filtered = _collect_discussion_photo_bounds(xml, object_id="A_20260817_001")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0], (400, 500, 600, 700))

    def test_general_gallery_detected_by_header(self) -> None:
        xml = """
        <hierarchy>
          <node text="Все изображения" bounds="[0,100][400,180]" />
          <node clickable="true" content-desc="Фото, 30 июля" bounds="[0,500][200,700]" />
        </hierarchy>
        """
        self.assertTrue(_discussion_on_general_gallery(xml))

    def test_verify_rejects_all_images_even_with_many_tiles(self) -> None:
        xml = """
        <hierarchy>
          <node text="Все изображения" bounds="[0,100][400,180]" />
          <node clickable="true" content-desc="Фото, 30 июля" bounds="[0,500][200,700]" />
          <node clickable="true" content-desc="Фото, 29 июля" bounds="[220,500][420,700]" />
        </hierarchy>
        """
        device = Mock()
        device.dump_hierarchy.return_value = xml
        self.assertFalse(
            _verify_object_album(
                device,
                "A_20260817_001",
                "publisher_social_carousel_A_20260817_001_abcd",
            )
        )

    def test_open_album_does_not_skip_recent_gallery(self) -> None:
        recent_xml = """
        <hierarchy>
          <node text="Все изображения" bounds="[0,100][400,180]" />
          <node clickable="true" content-desc="Фото, 30 июля" bounds="[0,500][200,700]" />
          <node clickable="true" content-desc="Фото, 29 июля" bounds="[220,500][420,700]" />
          <node clickable="true" content-desc="Фото, 28 июля" bounds="[440,500][640,700]" />
        </hierarchy>
        """
        album_xml = """
        <hierarchy>
          <node text="publisher_social_carousel_A_20260817_001_abcd" bounds="[0,100][600,180]" />
          <node clickable="true" content-desc="slide_01" bounds="[0,500][200,700]" />
          <node clickable="true" content-desc="slide_02" bounds="[220,500][420,700]" />
        </hierarchy>
        """
        device = Mock()
        device.window_size.return_value = (1080, 2400)
        device.dump_hierarchy.side_effect = [
            recent_xml,
            recent_xml,
            recent_xml,
            album_xml,
            album_xml,
        ]

        picker = Mock()
        picker.exists.return_value = True
        picker.click = Mock()

        all_images = Mock()
        all_images.exists.return_value = True
        all_images.click = Mock()

        album_node = Mock()
        album_node.exists.return_value = True
        album_node.click = Mock()

        def _selector(**kwargs: object) -> Mock:
            raw = str(
                kwargs.get("descriptionContains")
                or kwargs.get("textContains")
                or kwargs.get("text")
                or kwargs.get("description")
                or ""
            )
            if "Выбор альбома" in raw:
                return picker
            if raw == "Все изображения":
                return all_images
            if "publisher_social_carousel" in raw or raw == "publisher_social_carousel_A_20260817_001_abcd":
                return album_node
            node = Mock()
            node.exists.return_value = False
            return node

        device.side_effect = _selector
        _open_discussion_gallery_album(
            device,
            {},
            album="publisher_social_carousel_A_20260817_001_abcd",
            object_id="A_20260817_001",
        )
        self.assertTrue(all_images.click.called or picker.click.called)
        album_node.click.assert_called()

    def test_collect_slide_filenames_without_photo_word(self) -> None:
        xml = """
        <hierarchy>
          <node content-desc="slide_01.jpg" bounds="[400,500][600,700]" />
          <node content-desc="slide_02.jpg" bounds="[0,500][200,700]" />
        </hierarchy>
        """
        bounds = _collect_discussion_photo_bounds(xml)
        self.assertEqual(len(bounds), 2)

    def test_collect_orders_bottom_right_first_for_price_hook(self) -> None:
        xml = """
        <hierarchy>
          <node clickable="true" content-desc="Фото, 30 июля" bounds="[0,500][200,700]" />
          <node clickable="true" content-desc="Фото, 29 июля" bounds="[400,500][600,700]" />
        </hierarchy>
        """
        bounds = _collect_discussion_photo_bounds(xml)
        self.assertEqual(len(bounds), 2)
        self.assertEqual(bounds[0], (400, 500, 600, 700))

    def test_checkbox_point_is_top_right_not_center(self) -> None:
        cx, cy = _fb_photo_checkbox_point((0, 200, 200, 400))
        self.assertGreater(cx, 150)
        self.assertLess(cy, 250)

    def test_read_selected_count_from_counter_label(self) -> None:
        xml = """
        <hierarchy>
          <node text="Выбрано 9" />
        </hierarchy>
        """
        self.assertEqual(_read_discussion_selected_count_from_xml(xml), 9)

    def test_read_selected_count_ignores_photo_date_numbers(self) -> None:
        xml = """
        <hierarchy>
          <node clickable="true" content-desc="Photo taken on Jul 30. Не выбрано" bounds="[400,500][600,700]" />
        </hierarchy>
        """
        self.assertIsNone(_read_discussion_selected_count_from_xml(xml))

    def test_selection_state_for_bounds(self) -> None:
        xml = """
        <hierarchy>
          <node clickable="true" content-desc="Фото, 1. Выбрано" bounds="[400,500][600,700]" />
          <node clickable="true" content-desc="Фото, 2. Не выбрано" bounds="[0,200][200,400]" />
        </hierarchy>
        """
        self.assertEqual(
            _selection_state_for_bounds(xml, (400, 500, 600, 700)),
            "selected",
        )
        self.assertEqual(
            _selection_state_for_bounds(xml, (0, 200, 200, 400)),
            "unselected",
        )

    def test_select_clicks_each_target_once_without_a11y_selected_state(self) -> None:
        xml = """
        <hierarchy>
          <node clickable="true" content-desc="Фото, 30 июля" bounds="[400,500][600,700]" />
          <node clickable="true" content-desc="Фото, 29 июля" bounds="[0,500][200,700]" />
          <node clickable="true" content-desc="Фото, 28 июля" bounds="[220,500][420,700]" />
        </hierarchy>
        """
        device = Mock()
        device.window_size.return_value = (1080, 2400)
        device.dump_hierarchy.return_value = xml

        def _selector(**kwargs: object) -> Mock:
            node = Mock()
            raw = str(kwargs.get("descriptionContains") or kwargs.get("text") or "")
            node.exists.return_value = "несколько" in raw.lower()
            return node

        device.side_effect = _selector
        count = _select_discussion_photos(device, {}, max_images=3)
        self.assertEqual(count, 3)
        self.assertGreaterEqual(device.click.call_count, 3)

    def test_select_skips_already_selected_thumbnails(self) -> None:
        xml = """
        <hierarchy>
          <node clickable="true" content-desc="Фото, 1. Выбрано" bounds="[400,500][600,700]" />
          <node clickable="true" content-desc="Фото, 2. Не выбрано" bounds="[0,500][200,700]" />
          <node text="Выбрано 1" />
        </hierarchy>
        """
        done_xml = """
        <hierarchy>
          <node clickable="true" content-desc="Фото, 1. Выбрано" bounds="[400,500][600,700]" />
          <node clickable="true" content-desc="Фото, 2. Выбрано" bounds="[0,500][200,700]" />
          <node text="Выбрано 2" />
        </hierarchy>
        """
        device = Mock()
        device.window_size.return_value = (1080, 2400)
        calls = {"n": 0}

        def _hierarchy() -> str:
            calls["n"] += 1
            if calls["n"] > 10:
                return done_xml
            return xml

        device.dump_hierarchy.side_effect = _hierarchy

        def _selector(**kwargs: object) -> Mock:
            node = Mock()
            raw = str(kwargs.get("descriptionContains") or kwargs.get("text") or "")
            node.exists.return_value = "несколько" in raw.lower()
            return node

        device.side_effect = _selector
        count = _select_discussion_photos(device, {}, max_images=2)
        self.assertEqual(count, 2)
        self.assertGreaterEqual(device.click.call_count, 1)

    def test_select_uses_long_press_when_multi_select_button_missing(self) -> None:
        xml = """
        <hierarchy>
          <node clickable="true" content-desc="Photo taken on Jul 30. Не выбрано" bounds="[400,500][600,700]" />
        </hierarchy>
        """
        done_xml = """
        <hierarchy>
          <node clickable="true" content-desc="Photo taken on Jul 30. Выбрано" bounds="[400,500][600,700]" />
          <node text="Выбрано 1" />
        </hierarchy>
        """
        device = Mock()
        device.window_size.return_value = (1080, 2400)
        device.dump_hierarchy.side_effect = [xml, done_xml, done_xml, done_xml]

        def _selector(**kwargs: object) -> Mock:
            node = Mock()
            node.exists.return_value = False
            return node

        device.side_effect = _selector
        count = _select_discussion_photos(device, {}, max_images=1)
        self.assertEqual(count, 1)
        device.long_click.assert_called_once()
        device.click.assert_not_called()


if __name__ == "__main__":
    unittest.main()

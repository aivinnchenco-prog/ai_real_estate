from __future__ import annotations

import unittest
from unittest.mock import Mock

from publisher_social.channels.fb_groups import (
    _collect_discussion_photo_bounds,
    _fb_photo_checkbox_point,
    _read_discussion_selected_count,
    _select_discussion_photos,
)


class FbGroupsPhotoSelectionTests(unittest.TestCase):
    def test_collect_orders_bottom_right_first_for_price_hook(self) -> None:
        xml = """
        <hierarchy>
          <node clickable="true" content-desc="Фото, 30 июля" bounds="[0,200][200,400]" />
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

    def test_select_taps_each_thumbnail_once_after_multi_select_button(self) -> None:
        xml = """
        <hierarchy>
          <node clickable="true" content-desc="Фото, 1" bounds="[400,500][600,700]" />
          <node clickable="true" content-desc="Фото, 2" bounds="[0,200][200,400]" />
          <node clickable="true" content-desc="Фото, 3" bounds="[220,200][420,400]" />
          <node text="Выбрано 3" />
        </hierarchy>
        """
        device = Mock()
        device.dump_hierarchy.return_value = xml

        def _selector(**kwargs: object) -> Mock:
            node = Mock()
            raw = str(kwargs.get("descriptionContains") or kwargs.get("text") or "")
            node.exists.return_value = "несколько" in raw.lower() or raw.lower() == "select multiple"
            return node

        device.side_effect = _selector
        count = _select_discussion_photos(device, {}, max_images=3)
        self.assertEqual(count, 3)
        self.assertEqual(device.click.call_count, 3)
        device.long_click.assert_not_called()

    def test_select_uses_long_press_when_multi_select_button_missing(self) -> None:
        xml = """
        <hierarchy>
          <node clickable="true" content-desc="Photo taken on Jul 30" bounds="[400,500][600,700]" />
        </hierarchy>
        """
        device = Mock()
        device.dump_hierarchy.return_value = xml

        def _selector(**kwargs: object) -> Mock:
            node = Mock()
            node.exists.return_value = False
            return node

        device.side_effect = _selector
        count = _select_discussion_photos(device, {}, max_images=1)
        self.assertEqual(count, 1)
        device.long_click.assert_called_once()


def _read_discussion_selected_count_from_xml(xml: str) -> int | None:
    device = Mock()
    device.dump_hierarchy.return_value = xml
    return _read_discussion_selected_count(device)


if __name__ == "__main__":
    unittest.main()

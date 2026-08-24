from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from publisher_social.channels.fb_groups import (
    _discussion_bottom_publish_targets,
    _discussion_tap_bottom_publish,
)


class FbGroupsPublishTests(unittest.TestCase):
    def test_bottom_targets_ignore_top_toolbar(self) -> None:
        xml = """
        <hierarchy>
          <node text="Опубликовать" bounds="[900,80][1040,160]" enabled="true" clickable="true" />
          <node text="Опубликовать" bounds="[820,2100][1040,2260]" enabled="true" clickable="true" />
        </hierarchy>
        """
        targets = _discussion_bottom_publish_targets(xml, screen_h=2400, screen_w=1080)
        self.assertEqual(len(targets), 1)
        self.assertGreater(targets[0][1], 1800)

    def test_tap_bottom_publish_uses_a11y_point_first(self) -> None:
        device = Mock()
        device.window_size.return_value = (1080, 2400)
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node text="Опубликовать" bounds="[820,2100][1040,2260]" enabled="true" clickable="true" />
        </hierarchy>
        """

        visible = Mock(side_effect=[True, False])

        with patch(
            "publisher_social.channels.fb_groups._discussion_bottom_publish_visible",
            visible,
        ), patch("publisher_social.channels.fb_groups.human_pause"):
            self.assertTrue(_discussion_tap_bottom_publish(device, {}))

        first_click = device.click.call_args_list[0][0]
        self.assertGreater(first_click[0], 900)
        self.assertGreater(first_click[1], 2100)
        self.assertLess(first_click[1], 2280)

    def test_coordinate_publish_points_not_at_screen_bottom(self) -> None:
        from publisher_social.channels.fb_groups import _discussion_coordinate_publish_points

        w, h = 1080, 1600
        for _x, y in _discussion_coordinate_publish_points(w, h):
            self.assertLess(y, int(h * 0.92), "tap must stay ~2cm above bottom edge")
            self.assertGreater(y, int(h * 0.80))


if __name__ == "__main__":
    unittest.main()

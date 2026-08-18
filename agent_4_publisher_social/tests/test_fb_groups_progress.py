from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from publisher_social import pipeline, state
from publisher_social.config import load_fb_groups


def _page(*, groups_done: bool | None = None) -> dict:
    props: dict = {
        "Объект ID": {"type": "rich_text", "rich_text": [{"plain_text": "A_20260811_001"}]},
        "Название объекта": {"type": "title", "title": [{"plain_text": "Test villa"}]},
    }
    if groups_done is not None:
        props["phone_fb_groups_done"] = {"type": "checkbox", "checkbox": groups_done}
    return {"id": "page-1", "properties": props}


def _config() -> dict:
    return {
        "channels": ["fb_groups", "fb_marketplace"],
        "notion": {
            "fields": {
                "object_id": "Объект ID",
                "title": "Название объекта",
                "photo": "Фото",
                "carousel_url": "carousel_url",
                "housing_type": "Тип жилья",
                "rooms": "Количество комнат",
                "bathrooms": "Количество сан.узлов",
                "price_monthly": "Цена за месяц",
                "district": "Район",
                "address": "Адрес",
                "google_maps": "Google Maps",
                "video_url_seedance": "video_url_Seedance",
                "video_url_vertical": "video_url_vertical",
                "caption_social": "Описание соц.сети",
                "caption_fb": "Описание для FB Marketplace",
                "cta_instagram": "CTA Instagram",
                "brand_open_home_url": "brand_open_home_url",
            },
            "phone_lock_fields": {
                "fb_groups_done": "phone_fb_groups_done",
            },
        },
        "fb_groups": {
            "groups_file": "config/fb_groups_list.txt",
            "max_groups_per_object": 13,
        },
        "media": {"allow_raw_gallery_fallback": False},
        "fb_marketplace": {},
    }


class FbGroupsProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "state.json"
        self.state_patch = patch(
            "publisher_social.state.state_path",
            return_value=self.state_path,
        )
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def test_mark_and_pending_groups(self) -> None:
        st = state.load_state()
        groups = load_fb_groups(_config())[:3]
        self.assertGreaterEqual(len(groups), 2)
        state.mark_fb_group_published(st, "A_20260811_001", groups[0])
        pending = state.pending_fb_group_urls(st, "A_20260811_001", groups)
        self.assertEqual(pending, groups[1:])
        self.assertFalse(state.all_fb_groups_published(st, "A_20260811_001", groups))
        state.mark_fb_group_published(st, "A_20260811_001", groups[1])
        state.mark_fb_group_published(st, "A_20260811_001", groups[2])
        self.assertTrue(state.all_fb_groups_published(st, "A_20260811_001", groups))

    def test_build_job_uses_only_pending_groups(self) -> None:
        groups = load_fb_groups(_config())[:2]
        st = state.load_state()
        state.mark_fb_group_published(st, "A_20260811_001", groups[0])
        with patch("publisher_social.pipeline.load_fb_groups", return_value=groups):
            job = pipeline.build_job_from_page(_page(groups_done=False), config=_config())
        self.assertEqual(job.fb_groups, [groups[1]])

    def test_unchecking_phone_done_clears_group_progress(self) -> None:
        groups = load_fb_groups(_config())[:1]
        st = state.load_state()
        state.mark_fb_group_published(st, "A_20260811_001", groups[0])
        state.mark_channel_done(st, "A_20260811_001", "fb_groups")
        pipeline.pending_channels_for_page(
            _page(groups_done=False),
            object_id="A_20260811_001",
            config=_config(),
        )
        st = state.load_state()
        self.assertFalse(state.is_fb_group_published(st, "A_20260811_001", groups[0]))


if __name__ == "__main__":
    unittest.main()

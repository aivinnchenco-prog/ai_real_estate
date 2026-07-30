from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from publisher_social import pipeline, state
from publisher_social.channels.base import ChannelResult
from publisher_social.models import PublishJob


def make_job(object_id: str = "object-1") -> PublishJob:
    return PublishJob(
        page_id=f"page-{object_id}",
        object_id=object_id,
        title="Title",
        caption_social="Caption",
        caption_fb="FB caption",
        channels_pending=["tiktok_carousel"],
    )


def page_for(object_id: str = "object-1") -> dict:
    return {
        "id": f"page-{object_id}",
        "properties": {
            "Object ID": {
                "type": "rich_text",
                "rich_text": [{"plain_text": object_id}],
            }
        },
    }


class UrlSafetyPipelineTests(unittest.TestCase):
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

    def test_post_url_must_match_exact_host_and_publication_type(self) -> None:
        valid = (
            ("https://www.tiktok.com/@owner/video/123", "tiktok"),
            ("https://www.tiktok.com/@owner/photo/456", "tiktok_carousel"),
            ("https://vm.tiktok.com/ZMshort/", "tiktok_carousel"),
            ("https://www.instagram.com/reel/ABC/", "instagram_reel"),
            ("https://www.instagram.com/p/DEF/", "instagram_carousel"),
        )
        invalid = (
            ("https://www.tiktok.com/@owner/video/123", "tiktok_carousel"),
            ("https://www.tiktok.com/@owner/photo/456", "tiktok"),
            ("https://www.instagram.com/reel/old/", "instagram_carousel"),
            ("https://www.instagram.com/p/wrong-type/", "instagram_reel"),
            ("https://www.tiktok.com.evil.invalid/@owner/photo/456", "tiktok_carousel"),
            ("https://metricool.com/planner/post/123", "instagram_carousel"),
        )

        for url, channel in valid:
            with self.subTest(url=url, channel=channel):
                self.assertTrue(pipeline._is_real_post_url(url, channel))
        for url, channel in invalid:
            with self.subTest(url=url, channel=channel):
                self.assertFalse(pipeline._is_real_post_url(url, channel))

    def test_verified_status_is_downgraded_when_url_type_is_wrong(self) -> None:
        result = ChannelResult(
            channel="instagram_carousel",
            ok=True,
            post_url="https://www.instagram.com/reel/old/",
            publication_status="verified",
        )

        status = pipeline._result_publication_status(
            result,
            "instagram_carousel",
            {"verification": {"url_optional_channels": []}},
            dry_run=False,
            confirm_post=True,
        )

        self.assertEqual(status, "submitted_unverified")

    def test_unverified_result_never_writes_url_to_notion(self) -> None:
        cfg = {
            "notion": {
                "fields": {
                    "post_url_instagram_carousel": "Carousel URL",
                }
            }
        }
        result = ChannelResult(
            channel="instagram_carousel",
            ok=True,
            post_url="https://www.instagram.com/p/possibly-old/",
            publication_status="submitted_unverified",
        )

        with patch("publisher_social.pipeline.load_publisher_config", return_value=cfg):
            with patch("publisher_social.pipeline.notion.update_fields") as update:
                pipeline._try_write_notion_result(
                    make_job(),
                    "instagram_carousel",
                    result,
                )

        update.assert_not_called()
        self.assertEqual(state.pending_notion_updates(state.load_state()), [])

    def test_tiktok_carousel_url_verifies_locally_without_overwriting_video_field(
        self,
    ) -> None:
        cfg = {
            "notion": {
                "fields": {
                    "object_id": "Object ID",
                    # This field belongs to the TikTok video, not the carousel.
                    "post_url_tiktok": "TikTok video URL",
                }
            }
        }
        url = "https://www.tiktok.com/@owner/photo/456"

        with patch("publisher_social.pipeline.load_publisher_config", return_value=cfg):
            with patch("publisher_social.pipeline.notion.update_fields") as update:
                pipeline.record_captured_post_url(
                    page_for(),
                    "tiktok_carousel",
                    url,
                )

        done = state.load_state()["objects"]["object-1"]["channels_done"][
            "tiktok_carousel"
        ]
        self.assertEqual(done["status"], "verified")
        self.assertEqual(done["post_url"], url)
        update.assert_not_called()
        self.assertEqual(state.pending_notion_updates(state.load_state()), [])

    def test_tiktok_carousel_uses_only_its_dedicated_notion_field(self) -> None:
        cfg = {
            "notion": {
                "fields": {
                    "object_id": "Object ID",
                    "post_url_tiktok": "TikTok video URL",
                    "post_url_tiktok_carousel": "TikTok carousel URL",
                }
            }
        }
        url = "https://www.tiktok.com/@owner/photo/456"

        with patch("publisher_social.pipeline.load_publisher_config", return_value=cfg):
            with patch("publisher_social.pipeline.notion.update_fields") as update:
                pipeline.record_captured_post_url(
                    page_for(),
                    "tiktok_carousel",
                    url,
                )

        properties = update.call_args.args[1]
        self.assertEqual(properties, {"TikTok carousel URL": {"url": url}})
        self.assertNotIn("TikTok video URL", properties)

    def test_wrong_manual_url_does_not_change_state_or_notion(self) -> None:
        cfg = {
            "notion": {
                "fields": {
                    "object_id": "Object ID",
                    "post_url_instagram_carousel": "Carousel URL",
                }
            }
        }

        with patch("publisher_social.pipeline.load_publisher_config", return_value=cfg):
            with patch("publisher_social.pipeline.notion.update_fields") as update:
                with self.assertRaises(ValueError):
                    pipeline.record_captured_post_url(
                        page_for(),
                        "instagram_carousel",
                        "https://www.instagram.com/reel/not-a-carousel/",
                    )

        update.assert_not_called()
        self.assertNotIn("object-1", state.load_state()["objects"])

    def test_wrong_type_notion_url_is_not_reconciled_as_completed(self) -> None:
        cfg = {
            "channels": ["instagram_carousel"],
            "notion": {
                "fields": {
                    "post_url_instagram_carousel": "Carousel URL",
                }
            },
        }
        page = {
            "id": "page-object-1",
            "properties": {
                "Carousel URL": {
                    "type": "url",
                    "url": "https://www.instagram.com/reel/old-or-foreign/",
                }
            },
        }

        reconciled = pipeline.reconcile_local_completed_from_notion(
            page,
            make_job(),
            cfg,
        )

        self.assertEqual(reconciled, [])
        self.assertFalse(
            state.is_channel_done(
                state.load_state(),
                "object-1",
                "instagram_carousel",
            )
        )

    def test_live_result_with_foreign_url_is_saved_unverified_without_notion_write(
        self,
    ) -> None:
        cfg = {
            "channels": ["instagram_carousel"],
            "limits": {"per_channel_daily_max": {}},
            "notion": {
                "fields": {
                    "post_url_instagram_carousel": "Carousel URL",
                }
            },
            "orchestration": {"one_channel_per_live_run": True},
            "verification": {"url_optional_channels": []},
        }
        channel = Mock()
        channel.publish.return_value = ChannelResult(
            channel="instagram_carousel",
            ok=True,
            post_url="https://www.instagram.com/reel/old-post/",
            publication_status="verified",
        )

        with patch("publisher_social.pipeline.load_publisher_config", return_value=cfg):
            with patch("publisher_social.pipeline.load_android_config", return_value={}):
                with patch(
                    "publisher_social.pipeline.prepare_job",
                    side_effect=lambda job, **_: job,
                ):
                    with patch(
                        "publisher_social.pipeline.get_channel",
                        return_value=channel,
                    ):
                        with patch(
                            "publisher_social.pipeline.notion.update_fields"
                        ) as update:
                            results = pipeline.run_channels(
                                make_job(),
                                channels=["instagram_carousel"],
                                dry_run=False,
                                confirm_post=True,
                            )

        self.assertEqual(results[0].publication_status, "submitted_unverified")
        self.assertIsNone(results[0].post_url)
        done = state.load_state()["objects"]["object-1"]["channels_done"][
            "instagram_carousel"
        ]
        self.assertEqual(done["status"], "submitted_unverified")
        self.assertIsNone(done["post_url"])
        update.assert_not_called()


if __name__ == "__main__":
    unittest.main()

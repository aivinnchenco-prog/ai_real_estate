from __future__ import annotations

import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from publisher_social.channels.base import ChannelResult
from publisher_social.models import PublishJob
from publisher_social import pipeline, state
from publisher_social import __main__ as cli


def make_job(
    object_id: str = "object-1",
    *,
    pending: list[str] | None = None,
) -> PublishJob:
    return PublishJob(
        page_id=f"page-{object_id}",
        object_id=object_id,
        title="Title",
        caption_social="Caption",
        caption_fb="FB caption",
        channels_pending=list(pending or []),
    )


def base_config(*, daily: dict[str, int] | None = None) -> dict:
    return {
        "timezone": "Asia/Bangkok",
        "channels": ["tiktok", "tiktok_carousel", "instagram_carousel"],
        "limits": {"per_channel_daily_max": daily or {}},
        "notion": {"fields": {}},
        "orchestration": {
            "one_channel_per_live_run": True,
            "automatic_live_retries": False,
            "video_channels": ["tiktok"],
            "carousel_channels": [],
            "final_channels": [],
            "delayed_carousel": {
                "tiktok_carousel": {"after": "tiktok", "delay_minutes": 60},
                "instagram_carousel": {
                    "after": "instagram_reel",
                    "delay_minutes": 60,
                },
            },
        },
        "verification": {
            "url_optional_channels": ["fb_groups", "fb_marketplace"],
        },
    }


class PipelineTests(unittest.TestCase):
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

    def test_normal_queue_excludes_delayed_but_explicit_channel_can_override(self) -> None:
        cfg = base_config()
        job = make_job(
            pending=["tiktok", "tiktok_carousel", "instagram_carousel"],
        )
        self.assertEqual(
            pipeline.immediate_channels_for_queue(job, cfg),
            ["tiktok"],
        )
        self.assertEqual(
            pipeline.immediate_channels_for_queue(
                job,
                cfg,
                requested=["tiktok_carousel"],
            ),
            ["tiktok_carousel"],
        )

    def test_missing_url_is_warning_except_for_optional_url_channels(self) -> None:
        cfg = base_config()
        submitted = ChannelResult(channel="tiktok", ok=True)
        marketplace = ChannelResult(channel="fb_marketplace", ok=True)
        self.assertEqual(
            pipeline._result_publication_status(
                submitted,
                "tiktok",
                cfg,
                dry_run=False,
                confirm_post=True,
            ),
            "submitted_unverified",
        )
        self.assertEqual(
            pipeline._result_publication_status(
                marketplace,
                "fb_marketplace",
                cfg,
                dry_run=False,
                confirm_post=True,
            ),
            "accepted",
        )

    def test_missing_schedule_is_recovered_from_real_parent_url(self) -> None:
        cfg = base_config()
        cfg["notion"]["fields"] = {
            "post_url_tiktok": "TikTok URL",
        }
        page = {
            "id": "page-object-1",
            "properties": {
                "TikTok URL": {
                    "type": "url",
                    "url": "https://www.tiktok.com/@account/video/123",
                }
            },
        }
        job = make_job("object-1", pending=["tiktok_carousel"])
        fixed_now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)

        first = pipeline.reconcile_missing_delayed_schedules(
            page,
            job,
            cfg,
            now=fixed_now,
        )
        second = pipeline.reconcile_missing_delayed_schedules(
            page,
            job,
            cfg,
            now=fixed_now,
        )

        self.assertEqual(first, ["tiktok_carousel"])
        self.assertEqual(second, [])
        entry = state.load_state()["scheduled"]["object-1:tiktok_carousel"]
        self.assertEqual(entry["publish_after"], fixed_now.isoformat())
        self.assertEqual(entry["after_channel"], "tiktok")

    def test_real_notion_url_is_reconciled_into_local_verified_state(self) -> None:
        cfg = base_config()
        cfg["notion"]["fields"] = {
            "post_url_tiktok": "TikTok URL",
        }
        page = {
            "id": "page-object-1",
            "properties": {
                "TikTok URL": {
                    "type": "url",
                    "url": "https://www.tiktok.com/@account/video/123",
                }
            },
        }
        job = make_job("object-1", pending=["tiktok_carousel"])

        reconciled = pipeline.reconcile_local_completed_from_notion(page, job, cfg)

        self.assertEqual(reconciled, ["tiktok"])
        done = state.load_state()["objects"]["object-1"]["channels_done"]["tiktok"]
        self.assertEqual(done["status"], "verified")
        self.assertIn("tiktok.com", done["post_url"])
        self.assertEqual(state.load_state()["daily"], {})

    def test_queue_consumes_due_then_runs_only_immediate_channels(self) -> None:
        cfg = base_config()
        job = make_job("queue", pending=["tiktok", "tiktok_carousel"])
        args = Namespace(
            channels=None,
            limit=None,
            dry_run=True,
            push_media=False,
            ui=False,
            live=False,
        )
        with patch("publisher_social.__main__.run_scheduled", return_value=[]) as scheduled:
            with patch(
                "publisher_social.__main__.fetch_queue_pages",
                return_value=([{"id": job.page_id}], []),
            ):
                with patch("publisher_social.__main__.load_publisher_config", return_value=cfg):
                    with patch(
                        "publisher_social.__main__.build_job_from_page",
                        return_value=job,
                    ):
                        with patch(
                            "publisher_social.__main__.run_channels",
                            return_value=[],
                        ) as run:
                            with redirect_stdout(StringIO()):
                                code = cli.cmd_queue(args)

        self.assertEqual(code, 0)
        scheduled.assert_called_once()
        self.assertEqual(run.call_args.kwargs["channels"], ["tiktok"])

    def test_live_run_channels_executes_only_first_requested_channel(self) -> None:
        cfg = base_config()
        channel = Mock()
        channel.publish.return_value = ChannelResult(
            channel="tiktok",
            ok=True,
            post_url="https://www.tiktok.com/@account/video/123",
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
                    ) as get_channel:
                        with patch("publisher_social.pipeline._try_write_notion_result"):
                            with patch("publisher_social.pipeline._maybe_write_published_at"):
                                results = pipeline.run_channels(
                                    make_job(
                                        "stepwise",
                                        pending=["tiktok", "instagram_carousel"],
                                    ),
                                    channels=["tiktok", "instagram_carousel"],
                                    dry_run=False,
                                    confirm_post=True,
                                )

        self.assertEqual([result.channel for result in results], ["tiktok"])
        get_channel.assert_called_once_with("tiktok")

    def test_live_run_skips_done_channel_before_choosing_single_action(self) -> None:
        snapshot = state.load_state()
        state.mark_channel_done(
            snapshot,
            "stepwise-next",
            "tiktok",
            status="verified",
        )
        cfg = base_config()
        channel = Mock()
        channel.publish.return_value = ChannelResult(
            channel="instagram_carousel",
            ok=True,
            post_url="https://www.instagram.com/p/new/",
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
                    ) as get_channel:
                        with patch("publisher_social.pipeline._try_write_notion_result"):
                            results = pipeline.run_channels(
                                make_job(
                                    "stepwise-next",
                                    pending=["tiktok", "instagram_carousel"],
                                ),
                                channels=["tiktok", "instagram_carousel"],
                                dry_run=False,
                                confirm_post=True,
                            )

        self.assertEqual(
            [result.channel for result in results],
            ["instagram_carousel"],
        )
        get_channel.assert_called_once_with("instagram_carousel")

    def test_live_retry_is_disabled_in_stepwise_mode(self) -> None:
        job = make_job("one-attempt", pending=["tiktok"])
        failed = ChannelResult(channel="tiktok", ok=False, reason="needs review")
        cfg = base_config()
        with patch("publisher_social.pipeline.load_publisher_config", return_value=cfg):
            with patch("publisher_social.pipeline.load_android_config", return_value={}):
                with patch("publisher_social.android.ui.connect_device"):
                    with patch("publisher_social.android.ui.ensure_unlocked"):
                        with patch("publisher_social.pipeline.reset_uiautomator"):
                            with patch(
                                "publisher_social.pipeline.check_adb",
                                return_value={"ok": True},
                            ):
                                with patch(
                                    "publisher_social.pipeline.run_channels",
                                    return_value=[failed],
                                ) as run:
                                    results = pipeline.run_channels_with_retry(
                                        job,
                                        channels=["tiktok"],
                                        dry_run=False,
                                        confirm_post=True,
                                        max_attempts=5,
                                    )

        self.assertEqual(results, [failed])
        run.assert_called_once()

    def test_due_scheduled_entry_already_done_is_consumed_without_notion(self) -> None:
        snapshot = state.load_state()
        state.mark_channel_done(
            snapshot,
            "object-1",
            "tiktok_carousel",
            status="verified",
        )
        state.schedule_channel(
            snapshot,
            object_id="object-1",
            page_id="page-object-1",
            channel="tiktok_carousel",
            publish_after="2000-01-01T00:00:00+00:00",
        )

        with patch("publisher_social.pipeline.load_publisher_config", return_value=base_config()):
            with patch("publisher_social.pipeline.notion.get_page") as get_page:
                results = pipeline.run_scheduled(dry_run=False)

        get_page.assert_not_called()
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0][1].skipped)
        self.assertEqual(state.load_state()["scheduled"], {})

    def test_scheduled_filter_never_consumes_another_object(self) -> None:
        snapshot = state.load_state()
        for object_id in ("target", "other"):
            state.mark_channel_done(
                snapshot,
                object_id,
                "tiktok_carousel",
                status="verified",
            )
            state.schedule_channel(
                snapshot,
                object_id=object_id,
                page_id=f"page-{object_id}",
                channel="tiktok_carousel",
                publish_after="2000-01-01T00:00:00+00:00",
            )

        with patch("publisher_social.pipeline.load_publisher_config", return_value=base_config()):
            results = pipeline.run_scheduled(
                dry_run=False,
                object_id="target",
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0].object_id, "target")
        scheduled = state.load_state()["scheduled"]
        self.assertNotIn("target:tiktok_carousel", scheduled)
        self.assertIn("other:tiktok_carousel", scheduled)

    def test_successful_due_scheduled_entry_is_removed(self) -> None:
        snapshot = state.load_state()
        state.schedule_channel(
            snapshot,
            object_id="object-1",
            page_id="page-object-1",
            channel="tiktok_carousel",
            publish_after="2000-01-01T00:00:00+00:00",
        )
        job = make_job("object-1", pending=["tiktok_carousel"])
        success = ChannelResult(
            channel="tiktok_carousel",
            ok=True,
            publication_status="submitted_unverified",
        )
        with patch("publisher_social.pipeline.load_publisher_config", return_value=base_config()):
            with patch("publisher_social.pipeline.notion.get_page", return_value={"id": job.page_id}):
                with patch("publisher_social.pipeline.build_job_from_page", return_value=job):
                    with patch(
                        "publisher_social.pipeline.run_channels",
                        return_value=[success],
                    ):
                        results = pipeline.run_scheduled(dry_run=False)

        self.assertEqual(results[0][1], success)
        self.assertEqual(state.load_state()["scheduled"], {})

    def test_scheduled_daily_limit_skip_stays_queued(self) -> None:
        snapshot = state.load_state()
        state.schedule_channel(
            snapshot,
            object_id="object-1",
            page_id="page-object-1",
            channel="tiktok_carousel",
            publish_after="2000-01-01T00:00:00+00:00",
        )
        job = make_job("object-1", pending=["tiktok_carousel"])
        limited = ChannelResult(
            channel="tiktok_carousel",
            ok=False,
            skipped=True,
            reason="daily_limit_reached",
            publication_status="skipped",
        )
        with patch("publisher_social.pipeline.load_publisher_config", return_value=base_config()):
            with patch("publisher_social.pipeline.notion.get_page", return_value={"id": job.page_id}):
                with patch("publisher_social.pipeline.build_job_from_page", return_value=job):
                    with patch(
                        "publisher_social.pipeline.run_channels",
                        return_value=[limited],
                    ):
                        pipeline.run_scheduled(dry_run=False)

        self.assertIn(
            "object-1:tiktok_carousel",
            state.load_state()["scheduled"],
        )

    def test_daily_limit_blocks_second_object_and_counts_once(self) -> None:
        cfg = base_config(daily={"tiktok": 1})
        channel = Mock()
        channel.publish.return_value = ChannelResult(
            channel="tiktok",
            ok=True,
            post_url="https://www.tiktok.com/@account/video/123",
            publication_status="verified",
        )

        patches = (
            patch("publisher_social.pipeline.load_publisher_config", return_value=cfg),
            patch("publisher_social.pipeline.load_android_config", return_value={}),
            patch(
                "publisher_social.pipeline.prepare_job",
                side_effect=lambda job, **_: job,
            ),
            patch("publisher_social.pipeline.get_channel", return_value=channel),
            patch("publisher_social.pipeline._try_write_notion_result"),
            patch("publisher_social.pipeline._maybe_write_published_at"),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            first = pipeline.run_channels(
                make_job("one", pending=["tiktok"]),
                channels=["tiktok"],
                dry_run=False,
                confirm_post=True,
            )
            second = pipeline.run_channels(
                make_job("two", pending=["tiktok"]),
                channels=["tiktok"],
                dry_run=False,
                confirm_post=True,
            )

        self.assertEqual(first[0].publication_status, "verified")
        self.assertEqual(second[0].reason.split()[0], "daily_limit_reached")
        self.assertEqual(channel.publish.call_count, 1)
        day = pipeline._local_day(cfg, now=datetime.now(timezone.utc))
        self.assertEqual(state.daily_count(state.load_state(), day, "tiktok"), 1)

    def test_retry_only_retries_real_failures(self) -> None:
        job = make_job("retry", pending=["tiktok"])
        failed = ChannelResult(channel="tiktok", ok=False, reason="temporary")
        success = ChannelResult(
            channel="tiktok",
            ok=True,
            publication_status="submitted_unverified",
        )
        with patch("publisher_social.pipeline.load_android_config", return_value={}):
            with patch("publisher_social.pipeline.reset_uiautomator"):
                with patch(
                    "publisher_social.pipeline.check_adb",
                    return_value={"ok": True},
                ):
                    with patch(
                        "publisher_social.pipeline.run_channels",
                        side_effect=[[failed], [success]],
                    ) as run:
                        results = pipeline.run_channels_with_retry(
                            job,
                            channels=["tiktok"],
                            dry_run=True,
                            max_attempts=3,
                        )

        self.assertEqual(run.call_count, 2)
        self.assertEqual(results[-1].publication_status, "submitted_unverified")

    def test_retry_never_republishes_done_channel(self) -> None:
        snapshot = state.load_state()
        state.mark_channel_done(
            snapshot,
            "done",
            "tiktok",
            status="submitted_unverified",
        )
        with patch("publisher_social.pipeline.load_android_config", return_value={}):
            with patch("publisher_social.pipeline.run_channels") as run:
                results = pipeline.run_channels_with_retry(
                    make_job("done", pending=["tiktok"]),
                    channels=["tiktok"],
                    dry_run=True,
                )

        run.assert_not_called()
        self.assertTrue(results[0].skipped)
        self.assertIn("retry suppressed", results[0].reason)

    def test_interrupted_live_attempt_requires_manual_verification(self) -> None:
        snapshot = state.load_state()
        state.mark_channel_inflight(snapshot, "interrupted", "tiktok")
        cfg = base_config()
        with patch("publisher_social.pipeline.load_publisher_config", return_value=cfg):
            with patch("publisher_social.pipeline.load_android_config", return_value={}):
                with patch(
                    "publisher_social.pipeline.prepare_job",
                    side_effect=lambda job, **_: job,
                ):
                    with patch("publisher_social.pipeline.get_channel") as get_channel:
                        results = pipeline.run_channels(
                            make_job("interrupted", pending=["tiktok"]),
                            channels=["tiktok"],
                            dry_run=False,
                            confirm_post=True,
                        )

        get_channel.assert_not_called()
        self.assertTrue(results[0].skipped)
        self.assertEqual(results[0].publication_status, "submitted_unverified")
        self.assertIn("manual_verification_required", results[0].reason)

    def test_failed_notion_write_stays_in_outbox_then_recovers(self) -> None:
        cfg = {
            "notion": {
                "fields": {
                    "post_url_tiktok": "post_url_tiktok",
                }
            }
        }
        result = ChannelResult(
            channel="tiktok",
            ok=True,
            post_url="https://www.tiktok.com/@account/video/123",
            publication_status="verified",
        )
        with patch("publisher_social.pipeline.load_publisher_config", return_value=cfg):
            with patch(
                "publisher_social.pipeline.notion.update_fields",
                side_effect=RuntimeError("temporary"),
            ):
                pipeline._try_write_notion_result(make_job(), "tiktok", result)

            self.assertEqual(len(state.pending_notion_updates(state.load_state())), 1)

            with patch("publisher_social.pipeline.notion.update_fields") as update:
                delivered, failed = pipeline.retry_pending_notion_updates()

        self.assertEqual((delivered, failed), (1, 0))
        update.assert_called_once()
        self.assertEqual(state.pending_notion_updates(state.load_state()), [])

    def test_manual_captured_url_upgrades_local_status(self) -> None:
        snapshot = state.load_state()
        state.mark_channel_done(
            snapshot,
            "object-1",
            "tiktok",
            status="submitted_unverified",
        )
        cfg = {
            "notion": {
                "fields": {
                    "object_id": "Object ID",
                    "post_url_tiktok": "post_url_tiktok",
                }
            }
        }
        page = {
            "id": "page-object-1",
            "properties": {
                "Object ID": {
                    "type": "rich_text",
                    "rich_text": [{"plain_text": "object-1"}],
                }
            },
        }
        with patch("publisher_social.pipeline.load_publisher_config", return_value=cfg):
            with patch("publisher_social.pipeline.notion.update_fields"):
                pipeline.record_captured_post_url(
                    page,
                    "tiktok",
                    "https://www.tiktok.com/@account/video/123",
                )

        channel_state = state.load_state()["objects"]["object-1"]["channels_done"]["tiktok"]
        self.assertEqual(channel_state["status"], "verified")
        self.assertEqual(
            channel_state["post_url"],
            "https://www.tiktok.com/@account/video/123",
        )

    def test_invalidated_url_is_cleared_without_allowing_republish(self) -> None:
        snapshot = state.load_state()
        state.mark_channel_done(
            snapshot,
            "object-1",
            "instagram_carousel",
            status="verified",
            post_url="https://www.instagram.com/p/old/",
        )
        cfg = {
            "notion": {
                "fields": {
                    "object_id": "Object ID",
                    "post_url_instagram_carousel": "Carousel URL",
                }
            }
        }
        page = {
            "id": "page-object-1",
            "properties": {
                "Object ID": {
                    "type": "rich_text",
                    "rich_text": [{"plain_text": "object-1"}],
                }
            },
        }
        with patch("publisher_social.pipeline.load_publisher_config", return_value=cfg):
            with patch("publisher_social.pipeline.notion.update_fields") as update:
                pipeline.invalidate_captured_post_url(
                    page,
                    "instagram_carousel",
                    reason="captured URL did not belong to the new post",
                )

        channel_state = state.load_state()["objects"]["object-1"]["channels_done"][
            "instagram_carousel"
        ]
        self.assertEqual(channel_state["status"], "submitted_unverified")
        self.assertIsNone(channel_state["post_url"])
        self.assertTrue(
            state.is_channel_done(
                state.load_state(),
                "object-1",
                "instagram_carousel",
            )
        )
        properties = update.call_args.args[1]
        self.assertEqual(properties["Carousel URL"], {"url": None})


if __name__ == "__main__":
    unittest.main()

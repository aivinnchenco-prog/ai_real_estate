from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from publisher_social.channels.base import ChannelResult
from publisher_social.facebook_batch import (
    resolve_facebook_batch_config,
    run_facebook_phone_batch,
    summarize_facebook_batch_status,
)
from publisher_social.models import PublishJob
from publisher_social import pipeline, state


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "termux" / "bin" / "run_loop.sh"


def make_job(
    object_id: str = "A_20260806_001",
    *,
    pending: list[str] | None = None,
) -> PublishJob:
    return PublishJob(
        page_id=f"page-{object_id}",
        object_id=object_id,
        title="Title",
        caption_social="Caption",
        caption_fb="FB caption",
        channels_pending=list(pending or ["fb_groups", "fb_marketplace"]),
        image_urls=["https://example.invalid/carousel/1.jpg"],
        marketplace_image_urls=["https://example.invalid/brand/1.jpg"],
    )


def fb_config(**batch_overrides) -> dict:
    batch = {
        "enabled": True,
        "channels": ["fb_groups", "fb_marketplace"],
        "delay_between_channels_seconds": 25,
        "stop_on_failure": True,
    }
    batch.update(batch_overrides)
    return {
        "channels": ["fb_groups", "fb_marketplace"],
        "notion": {"fields": {}},
        "verification": {"url_optional_channels": ["fb_groups", "fb_marketplace"]},
        "facebook_batch": batch,
    }


class FacebookBatchConfigTests(unittest.TestCase):
    def test_missing_block_uses_safe_fallback(self) -> None:
        cfg = resolve_facebook_batch_config({})
        self.assertIsNotNone(cfg)
        assert cfg is not None
        self.assertEqual(cfg["channels"], ["fb_groups", "fb_marketplace"])
        self.assertEqual(cfg["delay_between_channels_seconds"], 25)
        self.assertTrue(cfg["stop_on_failure"])


class FacebookBatchUnitTests(unittest.TestCase):
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

    def _run_batch(
        self,
        *,
        channels: list[str],
        execute: dict[str, ChannelResult],
        dry_run: bool = False,
        confirm_post: bool = True,
        batch_cfg: dict | None = None,
        sleep_calls: list[float] | None = None,
    ) -> list[ChannelResult]:
        calls: list[float] = sleep_calls if sleep_calls is not None else []

        def execute_channel(ch: str) -> ChannelResult:
            return execute[ch]

        return run_facebook_phone_batch(
            make_job(),
            channels=channels,
            batch_cfg=batch_cfg or fb_config()["facebook_batch"],
            dry_run=dry_run,
            confirm_post=confirm_post,
            execute_channel=execute_channel,
            sleep_fn=lambda seconds: calls.append(seconds),
        )

    def test_groups_success_then_sleep_then_marketplace(self) -> None:
        sleeps: list[float] = []
        results = self._run_batch(
            channels=["fb_groups", "fb_marketplace"],
            execute={
                "fb_groups": ChannelResult(channel="fb_groups", ok=True),
                "fb_marketplace": ChannelResult(channel="fb_marketplace", ok=True),
            },
            sleep_calls=sleeps,
        )
        self.assertEqual([r.channel for r in results], ["fb_groups", "fb_marketplace"])
        self.assertEqual(sleeps, [25.0])

    def test_groups_failed_blocks_marketplace(self) -> None:
        results = self._run_batch(
            channels=["fb_groups", "fb_marketplace"],
            execute={
                "fb_groups": ChannelResult(channel="fb_groups", ok=False, reason="ui fail"),
            },
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(results[1].channel, "fb_marketplace")
        self.assertEqual(results[1].reason, "blocked_by_dependency")

    def test_groups_already_published_runs_marketplace_without_pause(self) -> None:
        snapshot = state.load_state()
        state.mark_channel_done(snapshot, "A_20260806_001", "fb_groups", status="verified")
        sleeps: list[float] = []
        results = self._run_batch(
            channels=["fb_groups", "fb_marketplace"],
            execute={
                "fb_marketplace": ChannelResult(channel="fb_marketplace", ok=True),
            },
            sleep_calls=sleeps,
        )
        self.assertTrue(results[0].skipped)
        self.assertEqual(results[1].channel, "fb_marketplace")
        self.assertEqual(sleeps, [])

    def test_partial_success_and_retry_only_marketplace(self) -> None:
        snapshot = state.load_state()
        state.mark_channel_done(snapshot, "A_20260806_001", "fb_groups", status="verified")
        first = self._run_batch(
            channels=["fb_groups", "fb_marketplace"],
            execute={
                "fb_marketplace": ChannelResult(
                    channel="fb_marketplace",
                    ok=False,
                    reason="timeout",
                ),
            },
        )
        self.assertEqual(summarize_facebook_batch_status(first), "partial_success")

        second = self._run_batch(
            channels=["fb_groups", "fb_marketplace"],
            execute={
                "fb_marketplace": ChannelResult(channel="fb_marketplace", ok=True),
            },
        )
        self.assertEqual([r.channel for r in second], ["fb_groups", "fb_marketplace"])
        self.assertTrue(second[0].skipped)
        self.assertTrue(second[1].ok)

    def test_both_published_is_nothing_to_publish(self) -> None:
        snapshot = state.load_state()
        state.mark_channel_done(snapshot, "A_20260806_001", "fb_groups", status="verified")
        state.mark_channel_done(
            snapshot,
            "A_20260806_001",
            "fb_marketplace",
            status="verified",
        )
        results = self._run_batch(
            channels=["fb_groups", "fb_marketplace"],
            execute={},
        )
        self.assertEqual(summarize_facebook_batch_status(results), "nothing_to_publish")

    def test_delay_read_from_config(self) -> None:
        sleeps: list[float] = []
        batch_cfg = fb_config(delay_between_channels_seconds=42)["facebook_batch"]
        self._run_batch(
            channels=["fb_groups", "fb_marketplace"],
            execute={
                "fb_groups": ChannelResult(channel="fb_groups", ok=True),
                "fb_marketplace": ChannelResult(channel="fb_marketplace", ok=True),
            },
            batch_cfg=batch_cfg,
            sleep_calls=sleeps,
        )
        self.assertEqual(sleeps, [42.0])

    def test_dry_run_does_not_sleep(self) -> None:
        sleeps: list[float] = []
        self._run_batch(
            channels=["fb_groups", "fb_marketplace"],
            execute={
                "fb_groups": ChannelResult(channel="fb_groups", ok=True),
                "fb_marketplace": ChannelResult(channel="fb_marketplace", ok=True),
            },
            dry_run=True,
            confirm_post=False,
            sleep_calls=sleeps,
        )
        self.assertEqual(sleeps, [])

    def test_marketplace_failed_is_partial_success(self) -> None:
        results = self._run_batch(
            channels=["fb_groups", "fb_marketplace"],
            execute={
                "fb_groups": ChannelResult(channel="fb_groups", ok=True),
                "fb_marketplace": ChannelResult(
                    channel="fb_marketplace",
                    ok=False,
                    reason="failed",
                ),
            },
        )
        self.assertEqual(summarize_facebook_batch_status(results), "partial_success")


class FacebookBatchPipelineTests(unittest.TestCase):
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

    def test_live_run_selects_both_channels_in_order(self) -> None:
        cfg = fb_config()
        order: list[str] = []

        def track_get_channel(name: str):
            order.append(name)
            channel = Mock()
            channel.publish.return_value = ChannelResult(channel=name, ok=True)
            return channel

        with patch("publisher_social.pipeline.load_publisher_config", return_value=cfg):
            with patch("publisher_social.pipeline.load_android_config", return_value={}):
                with patch(
                    "publisher_social.pipeline.prepare_job",
                    side_effect=lambda job, **_: job,
                ):
                    with patch(
                        "publisher_social.pipeline.get_channel",
                        side_effect=track_get_channel,
                    ):
                        with patch("publisher_social.pipeline._try_write_notion_result"):
                            with patch("publisher_social.pipeline._maybe_write_published_at"):
                                results = pipeline.run_channels(
                                    make_job(),
                                    channels=["fb_groups", "fb_marketplace"],
                                    dry_run=False,
                                    confirm_post=True,
                                    sleep_fn=lambda _: None,
                                )

        self.assertEqual([r.channel for r in results], ["fb_groups", "fb_marketplace"])
        self.assertEqual(order, ["fb_groups", "fb_marketplace"])

    def test_media_payloads_stay_separate(self) -> None:
        cfg = fb_config()
        job = make_job()
        prepared: list[PublishJob] = []

        def capture_prepare(job_in, **kwargs):
            prepared.append(job_in)
            return job_in

        channel = Mock()
        channel.publish.return_value = ChannelResult(channel="fb_groups", ok=True)

        with patch("publisher_social.pipeline.load_publisher_config", return_value=cfg):
            with patch("publisher_social.pipeline.load_android_config", return_value={}):
                with patch(
                    "publisher_social.pipeline.prepare_job",
                    side_effect=capture_prepare,
                ) as prepare_mock:
                    with patch(
                        "publisher_social.pipeline.get_channel",
                        return_value=channel,
                    ):
                        with patch("publisher_social.pipeline._try_write_notion_result"):
                            pipeline.run_channels(
                                job,
                                channels=["fb_groups", "fb_marketplace"],
                                dry_run=False,
                                confirm_post=True,
                                sleep_fn=lambda _: None,
                            )

        prepare_mock.assert_called_once()
        self.assertTrue(prepare_mock.call_args.kwargs.get("push_marketplace"))
        self.assertEqual(prepared[0].image_urls, job.image_urls)
        self.assertEqual(
            prepared[0].marketplace_image_urls,
            job.marketplace_image_urls,
        )


class TermuxFacebookBatchTests(unittest.TestCase):
    def _write_executable(self, path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def _base_home(self, root: Path, *, sequence: str) -> Path:
        home = root / "home"
        project = root / "project"
        fake_bin = root / "fake-bin"
        (home / ".publisher").mkdir(parents=True)
        (home / "bin").mkdir(parents=True)
        project.mkdir()
        fake_bin.mkdir()
        (home / ".publisher" / "runner.env").write_text(
            "\n".join(
                [
                    f'PROJECT_DIR="{project}"',
                    'RUN_CMD="queue --live"',
                    "POLL_SECONDS=0",
                    "QUIET_START=25",
                    "QUIET_END=25",
                    "WAKELOCK=0",
                    "DAILY_DIGEST_HOUR=-1",
                    "STOP_AFTER_FB_BATCH=1",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self._write_executable(
            home / "bin" / "connect_self.sh",
            "#!/bin/bash\nprintf 'connect\\n' >> \"$HOME/connect.calls\"\n",
        )
        self._write_executable(
            home / "bin" / "notify.sh",
            "#!/bin/bash\nprintf '%s\\n---\\n' \"$1\" >> \"$HOME/notify.calls\"\n",
        )
        self._write_executable(fake_bin / "flock", "#!/bin/bash\nexit 0\n")
        self._write_executable(fake_bin / "sleep", "#!/bin/bash\nexit 0\n")
        self._write_executable(
            fake_bin / "termux-battery-status",
            "#!/bin/bash\nprintf '{\"percentage\": 100}\\n'\n",
        )
        self._write_executable(fake_bin / "termux-wake-unlock", "#!/bin/bash\nexit 0\n")
        self._write_executable(fake_bin / "termux-wake-lock", "#!/bin/bash\nexit 0\n")
        self._write_executable(
            fake_bin / "timeout",
            f"""#!/bin/bash
count_file="$HOME/timeout.count"
count=0
[ -f "$count_file" ] && count="$(cat "$count_file")"
count=$((count + 1))
printf '%s\\n' "$count" > "$count_file"
if [ "$count" = "1" ]; then
  printf 'object_id=A_20260806_001\\n'
  printf '[OK] fb_groups: posted\\n'
  exit 0
fi
printf 'object_id=A_20260806_001\\n'
printf '[OK] fb_marketplace: posted\\n'
printf '[facebook_batch] status=success\\n'
""",
        )
        return home

    def test_runner_continues_after_groups_without_batch_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = self._base_home(root, sequence="partial")
            proc = subprocess.run(
                ["bash", str(RUNNER)],
                env={
                    **os.environ,
                    "HOME": str(home),
                    "PATH": f"{root / 'fake-bin'}:/usr/bin:/bin:/sbin",
                },
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual((home / "timeout.count").read_text().strip(), "2")
            log = (home / ".publisher" / "runner.log").read_text(encoding="utf-8")
            self.assertIn("live без завершённого facebook batch", log)
            self.assertIn("facebook batch завершён", log)

    def test_runner_stops_after_full_facebook_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = self._base_home(root, sequence="full")
            proc = subprocess.run(
                ["bash", str(RUNNER)],
                env={
                    **os.environ,
                    "HOME": str(home),
                    "PATH": f"{root / 'fake-bin'}:/usr/bin:/bin:/sbin",
                },
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            notices = (home / "notify.calls").read_text(encoding="utf-8")
            self.assertIn("fb_marketplace", notices)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "termux" / "bin" / "run_loop.sh"
NOTIFY = ROOT / "termux" / "bin" / "notify.sh"
CTL = ROOT / "termux" / "bin" / "ctl"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


class TermuxRuntimeTests(unittest.TestCase):
    def _base_home(self, root: Path, *, run_cmd: str = "queue --live") -> Path:
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
                    f'RUN_CMD="{run_cmd}"',
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
        _write_executable(
            home / "bin" / "connect_self.sh",
            "#!/bin/bash\nprintf 'connect\\n' >> \"$HOME/connect.calls\"\n",
        )
        _write_executable(
            home / "bin" / "notify.sh",
            (
                "#!/bin/bash\n"
                "printf '%s\\n---\\n' \"$1\" >> \"$HOME/notify.calls\"\n"
                "exit \"${FAKE_NOTIFY_RC:-0}\"\n"
            ),
        )
        _write_executable(fake_bin / "flock", "#!/bin/bash\nexit \"${FAKE_FLOCK_RC:-0}\"\n")
        _write_executable(fake_bin / "sleep", "#!/bin/bash\nexit 0\n")
        _write_executable(
            fake_bin / "termux-battery-status",
            "#!/bin/bash\nprintf '{\"percentage\": 100}\\n'\n",
        )
        _write_executable(fake_bin / "termux-wake-unlock", "#!/bin/bash\nexit 0\n")
        _write_executable(fake_bin / "termux-wake-lock", "#!/bin/bash\nexit 0\n")
        _write_executable(
            fake_bin / "timeout",
            """#!/bin/bash
count_file="$HOME/timeout.count"
count=0
[ -f "$count_file" ] && count="$(cat "$count_file")"
count=$((count + 1))
printf '%s\n' "$count" > "$count_file"
if [ "${FAKE_SEQUENCE:-ok}" = "skip_then_ok" ] && [ "$count" = "1" ]; then
  printf '[OK skip] scheduled F_20260719_014 tiktok: already done\n'
else
  printf 'object_id=F_20260719_014\n'
  printf '[OK] tiktok: posted url=https://example.invalid/post/1\n'
  printf '[facebook_batch] status=success\n'
fi
""",
        )
        return home

    def _runner_env(self, root: Path, home: Path, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home),
                "PATH": f"{root / 'fake-bin'}:/usr/bin:/bin:/sbin",
                **extra,
            }
        )
        return env

    def test_notify_requires_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            home.mkdir()
            proc = subprocess.run(
                ["bash", str(NOTIFY), "required report"],
                env={**os.environ, "HOME": str(home)},
                check=False,
            )
            self.assertEqual(proc.returncode, 2)

    def test_notify_dedup_marker_is_written_only_after_confirmed_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            fake_bin = root / "bin"
            (home / ".publisher").mkdir(parents=True)
            fake_bin.mkdir()
            (home / ".publisher" / "runner.env").write_text(
                "TG_BOT_TOKEN=test-only\nTG_CHAT_ID=1\n",
                encoding="utf-8",
            )
            curl = fake_bin / "curl"
            _write_executable(curl, "#!/bin/bash\nprintf '{\"ok\":false}\\n'\n")
            env = {
                **os.environ,
                "HOME": str(home),
                "PATH": f"{fake_bin}:/usr/bin:/bin:/sbin",
            }

            failed = subprocess.run(
                ["bash", str(NOTIFY), "report", "report-key"],
                env=env,
                check=False,
            )
            self.assertEqual(failed.returncode, 1)
            self.assertEqual(list((home / ".publisher").glob("notify_*")), [])

            _write_executable(curl, "#!/bin/bash\nprintf '{\"ok\":true}\\n'\n")
            delivered = subprocess.run(
                ["bash", str(NOTIFY), "report", "report-key"],
                env=env,
                check=False,
            )
            self.assertEqual(delivered.returncode, 0)
            self.assertEqual(len(list((home / ".publisher").glob("notify_*"))), 1)

    def test_live_runner_reports_url_and_stops_after_actual_channel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = self._base_home(root)
            proc = subprocess.run(
                ["bash", str(RUNNER)],
                env=self._runner_env(root, home, FAKE_SEQUENCE="skip_then_ok"),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual((home / "timeout.count").read_text().strip(), "2")
            log = (home / ".publisher" / "runner.log").read_text(encoding="utf-8")
            self.assertIn("служебных пропусков: 1", log)
            self.assertIn("facebook batch завершён", log)
            notices = (home / "notify.calls").read_text(encoding="utf-8")
            self.assertIn("https://example.invalid/post/1", notices)
            self.assertFalse(
                (home / ".publisher" / "pending_channel_report.txt").exists()
            )

    def test_failed_channel_report_is_durable_and_blocks_next_live_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = self._base_home(root)
            failed = subprocess.run(
                ["bash", str(RUNNER)],
                env=self._runner_env(root, home, FAKE_NOTIFY_RC="1"),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(failed.returncode, 0, failed.stdout + failed.stderr)
            pending = home / ".publisher" / "pending_channel_report.txt"
            self.assertTrue(pending.exists())
            self.assertIn(
                "https://example.invalid/post/1",
                pending.read_text(encoding="utf-8"),
            )

            (home / "timeout.count").unlink()
            blocked = subprocess.run(
                ["bash", str(RUNNER)],
                env=self._runner_env(root, home, FAKE_NOTIFY_RC="1"),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(blocked.returncode, 4, blocked.stdout + blocked.stderr)
            self.assertFalse((home / "timeout.count").exists())

    def test_ctl_refuses_ui_action_when_flock_is_busy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = self._base_home(root, run_cmd="queue --dry-run")
            proc = subprocess.run(
                ["bash", str(CTL), "once"],
                env=self._runner_env(root, home, FAKE_FLOCK_RC="1"),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 3)
            self.assertIn("UI занят другим процессом", proc.stderr)
            self.assertFalse((home / "connect.calls").exists())


if __name__ == "__main__":
    unittest.main()

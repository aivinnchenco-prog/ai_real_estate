from __future__ import annotations

import tempfile
import unittest
from multiprocessing import get_context
from pathlib import Path

from publisher_social.process_lock import PublisherLockBusy, PublisherProcessLock


def _hold_lock(path: str, ready, release) -> None:
    with PublisherProcessLock("child", path=Path(path)):
        ready.set()
        release.wait(5)


class ProcessLockTests(unittest.TestCase):
    def test_second_owner_gets_clear_busy_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "publisher.lock"
            with PublisherProcessLock("first", path=path):
                with self.assertRaises(PublisherLockBusy) as caught:
                    with PublisherProcessLock("second", path=path):
                        pass

            self.assertEqual(caught.exception.owner.get("command"), "first")
            with PublisherProcessLock("after-release", path=path):
                self.assertTrue(path.exists())

    def test_lock_is_enforced_between_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "publisher.lock"
            context = get_context("fork")
            ready = context.Event()
            release = context.Event()
            process = context.Process(
                target=_hold_lock,
                args=(str(path), ready, release),
            )
            process.start()
            try:
                self.assertTrue(ready.wait(3))
                with self.assertRaises(PublisherLockBusy):
                    with PublisherProcessLock("parent", path=path):
                        pass
            finally:
                release.set()
                process.join(5)
                if process.is_alive():
                    process.terminate()
                    process.join(2)
            self.assertEqual(process.exitcode, 0)


if __name__ == "__main__":
    unittest.main()

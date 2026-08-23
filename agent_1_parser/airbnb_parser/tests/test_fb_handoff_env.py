#!/usr/bin/env python3
"""FB parser must use .venv311 — never silently fall back to the bot interpreter."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_AIRBNB_ROOT = Path(__file__).resolve().parents[1]
if str(_AIRBNB_ROOT) not in sys.path:
    sys.path.insert(0, str(_AIRBNB_ROOT))

import config
import fb_handoff
from fb_handoff import (
    _fb_python_candidates,
    describe_fb_parser_env,
    extract_fb_url,
    handoff_fb_to_agent2,
    resolve_fb_parser_python,
)


def _write_exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


OK_STUB = """#!/usr/bin/env python3
import sys
print("3.11")
raise SystemExit(0)
"""

MISSING_REQUESTS_STUB = """#!/usr/bin/env python3
import sys
print("3.11")
print("ModuleNotFoundError: No module named 'requests'", file=sys.stderr)
raise SystemExit(1)
"""

WRONG_VERSION_STUB = """#!/usr/bin/env python3
print("3.12")
raise SystemExit(0)
"""


class FbHandoffEnvTest(unittest.TestCase):
    def test_extract_share_and_item(self):
        self.assertEqual(
            extract_fb_url("https://m.facebook.com/marketplace/item/123/?ref=x"),
            "https://www.facebook.com/marketplace/item/123/",
        )
        self.assertTrue(extract_fb_url("https://www.facebook.com/share/abc/").endswith("/share/abc/"))

    def test_candidates_never_include_bot_python(self):
        with patch.object(config, "FB_PARSER_PYTHON", ""):
            cands = _fb_python_candidates(Path("/tmp/fb_parser"))
        self.assertTrue(cands)
        self.assertNotIn(Path(sys.executable), cands)
        self.assertTrue(all(".venv311" in str(p) for p in cands))

    def test_missing_venv_is_error_not_sys_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(config, "FB_PARSER_PYTHON", ""):
                python_bin, err = resolve_fb_parser_python(root)
        self.assertIsNone(python_bin)
        self.assertNotEqual(python_bin, sys.executable)
        self.assertIn("requests", err)
        self.assertIn(".venv311", err)

    def test_override_python_used_when_probe_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = _write_exe(Path(tmp) / "python", OK_STUB)
            with patch.object(config, "FB_PARSER_PYTHON", str(stub)):
                python_bin, err = resolve_fb_parser_python(Path(tmp) / "unused")
        self.assertEqual(python_bin, str(stub))
        self.assertEqual(err, "")

    def test_broken_venv_with_requests_missing_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = _write_exe(Path(tmp) / ".venv311" / "bin" / "python", MISSING_REQUESTS_STUB)
            with patch.object(config, "FB_PARSER_PYTHON", ""):
                python_bin, err = resolve_fb_parser_python(Path(tmp))
        self.assertIsNone(python_bin)
        self.assertIn("requests", err)
        self.assertNotEqual(str(stub), sys.executable)

    def test_python312_rejected_even_if_imports_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = _write_exe(Path(tmp) / "python", WRONG_VERSION_STUB)
            with patch.object(config, "FB_PARSER_PYTHON", str(stub)):
                with patch.object(config, "FB_PARSER_ALLOW_NON311", False):
                    python_bin, err = resolve_fb_parser_python(Path(tmp))
        self.assertIsNone(python_bin)
        self.assertIn("3.11", err)
        self.assertIn("3.12", err)

    def test_handoff_returns_env_error_without_parser_process(self):
        with patch.object(fb_handoff, "find_fb_parser_root", return_value=Path("/tmp/fb")):
            with patch.object(
                fb_handoff, "resolve_fb_parser_python", return_value=(None, "нет .venv311")
            ):
                with patch.object(fb_handoff, "parse_fb_listing") as parse:
                    result = handoff_fb_to_agent2(
                        "https://www.facebook.com/marketplace/item/1/"
                    )
        parse.assert_not_called()
        self.assertFalse(result.ok)
        self.assertIn("не готов", result.note)
        self.assertIn("нет .venv311", result.note)

    def test_describe_env_ok_path(self):
        with patch.object(
            fb_handoff, "resolve_fb_parser_python", return_value=("/opt/fb/.venv311/bin/python", "")
        ):
            ok, msg = describe_fb_parser_env()
        self.assertTrue(ok)
        self.assertIn(".venv311", msg)

    def test_subprocess_gets_monorepo_pythonpath(self):
        import subprocess as sp

        completed = sp.CompletedProcess(args=["py"], returncode=0, stdout="", stderr="")
        with patch.object(fb_handoff, "_fb_python", return_value="/tmp/fake-python"):
            with patch.object(
                fb_handoff, "_openhome_app_root", return_value=Path("/opt/openhome/app")
            ):
                with patch("fb_handoff.subprocess.run", return_value=completed) as run:
                    fb_handoff.parse_fb_listing(
                        "https://www.facebook.com/marketplace/item/1/",
                        Path("/tmp/fb"),
                        "FB_1",
                    )
        env = run.call_args.kwargs["env"]
        self.assertEqual(run.call_args.kwargs.get("cwd"), "/tmp/fb")
        self.assertIn("/opt/openhome/app", env["PYTHONPATH"].split(os.pathsep))

    def test_required_modules_include_openhome_shared(self):
        self.assertIn("openhome_shared", fb_handoff._required_fb_modules("crawl4ai"))


if __name__ == "__main__":
    unittest.main()

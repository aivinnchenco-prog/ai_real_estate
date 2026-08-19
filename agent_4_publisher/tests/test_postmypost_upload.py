#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from postmypost_client import upload_file_by_url  # noqa: E402


class PostmypostUploadTests(unittest.TestCase):
    def test_upload_defaults_to_direct(self):
        cfg = {"postmypost": {"project_id": 1, "upload_mode": "direct"}}
        with patch("postmypost_client.upload_file_direct", return_value=42) as direct, patch(
            "postmypost_client.upload_file_by_url_remote"
        ) as remote:
            self.assertEqual(upload_file_by_url(1, "https://example.com/a.jpg", cfg), 42)
            direct.assert_called_once()
            remote.assert_not_called()

    def test_upload_url_mode_falls_back_on_422(self):
        cfg = {"postmypost": {"project_id": 1, "upload_mode": "url"}}
        with patch(
            "postmypost_client.upload_file_by_url_remote",
            side_effect=RuntimeError("PostMyPost HTTP 422 /upload/init: bad"),
        ), patch("postmypost_client.upload_file_direct", return_value=99) as direct:
            self.assertEqual(upload_file_by_url(1, "https://example.com/a.jpg", cfg), 99)
            direct.assert_called_once()


if __name__ == "__main__":
    unittest.main()

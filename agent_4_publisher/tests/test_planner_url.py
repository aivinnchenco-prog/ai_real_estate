#!/usr/bin/env python3
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from publish_pipeline import metricool_planner_post_url  # noqa: E402


def test_planner_url_uses_uuid_and_planner_path(monkeypatch):
    monkeypatch.setenv("METRICOOL_BLOG_ID", "6519911")
    url = metricool_planner_post_url(
        "900615601757047914",
        {"metricool": {"planner_calendar_url": "https://app.metricool.com/planner/calendar"}},
    )
    assert url.startswith("https://app.metricool.com/planner/calendar?")
    assert "openWithPostUuid=900615601757047914" in url
    assert "blogId=6519911" in url
    assert "postId=" not in url
    assert "userId=" not in url
    assert "planning/calendar" not in url

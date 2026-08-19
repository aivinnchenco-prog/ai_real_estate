#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from postmypost_client import extract_publication_url, postmypost_planner_url  # noqa: E402


def test_planner_url_share_preview_from_template():
    cfg = {
        "postmypost": {
            "project_code": "B4RHA",
            "planner_locale": "uk",
            "planner_url_template": (
                "https://app.postmypost.io/{locale}/p/{project_code}#share={publication_id}"
            ),
        }
    }
    url = postmypost_planner_url(31527360, cfg)
    assert url == "https://app.postmypost.io/uk/p/B4RHA#share=31527360"


def test_planner_url_share_preview_without_template():
    cfg = {"postmypost": {"project_code": "B4RHA", "planner_locale": "ru"}}
    url = postmypost_planner_url("31527360", cfg)
    assert url == "https://app.postmypost.io/ru/p/B4RHA#share=31527360"


def test_extract_prefers_posts_url_over_details_link():
    payload = {
        "details": [{"link": "https://openhome.example/utm-landing"}],
        "posts": [
            {
                "account_id": 1,
                "url": "https://www.instagram.com/p/Db2Zp1tEbXt",
                "post_status": 1,
            }
        ],
    }
    assert (
        extract_publication_url(payload, "instagram")
        == "https://www.instagram.com/p/Db2Zp1tEbXt"
    )


def test_extract_falls_back_to_details_link():
    payload = {"details": [{"link": "https://openhome.example/landing"}], "posts": []}
    assert extract_publication_url(payload, "instagram") == "https://openhome.example/landing"


def test_extract_none_while_scheduled():
    payload = {
        "posts": [{"account_id": 1, "post_status": 5}],
        "details": [{"content": "hello"}],
    }
    assert extract_publication_url(payload, "instagram") is None

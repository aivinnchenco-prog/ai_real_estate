#!/usr/bin/env python3
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from publish_pipeline import (  # noqa: E402
    metricool_enabled,
    postmypost_enabled,
    social_publisher_backend,
    spawn_agent5_postmypost_ai_agent,
)


def test_metricool_disabled_in_config():
    cfg_path = Path(__file__).resolve().parents[1] / "config" / "publisher.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg["metricool"]["enabled"] is False
    assert metricool_enabled(cfg) is False


def test_postmypost_enabled_in_config():
    cfg_path = Path(__file__).resolve().parents[1] / "config" / "publisher.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg["postmypost"]["enabled"] is True
    assert postmypost_enabled(cfg) is True
    assert social_publisher_backend(cfg) == "postmypost"


def test_chatplace_disabled_in_config():
    cfg_path = Path(__file__).resolve().parents[1] / "config" / "publisher.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg.get("chatplace", {}).get("enabled") is False


def test_no_chatplace_spawn_functions():
    import publish_pipeline as pp

    assert not hasattr(pp, "spawn_deferred_chatplace_funnel")
    assert not hasattr(pp, "spawn_chatplace_if_reel_url_ready")


def test_agent5_queues_after_publish(tmp_path, monkeypatch):
    cfg_path = Path(__file__).resolve().parents[1] / "config" / "publisher.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    state_file = tmp_path / "state.json"

    import agent_5_usher.postmypost_ai_agent as agent5

    monkeypatch.setattr(agent5, "_STATE_FILE", state_file)

    import publish_pipeline as pp

    import metricool_post_search

    monkeypatch.setattr(pp, "notion_get_page", lambda _pid: {"id": "page-1", "properties": {}})
    monkeypatch.setattr(
        metricool_post_search,
        "object_id_from_page",
        lambda _page, _cfg: "20260805_001",
    )

    out = spawn_agent5_postmypost_ai_agent(
        "page-1",
        "instagram",
        cfg,
        post_id="pub-99",
        post_url="https://www.instagram.com/p/abc/",
    )
    assert out is not None
    assert out["status"] == "pending_postmypost_ai_agent"
    out2 = spawn_agent5_postmypost_ai_agent(
        "page-1",
        "instagram",
        cfg,
        post_id="pub-99",
        post_url="https://www.instagram.com/p/abc/",
    )
    assert out2.get("skipped") is True

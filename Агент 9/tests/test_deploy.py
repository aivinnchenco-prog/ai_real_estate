"""Deploy and service isolation tests."""

from __future__ import annotations

from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
ROOT = Path(__file__).resolve().parents[1]


def test_systemd_service_isolated():
    text = (DEPLOY / "agent9-connector.service").read_text(encoding="utf-8")
    assert "start_connector.sh" in text
    assert "agent_6" not in text
    assert "agent_4" not in text


def test_all_runtime_inside_agent9_root():
    src = ROOT / "src" / "agent9_connector"
    assert src.exists()
    assert not (ROOT.parent / "agent_6_qualifier" / "src" / "agent9_connector").exists()


def test_start_script_entrypoint():
    script = (ROOT / "scripts" / "start_connector.sh").read_text(encoding="utf-8")
    assert "agent9_connector.main" in script

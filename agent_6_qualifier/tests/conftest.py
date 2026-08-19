"""Test isolation for Agent 6 / 7 / 8 suites.

``agent6_qualifier.tg_userbot`` calls ``load_env()`` at import time, which
seeds the real ``.env`` into ``os.environ`` for the whole pytest process.
Any test collected after that module would otherwise reach the live Gemini
API through the intent fallback and write to ``/opt/openhome/.env``.
"""
from __future__ import annotations

import os

import pytest

# Env that must never leak from a developer/production .env into a test run.
_BLOCKED_ENV = (
    "GEMINI_API_KEY",
    "AMO_ACCESS_TOKEN",
    "AMO_SUBDOMAIN",
    "WAZZUP_API_KEY",
    "NOTION_TOKEN",
    "TELEGRAM_BOT_TOKEN",
)


@pytest.fixture(autouse=True)
def _offline_deterministic_env(monkeypatch, tmp_path_factory):
    for key in _BLOCKED_ENV:
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path_factory.mktemp("openhome-env") / ".env"
    monkeypatch.setenv("OPENHOME_ENV_FILE", str(env_file))
    yield


@pytest.fixture(autouse=True)
def _no_outbound_http(monkeypatch, request):
    """Fail loudly instead of silently hitting the network during a test run."""
    if request.node.get_closest_marker("allow_network"):
        return

    import requests

    def _blocked(*args, **kwargs):
        raise AssertionError(
            "outbound HTTP is disabled in tests; patch the transport instead"
        )

    monkeypatch.setattr(requests, "request", _blocked)
    monkeypatch.setattr(requests, "post", _blocked)
    monkeypatch.setattr(requests, "get", _blocked)
    monkeypatch.setattr(requests, "put", _blocked)
    monkeypatch.setattr(requests, "patch", _blocked)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_network: test may perform real outbound HTTP"
    )
    os.environ.setdefault("SKIP_SCHEMA_CHECK", "1")

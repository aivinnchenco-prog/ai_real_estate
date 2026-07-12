import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from agent7.amo import AmoClient


@pytest.fixture
def amo(monkeypatch):
    monkeypatch.setenv("AMO_SUBDOMAIN", "test")
    monkeypatch.setenv("AMO_ACCESS_TOKEN", "token")
    client = AmoClient()
    client.pipeline_id = 11088150
    return client


def make_req(responses):
    def _req(method, path, **kw):
        for prefix, resp in responses.items():
            if path.startswith(prefix):
                return resp() if callable(resp) else resp
        raise AssertionError(f"неожиданный запрос {path}")
    return _req


def test_find_open_lead_returns_active(amo, monkeypatch):
    monkeypatch.setattr(amo, "_req", make_req({
        "/contacts/10": {"_embedded": {"leads": [{"id": 1}, {"id": 2}]}},
        "/leads/1": {"id": 1, "pipeline_id": 11088150, "status_id": 142},  # закрыта
        "/leads/2": {"id": 2, "pipeline_id": 11088150, "status_id": 87078554},  # открыта
    }))
    assert amo.find_open_lead(10) == 2


def test_find_open_lead_ignores_other_pipeline(amo, monkeypatch):
    monkeypatch.setattr(amo, "_req", make_req({
        "/contacts/10": {"_embedded": {"leads": [{"id": 5}]}},
        "/leads/5": {"id": 5, "pipeline_id": 999, "status_id": 100},  # чужая воронка
    }))
    assert amo.find_open_lead(10) is None


def test_find_open_lead_no_leads(amo, monkeypatch):
    monkeypatch.setattr(amo, "_req", make_req({"/contacts/10": {"_embedded": {}}}))
    assert amo.find_open_lead(10) is None

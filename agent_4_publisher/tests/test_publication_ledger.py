"""Publication Ledger tests (Agent 4) + Agent 10 read scope."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from postmypost_publication_state import (  # noqa: E402
    lookup_publication,
    register_publication,
)
from publication_ledger import (  # noqa: E402
    PublicationLedger,
    enrich_publication_in_ledger,
    reconcile_from_current_state,
    register_publication_in_ledger,
)
from sync_postmypost_urls import sync_postmypost_url_to_notion  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "postmypost"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def ledger(tmp_path: Path) -> PublicationLedger:
    return PublicationLedger(tmp_path / "publications.sqlite3")


@pytest.fixture
def pmp_config() -> dict:
    return {
        "postmypost": {
            "enabled": True,
            "project_id": 355063,
            "platform_accounts": {"instagram": [2214120], "facebook": [2214118], "tiktok": [2214123]},
        },
        "notion": {
            "published_url_fields": {
                "instagram_carousel": "post_url_instagram_carousel",
                "instagram_reel": "post_url_instagram_reel",
                "facebook": "post_url_facebook",
                "tiktok": "post_url_tiktok",
            },
        },
    }


def test_first_second_third_ig_reel_same_slot(ledger: PublicationLedger) -> None:
    for i, pid in enumerate(["100", "101", "102"], start=1):
        ledger.register_from_create(
            object_id="1847",
            notion_page_id="page-1847",
            postmypost_publication_id=pid,
            platform="instagram",
            slot="instagram:reel",
            account_id=2214120,
            scheduled_at=f"2026-08-0{i}T10:00:00Z",
            post_kind="reel",
            upload_video=True,
            publication_type=4,
            raw_status=5,
        )
    rows = ledger.list_by_object("1847", platform="instagram", format="reel")
    assert len(rows) == 3
    assert [r.postmypost_publication_id for r in rows] == ["100", "101", "102"]


def test_duplicate_postmypost_id_idempotent(ledger: PublicationLedger) -> None:
    a = ledger.register_from_create(
        object_id="1847",
        notion_page_id="page-1847",
        postmypost_publication_id="100",
        platform="instagram",
        slot="instagram:reel",
        account_id=1,
        scheduled_at="2026-08-01T10:00:00Z",
        post_kind="reel",
        upload_video=True,
    )
    b = ledger.register_from_create(
        object_id="1847",
        notion_page_id="page-1847",
        postmypost_publication_id="100",
        platform="instagram",
        slot="instagram:reel",
        account_id=1,
        scheduled_at="2026-08-01T10:00:00Z",
        post_kind="reel",
        upload_video=True,
    )
    assert a.publication_key == b.publication_key
    assert ledger.count() == 1


def test_object_isolation(ledger: PublicationLedger) -> None:
    ledger.register_from_create(
        object_id="A",
        notion_page_id="pa",
        postmypost_publication_id="1",
        platform="instagram",
        slot="instagram:reel",
        account_id=1,
        scheduled_at=None,
        post_kind="reel",
    )
    ledger.register_from_create(
        object_id="B",
        notion_page_id="pb",
        postmypost_publication_id="2",
        platform="instagram",
        slot="instagram:reel",
        account_id=1,
        scheduled_at=None,
        post_kind="reel",
    )
    assert len(ledger.list_by_object("A")) == 1
    assert len(ledger.list_by_object("B")) == 1


def test_current_state_pointer_still_works(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    register_publication(
        page_id="page-1",
        object_id="1847",
        platform="instagram",
        publication_id="111",
        planner_url="https://app.postmypost.io/publications/111",
        scheduled_time="2026-08-07T10:00:00Z",
        post_kind="reel",
        path=state,
    )
    register_publication(
        page_id="page-1",
        object_id="1847",
        platform="instagram",
        publication_id="222",
        planner_url="https://app.postmypost.io/publications/222",
        scheduled_time="2026-08-07T14:00:00Z",
        post_kind="reel",
        path=state,
    )
    # current pointer overwrites same slot
    cur = lookup_publication("page-1", "instagram", post_kind="reel", path=state)
    assert cur["publication_id"] == "222"


def test_deferred_enrich_updates_same_record_no_duplicate(
    ledger: PublicationLedger, tmp_path: Path, pmp_config
) -> None:
    db = tmp_path / "publications.sqlite3"
    register_publication_in_ledger(
        object_id="1847",
        notion_page_id="page-1",
        postmypost_publication_id="31462704",
        platform="instagram",
        slot="instagram:reel",
        account_id=2214120,
        scheduled_at="2026-08-07T10:00:00Z",
        post_kind="reel",
        upload_video=True,
        publication_type=4,
        raw_status=5,
        db_path=db,
    )
    payload = _fixture("instagram_reel_published.json")
    page = {"properties": {"post_url_instagram_reel": {"type": "url", "url": None}}}
    updates: dict = {}

    def fake_update(page_id, fields):
        updates.update(fields)

    enriched = enrich_publication_in_ledger(
        "31462704",
        payload,
        permalink="https://www.instagram.com/reel/DreelSlotTest",
        platform="instagram",
        db_path=db,
    )

    with patch("sync_postmypost_urls.postmypost_get_publication", return_value=payload), patch(
        "sync_postmypost_urls.notion_update_fields", side_effect=fake_update
    ), patch(
        "publication_ledger.enrich_publication_in_ledger",
        side_effect=lambda *a, **k: enrich_publication_in_ledger(
            *a, **{**k, "db_path": db}
        ),
    ):
        result = sync_postmypost_url_to_notion(
            "page-1",
            "instagram",
            pmp_config,
            publication_id="31462704",
            post_kind="reel",
            page=page,
        )

    assert enriched is not None
    assert enriched.permalink == "https://www.instagram.com/reel/DreelSlotTest"
    assert enriched.external_media_id == "18111531301999159"
    assert enriched.status == "published"
    assert enriched.raw_status == 1
    assert enriched.published_at is None  # not invented
    assert PublicationLedger(db).count() == 1
    assert result["updated"] is True
    # Notion writeback still current/latest convenience field
    assert "post_url_instagram_reel" in updates


def test_permalink_and_external_id_nullable(ledger: PublicationLedger) -> None:
    row = ledger.register_from_create(
        object_id="1847",
        notion_page_id="p",
        postmypost_publication_id="9",
        platform="facebook",
        slot="facebook:post",
        account_id=2214118,
        scheduled_at="2026-08-07T10:00:00Z",
        post_kind=None,
        publication_type=1,
        raw_status=5,
    )
    assert row.permalink is None
    assert row.external_media_id is None
    assert row.format == "post"
    assert row.status == "pending"
    assert row.raw_status == 5
    assert row.scheduled_at == "2026-08-07T10:00:00Z"
    assert row.published_at is None


def test_facebook_format_not_guessed_as_reel_without_type(ledger: PublicationLedger) -> None:
    row = ledger.register_from_create(
        object_id="1847",
        notion_page_id="p",
        postmypost_publication_id="55",
        platform="facebook",
        slot="facebook:post",
        account_id=1,
        scheduled_at=None,
        post_kind=None,
        publication_type=None,
        upload_video=False,
    )
    assert row.format == "post"


def test_reconcile_from_current_state(tmp_path: Path) -> None:
    db = tmp_path / "publications.sqlite3"
    state = {
        "version": 1,
        "slots": {
            "page-1:instagram:reel": {
                "page_id": "page-1",
                "object_id": "1847",
                "platform": "instagram",
                "slot_id": "instagram:reel",
                "publication_id": "777",
                "post_kind": "reel",
                "scheduled_time": "2026-08-07T10:00:00Z",
            },
            "bad": {"publication_id": "x"},
        },
    }
    result = reconcile_from_current_state(state, db_path=db)
    assert result["imported"] == 1
    assert result["skipped"] >= 1
    assert PublicationLedger(db).get_by_postmypost_id("777") is not None


def test_default_db_path_env_override(monkeypatch, tmp_path: Path) -> None:
    from publication_ledger import default_db_path

    monkeypatch.delenv("PUBLISHER_LEDGER_PATH", raising=False)
    assert default_db_path().name == "publications.sqlite3"
    target = tmp_path / "runtime" / "publications.sqlite3"
    monkeypatch.setenv("PUBLISHER_LEDGER_PATH", str(target))
    assert default_db_path() == target


def test_runtime_db_gitignored() -> None:
    root_gi = (ROOT.parents[0] / ".gitignore").read_text(encoding="utf-8")
    local_gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "publications.sqlite3" in root_gi or "*.sqlite3" in root_gi
    assert "sqlite3" in local_gi

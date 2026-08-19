"""Config env source resolution tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from availability_service.app import config as config_mod


def test_canonical_prod_env_preferred_when_present(tmp_path: Path):
    fake_prod = tmp_path / "openhome.env"
    fake_prod.write_text("AVAILABILITY_ENABLED=false\n", encoding="utf-8")
    project_env = tmp_path / "app.env"
    project_env.write_text("AVAILABILITY_ENABLED=true\n", encoding="utf-8")

    with patch.object(config_mod, "CANONICAL_PROD_ENV", fake_prod):
        with patch.object(config_mod, "PROJECT_ROOT", tmp_path):
            with patch.object(config_mod, "PACKAGE_ROOT", tmp_path / "pkg"):
                config_mod.load_project_env()
                assert config_mod.canonical_env_source() == str(fake_prod)
                assert str(fake_prod) in config_mod.loaded_env_sources()
                assert str(project_env) not in config_mod.loaded_env_sources()

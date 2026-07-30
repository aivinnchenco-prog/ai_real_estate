from __future__ import annotations

import os
from pathlib import Path


def package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _real_estate_env_candidates(root: Path) -> list[Path]:
    """Find Real Estate Agent .env whether we live inside monorepo or as sibling."""
    out: list[Path] = []
    # Nested: …/Real Estate Agent/agent_4_publisher_social
    monorepo_env = root.parent / ".env"
    if monorepo_env.exists():
        out.append(monorepo_env)
    nested_legacy = (
        root.parent
        / "agent_2_registrar"
        / "_import"
        / "assistant-media"
        / ".env.real-estate"
    )
    if nested_legacy.exists():
        out.append(nested_legacy)
    # Legacy sibling: …/Агенты/Publisher social next to …/Агенты/Real Estate Agent
    sibling = root.parent / "Real Estate Agent" / ".env"
    if sibling.exists():
        out.append(sibling)
    sibling_legacy = (
        root.parent
        / "Real Estate Agent"
        / "agent_2_registrar"
        / "_import"
        / "assistant-media"
        / ".env.real-estate"
    )
    if sibling_legacy.exists():
        out.append(sibling_legacy)
    return out


def load_dotenv() -> None:
    """Load .env from project root, then optional Real Estate Agent .env."""
    root = package_root()
    candidates = [
        root / ".env",
        root / ".env.local",
    ]
    real_estate = os.environ.get("REAL_ESTATE_ENV")
    if real_estate:
        candidates.append(Path(real_estate))
    else:
        candidates.extend(_real_estate_env_candidates(root))

    for env_path in candidates:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

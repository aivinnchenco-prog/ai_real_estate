"""Apply CURATOR_BASE_URL and other env overrides to pipeline.json config."""
from __future__ import annotations

import copy
import os


def apply_env_overrides(cfg: dict) -> dict:
    out = copy.deepcopy(cfg)
    base = (os.environ.get("CURATOR_BASE_URL") or "").rstrip("/")
    if base:
        out["curator_url"] = f"{base}/select"
        out["curator_health_url"] = f"{base}/health"
        seedance = out.setdefault("seedance", {})
        seedance["curator_diverse_url"] = f"{base}/select-diverse"
    return out

"""Pytest path setup for Agent 4 cross-package tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

for path in (ROOT, SCRIPTS):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

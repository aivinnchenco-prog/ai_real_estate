#!/usr/bin/env python3
"""Alias of analyze_object.py — recommend ad creative + budget for an object."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent10_marketer.cli import main  # noqa: E402

if __name__ == "__main__":
    # Default to human report for recommend_ad entrypoint feel
    argv = list(sys.argv[1:])
    if "--report" not in argv and "-h" not in argv and "--help" not in argv:
        argv.append("--report")
    raise SystemExit(main(argv))

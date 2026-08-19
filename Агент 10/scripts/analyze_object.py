#!/usr/bin/env python3
"""Analyze object publications and recommend best Reel for promotion.

Usage:
  PYTHONPATH=src python3 scripts/analyze_object.py 1847 --fixture --report
  PYTHONPATH=src python3 scripts/analyze_object.py 1847
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent10_marketer.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

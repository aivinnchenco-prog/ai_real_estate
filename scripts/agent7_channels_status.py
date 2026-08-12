#!/usr/bin/env python3
"""Wrapper: Agent7 channel status (runs agent_6_qualifier script)."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

TARGET = (
    Path(__file__).resolve().parents[1]
    / "agent_6_qualifier"
    / "scripts"
    / "agent7_channels_status.py"
)
sys.argv[0] = str(TARGET)
runpy.run_path(str(TARGET), run_name="__main__")

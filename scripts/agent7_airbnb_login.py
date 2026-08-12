#!/usr/bin/env python3
"""Wrapper: Airbnb manual login for Agent7 owner outreach."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

TARGET = (
    Path(__file__).resolve().parents[1]
    / "agent_6_qualifier"
    / "scripts"
    / "agent7_airbnb_login.py"
)
sys.argv[0] = str(TARGET)
runpy.run_path(str(TARGET), run_name="__main__")

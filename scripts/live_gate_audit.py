#!/usr/bin/env python3
"""Wrapper → agent_6_qualifier/scripts/live_gate_audit.py"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

TARGET = (
    Path(__file__).resolve().parents[1]
    / "agent_6_qualifier"
    / "scripts"
    / "live_gate_audit.py"
)
sys.argv[0] = str(TARGET)
runpy.run_path(str(TARGET), run_name="__main__")

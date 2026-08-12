#!/usr/bin/env python3
"""Wrapper → agent_6_qualifier/scripts/amo_chat_webhook_status.py"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

TARGET = (
    Path(__file__).resolve().parents[1]
    / "agent_6_qualifier"
    / "scripts"
    / "amo_chat_webhook_status.py"
)
sys.argv[0] = str(TARGET)
runpy.run_path(str(TARGET), run_name="__main__")

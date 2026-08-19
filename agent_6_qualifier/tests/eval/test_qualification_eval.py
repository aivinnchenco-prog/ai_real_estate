"""Production-grade qualification eval suite — not part of production runtime.

Run: pytest agent_6_qualifier/tests/eval/test_qualification_eval.py -v

Tiers:
  baseline    — must pass on current canonical refactor qualifier
  post_merge  — Wave 1 post-audit stack
  wave2       — Wave 2 progressive qualification
  v2_target   — unblocked by Wave 3 (confidence, contradictions, repair, reactions)
  wave3       — Wave 3 qualification policy V2

All tiers must pass: Wave 3 leaves no expected xfail behind.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tests.eval.harness import load_scenarios, run_and_assert, scenarios_by_tier

ALL_TIERS = ("baseline", "post_merge", "wave2", "v2_target", "wave3")


def _ids(scenarios):
    return [s.get("id", "?") for s in scenarios]


@pytest.mark.parametrize("scenario", load_scenarios(), ids=_ids(load_scenarios()))
def test_eval_scenario(scenario):
    run_and_assert(scenario)


def test_minimum_scenario_count():
    scenarios = load_scenarios()
    assert len(scenarios) >= 121, f"expected >=121 scenarios, got {len(scenarios)}"


def test_tier_coverage():
    tiers = {s.get("tier") for s in load_scenarios()}
    for tier in ALL_TIERS:
        assert tier in tiers, f"missing tier {tier}"


def test_no_unknown_tier():
    unknown = {
        s.get("id") for s in load_scenarios() if s.get("tier") not in ALL_TIERS
    }
    assert not unknown, f"scenarios with unknown tier: {sorted(unknown)}"


def test_post_merge_scenarios_pass():
    """All post_merge scenarios must pass after Wave 1 merge."""
    failed = []
    for spec in scenarios_by_tier("post_merge"):
        try:
            run_and_assert(spec)
        except AssertionError as e:
            failed.append(f"{spec.get('id')}: {e}")
    assert not failed, "\n".join(failed)


def test_baseline_scenarios_pass():
    """All baseline scenarios must pass without xfail."""
    failed = []
    for spec in scenarios_by_tier("baseline"):
        try:
            run_and_assert(spec)
        except AssertionError as e:
            failed.append(f"{spec.get('id')}: {e}")
    assert not failed, "\n".join(failed)


def test_wave2_scenarios_pass():
    """All wave2 tier scenarios must pass."""
    failed = []
    for spec in scenarios_by_tier("wave2"):
        try:
            run_and_assert(spec)
        except AssertionError as e:
            failed.append(f"{spec.get('id')}: {e}")
    assert not failed, "\n".join(failed)


def test_v2_target_scenarios_pass():
    """Wave 3 unblocks every previously-xfailed v2_target scenario."""
    failed = []
    for spec in scenarios_by_tier("v2_target"):
        try:
            run_and_assert(spec)
        except AssertionError as e:
            failed.append(f"{spec.get('id')}: {e}")
    assert not failed, "\n".join(failed)


def test_wave3_scenarios_pass():
    """All wave3 tier scenarios must pass."""
    failed = []
    for spec in scenarios_by_tier("wave3"):
        try:
            run_and_assert(spec)
        except AssertionError as e:
            failed.append(f"{spec.get('id')}: {e}")
    assert not failed, "\n".join(failed)

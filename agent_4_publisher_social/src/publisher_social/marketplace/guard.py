from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .states import ALLOWED_ACTIONS, FORBIDDEN_ACTIONS_GLOBAL, MarketplaceState


@dataclass
class ActionPlan:
    state: MarketplaceState
    action: str
    target: str
    value_type: str
    confidence: float
    evidence: list[str]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload


class ActionGuardDecision(str):
    EXECUTE = "execute"
    RECHECK = "recheck"
    SAFE_STOP = "safe_stop"


@dataclass(frozen=True)
class GuardResult:
    decision: str
    plan: ActionPlan
    reason: str = ""


def evaluate_action_plan(plan: ActionPlan, ui_cfg: dict[str, Any]) -> GuardResult:
    confidence_cfg = ui_cfg.get("confidence", {})
    execute_threshold = float(confidence_cfg.get("execute", 0.9))
    recheck_threshold = float(confidence_cfg.get("recheck", 0.7))

    if plan.action in FORBIDDEN_ACTIONS_GLOBAL:
        return GuardResult(ActionGuardDecision.SAFE_STOP, plan, "forbidden_action")

    allowed = ALLOWED_ACTIONS.get(plan.state, frozenset())
    if plan.action not in allowed and plan.action != "detect_only":
        return GuardResult(
            ActionGuardDecision.SAFE_STOP,
            plan,
            f"action_not_allowed_in_state:{plan.state.value}",
        )

    if plan.confidence >= execute_threshold:
        return GuardResult(ActionGuardDecision.EXECUTE, plan)
    if plan.confidence >= recheck_threshold:
        return GuardResult(ActionGuardDecision.RECHECK, plan, "low_confidence_recheck")
    return GuardResult(ActionGuardDecision.SAFE_STOP, plan, "confidence_below_threshold")


def build_fill_plan(
    *,
    state: MarketplaceState,
    target: str,
    value_type: str,
    confidence: float,
    evidence: list[str],
) -> ActionPlan:
    return ActionPlan(
        state=state,
        action="fill_field",
        target=target,
        value_type=value_type,
        confidence=confidence,
        evidence=evidence,
    )

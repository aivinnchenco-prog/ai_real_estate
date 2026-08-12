"""MarketingBrain — future LLM interface (disabled; no Meta token access).

Deterministic analytics / scores / caps stay outside the brain.
Flow: facts → analytics → MarketingBrain → policy → approval → Meta service.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agent10_marketer.models import CampaignSuggestion, PaidPerformanceMetrics, RankedReel


class MarketingBrain(ABC):
    """Strategic explanations / hypotheses only. Never computes CTR/CPC/CPM/caps."""

    @abstractmethod
    def analyze_campaign(
        self,
        *,
        metrics: PaidPerformanceMetrics,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def recommend_creative(
        self,
        *,
        candidates: list[RankedReel],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def recommend_budget(
        self,
        *,
        suggestion: CampaignSuggestion,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def explain_performance(
        self,
        *,
        metrics: PaidPerformanceMetrics,
        context: dict[str, Any] | None = None,
    ) -> str:
        raise NotImplementedError


class DisabledMarketingBrain(MarketingBrain):
    """Default — LLM not connected to production Meta actions."""

    def __init__(self, reason: str = "AGENT10_LLM_ENABLED=false"):
        self.reason = reason

    def _block(self, op: str) -> dict[str, Any]:
        return {
            "status": "disabled",
            "operation": op,
            "reason": self.reason,
            "note": (
                "MarketingBrain must not call Meta adapter or invent metrics; "
                "enable only after provider credentials + policy wiring."
            ),
        }

    def analyze_campaign(
        self,
        *,
        metrics: PaidPerformanceMetrics,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._block("analyze_campaign")

    def recommend_creative(
        self,
        *,
        candidates: list[RankedReel],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._block("recommend_creative")

    def recommend_budget(
        self,
        *,
        suggestion: CampaignSuggestion,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._block("recommend_budget")

    def explain_performance(
        self,
        *,
        metrics: PaidPerformanceMetrics,
        context: dict[str, Any] | None = None,
    ) -> str:
        return f"[MarketingBrain disabled] {self.reason}"

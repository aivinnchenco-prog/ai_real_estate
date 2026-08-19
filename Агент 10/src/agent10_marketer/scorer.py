"""CreativeScorer — deterministic organic score 0..100 with configurable weights."""

from __future__ import annotations

from agent10_marketer.config import ScoringConfig
from agent10_marketer.models import AnalyticsMetrics, ScoreBreakdown


def _component_value(analytics: AnalyticsMetrics, name: str) -> float | None:
    return getattr(analytics, name, None)


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(w)) for w in weights.values())
    if total <= 0:
        return {}
    return {k: max(0.0, float(v)) / total for k, v in weights.items()}


def _relative_scores(
    values: dict[str, float | None],
    peer_values: list[dict[str, float | None]] | None,
) -> dict[str, float]:
    """
    Map each available component to 0..1 within the peer group (or identity if alone).
    Missing values are omitted (not scored as zero).
    """
    peers = peer_values or []
    out: dict[str, float] = {}
    for key, value in values.items():
        if value is None:
            continue
        series = [value]
        for peer in peers:
            pv = peer.get(key)
            if pv is not None:
                series.append(float(pv))
        lo = min(series)
        hi = max(series)
        if hi == lo:
            out[key] = 1.0 if len(series) == 1 else 0.5
        else:
            out[key] = (float(value) - lo) / (hi - lo)
    return out


class CreativeScorer:
    def __init__(self, config: ScoringConfig):
        self.config = config
        self.weights = dict(config.weights)

    def score(
        self,
        analytics: AnalyticsMetrics,
        *,
        peers: list[AnalyticsMetrics] | None = None,
    ) -> ScoreBreakdown:
        component_names = list(self.weights.keys())
        values = {name: _component_value(analytics, name) for name in component_names}
        missing = [n for n, v in values.items() if v is None]
        available = [n for n, v in values.items() if v is not None]

        active_weights = {n: float(self.weights[n]) for n in available if n in self.weights}
        used = _normalize_weights(active_weights)

        peer_dicts: list[dict[str, float | None]] | None = None
        if peers:
            peer_dicts = [
                {name: _component_value(p, name) for name in component_names} for p in peers
            ]

        rel = _relative_scores(values, peer_dicts)

        breakdown: dict[str, float] = {}
        score = 0.0
        for name, weight in used.items():
            part = rel.get(name, 0.0) * 100.0
            breakdown[name] = round(part, 4)
            score += part * weight

        if not used:
            organic = 0.0
        else:
            organic = max(0.0, min(100.0, round(score, 4)))

        return ScoreBreakdown(
            organic_score=organic,
            breakdown=breakdown,
            used_weights={k: round(v, 6) for k, v in used.items()},
            missing_components=missing,
            available_components=available,
        )

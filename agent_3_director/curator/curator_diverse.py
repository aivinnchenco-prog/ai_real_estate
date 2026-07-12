"""MMR-отбор разнообразных кадров — без CLIP/torch."""
from __future__ import annotations

import math


def _norm(vec: list[float]) -> list[float]:
    mag = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / mag for x in vec]


def cosine_sim(a, b) -> float:
    if hasattr(a, "tolist"):
        a = a.tolist()
    if hasattr(b, "tolist"):
        b = b.tolist()
    return sum(x * y for x, y in zip(a, b, strict=False))


def select_diverse_candidates(
    survivors: list[tuple],
    top_k: int,
    max_per_category: int,
    max_similarity: float,
    mmr_lambda: float,
) -> list[tuple]:
    """survivors: (key, category, aest, conf, embed)."""
    if not survivors:
        return []

    pool = sorted(survivors, key=lambda t: t[2], reverse=True)

    selected: list[tuple] = []
    cat_count: dict[str, int] = {}
    selected_keys: set[str] = set()

    first = pool[0]
    selected.append(first)
    selected_keys.add(first[0])
    cat_count[first[1]] = cat_count.get(first[1], 0) + 1

    while len(selected) < top_k:
        best = None
        best_score = -1e9
        for cand in pool:
            key, cat, aest, _conf, emb = cand
            if key in selected_keys:
                continue
            if cat_count.get(cat, 0) >= max_per_category:
                continue
            max_sim = max(cosine_sim(emb, s[4]) for s in selected)
            if max_sim > max_similarity:
                continue
            mmr = mmr_lambda * aest - (1.0 - mmr_lambda) * max_sim
            if mmr > best_score:
                best_score = mmr
                best = cand
        if best is None:
            break
        selected.append(best)
        selected_keys.add(best[0])
        cat_count[best[1]] = cat_count.get(best[1], 0) + 1

    return selected

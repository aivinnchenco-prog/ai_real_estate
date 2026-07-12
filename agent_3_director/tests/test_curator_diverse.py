#!/usr/bin/env python3
"""Unit tests for curator diverse selection (MMR) — без CLIP/torch."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "curator"))

from curator_diverse import select_diverse_candidates, _norm as emb


class CuratorDiverseTests(unittest.TestCase):
    def test_rejects_similar_embeddings(self):
        survivors = [
            ("a.jpg", "living", 0.9, 0.8, emb([1, 0, 0])),
            ("b.jpg", "living", 0.85, 0.7, emb([0.99, 0.01, 0])),
            ("c.jpg", "kitchen", 0.8, 0.7, emb([0, 1, 0])),
            ("d.jpg", "bedroom", 0.75, 0.6, emb([0, 0, 1])),
        ]
        picked = select_diverse_candidates(survivors, top_k=3, max_per_category=2, max_similarity=0.88, mmr_lambda=0.6)
        keys = [p[0] for p in picked]
        self.assertIn("a.jpg", keys)
        self.assertNotIn("b.jpg", keys)
        self.assertEqual(len(keys), 3)

    def test_category_cap(self):
        survivors = [
            ("a.jpg", "bedroom", 0.9, 0.8, emb([1, 0, 0])),
            ("b.jpg", "bedroom", 0.88, 0.7, emb([0, 1, 0])),
            ("c.jpg", "bedroom", 0.87, 0.6, emb([0, 0, 1])),
            ("d.jpg", "kitchen", 0.86, 0.6, emb([1, 1, 0])),
        ]
        picked = select_diverse_candidates(survivors, top_k=4, max_per_category=2, max_similarity=0.99, mmr_lambda=0.6)
        bedroom = sum(1 for p in picked if p[1] == "bedroom")
        self.assertLessEqual(bedroom, 2)


if __name__ == "__main__":
    unittest.main()

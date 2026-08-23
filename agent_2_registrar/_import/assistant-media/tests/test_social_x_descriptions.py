#!/usr/bin/env python3
"""Формулы описаний: соцсети vs X.com (Агент 2)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from description_validator import (  # noqa: E402
    SOCIAL_LIMIT,
    X_LIMIT,
    validate_and_fit_social,
    validate_and_fit_x,
)
from description_writer import (  # noqa: E402
    build_context,
    generate_social_description,
    generate_x_description,
    rent_type_label,
    template_social,
    template_x,
)


def _draft(**kwargs) -> SimpleNamespace:
    base = dict(
        housing_type="Квартира",
        rooms=2,
        district="Бангтао",
        price_monthly=None,
        price_yearly=None,
        deposit=None,
        amenities=["Бассейн", "Охрана", "Парковка"],
        view="бассейн",
        rent_type="Краткосрочная",
        max_guests=5,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _ctx(*, object_id: str = "A_20260823_001", **draft_kw) -> dict:
    return build_context(
        draft=_draft(**draft_kw),
        description="2BR condo, sleeps 5, pool view, 7 minutes to Bang Tao beach",
        object_id=object_id,
        contacts={"whatsapp": "+66625124002", "telegram": "ТГ"},
        parsed_meta={"guests": "5"},
        complex_name="Legendary",
        region="Пхукет",
    )


class RentTypeLabelTests(unittest.TestCase):
    def test_airbnb_short_term(self):
        self.assertEqual(rent_type_label("Краткосрочная", "A_1"), "Краткосрочная аренда")

    def test_facebook_long_term(self):
        self.assertEqual(rent_type_label("Долгосрочная", "F_1"), "Долгосрочная аренда")

    def test_object_id_fallback(self):
        self.assertEqual(rent_type_label("", "A_20260823_001"), "Краткосрочная аренда")
        self.assertEqual(rent_type_label("", "F_20260823_001"), "Долгосрочная аренда")


class SocialFormulaTests(unittest.TestCase):
    def test_social_starts_with_rent_type_and_has_newlines(self):
        text = template_social(_ctx(rent_type="Краткосрочная"))
        self.assertTrue(text.startswith("Краткосрочная аренда"))
        self.assertIn("\n", text)

    def test_social_long_term_from_facebook(self):
        text = template_social(_ctx(object_id="F_20260823_002", rent_type="Долгосрочная"))
        self.assertTrue(text.startswith("Долгосрочная аренда"))

    def test_generate_social_without_llm_fits_new_limit(self):
        ctx = _ctx()
        with patch("description_writer.call_llm", return_value=None):
            text = generate_social_description(ctx)
        self.assertLessEqual(len(text), SOCIAL_LIMIT)
        self.assertTrue(text.startswith("Краткосрочная аренда"))
        self.assertNotIn("Объект №" + ctx["object_id"], text)


class XFormulaTests(unittest.TestCase):
    def test_x_is_dense_and_under_280(self):
        ctx = _ctx()
        raw = template_x(ctx)
        self.assertNotIn("\n", raw)
        self.assertTrue(raw.startswith("Краткосрочная аренда"))
        with patch("description_writer.call_llm", return_value=None):
            fitted = generate_x_description(ctx)
        self.assertLessEqual(len(fitted), X_LIMIT)
        self.assertLessEqual(len(fitted), 275)

    def test_validator_x_trims_to_280(self):
        blob = ("Квартира в аренду на Пхукете с длинным текстом который точно не влезет. " * 20)
        out = validate_and_fit_x(blob, "A_1")
        self.assertLessEqual(len(out), 275)

    def test_validator_social_allows_more_than_280(self):
        blob = ("Краткосрочная аренда\n\nКвартира в аренду, Бангтао. " * 15)
        out = validate_and_fit_social(blob, "A_1")
        self.assertGreater(len(out), 280)
        self.assertLessEqual(len(out), SOCIAL_LIMIT)


if __name__ == "__main__":
    unittest.main()

"""Тесты человеческого поведения userbot (задержки перед ответом).

Окна активности НЕТ намеренно: квалификатор отвечает круглосуточно.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7 import human


def test_typing_delay_scales_with_length_and_bounded():
    short = human.typing_delay_seconds("ок", min_s=1.0, max_s=9.0, jitter=0.0)
    long = human.typing_delay_seconds("а" * 300, min_s=1.0, max_s=9.0, jitter=0.0)
    assert short == 1.0                 # короткое упирается в минимум
    assert long == 9.0                  # длинное упирается в максимум
    assert 1.0 <= short <= long <= 9.0


def test_typing_delay_jitter_positive():
    for _ in range(50):
        d = human.typing_delay_seconds("привет как дела", cps=12, min_s=0.1, max_s=100, jitter=0.25)
        assert d > 0


def test_read_delay_in_range():
    for _ in range(50):
        assert 0.5 <= human.read_delay_seconds(0.5, 2.0) <= 2.0


def test_no_active_hours_gate():
    """Квалификатор работает 24/7 — функций окна активности быть не должно."""
    assert not hasattr(human, "within_active_hours")
    assert not hasattr(human, "seconds_until_active")

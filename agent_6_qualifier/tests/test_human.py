"""Тесты человеческого поведения userbot (задержки, окно активности)."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7 import human


def test_typing_delay_scales_with_length_and_bounded():
    short = human.typing_delay_seconds("ок", min_s=1.0, max_s=9.0, jitter=0.0)
    long = human.typing_delay_seconds("а" * 300, min_s=1.0, max_s=9.0, jitter=0.0)
    assert short == 1.0                 # короткое упирается в минимум
    assert long == 9.0                  # длинное упирается в максимум
    assert 1.0 <= short <= long <= 9.0


def test_typing_delay_jitter_within_range():
    for _ in range(50):
        d = human.typing_delay_seconds("привет как дела", cps=12, min_s=0.1, max_s=100, jitter=0.25)
        assert d > 0


def test_active_hours_day_vs_night():
    # 15:00 по Пхукету (UTC+7) = 08:00 UTC — внутри окна 09:00-22:30
    midday = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
    assert human.within_active_hours(midday, tz_offset_hours=7, start="09:00", end="22:30")
    # 03:00 по Пхукету = 20:00 UTC пред. дня — ночь, окно закрыто
    night = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)
    assert not human.within_active_hours(night, tz_offset_hours=7, start="09:00", end="22:30")


def test_seconds_until_active_zero_when_open():
    midday = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
    assert human.seconds_until_active(midday, tz_offset_hours=7, start="09:00") == 0.0


def test_seconds_until_active_positive_at_night():
    night = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)  # 03:00 местного
    wait = human.seconds_until_active(night, tz_offset_hours=7, start="09:00")
    assert 5 * 3600 <= wait <= 6 * 3600  # до 09:00 местного ~6 часов

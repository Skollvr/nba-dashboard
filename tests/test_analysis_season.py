from datetime import date

from app import get_analysis_season


def test_current_season_uses_previous_base_during_early_window():
    assert get_analysis_season(
        date(2026, 10, 20),
        date(2026, 10, 5),
    ) == "2025-26"


def test_historical_game_keeps_its_own_season():
    assert get_analysis_season(
        date(2025, 10, 25),
        date(2026, 10, 5),
    ) == "2025-26"


def test_current_season_switches_after_november_15():
    assert get_analysis_season(
        date(2026, 11, 20),
        date(2026, 11, 16),
    ) == "2026-27"

from datetime import date

from app import get_analysis_season
from pdf_reader import get_season_string


def test_opening_night_uses_2026_27_with_no_previous_season_fallback():
    assert get_analysis_season(date(2026, 10, 20), date(2026, 10, 22)) == "2026-27"


def test_even_on_the_first_day_of_season_uses_the_new_season():
    assert get_analysis_season(date(2026, 10, 20), date(2026, 10, 20)) == "2026-27"


def test_future_game_uses_its_own_season_during_early_weeks():
    assert get_analysis_season(date(2026, 11, 1), date(2026, 10, 22)) == "2026-27"


def test_revisiting_2026_27_game_after_november_never_substitutes_2025_26():
    assert get_analysis_season(date(2026, 10, 20), date(2027, 1, 10)) == "2026-27"


def test_historical_game_uses_its_own_season():
    assert get_analysis_season(date(2026, 2, 14), date(2026, 10, 22)) == "2025-26"


def test_season_boundary_in_october():
    assert get_season_string(date(2026, 9, 30)) == "2025-26"
    assert get_season_string(date(2026, 10, 20)) == "2026-27"

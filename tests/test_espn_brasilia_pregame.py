from datetime import date

import pandas as pd

from espn_context import _cutoff_timestamp, _pregame_date_mask


def test_pregame_cutoff_on_opening_night_excludes_its_own_game():
    timestamps = pd.Series([
        "2026-10-20T23:00:00Z",
        "2026-10-21T01:30:00Z",
        "2026-10-21T03:30:00Z",
    ])
    cutoff = _cutoff_timestamp("2026-10-20")
    assert _pregame_date_mask(timestamps, cutoff).tolist() == [False, False, False]


def test_overnight_utc_game_is_previous_brasilia_day():
    timestamps = pd.Series([
        "2026-10-20T23:00:00Z",  # Oct 20 20:00 in Brazil
        "2026-10-21T01:30:00Z",  # Oct 20 22:30 in Brazil
        "2026-10-21T03:30:00Z",  # Oct 21 00:30 in Brazil
        "2026-10-22T00:00:00Z",  # Oct 21 21:00 in Brazil
    ])
    cutoff = _cutoff_timestamp("2026-10-21")
    assert _pregame_date_mask(timestamps, cutoff).tolist() == [True, True, False, False]


def test_game_from_day_21_can_enter_day_22_pregame_projection():
    timestamps = pd.Series([
        "2026-10-21T03:30:00Z",
        "2026-10-22T00:00:00Z",
        "2026-10-22T23:30:00Z",
    ])
    cutoff = _cutoff_timestamp(date(2026, 10, 22))
    assert _pregame_date_mask(timestamps, cutoff).tolist() == [True, True, False]

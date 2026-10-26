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


def test_h2h_prior_games_only_in_brasilia_not_utc_day():
    from ui_components import filter_player_log_before_game

    logs = pd.DataFrame({
        "GAME_ID": ["previous", "overnight", "same_day", "future", "undated"],
        "GAME_DATE": [
            "2026-10-20T23:00:00Z", # Oct 20 in Brazil
            "2026-10-21T01:30:00Z", # Oct 20 in Brazil
            "2026-10-21T03:00:00Z", # Oct 21 in Brazil
            "2026-10-23T01:00:00Z",
            None,
        ],
        "PTS": [19, 20, 99, 120, 999],
    })
    actual = filter_player_log_before_game(logs, date(2026, 10, 21))
    assert actual["GAME_ID"].tolist() == ["previous", "overnight"]
    assert actual["PTS"].tolist() == [19, 20]
    assert len(filter_player_log_before_game(logs)) == 5


def test_h2h_selected_date_does_not_include_that_games_result():
    from ui_components import filter_player_log_before_game

    logs = pd.DataFrame({
        "GAME_ID": ["prior", "selected", "later"],
        "GAME_DATE": [
            "2026-10-21T01:30:00Z", # BRT Oct 20
            "2026-10-22T00:00:00Z", # BRT Oct 21
            "2026-10-25T00:00:00Z",
        ],
    })
    assert filter_player_log_before_game(logs, "2026-10-21")[
        "GAME_ID"
    ].tolist() == ["prior"]


def test_play_in_is_not_silently_mixed_into_other_espn_phases():
    import pytest
    from api_espn import _scope_to_espn_type

    assert _scope_to_espn_type("Regular Season") == 2
    assert _scope_to_espn_type("Playoffs") == 3
    assert _scope_to_espn_type("All") is None
    for name in ("PlayIn", "Play-In", "play in"):
        with pytest.raises(ValueError, match="Play-In isolado"):
            _scope_to_espn_type(name)


def test_unverified_play_in_selector_hidden_but_other_scopes_available():
    import inspect
    import app

    source = inspect.getsource(app.main)
    assert '["Temporada Regular", "Playoffs", "Tudo"]' in source
    assert "Play-In separado temporariamente indisponível" in source
    assert '"Play-In": "PlayIn"' not in source


def test_focused_h2h_uses_selected_scope_and_historical_date():
    import inspect
    from ui_components import render_player_focus_panel, render_team_section_v2

    focus_source = inspect.getsource(render_player_focus_panel)
    team_source = inspect.getsource(render_team_section_v2)
    assert 'get_player_log(' in focus_source
    assert 'season, season_scope=season_scope' in focus_source
    assert 'filter_player_log_before_game(' in focus_source
    assert 'target_date=target_date' in team_source
    app_source = inspect.getsource(__import__("app").main)
    assert 'target_date=selected_date,' in app_source

"""FanDuel/BetMGM parallel market regression tests (no live API key needed)."""
from datetime import date
from unittest.mock import patch

import pandas as pd

from api_odds import extract_betmgm_player_props, fetch_nba_odds_events
from processamento import (
    get_line_context,
    merge_betmgm_odds,
    select_bookmaker_lines,
)


def both_books_event():
    def odd(stat, side, mgm=None, fanduel=None):
        return {
            "periodID": "game", "betTypeID": "ou",
            "statID": stat, "playerID": "p1", "sideID": side,
            "byBookmaker": {
                "betmgm": {"available": mgm is not None, "overUnder": mgm, "odds": "-110"},
                "fanduel": {"available": fanduel is not None, "overUnder": fanduel, "odds": "-115"},
            }
        }
    return {
        "eventID": "nba-philly",
        "players": {"p1": {"firstName": "Tyrese", "lastName": "Maxey"}},
        "odds": {
            "pts-over": odd("points", "over", "27.5", "28.5"),
            "pts-under": odd("points", "under", "27.5", "28.5"),
            "fga-over": odd("fieldGoalsAttempted", "over", None, "20.5"),
            "fga-under": odd("fieldGoalsAttempted", "under", None, "20.5"),
            "3pa-over": odd("threePointersAttempted", "over", None, "8.5"),
            "3pa-under": odd("threePointersAttempted", "under", None, "8.5"),
        },
    }


def player():
    return pd.DataFrame([{
        "PLAYER": "Tyrese Maxey", "PLAYER_KEY": "tyrese maxey",
        "PLAYER_ID": 1,
        "PROJ_PTS_V1": 29.0,
        "PROJ_FGA_V1": 21.1,
        "PROJ_3PA_V1": 9.1,
        "PTS_RECENT_VALUES": [25, 30],
        "FGA_RECENT_VALUES": [22, 19],
        "3PA_RECENT_VALUES": [9, 8],
    }])


def attach_and_select(enabled=("betmgm", "fanduel"), priority="betmgm"):
    frame = player()
    event = both_books_event()
    for book in ("betmgm", "fanduel"):
        data = extract_betmgm_player_props(
            event, bookmaker_id=book
        ) if book in enabled else pd.DataFrame()
        frame = merge_betmgm_odds(frame, data, bookmaker_id=book)
    return select_bookmaker_lines(frame, enabled, priority)


def test_selected_book_is_independent_and_mgm_remains_default():
    row = attach_and_select().iloc[0]
    assert row["BETMGM_PTS_LINE"] == 27.5
    assert row["FANDUEL_PTS_LINE"] == 28.5
    assert row["MARKET_PTS_LINE"] == 27.5
    assert row["MARKET_PTS_SOURCE"] == "BetMGM"
    ctx = get_line_context(row, "PTS", None, use_market_line=True)
    assert ctx["quoted_source"] == "BetMGM"
    assert ctx["line_value"] == 27.5


def test_fanduel_selected_first_when_enabled():
    row = attach_and_select(priority="fanduel").iloc[0]
    assert row["MARKET_PTS_LINE"] == 28.5
    assert row["MARKET_PTS_SOURCE"] == "FanDuel"
    ctx = get_line_context(row, "PTS", None, use_market_line=True)
    assert ctx["line_source"] == "FanDuel"
    assert ctx["line_value"] == 28.5


def test_fanduel_fills_missing_mgm_fga_and_3pa_without_inventing_lines():
    row = attach_and_select().iloc[0]
    assert pd.isna(row["BETMGM_FGA_LINE"])
    assert pd.isna(row["BETMGM_3PA_LINE"])
    assert row["MARKET_FGA_LINE"] == 20.5
    assert row["MARKET_3PA_LINE"] == 8.5
    assert get_line_context(row, "FGA", None, use_market_line=True)["line_source"] == "FanDuel"
    assert get_line_context(row, "3PA", None, use_market_line=True)["line_source"] == "FanDuel"


def test_fanduel_only_and_both_disabled_are_safe():
    fanduel_row = attach_and_select(enabled=("fanduel",)).iloc[0]
    assert fanduel_row["MARKET_PTS_SOURCE"] == "FanDuel"
    assert pd.isna(fanduel_row["BETMGM_PTS_LINE"])
    manual = attach_and_select(enabled=()).iloc[0]
    manual["MANUAL_LINE_PTS"] = 26.5
    assert get_line_context(manual, "PTS", None, True)["line_source"] == "Manual"
    assert pd.isna(manual["MARKET_PTS_LINE"])


def test_both_bookmakers_share_a_single_api_request():
    fetch_nba_odds_events.clear()
    params_seen = []
    def response_fn(url, params, headers, timeout):
        params_seen.append(params.copy())
        class R:
            def raise_for_status(self): pass
            def json(self): return {"success": True, "data": [both_books_event()]}
        return R()
    with (
        patch("api_odds.get_odds_api_key", return_value="fake-key"),
        patch("api_odds.requests.get", side_effect=response_fn),
    ):
        fetched = fetch_nba_odds_events(
            date(2026, 10, 22), bookmaker_ids=("betmgm", "fanduel")
        )
    assert len(fetched) == 1
    assert len(params_seen) == 1
    assert params_seen[0]["bookmakerID"] == "betmgm,fanduel"


def test_fanduel_without_a_market_never_uses_consensus_line():
    evt = both_books_event()
    for item in evt["odds"].values():
        item["byBookmaker"]["fanduel"]["available"] = False
        item["bookOverUnder"] = "19.5"
    assert extract_betmgm_player_props(evt, bookmaker_id="fanduel").empty



def test_fanduel_selected_price_is_not_taken_from_betmgm():
    row = attach_and_select(priority="fanduel").iloc[0]
    # FanDuel -115, BetMGM -110; the two books must not share odds.
    assert round(row["MARKET_PTS_OVER_DEC"], 2) == 1.87
    assert round(row["MARKET_PTS_UNDER_DEC"], 2) == 1.87
    assert row["MARKET_PTS_SOURCE"] == "FanDuel"


def test_fanduel_shows_on_modern_player_card_without_valid_projection():
    from ui_refresh import render_player_card_refresh

    row = attach_and_select(priority="fanduel").iloc[0].copy()
    row["SEASON_GP"] = 0
    row["PROJ_PTS_V1"] = float("nan")
    row["TEAM_ID"] = 1610612755
    row["TEAM_NAME"] = "Philadelphia 76ers"
    row["TEAM_ABBR"] = "PHI"
    row["POSITION"] = "G"
    row["ROLE"] = "Titular projetado"
    row["INJ_STATUS"] = "Available"
    seen = []
    with patch(
        "ui_refresh.st.markdown",
        side_effect=lambda html, **kwargs: seen.append(html),
    ):
        render_player_card_refresh(row, "PTS", None, use_market_line=True)
    assert "Linha FanDuel" in seen[0]
    assert "28.5 PTS" in seen[0]
    assert "Sem Edge" in seen[0]
    assert 'class="nova-pred-value">—</div>' in seen[0]

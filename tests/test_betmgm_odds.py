"""Regression tests for SportsGameOdds BetMGM primary-line fallback."""
from datetime import date
from unittest.mock import patch
from types import SimpleNamespace

import pandas as pd

from api_odds import (
    _brasilia_utc_window,
    extract_betmgm_player_props,
    fetch_nba_odds_events,
    get_odds_api_key,
    find_matching_odds_event,
)
from processamento import get_line_context, merge_betmgm_odds


def _event():
    return {
        "eventID": "nba-game-1",
        "teams": {
            "home": {"names": {"long": "Philadelphia 76ers"}},
            "away": {"names": {"long": "Cleveland Cavaliers"}},
        },
        "players": {
            "p1": {"firstName": "Tyrese", "lastName": "Maxey"},
            "p2": {"firstName": "Joel", "lastName": "Embiid"},
        },
        "odds": {
            "a": {
                "periodID": "game", "betTypeID": "ou",
                "statID": "points", "playerID": "p1", "sideID": "over",
                "bookOverUnder": "27.5",  # consensus, NOT BetMGM
                "byBookmaker": {
                    "betmgm": {"available": True, "overUnder": "28.5",
                               "odds": "-115", "lastUpdatedAt": "2026-10-22T18:00:00Z"}
                },
            },
            "b": {
                "periodID": "game", "betTypeID": "ou",
                "statID": "points", "playerID": "p1", "sideID": "under",
                "bookOverUnder": "27.5",
                "byBookmaker": {
                    "betmgm": {"available": True, "overUnder": "28.5",
                               "odds": "-105", "lastUpdatedAt": "2026-10-22T18:00:00Z"}
                },
            },
            "c": {
                "periodID": "game", "betTypeID": "ou",
                "statID": "rebounds", "playerID": "p2", "sideID": "over",
                "byBookmaker": {"betmgm": {"available": False, "overUnder": "10.5"}},
            },
            "d": {
                "periodID": "game", "betTypeID": "ou",
                "statID": "assists", "playerID": "p2", "sideID": "over",
                "byBookmaker": {
                    "fanduel": {"available": True, "overUnder": "4.5"}
                },
            },
        },
    }


def _player(**fields):
    baseline = {
        "PLAYER": "Tyrese Maxey",
        "PLAYER_KEY": "tyrese maxey",
        "PLAYER_ID": 1,
        "PROJ_PTS_V1": 30.1,
        "PTS_RECENT_VALUES": [28, 33, 27, 30],
    }
    baseline.update(fields)
    return pd.Series(baseline)


def test_betmgm_line_is_bookmaker_specific_not_consensus():
    frame = extract_betmgm_player_props(_event())
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["BETMGM_PTS_LINE"] == 28.5
    assert row["BETMGM_PTS_OVER_DEC"] == 1.87
    assert row["BETMGM_PTS_UNDER_DEC"] == 1.95
    assert pd.isna(row["BETMGM_REB_LINE"])


def test_market_has_priority_over_manual_and_manual_fills_missing_market():
    df = merge_betmgm_odds(
        pd.DataFrame([dict(_player())]),
        extract_betmgm_player_props(_event()),
    )
    row = df.iloc[0].copy()
    row["MANUAL_LINE_PTS"] = 27.5
    ctx = get_line_context(row, "PTS", None, use_market_line=True)
    assert ctx["line_source"] == "BetMGM"
    assert ctx["line_value"] == 28.5
    assert ctx["has_market_line"]

    row["BETMGM_PTS_LINE"] = float("nan")
    fallback = get_line_context(row, "PTS", None, use_market_line=True)
    assert fallback["line_source"] == "Manual"
    assert fallback["line_value"] == 27.5
    assert not fallback["has_market_line"]

    row["MANUAL_LINE_PTS"] = float("nan")
    missing = get_line_context(row, "PTS", None, use_market_line=True)
    assert not missing["has_active_line"]
    assert missing["label"] == "Linha indisponível"
    assert pd.isna(missing["line_value"]) or missing["line_value"] is None


def test_missing_projection_never_produces_market_edge():
    row = _player(PROJ_PTS_V1=float("nan"), BETMGM_PTS_LINE=28.5)
    ctx = get_line_context(row, "PTS", None, use_market_line=True)
    assert not ctx["has_active_line"]
    assert ctx["label"] == "Dados insuficientes"


def test_safely_ignores_unavailable_bookmaker_and_wrong_event():
    event = _event()
    assert find_matching_odds_event(
        [event], "Philadelphia 76ers", "Cleveland Cavaliers",
    ) == event
    assert find_matching_odds_event(
        [event], "Boston Celtics", "Cleveland Cavaliers",
    ) is None
    event["odds"]["a"]["byBookmaker"]["betmgm"]["available"] = False
    event["odds"]["b"]["byBookmaker"]["betmgm"]["available"] = False
    assert extract_betmgm_player_props(event).empty


def test_date_query_uses_brazil_game_date_not_utc_midnight():
    after, before = _brasilia_utc_window(date(2026, 10, 22))
    assert after == "2026-10-22T03:00:00Z"
    assert before == "2026-10-23T03:00:00Z"


def test_fetch_follows_cursor_and_filters_betmgm():
    fetch_nba_odds_events.clear()
    seen = []

    def response_fn(url, params, headers, timeout):
        seen.append(dict(params))
        assert headers["x-api-key"] == "unit-test-key"
        assert params["bookmakerID"] == "betmgm"
        class Response:
            def raise_for_status(self): return None
            def json(self):
                if "cursor" not in params:
                    return {"success": True, "data": [{"eventID": "one"}],
                            "nextCursor": "page2"}
                return {"success": True, "data": [{"eventID": "two"}],
                        "nextCursor": None}
        return Response()

    with (
        patch("api_odds.get_odds_api_key", return_value="unit-test-key"),
        patch("api_odds.requests.get", side_effect=response_fn),
    ):
        events = fetch_nba_odds_events(date(2026, 10, 22))
    assert [e["eventID"] for e in events] == ["one", "two"]
    assert len(seen) == 2
    assert seen[1]["cursor"] == "page2"


def test_key_can_be_read_from_cloud_secrets_without_exposure():
    with (
        patch("api_odds.os.getenv", return_value=""),
        patch("api_odds.st.secrets", {"SPORTSGAMEODDS_API_KEY": "secret-value"}),
    ):
        assert get_odds_api_key() == "secret-value"



def test_player_stat_entity_id_is_supported_without_player_id_field():
    event = _event()
    for item in event["odds"].values():
        if item.get("playerID") == "p1":
            item["statEntityID"] = item.pop("playerID")
    df = extract_betmgm_player_props(event)
    assert len(df) == 1
    assert df.iloc[0]["PLAYER_NAME_ODDS"] == "Tyrese Maxey"
    assert df.iloc[0]["BETMGM_PTS_LINE"] == 28.5


def test_does_not_mix_over_under_odds_from_inconsistent_lines():
    event = _event()
    event["odds"]["b"]["byBookmaker"]["betmgm"]["overUnder"] = "29.5"
    df = extract_betmgm_player_props(event)
    assert df.iloc[0]["BETMGM_PTS_LINE"] == 28.5
    assert df.iloc[0]["BETMGM_PTS_UNDER_DEC"] is None


def test_ui_has_no_old_backtest_or_pdf_diagnostic_controls():
    from pathlib import Path
    app = Path("app.py").read_text(encoding="utf-8")
    ui = Path("ui_components.py").read_text(encoding="utf-8")
    assert "Backtest temporal — minutos + forma" not in app
    assert "Diagnóstico técnico ESPN — FGA" not in app
    assert "Diagnóstico da fonte de lesões" not in ui
    assert "Diagnosticar localização, download e leitura do PDF NBA" not in ui
    assert 'value=True,' in app  # BetMGM priority is enabled by default
    assert "Gerar linha manual" in app

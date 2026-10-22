"""Diagnose blocked Stake odds without disrupting valid BetMGM lines."""
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest
import requests

from api_odds import (
    SportsGameOddsHTTPError,
    _request_events_page,
)
from processamento import get_matchup_context

def fake_http_response(status_code):
    class Response:
        def __init__(self):
            self.status_code = status_code
        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(
                    "Request rejected",
                    response=self,
                )
        def json(self):
            return {"success": True, "data": []}
    return Response()


def test_http_403_preserved_without_retry_or_exposing_key():
    with (
        patch("api_odds.requests.get", return_value=fake_http_response(403)) as get,
        patch("api_odds.time.sleep") as sleep,
    ):
        with pytest.raises(SportsGameOddsHTTPError) as error:
            _request_events_page({"bookmakerID": "stake"}, "never-display-this")
    assert error.value.status_code == 403
    assert "never-display-this" not in str(error.value)
    get.assert_called_once()
    sleep.assert_not_called()


def test_http_400_is_classified_separately_from_no_lines():
    with patch("api_odds.requests.get", return_value=fake_http_response(400)):
        with pytest.raises(SportsGameOddsHTTPError) as error:
            _request_events_page({"bookmakerID": "stake"}, "secret-key")
    assert error.value.status_code == 400


def test_http_429_retries_only_once_then_returns_success():
    with (
        patch(
            "api_odds.requests.get",
            side_effect=[fake_http_response(429), fake_http_response(200)],
        ) as get,
        patch("api_odds.time.sleep") as sleep,
    ):
        response = _request_events_page({"bookmakerID": "stake"}, "key")
    assert response.status_code == 200
    assert get.call_count == 2
    sleep.assert_called_once()


def _simple_roster():
    return pd.DataFrame([{
        "PLAYER": "Tyrese Maxey",
        "PLAYER_KEY": "tyrese maxey",
        "PLAYER_ID": 1234,
        "PROJ_PTS_V1": 30.1,
        "PTS_RECENT_VALUES": [28.0, 30.0],
    }])


def _betmgm_event():
    return {
        "eventID": "test-game",
        "teams": {
            "home": {"names": {"long": "Philadelphia 76ers"}},
            "away": {"names": {"long": "Cleveland Cavaliers"}},
        },
        "players": {
            "p1": {"firstName": "Tyrese", "lastName": "Maxey"},
        },
        "odds": {
            "PTS_OVER": {
                "periodID": "game", "betTypeID": "ou",
                "statID": "points", "playerID": "p1", "sideID": "over",
                "byBookmaker": {
                    "betmgm": {
                        "available": True,
                        "overUnder": "28.5",
                        "odds": "-110",
                    }
                },
            },
        },
    }


def test_stake_forbidden_preserves_betmgm_player_line():
    def odds_fetch(target_date, bookmaker_ids):
        if bookmaker_ids == ("stake",):
            raise SportsGameOddsHTTPError(403)
        assert bookmaker_ids == ("betmgm",)
        return [_betmgm_event()]

    with (
        patch("processamento.build_team_table", return_value=_simple_roster()),
        patch("processamento.get_espn_team_rotation", return_value=pd.DataFrame()),
        patch("processamento.merge_daily_lineups", side_effect=lambda df, *a, **k: df),
        patch("processamento.get_preferred_injury_report", return_value=pd.DataFrame()),
        patch("processamento.merge_injury_report", side_effect=lambda df, *a, **k: df),
        patch("processamento.enrich_team_with_context", side_effect=lambda team_df, **k: team_df),
        patch("processamento.get_odds_api_key", return_value="configured"),
        patch("processamento.fetch_nba_odds_events", side_effect=odds_fetch) as fetch,
    ):
        away, home = get_matchup_context(
            1610612739, 1610612755,
            "Cleveland Cavaliers", "Philadelphia 76ers",
            "2026-27", include_market=True,
            include_stake=True,
            target_date=date(2026, 10, 23),
        )

    assert fetch.call_count == 2
    assert home.iloc[0]["BETMGM_PTS_LINE"] == 28.5
    assert pd.isna(home.iloc[0]["STAKE_PTS_LINE"])
    assert home.iloc[0]["MARKET_PTS_SOURCE"] == "BetMGM"
    assert home.iloc[0]["MARKET_PTS_LINE"] == 28.5
    assert "Stake: HTTP 403" in home.iloc[0]["ODDS_FETCH_STATUS"]
    assert "BetMGM: 1 jogador(es) com props" in home.iloc[0]["ODDS_FETCH_STATUS"]

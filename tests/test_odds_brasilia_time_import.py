"""Regression: API retry time module must not shadow datetime.time."""
from datetime import date
from unittest.mock import patch

import pytest

from api_odds import _brasilia_utc_window, fetch_nba_odds_events


@pytest.mark.parametrize("bookmaker", ("betmgm", "fanduel"))
def test_brasilia_game_date_fetch_reaches_sportsbook(bookmaker):
    fetch_nba_odds_events.clear()
    seen = []
    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"success": True, "data": [], "nextCursor": None}

    def fake_get(url, params, headers, timeout):
        seen.append(dict(params))
        return Response()

    with (
        patch("api_odds.get_odds_api_key", return_value="fake-test-key"),
        patch("api_odds.requests.get", side_effect=fake_get),
    ):
        events = fetch_nba_odds_events(
            target_date=date(2026, 10, 23),
            bookmaker_ids=(bookmaker,),
        )
    assert events == []
    assert len(seen) == 1
    assert seen[0]["bookmakerID"] == bookmaker
    assert seen[0]["startsAfter"] == "2026-10-23T03:00:00Z"
    assert seen[0]["startsBefore"] == "2026-10-24T03:00:00Z"


def test_datetime_time_and_retry_sleep_are_distinct_imports():
    from api_odds import datetime_time, time
    assert datetime_time.min.isoformat() == "00:00:00"
    assert callable(time.sleep)
    assert _brasilia_utc_window(date(2026, 10, 23)) == (
        "2026-10-23T03:00:00Z", "2026-10-24T03:00:00Z"
    )

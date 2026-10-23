"""Safety regressions: NBA feed confirmation and ESPN rotation."""
import numpy as np
import pandas as pd
import pytest

from api_lineups import normalize_daily_lineups
from lineup_rotation import merge_nba_lineup_evidence, summarize_recent_rotation


def roster():
    return pd.DataFrame([{
        "PLAYER_ID": 1966,  # ESPN and official NBA ID spaces may differ
        "PLAYER": "LeBron James", "TEAM_ID": 1610612747,
        "ROLE": "Titular projetado", "LINEUP_STATUS": "Titular projetado",
        "LINEUP_SOURCE": "ESPN Depth Chart",
        "PROJECTED_MINUTES_EXTERNAL": np.nan, "PROJ_MIN_V1": 32.0,
        "IS_UNAVAILABLE": False, "RECENT_MIN_L10": [33, 30, 29, 31, 34, 32],
    }])


def feed_player(**changes):
    row = {
        "playerId": 2544,
        "playerName": "LeBron James",
        "teamId": 1610612747,
        "isStarter": True,
        "isConfirmed": True,
        "projectedMinutes": 40,
    }
    row.update(changes)
    return row


def normalized(rows):
    return normalize_daily_lineups({"games": [{"players": rows}]})


def test_explicit_confirmed_starter_with_team_and_name():
    evidence = normalized([feed_player()])
    assert evidence.iloc[0].LINEUP_STATUS == "Titular confirmado"
    merged = merge_nba_lineup_evidence(roster(), evidence, 1610612747, "23/10/2026 18:40")
    row = merged.iloc[0]
    assert row.ROLE == "Titular confirmado"
    assert row.LINEUP_SOURCE == "NBA Daily Lineups"
    assert row.LINEUP_CHECKED_BRT == "23/10/2026 18:40"
    assert row.PROJ_MIN_V1 == 32.0
    assert pd.isna(row.PROJECTED_MINUTES_EXTERNAL)


@pytest.mark.parametrize("change", [
    {"isStarter": None},
    {"isStarter": False},
    {"isConfirmed": False},
    {"isConfirmed": False, "lineupStatus": "unconfirmed"},
])
def test_confirmation_must_not_be_inferred(change):
    evidence = normalized([feed_player(**change)])
    merged = merge_nba_lineup_evidence(roster(), evidence, 1610612747)
    assert merged.iloc[0].ROLE != "Titular confirmado"


def test_wrong_team_cannot_promote_to_confirmed():
    evidence = normalized([feed_player(teamId=1610612748)])
    merged = merge_nba_lineup_evidence(roster(), evidence, 1610612747)
    assert merged.iloc[0].ROLE == "Titular projetado"


def test_missing_team_cannot_promote_to_confirmed():
    evidence = normalized([feed_player(teamId=None)])
    merged = merge_nba_lineup_evidence(roster(), evidence, 1610612747)
    assert merged.iloc[0].ROLE == "Titular projetado"


def test_unavailable_player_cannot_be_promoted():
    current = roster()
    current.loc[0, "IS_UNAVAILABLE"] = True
    merged = merge_nba_lineup_evidence(
        current, normalized([feed_player()]), 1610612747
    )
    assert merged.iloc[0].ROLE == "Titular projetado"


def test_duplicate_names_are_ambiguous_and_not_promoted():
    current = pd.concat([roster(), roster()], ignore_index=True)
    merged = merge_nba_lineup_evidence(
        current, normalized([feed_player()]), 1610612747
    )
    assert merged.ROLE.eq("Titular confirmado").sum() == 0


def test_unknown_feed_does_not_invent_confirmations():
    current = roster()
    empty = merge_nba_lineup_evidence(current, pd.DataFrame(), 1610612747)
    assert empty.iloc[0].ROLE == "Titular projetado"
    assert current.equals(roster())


def test_real_minutes_observations_and_order():
    result = summarize_recent_rotation(roster().iloc[0])
    assert result["sample"] == 6
    assert result["last_game"] == 33
    assert result["l5_mean"] == pytest.approx((33 + 30 + 29 + 31 + 34) / 5)
    assert result["l10_mean"] == pytest.approx((33 + 30 + 29 + 31 + 34 + 32) / 6)
    assert result["values_oldest_first"] == [32, 34, 31, 29, 30, 33]
    assert result["maximum"] == 34
    assert result["minimum"] == 29


def test_missing_rotation_does_not_become_zero_minutes():
    row = pd.Series({"RECENT_MIN_L10": [None, np.nan, 0, -5]})
    result = summarize_recent_rotation(row)
    assert result["sample"] == 0
    assert result["l5_mean"] is None
    assert result["last_game"] is None


def test_lineup_feed_only_with_confirmed_text_but_no_starter_is_not_confirmed():
    evidence = normalized([feed_player(isStarter=None, isConfirmed=None, lineupStatus="Confirmed")])
    assert evidence.empty or not evidence.LINEUP_STATUS.eq("Titular confirmado").any()

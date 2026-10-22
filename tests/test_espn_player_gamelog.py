import pandas as pd

from api_espn import (
    _parse_espn_player_gamelog_payload,
    aggregate_espn_player_stats,
    parse_espn_minutes,
    parse_espn_shooting_stats,
)


def test_parses_flat_espn_gamelog_events():
    payload = {
        "labels": ["DATE", "OPP", "RESULT", "MIN", "FG", "3PT", "FT", "REB", "AST", "STL", "BLK", "PTS"],
        "events": [
            {
                "id": "401000001",
                "date": "2025-10-25T00:00Z",
                "opponent": {"abbreviation": "BOS"},
                "gameResult": "W",
                "homeAway": "home",
                "stats": ["36", "12-24", "4-10", "4-4", "5", "7", "1", "0", "32"],
            }
        ],
    }

    df = _parse_espn_player_gamelog_payload(payload, 123, "Regular Season")

    assert len(df) == 1
    row = df.iloc[0]
    assert row["PLAYER_ID"] == 123
    assert row["MIN"] == 36
    assert row["PTS"] == 32
    assert row["REB"] == 5
    assert row["AST"] == 7
    assert row["FG3M"] == 4
    assert row["FGA"] == 24
    assert row["FG3A"] == 10
    assert row["PRA"] == 44
    assert row["MATCHUP"] == "vs. BOS"


def test_parses_legacy_season_types_and_filters_regular_season():
    payload = {
        "labels": ["MIN", "FG", "3PT", "FT", "REB", "AST", "STL", "BLK", "PTS"],
        "events": {
            "401000001": {
                "gameDate": "2025-10-25T00:00Z",
                "opponent": {"abbreviation": "BOS"},
                "gameResult": "L",
                "atVs": "@",
            },
            "401000002": {
                "gameDate": "2026-04-20T00:00Z",
                "opponent": {"abbreviation": "NYK"},
                "gameResult": "W",
                "atVs": "vs",
            },
        },
        "seasonTypes": [
            {
                "id": 2,
                "name": "Regular Season",
                "categories": [
                    {
                        "type": "game",
                        "events": [
                            {
                                "eventId": "401000001",
                                "stats": ["34", "8-17", "2-6", "5-6", "8", "6", "1", "0", "23"],
                            }
                        ],
                    }
                ],
            },
            {
                "id": 3,
                "name": "Postseason",
                "categories": [
                    {
                        "type": "game",
                        "events": [
                            {
                                "eventId": "401000002",
                                "stats": ["40", "10-20", "3-8", "4-4", "9", "7", "2", "1", "27"],
                            }
                        ],
                    }
                ],
            },
        ],
    }

    df = _parse_espn_player_gamelog_payload(payload, 456, "Regular Season")

    assert len(df) == 1
    row = df.iloc[0]
    assert row["GAME_ID"] == "401000001"
    assert row["MATCHUP"] == "@ BOS"
    assert row["PTS"] == 23
    assert row["REB"] == 8
    assert row["AST"] == 6
    assert pd.notna(row["GAME_DATE"])


def test_normalizes_espn_opponent_alias_for_h2h_matching():
    payload = {
        "labels": ["MIN", "FG", "3PT", "FT", "REB", "AST", "STL", "BLK", "PTS"],
        "events": [
            {
                "id": "401000003",
                "date": "2026-01-10T00:00Z",
                "opponent": {"abbreviation": "NY"},
                "gameResult": "W",
                "homeAway": "away",
                "stats": ["35", "9-18", "2-6", "4-5", "7", "5", "1", "0", "24"],
            }
        ],
    }

    df = _parse_espn_player_gamelog_payload(payload, 789, "Regular Season")

    assert len(df) == 1
    assert df.iloc[0]["MATCHUP"] == "@ NYK"


def test_espn_fgm_a_and_3pm_a_labels_are_recognized():
    payload = {
        "labels": ["MIN", "FGM-A", "3PM-A", "PTS", "REB", "AST"],
        "events": [{
            "id": "fgm-variant",
            "date": "2026-02-01T23:00:00Z",
            "opponent": {"abbreviation": "NY"},
            "homeAway": "away",
            "stats": ["34:30", "11-23", "2-9", "24", "6", "8"],
        }],
    }
    logs = _parse_espn_player_gamelog_payload(payload, 22)
    assert len(logs) == 1
    assert logs.iloc[0]["MIN"] == 34.5
    assert logs.iloc[0]["FGA"] == 23
    assert logs.iloc[0]["FG3A"] == 9
    assert logs.iloc[0]["FG3M"] == 2
    assert logs.iloc[0]["MATCHUP"] == "@ NYK"
    logs["PLAYER_NAME"] = "Player"
    logs["TEAM_ID"] = 1
    summary = aggregate_espn_player_stats(logs)
    assert summary.iloc[0]["FGA"] == 23
    assert summary.iloc[0]["FGA_VALID_GP"] == 1


def test_missing_fga_is_not_silently_zeroed_in_aggregation():
    fga, made, three_attempts, issues = parse_espn_shooting_stats({
        "3PM-A": "2-8",
    })
    assert fga is None
    assert made == 2
    assert three_attempts == 8
    assert "FGA_missing" in issues

    logs = pd.DataFrame([{
        "PLAYER_ID": 12,
        "PLAYER_NAME": "Player",
        "TEAM_ID": 1,
        "GAME_DATE": pd.Timestamp("2026-02-02"),
        "MIN": 30,
        "PTS": 20, "REB": 4, "AST": 5,
        "FG3M": 2, "FGA": float("nan"), "FG3A": 8,
    }])
    summary = aggregate_espn_player_stats(logs)
    assert pd.isna(summary.iloc[0]["FGA"])
    assert summary.iloc[0]["FGA_VALID_GP"] == 0
    assert summary.iloc[0]["FG3A_VALID_GP"] == 1


def test_invalid_attempt_relation_is_not_used():
    fga, made, three_attempts, issues = parse_espn_shooting_stats({
        "FGM-A": "2-4",
        "3PM-A": "3-8",
    })
    assert fga is None
    assert made == 3
    assert three_attempts == 8
    assert "3PA_exceeds_FGA" in issues


def test_valid_zero_attempts_are_kept():
    fga, made, three_attempts, issues = parse_espn_shooting_stats({
        "FGM-A": "0-0",
        "3PM-A": "0-0",
    })
    assert (fga, made, three_attempts) == (0.0, 0.0, 0.0)
    assert not issues
    assert parse_espn_minutes("37:45") == 37.75
    assert parse_espn_minutes("DNP") is None




def test_recovers_unknown_espn_fg_header_only_when_score_verifies():
    payload = {
        "labels": ["MIN", "UNKNOWN_FG_FIELD", "3PT", "FT", "REB", "AST", "STL", "BLK", "PTS"],
        "events": [{
            "id": "safe-recovery",
            "date": "2026-01-21T00:00:00Z",
            "stats": ["36", "12-24", "4-10", "4-4", "8", "7", "1", "0", "32"],
        }],
    }
    logs = _parse_espn_player_gamelog_payload(payload, 900)
    assert logs.iloc[0]["FGA"] == 24
    assert logs.iloc[0]["FG3A"] == 10
    assert logs.iloc[0]["FG3M"] == 4
    assert logs.iloc[0]["SHOOTING_ISSUES"] == ""


def test_rejects_unverifiable_fg_vector_with_wrong_points():
    payload = {
        "labels": ["MIN", "UNKNOWN_FG_FIELD", "3PT", "FT", "REB", "AST", "STL", "BLK", "PTS"],
        "events": [{
            "id": "unsafe-recovery",
            "date": "2026-01-21T00:00:00Z",
            "stats": ["36", "12-24", "4-10", "4-4", "8", "7", "1", "0", "33"],
        }],
    }
    logs = _parse_espn_player_gamelog_payload(payload, 900)
    assert pd.isna(logs.iloc[0]["FGA"])
    assert logs.iloc[0]["FG3A"] == 10


def test_recovers_fg_attempts_from_percentage_header_containing_pairs():
    payload = {
        "labels": ["MIN", "FG%", "3PT", "FT", "REB", "AST", "STL", "BLK", "PTS"],
        "events": [{
            "id": "fg-percent-label",
            "date": "2026-01-21T00:00:00Z",
            "stats": ["36", "12-24", "4-10", "4-4", "8", "7", "1", "0", "32"],
        }],
    }
    logs = _parse_espn_player_gamelog_payload(payload, 900)
    assert logs.iloc[0]["FGA"] == 24


def test_missing_field_goal_attempts_remain_nan_with_known_three_attempts():
    # Regression: previously fillna(0) created a fake 0-FGA game.
    payload = {
        "labels": ["MIN", "3PT", "REB", "AST", "PTS"],
        "events": [{
            "id": "missing-fga",
            "date": "2026-02-05T01:00:00Z",
            "stats": ["34", "3-8", "7", "5", "25"],
        }],
    }
    logs = _parse_espn_player_gamelog_payload(payload, 999)
    assert len(logs) == 1
    assert pd.isna(logs.loc[0, "FGA"])
    assert logs.loc[0, "FG3A"] == 8
    logs["PLAYER_NAME"] = "Demo Player"
    logs["TEAM_ID"] = 1610612738
    agg = aggregate_espn_player_stats(logs)
    assert pd.isna(agg.loc[0, "FGA"])
    assert agg.loc[0, "FGA_VALID_GP"] == 0
    assert agg.loc[0, "FG3A_VALID_GP"] == 1


def test_descriptive_espn_names_with_made_attempted_values():
    # Other ESPN variants use descriptive keys for made/attempt pairs.
    payload = {
        "names": [
            "date", "opponent", "gameResult", "minutes",
            "fieldGoalsMade", "threePointsMade", "freeThrowsMade",
            "rebounds", "assists", "steals", "blocks", "points",
        ],
        "events": [{
            "id": "names-variant",
            "date": "2026-01-16T00:00:00Z",
            "stats": ["36", "9-21", "4-10", "2-2", "8", "7", "1", "0", "24"],
        }],
    }
    logs = _parse_espn_player_gamelog_payload(payload, 998)
    assert logs.loc[0, "FGA"] == 21
    assert logs.loc[0, "FG3A"] == 10
    assert logs.loc[0, "FG3M"] == 4
    assert logs.loc[0, "SHOOTING_ISSUES"] == ""


def test_three_attempts_exceeding_total_are_flagged_not_clipped():
    payload = {
        "labels": ["MIN", "FG", "3PT", "PTS"],
        "events": [{
            "id": "invalid-attempts",
            "date": "2026-02-01T00:00:00Z",
            "stats": ["32", "4-5", "2-8", "12"],
        }],
    }
    logs = _parse_espn_player_gamelog_payload(payload, 997)
    assert len(logs) == 1
    assert pd.isna(logs.loc[0, "FGA"])
    assert logs.loc[0, "FG3A"] == 8
    assert "3PA_exceeds_FGA" in logs.loc[0, "SHOOTING_ISSUES"]

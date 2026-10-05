import pandas as pd

from api_espn import _parse_espn_player_gamelog_payload


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

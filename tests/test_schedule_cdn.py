import unittest
from datetime import date

import pandas as pd

from api_nba import (
    _filter_espn_events_for_date,
    _games_from_espn_payload,
    _games_from_espn_team_schedule_frames,
    _merge_espn_scoreboard_payloads,
    _nba_team_from_espn,
)
from config import TEAM_LOOKUP


class EspnScheduleParsingTests(unittest.TestCase):
    def test_builds_game_row_and_maps_team_abbreviations(self):
        payload = {
            "events": [
                {
                    "id": "401999999",
                    "date": "2026-10-20T23:30:00Z",
                    "status": {
                        "type": {
                            "shortDetail": "7:30 PM ET"
                        }
                    },
                    "competitions": [
                        {
                            "competitors": [
                                {
                                    "homeAway": "away",
                                    "team": {
                                        "abbreviation": "BOS",
                                        "displayName": "Boston Celtics",
                                    },
                                },
                                {
                                    "homeAway": "home",
                                    "team": {
                                        "abbreviation": "NYK",
                                        "displayName": "New York Knicks",
                                    },
                                },
                            ]
                        }
                    ],
                }
            ]
        }

        result = _games_from_espn_payload(payload)

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["VISITOR_TEAM_ABBR"], "BOS")
        self.assertEqual(result.iloc[0]["HOME_TEAM_ABBR"], "NYK")
        self.assertGreater(int(result.iloc[0]["VISITOR_TEAM_ID"]), 0)
        self.assertGreater(int(result.iloc[0]["HOME_TEAM_ID"]), 0)
        self.assertIn("Boston Celtics @ New York Knicks", result.iloc[0]["label"])
        self.assertIn("20:30 BRT", result.iloc[0]["label"])
        self.assertEqual(result.iloc[0]["GAME_DATE_BRT"], "20/10/2026")
        self.assertEqual(result.iloc[0]["GAME_TIME_BRT"], "20:30 BRT")

    def test_returns_empty_frame_for_empty_schedule(self):
        result = _games_from_espn_payload({"events": []})

        self.assertTrue(result.empty)
        self.assertIn("GAME_ID", result.columns)

    def test_monthly_payload_groups_games_by_brasilia_calendar_date(self):
        payload = {
            "events": [
                {"id": "bos-det", "date": "2026-10-20T23:00:00Z"},
                {"id": "okc-sas", "date": "2026-10-21T01:30:00Z"},
                # 03:30 UTC is 00:30 in Brasilia, so this belongs to Oct. 21 locally.
                {"id": "late-west", "date": "2026-10-21T03:30:00Z"},
                {"id": "atl-orl", "date": "2026-10-21T23:00:00Z"},
            ]
        }

        oct20 = _filter_espn_events_for_date(payload, date(2026, 10, 20))
        oct21 = _filter_espn_events_for_date(payload, date(2026, 10, 21))

        self.assertEqual(
            [event["id"] for event in oct20["events"]],
            ["bos-det", "okc-sas"],
        )
        self.assertEqual(
            [event["id"] for event in oct21["events"]],
            ["late-west", "atl-orl"],
        )

    def test_adjacent_daily_payloads_are_merged_before_brasilia_filter(self):
        payload_20 = {
            "events": [
                {"id": "game-a", "date": "2026-10-20T23:00:00Z"},
            ]
        }
        payload_21 = {
            "events": [
                {"id": "game-a", "date": "2026-10-20T23:00:00Z"},
                {"id": "game-b", "date": "2026-10-21T01:00:00Z"},
                {"id": "game-c", "date": "2026-10-21T02:30:00Z"},
                {"id": "game-d", "date": "2026-10-21T03:30:00Z"},
            ]
        }

        merged = _merge_espn_scoreboard_payloads([payload_20, payload_21])
        oct20 = _filter_espn_events_for_date(merged, date(2026, 10, 20))
        oct21 = _filter_espn_events_for_date(merged, date(2026, 10, 21))

        self.assertEqual(
            [event["id"] for event in oct20["events"]],
            ["game-a", "game-b", "game-c"],
        )
        self.assertEqual(
            [event["id"] for event in oct21["events"]],
            ["game-d"],
        )

    def test_team_schedules_reconstruct_three_opening_night_games(self):
        frames = {
            1610612738: pd.DataFrame([
                {
                    "GAME_ID": "bos-det",
                    "GAME_DATE": "2026-10-20T19:00:00Z",
                    "TEAM_ID": 1610612738,
                    "OPPONENT_TEAM_ID": 1610612765,
                    "HOME_AWAY": "away",
                    "COMPLETED": False,
                }
            ]),
            1610612755: pd.DataFrame([
                {
                    "GAME_ID": "phi-nyk",
                    "GAME_DATE": "2026-10-20T23:00:00Z",
                    "TEAM_ID": 1610612755,
                    "OPPONENT_TEAM_ID": 1610612752,
                    "HOME_AWAY": "away",
                    "COMPLETED": False,
                }
            ]),
            1610612760: pd.DataFrame([
                {
                    "GAME_ID": "okc-sas",
                    "GAME_DATE": "2026-10-21T01:30:00Z",
                    "TEAM_ID": 1610612760,
                    "OPPONENT_TEAM_ID": 1610612759,
                    "HOME_AWAY": "away",
                    "COMPLETED": False,
                }
            ]),
            1610612765: pd.DataFrame([
                {
                    "GAME_ID": "bos-det",
                    "GAME_DATE": "2026-10-20T19:00:00Z",
                    "TEAM_ID": 1610612765,
                    "OPPONENT_TEAM_ID": 1610612738,
                    "HOME_AWAY": "home",
                    "COMPLETED": False,
                }
            ]),
        }

        result = _games_from_espn_team_schedule_frames(
            frames,
            date(2026, 10, 20),
        )

        self.assertEqual(len(result), 3)
        self.assertEqual(
            result["GAME_ID"].tolist(),
            ["bos-det", "phi-nyk", "okc-sas"],
        )
        self.assertEqual(
            result["GAME_TIME_BRT"].tolist(),
            ["16:00 BRT", "20:00 BRT", "22:30 BRT"],
        )

    def test_espn_team_aliases_map_to_official_nba_ids(self):
        aliases = {
            "NY": "NYK",
            "SA": "SAS",
            "NO": "NOP",
            "GS": "GSW",
            "WSH": "WAS",
            "UTAH": "UTA",
        }
        official_by_abbr = {
            str(team.get("abbreviation") or "").upper(): int(team_id)
            for team_id, team in TEAM_LOOKUP.items()
        }

        for espn_abbr, nba_abbr in aliases.items():
            team_id, _, returned_abbr = _nba_team_from_espn({
                "team": {
                    "abbreviation": espn_abbr,
                    "displayName": nba_abbr,
                }
            })
            self.assertEqual(team_id, official_by_abbr[nba_abbr])
            self.assertEqual(returned_abbr, espn_abbr)

    def test_opening_night_payload_keeps_ny_and_sa_alias_games(self):
        payload = {
            "events": [
                {
                    "id": "bos-det",
                    "date": "2026-10-20T19:00:00Z",
                    "competitions": [{"competitors": [
                        {"homeAway": "away", "team": {"abbreviation": "BOS", "displayName": "Boston Celtics"}},
                        {"homeAway": "home", "team": {"abbreviation": "DET", "displayName": "Detroit Pistons"}},
                    ]}],
                },
                {
                    "id": "phi-ny",
                    "date": "2026-10-20T23:00:00Z",
                    "competitions": [{"competitors": [
                        {"homeAway": "away", "team": {"abbreviation": "PHI", "displayName": "Philadelphia 76ers"}},
                        {"homeAway": "home", "team": {"abbreviation": "NY", "displayName": "New York Knicks"}},
                    ]}],
                },
                {
                    "id": "okc-sa",
                    "date": "2026-10-21T01:30:00Z",
                    "competitions": [{"competitors": [
                        {"homeAway": "away", "team": {"abbreviation": "OKC", "displayName": "Oklahoma City Thunder"}},
                        {"homeAway": "home", "team": {"abbreviation": "SA", "displayName": "San Antonio Spurs"}},
                    ]}],
                },
            ]
        }

        result = _games_from_espn_payload(payload)

        self.assertEqual(len(result), 3)
        self.assertEqual(
            set(result["GAME_ID"].tolist()),
            {"bos-det", "phi-ny", "okc-sa"},
        )


if __name__ == "__main__":
    unittest.main()

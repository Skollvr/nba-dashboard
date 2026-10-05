import unittest
from datetime import date

from api_nba import (
    _filter_espn_events_for_date,
    _games_from_espn_payload,
    _merge_espn_scoreboard_payloads,
)


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


if __name__ == "__main__":
    unittest.main()

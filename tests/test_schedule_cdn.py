import unittest
from datetime import date

from api_nba import _filter_espn_events_for_date, _games_from_espn_payload


class EspnScheduleParsingTests(unittest.TestCase):
    def test_builds_game_row_and_maps_team_abbreviations(self):
        payload = {
            "events": [
                {
                    "id": "401999999",
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

    def test_returns_empty_frame_for_empty_schedule(self):
        result = _games_from_espn_payload({"events": []})

        self.assertTrue(result.empty)
        self.assertIn("GAME_ID", result.columns)

    def test_monthly_payload_keeps_all_opening_night_games_on_eastern_date(self):
        payload = {
            "events": [
                {"id": "bos-det", "date": "2026-10-20T19:00:00Z"},
                {"id": "phi-nyk", "date": "2026-10-20T23:00:00Z"},
                # 9:30 PM ET is already Oct. 21 in UTC, but still belongs to Oct. 20 NBA schedule.
                {"id": "okc-sas", "date": "2026-10-21T01:30:00Z"},
                {"id": "atl-orl", "date": "2026-10-21T23:00:00Z"},
            ]
        }

        result = _filter_espn_events_for_date(payload, date(2026, 10, 20))

        self.assertEqual(
            [event["id"] for event in result["events"]],
            ["bos-det", "phi-nyk", "okc-sas"],
        )


if __name__ == "__main__":
    unittest.main()

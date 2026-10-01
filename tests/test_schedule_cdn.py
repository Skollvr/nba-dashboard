import unittest
from datetime import date

from api_nba import _games_from_schedule_payload


class ScheduleCdnParsingTests(unittest.TestCase):
    def test_filters_selected_date_and_builds_game_row(self):
        payload = {
            "leagueSchedule": {
                "gameDates": [
                    {
                        "gameDate": "10/20/2026 00:00:00",
                        "games": [
                            {
                                "gameId": "0022600001",
                                "gameStatusText": "7:30 pm ET",
                                "awayTeam": {
                                    "teamId": 1,
                                    "teamCity": "Away",
                                    "teamName": "Team",
                                    "teamTricode": "AWY",
                                },
                                "homeTeam": {
                                    "teamId": 2,
                                    "teamCity": "Home",
                                    "teamName": "Team",
                                    "teamTricode": "HME",
                                },
                            }
                        ],
                    },
                    {
                        "gameDate": "10/21/2026 00:00:00",
                        "games": [],
                    },
                ]
            }
        }

        result = _games_from_schedule_payload(payload, date(2026, 10, 20))

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["GAME_ID"], "0022600001")
        self.assertEqual(result.iloc[0]["VISITOR_TEAM_ABBR"], "AWY")
        self.assertEqual(result.iloc[0]["HOME_TEAM_ABBR"], "HME")
        self.assertIn("Away Team @ Home Team", result.iloc[0]["label"])

    def test_returns_empty_frame_when_selected_date_has_no_games(self):
        payload = {
            "leagueSchedule": {
                "gameDates": [
                    {
                        "gameDate": "10/20/2026 00:00:00",
                        "games": [],
                    }
                ]
            }
        }

        result = _games_from_schedule_payload(payload, date(2026, 10, 21))

        self.assertTrue(result.empty)
        self.assertIn("GAME_ID", result.columns)


if __name__ == "__main__":
    unittest.main()

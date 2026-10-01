import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from api_nba import get_games_for_date


class ScheduleFailureTests(unittest.TestCase):
    def setUp(self):
        get_games_for_date.clear()

    @patch("api_nba.fetch_espn_games_for_date")
    @patch("api_nba.fetch_nba_scoreboard_v2_once")
    def test_espn_is_used_when_nba_fails(self, mock_nba, mock_espn):
        mock_nba.side_effect = RuntimeError("NBA unavailable")
        mock_espn.return_value = {"events": []}

        result = get_games_for_date(date(2026, 10, 20))

        self.assertTrue(result.empty)
        mock_espn.assert_called_once()

    @patch("api_nba.fetch_espn_games_for_date")
    @patch("api_nba.fetch_nba_scoreboard_v2_once")
    def test_espn_is_not_called_when_nba_returns_games(self, mock_nba, mock_espn):
        mock_nba.return_value = pd.DataFrame(
            [
                {
                    "GAME_ID": "1",
                    "HOME_TEAM_ID": 2,
                    "VISITOR_TEAM_ID": 1,
                    "GAME_STATUS_TEXT": "7:30 PM ET",
                    "HOME_TEAM_ABBR": "NYK",
                    "VISITOR_TEAM_ABBR": "BOS",
                    "home_team_name": "New York Knicks",
                    "away_team_name": "Boston Celtics",
                    "label": "Boston Celtics @ New York Knicks • 7:30 PM ET",
                }
            ]
        )

        result = get_games_for_date(date(2026, 10, 20))

        self.assertEqual(len(result), 1)
        mock_espn.assert_not_called()

    @patch("api_nba.fetch_espn_games_for_date")
    @patch("api_nba.fetch_nba_scoreboard_v2_once")
    def test_both_sources_failing_raises(self, mock_nba, mock_espn):
        mock_nba.side_effect = RuntimeError("NBA unavailable")
        mock_espn.side_effect = RuntimeError("ESPN unavailable")

        with self.assertRaises(RuntimeError):
            get_games_for_date(date(2026, 10, 20))


if __name__ == "__main__":
    unittest.main()

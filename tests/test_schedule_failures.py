import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from api_nba import get_games_for_date


def _game_row(game_id, away_id, home_id, away_name, home_name, away_abbr, home_abbr, time_brt):
    return {
        "GAME_ID": game_id,
        "HOME_TEAM_ID": home_id,
        "VISITOR_TEAM_ID": away_id,
        "GAME_STATUS_TEXT": "Agendado",
        "GAME_DATETIME_BRT": f"2026-10-20T{time_brt}:00-03:00",
        "GAME_DATE_BRT": "20/10/2026",
        "GAME_TIME_BRT": f"{time_brt} BRT",
        "HOME_TEAM_ABBR": home_abbr,
        "VISITOR_TEAM_ABBR": away_abbr,
        "home_team_name": home_name,
        "away_team_name": away_name,
        "label": f"{away_name} @ {home_name} • {time_brt} BRT",
    }


class ScheduleFailureTests(unittest.TestCase):
    def setUp(self):
        get_games_for_date.clear()

    @patch("api_nba.fetch_nba_scoreboard_v2_once")
    @patch("api_nba.fetch_nba_cdn_schedule")
    @patch("api_nba.fetch_espn_league_schedule_for_date")
    @patch("api_nba.fetch_espn_games_for_date")
    def test_partial_scoreboard_is_completed_by_team_schedules(
        self,
        mock_scoreboard,
        mock_team_schedules,
        mock_cdn,
        mock_nba,
    ):
        mock_scoreboard.return_value = {
            "events": [
                {
                    "id": "bos-det",
                    "date": "2026-10-20T19:00:00Z",
                    "competitions": [{"competitors": [
                        {"homeAway": "away", "team": {"abbreviation": "BOS", "displayName": "Boston Celtics"}},
                        {"homeAway": "home", "team": {"abbreviation": "DET", "displayName": "Detroit Pistons"}},
                    ]}],
                }
            ]
        }
        mock_team_schedules.return_value = pd.DataFrame([
            _game_row("bos-det", 1610612738, 1610612765, "Boston Celtics", "Detroit Pistons", "BOS", "DET", "16:00"),
            _game_row("phi-nyk", 1610612755, 1610612752, "Philadelphia 76ers", "New York Knicks", "PHI", "NYK", "20:00"),
            _game_row("okc-sas", 1610612760, 1610612759, "Oklahoma City Thunder", "San Antonio Spurs", "OKC", "SAS", "22:30"),
        ])

        result = get_games_for_date(date(2026, 10, 20))

        self.assertEqual(len(result), 3)
        self.assertEqual(
            set(result["GAME_ID"].tolist()),
            {"bos-det", "phi-nyk", "okc-sas"},
        )
        mock_cdn.assert_not_called()
        mock_nba.assert_not_called()

    @patch("api_nba.fetch_nba_scoreboard_v2_once")
    @patch("api_nba.fetch_nba_cdn_schedule")
    @patch("api_nba.fetch_espn_league_schedule_for_date")
    @patch("api_nba.fetch_espn_games_for_date")
    def test_team_schedule_can_supply_games_when_scoreboard_fails(
        self,
        mock_scoreboard,
        mock_team_schedules,
        mock_cdn,
        mock_nba,
    ):
        mock_scoreboard.side_effect = RuntimeError("ESPN scoreboard unavailable")
        mock_team_schedules.return_value = pd.DataFrame([
            _game_row("phi-nyk", 1610612755, 1610612752, "Philadelphia 76ers", "New York Knicks", "PHI", "NYK", "20:00"),
        ])

        result = get_games_for_date(date(2026, 10, 20))

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["GAME_ID"], "phi-nyk")
        mock_cdn.assert_not_called()
        mock_nba.assert_not_called()

    @patch("api_nba.fetch_nba_scoreboard_v2_once")
    @patch("api_nba.fetch_nba_cdn_schedule")
    @patch("api_nba.fetch_espn_league_schedule_for_date")
    @patch("api_nba.fetch_espn_games_for_date")
    def test_all_sources_failing_raises(
        self,
        mock_scoreboard,
        mock_team_schedules,
        mock_cdn,
        mock_nba,
    ):
        mock_scoreboard.side_effect = RuntimeError("ESPN scoreboard unavailable")
        mock_team_schedules.side_effect = RuntimeError("ESPN team schedules unavailable")
        mock_cdn.side_effect = RuntimeError("NBA CDN unavailable")
        mock_nba.side_effect = RuntimeError("NBA stats unavailable")

        with self.assertRaises(RuntimeError):
            get_games_for_date(date(2026, 10, 20))


if __name__ == "__main__":
    unittest.main()

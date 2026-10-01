import unittest
from datetime import date
from unittest.mock import patch

from api_nba import get_games_for_date


class ScheduleFailureTests(unittest.TestCase):
    def setUp(self):
        get_games_for_date.clear()

    @patch("api_nba.run_api_call_with_retry")
    def test_both_scoreboards_failing_raises_instead_of_returning_empty(self, mock_retry):
        mock_retry.side_effect = [
            RuntimeError("ScoreboardV2 unavailable"),
            RuntimeError("ScoreboardV3 unavailable"),
        ]

        with self.assertRaises(RuntimeError):
            get_games_for_date(date(2026, 10, 1))


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

import pandas as pd

from processamento import get_position_opponent_profile_v2


class MatchupProfileRegressionTests(unittest.TestCase):
    def setUp(self):
        get_position_opponent_profile_v2.clear()

    @patch("processamento.get_league_position_baseline")
    @patch("processamento.get_position_allowed_profile")
    def test_matchup_profile_uses_returned_dataframes(self, mock_opp, mock_league):
        mock_opp.return_value = pd.DataFrame(
            [{
                "GP": 10,
                "PTS": 25.0,
                "REB": 8.0,
                "AST": 6.0,
                "FG3M": 3.0,
                "FGA": 18.0,
                "FG3A": 8.0,
            }]
        )
        mock_league.return_value = pd.DataFrame(
            [{
                "GP": 10,
                "PTS": 22.0,
                "REB": 7.0,
                "AST": 5.0,
                "FG3M": 2.5,
                "FGA": 16.0,
                "FG3A": 7.0,
            }]
        )

        result = get_position_opponent_profile_v2(
            season="2025-26",
            opponent_team_id=1,
            position_group="G",
            season_scope="Regular Season",
        )

        self.assertEqual(result["OPP_PTS_ALLOWED"], 25.0)
        self.assertEqual(result["LEAGUE_PTS_BASELINE"], 22.0)
        self.assertEqual(result["MATCHUP_DIFF_PTS"], 3.0)
        self.assertEqual(result["MATCHUP_LABEL_PTS"], "Favorável")


if __name__ == "__main__":
    unittest.main()

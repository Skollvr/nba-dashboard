from unittest.mock import patch

from matchup_cache import cached_matchup_context, clear_cached_matchup


def test_matchup_cache_can_be_explicitly_invalidated():
    clear_cached_matchup()
    kwargs=dict(
        away_team_id=1,home_team_id=2,
        away_team_name="A",home_team_name="B",season="2026-27",
        include_market=False,
    )
    with patch("matchup_cache.get_matchup_context", return_value=("away","home")) as fn:
        assert cached_matchup_context(**kwargs)==("away","home")
        assert cached_matchup_context(**kwargs)==("away","home")
        assert fn.call_count==1
        clear_cached_matchup()
        assert cached_matchup_context(**kwargs)==("away","home")
        assert fn.call_count==2

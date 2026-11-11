"""Regression tests for Streamlit CacheReplayClosureError on team switches."""
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

import matchup_cache
from matchup_cache import cached_matchup_context, clear_cached_matchup


@pytest.fixture
def isolated_state(monkeypatch):
    state = {}
    monkeypatch.setattr(matchup_cache.st, "session_state", state)
    return state


def _kwargs(**extra):
    args = dict(
        away_team_id=1, home_team_id=2,
        away_team_name="A", home_team_name="B",
        season="2026-27", include_market=False,
        target_date=date(2026, 11, 9),
    )
    args.update(extra)
    return args


def test_matchup_cache_reuses_data_and_can_be_explicitly_invalidated(isolated_state):
    away = pd.DataFrame({"PROJ_AST_V1": [4.8]})
    home = pd.DataFrame({"PROJ_AST_V1": [6.2]})
    with patch("matchup_cache.get_matchup_context", return_value=(away, home)) as fn:
        left, right = cached_matchup_context(**_kwargs())
        left.loc[0, "PROJ_AST_V1"] = 100
        updated_left, updated_right = cached_matchup_context(**_kwargs())
        assert updated_left.loc[0, "PROJ_AST_V1"] == pytest.approx(4.8)
        assert updated_right.loc[0, "PROJ_AST_V1"] == pytest.approx(6.2)
        assert fn.call_count == 1
        clear_cached_matchup()
        cached_matchup_context(**_kwargs())
        assert fn.call_count == 2


def test_market_date_or_team_changes_do_not_reuse_old_data(isolated_state):
    result = (pd.DataFrame({"PTS": [7]}), pd.DataFrame({"PTS": [9]}))
    with patch("matchup_cache.get_matchup_context", return_value=result) as fn:
        cached_matchup_context(**_kwargs())
        cached_matchup_context(**_kwargs(include_market=True))
        cached_matchup_context(**_kwargs(home_team_id=3))
        cached_matchup_context(**_kwargs(target_date=date(2026, 11, 10)))
        assert fn.call_count == 4


def test_cached_matchup_reuses_no_old_progress_callback(isolated_state):
    result = (pd.DataFrame({"A": [1]}), pd.DataFrame({"B": [2]}))
    prior_callback_calls = []
    current_callback_calls = []

    def fetch(*args, **kwargs):
        kwargs["progress_callback"]("ESPN loaded")
        return result

    with patch("matchup_cache.get_matchup_context", side_effect=fetch) as fn:
        cached_matchup_context(**_kwargs(), _progress_callback=prior_callback_calls.append)
        cached_matchup_context(**_kwargs(), _progress_callback=current_callback_calls.append)
        assert fn.call_count == 1
        assert prior_callback_calls == ["ESPN loaded"]
        assert current_callback_calls == []


def test_switch_team_inside_streamlit_status_does_not_replay_old_layout():
    """Reproduce the exact progress-container rerun from the user's error."""
    from streamlit.testing.v1 import AppTest

    script = """
import pandas as pd
import streamlit as st
from matchup_cache import cached_matchup_context
if "team" not in st.session_state:
    st.session_state.team = "Away"
with st.status("Carregando dados do confronto...", expanded=True) as status:
    away, home = cached_matchup_context(
        1, 2, "Away", "Home", "2026-27", False,
        _progress_callback=status.write,
    )
    status.update(label="Confronto carregado", state="complete", expanded=False)
st.write("Time selecionado:", st.session_state.team)
if st.button("Trocar time"):
    st.session_state.team = "Home" if st.session_state.team == "Away" else "Away"
"""

    def load(*args, **kwargs):
        kwargs["progress_callback"]("ESPN carregada")
        return (pd.DataFrame({"PTS": [5]}), pd.DataFrame({"PTS": [4]}))

    with patch("matchup_cache.get_matchup_context", side_effect=load) as fn:
        at = AppTest.from_string(script).run()
        assert not at.exception
        at.button[0].click().run()
        assert not at.exception
        at.run()
        assert not at.exception
        assert fn.call_count == 1

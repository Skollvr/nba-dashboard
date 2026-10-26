from contextlib import nullcontext
from unittest.mock import patch

import numpy as np
import pandas as pd

from raiox_refresh import observed_log, render_raiox_refresh


def _log():
    return pd.DataFrame({
        "GAME_DATE": [
            "2026-10-21T00:00:00Z",
            "2026-10-23T00:00:00Z",
            "2026-10-26T00:00:00Z",
        ],
        "PTS": [20, 24, 99],
        "REB": [5, 6, 30],
        "AST": [3, 4, 40],
        "MIN": [29, 31, 45],
        "FG3M": [2, 3, np.nan],
        "FGA": [15, 18, np.nan],
        "FG3A": [6, 9, np.nan],
    })


def _row():
    return pd.Series({
        "PLAYER": "Jogador Teste", "PLAYER_ID": 12,
        "SEASON_GP": 2,
        "SEASON_PTS": 22, "L10_PTS": 22, "L5_PTS": 22,
        "SEASON_REB": 5.5, "SEASON_AST": 3.5,
        "SEASON_PRA": 31, "L10_PRA": 31, "L5_PRA": 31,
    })


def test_raiox_does_not_leak_selected_or_future_results():
    actual = observed_log(_log(), "2026-10-23")
    assert actual["PTS"].tolist() == [20]
    assert len(observed_log(_log(), "2026-10-27")) == 3
    assert actual["PRA"].tolist() == [28]


def test_raiox_never_coerces_missing_shot_attempts_to_zero():
    actual = observed_log(_log())
    assert np.isnan(actual.loc[2, "FGA"])
    assert np.isnan(actual.loc[2, "FG3A"])


def test_raiox_mobile_friendly_charts_use_actual_observed_samples():
    plots = []
    with (
        patch("raiox_refresh.get_player_log", return_value=_log()),
        patch("raiox_refresh.st.markdown"),
        patch("raiox_refresh.st.caption"),
        patch("raiox_refresh.st.info"),
        patch("raiox_refresh.st.columns", return_value=(nullcontext(), nullcontext())),
        patch("raiox_refresh.st.plotly_chart",
              side_effect=lambda fig, **kwargs: plots.append((fig, kwargs))),
    ):
        render_raiox_refresh(
            _row(), "PTS", "2026-27", "Regular Season",
            "Compacto", {"has_active_line": True, "line_value": 20.5},
            target_date="2026-10-24",
        )
    assert len(plots) == 4
    for _, opts in plots:
        assert opts["config"]["displayModeBar"] is False
    recent = plots[0][0]
    assert list(recent.data[0].y) == [20, 24]
    assert recent.layout.yaxis.range[1] >= 30
    donut = plots[2][0]
    assert list(donut.data[0].values) == [1, 1, 0]


def test_raiox_is_only_active_in_renovado():
    import inspect
    from ui_components import render_player_focus_panel

    code = inspect.getsource(render_player_focus_panel)
    segment = code.split("    with visual_tab:", 1)[1].split(
        "    with market_tab:", 1
    )[0]
    assert "if modern_focus:" in segment
    assert "render_raiox_refresh(" in segment
    assert "else:" in segment
    assert "render_player_chart(" in segment  # old classic retained

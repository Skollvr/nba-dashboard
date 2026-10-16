import pandas as pd
import pytest

from projection_validation import _project_core, rolling_projection_backtest
from processamento import get_line_context


def _logs(n=12):
    return pd.DataFrame([{
        "PLAYER_ID": 123,
        "GAME_ID": str(i),
        "GAME_DATE": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i),
        "MIN": 30.0,
        "PTS": 20.0,
        "REB": 8.0,
        "AST": 5.0,
        "FG3M": 2.0,
        "FGA": 15.0,
        "FG3A": 6.0,
    } for i in range(n)])


def test_walk_forward_no_lookahead_on_constant_logs():
    report = rolling_projection_backtest(_logs())
    pts = report[report["Métrica"] == "PTS"].iloc[0]
    pra = report[report["Métrica"] == "PRA"].iloc[0]
    assert pts["Jogos avaliados"] == 2
    assert pts["MAE modelo"] == pytest.approx(0.0)
    assert pra["MAE modelo"] == pytest.approx(0.0)
    assert pts["Viés (proj − real)"] == pytest.approx(0.0)


def test_compound_pra_projection_equals_components_before_rounding():
    predicted = _project_core(_logs(10))
    assert predicted["PRA"] == pytest.approx(
        predicted["PTS"] + predicted["REB"] + predicted["AST"]
    )
    assert predicted["FGA"] >= predicted["3PA"] >= predicted["3PM"]


def test_future_outlier_changes_error_not_prediction_of_earlier_games():
    base = _logs(11)
    high = base.copy()
    high.loc[10, "PTS"] = 100.0
    high.loc[10, "REB"] = 10.0
    low = base.copy()
    low.loc[10, "PTS"] = 30.0
    low.loc[10, "REB"] = 10.0
    high_report = rolling_projection_backtest(high)
    low_report = rolling_projection_backtest(low)
    pts_high = high_report[high_report["Métrica"] == "PTS"].iloc[0]
    pts_low = low_report[low_report["Métrica"] == "PTS"].iloc[0]
    assert pts_high["Jogos avaliados"] == pts_low["Jogos avaliados"] == 1
    assert pts_high["MAE modelo"] - pts_low["MAE modelo"] == pytest.approx(70.0)


def test_missing_shooting_samples_are_excluded_not_treated_as_zeros():
    logs = _logs()
    logs["FGA"] = float("nan")
    result = rolling_projection_backtest(logs)
    assert "FGA" not in result["Métrica"].tolist()
    assert "PTS" in result["Métrica"].tolist()


def test_invalid_projection_does_not_create_market_line_edge():
    row = pd.Series({
        "PROJ_FGA": float("nan"),
        "PROJ_FGA_V1": float("nan"),
        "MANUAL_LINE_FGA": 12.5,
    })
    result = get_line_context(row, "FGA", 12.5)
    assert not result["has_active_line"]
    assert pd.isna(result["projection"])

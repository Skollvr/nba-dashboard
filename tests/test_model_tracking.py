from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pandas as pd

from model_tracking import (
    append_unique_jsonl, load_jsonl, snapshot_game, result_game, tracking_frame,
)
from model_tracking_ui import model_performance_metrics


def _sample_game():
    now = datetime(2026, 10, 26, 20, 0, tzinfo=timezone.utc)
    return now, pd.Series({
        "GAME_ID": "game123",
        "GAME_DATETIME_BRT": (now + timedelta(hours=2)).isoformat(),
        "GAME_DATE_BRT": "2026-10-26",
    })


def _sample_team():
    return pd.DataFrame([{
        "PLAYER_ID": 11, "TEAM_ID": 1610612738, "PLAYER": "Player X",
        "SEASON_GP": 3, "PROJ_MIN_V1": 32.1,
        "PROJ_PTS_V1": 21.7, "PROJ_REB_V1": 6.3,
        "PROJ_FGA_V1": float("nan"),
        "MARKET_PTS_LINE": 20.5,
        "MARKET_PTS_SOURCE": "BetMGM",
        "INJ_STATUS": "Available", "IS_UNAVAILABLE": False,
    }])


def test_snapshot_freezes_only_usable_pregame_metrics():
    now, game = _sample_game()
    rows = snapshot_game(game, _sample_team(), pd.DataFrame(), now)
    assert len(rows) == 2
    assert {r["metric"] for r in rows} == {"PTS", "REB"}
    assert {r["version"] for r in rows} == {"Matchup V4"}
    assert rows[0]["captured_utc"] == now.isoformat()
    pts = next(row for row in rows if row["metric"] == "PTS")
    assert pts["line"] == 20.5
    assert pts["projection"] == 21.7
    assert pts["season_gp"] == 3
    assert pts["key"] == "game123:11:PTS"
    assert snapshot_game(game, _sample_team(), pd.DataFrame(),
                         now + timedelta(hours=3)) == []
    assert snapshot_game(game, _sample_team(), pd.DataFrame(),
                         now - timedelta(hours=7)) == []


def test_snapshot_never_labels_missing_sample_as_prediction():
    now, game = _sample_game()
    team = _sample_team()
    team.loc[0, "SEASON_GP"] = 0
    assert snapshot_game(game, team, pd.DataFrame(), now) == []


def test_jsonl_append_immutable_and_idempotent(tmp_path):
    target = tmp_path / "data" / "history.jsonl"
    assert append_unique_jsonl(target, [{"key": "one", "value": 3}]) == 1
    assert append_unique_jsonl(target, [{"key": "one", "value": 500}]) == 0
    assert append_unique_jsonl(target, [
        {"key": "two", "value": 1}, {"key": "two", "value": 50},
    ]) == 1
    assert load_jsonl(target) == [
        {"key": "one", "value": 3}, {"key": "two", "value": 1},
    ]


def test_settlement_requires_completed_espn_boxscore_and_skip_dnp():
    now, game = _sample_game()
    team = _sample_team()
    records = snapshot_game(game, team, pd.DataFrame(), now)
    open_game = {"header": {"competitions": [
        {"status": {"type": {"completed": False}}}
    ]}}
    finished = {"header": {"competitions": [
        {"status": {"type": {"completed": True}}}
    ]}}
    assert result_game(records, open_game) == []
    with patch("api_espn._extract_summary_rows", return_value=[{
        "PLAYER_ID": 11, "TEAM_ID": 1610612738,
        "PTS": 24.0, "REB": 5.0, "AST": 4.0,
    }]):
        results = result_game(records, finished)
    assert len(results) == 2
    pts = next(item for item in results if item["key"].endswith(":PTS"))
    assert pts["actual"] == 24.0
    assert pts["absolute_error"] == 2.3
    assert pts["market_outcome"] == "Over"
    assert pts["direction_hit"] is True
    assert results[0]["settled_utc"]


def test_performance_metrics_are_computed_only_from_observed_outcomes():
    now, game = _sample_game()
    snapshots = snapshot_game(game, _sample_team(), pd.DataFrame(), now)
    frame = tracking_frame(snapshots, [])
    assert model_performance_metrics(frame)["count"] == 0
    results = [
        {"key": "game123:11:PTS", "actual": 24., "error": -2.3,
         "absolute_error": 2.3, "direction_hit": True}
    ]
    frame = tracking_frame(snapshots, results)
    result = model_performance_metrics(frame)
    assert result["count"] == 1
    assert result["games"] == 1
    assert result["mae"] == 2.3
    assert result["direction_n"] == 1
    assert result["direction_rate"] == 1.0


def test_dashboard_without_snapshots_does_not_render_made_up_metrics():
    from model_tracking_ui import render_model_performance
    with (
        patch("model_tracking_ui.load_jsonl", return_value=[]),
        patch("model_tracking_ui.st.markdown"),
        patch("model_tracking_ui.st.caption"),
        patch("model_tracking_ui.st.info") as notice,
    ):
        render_model_performance()
    assert "Nenhuma projeção pré-jogo registrada" in notice.call_args.args[0]

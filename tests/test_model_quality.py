import pandas as pd
import pytest

from model_quality import coverage, performance_buckets, model_comparison, minutes_comparison, market_performance


def test_data_coverage_separates_actual_lines_from_missing():
    snap = [{"key":"a","game_id":"g","line": 3.5,"line_source":"BetMGM","projection_v41":4.5},
            {"key":"b","game_id":"g","line": None,"line_source":"","projection_v41":None}]
    result = [{"key":"a","actual_minutes":31},{"key":"missing","actual_minutes":19}]
    d=coverage(snap,result)
    assert d["snapshots"]==2 and d["market_quoted"]==1
    assert d["results"]==1 and d["pending"]==1


def test_paired_shadow_is_forward_only_and_preserves_absence():
    rows=pd.DataFrame([
        {"metric":"AST","projection":4.7,"projection_v41":4.1,"actual":5},
        {"metric":"AST","projection":4.2,"projection_v41":float("nan"),"actual":4},
    ])
    result=model_comparison(rows)
    assert result.iloc[0]["N"]==1
    assert result.iloc[0]["MAE V4"]==pytest.approx(.3)
    assert result.iloc[0]["MAE V4.1"]==pytest.approx(.9)


def test_market_performance_never_counts_missing_odds_or_push():
    df=pd.DataFrame([
        {"line": 4.5,"line_source":"BetMGM","projection":5.1,"actual":5,"over_decimal":1.9,"under_decimal":1.8},
        {"line": 4.5,"line_source":"FanDuel","projection":4.0,"actual":7,"over_decimal":2.1,"under_decimal":2.0},
        {"line": 4.5,"line_source":"","projection":4.0,"actual":3,"over_decimal":1.8,"under_decimal":1.8},
    ])
    m=market_performance(df)
    assert m["n"]==2 and m["correct"]==1
    assert m["yield_n"]==2 and m["yield_unit"]==pytest.approx(-.05)


def test_pregame_buckets_use_ceil_and_do_not_invent_missing():
    frame=pd.DataFrame([
        {"metric":"AST","projection":4.8,"actual":4},
        {"metric":"AST","projection":4.8,"actual":5},
        {"metric":"AST","projection":3.1,"actual":None},
    ])
    r=performance_buckets(frame,"AST")
    assert r.iloc[0]["N"]==2 and r.iloc[0]["Metas"]==50.0


def test_minutes_error_not_multiplied_by_seven_metrics():
    data=pd.DataFrame([
        {"game_id":"a","player_id":1,"minutes":29,"actual_minutes":24},
        {"game_id":"a","player_id":1,"minutes":29,"actual_minutes":24},
    ])
    result=minutes_comparison(data)
    assert result["N"]==1 and result["MAE minutos"]==5


def test_frozen_baselines_compare_same_forward_games_only():
    from model_quality import baseline_comparison
    data=pd.DataFrame([
        {"metric":"3PA","projection":5.3,"actual":5,"l10_avg":6,"l5_avg":7,"season_avg":5.7},
        {"metric":"3PA","projection":5.3,"actual":6,"l10_avg":None,"l5_avg":None,"season_avg":5.5},
    ])
    table=baseline_comparison(data)
    l10=table[table["Referência"]=="L10"].iloc[0]
    assert l10["N"]==1 and l10["MAE V4"]==pytest.approx(.3)
    assert l10["MAE referência"]==pytest.approx(1.0)
    assert l10["Ganho V4"]==pytest.approx(.7)
    season=table[table["Referência"]=="Temporada"].iloc[0]
    assert season["N"]==2

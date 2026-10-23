"""Regression coverage for user-chosen pregame minutes."""
import numpy as np
import pandas as pd
import pytest

from assisted_minutes import apply_manual_minutes


def player_frame():
    return pd.DataFrame([
        dict(
            PLAYER_ID=7, PLAYER="Primeiro", SEASON_GP=20, PROJ_MIN_V1=30.0,
            INJ_STATUS="Available", IS_UNAVAILABLE=False,
            MINUTES_SOURCE="Modelo interno",
            BASE_PTS_V1=20., PROJ_PTS_V1=21.,
            BASE_REB_V1=7., PROJ_REB_V1=7.,
            BASE_AST_V1=5., PROJ_AST_V1=5.,
            BASE_PRA_V1=32., PROJ_PRA_V1=33.,
            BASE_3PM_V1=2., PROJ_3PM_V1=2.,
            BASE_3PA_V1=6., PROJ_3PA_V1=6.,
            BASE_2PA_V1=8., PROJ_2PA_V1=8.,
            BASE_FGA_V1=14., PROJ_FGA_V1=14.,
            MATCHUP_EFFECT_PCT_PTS_V2=.04
        ),
        dict(
            PLAYER_ID=8, PLAYER="Segundo", SEASON_GP=20, PROJ_MIN_V1=30.0,
            INJ_STATUS="Available", IS_UNAVAILABLE=False,
            MINUTES_SOURCE="Modelo interno",
            BASE_PTS_V1=10., PROJ_PTS_V1=11.,
            BASE_REB_V1=4., PROJ_REB_V1=4.,
            BASE_AST_V1=2., PROJ_AST_V1=2.,
            BASE_PRA_V1=16., PROJ_PRA_V1=17.,
            BASE_3PM_V1=1., PROJ_3PM_V1=1.,
            BASE_3PA_V1=3., PROJ_3PA_V1=3.,
            BASE_2PA_V1=4., PROJ_2PA_V1=4.,
            BASE_FGA_V1=7., PROJ_FGA_V1=7.,
            MATCHUP_EFFECT_PCT_PTS_V2=0.
        ),
    ])


def test_manual_minutes_recompute_v4_preserve_original_and_other_players():
    original = player_frame()
    changed = apply_manual_minutes(original, {7: 36})
    p = changed.iloc[0]
    assert p.PROJ_MIN_V1 == 36
    assert p.PROJ_MIN_AUTO == 30
    assert p.PROJ_PTS_V1 == pytest.approx(21 * 1.2)
    assert p.PROJ_PRA_V1 == pytest.approx(
        p.PROJ_PTS_V1 + p.PROJ_REB_V1 + p.PROJ_AST_V1)
    assert p.PROJ_FGA_V1 == pytest.approx(p.PROJ_2PA_V1 + p.PROJ_3PA_V1)
    assert p.PROJ_FGA_V1 >= p.PROJ_3PA_V1 >= p.PROJ_3PM_V1
    assert p.MATCHUP_EFFECT_PCT_PTS_V2 == .04
    assert p.PROJ_PTS_V1_AUTO == 21
    assert p.MINUTES_SOURCE_AUTO == "Modelo interno"
    assert p.MINUTES_SOURCE == "Ajuste manual pré-jogo"
    assert p.MANUAL_MINUTES_ACTIVE
    assert changed.iloc[1].PROJ_MIN_V1 == 30
    assert not changed.iloc[1].MANUAL_MINUTES_ACTIVE
    assert original.iloc[0].PROJ_MIN_V1 == 30


def test_reverting_to_no_overrides_restores_automatic():
    original = player_frame()
    edited = apply_manual_minutes(original, {7: 35})
    restored = apply_manual_minutes(original, {})
    assert edited.iloc[0].PROJ_PTS_V1 != restored.iloc[0].PROJ_PTS_V1
    assert restored.iloc[0].PROJ_PTS_V1 == 21
    assert not restored.iloc[0].MANUAL_MINUTES_ACTIVE


@pytest.mark.parametrize("minutes", [0, -1, 49, np.nan, np.inf, "-"])
def test_bad_manual_values_are_ignored(minutes):
    result = apply_manual_minutes(player_frame(), {7: minutes})
    assert result.iloc[0].PROJ_MIN_V1 == 30
    assert not result.iloc[0].MANUAL_MINUTES_ACTIVE


@pytest.mark.parametrize("status", ["Out", "Doubtful"])
def test_inactive_players_not_adjusted(status):
    original = player_frame()
    original.loc[0, "INJ_STATUS"] = status
    result = apply_manual_minutes(original, {7: 35})
    assert not result.iloc[0].MANUAL_MINUTES_ACTIVE


def test_no_history_and_unknown_minutes_are_not_invented():
    original = player_frame()
    original.loc[0, "SEASON_GP"] = 0
    original.loc[0, "PROJ_MIN_V1"] = np.nan
    original.loc[0, "PROJ_PTS_V1"] = np.nan
    result = apply_manual_minutes(original, {7: 34})
    assert pd.isna(result.iloc[0].PROJ_PTS_V1)
    assert not result.iloc[0].MANUAL_MINUTES_ACTIVE


def test_invalid_fga_stays_missing_and_no_fake_edge_created():
    original = player_frame()
    original.loc[0, "PROJ_FGA_V1"] = np.nan
    changed = apply_manual_minutes(original, {7: 34})
    assert pd.isna(changed.iloc[0].PROJ_FGA_V1)

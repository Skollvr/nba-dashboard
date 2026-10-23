import numpy as np
import pandas as pd
import pytest
from assisted_minutes import apply_manual_minutes


def sample():
    return pd.DataFrame([dict(
        PLAYER_ID=7, SEASON_GP=20, PROJ_MIN_V1=30.0,
        INJ_STATUS="Available", IS_UNAVAILABLE=False,
        MINUTES_SOURCE="Modelo interno",
        BASE_PTS_V1=20., PROJ_PTS_V1=21.,
        BASE_REB_V1=7., PROJ_REB_V1=7.,
        BASE_AST_V1=5., PROJ_AST_V1=5.,
        BASE_PRA_V1=32., PROJ_PRA_V1=33.,
        BASE_3PM_V1=2., PROJ_3PM_V1=2.,
        BASE_3PA_V1=6., PROJ_3PA_V1=6.,
        BASE_2PA_V1=9., PROJ_2PA_V1=9.,
        BASE_FGA_V1=15., PROJ_FGA_V1=15.,
        MATCHUP_EFFECT_PCT_PTS_V2=.04
    )])


def test_adjustment_and_restore():
    original = sample()
    updated = apply_manual_minutes(original, {7: 36})
    assert updated.loc[0, "PROJ_PTS_V1"] == pytest.approx(25.2)
    assert updated.loc[0, "PROJ_MIN_AUTO"] == 30
    assert updated.loc[0, "PROJ_PRA_V1"] == pytest.approx(39.6)
    assert updated.loc[0, "PROJ_FGA_V1"] >= updated.loc[0, "PROJ_3PA_V1"] >= updated.loc[0, "PROJ_3PM_V1"]
    assert updated.loc[0, "MATCHUP_EFFECT_PCT_PTS_V2"] == .04
    assert bool(updated.loc[0, "MANUAL_MINUTES_ACTIVE"])
    assert original.loc[0, "PROJ_MIN_V1"] == 30
    restored = apply_manual_minutes(original, {})
    assert restored.loc[0, "PROJ_PTS_V1"] == 21


@pytest.mark.parametrize("minutes", [0, -1, 49, np.nan, np.inf])
def test_invalid_minute_input(minutes):
    assert not bool(apply_manual_minutes(sample(), {7: minutes}).loc[0, "MANUAL_MINUTES_ACTIVE"])


@pytest.mark.parametrize("status", ["Out", "Doubtful"])
def test_injured_player_rejected(status):
    data = sample()
    data.loc[0, "INJ_STATUS"] = status
    assert not bool(apply_manual_minutes(data, {7: 35}).loc[0, "MANUAL_MINUTES_ACTIVE"])


def test_missing_shooting_data_preserved():
    data = sample()
    data.loc[0, "PROJ_FGA_V1"] = np.nan
    result = apply_manual_minutes(data, {7: 35})
    assert np.isnan(result.loc[0, "PROJ_FGA_V1"])

import pytest

from processamento import classify_percentile_matchup_score, combine_matchup_score


def test_missing_h2h_does_not_renormalize_defense_to_full_strength():
    score = combine_matchup_score(
        team_score=-0.8,
        position_score=-0.8,
        h2h_score=0.0,
        h2h_reliability=0.0,
    )
    assert score == pytest.approx(-0.56)
    assert classify_percentile_matchup_score(score) == "Difícil"


def test_midrange_matchup_stays_neutral():
    score = combine_matchup_score(
        team_score=-0.3,
        position_score=-0.3,
        h2h_score=0.0,
        h2h_reliability=0.0,
    )
    assert score == pytest.approx(-0.21)
    assert classify_percentile_matchup_score(score) == "Neutro"


def test_very_difficult_requires_extreme_alignment():
    score = combine_matchup_score(
        team_score=-1.0,
        position_score=-1.0,
        h2h_score=0.0,
        h2h_reliability=0.0,
    )
    assert score == pytest.approx(-0.70)
    assert classify_percentile_matchup_score(score) == "Muito difícil"


def test_one_game_favorable_h2h_only_softens_defense():
    score = combine_matchup_score(
        team_score=-0.8,
        position_score=-0.8,
        h2h_score=1.0,
        h2h_reliability=0.25,
    )
    assert score == pytest.approx(-0.485)
    assert classify_percentile_matchup_score(score) == "Difícil"


def test_strong_positive_alignment_can_be_very_favorable():
    score = combine_matchup_score(
        team_score=1.0,
        position_score=1.0,
        h2h_score=1.0,
        h2h_reliability=1.0,
    )
    assert score == pytest.approx(1.0)
    assert classify_percentile_matchup_score(score) == "Muito favorável"

"""Visual-only smoke tests for the reversible dashboard redesign."""
from unittest.mock import patch

import numpy as np
import pandas as pd

from ui_refresh import render_player_card_refresh, render_matchup_header_refresh


def _player(**kw):
    row = {
        "PLAYER": "Jayson Tatum",
        "PLAYER_ID": 4065648,
        "TEAM_ID": 1610612738,
        "TEAM_ABBR": "BOS",
        "TEAM_NAME": "Boston Celtics",
        "POSITION": "F",
        "SEASON_GP": 3,
        "PROJ_MIN_V1": 34.1,
        "MINUTES_SOURCE": "Modelo interno",
        "ROLE": "Titular projetado",
        "INJ_STATUS": "Available",
        "L10_PTS": 22.4,
        "L10_REB": 10.9,
        "L10_AST": 6.1,
        "MATCHUP_LABEL_PTS_V1": "Difícil",
    }
    row.update(kw)
    return pd.Series(row)


def _context(projection=22.0, active=False):
    return {
        "projection": projection,
        "has_active_line": active,
        "line_value": 20.5 if active else None,
        "edge": 1.5 if active else None,
    }


def test_modern_card_preserves_player_image_team_logo_and_core_metrics():
    html = []
    with (
        patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)),
        patch("ui_refresh.get_line_context", return_value=_context()),
    ):
        render_player_card_refresh(_player(), "PTS", None, False)

    assert len(html) == 1
    assert 'alt="Foto de Jayson Tatum"' in html[0]
    assert "a.espncdn.com/i/headshots/nba/players/full/4065648.png" in html[0]
    assert "cdn.nba.com/logos/nba/1610612738/" in html[0]
    assert "22.0" in html[0]
    assert "22.4" in html[0] and "10.9" in html[0] and "6.1" in html[0]
    assert "Matchup Difícil" in html[0]
    assert "34.1" in html[0]
    assert "3 jogos nesta temporada" in html[0]


def test_missing_stat_is_not_shown_as_fake_zero_and_injury_is_visible():
    html = []
    with (
        patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)),
        patch("ui_refresh.get_line_context", return_value=_context(np.nan)),
    ):
        render_player_card_refresh(
            _player(SEASON_GP=0, INJ_STATUS="Out"), "PTS", None, False
        )
    assert "Amostra insuficiente" in html[0]
    assert 'class="nova-pred-value">—</div>' in html[0]
    assert '<span class="nova-chip negative">Out</span>' in html[0]
    assert "Sem jogos nesta temporada" in html[0]
    assert '<div class="nova-stat-value">—</div>' in html[0]


def test_modern_card_displays_line_and_edge_without_recalculating_them():
    html = []
    with (
        patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)),
        patch("ui_refresh.get_line_context", return_value=_context(22.0, True)) as projection,
    ):
        render_player_card_refresh(_player(), "PTS", 20.5, False)
    assert projection.call_count == 1
    assert "Linha 20.5 · PTS" in html[0]
    assert "Edge +1.5" in html[0]


def test_player_text_is_html_escaped():
    html = []
    with (
        patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)),
        patch("ui_refresh.get_line_context", return_value=_context()),
    ):
        render_player_card_refresh(
            _player(PLAYER="<script>alert('bad')</script>"), "PTS", None, False
        )
    assert "<script>" not in html[0]
    assert "&lt;script&gt;" in html[0]


def test_matchup_header_has_logos_both_team_names_and_brasilia_time():
    html = []
    row = pd.Series({
        "VISITOR_TEAM_ID": 1610612738,
        "HOME_TEAM_ID": 1610612765,
        "away_team_name": "Boston Celtics",
        "home_team_name": "Detroit Pistons",
        "GAME_TIME_BRT": "20/10 20:00 BRT",
        "GAME_STATUS_TEXT": "7:00 PM ET",
    })
    with patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)):
        render_matchup_header_refresh(row)
    assert len(html) == 1
    assert "Boston Celtics" in html[0] and "Detroit Pistons" in html[0]
    assert "20/10 20:00 BRT" in html[0]
    assert "/1610612738/" in html[0]
    assert "/1610612765/" in html[0]

"""Visual-only smoke tests for the reversible dashboard redesign."""
from unittest.mock import patch

import numpy as np
import pandas as pd

from ui_refresh import (
    render_player_card_refresh, render_matchup_header_refresh,
    render_focus_summary_refresh, render_focus_comparison_refresh,
    render_tip_card_refresh,
)


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
        "line_source": "BetMGM" if active else "Linha indisponível",
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
    assert "Sem Edge · amostra insuficiente" in html[0]
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
    assert "Linha BetMGM" in html[0]
    assert "20.5 PTS" in html[0]
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



def test_modern_focus_summary_uses_same_projection_and_line_context():
    html = []
    player = _player(
        PROJ_PTS=22.0, PROJ_PTS_V1=21.7,
        SEASON_PTS=22.1, L10_PTS=22.4,
        MATCHUP_LABEL_PTS_V1="Difícil",
        MATCHUP_SCORE_PTS_V1=-0.45,
        OSC_PTS_CLASS="Média",
        FORM_PTS_SIGNAL="→ Estável",
    )
    with patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)):
        render_focus_summary_refresh(player, "PTS", _context(21.7, True))
    assert len(html) == 1
    assert "21.7" in html[0] and "22.4" in html[0]
    assert "20.5" in html[0]
    assert "Difícil" in html[0]


def test_modern_projection_comparison_shows_base_and_contextual_delta():
    html = []
    player = _player(PROJ_PTS=22.0, PROJ_PTS_V1=21.7)
    with patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)):
        render_focus_comparison_refresh(player, "PTS")
    assert "22.0" in html[0]
    assert "21.7" in html[0]
    assert "-0.3" in html[0]


def test_qualified_tip_card_preserves_existing_strength_and_line():
    html = []
    tip = {
        "PLAYER": "Tyrese Maxey",
        "TEAM_NAME": "Philadelphia 76ers",
        "METRIC": "PTS",
        "DIRECTION": "OVER",
        "LINE": 26.5,
        "PROJECTION": 28.1,
        "EDGE": 1.6,
        "HIT_TEXT": "7/10",
        "MATCHUP": "Favorável",
        "PROJ_MIN": 36,
        "LINE_SOURCE": "Manual",
        "STRENGTH": "DESTAQUE FORTE",
    }
    with patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)):
        render_tip_card_refresh("MELHOR LINHA DO CONFRONTO", tip)
    assert "Tyrese Maxey" in html[0]
    assert "26.5" in html[0] and "28.1" in html[0]
    assert "+1.6" in html[0]
    assert "7/10" in html[0]
    assert "DESTAQUE FORTE" in html[0]


def test_default_pts_and_separate_panorama_remain_selected():
    from pathlib import Path
    app_text = Path("app.py").read_text(encoding="utf-8")
    assert 'st.pills("Métrica", LINE_METRIC_OPTIONS, default="PTS")' in app_text
    assert 'st.tabs(["🏀 Jogadores", "📊 Panorama do confronto"])' in app_text
    assert 'key="dashboard_visual_mode"' in app_text


def test_dark_primary_buttons_have_dark_text_for_descendants():
    from pathlib import Path
    css = Path("style_refresh.css").read_text(encoding="utf-8")
    assert '#52bfad' in css
    assert 'button[kind="primary"] p' in css
    assert 'color: #07141b !important' in css



def test_refreshed_card_explains_unavailable_line_and_manual_entry():
    html = []
    with (
        patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)),
        patch("ui_refresh.get_line_context", return_value=_context(22.0, False)),
    ):
        render_player_card_refresh(_player(), "PTS", None, True)
    assert "Linha indisponível" in html[0]
    assert "Gerar manual" in html[0]
    assert "22.0" in html[0]



def test_bookmaker_quote_displays_on_card_even_with_gp_zero_and_no_projection():
    from processamento import get_line_context as true_context

    sample = _player(
        SEASON_GP=0,
        PROJ_PTS_V1=np.nan,
        BETMGM_PTS_LINE=29.5,
        MANUAL_LINE_PTS=25.5,
    )
    html = []
    with patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)):
        render_player_card_refresh(sample, "PTS", None, True)
    text = html[0]
    assert "Linha BetMGM" in text
    assert "29.5 PTS" in text
    assert 'class="nova-pred-value">—</div>' in text
    assert "Sem Edge · amostra insuficiente" in text
    assert "Linha ativa" not in text and "LINHA ATIVA" not in text

    context = true_context(sample, "PTS", None, use_market_line=True)
    assert context["quoted_source"] == "BetMGM"
    assert context["quoted_line"] == 29.5
    assert context["has_quote"]
    assert not context["has_active_line"]


def test_manual_line_displays_even_without_current_season_projection():
    sample = _player(
        SEASON_GP=0,
        PROJ_PTS_V1=np.nan,
        BETMGM_PTS_LINE=np.nan,
        MANUAL_LINE_PTS=23.5,
    )
    html = []
    with patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)):
        render_player_card_refresh(sample, "PTS", None, True)
    text = html[0]
    assert "Linha Manual" in text
    assert "23.5 PTS" in text
    assert "Sem Edge · amostra insuficiente" in text


def test_no_market_quote_never_displays_fake_active_line():
    sample = _player(
        SEASON_GP=0,
        PROJ_PTS_V1=np.nan,
        BETMGM_PTS_LINE=np.nan,
        MANUAL_LINE_PTS=np.nan,
    )
    html = []
    with patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)):
        render_player_card_refresh(sample, "PTS", None, True)
    text = html[0]
    assert "Linha indisponível" in text
    assert "Gerar manual" in text
    assert 'class="nova-pred-value">—</div>' in text


def test_focus_summary_preserves_line_even_if_player_has_no_game_sample():
    row = _player(
        SEASON_GP=0,
        PROJ_PTS_V1=np.nan,
        PROJ_PTS=np.nan,
        BETMGM_PTS_LINE=27.5,
    )
    from processamento import get_line_context
    ctx = get_line_context(row, "PTS", None, use_market_line=True)
    html = []
    with patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)):
        render_focus_summary_refresh(row, "PTS", ctx)
    assert "Linha BetMGM" in html[0]
    assert "27.5" in html[0]
    assert "Cotação existente; projeção ainda indisponível" in html[0]

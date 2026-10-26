"""Visual-only smoke tests for the reversible dashboard redesign."""
from unittest.mock import patch

import numpy as np
import pandas as pd

from ui_refresh import (
    render_player_card_refresh, render_matchup_header_refresh,
    render_focus_summary_refresh, render_focus_comparison_refresh,
    render_focus_statistics_refresh,
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
    assert 'line_metric = st.session_state.get("dashboard_line_metric", "PTS")' in app_text
    assert 'st.pills("Métrica", LINE_METRIC_OPTIONS, default="PTS")' not in app_text
    from pathlib import Path as _Path
    team_view = _Path("ui_components.py").read_text(encoding="utf-8")
    assert 'key="dashboard_line_metric"' in team_view
    assert team_view.index('key="dashboard_line_metric"') < team_view.index("    render_player_cards_grid(", team_view.index("def render_team_section_v2("))
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
    assert "Linha disponível · Edge sem amostra" in html[0]
    assert "nova-overview" in html[0]
    assert 'class="nova-overview-number">—</div>' in html[0]


def _book_row(**overrides):
    kwargs = {
        "MARKET_PTS_LINE": 28.5,
        "MARKET_PTS_SOURCE": "FanDuel",
        "MARKET_PTS_OVER_DEC": 1.87,
        "MARKET_PTS_UNDER_DEC": 1.95,
    }
    kwargs.update(overrides)
    return _player(**kwargs)


def _book_context(**overrides):
    context = {
        "projection": 31.0, "has_active_line": True,
        "has_market_line": True,
        "line_value": 28.5,
        "quoted_line": 28.5,
        "quoted_source": "FanDuel",
        "line_source": "FanDuel",
        "edge": 2.5,
    }
    context.update(overrides)
    return context


def test_market_odds_present_beside_correct_active_fanduel_line():
    from ui_refresh import card_market_odds
    row = _book_row()
    odds = card_market_odds(row, "PTS", _book_context(), True)
    assert odds == {"source": "FanDuel", "over": 1.87, "under": 1.95}
    html = []
    with (
        patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)),
        patch("ui_refresh.get_line_context", return_value=_book_context()),
    ):
        render_player_card_refresh(row, "PTS", None, True)
    assert "Linha FanDuel" in html[0]
    assert '<span class="nova-odd-over">Over 1.87</span>' in html[0]
    assert '<span class="nova-odd-under">Under 1.95</span>' in html[0]
    assert "Edge +2.5" in html[0]


def test_betmgm_quote_is_shown_not_fanduel_value():
    from ui_refresh import card_market_odds
    row = _book_row(
        MARKET_PTS_SOURCE="BetMGM",
        MARKET_PTS_OVER_DEC=2.15,
        MARKET_PTS_UNDER_DEC=1.71,
    )
    ctx = _book_context(quoted_source="BetMGM", line_source="BetMGM")
    odds = card_market_odds(row, "PTS", ctx, True)
    assert odds == {"source": "BetMGM", "over": 2.15, "under": 1.71}


def test_manual_line_cannot_inherit_bookmaker_odds():
    from ui_refresh import card_market_odds
    assert card_market_odds(
        _book_row(), "PTS",
        _book_context(quoted_source="Manual", line_source="Manual"),
        True
    ) is None
    assert card_market_odds(_book_row(), "PTS", _book_context(), False) is None


def test_odds_must_match_selected_book_and_line():
    from ui_refresh import card_market_odds
    assert card_market_odds(
        _book_row(), "PTS",
        _book_context(quoted_source="BetMGM", line_source="BetMGM"),
        True,
    ) is None
    assert card_market_odds(
        _book_row(), "PTS", _book_context(quoted_line=29.5), True
    ) is None


def test_partial_odds_are_not_fabricated_and_missing_both_are_hidden():
    from ui_refresh import card_market_odds
    row = _book_row(MARKET_PTS_UNDER_DEC=float("nan"))
    odds = card_market_odds(row, "PTS", _book_context(), True)
    assert odds == {"source": "FanDuel", "over": 1.87, "under": None}
    assert card_market_odds(
        _book_row(MARKET_PTS_OVER_DEC=None, MARKET_PTS_UNDER_DEC=None),
        "PTS", _book_context(), True
    ) is None


def test_market_quotes_visible_even_without_valid_player_projection():
    from ui_refresh import card_market_odds
    context = _book_context(
        projection=float("nan"), has_active_line=False, line_source="Dados ESPN incompletos",
    )
    row = _book_row(SEASON_GP=0, PROJ_PTS_V1=float("nan"))
    html = []
    with (
        patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)),
        patch("ui_refresh.get_line_context", return_value=context),
    ):
        render_player_card_refresh(row, "PTS", None, True)
    assert '<span class="nova-odd-over">Over 1.87</span>' in html[0]
    assert "Sem Edge" in html[0]


def test_selection_uses_small_official_team_shields_and_preserves_names():
    import inspect
    import app
    source = inspect.getsource(app.main)
    assert 'TEAM_LOGO_URL.format(team_id=team_id)' in source
    assert 'st.image(TEAM_LOGO_URL.format(team_id=team_id), width=26)' in source
    assert 'st.session_state[team_key] = team_name' in source
    assert 'st.button(' in source
    assert 'st.segmented_control(\n            "Time em análise"' not in source


def test_large_early_season_banner_removed_but_data_quality_checks_remain():
    import inspect
    import app
    source = inspect.getsource(app.main)
    assert "Amostra inicial da temporada — referência mínima" not in source
    assert "Os dados de game logs" not in source or "mas não retornou game logs" in source
    assert "mas não retornou game logs" in source


def test_odds_are_inline_and_use_distinct_over_under_colors():
    """Regression for long cards caused by strong tags wrapping vertically."""
    from pathlib import Path
    from ui_refresh import render_player_card_refresh
    html = []
    with (
        patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)),
        patch("ui_refresh.get_line_context", return_value=_book_context()),
    ):
        render_player_card_refresh(_book_row(), "PTS", None, True)
    fragment = html[0].split('class="nova-card-odds"')[1].split("</div>")[0]
    assert "<strong>" not in fragment
    assert '<span class="nova-odd-over">Over 1.87</span>' in fragment
    assert '<span class="nova-odd-under">Under 1.95</span>' in fragment
    assert fragment.index("nova-odd-over") < fragment.index("nova-odd-under")
    css = Path("style_refresh.css").read_text(encoding="utf-8")
    assert "flex-wrap:nowrap !important" in css
    assert ".nova-odd-over { color:#70e4a5 !important; }" in css
    assert ".nova-odd-under { color:#ff8990 !important; }" in css


def test_market_balloons_follow_fanduel_selection_without_betmgm_mentions():
    from ui_components import player_market_header_labels
    df = pd.DataFrame({"MARKET_PTS_SOURCE": ["FanDuel", "FanDuel", ""]})
    headline, mode = player_market_header_labels(
        df, "PTS", True, preferred_bookmaker="fanduel",
        enabled_bookmakers=("fanduel",),
    )
    assert "FanDuel" in headline and "FanDuel" in mode
    assert "BetMGM" not in headline + mode
    assert "PTS" in headline


def test_market_balloons_follow_betmgm_selection():
    from ui_components import player_market_header_labels
    df = pd.DataFrame({"MARKET_3PM_SOURCE": ["BetMGM", "", "BetMGM"]})
    headline, mode = player_market_header_labels(
        df, "3PM", True, preferred_bookmaker="betmgm",
        enabled_bookmakers=("betmgm",),
    )
    assert "BetMGM" in headline and "BetMGM" in mode
    assert "FanDuel" not in headline + mode


def test_both_selected_books_show_actual_fallback_without_false_labels():
    from ui_components import player_market_header_labels
    df = pd.DataFrame({"MARKET_PTS_SOURCE": ["BetMGM", "FanDuel", ""]})
    headline, mode = player_market_header_labels(
        df, "PTS", True, preferred_bookmaker="fanduel",
        enabled_bookmakers=("betmgm", "fanduel"),
    )
    assert "FanDuel / BetMGM por jogador" in headline
    assert "FanDuel prioritária" in mode
    assert "alternativa BetMGM" in mode


def test_selected_fanduel_only_without_price_does_not_claim_market_quote():
    from ui_components import player_market_header_labels
    df = pd.DataFrame({"MARKET_PTS_SOURCE": ["", None]})
    headline, mode = player_market_header_labels(
        df, "PTS", True, preferred_bookmaker="fanduel",
        enabled_bookmakers=("fanduel",),
    )
    assert "FanDuel quando houver" in headline
    assert "Modo mercado: FanDuel" == mode


def test_no_sportsbooks_keeps_manual_market_balloons():
    from ui_components import player_market_header_labels
    line, mode = player_market_header_labels(
        pd.DataFrame(), "REB", False,
        preferred_bookmaker="betmgm",
        enabled_bookmakers=(),
    )
    assert line == "Linha ativa: REB • manual"
    assert mode == "Modo mercado: Manual"


def test_focus_overview_only_contains_three_essential_decision_cards():
    html = []
    player = _player(
        PROJ_PTS=22.0, PROJ_PTS_V1=21.7,
        L5_PTS=23.2, L10_PTS=22.4,
        SEASON_PTS=22.1, SEASON_GP=3,
        PROJ_MIN_V1=34.1, MINUTES_SOURCE="Modelo interno",
        MATCHUP_LABEL_PTS_V1="Neutro",
    )
    with patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)):
        render_focus_summary_refresh(player, "PTS", _context(21.7, True))
    assert len(html) == 1
    assert html[0].count('class="nova-overview-card') == 3
    assert 'class="nova-overview-grid"' in html[0]
    assert "Projeção contextual · PTS" in html[0]
    assert "21.7" in html[0]
    assert "Linha BetMGM" in html[0] and "20.5" in html[0]
    assert "Edge" in html[0] and "+1.5" in html[0]
    assert "Média últimos 10 · PTS" in html[0] and "22.4" in html[0]
    assert "L5 <b>23.2</b>" in html[0]
    assert "Matchup Neutro" in html[0]
    assert "34.1 min estimados" in html[0]


def test_focus_overview_absent_quote_does_not_invent_edge():
    html = []
    ctx = _context(21.7, False)
    player = _player(PROJ_PTS_V1=21.7, L5_PTS=23.0)
    with patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)):
        render_focus_summary_refresh(player, "PTS", ctx)
    assert "Sem linha ativa para comparação" in html[0]
    assert "Informe uma linha para comparar" in html[0]
    assert 'class="nova-overview-edge' not in html[0]


def test_focus_overview_clears_recent_stats_for_zero_game_sample():
    html = []
    player = _player(
        SEASON_GP=0, SEASON_PTS=22.1, L5_PTS=24.4, L10_PTS=22.4,
        PROJ_PTS_V1=float("nan"),
    )
    with patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)):
        render_focus_summary_refresh(player, "PTS", _context(float("nan"), False))
    assert "0 jogos na temporada" in html[0]
    assert "22.4" not in html[0] and "24.4" not in html[0]


def test_focus_overview_uses_html_escape_for_external_names():
    html = []
    player = _player(
        MINUTES_SOURCE="<script>alert(1)</script>",
        MATCHUP_LABEL_PTS_V1="<b>inventado</b>",
    )
    with patch("ui_refresh.st.markdown", side_effect=lambda body, **kw: html.append(body)):
        render_focus_summary_refresh(player, "PTS", _context(21.7, True))
    assert "<script>" not in html[0]
    assert "&lt;script&gt;" in html[0]
    assert "<b>inventado</b>" not in html[0]


def test_focus_overview_scoped_to_modern_tab_with_calculation_collapsed():
    import inspect
    from ui_components import render_player_focus_panel
    source = inspect.getsource(render_player_focus_panel)
    assert 'with overview_tab:' in source
    assert 'if modern_focus:' in source
    assert 'with st.expander("Ver composição da projeção e critérios", expanded=False):' in source
    assert 'render_focus_summary_refresh(row, visual_metric, visual_line_context)' in source
    assert source.index('with overview_tab:') < source.index('render_focus_summary_refresh(')
    assert source.index('render_focus_summary_refresh(') < source.index('with detail_tab:')
    css = __import__("pathlib").Path("style_refresh.css").read_text(encoding="utf-8")
    assert ".nova-overview-grid" in css
    assert "@media(max-width:550px)" in css


def _statistics_player(**overrides):
    vals = {
        "SEASON_GP": 11, "L5_GP": 5, "L10_GP": 10,
        "MATCHUP_LABEL_REB_V1": "Neutro",
        "MATCHUP_SCORE_REB_V1": 0.12,
    }
    for metric, season, l10, l5 in (
        ("PRA", 28, 29, 31),
        ("PTS", 18, 19, 20),
        ("REB", 6, 7, 8),
        ("AST", 4, 3, 3),
        ("3PM", 2, 2, 2),
        ("FGA", 14, 15, 15),
        ("3PA", 5, 6, 7),
    ):
        vals[f"SEASON_{metric}"] = float(season)
        vals[f"L10_{metric}"] = float(l10)
        vals[f"L5_{metric}"] = float(l5)
    vals.update(overrides)
    return pd.Series(vals)


def _render_statistics(row, selected_metric="REB"):
    html = []
    with patch(
        "ui_refresh.st.markdown",
        side_effect=lambda value, **kw: html.append(value),
    ):
        render_focus_statistics_refresh(row, selected_metric)
    assert len(html) == 1
    return html[0]


def test_statistics_focus_renders_seven_visual_cards_with_featured_metric():
    html = _render_statistics(_statistics_player())
    assert html.count('<article class="nova-statcard') == 7
    assert html.count('class="nova-statcard featured"') == 1
    assert 'class="nova-statcards-grid"' in html
    assert "Desempenho do jogador" in html
    assert "L10 · 10j" in html and "L5 · 5j" in html
    assert "Matchup · REB" in html and "Neutro" in html
    assert "Pontos" in html and "Tentativas de 3 pontos" in html
    assert "Δ L5 − L10  +1.0" in html
    assert html.index("<strong>REB</strong>") < html.index("<strong>PRA</strong>")


def test_statistics_focus_cards_do_not_invent_early_season_trend():
    html = _render_statistics(
        _statistics_player(SEASON_GP=1, L5_GP=1, L10_GP=1)
    )
    assert "L10 · 1j" in html and "L5 · 1j" in html
    assert "Amostra curta" in html
    assert html.count("Δ L5 − L10  +") == 0
    assert 'class="nova-statcard-delta insufficient"' in html


def test_statistics_focus_cards_do_not_invent_zero_game_stats():
    html = _render_statistics(
        _statistics_player(SEASON_GP=0, L5_GP=0, L10_GP=0)
    )
    assert "Sem jogos na temporada" in html
    assert "0 jogo(s) nesta temporada" in html
    assert "28.0" not in html and "29.0" not in html
    assert '<strong>—</strong>' in html


def test_statistics_focus_gates_failed_espn_shooting_metrics():
    html = _render_statistics(
        _statistics_player(
            DATA_QUALITY_3PA=False,
            DATA_QUALITY_FGA=False,
            DATA_QUALITY_3PM=False,
        ), selected_metric="3PA"
    )
    assert html.count('class="nova-statcard featured"') == 1
    for metric in ("3PA", "FGA", "3PM"):
        card = html.split(f"<strong>{metric}</strong>")[1].split("</article>")[0]
        assert card.count("<strong>—</strong>") == 3
        assert card.count("width:0.0%") == 3
    # 7.0 may still legitimately appear in REB or another valid metric.
    # Verify only the three explicitly invalidated shooting cards above.


def test_statistics_focus_escapes_untrusted_matchup_values():
    html = _render_statistics(
        _statistics_player(MATCHUP_LABEL_REB_V1="<img src=x onerror=alert(1)>")
    )
    assert "<img" not in html
    assert "&lt;img" in html


def test_statistics_focus_bars_have_local_metric_scales():
    html = _render_statistics(_statistics_player())
    selected = html.split('<article class="nova-statcard featured">')[1].split("</article>")[0]
    # REB = 6/7/8, so the relative widths are 75%, 87.5%, 100%.
    assert 'width:75.0%' in selected
    assert 'width:87.5%' in selected
    assert 'width:100.0%' in selected
    assert 'aria-hidden="true"' in selected


def test_statistics_redesign_affects_modern_statistics_tab_only():
    import inspect
    from ui_components import render_player_focus_panel
    src = inspect.getsource(render_player_focus_panel)
    begin = src.index("    with detail_tab:")
    end = src.index("    with visual_tab:", begin)
    details = src[begin:end]
    assert "if modern_focus:" in details
    assert "render_focus_statistics_refresh(row, visual_metric)" in details
    assert 'st.expander("Ver leitura defensiva completa", expanded=False)' in details
    assert "render_detail_metric_box_html" in details  # Classic preserved
    assert "with overview_tab:" in src and "with visual_tab:" in src
    css = __import__("pathlib").Path("style_refresh.css").read_text(encoding="utf-8")
    assert ".nova-statcards-grid" in css
    assert ".nova-statcard.featured" in css
    assert "@media(max-width:620px)" in css
    assert ".nova-stats-row" not in css


def test_player_over_under_chart_marks_push_as_neutral():
    from ui_components import render_player_performance_chart

    figures = []
    with patch("ui_components.st.plotly_chart", side_effect=lambda fig, **kw: figures.append(fig)):
        render_player_performance_chart(
            "Jogador de teste", [4, 6, 8], 6.0, "REB"
        )
    assert len(figures) == 1
    assert list(figures[0].data[0].marker.color) == [
        "#ef4444", "#94a3b8", "#22c55e"
    ]


def test_h2h_uses_strict_outcome_and_labels_push_neutrally():
    import inspect
    from ui_components import render_player_focus_panel

    source = inspect.getsource(render_player_focus_panel)
    assert 'h2h_display["Resultado"]' in source
    assert 'classify_line_result(value, active_line)' in source
    assert 'if num > active_line:' in source
    assert 'if num < active_line:' in source
    assert 'push_count = int(values.eq(active_line).sum())' in source
    assert 'get_player_log(' in source and 'season_scope=season_scope' in source

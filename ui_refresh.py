"""Optional dark redesign. Pure presentation; forecasts and data sources are unchanged.

The classic components are preserved in ui_components.py and style.css.
"""
from __future__ import annotations

from html import escape
import math

import pandas as pd
import streamlit as st

from config import NBA_TEAM_COLORS, TEAM_LOOKUP, TEAM_LOGO_URL
from processamento import get_line_context


def _e(value) -> str:
    return escape(str("" if value is None else value), quote=True)


def _number(value, decimals=1) -> str:
    try:
        value = float(value)
        return f"{value:.{decimals}f}" if math.isfinite(value) else "—"
    except (ValueError, TypeError):
        return "—"


def _signed(value, decimals=1) -> str:
    try:
        value = float(value)
        return f"{value:+.{decimals}f}" if math.isfinite(value) else "—"
    except (ValueError, TypeError):
        return "—"


def _identity(row: pd.Series) -> tuple[str, int, dict]:
    raw_id = pd.to_numeric(row.get("TEAM_ID"), errors="coerce")
    team_id = int(raw_id) if pd.notna(raw_id) else 0
    abbr = str(row.get("TEAM_ABBR", "") or "").upper().strip()
    if team_id in TEAM_LOOKUP:
        abbr = str(TEAM_LOOKUP[team_id].get("abbreviation") or abbr).upper()
    if abbr not in NBA_TEAM_COLORS:
        fullname = str(row.get("TEAM_NAME") or "").casefold()
        for official_id, details in TEAM_LOOKUP.items():
            if str(details.get("full_name") or "").casefold() == fullname:
                team_id = int(official_id)
                abbr = str(details.get("abbreviation") or "").upper()
                break
    return abbr or "NBA", team_id, NBA_TEAM_COLORS.get(
        abbr, {"primary": "#6da4b8", "secondary": "#9bbbd7"}
    )


def render_matchup_header_refresh(game_row: pd.Series) -> None:
    away_id, home_id = int(game_row["VISITOR_TEAM_ID"]), int(game_row["HOME_TEAM_ID"])
    away = _e(game_row.get("away_team_name") or "Visitante")
    home = _e(game_row.get("home_team_name") or "Mandante")
    game_time = str(game_row.get("GAME_TIME_BRT") or "")
    if game_time.strip().lower() in {"nan", "none", ""}:
        game_time = str(game_row.get("GAME_STATUS_TEXT") or "Horário a confirmar")
    away_abbr = str(TEAM_LOOKUP.get(away_id, {}).get("abbreviation", "NBA"))
    home_abbr = str(TEAM_LOOKUP.get(home_id, {}).get("abbreviation", "NBA"))
    away_color = NBA_TEAM_COLORS.get(away_abbr, {}).get("primary", "#56a0bb")
    home_color = NBA_TEAM_COLORS.get(home_abbr, {}).get("primary", "#9580b8")
    away_logo = TEAM_LOGO_URL.format(team_id=away_id)
    home_logo = TEAM_LOGO_URL.format(team_id=home_id)
    html = (
        f'<section class="nova-matchup" style="--team-away:{away_color};--team-home:{home_color}">'
        '<div class="nova-matchup-kicker">Confronto selecionado · Horário de Brasília</div>'
        '<div class="nova-matchup-main">'
        '<div class="nova-matchup-team away">'
        f'<img class="nova-matchup-logo" src="{away_logo}" alt="Escudo {away}">'
        f'<div><div class="nova-matchup-name">{away}</div>'
        f'<div class="nova-matchup-meta">VISITANTE · {_e(away_abbr)}</div></div></div>'
        '<div class="nova-matchup-center"><div class="nova-versus">VS</div>'
        f'<span class="nova-gametime">{_e(game_time)}</span></div>'
        '<div class="nova-matchup-team home">'
        f'<img class="nova-matchup-logo" src="{home_logo}" alt="Escudo {home}">'
        f'<div><div class="nova-matchup-name">{home}</div>'
        f'<div class="nova-matchup-meta">MANDANTE · {_e(home_abbr)}</div></div></div>'
        '</div></section>'
    )
    st.markdown(html, unsafe_allow_html=True)


def render_player_card_refresh(
    row: pd.Series, line_metric: str, line_value: float, use_market_line: bool
) -> None:
    """Compact card: identity -> projection -> recent form -> risk & minutes."""
    abbr, team_id, colors = _identity(row)
    name = _e(row.get("PLAYER", "Jogador"))
    team = _e(row.get("TEAM_NAME", abbr))
    pos = _e(row.get("POSITION", "—"))
    gp_raw = pd.to_numeric(row.get("SEASON_GP"), errors="coerce")
    gp = int(gp_raw) if pd.notna(gp_raw) else 0
    sample = (
        f"{gp} jogo{'s' if gp != 1 else ''} nesta temporada"
        if gp else "Sem jogos nesta temporada"
    )
    player_raw = pd.to_numeric(row.get("PLAYER_ID"), errors="coerce")
    player_id = int(player_raw) if pd.notna(player_raw) else 0
    if player_id:
        image = (
            f'<img class="nova-portrait" loading="lazy" '
            f'src="https://a.espncdn.com/i/headshots/nba/players/full/{player_id}.png" '
            f'alt="Foto de {name}">'
        )
    else:
        image = '<div class="nova-portrait"></div>'
    logo = (
        f'<img class="nova-small-logo" loading="lazy" '
        f'src="{TEAM_LOGO_URL.format(team_id=team_id)}" alt="Escudo {team}">'
        if team_id else ""
    )
    ctx = get_line_context(
        row, line_metric, line_value, use_market_line=use_market_line
    )
    projection = pd.to_numeric(ctx.get("projection"), errors="coerce")
    is_valid = bool(pd.notna(projection))
    is_active = is_valid and bool(ctx.get("has_active_line", False))
    label = (
        f"Linha {_number(ctx.get('line_value'))} · {line_metric}"
        if is_active else f"Projeção {line_metric}"
    )
    subtitle = (
        f'Edge {_signed(ctx.get("edge"))}'
        if is_active else "Sem linha" if is_valid else "Amostra insuficiente"
    )
    matchup = str(row.get(f"MATCHUP_LABEL_{line_metric}_V1", "Neutro"))
    matchup_kind = (
        "positive" if matchup in {"Favorável", "Muito favorável"}
        else "negative" if matchup in {"Difícil", "Muito difícil"}
        else ""
    )
    injury = str(row.get("INJ_STATUS") or "—")
    injury_kind = (
        "negative" if injury in {"Out", "Doubtful"}
        else "warning" if injury == "Questionable" else ""
    )
    injury_tag = (
        f'<span class="nova-chip {injury_kind}">{_e(injury)}</span>'
        if injury not in {"—", "nan", "None", ""}
        else '<span class="nova-chip warning">Lesões não confirmadas</span>'
    )
    role = str(row.get("ROLE") or "Rotação")
    minutes = _number(row.get("PROJ_MIN_V1"))
    source = _e(row.get("MINUTES_SOURCE", "Modelo interno"))

    def _stat(code: str, key: str) -> str:
        val = _number(row.get(key)) if gp > 0 else "—"
        return (
            '<div class="nova-stat">'
            f'<div class="nova-stat-label">{code} · L10</div>'
            f'<div class="nova-stat-value">{val}</div>'
            '<div class="nova-stat-small">média recente</div></div>'
        )

    html = (
        f'<article class="nova-player" style="--team:{colors["primary"]};'
        f'--team-soft:{colors["secondary"]}">'
        '<div class="nova-player-top">'
        f'{image}<div>'
        f'<div class="nova-player-name">{name}</div>'
        f'<div class="nova-player-team"><strong>{_e(abbr)}</strong> · {pos}</div>'
        f'<div class="nova-sample">{_e(sample)}</div>'
        f'</div>{logo}</div>'
        '<div class="nova-pred"><div>'
        f'<div class="nova-pred-label">{_e(label)}</div>'
        f'<div class="nova-pred-value">{_number(projection)}</div>'
        '</div><div class="nova-pred-aside">'
        f'<div class="nova-small">{"EDGE / LINHA" if is_active else "LINHA ATIVA"}</div>'
        f'<strong>{_e(subtitle)}</strong></div></div>'
        '<div class="nova-stat-grid">'
        f'{_stat("PTS", "L10_PTS")}'
        f'{_stat("REB", "L10_REB")}'
        f'{_stat("AST", "L10_AST")}'
        '</div>'
        '<div class="nova-card-bottom"><div class="nova-chips">'
        f'<span class="nova-chip">{_e(role)}</span>'
        f'<span class="nova-chip {matchup_kind}">Matchup {_e(matchup)}</span>'
        f'{injury_tag}'
        '</div></div>'
        '<div class="nova-card-footer">'
        f'MIN PROJETADOS <strong>{minutes}</strong> · {source} · {team}'
        '</div></article>'
    )
    st.markdown(html, unsafe_allow_html=True)

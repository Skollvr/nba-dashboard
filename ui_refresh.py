"""Optional dark redesign. Pure presentation; forecasts and data sources are unchanged.

The classic components are preserved in ui_components.py and style.css.
"""
from __future__ import annotations

from html import escape
import math

import pandas as pd
import streamlit as st

from config import NBA_TEAM_COLORS, TEAM_LOOKUP, TEAM_LOGO_URL
from processamento import get_line_context, get_metric_projection_column


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


def card_market_odds(
    row: pd.Series, metric: str, context: dict, use_market_line: bool
) -> dict | None:
    """Read the active bookmaker's existing decimal odds; never blend books.

    Market quotations are independent of prediction sample size. An absent
    Over/Under side must remain absent, and manual lines never inherit market
    prices.
    """
    if not use_market_line:
        return None
    source = str(context.get("quoted_source") or context.get("line_source") or "")
    stored_source = str(row.get(f"MARKET_{metric}_SOURCE") or "")
    if source not in {"BetMGM", "FanDuel"} or source != stored_source:
        return None

    selected_line = pd.to_numeric(row.get(f"MARKET_{metric}_LINE"), errors="coerce")
    quoted_line = pd.to_numeric(context.get("quoted_line"), errors="coerce")
    if (
        pd.isna(selected_line) or pd.isna(quoted_line)
        or not (math.isfinite(float(selected_line)) and math.isfinite(float(quoted_line)))
        or selected_line <= 0 or abs(float(selected_line) - float(quoted_line)) > 1e-6
    ):
        return None

    def _valid_decimal(value):
        candidate = pd.to_numeric(value, errors="coerce")
        return (
            float(candidate)
            if pd.notna(candidate) and math.isfinite(float(candidate)) and candidate > 1
            else None
        )

    over = _valid_decimal(row.get(f"MARKET_{metric}_OVER_DEC"))
    under = _valid_decimal(row.get(f"MARKET_{metric}_UNDER_DEC"))
    if over is None and under is None:
        return None
    return {"source": source, "over": over, "under": under}


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
    # The bookmaker may publish a line before the player has a single game
    # in the selected season. Show it without inventing a projection or Edge.
    raw_quote = ctx.get(
        "quoted_line",
        ctx.get("line_value") if is_active else None,
    )
    quote = pd.to_numeric(raw_quote, errors="coerce")
    has_quote = bool(pd.notna(quote) and float(quote) > 0)
    quote_source = str(
        ctx.get("quoted_source")
        or (ctx.get("line_source") if is_active else "")
        or "Linha"
    )
    label = f"Projeção {line_metric}"
    quote_label = (
        f"Linha {quote_source}" if has_quote else "Linha indisponível"
    )
    quote_value = (
        f"{_number(quote)} {line_metric}" if has_quote else "Gerar manual"
    )
    quote_note = (
        f'Edge {_signed(ctx.get("edge"))}'
        if is_active else "Sem Edge · amostra insuficiente"
        if not is_valid else "Sem linha para calcular Edge"
    )
    market_odds = card_market_odds(row, line_metric, ctx, use_market_line)
    odds_html = ""
    if market_odds:
        over_text = _number(market_odds["over"], 2)
        under_text = _number(market_odds["under"], 2)
        odds_html = (
            '<div class="nova-card-odds" aria-label="Odds decimais da linha">'
            f'<span class="nova-odd-over">Over {over_text}</span>'
            f'<span class="nova-odd-under">Under {under_text}</span>'
            '</div>'
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
        f'<div class="nova-small">{_e(quote_label)}</div>'
        f'<strong>{_e(quote_value)}</strong>'
        f'<div class="nova-quote-hint">{_e(quote_note)}</div>'
        f'{odds_html}'
        '</div></div>'
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



def render_focus_summary_refresh(
    row: pd.Series,
    metric: str,
    line_context: dict,
) -> None:
    """Concise decision-oriented overview, with no change to V4 calculations.

    One projection, one market comparison, one recent-form panel. Technical
    decomposition is accessible in a collapsed disclosure in the caller.
    """
    projected = pd.to_numeric(row.get(f"PROJ_{metric}_V1"), errors="coerce")
    if pd.isna(projected):
        projected = pd.to_numeric(line_context.get("projection"), errors="coerce")
    season = pd.to_numeric(row.get(f"SEASON_{metric}"), errors="coerce")
    l5 = pd.to_numeric(row.get(f"L5_{metric}"), errors="coerce")
    l10 = pd.to_numeric(row.get(f"L10_{metric}"), errors="coerce")
    minutes = pd.to_numeric(row.get("PROJ_MIN_V1"), errors="coerce")
    games = pd.to_numeric(row.get("SEASON_GP"), errors="coerce")
    # A zero-game roster must not surface stale or placeholder trend stats.
    if pd.notna(games) and games <= 0:
        season = l5 = l10 = float("nan")
    has_projection = pd.notna(projected) and math.isfinite(float(projected))

    active = bool(line_context.get("has_active_line", False)) and has_projection
    quoted = pd.to_numeric(
        line_context.get(
            "quoted_line", line_context.get("line_value") if active else None
        ), errors="coerce",
    )
    has_quote = pd.notna(quoted) and math.isfinite(float(quoted)) and quoted > 0
    source = str(line_context.get("quoted_source") or (
        line_context.get("line_source") if active else ""
    ) or "Mercado")
    edge = pd.to_numeric(line_context.get("edge"), errors="coerce") if active else None
    edge_label = _signed(edge) if active else "—"
    edge_tone = (
        "positive" if active and pd.notna(edge) and edge > 0
        else "negative" if active and pd.notna(edge) and edge < 0
        else "neutral"
    )

    matchup = str(row.get(f"MATCHUP_LABEL_{metric}_V1") or "Sem avaliação")
    hit = str(line_context.get("hit_l10") or "—") if active else "—"
    sample = (
        f"{int(games)} jogo{'s' if int(games) != 1 else ''} na temporada"
        if pd.notna(games) and games >= 0 else "Amostra não disponível"
    )
    minute_source = str(row.get("MINUTES_SOURCE") or "Modelo interno")

    projection_info = (
        f"{_number(minutes)} min estimados · {_e(minute_source)}"
        if has_projection else "Projeção indisponível nesta amostra"
    )
    market_info = (
        f'Edge <span class="nova-overview-edge {edge_tone}">{_e(edge_label)}</span>'
        if active else "Linha disponível · Edge sem amostra"
        if has_quote else "Sem linha ativa para comparação"
    )
    hit_info = f"Acima da linha no L10: {_e(hit)}" if active else (
        "Aguardando dados de projeção" if has_quote
        else "Informe uma linha para comparar"
    )
    recent_info = (
        f'L5 <b>{_number(l5)}</b><span>·</span>'
        f'Temporada <b>{_number(season)}</b>'
    )

    html = (
        '<section class="nova-overview">'
        '<div class="nova-overview-heading">'
        '<strong>Leitura rápida</strong>'
        '<span>O essencial para avaliar a projeção atual</span>'
        '</div>'
        '<div class="nova-overview-grid">'
        '<article class="nova-overview-card primary">'
        f'<div class="nova-overview-label">Projeção contextual · {_e(metric)}</div>'
        f'<div class="nova-overview-number">{_number(projected)}</div>'
        f'<div class="nova-overview-sub">{projection_info}</div>'
        '</article>'
        '<article class="nova-overview-card">'
        f'<div class="nova-overview-label">Linha {_e(source) if has_quote else "de mercado"} · {_e(metric)}</div>'
        f'<div class="nova-overview-number">{_number(quoted) if has_quote else "—"}</div>'
        f'<div class="nova-overview-sub">{market_info}</div>'
        f'<div class="nova-overview-foot">{hit_info}</div>'
        '</article>'
        '<article class="nova-overview-card">'
        f'<div class="nova-overview-label">Média últimos 10 · {_e(metric)}</div>'
        f'<div class="nova-overview-number">{_number(l10)}</div>'
        f'<div class="nova-overview-sub nova-overview-recent">{recent_info}</div>'
        f'<div class="nova-overview-foot">{_e(sample)} · Matchup {_e(matchup)}</div>'
        '</article>'
        '</div></section>'
    )
    st.markdown(html, unsafe_allow_html=True)


def render_focus_statistics_refresh(row: pd.Series, selected_metric: str) -> None:
    """Seven visual season/L10/L5 cards with the selected metric featured.

    Presentation only: existing ESPN averages, sample counts and matchup
    classifications are used unchanged. Incomplete shooting data remain blank,
    and L5-L10 deltas are withheld while the windows overlap fully.
    """
    metrics = ("PRA", "PTS", "REB", "AST", "3PM", "FGA", "3PA")
    names = {
        "PRA": "Pontos + rebotes + assistências",
        "PTS": "Pontos", "REB": "Rebotes", "AST": "Assistências",
        "3PM": "Cestas de 3 pontos", "FGA": "Tentativas de arremesso",
        "3PA": "Tentativas de 3 pontos",
    }
    gp = pd.to_numeric(row.get("SEASON_GP"), errors="coerce")
    season_gp = int(gp) if pd.notna(gp) and math.isfinite(float(gp)) and gp > 0 else 0

    def sample(column: str, maximum: int) -> int:
        observed = pd.to_numeric(row.get(column), errors="coerce")
        if pd.notna(observed) and math.isfinite(float(observed)):
            return max(0, min(season_gp, maximum, int(observed)))
        return min(season_gp, maximum)

    l5_gp = sample("L5_GP", 5)
    l10_gp = sample("L10_GP", 10)
    enough_for_delta = l5_gp == 5 and l10_gp >= 6

    def number(metric: str, period: str, count: int) -> float | None:
        if count == 0:
            return None
        if metric in {"3PM", "FGA", "3PA"}:
            flag = row.get(f"DATA_QUALITY_{metric}", True)
            if pd.isna(flag) or str(flag).strip().casefold() in {
                "false", "0", "none", "nan"
            }:
                return None
        raw = pd.to_numeric(row.get(f"{period}_{metric}"), errors="coerce")
        return float(raw) if pd.notna(raw) and math.isfinite(float(raw)) else None

    chosen = selected_metric if selected_metric in metrics else "PRA"
    ordered = (chosen, *(metric for metric in metrics if metric != chosen))
    cards = [
        '<section class="nova-statcards-section">',
        '<div class="nova-statcards-intro">',
        '<div><strong>Desempenho do jogador</strong>',
        '<p>Temporada, últimos 10 e últimos 5 jogos em uma leitura visual.</p></div>',
        f'<span class="nova-statcards-sample">{season_gp} jogo(s) nesta temporada</span>',
        '</div>',
        '<div class="nova-statcards-grid">',
    ]
    for metric in ordered:
        values = (
            number(metric, "SEASON", season_gp),
            number(metric, "L10", l10_gp),
            number(metric, "L5", l5_gp),
        )
        valid = [value for value in values if value is not None]
        maximum = max(valid, default=0.0)
        widths = [
            min(100.0, max(0.0, (value / maximum) * 100))
            if value is not None and maximum > 0 else 0.0
            for value in values
        ]
        delta = (
            values[2] - values[1]
            if enough_for_delta and values[2] is not None and values[1] is not None
            else None
        )
        tone = (
            "up" if delta is not None and delta > 0.049
            else "down" if delta is not None and delta < -0.049
            else "steady" if delta is not None else "insufficient"
        )
        if delta is not None:
            delta_text = f"Δ L5 − L10  {_signed(delta)}"
        elif season_gp == 0:
            delta_text = "Sem jogos na temporada"
        elif not enough_for_delta:
            delta_text = "Amostra curta · sem tendência L5/L10"
        else:
            delta_text = "Dados insuficientes para variação"
        featured = metric == chosen
        selected_note = '<span class="nova-statcard-selected">Em análise</span>' if featured else ""
        class_name = "nova-statcard featured" if featured else "nova-statcard"
        cards.extend([
            f'<article class="{class_name}">',
            '<div class="nova-statcard-head">',
            f'<div><strong>{_e(metric)}</strong><small>{_e(names[metric])}</small></div>',
            selected_note,
            '</div>',
            '<div class="nova-statcard-values">',
        ])
        for period_name, count, value, period_class in (
            ("Temporada", season_gp, values[0], "season"),
            ("L10", l10_gp, values[1], "last10"),
            ("L5", l5_gp, values[2], "last5"),
        ):
            label = period_name if period_name == "Temporada" else f"{period_name} · {count}j"
            cards.extend([
                f'<div class="nova-statcard-value {period_class}">',
                f'<span>{_e(label)}</span>',
                f'<strong>{_number(value)}</strong>',
                '</div>',
            ])
        cards.append('</div>')
        cards.append('<div class="nova-statcard-bars" aria-hidden="true">')
        for period_class, width in zip(("season", "last10", "last5"), widths):
            cards.append(
                f'<div class="nova-statcard-track {period_class}">'
                f'<i style="width:{width:.1f}%"></i></div>'
            )
        cards.extend([
            '</div>',
            f'<div class="nova-statcard-delta {tone}">{_e(delta_text)}</div>',
            '</article>',
        ])
    cards.extend(['</div>'])

    matchup = row.get(f"MATCHUP_LABEL_{chosen}_V1", "Sem avaliação")
    label = str(matchup) if pd.notna(matchup) and str(matchup).strip() else "Sem avaliação"
    score = pd.to_numeric(row.get(f"MATCHUP_SCORE_{chosen}_V1"), errors="coerce")
    score_text = f"Score {_signed(score, 2)}" if pd.notna(score) and math.isfinite(float(score)) else ""
    cards.extend([
        '<div class="nova-statcards-matchup">',
        f'<span>Matchup · {_e(chosen)}</span>',
        f'<strong>{_e(label)}</strong>',
        f'<small>{_e(score_text) if score_text else "Avaliação do Matchup V4"}</small>',
        '</div>',
        '<p class="nova-statcards-note">As barras comparam apenas períodos da mesma '
        'métrica, sem comparar pontos com rebotes. Variação L5 − L10 indica '
        'produção recente, não resultado previsto.</p>',
        '</section>',
    ])
    st.markdown("".join(cards), unsafe_allow_html=True)


def render_focus_comparison_refresh(row: pd.Series, metric: str) -> None:
    """Compare baseline and contextual projections without recomputing them."""
    base = pd.to_numeric(row.get(get_metric_projection_column(metric)), errors="coerce")
    contextual = pd.to_numeric(row.get(f"PROJ_{metric}_V1"), errors="coerce")
    delta = (
        contextual - base
        if pd.notna(contextual) and pd.notna(base) else None
    )
    metrics = [
        ("Projeção padrão", _number(base), ""),
        ("Projeção contextual", _number(contextual), "proj"),
        ("Ajuste total", _signed(delta), ""),
    ]
    html = '<div class="nova-detail-compare">'
    for label, value, style in metrics:
        html += (
            f'<div class="nova-compare-item {style}">'
            f'<div class="nova-compare-label">{_e(label)} · {_e(metric)}</div>'
            f'<div class="nova-compare-value">{_e(value)}</div>'
            '</div>'
        )
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)


def render_tip_card_refresh(title: str, tip: dict | None) -> None:
    """Display an existing qualified best-line candidate without recalculation."""
    if tip is None:
        st.markdown(
            '<div class="nova-empty-panel">Ainda não há outra linha '
            'que atenda aos critérios de destaque.</div>',
            unsafe_allow_html=True,
        )
        return
    top = "top" if str(title).lower().find("melhor linha") >= 0 and "segunda" not in str(title).lower() else ""
    stats = [
        ("Projeção", _number(tip.get("PROJECTION"))),
        ("Edge", _signed(tip.get("EDGE"))),
        (f"Hit L10 {tip.get('DIRECTION', '')}", str(tip.get("HIT_TEXT") or "—")),
    ]
    cells = "".join(
        '<div class="nova-tip-metric">'
        f'<small>{_e(label)}</small><strong>{_e(value)}</strong></div>'
        for label, value in stats
    )
    line = _number(tip.get("LINE"))
    html = (
        f'<section class="nova-tip {top}">'
        '<div class="nova-tip-top">'
        f'<div class="nova-tip-label">{_e(title)}</div>'
        f'<div class="nova-tip-strength">{_e(tip.get("STRENGTH", ""))}</div>'
        '</div>'
        f'<div class="nova-tip-player">{_e(tip.get("PLAYER", ""))}</div>'
        f'<div class="nova-tip-details">'
        f'{_e(tip.get("TEAM_NAME", ""))} · '
        f'{_e(tip.get("DIRECTION", ""))} {line} {_e(tip.get("METRIC", ""))}'
        '</div>'
        f'<div class="nova-tip-metrics">{cells}</div>'
        '<div class="nova-tip-details">'
        f'Matchup: {_e(tip.get("MATCHUP", "—"))} · '
        f'MIN proj: {_number(tip.get("PROJ_MIN"))} · '
        f'{_e(tip.get("LINE_SOURCE", "linha"))}'
        '</div></section>'
    )
    st.markdown(html, unsafe_allow_html=True)

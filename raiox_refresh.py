"""Modern, mobile-first Raio-X only. Classic charts remain untouched."""
from __future__ import annotations

from html import escape
import math
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from api_nba import get_player_log
from espn_context import _cutoff_timestamp, _pregame_date_mask

DISPLAY_CONFIG = {"displayModeBar": False, "displaylogo": False, "responsive": True}
METRIC_COL = {
    "PTS": "PTS", "REB": "REB", "AST": "AST", "PRA": "PRA",
    "3PM": "FG3M", "FGA": "FGA", "3PA": "FG3A",
}
ACCENTS = {
    "PTS": "#4ab4fa", "REB": "#4cdaa4",
    "AST": "#f4bc6b", "PRA": "#bd9aff",
}
FLOAT_COLORS = ["#5c9ce2", "#66dfbb", "#f3bb77"]


def observed_log(log: pd.DataFrame, target_date=None) -> pd.DataFrame:
    """Never show later or same-day results for selected historical matchup."""
    if log is None or log.empty:
        return pd.DataFrame()
    work = log.copy()
    if target_date is not None:
        cutoff = _cutoff_timestamp(target_date)
        if cutoff is None or "GAME_DATE" not in work:
            return work.iloc[0:0].copy()
        work = work.loc[_pregame_date_mask(work["GAME_DATE"], cutoff)].copy()
    needed = ("GAME_DATE", "PTS", "REB", "AST")
    if any(field not in work for field in needed):
        return work.iloc[0:0].copy()
    work["GAME_DATE"] = pd.to_datetime(work["GAME_DATE"], utc=True, errors="coerce")
    for col in ("PTS", "REB", "AST", "MIN", "FGA", "FG3M", "FG3A"):
        if col in work:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["GAME_DATE", "PTS", "REB", "AST"])
    work["PRA"] = work["PTS"] + work["REB"] + work["AST"]
    return work.sort_values("GAME_DATE").reset_index(drop=True)


def _value(value) -> str:
    num = pd.to_numeric(value, errors="coerce")
    return f"{float(num):.1f}" if pd.notna(num) and np.isfinite(num) else "—"


def _base_fig(height: int = 280) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        template="plotly_dark", height=height,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(12,27,43,0.78)",
        font=dict(color="#b8d2e5", size=11), showlegend=False,
        margin=dict(l=22, r=18, t=40, b=40), dragmode=False,
        hoverlabel=dict(bgcolor="#142c42", font=dict(color="#edf8ff")),
    )
    fig.update_yaxes(gridcolor="rgba(136,173,199,0.16)", zeroline=False)
    fig.update_xaxes(showgrid=False)
    return fig


def render_raiox_refresh(
    row: pd.Series,
    metric: str,
    season: str,
    season_scope: str,
    chart_mode: str,
    line_context: dict,
    target_date=None,
) -> None:
    name = escape(str(row.get("PLAYER") or "Jogador"))
    st.markdown(
        f'<div class="xray-title"><strong>Raio-X · {name}</strong>'
        '<span>Forma recente, dispersão e relação com minutos</span></div>',
        unsafe_allow_html=True,
    )
    stat_metric = metric if metric in METRIC_COL else "PTS"
    gp = pd.to_numeric(row.get("SEASON_GP"), errors="coerce")
    sample = int(gp) if pd.notna(gp) and gp > 0 else 0
    stat_cards = ['<div class="xray-stat-grid">']
    for m in ("PTS", "REB", "AST", "PRA"):
        raw = row.get(f"SEASON_{m}") if sample else None
        stat_cards.append(
            f'<div class="xray-stat" style="--xray-accent:{ACCENTS[m]}">'
            f'<span>{m} · Temporada</span>'
            f'<strong>{_value(raw)}</strong>'
            f'<small>{sample} jogo(s) disputado(s)</small></div>'
        )
    stat_cards.append("</div>")
    st.markdown("".join(stat_cards), unsafe_allow_html=True)

    log = observed_log(
        get_player_log(int(row["PLAYER_ID"]), season, season_scope=season_scope),
        target_date,
    )
    source_col = METRIC_COL[stat_metric]
    if log.empty or source_col not in log:
        st.info(f"Sem jogos anteriores com dados válidos de {stat_metric} neste recorte.")
        return
    usable = log.dropna(subset=[source_col]).copy()
    if usable.empty:
        st.info(f"A ESPN não disponibilizou valores válidos de {stat_metric}.")
        return
    usable["VALUE"] = usable[source_col]
    take = 10 if chart_mode == "Completo" else 5
    recent = usable.tail(take).copy()
    recent["SHORT_DATE"] = recent["GAME_DATE"].dt.strftime("%d/%m")
    observed_mean = float(recent["VALUE"].mean())
    peak = max(1, float(recent["VALUE"].max()))

    st.markdown(
        '<div class="xray-section-head"><strong>Últimos jogos</strong>'
        f'<span>{stat_metric} · {len(recent)} resultado(s) · '
        f'média {_value(observed_mean)}</span></div>',
        unsafe_allow_html=True,
    )
    fig = _base_fig(295)
    fig.add_trace(go.Bar(
        x=recent["SHORT_DATE"], y=recent["VALUE"],
        text=recent["VALUE"].round(1), textposition="outside",
        cliponaxis=False, marker_color="#42afe8",
        hovertemplate="%{x} · %{y:.1f} " + stat_metric + "<extra></extra>",
    ))
    fig.add_hline(
        y=observed_mean, line_dash="dash", line_color="#f2c574",
        annotation_text="Média", annotation_position="top left",
        annotation_font_color="#f2c574",
    )
    fig.update_yaxes(range=[0, peak * 1.25], title_text="")
    fig.update_xaxes(type="category", title_text="", tickangle=0)
    st.plotly_chart(fig, use_container_width=True, config=DISPLAY_CONFIG)

    st.markdown(
        '<div class="xray-section-head"><strong>Desempenho por período</strong>'
        '<span>Médias observadas, não projeções</span></div>',
        unsafe_allow_html=True,
    )
    parts = ['<div class="xray-period-grid">']
    for label, key, color in (
        ("Temporada", "SEASON", "#50b5f5"),
        ("Últimos 10", "L10", "#e8b460"),
        ("Últimos 5", "L5", "#53dba6"),
    ):
        value = row.get(f"{key}_{stat_metric}") if sample else None
        parts.append(
            f'<div class="xray-period" style="--xray-accent:{color}">'
            f'<span>{label}</span><strong>{_value(value)}</strong>'
            f'<small>{min(sample, 10 if key == "L10" else 5) if key != "SEASON" else sample}'
            ' jogo(s)</small></div>'
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)
    if sample < 6:
        st.caption("Amostra pequena: L5 e L10 podem conter os mesmos jogos.")

    distribution_col, frequency_col = st.columns(2, gap="small")
    with distribution_col:
        st.markdown('<div class="xray-section-head"><strong>Distribuição da produção</strong>'
                    '<span>Jogos da temporada selecionada</span></div>', unsafe_allow_html=True)
        chart = _base_fig(260)
        chart.add_trace(go.Histogram(
            x=usable["VALUE"], marker_color="#46b0f0",
            xbins=dict(start=-0.5, end=peak + 1, size=max(1, math.ceil(peak / 14))),
            hovertemplate="Faixa: %{x}<br>Jogos: %{y}<extra></extra>",
        ))
        chart.add_vline(
            x=float(usable["VALUE"].mean()), line_dash="dash",
            line_color="#f3bb77",
        )
        chart.update_layout(margin=dict(l=20, r=12, t=20, b=30))
        chart.update_xaxes(title_text=stat_metric)
        chart.update_yaxes(title_text="Jogos")
        st.plotly_chart(chart, use_container_width=True, config=DISPLAY_CONFIG)
        st.caption(
            f'Média {_value(usable["VALUE"].mean())} · '
            f'Mín {_value(usable["VALUE"].min())} · '
            f'Máx {_value(usable["VALUE"].max())}'
        )

    with frequency_col:
        st.markdown('<div class="xray-section-head"><strong>Frequência na linha</strong>'
                    '<span>Resultados observados</span></div>', unsafe_allow_html=True)
        line = pd.to_numeric(line_context.get("line_value"), errors="coerce")
        if not line_context.get("has_active_line") or pd.isna(line):
            st.info("Sem linha válida para calcular Over, Under ou Push.")
        else:
            above = int(usable["VALUE"].gt(line).sum())
            below = int(usable["VALUE"].lt(line).sum())
            pushes = int(usable["VALUE"].eq(line).sum())
            pie = _base_fig(260)
            pie.add_trace(go.Pie(
                labels=["Over", "Under", "Push"],
                values=[above, below, pushes],
                hole=.70, sort=False, textinfo="none",
                marker=dict(colors=["#43d6a2", "#fa7783", "#94a3b8"]),
                hovertemplate="%{label}: %{value} jogo(s)<extra></extra>",
            ))
            pie.update_layout(
                annotations=[dict(text=f"{len(usable)}<br>jogos", x=.5, y=.5,
                                  showarrow=False, font=dict(size=14, color="#eff8ff"))],
                margin=dict(l=10, r=10, t=8, b=8),
            )
            st.plotly_chart(pie, use_container_width=True, config=DISPLAY_CONFIG)
            st.caption(f"Linha {_value(line)} · Over {above} · Under {below} · Push {pushes}")

    st.markdown(
        '<div class="xray-section-head"><strong>Produção x minutos</strong>'
        '<span>Cada ponto representa um jogo real</span></div>',
        unsafe_allow_html=True,
    )
    if "MIN" not in usable or usable["MIN"].dropna().empty:
        st.caption("Minutos registrados insuficientes para esta análise.")
        return
    minute_log = usable.dropna(subset=["MIN"]).copy()
    minute_fig = _base_fig(265)
    minute_fig.add_trace(go.Scatter(
        x=minute_log["MIN"], y=minute_log["VALUE"],
        mode="markers", marker=dict(color="#63d5c3", size=10),
        customdata=minute_log["GAME_DATE"].dt.strftime("%d/%m"),
        hovertemplate="Jogo %{customdata}<br>Min %{x:.1f}<br>" + stat_metric +
        " %{y:.1f}<extra></extra>",
    ))
    minute_fig.update_xaxes(title_text="Minutos disputados")
    minute_fig.update_yaxes(title_text=stat_metric, rangemode="tozero")
    st.plotly_chart(minute_fig, use_container_width=True, config=DISPLAY_CONFIG)
    st.caption("Associação observada entre minutos e produção; não é uma projeção causal.")

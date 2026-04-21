import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import pytz
from datetime import datetime
from pandas.io.formats.style import Styler
from pdf_reader import parse_injury_report_timestamp_from_url
from config import (
    NBA_TEAM_COLORS, TEAM_LOGO_URL, PLAYER_HEADSHOT_URL, 
    LINE_METRIC_OPTIONS, CHART_OPTIONS, TEAM_LOOKUP, SORT_OPTIONS
)
from processamento import (
    get_line_context,
    get_matchup_chip_class,
    get_metric_matchup_context,
    fetch_latest_injury_report_df,
    merge_injury_report,
    filter_and_sort_team_df,
    build_display_dataframes,
    build_summary_cards_data,
    get_metric_projection_column,
)
from api_nba import get_player_log

from datetime import timedelta

# =========================================================
# 1. ESTILIZAÇÃO E CSS
# =========================================================

def get_game_datetime_brasilia(status_text: str) -> str:
    """Converte o status de horário da NBA (ET) para o horário de Brasília."""
    if not status_text or "ET" not in status_text:
        return status_text # Retorna "Final" ou "Live" se o jogo já começou
    
    try:
        # Extrai o horário (ex: "7:30 pm")
        time_part = status_text.replace(" ET", "").strip()
        # Converte para objeto datetime assumindo a data de hoje
        et_time = datetime.strptime(time_part, "%I:%M %p")
        
        # Define o fuso de NY (Eastern Time) e de Brasília
        tz_et = pytz.timezone("US/Eastern")
        tz_br = pytz.timezone("America/Sao_Paulo") # Ou use a variável APP_TIMEZONE
        
        # Ajusta para hoje e aplica o fuso ET
        now = datetime.now()
        et_dt = tz_et.localize(datetime(now.year, now.month, now.day, et_time.hour, et_time.minute))
        
        # Converte para Brasília
        br_dt = et_dt.astimezone(tz_br)
        return br_dt.strftime("%H:%M BRT")
    except Exception:
        return status_text # Caso algo falhe, mantém o original para não quebrar a tela

def inject_css() -> None:
    """Lê o arquivo style.css e aplica no Streamlit."""
    try:
        with open("style.css", "r", encoding="utf-8") as f:
            css = f.read()
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        pass

def style_full_stats_table(df: pd.DataFrame) -> Styler:
    """Aplica cores e formatos na tabela detalhada."""
    return df.style.format(precision=1).background_gradient(
        cmap="RdYlGn", subset=["Δ PRA L10", "Δ PRA L5"], vmin=-5, vmax=5
    )

def style_summary_table(df: pd.DataFrame) -> Styler:
    """Aplica cores na tabela de resumo."""
    return df.style.format(precision=1).background_gradient(
        cmap="RdYlGn", subset=["Δ PRA L10"], vmin=-5, vmax=5
    )

# =========================================================
# 2. GRÁFICOS (PLOTLY)
# =========================================================

def render_player_performance_chart(player_name: str, recent_values: list, line_value: float, metric: str):
    """Gera o gráfico de barras dos últimos 10 jogos."""
    if not recent_values:
        st.warning("Sem dados históricos para este jogador.")
        return

    fig = go.Figure()
    colors = ["#22c55e" if v >= line_value else "#ef4444" for v in recent_values]
    
    fig.add_trace(go.Bar(
        x=[f"J{i+1}" for i in range(len(recent_values))],
        y=recent_values,
        marker_color=colors,
        text=recent_values,
        textposition='auto',
    ))

    fig.add_hline(y=line_value, line_dash="dash", line_color="#cbd5e1", 
                  annotation_text=f"Linha: {line_value}", annotation_position="top left")

    fig.update_layout(
        title=f"Últimos {len(recent_values)} jogos - {player_name} ({metric})",
        template="plotly_dark",
        height=300,
        margin=dict(l=20, r=20, t=40, b=20),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
    )
    st.plotly_chart(fig, use_container_width=True)

def render_metric_distribution_chart(row: pd.Series):
    """Gera o gráfico de pizza ou rosca da distribuição de PRA."""
    labels = ['Pontos', 'Rebotes', 'Assistências']
    values = [row['SEASON_PTS'], row['SEASON_REB'], row['SEASON_AST']]
    
    fig = go.Figure(data=[go.Pie(labels=labels, values=values, hole=.4)])
    fig.update_layout(
        title="Distribuição Média de PRA",
        template="plotly_dark",
        height=300,
        margin=dict(l=20, r=20, t=40, b=20),
        paper_bgcolor='rgba(0,0,0,0)',
    )
    st.plotly_chart(fig, use_container_width=True)

# =========================================================
# 3. COMPONENTES DE INTERFACE (HTML/CARDS)
# =========================================================

def render_player_card(row: pd.Series, line_metric: str, line_value: float, use_market_line: bool) -> None:
    # --- RASTREADOR DE CORES "BLINDADO" (CORREÇÃO DEFINITIVA) ---
    # Pegamos os dados brutos para evitar adivinhações por texto
    raw_team_name = str(row.get('TEAM_NAME', 'NBA'))
    raw_team_abbr = str(row.get('TEAM_ABBR', '')).strip().upper()
    
    tk = 'NBA' # Padrão
    
    # 1. Tentativa por Sigla Oficial (A mais segura: GSW é sempre GSW)
    if raw_team_abbr in NBA_TEAM_COLORS:
        tk = raw_team_abbr
    else:
        # 2. Tentativa por Nome (Exige a palavra inteira para evitar conflitos como "Nets" em "Hornets")
        search_name = f" {raw_team_name.upper()} "
        for abbr, info in NBA_TEAM_COLORS.items():
            team_key = str(info.get('name', '')).upper().strip()
            if team_key and f" {team_key} " in search_name:
                tk = abbr
                break
                
    # Puxa as cores finais baseadas na sigla encontrada (tk)
    colors = NBA_TEAM_COLORS.get(tk, {'primary': '#1d222d', 'secondary': '#ffcc00'})
    
    # --- INÍCIO DO SEU DESIGN ORIGINAL PRESERVADO ---
    with st.container(border=True):
        top_left, top_right = st.columns([1, 4])

        with top_left:
            st.image(get_player_headshot_url(int(row["PLAYER_ID"])), width=72)

        with top_right:
            # Mini Banner no topo do card com as cores do time (tk agora é GSW/CHA correto)
            st.markdown(f"""
            <div style="
                background: linear-gradient(90deg, {colors['primary']} 0%, {colors['secondary']} 250%);
                border-left: 8px solid {colors['secondary']};
                padding: 12px;
                border-radius: 8px;
                margin-bottom: 12px;
                box-shadow: 2px 4px 10px rgba(0,0,0,0.3);
            ">
                <div style="color: {colors['secondary']}; font-weight: 800; font-size: 15px; line-height: 1.1;">
                    {row['PLAYER']}
                </div>
                <div style="color: {colors['secondary']}; opacity: 0.8; font-size: 10px; margin-top: 1px;">
                    {tk} | {row.get('POSITION', '')}
                </div>
            </div>
        """, unsafe_allow_html=True)
            
            position = row["POSITION"] if str(row["POSITION"]).strip() else "-"
            st.caption(f"Pos {position} • GP {int(row['SEASON_GP'])} • MIN {format_number(row['SEASON_MIN'])}")
            st.markdown(
                render_player_headline_html(row, line_metric, line_value, use_market_line),
                unsafe_allow_html=True,
            )
            matchup_ctx = get_metric_matchup_context(row, line_metric)
            render_badges(
                row["ROLE"],
                row.get("FORM_SIGNAL", "→ Estável"),
                row.get("OSC_CLASS", "-"),
                matchup_ctx["label"],
            )

        # Tiles de suporte e informações de linha originais
        render_player_support_tiles(row, line_metric, line_value, use_market_line)
        line_context = get_line_context(row, line_metric, line_value, use_market_line=use_market_line)
        if line_context["has_market_line"] and line_context["over_dec"] and line_context["under_dec"]:
            st.caption(f"BetMGM • Linha {format_number(line_context['line_value'])} • Over {format_number(line_context['over_dec'], 2)} • Under {format_number(line_context['under_dec'], 2)}")
        else:
            st.caption("Detalhamento completo no painel abaixo.")

def render_player_cards_grid(
    filtered_df: pd.DataFrame,
    line_metric: str,
    line_value: float,
    use_market_line: bool,
    cards_per_row: int = 2,
) -> None:
    rows = [filtered_df.iloc[i:i + cards_per_row] for i in range(0, len(filtered_df), cards_per_row)]
    for row_df in rows:
        cols = st.columns(cards_per_row)
        for col_idx in range(cards_per_row):
            with cols[col_idx]:
                if col_idx < len(row_df):
                    render_player_card(row_df.iloc[col_idx], line_metric, line_value, use_market_line)


def render_summary_cards(away_df: pd.DataFrame, home_df: pd.DataFrame, min_games: int, min_minutes: int, role_filter: str):
    """Renderiza os cards de destaque do confronto (Líderes em PTS, REB e AST)."""
    combined = build_summary_cards_data(away_df, home_df, min_games, min_minutes, role_filter)
    
    if combined.empty:
        return
        
    # Título da seção atualizado
    st.markdown('<div style="margin-top: 1rem; margin-bottom: 0.8rem; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 1.5px; color: #10b981; font-weight: 800;">🔥 Líderes do Confronto (Média últimos 10 jogos)</div>', unsafe_allow_html=True)
    
    # Identifica os líderes independentes para cada estatística (baseado nos últimos 10 jogos)
    leader_pts = combined.sort_values("L10_PTS", ascending=False).iloc[0]
    leader_reb = combined.sort_values("L10_REB", ascending=False).iloc[0]
    leader_ast = combined.sort_values("L10_AST", ascending=False).iloc[0]
    
    cols = st.columns(3)
    
    # Card 1: Líder em Pontos (PTS)
    with cols[0]:
        html_pts = render_single_card(
            title="🎯 Cestinha (PTS)",
            value=leader_pts.get('PLAYER', '-'),
            meta=f"{leader_pts.get('TEAM_NAME', '-')} • {leader_pts.get('ROLE', '-')}",
            left_label="Temp",
            left_value=format_number(leader_pts.get('SEASON_PTS', 0)),
            right_label="L10",
            right_value=format_number(leader_pts.get('L10_PTS', 0)),
            right_highlight=True
        )
        st.markdown(html_pts, unsafe_allow_html=True)

    # Card 2: Líder em Rebotes (REB)
    with cols[1]:
        html_reb = render_single_card(
            title="🧱 Garrafão (REB)",
            value=leader_reb.get('PLAYER', '-'),
            meta=f"{leader_reb.get('TEAM_NAME', '-')} • {leader_reb.get('ROLE', '-')}",
            left_label="Temp",
            left_value=format_number(leader_reb.get('SEASON_REB', 0)),
            right_label="L10",
            right_value=format_number(leader_reb.get('L10_REB', 0)),
            right_highlight=True
        )
        st.markdown(html_reb, unsafe_allow_html=True)

    # Card 3: Líder em Assistências (AST)
    with cols[2]:
        html_ast = render_single_card(
            title="🏀 Maestro (AST)",
            value=leader_ast.get('PLAYER', '-'),
            meta=f"{leader_ast.get('TEAM_NAME', '-')} • {leader_ast.get('ROLE', '-')}",
            left_label="Temp",
            left_value=format_number(leader_ast.get('SEASON_AST', 0)),
            right_label="L10",
            right_value=format_number(leader_ast.get('L10_AST', 0)),
            right_highlight=True
        )
        st.markdown(html_ast, unsafe_allow_html=True)
        
def render_player_focus_panel(
    row: pd.Series,
    line_metric: str,
    line_value: float,
    use_market_line: bool,
    season: str,
    chart_mode: str,
    opp_abbr: str
) -> None:
    # --- RASTREADOR DE CORES (GSW/CHA FIX) ---
    t_name = str(row.get('TEAM_NAME', '')).upper()
    t_abbr = str(row.get('TEAM_ABBR', '')).strip().upper()
    
    tk = 'NBA'
    if t_abbr in NBA_TEAM_COLORS:
        tk = t_abbr
    else:
        for abbr, info in NBA_TEAM_COLORS.items():
            team_info_name = str(info.get('name', '')).upper()
            if team_info_name and (f" {team_info_name} " in f" {t_name} "):
                tk = abbr
                break
                
    colors = NBA_TEAM_COLORS.get(tk, {'primary': '#1d222d', 'secondary': '#ffcc00'})

    st.markdown('<div class="focus-shell">', unsafe_allow_html=True)

    top_left, top_right = st.columns([1, 5])
    with top_left:
        st.image(get_player_headshot_url(int(row["PLAYER_ID"])), width=92)

    with top_right:
        jersey = f"#{row.get('JERSEY_NUMBER')} | " if row.get('JERSEY_NUMBER') and str(row.get('JERSEY_NUMBER')).strip() else ""
        st.markdown(f"""
            <div style="
                background: linear-gradient(135deg, {colors['primary']} 0%, {colors['secondary']} 150%);
                padding: 20px; border-radius: 12px; border-left: 15px solid {colors['secondary']};
                margin-bottom: 20px; box-shadow: 0 8px 20px rgba(0,0,0,0.4);
            ">
                <h1 style="color: {colors['secondary']}; margin: 0; font-size: 32px; font-weight: 800; letter-spacing: -1px;">
                    {row['PLAYER']}
                </h1>
                <div style="color: {colors['secondary']}; opacity: 0.9; font-weight: 600; font-size: 14px;">
                    {row.get('TEAM_NAME', 'NBA')} | {jersey}{row.get('POSITION', '')}
                </div>
            </div>
        """, unsafe_allow_html=True)

    position = row["POSITION"] if str(row["POSITION"]).strip() else "-"
        st.markdown(
            f'<div class="focus-sub">Pos {position} • GP {int(row["SEASON_GP"])} • MIN {format_number(row["SEASON_MIN"])} • Time {row["TEAM_NAME"]}</div>',
            unsafe_allow_html=True,
        )
        
            
    _visual_metric = st.pills(
        "Métrica em análise detalhada", 
        ["PRA", "PTS", "REB", "AST", "3PM", "FGA", "3PA"], 
        default=line_metric, 
        key=f"v_met_{row['PLAYER_ID']}"
    )
    visual_metric = _visual_metric if _visual_metric else line_metric
    focus_matchup_ctx = get_metric_matchup_context(row, visual_metric)

    render_badges(
        row.get("ROLE", "-"),
        row.get("FORM_SIGNAL", "→ Estável"),
        row.get("OSC_CLASS", "-"),
        focus_matchup_ctx["label"],
    )
    render_focus_summary_tiles(row, visual_metric, line_value, use_market_line)

    overview_tab, detail_tab, visual_tab, market_tab = st.tabs(["Resumo", "Detalhamento", "📈 Raio-X Visual", "💰 Tendências Market"])

    with overview_tab:
        render_player_support_tiles(row, line_metric, line_value, use_market_line)
        st.markdown(render_split_detail_box_html(row, visual_metric), unsafe_allow_html=True)
        st.markdown(render_projection_detail_box_html(row), unsafe_allow_html=True)
        st.markdown(
        render_manual_line_detail_box_html(row, line_metric, line_value, use_market_line),
        unsafe_allow_html=True,
    )

    with detail_tab:
        first_cols = st.columns(2)
        second_cols = st.columns(2)

        detail_items = [
            ("PRA", row["SEASON_PRA"], row["L5_PRA"], row["L10_PRA"]),
            ("PTS", row["SEASON_PTS"], row["L5_PTS"], row["L10_PTS"]),
            ("REB", row["SEASON_REB"], row["L5_REB"], row["L10_REB"]),
            ("AST", row["SEASON_AST"], row["L5_AST"], row["L10_AST"]),
        ]

        for col, item in zip([*first_cols, *second_cols], detail_items):
            with col:
                st.markdown(
                    render_detail_metric_box_html(item[0], item[1], item[2], item[3]),
                    unsafe_allow_html=True,
                )

        extra_cols = st.columns(3)
        extra_detail_items = [
            ("3PM", row["SEASON_3PM"], row["L5_3PM"], row["L10_3PM"]),
            ("FGA", row["SEASON_FGA"], row["L5_FGA"], row["L10_FGA"]),
            ("3PA", row["SEASON_3PA"], row["L5_3PA"], row["L10_3PA"]),
        ]

        for col, item in zip(extra_cols, extra_detail_items):
            with col:
                st.markdown(
                    render_detail_metric_box_html(item[0], item[1], item[2], item[3]),
                    unsafe_allow_html=True,
                )

        st.markdown(render_matchup_detail_box_html(row), unsafe_allow_html=True)

    with visual_tab:
        render_player_chart(row["PLAYER"], int(row["PLAYER_ID"]), season, chart_mode, visual_metric)

        st.divider()

        st.markdown(f"### Frequência na Temporada — {visual_metric}")
        st.caption("Veja os montinhos: concentrados à esquerda (piso), espalhados à direita (teto).")

        log = get_player_log(int(row["PLAYER_ID"]), season)

        if not log.empty:
            log["PRA"] = log["PTS"] + log["REB"] + log["AST"]
            log["3PM"] = log.get("FG3M", 0)
            log["3PA"] = log.get("FG3A", 0)

            visual_ctx = get_line_context(row, visual_metric, line_value, use_market_line)
            active_line = float(visual_ctx["line_value"])

            log_col_map = {
                "PRA": "PRA",
                "PTS": "PTS",
                "REB": "REB",
                "AST": "AST",
                "3PM": "3PM",
                "FGA": "FGA",
                "3PA": "3PA",
            }
            active_col = log_col_map.get(visual_metric, "PRA")

            if active_col in log.columns:
                hist_data = log[active_col].dropna()

                if not hist_data.empty:
                    fig = go.Figure()
                    fig.add_trace(go.Histogram(
                        x=hist_data,
                        xbins=dict(start=-0.5, end=max(hist_data.max(), active_line) + 5, size=1),
                        marker_color="rgba(139,92,246, 0.65)",
                        marker_line_color="rgba(139,92,246, 1)",
                        marker_line_width=1.5,
                        opacity=0.9,
                        hovertemplate=f"Valor exato de {visual_metric}: %{{x}}<br>Jogos: %{{y}}<extra></extra>"
                    ))

                    line_color = "#10b981" if visual_ctx["edge"] >= 0 else "#ef4444"
                    fig.add_vline(
                        x=active_line,
                        line_dash="dash",
                        line_color=line_color,
                        line_width=3,
                        annotation_text=f"Linha ({visual_ctx['line_source']}): {active_line}",
                        annotation_position="top right",
                        annotation_font_color="#cbd5e1"
                    )

                    if not use_market_line and visual_metric != line_metric:
                        st.warning(
                            f"A linha vertical usa o valor manual da sidebar ({active_line}), "
                            f"que foi digitado para {line_metric}."
                        )

                    fig.update_layout(
                        template="plotly_dark",
                        height=380,
                        margin=dict(l=20, r=20, t=40, b=20),
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(15,23,42,0.35)",
                        bargap=0.08,
                        showlegend=False,
                        dragmode=False,
                    )
                    fig.update_xaxes(
                        title=f"{visual_metric} por jogo",
                        showgrid=True,
                        gridcolor="rgba(148,163,184,0.1)"
                    )
                    fig.update_yaxes(
                        title="Quantidade de jogos",
                        showgrid=True,
                        gridcolor="rgba(148,163,184,0.1)"
                    )

                    st.plotly_chart(fig, use_container_width=True)

                    over_count = int((hist_data > active_line).sum())
                    under_count = int((hist_data < active_line).sum())
                    push_count = int((hist_data == active_line).sum())

                    push_html = (
                        f'<span style="margin: 0 0.8rem; color: #334155;">|</span>'
                        f'<span style="color: #cbd5e1;">PUSH = {push_count}</span>'
                        if push_count > 0 else ""
                    )

                    st.markdown(
                        f"""
                        <div style="font-size: 0.95rem; margin-top: 0.35rem;">
                            <span style="color: #10b981;">OVER NA TEMPORADA = {over_count}</span>
                            <span style="margin: 0 0.8rem; color: #334155;">|</span>
                            <span style="color: #ef4444;">UNDER NA TEMPORADA = {under_count}</span>
                            {push_html}
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

                st.divider()

                st.markdown(f"### Eficiência: Minutos vs {visual_metric}")
                st.caption("Cada ponto é um jogo. A linha de tendência mostra se a produção sobe junto com os minutos.")

                scatter_df = log[["MIN", active_col, "GAME_DATE"]].copy().dropna()
                scatter_df["MATCHUP"] = log["MATCHUP"] if "MATCHUP" in log.columns else ""

                if not scatter_df.empty:
                    x = scatter_df["MIN"]
                    y = scatter_df[active_col]

                    z = np.polyfit(x, y, 1)
                    p = np.poly1d(z)
                    trend_x = np.array([x.min(), x.max()])
                    trend_y = p(trend_x)

                    line_color = "#10b981" if visual_ctx["edge"] >= 0 else "#ef4444"

                    fig_scatter = go.Figure()
                    fig_scatter.add_trace(go.Scatter(
                        x=x,
                        y=y,
                        mode="markers",
                        marker=dict(
                            size=12,
                            color=np.where(y >= active_line, "#10b981", "#ef4444"),
                            line=dict(width=1, color="#f8fafc"),
                            opacity=0.8,
                        ),
                        text=scatter_df.apply(
                            lambda r: f"Data: {r['GAME_DATE'].strftime('%d/%m')}<br>"
                                      f"Matchup: {r['MATCHUP']}<br>"
                                      f"Minutos: {r['MIN']}<br>"
                                      f"{visual_metric}: {r[active_col]}",
                            axis=1
                        ),
                        hoverinfo="text",
                        name="Jogos"
                    ))

                    fig_scatter.add_trace(go.Scatter(
                        x=trend_x,
                        y=trend_y,
                        mode="lines",
                        line=dict(color="rgba(255, 255, 255, 0.4)", width=2, dash="dot"),
                        name="Tendência",
                        hoverinfo="skip"
                    ))

                    fig_scatter.add_hline(
                        y=active_line,
                        line_dash="dash",
                        line_color=line_color,
                        line_width=2,
                        annotation_text="Linha",
                        annotation_position="bottom right"
                    )

                    fig_scatter.update_layout(
                        template="plotly_dark",
                        height=400,
                        margin=dict(l=20, r=20, t=20, b=20),
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(15,23,42,0.35)",
                        xaxis_title="Minutos Jogados",
                        yaxis_title=f"Valor de {visual_metric}",
                        showlegend=False,
                        dragmode=False
                    )
                    fig_scatter.update_xaxes(showgrid=True, gridcolor="rgba(148,163,184,0.1)")
                    fig_scatter.update_yaxes(showgrid=True, gridcolor="rgba(148,163,184,0.1)")

                    st.plotly_chart(fig_scatter, use_container_width=True)

                    correl = x.corr(y)
                    if correl > 0.7:
                        st.success(f"📈 Alta Correlação ({correl:.2f}): produção muito dependente dos minutos.")
                    elif correl > 0.4:
                        st.info(f"📊 Correlação Moderada ({correl:.2f}): minutos ajudam, mas não explicam tudo.")
                    else:
                        st.warning(f"📉 Baixa Correlação ({correl:.2f}): produção mais oscilante, pouco dependente dos minutos.")
            else:
                st.info(f"Dados indisponíveis para a métrica {visual_metric}.")
        else:
            st.info("Sem histórico suficiente para gerar os gráficos.")
    
    # --- ABA DE MERCADO DINÂMICA (FORMATO 22,5 CORRIGIDO) ---
    with market_tab:
        st.markdown(f"### ⚔️ Histórico de Confronto (H2H) — Foco em {visual_metric}")
        
        v_ctx = get_line_context(row, visual_metric, line_value, use_market_line)
        active_line = float(v_ctx['line_value'])
        
        current_opp = opp_abbr
        for abbr, info in NBA_TEAM_COLORS.items():
            team_info_name = str(info.get('name', '')).upper()
            if team_info_name in str(current_opp).upper():
                current_opp = abbr
                break
        
        log = get_player_log(int(row["PLAYER_ID"]), season)
        
        if not log.empty:
            log['PRA'] = log['PTS'] + log['REB'] + log['AST']
            log['3PM'] = log.get('FG3M', 0)
            log['3PA'] = log.get('FG3A', 0)
            
            h2h_log = log[log['MATCHUP'].str.contains(current_opp, case=False, na=False)].copy()
            
            if not h2h_log.empty:
                target_col = visual_metric
                h2h_display = h2h_log[['GAME_DATE', 'MATCHUP', 'WL', 'MIN', target_col]].copy()
                h2h_display['Data'] = h2h_display['GAME_DATE'].dt.strftime('%d/%m/%Y')
                h2h_display['Linha'] = active_line
                
                real_col_name = f'Real ({visual_metric})'
                h2h_display = h2h_display.rename(columns={
                    'MATCHUP': 'Confronto',
                    'WL': 'Res',
                    'MIN': 'Min',
                    target_col: real_col_name
                })
                
                final_table = h2h_display[['Data', 'Confronto', 'Res', 'Min', 'Linha', real_col_name]]
                
                def style_hit_miss(val):
                    try:
                        num = float(val)
                        if num >= active_line:
                            return 'background-color: rgba(34,197,94,0.15); color: #86efac; font-weight: bold; border-left: 4px solid #22c55e;'
                        else:
                            return 'background-color: rgba(239,68,68,0.15); color: #fca5a5; font-weight: bold; border-left: 4px solid #ef4444;'
                    except:
                        return ''

                st.write(f"Comparando histórico com a linha atual: **{active_line:.1f}** ({v_ctx['line_source']})")
                
                # CORREÇÃO DA VÍRGULA E CASAS DECIMAIS:
                # O format abaixo força 1 casa decimal e troca o ponto por vírgula
                styled_df = final_table.style.format({
                    'Linha': lambda x: f"{x:.1f}".replace('.', ','),
                    real_col_name: lambda x: f"{x:.1f}".replace('.', ',')
                }).map(style_hit_miss, subset=[real_col_name])
                
                st.dataframe(styled_df, use_container_width=True, hide_index=True)
                
                hits = (h2h_log[target_col] >= active_line).sum()
                total = len(h2h_log)
                pct = int((hits / total) * 100) if total > 0 else 0
                
                st.markdown(f"""
                    <div style="padding: 15px; background: rgba(15,23,42,0.6); border-radius: 10px; border-left: 5px solid #38bdf8; margin-top: 10px;">
                        🎯 <b>Taxa de Acerto vs {current_opp}:</b> {hits}/{total} ({pct}%) 
                        <br><small style="opacity: 0.8;">Jogador cumpriu a linha de <b>{active_line:.1f} {visual_metric}</b> em {hits} dos últimos {total} jogos contra este time.</small>
                    </div>
                """, unsafe_allow_html=True)
            else:
                st.info(f"Nenhum jogo de {row['PLAYER']} contra {current_opp} encontrado nesta temporada.")
        else:
            st.error("Erro ao carregar log do jogador.")            
def render_team_section_v2(
    team_name: str,
    team_df: pd.DataFrame,
    season: str,
    min_games: int,
    min_minutes: int,
    role_filter: str,
    sort_label: str,
    ascending: bool,
    chart_mode: str,
    line_metric: str,
    line_value: float,
    use_market_line: bool,
    cards_per_row: int,
    opp_abbr
) -> None:
    if team_df.empty:
        st.warning(f"Não encontrei dados para {team_name}.")
        return

    sort_column = SORT_OPTIONS[sort_label]
    filtered_df = filter_and_sort_team_df(team_df, min_games, min_minutes, role_filter, sort_column, ascending)
    if filtered_df.empty:
        st.info("Nenhum jogador desse time passou pelos filtros atuais.")
        return

    st.markdown(f"### {team_name}")

    line_info_text = (
        f"Linha ativa: {line_metric} • mercado por jogador (BetMGM quando houver)"
        if use_market_line
        else f"Linha ativa: {line_metric} {format_number(line_value)} • manual"
    )

    st.markdown(
        f'<div><span class="info-pill">Jogadores: {len(filtered_df)}</span><span class="info-pill">{line_info_text}</span><span class="info-pill">Modo mercado: {"BetMGM quando houver" if use_market_line else "Manual"}</span></div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="section-note">Cards curtos no topo e painel detalhado do jogador sob demanda.</div>',
        unsafe_allow_html=True,
    )

    render_player_cards_grid(
        filtered_df,
        line_metric=line_metric,
        line_value=line_value,
        use_market_line=use_market_line,
        cards_per_row=cards_per_row,
    )

    options = filtered_df[["PLAYER", "PLAYER_ID"]].drop_duplicates()
    player_name = st.selectbox(
        f"Jogador em foco — {team_name}",
        options["PLAYER"].tolist(),
        key=f"player_focus_v2_{team_name}",
    )

    show_focus_panel = st.toggle(
        f"Mostrar análise detalhada — {team_name}",
        value=False,
        key=f"show_focus_panel_{team_name}",
    )

    if show_focus_panel:
        selected_row = filtered_df.loc[filtered_df["PLAYER"] == player_name].iloc[0]
        render_player_focus_panel(
            selected_row,
            line_metric,
            line_value,
            use_market_line,
            season,
            chart_mode,
            opp_abbr,
        )

    st.divider()
    show_injury = st.toggle(f"🏥 Carregar Status Oficial — {team_name}", key=f"ir_toggle_{team_name}")

    if show_injury:
        with st.spinner("Buscando PDF oficial da NBA..."):
            injury_df = fetch_latest_injury_report_df()
            team_id = next((tid for tid, t in TEAM_LOOKUP.items() if t.get("full_name") == team_name), 0)
            
            enriched_team_df = merge_injury_report(
                team_df=team_df,
                injury_df=injury_df,
                team_name=team_name,
                team_id=team_id
            )
            
            ir_tab, lineup_tab = st.tabs(["Status Oficial", "Rotação Atualizada"])
            with ir_tab: render_injury_report_tab(enriched_team_df, team_name)
            with lineup_tab: render_lineup_report_tab(enriched_team_df, team_name)


def render_injury_report_tab(team_df: pd.DataFrame, team_name: str) -> None:
    st.markdown(
        '<div class="section-note">Status oficial do injury report da NBA para o elenco do time.</div>',
        unsafe_allow_html=True,
    )

    if "INJ_STATUS" not in team_df.columns:
        st.info("Injury report ainda não integrado nesta execução.")
        return

    report_url = ""
    if "INJ_REPORT_URL" in team_df.columns:
        valid_urls = team_df["INJ_REPORT_URL"].dropna().astype(str)
        valid_urls = valid_urls[valid_urls.str.strip() != ""]
        if not valid_urls.empty:
            report_url = valid_urls.iloc[0]

    report_meta = parse_injury_report_timestamp_from_url(report_url)

    top_cols = st.columns([1.4, 1.2, 1.4])
    with top_cols[0]:
        st.caption(f"PDF oficial: {report_meta['report_label_et']}")
    with top_cols[1]:
        st.caption(f"Brasília: {report_meta['report_label_brt']}")
    with top_cols[2]:
        if report_url:
            st.caption("Fonte oficial carregada")
        else:
            st.caption("Fonte oficial não identificada")

    if "INJ_MATCHUP_FOUND" in team_df.columns and not bool(team_df["INJ_MATCHUP_FOUND"].any()):
        st.warning("Não encontrei linhas do injury report oficial para este matchup. O app não deve assumir disponibilidade oficial aqui.")

    report_df = team_df[["PLAYER", "INJ_STATUS", "INJ_REASON"]].copy()
    report_df = report_df.rename(
        columns={
            "PLAYER": "Jogador",
            "INJ_STATUS": "Status oficial",
            "INJ_REASON": "Motivo",
        }
    )

    st.dataframe(report_df, use_container_width=True)

    unavailable = team_df[team_df["INJ_STATUS"].isin(["Out", "Doubtful"])]
    if not unavailable.empty:
        st.warning(
            f"{len(unavailable)} jogador(es) marcados como indisponíveis e que devem sair da leitura da provável escalação."
        )


def render_lineup_report_tab(team_df: pd.DataFrame, team_name: str) -> None:
    st.markdown(
        '<div class="section-note">Estrutura de rotação do time já filtrada por indisponibilidade oficial quando o injury report estiver carregado.</div>',
        unsafe_allow_html=True,
    )

    lineup_df = team_df.copy()
    if "IS_UNAVAILABLE" in lineup_df.columns:
        lineup_df = lineup_df[~lineup_df["IS_UNAVAILABLE"]].copy()

    starters = lineup_df[lineup_df["ROLE"] == "Titular provável"].copy()
    bench = lineup_df[lineup_df["ROLE"] != "Titular provável"].copy()

    top_cols = st.columns([1.2, 1.2, 1.6])
    with top_cols[0]:
        st.markdown(
            render_single_card(
                "Titulares prováveis",
                str(len(starters)),
                team_name,
                "Média MIN",
                format_number(starters["SEASON_MIN"].mean() if not starters.empty else 0),
                "PRA médio",
                format_number(starters["SEASON_PRA"].mean() if not starters.empty else 0),
            ),
            unsafe_allow_html=True,
        )
    with top_cols[1]:
        st.markdown(
            render_single_card(
                "Reservas",
                str(len(bench)),
                team_name,
                "Média MIN",
                format_number(bench["SEASON_MIN"].mean() if not bench.empty else 0),
                "PRA médio",
                format_number(bench["SEASON_PRA"].mean() if not bench.empty else 0),
            ),
            unsafe_allow_html=True,
        )
    with top_cols[2]:
        st.info("Jogadores Out/Doubtful saem automaticamente da leitura quando o injury report oficial estiver carregado.")

    st.markdown("#### Provável escalação")
    starter_view = (
        starters[["PLAYER", "POSITION", "SEASON_MIN", "SEASON_PRA", "L10_PRA", "TREND", "INJ_STATUS"]].copy()
        if not starters.empty
        else pd.DataFrame(columns=["PLAYER", "POSITION", "SEASON_MIN", "SEASON_PRA", "L10_PRA", "TREND", "INJ_STATUS"])
    )
    starter_view = starter_view.rename(
        columns={
            "PLAYER": "Jogador",
            "POSITION": "Pos",
            "SEASON_MIN": "MIN",
            "SEASON_PRA": "PRA Temp",
            "L10_PRA": "PRA L10",
            "TREND": "Trend",
            "INJ_STATUS": "Status",
        }
    )
    st.dataframe(style_table(starter_view, quick_view=True), use_container_width=True)

    st.markdown("#### Rotação / banco")
    bench_view = (
        bench[["PLAYER", "POSITION", "SEASON_MIN", "SEASON_PRA", "L10_PRA", "TREND", "INJ_STATUS"]].copy()
        if not bench.empty
        else pd.DataFrame(columns=["PLAYER", "POSITION", "SEASON_MIN", "SEASON_PRA", "L10_PRA", "TREND", "INJ_STATUS"])
    )
    bench_view = bench_view.rename(
        columns={
            "PLAYER": "Jogador",
            "POSITION": "Pos",
            "SEASON_MIN": "MIN",
            "SEASON_PRA": "PRA Temp",
            "L10_PRA": "PRA L10",
            "TREND": "Trend",
            "INJ_STATUS": "Status",
        }
    )
    st.dataframe(style_table(bench_view, quick_view=True), use_container_width=True)

def get_team_logo_url(team_id: int) -> str:
    return TEAM_LOGO_URL.format(team_id=team_id)


def get_player_headshot_url(player_id: int) -> str:
    return PLAYER_HEADSHOT_URL.format(player_id=player_id)


def format_number(value, decimals: int = 1) -> str:
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def format_signed_number(value, decimals: int = 1) -> str:
    try:
        value = float(value)
        return f"{value:+.{decimals}f}"
    except (TypeError, ValueError):
        return "-"



def get_matchup_parts(matchup: str) -> tuple[str, str]:
    if not isinstance(matchup, str) or matchup.strip() == "":
        return "", ""

    cleaned = matchup.replace("vs.", "vs").strip()
    parts = cleaned.split()
    if len(parts) < 3:
        return "", ""

    venue = "vs" if "vs" in parts else "@"
    opponent_abbr = parts[-1].strip().upper()
    return venue, opponent_abbr

def style_delta(val) -> str:
    try:
        value = float(val)
    except (TypeError, ValueError):
        return ""
    if value > 0:
        return "background-color: rgba(34,197,94,0.12); color: #dcfce7; font-weight: 600;"
    if value < 0:
        return "background-color: rgba(239,68,68,0.12); color: #fee2e2; font-weight: 600;"
    return "color: #cbd5e1;"


def style_trend(val) -> str:
    if val in ["🔥 Forte", "⬆️ Boa"]:
        return "background-color: rgba(34,197,94,0.10); color: #dcfce7; font-weight: 700;"
    if val in ["🥶 Queda", "⬇️ Fraca"]:
        return "background-color: rgba(239,68,68,0.10); color: #fee2e2; font-weight: 700;"
    return "background-color: rgba(148,163,184,0.10); color: #e2e8f0; font-weight: 600;"


def style_role(val) -> str:
    if "Titular" in str(val):
        return "background-color: rgba(139,92,246,0.12); color: #f3e8ff; font-weight: 700;"
    if "Reserva" in str(val):
        return "background-color: rgba(148,163,184,0.08); color: #cbd5e1; font-weight: 600;"
    return ""


def style_pra(val) -> str:
    return "background-color: rgba(139,92,246,0.10); color: #f5f3ff; font-weight: 700;"


def style_matchup(val) -> str:
    if "Favorável" in str(val):
        return "background-color: rgba(34,197,94,0.10); color: #dcfce7; font-weight: 700;"
    if "Difícil" in str(val):
        return "background-color: rgba(239,68,68,0.10); color: #fee2e2; font-weight: 700;"
    return "background-color: rgba(148,163,184,0.10); color: #e2e8f0; font-weight: 600;"


def style_signal(val) -> str:
    if "↗" in str(val):
        return "color: #86efac; font-weight: 700;"
    if "↘" in str(val):
        return "color: #fca5a5; font-weight: 700;"
    return "color: #e2e8f0; font-weight: 600;"


def style_oscillation(val) -> str:
    if "Baixa" in str(val):
        return "color: #86efac; font-weight: 700;"
    if "Alta" in str(val):
        return "color: #fca5a5; font-weight: 700;"
    return "color: #fcd34d; font-weight: 700;"


def style_hit_rate(val) -> str:
    text = str(val)
    if "/" not in text:
        return ""
    try:
        hit, sample = text.split("/")
        ratio = float(hit) / max(float(sample), 1.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return ""

    if ratio >= 0.7:
        return "background-color: rgba(34,197,94,0.10); color: #dcfce7; font-weight: 700;"
    if ratio <= 0.4:
        return "background-color: rgba(239,68,68,0.10); color: #fee2e2; font-weight: 700;"
    return "background-color: rgba(148,163,184,0.10); color: #e2e8f0; font-weight: 600;"
def style_table(df: pd.DataFrame, quick_view: bool) -> Styler:
    text_cols = {
        "Jogador",
        "Pos",
        "Papel",
        "Trend",
        "Matchup",
        "Hit PRA",
        "Hit PTS",
        "Hit REB",
        "Hit AST",
        "Hit 3PM",
        "Hit FGA",
        "Hit 3PA",
        "Oscilação",
        "Sinal",
        "Status",
        "Status oficial",
        "Motivo",
        "Última atualização",
    }

    format_map = {}
    for col in df.columns:
        if col in text_cols:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            format_map[col] = "{:.0f}" if col == "GP" else "{:.1f}"

    styler = df.style.format(format_map, na_rep="-")

    pra_cols = [c for c in ["PRA Temp", "PRA L5", "PRA L10", "PRA adv pos", "Liga pos"] if c in df.columns]
    delta_cols = [c for c in ["Δ PRA L5", "Δ PRA L10"] if c in df.columns]
    hit_cols = [c for c in ["Hit PRA", "Hit PTS", "Hit REB", "Hit AST", "Hit 3PM", "Hit FGA", "Hit 3PA"] if c in df.columns]
    center_cols = [
        c for c in [
            "Papel",
            "GP",
            "MIN",
            "Trend",
            "Matchup",
            "Oscilação",
            "Sinal",
            "Hit PRA",
            "Hit PTS",
            "Hit REB",
            "Hit AST",
            "Hit 3PM",
            "Hit FGA",
            "Hit 3PA",
            "Status",
        ] if c in df.columns
    ]

    if pra_cols:
        styler = styler.map(style_pra, subset=pra_cols)
    if delta_cols:
        styler = styler.map(style_delta, subset=delta_cols)
    if hit_cols:
        styler = styler.map(style_hit_rate, subset=hit_cols)
    if "Trend" in df.columns:
        styler = styler.map(style_trend, subset=["Trend"])
    if "Papel" in df.columns:
        styler = styler.map(style_role, subset=["Papel"])
    if "Matchup" in df.columns:
        styler = styler.map(style_matchup, subset=["Matchup"])
    if "Sinal" in df.columns:
        styler = styler.map(style_signal, subset=["Sinal"])
    if "Oscilação" in df.columns:
        styler = styler.map(style_oscillation, subset=["Oscilação"])
    if "Jogador" in df.columns:
        styler = styler.set_properties(subset=["Jogador"], **{"font-weight": "700"})
    if center_cols:
        styler = styler.set_properties(subset=center_cols, **{"text-align": "center"})
    if quick_view:
        quick_cols = [c for c in ["PRA Temp", "PRA L10", "Δ PRA L10"] if c in df.columns]
        if quick_cols:
            styler = styler.set_properties(subset=quick_cols, **{"font-weight": "700"})

    return styler
    
def render_matchup_header(game_row: pd.Series) -> None:
    away_team_id = int(game_row["VISITOR_TEAM_ID"])
    home_team_id = int(game_row["HOME_TEAM_ID"])

    st.markdown('<div class="matchup-shell">', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1.4, 0.9, 1.4])

    with c1:
        st.image(get_team_logo_url(away_team_id), width=96)
        st.markdown(f'<div class="team-title">{game_row["away_team_name"]}</div>', unsafe_allow_html=True)
        st.markdown('<div class="team-sub">Visitante</div>', unsafe_allow_html=True)

    with c2:
        st.markdown('<div class="center-vs">VS</div>', unsafe_allow_html=True)
        # CONVERSÃO DE HORÁRIO AQUI DENTRO:
        status_display = get_game_datetime_brasilia(game_row["GAME_STATUS_TEXT"])
        st.markdown(
            f'<div style="text-align:center; margin-top:0.7rem;"><span class="status-chip">{status_display}</span></div>',
            unsafe_allow_html=True,
        )

    with c3:
        st.image(get_team_logo_url(home_team_id), width=96)
        st.markdown(f'<div class="team-title">{game_row["home_team_name"]}</div>', unsafe_allow_html=True)
        st.markdown('<div class="team-sub">Mandante</div>', unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)

def render_single_card(
    title: str,
    value: str,
    meta: str,
    left_label: str,
    left_value: str,
    right_label: str,
    right_value: str,
    right_highlight: bool = True,
) -> str:
    right_class = "detail-mini detail-mini-highlight" if right_highlight else "detail-mini"
    return f"""
    <div class="summary-card">
        <div class="summary-label">{title}</div>
        <div class="summary-value">{value}</div>
        <div class="summary-meta">{meta}</div>
        <div class="detail-mini-grid" style="margin-top:0.75rem; grid-template-columns: repeat(2, minmax(0, 1fr));">
            <div class="detail-mini">
                <div class="detail-mini-label">{left_label}</div>
                <div class="detail-mini-value">{left_value}</div>
            </div>
            <div class="{right_class}">
                <div class="detail-mini-label">{right_label}</div>
                <div class="detail-mini-value">{right_value}</div>
            </div>
        </div>
    </div>
    """


def render_compact_ranking_html(rank_df: pd.DataFrame, mode: str) -> str:
    rows_html = []
    for idx, (_, row) in enumerate(rank_df.iterrows(), start=1):
        if mode == "projection":
            stat1_label, stat1_value = "Proj", format_number(row["RANK_PROJ"])
            stat2_label, stat2_value = "Hit", row["RANK_HIT_HTML"]
            stat3_label, stat3_value = "Match", row["MATCHUP_LABEL"]
            stat3_class = "ranking-good" if row["MATCHUP_LABEL"] == "Favorável" else ("ranking-bad" if row["MATCHUP_LABEL"] == "Difícil" else "")
        elif mode == "edge":
            stat1_label, stat1_value = "Edge", format_signed_number(row["RANK_EDGE"])
            stat2_label, stat2_value = "Linha", row["RANK_LINE_HTML"]
            stat3_label, stat3_value = "Proj", format_number(row["RANK_PROJ"])
            stat3_class = ""
        else:
            stat1_label, stat1_value = "Hit", row["RANK_HIT_HTML"]
            stat2_label, stat2_value = "Osc", row["OSC_CLASS"]
            stat3_label, stat3_value = "Proj", format_number(row["RANK_PROJ"])
            stat3_class = ""

        stat1_class = "ranking-good" if mode == "edge" and row["RANK_EDGE"] > 0.75 else ("ranking-bad" if mode == "edge" and row["RANK_EDGE"] < -0.75 else "")
        stat2_class = "ranking-good" if mode == "consistency" and row["OSC_CLASS"] == "Baixa" else ("ranking-bad" if mode == "consistency" and row["OSC_CLASS"] == "Alta" else "")

        row_html = (
            f'<div class="ranking-row">'
            f'<div class="ranking-rank">{idx}</div>'
            f'<div><div class="ranking-player">{row["PLAYER"]}</div><div class="ranking-sub">{row["TEAM_NAME"]} • {row["ROLE"]}</div></div>'
            f'<div class="ranking-stat {stat1_class}"><div class="ranking-stat-label">{stat1_label}</div><div class="ranking-stat-value">{stat1_value}</div></div>'
            f'<div class="ranking-stat {stat2_class}"><div class="ranking-stat-label">{stat2_label}</div><div class="ranking-stat-value">{stat2_value}</div></div>'
            f'<div class="ranking-stat {stat3_class}"><div class="ranking-stat-label">{stat3_label}</div><div class="ranking-stat-value">{stat3_value}</div></div>'
            f'</div>'
        )
        rows_html.append(row_html)

    return f'<div class="ranking-shell">{"".join(rows_html)}</div>'


def render_game_rankings(
    away_df: pd.DataFrame,
    home_df: pd.DataFrame,
    min_games: int,
    min_minutes: int,
    role_filter: str,
    line_metric: str,
    line_value: float,
    use_market_line: bool,
) -> None:
    combined = build_summary_cards_data(away_df, home_df, min_games, min_minutes, role_filter)
    if combined.empty:
        return

    projection_col = get_metric_projection_column(line_metric)

    def parse_ratio_text(text: str) -> float:
        try:
            hit, sample = str(text).split("/")
            sample_n = max(float(sample), 1.0)
            return float(hit) / sample_n
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0

    rank_df = combined.copy()

    rank_df["RANK_PROJ"] = pd.to_numeric(rank_df[projection_col], errors="coerce").fillna(0.0)

    rank_df["LINE_CONTEXT"] = rank_df.apply(
        lambda row: get_line_context(
            row,
            line_metric,
            line_value,
            use_market_line=use_market_line,
        ),
        axis=1,
    )

    rank_df["RANK_LINE"] = rank_df["LINE_CONTEXT"].apply(lambda ctx: float(ctx.get("line_value", 0.0)))
    rank_df["RANK_EDGE"] = rank_df["LINE_CONTEXT"].apply(lambda ctx: float(ctx.get("edge", 0.0)))
    rank_df["RANK_HIT_TEXT"] = rank_df["LINE_CONTEXT"].apply(lambda ctx: ctx.get("hit_l10", "-"))
    rank_df["RANK_HIT_HTML"] = rank_df["LINE_CONTEXT"].apply(lambda ctx: ctx.get("hit_l10_html", "-"))
    rank_df["RANK_LINE_HTML"] = rank_df["LINE_CONTEXT"].apply(lambda ctx: f'<span title="{ctx.get("tooltip", "")}" style="cursor:help;">{format_number(ctx.get("line_value", 0.0))} {ctx.get("icon", "")}</span>')
    rank_df["RANK_HIT_RATE"] = rank_df["RANK_HIT_TEXT"].apply(parse_ratio_text)

    if "LINE_CONTEXT" in rank_df.columns:
        rank_df = rank_df.drop(columns=["LINE_CONTEXT"])

    proj_df = rank_df.sort_values(
        ["RANK_PROJ", "RANK_HIT_RATE"],
        ascending=[False, False],
    ).head(5)

    edge_df = rank_df.sort_values(
        ["RANK_EDGE", "RANK_HIT_RATE"],
        ascending=[False, False],
    ).head(5)

    consistency_df = rank_df.sort_values(
        ["RANK_HIT_RATE", "OSC_L10", "RANK_PROJ"],
        ascending=[False, True, False],
    ).head(5)

    st.subheader(f"Ranking do confronto — {line_metric}")
    st.caption("Bloco compacto para leitura rápida, usando BetMGM quando houver linha disponível.")

    tab_proj, tab_edge, tab_cons = st.tabs(["Projeção", "Edge da linha", "Consistência"])

    with tab_proj:
        st.markdown(render_compact_ranking_html(proj_df, mode="projection"), unsafe_allow_html=True)

    with tab_edge:
        st.markdown(render_compact_ranking_html(edge_df, mode="edge"), unsafe_allow_html=True)

    with tab_cons:
        st.markdown(render_compact_ranking_html(consistency_df, mode="consistency"), unsafe_allow_html=True)

def render_player_chart(player_name: str, player_id: int, season: str, chart_mode: str, visual_metric: str) -> None:
    log = get_player_log(player_id, season)
    if log.empty:
        st.info("Sem histórico suficiente para esse jogador.")
        return

    # Garante que puxa o FGA também do banco de dados
    needed_cols = ["GAME_DATE", "PTS", "REB", "AST", "FGA", "FG3M", "FG3A"]
    if "MATCHUP" in log.columns:
        needed_cols.append("MATCHUP")

    recent = log[[c for c in needed_cols if c in log.columns]].copy()
    recent = recent.dropna(subset=["GAME_DATE", "PTS", "REB", "AST"]).sort_values("GAME_DATE")
    if recent.empty:
        st.info("Sem histórico suficiente para esse jogador.")
        return

    # Padroniza as siglas para bater com o resto do App
    recent["PRA"] = recent["PTS"] + recent["REB"] + recent["AST"]
    recent["3PM"] = recent.get("FG3M", 0)
    recent["3PA"] = recent.get("FG3A", 0)
    if "FGA" not in recent.columns:
        recent["FGA"] = 0

    if "MATCHUP" in recent.columns:
        matchup_parts = recent["MATCHUP"].apply(get_matchup_parts)
        recent["VENUE"] = matchup_parts.apply(lambda x: x[0])
        recent["OPP_ABBR"] = matchup_parts.apply(lambda x: x[1])
    else:
        recent["VENUE"] = ""
        recent["OPP_ABBR"] = ""

    recent["SHORT_LABEL"] = recent.apply(
        lambda row: f'{row["GAME_DATE"].strftime("%m/%d")}<br>{row["VENUE"]} {row["OPP_ABBR"]}'.strip() if row["OPP_ABBR"] else row["GAME_DATE"].strftime("%m/%d"),
        axis=1,
    )

    top_left, top_right = st.columns([1, 6])
    with top_left:
        st.image(get_player_headshot_url(int(player_id)), width=82)
    with top_right:
        st.markdown(f"### Últimos jogos — {player_name}")
        st.caption("Visual compacto: barras, últimos 5 jogos." if chart_mode == "Compacto" else "Visual completo: linha contínua, últimos 10 jogos.")

    if chart_mode == "Compacto":
        recent_view = recent.tail(5).copy()

        fig = go.Figure(
            go.Bar(
                x=recent_view["SHORT_LABEL"],
                y=recent_view[visual_metric],
                text=recent_view[visual_metric].round(1),
                textposition="outside",
                marker=dict(color="#4ade80"),
                hovertemplate=f"{visual_metric}: %{{y:.1f}}<extra></extra>",
            )
        )
        fig.update_layout(
            template="plotly_dark",
            height=360,
            margin=dict(l=20, r=20, t=10, b=20),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(15,23,42,0.35)",
            showlegend=False,
            bargap=0.28,
            dragmode=False,
        )
        fig.update_xaxes(title="", type="category", tickangle=0, showgrid=False, tickfont=dict(size=11))
        fig.update_yaxes(title="", showgrid=True, gridcolor="rgba(148,163,184,0.15)", zeroline=False)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"{visual_metric} • Média na Temp: {recent[visual_metric].mean():.1f} | Média no L5: {recent_view[visual_metric].mean():.1f}")
    else:
        recent_view = recent.tail(10).copy()
        fig = go.Figure()
        
        # Agora o Completo foca em 1 linha só, para não virar bagunça!
        fig.add_trace(
            go.Scatter(
                x=recent_view["SHORT_LABEL"],
                y=recent_view[visual_metric],
                mode="lines+markers+text",
                name=visual_metric,
                text=recent_view[visual_metric].round(1),
                textposition="top center",
                line=dict(width=4, color="#38bdf8"),
                marker=dict(size=8),
                opacity=1.0,
                hovertemplate=f"{visual_metric}: %{{y:.1f}}<extra></extra>",
            )
        )

        fig.update_layout(
            template="plotly_dark",
            height=400,
            margin=dict(l=20, r=20, t=10, b=20),
            showlegend=False,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(15,23,42,0.35)",
            hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155", font=dict(color="#f8fafc", size=13)),
            dragmode=False,
        )
        fig.update_xaxes(title="", type="category", tickangle=0, showgrid=False, tickfont=dict(size=11))
        fig.update_yaxes(title="", showgrid=True, gridcolor="rgba(148,163,184,0.15)", zeroline=False)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"{visual_metric} • Média na Temp: {recent[visual_metric].mean():.1f} | Média no L10: {recent_view[visual_metric].mean():.1f}")
def render_badges(role: str, momentum: str, oscillation: str, matchup: str) -> None:
    role_class = "badge-starter" if role == "Titular provável" else "badge-bench"

    if str(momentum).startswith(("↗", "↑")):
        momentum_class = "badge-good"
    elif str(momentum).startswith(("↘", "↓")):
        momentum_class = "badge-bad"
    else:
        momentum_class = "badge-neutral"

    if oscillation == "Baixa":
        oscillation_class = "badge-good"
    elif oscillation == "Alta":
        oscillation_class = "badge-bad"
    else:
        oscillation_class = "badge-neutral"

    if matchup == "Favorável":
        matchup_class = "badge-good"
    elif matchup == "Difícil":
        matchup_class = "badge-bad"
    else:
        matchup_class = "badge-neutral"

    st.markdown(
        f"""
        <div class="badge-row">
            <span class="badge {role_class}">{role}</span>
            <span class="badge {momentum_class}">{momentum}</span>
            <span class="badge {oscillation_class}">Osc {oscillation}</span>
            <span class="badge {matchup_class}">Matchup {matchup}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def build_delta_pill_html(label: str, value: float) -> str:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        numeric_value = 0.0

    if numeric_value > 0.3:
        css_class = "delta-up"
    elif numeric_value < -0.3:
        css_class = "delta-down"
    else:
        css_class = "delta-flat"

    return f'<span class="delta-pill {css_class}">{label} {format_signed_number(numeric_value)}</span>'


def render_detail_metric_box_html(title: str, temp_val: float, l5_val: float, l10_val: float) -> str:
    delta_l5 = l5_val - temp_val
    delta_l10 = l10_val - temp_val
    return f"""
    <div class="detail-box">
        <div class="detail-box-top">
            <div class="detail-box-title">{title}</div>
            <div class="delta-pill-row">
                {build_delta_pill_html('Δ L5', delta_l5)}
                {build_delta_pill_html('Δ L10', delta_l10)}
            </div>
        </div>
        <div class="detail-mini-grid">
            <div class="detail-mini">
                <div class="detail-mini-label">Temp</div>
                <div class="detail-mini-value">{format_number(temp_val)}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">L5</div>
                <div class="detail-mini-value">{format_number(l5_val)}</div>
            </div>
            <div class="detail-mini detail-mini-highlight">
                <div class="detail-mini-label">L10</div>
                <div class="detail-mini-value">{format_number(l10_val)}</div>
            </div>
        </div>
    </div>
    """

def _parse_ratio_text(text: str) -> float:
    try:
        hit, sample = str(text).split("/")
        hit = float(hit)
        sample = max(float(sample), 1.0)
        return hit / sample
    except Exception:
        return 0.0


def _confidence_label_and_score(
    edge: float,
    hit_ratio: float,
    osc_class: str,
    matchup_label: str,
    form_signal: str,
    inj_status: str,
) -> tuple[str, int]:
    score = 0

    if edge >= 2.5:
        score += 3
    elif edge >= 1.0:
        score += 2
    elif edge >= 0.3:
        score += 1
    elif edge <= -2.0:
        score -= 3
    elif edge <= -0.8:
        score -= 2

    if hit_ratio >= 0.70:
        score += 2
    elif hit_ratio >= 0.55:
        score += 1
    elif hit_ratio <= 0.40:
        score -= 2

    if osc_class == "Baixa":
        score += 1
    elif osc_class == "Alta":
        score -= 1

    if matchup_label == "Favorável":
        score += 1
    elif matchup_label == "Difícil":
        score -= 1

    if "↗" in str(form_signal):
        score += 1
    elif "↘" in str(form_signal):
        score -= 1

    if str(inj_status) in {"Out", "Doubtful"}:
        score -= 3
    elif str(inj_status) in {"Questionable"}:
        score -= 1

    if score >= 5:
        return "🔥 Confiança Alta", score
    if score >= 2:
        return "🟡 Confiança Média", score
    return "🔴 Confiança Baixa", score


def _best_metric_for_card(row: pd.Series, line_metric: str, line_value: float, use_market_line: bool) -> tuple[str, dict]:
    metrics = ["PRA", "PTS", "REB", "AST", "3PM", "FGA", "3PA"]

    if not use_market_line:
        chosen_ctx = get_line_context(row, line_metric, line_value, use_market_line=False)
        return line_metric, chosen_ctx

    best_metric = line_metric
    best_ctx = get_line_context(row, line_metric, line_value, use_market_line=True)
    best_score = -999.0

    for metric in metrics:
        ctx = get_line_context(row, metric, line_value, use_market_line=True)

        if not ctx.get("has_market_line"):
            continue

        hit_ratio = _parse_ratio_text(ctx.get("hit_l10", "0/1"))
        score = (float(ctx.get("edge", 0.0)) * 0.65) + (hit_ratio * 4.0)

        if score > best_score:
            best_score = score
            best_metric = metric
            best_ctx = ctx

    return best_metric, best_ctx


def _build_headline_reason(row: pd.Series, metric: str, ctx: dict) -> tuple[str, str]:
    hit_ratio = _parse_ratio_text(ctx.get("hit_l10", "0/1"))
    confidence_label, score = _confidence_label_and_score(
        edge=float(ctx.get("edge", 0.0)),
        hit_ratio=hit_ratio,
        osc_class=str(row.get("OSC_CLASS", "-")),
        matchup_label=str(row.get("MATCHUP_LABEL", "Neutro")),
        form_signal=str(row.get("FORM_SIGNAL", "→ Estável")),
        inj_status=str(row.get("INJ_STATUS", "Available")),
    )

    edge = float(ctx.get("edge", 0.0))
    matchup = str(row.get("MATCHUP_LABEL", "Neutro"))
    osc = str(row.get("OSC_CLASS", "-"))
    form_signal = str(row.get("FORM_SIGNAL", "→ Estável"))

    if score >= 5:
        headline = f"Vale ficar de olho em {metric}"
    elif score >= 2:
        headline = f"Sinal moderado para {metric}"
    else:
        headline = f"Cautela com {metric}"

    reasons = []

    if edge >= 1.5:
        reasons.append("proj acima da linha")
    elif edge <= -1.0:
        reasons.append("proj abaixo da linha")

    if hit_ratio >= 0.70:
        reasons.append("hit recente forte")
    elif hit_ratio <= 0.40:
        reasons.append("hit recente fraco")

    if osc == "Baixa":
        reasons.append("oscilação baixa")
    elif osc == "Alta":
        reasons.append("oscilação alta")

    if matchup == "Favorável":
        reasons.append("matchup favorável")
    elif matchup == "Difícil":
        reasons.append("matchup difícil")

    if "↗" in form_signal:
        reasons.append("momento em alta")
    elif "↘" in form_signal:
        reasons.append("momento em queda")

    if str(row.get("INJ_STATUS", "Available")) in {"Questionable", "Doubtful", "Out"}:
        reasons.append(f"status {row.get('INJ_STATUS')}")

    if not reasons:
        reasons.append("sinais mistos no recorte atual")

    reason_text = " • ".join(reasons[:4])

    context_line = (
        f"{row.get('OPP_TEAM_NAME', 'Oponente')} cede "
        f"{format_number(row.get('OPP_PRA_ALLOWED', 0.0) if metric == 'PRA' else row.get(f'OPP_{metric}_ALLOWED', 0.0))} "
        f"para {row.get('POSITION_GROUP', '-')}"
        f" • linha {format_number(ctx.get('line_value', 0.0))}"
        f" • proj {format_number(ctx.get('projection', 0.0))}"
    )

    return confidence_label, headline, reason_text, context_line

def render_player_headline_html(
    row: pd.Series,
    line_metric: str,
    line_value: float,
    use_market_line: bool,
) -> str:
    chosen_metric, chosen_ctx = _best_metric_for_card(row, line_metric, line_value, use_market_line)
    confidence_label, headline, reason_text, context_line = _build_headline_reason(row, chosen_metric, chosen_ctx)
    matchup_class = get_matchup_chip_class(row.get("MATCHUP_LABEL", "Neutro"))

    return f"""
    <div class="player-headline-card">
        <div class="player-headline-label">{confidence_label} • {"🎯 BetMGM" if chosen_ctx.get("line_source") == "BetMGM" else "✏️ Linha manual"}</div>
        <div class="player-headline-value">{headline}</div>
        <div class="player-headline-sub">
            {reason_text}.
        </div>
        <div class="hero-note">
            {context_line}
            <span class="matchup-chip {matchup_class}" style="margin-left:0.4rem;">
                {row.get("MATCHUP_LABEL", "Neutro")} vs {row.get("POSITION_GROUP", "-")}
            </span>
        </div>
    </div>
    """


def render_player_support_tiles(row: pd.Series, line_metric: str, line_value: float, use_market_line: bool) -> None:
    matchup_class = "quick-stat"
    if row["MATCHUP_LABEL"] == "Favorável":
        matchup_class = "quick-stat quick-stat-up"
    elif row["MATCHUP_LABEL"] == "Difícil":
        matchup_class = "quick-stat quick-stat-down"

    line_context = get_line_context(row, line_metric, line_value, use_market_line=use_market_line)
    if line_context["edge"] > 0.75:
        line_class = "quick-stat quick-stat-up"
    elif line_context["edge"] < -0.75:
        line_class = "quick-stat quick-stat-down"
    else:
        line_class = "quick-stat quick-stat-primary"

    pts_hit = row.get("PTS_HIT_RATE_L10_TEXT", "-")
    reb_hit = row.get("REB_HIT_RATE_L10_TEXT", "-")
    ast_hit = row.get("AST_HIT_RATE_L10_TEXT", "-")

    odds_meta = ""
    if line_context["has_market_line"] and line_context["over_dec"] and line_context["under_dec"]:
        odds_meta = f" • O {format_number(line_context['over_dec'], 2)} • U {format_number(line_context['under_dec'], 2)}"

    st.markdown(
        f"""
        <div class="player-quick-grid">
            <div class="quick-stat">
                <div class="quick-stat-label">PTS L10</div>
                <div class="quick-stat-value">{format_number(row['L10_PTS'])}</div>
                <div class="quick-stat-meta">Proj {format_number(row['PROJ_PTS'])} • Hit {pts_hit}</div>
            </div>
            <div class="quick-stat">
                <div class="quick-stat-label">REB L10</div>
                <div class="quick-stat-value">{format_number(row['L10_REB'])}</div>
                <div class="quick-stat-meta">Proj {format_number(row['PROJ_REB'])} • Hit {reb_hit}</div>
            </div>
            <div class="quick-stat">
                <div class="quick-stat-label">AST L10</div>
                <div class="quick-stat-value">{format_number(row['L10_AST'])}</div>
                <div class="quick-stat-meta">Proj {format_number(row['PROJ_AST'])} • Hit {ast_hit}</div>
            </div>
            <div class="{matchup_class}">
                <div class="quick-stat-label">Matchup</div>
                <div class="quick-stat-value">{row['MATCHUP_LABEL']}</div>
                <div class="quick-stat-meta">PRA cedido {format_number(row['OPP_PRA_ALLOWED'])} • diff {format_signed_number(row['MATCHUP_DIFF'])}</div>
            </div>
            <div class="{line_class}">
                <div class="quick-stat-label">{line_context['line_source']} {line_metric}</div>
                <div class="quick-stat-value">{format_signed_number(line_context['edge'])}</div>
                <div class="quick-stat-meta">Proj {format_number(line_context['projection'])} vs {format_number(line_context['line_value'])} • L10 {line_context['hit_l10_html']}{odds_meta}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_projection_detail_box_html(row: pd.Series) -> str:
    return f"""
    <div class="detail-box">
        <div class="detail-box-top">
            <div class="detail-box-title">Projeções do modelo</div>
            <div class="delta-pill-row">
                <span class="delta-pill delta-flat">peso L10 maior</span>
            </div>
        </div>
        <div class="detail-mini-grid" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
            <div class="detail-mini">
                <div class="detail-mini-label">Proj PTS</div>
                <div class="detail-mini-value">{format_number(row['PROJ_PTS'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Proj REB</div>
                <div class="detail-mini-value">{format_number(row['PROJ_REB'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Proj AST</div>
                <div class="detail-mini-value">{format_number(row['PROJ_AST'])}</div>
            </div>
            <div class="detail-mini detail-mini-highlight">
                <div class="detail-mini-label">Proj PRA</div>
                <div class="detail-mini-value">{format_number(row['PROJ_PRA'])}</div>
            </div>
        </div>
        <div class="detail-mini-grid" style="grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top:0.55rem;">
            <div class="detail-mini">
                <div class="detail-mini-label">Proj 3PM</div>
                <div class="detail-mini-value">{format_number(row['PROJ_3PM'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Proj FGA</div>
                <div class="detail-mini-value">{format_number(row['PROJ_FGA'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Proj 3PA</div>
                <div class="detail-mini-value">{format_number(row['PROJ_3PA'])}</div>
            </div>
        </div>
    </div>
    """

def render_split_detail_box_html(row: pd.Series, line_metric: str) -> str:
    is_home = row.get("IS_HOME", False)
    
    home_val = row.get(f"HOME_{line_metric}", 0.0)
    away_val = row.get(f"AWAY_{line_metric}", 0.0)
    season_val = row.get(f"SEASON_{line_metric}", 0.0)
    
    # Calcula se o jogador cresce ou some baseado no local do jogo de hoje
    active_val = home_val if is_home else away_val
    diff = active_val - season_val
    
    if diff >= 1.0:
        diff_pill = f'<span class="delta-pill delta-up">Rende +{diff:.1f} hoje</span>'
    elif diff <= -1.0:
        diff_pill = f'<span class="delta-pill delta-down">Cai {diff:.1f} hoje</span>'
    else:
        diff_pill = '<span class="delta-pill delta-flat">Sem impacto relevante</span>'
        
    home_class = "detail-mini detail-mini-highlight" if is_home else "detail-mini"
    away_class = "detail-mini detail-mini-highlight" if not is_home else "detail-mini"
    
    return f"""
    <div class="detail-box">
        <div class="detail-box-top">
            <div class="detail-box-title">Efeito de Mando de Quadra — {line_metric}</div>
            <div class="delta-pill-row">
                {diff_pill}
            </div>
        </div>
        <div class="detail-mini-grid" style="grid-template-columns: repeat(3, minmax(0, 1fr));">
            <div class="detail-mini">
                <div class="detail-mini-label">Média Geral</div>
                <div class="detail-mini-value">{format_number(season_val)}</div>
            </div>
            <div class="{home_class}">
                <div class="detail-mini-label">Jogando em Casa</div>
                <div class="detail-mini-value">{format_number(home_val)}</div>
            </div>
            <div class="{away_class}">
                <div class="detail-mini-label">Jogando Fora</div>
                <div class="detail-mini-value">{format_number(away_val)}</div>
            </div>
        </div>
        <div class="hero-note" style="margin-top: 0.55rem;">
            Hoje o jogador atua <strong>{'EM CASA' if is_home else 'FORA DE CASA'}</strong>. Card destacado indica o cenário ativo da partida.
        </div>
    </div>
    """

def render_manual_line_detail_box_html(row: pd.Series, line_metric: str, line_value: float, use_market_line: bool) -> str:
    line_context = get_line_context(row, line_metric, line_value, use_market_line=use_market_line)
    line_chip_class = "delta-flat"
    if line_context["edge"] > 0.75:
        line_chip_class = "delta-up"
    elif line_context["edge"] < -0.75:
        line_chip_class = "delta-down"

    odds_note = "Usando linha manual."
    if line_context["has_market_line"]:
        over_text = format_number(line_context["over_dec"], 2) if line_context["over_dec"] else "-"
        under_text = format_number(line_context["under_dec"], 2) if line_context["under_dec"] else "-"
        odds_note = f"BetMGM • Over {over_text} • Under {under_text}"

    return f"""
    <div class="detail-box">
        <div class="detail-box-top">
            <div class="detail-box-title">Linha {line_context['line_source']} — {line_metric}</div>
            <div class="delta-pill-row">
                <span class="delta-pill {line_chip_class}">{line_context['label']}</span>
                <span class="delta-pill delta-flat">Linha {format_number(line_context['line_value'])}</span>
            </div>
        </div>
        <div class="detail-mini-grid" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
            <div class="detail-mini">
                <div class="detail-mini-label">Projeção</div>
                <div class="detail-mini-value">{format_number(line_context['projection'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Edge</div>
                <div class="detail-mini-value">{format_signed_number(line_context['edge'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Hit L5</div>
                <div class="detail-mini-value">{line_context['hit_l5']}</div>
            </div>
            <div class="detail-mini detail-mini-highlight">
                <div class="detail-mini-label" title="{line_context['tooltip']}" style="cursor:help;">Hit L10 {line_context['icon']}</div>
                <div class="detail-mini-value">{line_context['hit_l10']}</div>
            </div>
        </div>
        <div style="margin-top: 0.8rem; padding-top: 0.6rem; border-top: 1px solid rgba(148,163,184,.12);">
            <div class="detail-mini-label" style="margin-bottom: 0.2rem;">Sequência L10 (→ mais recente)</div>
            <div style="font-size: 1.1rem; letter-spacing: 0.1rem;">{line_context['hit_sequence']}</div>
        </div>
        <div class="hero-note" style="margin-top: 0.6rem;">{odds_note}</div>
    </div>
    """

def render_matchup_detail_box_html(row: pd.Series) -> str:
    matchup_class = get_matchup_chip_class(row["MATCHUP_LABEL"])
    return f"""
    <div class="detail-box">
        <div class="detail-box-top">
            <div class="detail-box-title">Contexto do adversário</div>
            <div class="delta-pill-row">
                <span class="matchup-chip {matchup_class}">{row['MATCHUP_LABEL']}</span>
            </div>
        </div>
        <div class="detail-mini-grid" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
            <div class="detail-mini">
                <div class="detail-mini-label">PTS ced.</div>
                <div class="detail-mini-value">{format_number(row['OPP_PTS_ALLOWED'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">REB ced.</div>
                <div class="detail-mini-value">{format_number(row['OPP_REB_ALLOWED'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">AST ced.</div>
                <div class="detail-mini-value">{format_number(row['OPP_AST_ALLOWED'])}</div>
            </div>
            <div class="detail-mini detail-mini-highlight">
                <div class="detail-mini-label">PRA ced.</div>
                <div class="detail-mini-value">{format_number(row['OPP_PRA_ALLOWED'])}</div>
            </div>
        </div>
        <div class="hero-note">{row['OPP_TEAM_NAME']} vs {row['POSITION_GROUP']} • liga {format_number(row['LEAGUE_PRA_BASELINE'])} • diferença {format_signed_number(row['MATCHUP_DIFF'])}</div>
    </div>
    """


def render_focus_summary_tiles(row: pd.Series, line_metric: str, line_value: float, use_market_line: bool) -> None:
    line_context = get_line_context(row, line_metric, line_value, use_market_line=use_market_line)
    matchup_ctx = get_metric_matchup_context(row, line_metric)

    line_class = "micro-stat micro-stat-emph"
    if line_context["edge"] > 0.75:
        line_class = "micro-stat micro-stat-good"
    elif line_context["edge"] < -0.75:
        line_class = "micro-stat micro-stat-bad"

    matchup_class = "micro-stat"
    if matchup_ctx["label"] == "Favorável":
        matchup_class = "micro-stat micro-stat-good"
    elif matchup_ctx["label"] == "Difícil":
        matchup_class = "micro-stat micro-stat-bad"

    proj_col = get_metric_projection_column(line_metric)
    season_col = f"SEASON_{line_metric}" if line_metric != "PRA" else "SEASON_PRA"
    l10_col = f"L10_{line_metric}" if line_metric != "PRA" else "L10_PRA"

    st.markdown(
        f"""
        <div class="micro-grid">
            <div class="micro-stat micro-stat-emph">
                <div class="micro-label">Proj {line_metric}</div>
                <div class="micro-value">{format_number(row.get(proj_col, 0.0))}</div>
                <div class="micro-meta">Temp {format_number(row.get(season_col, 0.0))} • L10 {format_number(row.get(l10_col, 0.0))}</div>
            </div>
            <div class="{line_class}">
                <div class="micro-label">{line_context['line_source']} {line_metric}</div>
                <div class="micro-value">{format_signed_number(line_context['edge'])}</div>
                <div class="micro-meta">Proj {format_number(line_context['projection'])} vs {format_number(line_context['line_value'])} • L10 {line_context['hit_l10']}</div>
            </div>
            <div class="{matchup_class}">
                <div class="micro-label">Matchup</div>
                <div class="micro-value">{matchup_ctx['label']}</div>
                <div class="micro-meta">{row['OPP_TEAM_NAME']} vs {row['POSITION_GROUP']} • diff {format_signed_number(matchup_ctx['diff'])}</div>
            </div>
            <div class="micro-stat">
                <div class="micro-label">Oscilação</div>
                <div class="micro-value">{row['OSC_CLASS']}</div>
                <div class="micro-meta">{row['FORM_SIGNAL']}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )



def render_player_cards_grid(
    filtered_df: pd.DataFrame,
    line_metric: str,
    line_value: float,
    use_market_line: bool,
    cards_per_row: int = 2,
) -> None:
    rows = [filtered_df.iloc[i:i + cards_per_row] for i in range(0, len(filtered_df), cards_per_row)]
    for row_df in rows:
        cols = st.columns(cards_per_row)
        for col_idx in range(cards_per_row):
            with cols[col_idx]:
                if col_idx < len(row_df):
                    render_player_card(row_df.iloc[col_idx], line_metric, line_value, use_market_line)


def render_team_section_legacy(
    team_name: str,
    team_df: pd.DataFrame,
    season: str,
    min_games: int,
    min_minutes: int,
    role_filter: str,
    sort_label: str,
    ascending: bool,
    view_mode: str,
    chart_mode: str,
    line_metric: str,
    line_value: float,
    use_market_line: bool,
    cards_per_row: int,
) -> None:
    st.subheader(team_name)
    if team_df.empty:
        st.warning("Não consegui montar os dados desse time.")
        return

    sort_column = SORT_OPTIONS[sort_label]
    filtered_df = filter_and_sort_team_df(
        team_df=team_df,
        min_games=min_games,
        min_minutes=min_minutes,
        role_filter=role_filter,
        sort_column=sort_column,
        ascending=ascending,
    )
    if filtered_df.empty:
        st.warning("Nenhum jogador passou pelos filtros. Você apertou demais o funil, pequeno fiscal da amostra.")
        return

    active_source = "BetMGM quando disponível" if use_market_line else "Linha manual"
    st.markdown(
        f"""
        <div class="info-pill">Jogadores exibidos: {len(filtered_df)}</div>
        <div class="info-pill">GP mínimo: {min_games}</div>
        <div class="info-pill">MIN mínimo: {min_minutes}</div>
        <div class="info-pill">Papel: {role_filter}</div>
        <div class="info-pill">Ordenação: {sort_label}</div>
        <div class="info-pill">Visualização: {view_mode}</div>
        <div class="info-pill">Linha ativa: {active_source}</div>
        <div class="info-pill">Adversário: {filtered_df['OPP_TEAM_NAME'].iloc[0]}</div>
        """,
        unsafe_allow_html=True,
    )

    if view_mode == "Cards":
        st.markdown(
            '<div class="section-note">Cards curtos no topo e painel fixo do jogador abaixo para facilitar consulta no celular.</div>',
            unsafe_allow_html=True,
        )
        render_player_cards_grid(filtered_df, line_metric=line_metric, line_value=line_value, use_market_line=use_market_line, cards_per_row=cards_per_row)
    else:
        summary_df, detail_df = build_display_dataframes(filtered_df)
        quick_tab, detail_tab = st.tabs(["Leitura rápida", "Detalhamento"])
        with quick_tab:
            st.markdown(
                '<div class="section-note">Aqui o foco é no que bate rápido no olho: PRA, projeção, matchup, hit PRA, oscilação e sinal de forma.</div>',
                unsafe_allow_html=True,
            )
            st.dataframe(style_table(summary_df, quick_view=True), use_container_width=True, hide_index=True)
        with detail_tab:
            st.markdown(
                '<div class="section-note">Aqui entra a parte mais detalhada: PTS, REB, AST, PRA, projeções e hit rate separado por atributo.</div>',
                unsafe_allow_html=True,
            )
            st.dataframe(style_table(detail_df, quick_view=False), use_container_width=True, hide_index=True)

    options = filtered_df[["PLAYER", "PLAYER_ID"]].drop_duplicates()
    player_name = st.selectbox(
        f"Jogador em foco — {team_name}",
        options["PLAYER"].tolist(),
        key=f"player_focus_{team_name}_{view_mode}_{chart_mode}_{line_metric}",
    )
    selected_row = filtered_df.loc[filtered_df["PLAYER"] == player_name].iloc[0]
    render_player_focus_panel(selected_row, line_metric, line_value, use_market_line, season, chart_mode)


def main() -> None:
    inject_css()

    st.markdown('<div class="main-title">NBA Props Dashboard</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtitle">Escolha o jogo, defina a métrica e compare projeção, consistência e linha ativa por jogador.</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="hero-pills">
            <span class="hero-pill">Projeções</span>
            <span class="hero-pill">Linha manual / BetMGM</span>
            <span class="hero-pill">Leitura rápida mobile</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Configurações")
        selected_date = get_brasilia_today()
        st.caption(f"Jogos do dia em Brasília • {selected_date.strftime('%d/%m/%Y')}")

        st.divider()
        # Troca as bolinhas de seleção por pílulas clicáveis
        _selected_chart = st.pills("Modo do gráfico", CHART_OPTIONS, default="Compacto")
        # Trava de segurança: se desmarcar, volta pro Compacto
        chart_mode = _selected_chart if _selected_chart else "Compacto"
        # Troca o slider por pílulas clicáveis
        _selected_cards = st.pills("Cards por linha", [1, 2], default=2)
        # Trava de segurança: se desmarcar, volta para 2 cards por padrão
        cards_per_row = _selected_cards if _selected_cards else 2

        st.divider()
        st.subheader("Filtros")
        min_games = st.slider("Mínimo de jogos na temporada", 0, 82, 5, 1)
        min_minutes = st.slider("Mínimo de minutos por jogo", 0, 40, 15, 1)
        # Troca a caixa de seleção por botões clicáveis (pills)
        _selected_role = st.pills("Mostrar jogadores", ROLE_OPTIONS, default="Todos")
        # Trava de segurança: se desmarcar tudo sem querer, volta a mostrar o time inteiro
        role_filter = _selected_role if _selected_role else "Todos"

        st.divider()
        st.subheader("Linha")
        
        # Cria os botões visíveis na barra lateral
        _selected_metric = st.pills("Métrica da linha", LINE_METRIC_OPTIONS, default="PRA")
        # Trava de segurança: se o usuário desmarcar tudo, forçamos o PRA para não quebrar a lógica
        line_metric = _selected_metric if _selected_metric else "PRA"
        
        default_line_map = {"PRA": 25.5, "PTS": 20.5, "REB": 7.5, "AST": 5.5, "3PM": 2.5, "FGA": 15.5, "3PA": 6.5}
        line_value = st.number_input(
            "Valor da linha manual",
            min_value=0.0,
            value=float(default_line_map[line_metric]),
            step=0.5,
            key=f"manual_line_{line_metric}",
        )
        api_key_available = bool(get_odds_api_key())
        use_market_line = st.toggle(
            "Usar linha BetMGM",
            value=api_key_available,
            disabled=not api_key_available,
        )
        if not api_key_available:
            st.caption("Adicione SPORTSGAMEODDS_API_KEY em st.secrets ou variável de ambiente para usar BetMGM.")

        # Ordenação Automática "Invisível"
        # Ele pega a métrica selecionada (Ex: "PTS") e avisa pro app ordenar por "PTS L10" do maior pro menor
        sort_label = f"{line_metric} L10"
        if sort_label not in SORT_OPTIONS:
            sort_label = "PRA L10" # Trava de segurança
        ascending = False

        st.divider()
        st.caption("Este app busca os dados ao abrir a página.")
        if st.button("Forçar atualização"):
            st.cache_data.clear()
            st.rerun()

    season = get_season_string(selected_date)

    try:
        games = get_games_for_date(selected_date)
    except Exception as exc:
        st.error("A NBA demorou ou falhou ao responder na consulta dos jogos. Tente novamente em alguns segundos ou use o botão de atualização.")
        st.exception(exc)
        return

    st.caption(f"Temporada detectada: {season}")

    if games.empty:
        st.warning("Não encontrei jogos para hoje. A NBA também sabe sabotar entretenimento.")
        return

    game_label = st.selectbox("Escolha o jogo de hoje", games["label"].tolist())
    selected_game = games.loc[games["label"] == game_label].iloc[0]

    try:
        away_df, home_df = get_matchup_context(
            away_team_id=int(selected_game["VISITOR_TEAM_ID"]),
            home_team_id=int(selected_game["HOME_TEAM_ID"]),
            away_team_name=selected_game["away_team_name"],
            home_team_name=selected_game["home_team_name"],
            season=season,
            include_market=api_key_available,
        )
    except Exception as exc:
        st.error("A NBA demorou ou falhou ao responder nas estatísticas do confronto. Tente novamente em alguns segundos ou use o botão de atualização.")
        st.exception(exc)
        return    
        
   

    render_matchup_header(selected_game)
    st.caption("Injury report oficial temporariamente desativado para teste de performance.")    
    
        
    render_summary_cards(
        away_df=away_df,
        home_df=home_df,
        min_games=min_games,
        min_minutes=min_minutes,
        role_filter=role_filter,
    )
    render_game_rankings(
        away_df=away_df,
        home_df=home_df,
        min_games=min_games,
        min_minutes=min_minutes,
        role_filter=role_filter,
        line_metric=line_metric,
        line_value=line_value,
        use_market_line=use_market_line,
    )



    selected_team_view = st.segmented_control(
        "Time em análise",
        options=[selected_game["away_team_name"], selected_game["home_team_name"]],
        default=selected_game["away_team_name"],
        key=f"team_view_{selected_game['GAME_ID']}",
    )

    if selected_team_view == selected_game["away_team_name"]:
        render_team_section_v2(
            team_name=selected_game["away_team_name"],
            team_df=away_df,
            season=season,
            min_games=min_games,
            min_minutes=min_minutes,
            role_filter=role_filter,
            sort_label=sort_label,
            ascending=ascending,
            chart_mode=chart_mode,
            line_metric=line_metric,
            line_value=line_value,
            use_market_line=use_market_line,
            cards_per_row=cards_per_row,
        )
    else:
        render_team_section_v2(
            team_name=selected_game["home_team_name"],
            team_df=home_df,
            season=season,
            min_games=min_games,
            min_minutes=min_minutes,
            role_filter=role_filter,
            sort_label=sort_label,
            ascending=ascending,
            chart_mode=chart_mode,
            line_metric=line_metric,
            line_value=line_value,
            use_market_line=use_market_line,
            cards_per_row=cards_per_row,
        )
    
    st.markdown(
        """
        <div class="small-note">
        Nota: "Titular provável" neste MVP significa os 5 jogadores com mais minutos por jogo na temporada.
        É um atalho útil para análise, não a escalação oficial confirmada do jogo.
        </div>
        """,
        unsafe_allow_html=True,
    )

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
from pandas.io.formats.style import Styler

# Importamos o que é necessário para o visual
from config import (
    NBA_TEAM_COLORS, TEAM_LOGO_URL, PLAYER_HEADSHOT_URL, 
    LINE_METRIC_OPTIONS, CHART_OPTIONS, TEAM_LOOKUP
)
from processamento import (
    get_line_context, get_matchup_chip_class, 
    fetch_latest_injury_report_df, merge_injury_report
)

# =========================================================
# 1. ESTILIZAÇÃO E CSS
# =========================================================

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

def render_player_card(row: pd.Series, metric: str, line: float, use_market: bool):
    """Renderiza o card individual do jogador na galeria."""
    ctx = get_line_context(row, metric, line, use_market)
    match_cls = get_matchup_chip_class(row['MATCHUP_LABEL'])
    
    # O HTML complexo que estava no app.py agora mora aqui
    html = f"""
    <div class="summary-card">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <span class="summary-label">{row['PLAYER']}</span>
            <span class="matchup-chip {match_cls}">{row['MATCHUP_LABEL']}</span>
        </div>
        <div class="summary-value">{ctx['projection']:.1f}</div>
        <div class="badge-row">
            <span class="badge badge-starter">{row['ROLE']}</span>
            <span class="badge badge-neutral">L10: {ctx['hit_l10']}</span>
        </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

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


def render_summary_cards(df: pd.DataFrame, metric: str, line: float, use_market: bool):
    """Renderiza a grade de cards de resumo."""
    if df.empty:
        st.info("Nenhum jogador atende aos filtros.")
        return
    
    cols = st.columns(3)
    for i, (_, row) in enumerate(df.iterrows()):
        with cols[i % 3]:
            render_player_card(row, metric, line, use_market)

def render_player_focus_panel(
    row: pd.Series,
    line_metric: str,
    line_value: float,
    use_market_line: bool,
    season: str,
    chart_mode: str,
) -> None:
   # --- BUSCADOR DE CORES DEFINITIVO (HARDCODED) ---
    # Pegamos todo o texto disponível sobre o time do jogador
    search_text = f"{row.get('TEAM_NAME', '')} {row.get('TEAM_ABBR', '')}".upper()
    
    # 1. Definimos a sigla padrão (NBA)
    tk = 'NBA'
    
    # --- RASTREADOR INTELIGENTE PARA O BANNER GRANDE ---
    search_text = f"{row.get('TEAM_NAME', '') or ''} {row.get('TEAM_ABBR', '') or ''}".upper()
    tk = 'NBA'
    
    # Loop automático pelo dicionário que resolve todos os times de uma vez
    for abbr, info in NBA_TEAM_COLORS.items():
        team_keyword = info.get('name', '').upper()
        if (team_keyword and team_keyword in search_text) or (abbr in search_text):
            tk = abbr
            break
            
    colors = NBA_TEAM_COLORS.get(tk, {'primary': '#1d222d', 'secondary': '#ffcc00'})
    
    # 3. Puxamos as cores do dicionário global
    colors = NBA_TEAM_COLORS.get(tk, {'primary': '#1d222d', 'secondary': '#ffcc00'})

    # --- DEBUG: APAGUE ESSA LINHA ABAIXO APÓS TESTAR ---
    # st.write(f"DEBUG: Time identificado como: {tk}")


    st.markdown('<div class="focus-shell">', unsafe_allow_html=True)

    top_left, top_right = st.columns([1, 5])
    with top_left:
        st.image(get_player_headshot_url(int(row["PLAYER_ID"])), width=92)

    with top_right:
        # Banner com a cor primária do time e borda na cor secundária
        st.markdown(f"""
            <div style="
                background: linear-gradient(135deg, {colors['primary']} 0%, {colors['secondary']} 150%);
                padding: 20px; 
                border-radius: 12px; 
                border-left: 15px solid {colors['secondary']};
                margin-bottom: 20px;
                box-shadow: 0 8px 20px rgba(0,0,0,0.4);
            ">
                <h1 style="color: {colors['secondary']}; margin: 0; font-size: 32px; font-weight: 800; letter-spacing: -1px;">
                    {row['PLAYER']}
                </h1>
                <div style="color: {colors['secondary']}; opacity: 0.9; font-weight: 600; font-size: 14px;">
                    {row.get('TEAM_NAME', 'NBA') or ''} | #{row.get('JERSEY_NUMBER', '')} | {row.get('POSITION', '')}
                </div>
            </div>
        """, unsafe_allow_html=True)
        position = row["POSITION"] if str(row["POSITION"]).strip() else "-"
        st.markdown(
            f'<div class="focus-sub">Pos {position} • GP {int(row["SEASON_GP"])} • MIN {format_number(row["SEASON_MIN"])} • Time {row["TEAM_NAME"]}</div>',
            unsafe_allow_html=True,
        )
        render_badges(
            row["ROLE"],
            row.get("FORM_SIGNAL", "→ Estável"),
            row.get("OSC_CLASS", "-"),
            row.get("MATCHUP_LABEL", "Neutro"),
        )
        render_focus_summary_tiles(row, line_metric, line_value, use_market_line)
    
    # --- CONTROLE MESTRE DE MÉTRICA ---
    # Ele fica fora das abas, então aparece o tempo todo no topo
    _visual_metric = st.pills(
        "Métrica em análise detalhada",
        ["PRA", "PTS", "REB", "AST", "3PM", "FGA", "3PA"],
        default=line_metric,
        key=f"global_visual_metric_{row['PLAYER_ID']}"
    )
    visual_metric = _visual_metric if _visual_metric else line_metric


    # NOVIDADE: As 3 abas organizando a tela!
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
        # CONTROLE MESTRE: Controla ambos os gráficos simultaneamente!
        
        # 1. Gráfico Histórico Clássico (Agora recebe a ordem do Controle Mestre)
        render_player_chart(row["PLAYER"], int(row["PLAYER_ID"]), season, chart_mode, visual_metric)
        
        st.divider()
        
        # 2. Gráfico Piso e Teto (Histograma)
        st.markdown(f"### Frequência na Temporada — {visual_metric}")
        st.caption("Veja os montinhos: concentrados à esquerda (Piso seguro), espalhados à direita (Teto alto).")
        
        log = get_player_log(int(row["PLAYER_ID"]), season)
        if not log.empty:
            log["PRA"] = log["PTS"] + log["REB"] + log["AST"]
            log["3PM"] = log["FG3M"]
            log["3PA"] = log.get("FG3A", log["FGA"])
            
            # Recalcula a linha e a odd (se BetMGM) para a nova métrica selecionada!
            visual_ctx = get_line_context(row, visual_metric, line_value, use_market_line)
            active_line = float(visual_ctx["line_value"])
            
            log_col_map = {"PRA": "PRA", "PTS": "PTS", "REB": "REB", "AST": "AST", "3PM": "3PM", "FGA": "FGA", "3PA": "3PA"}
            active_col = log_col_map.get(visual_metric, "PRA")
            
            if active_col in log.columns:
                hist_data = log[active_col].dropna()
                
                fig = go.Figure()
                fig.add_trace(go.Histogram(
                    x=hist_data,
                    # Força tamanho 1. Começando em -0.5 garante que o número exato (ex: 2) fique bem no centro da barra!
                    xbins=dict(start=-0.5, end=max(hist_data.max(), active_line) + 5, size=1),
                    marker_color="rgba(139,92,246, 0.65)",
                    marker_line_color="rgba(139,92,246, 1)",
                    marker_line_width=1.5,
                    opacity=0.9,
                    # Agora o tooltip vai mostrar apenas o número cravado (ex: 2)
                    hovertemplate=f"Valor exato de {visual_metric}: %{{x}}<br>Jogos atingidos: %{{y}}<extra></extra>"
                ))
                
                # Desenha a linha vertical da aposta
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

                # UX: Se for manual e o usuário trocar a métrica visual, damos um aviso!
                if not use_market_line and visual_metric != line_metric:
                    st.warning(f"Atenção: A linha vertical está usando o valor manual da barra lateral ({active_line}), que originalmente foi digitado para {line_metric}.")

                fig.update_layout(
                    template="plotly_dark",
                    height=380,
                    margin=dict(l=20, r=20, t=40, b=20),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(15,23,42,0.35)",
                    xaxis_title=f"Valor de {visual_metric} na partida",
                    yaxis_title="Quantidade de Jogos",
                    dragmode=False,
                    bargap=0.15
                )
                # Força o eixo X a mostrar apenas números inteiros (0, 1, 2, 3...)
                fig.update_xaxes(showgrid=True, gridcolor="rgba(148,163,184,0.1)", tick0=0, dtick=1)
                fig.update_yaxes(showgrid=True, gridcolor="rgba(148,163,184,0.15)", zeroline=False)
                
                st.plotly_chart(fig, use_container_width=True)
                
                # --- NOVO: RESUMO OVER/UNDER MATEMÁTICO ---
                over_count = int((hist_data > active_line).sum())
                under_count = int((hist_data < active_line).sum())
                push_count = int((hist_data == active_line).sum())
                
                push_html = f'<span style="margin: 0 0.8rem; color: #334155;">|</span><span style="color: #fbbf24;">PUSH (DEVOLUÇÃO) = {push_count}</span>' if push_count > 0 else ''
                
                st.markdown(
                    f'''
                    <div style="margin-top: 0.2rem; text-align: center; padding: 0.85rem; background: rgba(15,23,42,0.8); border: 1px solid rgba(148,163,184,0.15); border-radius: 12px; font-weight: 800; font-size: 0.9rem; letter-spacing: 0.05em;">
                        <span style="color: #94a3b8;">LINHA {visual_ctx['line_source'].upper()}: {active_line}</span>
                        <span style="margin: 0 0.8rem; color: #334155;">|</span>
                        <span style="color: #10b981;">OVER NA TEMPORADA = {over_count}</span>
                        <span style="margin: 0 0.8rem; color: #334155;">|</span>
                        <span style="color: #ef4444;">UNDER NA TEMPORADA = {under_count}</span>
                        {push_html}
                    </div>
                    ''',
                    unsafe_allow_html=True
                )
                st.divider()

                # --- 3. NOVO: GRÁFICO DE DISPERSÃO (MINUTOS VS PRODUÇÃO) ---
                st.markdown(f"### Eficiência: Minutos vs {visual_metric}")
                st.caption("Cada ponto é um jogo. A linha de tendência mostra se a produção sobe junto com os minutos.")
                
                scatter_df = log[["MIN", active_col, "GAME_DATE"]].copy().dropna()
                # Adiciona informação de matchup se existir
                if "MATCHUP" in log.columns: scatter_df["MATCHUP"] = log["MATCHUP"]
                else: scatter_df["MATCHUP"] = ""
                
                if not scatter_df.empty:
                    # Calcula a linha de tendência (Regressão Linear Simples)
                    x = scatter_df["MIN"]
                    y = scatter_df[active_col]
                    z = np.polyfit(x, y, 1)
                    p = np.poly1d(z)
                    trend_x = np.array([x.min(), x.max()])
                    trend_y = p(trend_x)

                    fig_scatter = go.Figure()

                    # Adiciona os pontos (Jogos)
                    fig_scatter.add_trace(go.Scatter(
                        x=x, y=y,
                        mode='markers',
                        marker=dict(
                            size=12,
                            color=np.where(y >= active_line, '#10b981', '#ef4444'), # Verde se Over, Vermelho se Under
                            line=dict(width=1, color='#f8fafc'),
                            opacity=0.8
                        ),
                        text=scatter_df.apply(lambda r: f"Data: {r['GAME_DATE'].strftime('%d/%m')}<br>Matchup: {r['MATCHUP']}<br>Minutos: {r['MIN']}<br>{visual_metric}: {r[active_col]}", axis=1),
                        hoverinfo='text',
                        name='Jogos'
                    ))

                    # Adiciona a Linha de Tendência
                    fig_scatter.add_trace(go.Scatter(
                        x=trend_x, y=trend_y,
                        mode='lines',
                        line=dict(color='rgba(255, 255, 255, 0.4)', width=2, dash='dot'),
                        name='Tendência',
                        hoverinfo='skip'
                    ))

                    # Linha horizontal da aposta
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
                    
                    # Explicação da correlação
                    correl = x.corr(y)
                    if correl > 0.7:
                        st.success(f"📈 **Alta Correlação ({correl:.2f}):** O desempenho do jogador é extremamente dependente dos minutos. Se o tempo de quadra subir hoje, o Over é muito provável.")
                    elif correl > 0.4:
                        st.info(f"📊 **Correlação Moderada ({correl:.2f}):** Mais minutos costumam trazer mais {visual_metric}, mas outros fatores (eficiência/matchup) também pesam.")
                    else:
                        st.warning(f"📉 **Baixa Correlação ({correl:.2f}):** Esse jogador produz de forma oscilante, independente de quanto tempo fica em quadra.")
            else:
                st.info(f"Dados indisponíveis para a métrica {visual_metric}.")
        else:
            st.info("Sem histórico suficiente para gerar o gráfico.")
            
    with market_tab:
        # 1. MAPEAMENTO COMPLETO
        team_map = {
            'Hawks': 'ATL', 'Celtics': 'BOS', 'Nets': 'BKN', 'Hornets': 'CHA', 'Bulls': 'CHI', 
            'Cavaliers': 'CLE', 'Mavericks': 'DAL', 'Nuggets': 'DEN', 'Pistons': 'DET', 
            'Warriors': 'GSW', 'Rockets': 'HOU', 'Pacers': 'IND', 'Clippers': 'LAC', 
            'Lakers': 'LAL', 'Grizzlies': 'MEM', 'Heat': 'MIA', 'Bucks': 'MIL', 
            'Timberwolves': 'MIN', 'Pelicans': 'NOP', 'Knicks': 'NYK', 'Thunder': 'OKC', 
            'Magic': 'ORL', '76ers': 'PHI', 'Suns': 'PHX', 'Kings': 'SAC', 
            'Spurs': 'SAS', 'Raptors': 'TOR', 'Jazz': 'UTA', 'Wizards': 'WAS', 'Trail Blazers': 'POR'
        }

        # 2. BUSCA BRUTA (Varre cada coluna do jogador para achar o adversário)
        opp_abbr = None
        player_team_name = str(row.get('TEAM_NAME', ''))
        
        # O "Pulo do Gato": olhamos todos os valores da linha em busca de um time
        for col_val in row.values:
            val_str = str(col_val)
            for team_name, abbr in team_map.items():
                # Se acharmos um nome de time (ex: Memphis) e não for o time do Jaylen (Celtics)
                if team_name in val_str and team_name not in player_team_name:
                    opp_abbr = abbr
                    break
            if opp_abbr: break

        # 3. LOGICA DE EXIBIÇÃO
        if opp_abbr:
            st.markdown(f"### ⚔️ Histórico de Confronto: vs {opp_abbr}")
            st.caption(f"Desempenho real de {row['PLAYER']} nos últimos 5 jogos contra a defesa do {opp_abbr}.")
            
            if not log.empty:
                # Filtra o histórico procurando a sigla no campo MATCHUP do log
                h2h_log = log[log['MATCHUP'].str.contains(opp_abbr)].copy()
                h2h_log = h2h_log.sort_values('GAME_DATE', ascending=False).head(5)
                
                if not h2h_log.empty:
                    # Cálculos
                    h2h_log["PRA"] = h2h_log["PTS"] + h2h_log["REB"] + h2h_log["AST"]
                    h2h_log["3PM"] = h2h_log.get("FG3M", 0)
                    
                    m_ctx = get_line_context(row, visual_metric, line_value, use_market_line)
                    m_line = float(m_ctx["line_value"])
                    h2h_log['Status'] = h2h_log[visual_metric].apply(lambda x: "OVER" if x > m_line else "UNDER")
                    h2h_hits = (h2h_log[visual_metric] > m_line).sum()
                    h2h_total = len(h2h_log)
                    h2h_pct = (h2h_hits / h2h_total) * 100
                    
                    # Cards de métricas
                    c1, c2, c3 = st.columns(3)
                    with c1: st.metric("Encontros", h2h_total)
                    with c2: 
                        avg_h2h = h2h_log[visual_metric].mean()
                        st.metric(f"Média vs {opp_abbr}", f"{avg_h2h:.1f}", delta=f"{avg_h2h - m_line:.1f}")
                    with c3:
                        st.markdown("**Sequência H2H:**")
                        # Cria as bolinhas baseadas no status
                        dots = "".join(['<span style="color:#28a745;font-size:22px">🟢</span>' if "OVER" in s 
                                   else '<span style="color:#dc3545;font-size:22px">🔴</span>' 
                                   for s in h2h_log['Status'].iloc[::-1]])
                        st.markdown(f'<div style="letter-spacing:2px">{dots}</div>', unsafe_allow_html=True)
                        st.caption(f"Aproveitamento: {h2h_pct:.0f}%")

                    # Tabela
                    # --- TABELA DETALHADA COM COLUNA DE LINHA ---
                    h2h_display = h2h_log[['GAME_DATE', 'MATCHUP', visual_metric, 'MIN']].copy()
                    
                    # Adicionamos a linha atual para comparação direta
                    h2h_display['Linha'] = m_line 
                    
                    # Formata a data para um padrão mais curto (ganha espaço)
                    h2h_display['GAME_DATE'] = h2h_display['GAME_DATE'].dt.strftime('%d/%m/%y')
                    
                    # Define o Status comparando o Real vs a Linha
                    h2h_display['Status'] = h2h_display[visual_metric].apply(lambda x: "✅ OVER" if x > m_line else "❌ UNDER")
                    
                    # REORDENAR COLUNAS: Colocamos a Linha ao lado do resultado real
                    cols_order = ['GAME_DATE', 'MATCHUP', 'Linha', visual_metric, 'MIN', 'Status']
                    h2h_display = h2h_display[cols_order]
                    
                    st.dataframe(
                        h2h_display, 
                        hide_index=True, 
                        use_container_width=True,
                        column_config={
                            "GAME_DATE": st.column_config.TextColumn("Data", width="small"),
                            "MATCHUP": st.column_config.TextColumn("Confronto", width="medium"),
                            "Linha": st.column_config.NumberColumn("Linha", format="%.1f"),
                            visual_metric: st.column_config.NumberColumn(f"Real ({visual_metric})", format="%.0f"),
                            "MIN": st.column_config.NumberColumn("Min", format="%d", width="small"),
                            "Status": st.column_config.TextColumn("Status", width="small")
                        }
                    )
                else:
                    st.info(f"Nenhum jogo registrado contra {opp_abbr} nesta temporada.")
            else:
                st.info("Log de histórico indisponível.")
        else:
            # Caso a busca falhe, mostramos as chaves para debugar (ajuda a gente a consertar)
            st.warning("Não foi possível detectar o adversário automaticamente.")
            with st.expander("Ver dados brutos (Debug)"):
                st.write(row.to_dict())

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
        '<div class="section-note">Cards curtos no topo e painel detalhado do jogador sob demanda, para não carregar tranqueira à toa.</div>',
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

import streamlit as st
from datetime import date, datetime
from config import (
    TEAM_LOOKUP, SORT_OPTIONS, ROLE_OPTIONS, CHART_OPTIONS, 
    LINE_METRIC_OPTIONS, APP_TIMEZONE
)
from api_nba import get_games_for_date
from api_odds import get_odds_api_key
from pdf_reader import get_season_string
from processamento import get_matchup_context

# Puxando as funções do seu arquivo ui_components grandão!
from ui_components import (
    inject_css, 
    render_matchup_header,
    render_summary_cards,
    render_game_rankings,
    render_team_section_v2, 
    render_player_cards_grid,
    render_injury_report_tab,
    render_lineup_report_tab,
    render_player_focus_panel
)

def get_brasilia_today() -> date:
    return datetime.now(APP_TIMEZONE).date()

def main():
    st.set_page_config(page_title="NBA Props Dashboard", page_icon="🏀", layout="wide")
    inject_css()
    
    with st.sidebar:
        st.header("Configurações")
        chart_mode = st.pills("Gráfico", CHART_OPTIONS, default="Compacto")
        cards_per_row = st.pills("Cards/Linha", [1, 2], default=2)
        min_games = st.slider("Min Jogos", 0, 82, 5)
        min_minutes = st.slider("Min Minutos", 0, 40, 15)
        role_filter = st.pills("Jogadores", ROLE_OPTIONS, default="Todos")
        line_metric = st.pills("Métrica", LINE_METRIC_OPTIONS, default="PRA")
        line_value = st.number_input("Linha Manual", value=25.5, step=0.5)
        api_key_available = bool(get_odds_api_key())
        use_market_line = st.toggle("Usar BetMGM", value=api_key_available, disabled=not api_key_available)
        st.divider()
        st.caption("Este app busca os dados ao abrir a página.")
        if st.button("Forçar atualização"):
            st.cache_data.clear()
            st.rerun()

    selected_date = get_brasilia_today()
    season = get_season_string(selected_date)
    games = get_games_for_date(selected_date)

    if games.empty:
        st.warning("Sem jogos para hoje.")
        return

    game_label = st.selectbox("Escolha o jogo", games["label"].tolist())
    selected_game = games.loc[games["label"] == game_label].iloc[0]

    away_df, home_df = get_matchup_context(
        int(selected_game["VISITOR_TEAM_ID"]), int(selected_game["HOME_TEAM_ID"]),
        selected_game["away_team_name"], selected_game["home_team_name"],
        season, api_key_available
    )

    render_matchup_header(selected_game)
    render_summary_cards(away_df, home_df, min_games, min_minutes, role_filter)
    render_game_rankings(away_df, home_df, min_games, min_minutes, role_filter, line_metric, line_value, use_market_line)

    selected_team = st.segmented_control("Time em análise", [selected_game["away_team_name"], selected_game["home_team_name"]], default=selected_game["away_team_name"])
    
    target_df = away_df if selected_team == selected_game["away_team_name"] else home_df
    sort_label = f"{line_metric} L10" if f"{line_metric} L10" in SORT_OPTIONS else "PRA L10"

# --- BLOCO CORRIGIDO ---
    # 1. Criamos a seleção do time
    selected_team = st.segmented_control(
        "Time em análise", 
        [selected_game["away_team_name"], selected_game["home_team_name"]], 
        default=selected_game["away_team_name"]
    )
    
    # 2. Definimos quem é o alvo e quem é o adversário usando os dados do selected_game
    if selected_team == selected_game["away_team_name"]:
        target_df = away_df
        # Pegamos a sigla do mandante (adversário) para o H2H
        opp_abbr = selected_game.get("HOME_TEAM_ABBR", selected_game["home_team_name"])
    else:
        target_df = home_df
        # Pegamos a sigla do visitante (adversário) para o H2H
        opp_abbr = selected_game.get("VISITOR_TEAM_ABBR", selected_game["away_team_name"])

    sort_label = f"{line_metric} L10" if f"{line_metric} L10" in SORT_OPTIONS else "PRA L10"

    # 3. Chamamos a função passando o opp_abbr no final
    render_team_section_v2(
        selected_team, target_df, season, min_games, min_minutes, role_filter,
        sort_label, False, chart_mode, line_metric, line_value, use_market_line,
        cards_per_row, opp_abbr
    )
if __name__ == "__main__":
    main()

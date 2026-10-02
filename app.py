import streamlit as st
import time
from datetime import date, datetime, timedelta
from config import (
    TEAM_LOOKUP, SORT_OPTIONS, ROLE_OPTIONS, CHART_OPTIONS, 
    LINE_METRIC_OPTIONS, APP_TIMEZONE
)
from api_nba import get_games_for_date
from api_odds import get_odds_api_key
from pdf_reader import get_season_string
from processamento import get_matchup_context

# Importando as funções do ui_components.py
from ui_components import (
    inject_css, 
    render_matchup_header,
    render_summary_cards,
    render_game_rankings,
    render_team_section_v2
)

def get_brasilia_today() -> date:
    return datetime.now(APP_TIMEZONE).date()


def run_nba_endpoint_diagnostic(team_id: int, season: str) -> list[dict]:
    """
    Executa chamadas isoladas, sem retry, para medir a resposta do stats.nba.com.
    Imports ficam locais para não alterar o startup normal do app.
    """
    from nba_api.stats.endpoints import (
        commonteamroster,
        leaguedashplayerstats,
        playergamelogs,
    )

    tests = [
        (
            "CommonTeamRoster",
            lambda: commonteamroster.CommonTeamRoster(
                team_id=team_id,
                season=season,
                timeout=8,
            ).get_data_frames()[0],
        ),
        (
            "LeagueDashPlayerStats",
            lambda: leaguedashplayerstats.LeagueDashPlayerStats(
                season=season,
                season_type_all_star="Regular Season",
                per_mode_detailed="PerGame",
                measure_type_detailed_defense="Base",
                last_n_games=0,
                month=0,
                opponent_team_id=0,
                pace_adjust="N",
                plus_minus="N",
                rank="N",
                period=0,
                team_id_nullable="",
                timeout=8,
            ).get_data_frames()[0],
        ),
        (
            "PlayerGameLogs",
            lambda: playergamelogs.PlayerGameLogs(
                team_id_nullable=team_id,
                season_nullable=season,
                season_type_nullable="Regular Season",
                timeout=8,
            ).get_data_frames()[0],
        ),
    ]

    results = []

    for name, fetch_fn in tests:
        started = time.perf_counter()
        try:
            df = fetch_fn()
            elapsed = time.perf_counter() - started
            results.append({
                "Endpoint": name,
                "Status": "OK",
                "Tempo (s)": round(elapsed, 2),
                "Linhas": int(len(df)),
                "Erro": "",
            })
        except Exception as exc:
            elapsed = time.perf_counter() - started
            results.append({
                "Endpoint": name,
                "Status": "ERRO",
                "Tempo (s)": round(elapsed, 2),
                "Linhas": 0,
                "Erro": f"{type(exc).__name__}: {exc}",
            })

    return results


def main():
    st.set_page_config(page_title="NBA Props Dashboard", page_icon="🏀", layout="wide")
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
        selected_date = st.date_input(
            "Data dos jogos",
            value=get_brasilia_today(),
            format="DD/MM/YYYY",
        )
        st.divider()
        chart_mode = st.pills("Gráfico", CHART_OPTIONS, default="Compacto")
        cards_per_row = st.pills("Cards/Linha", [1, 2], default=2)
        min_games = st.slider("Min Jogos", 0, 82, 5)
        min_minutes = st.slider("Min Minutos", 0, 40, 15)
        role_filter = st.pills("Jogadores", ROLE_OPTIONS, default="Todos")
        line_metric = st.pills("Métrica", LINE_METRIC_OPTIONS, default="PRA")
        line_value = st.number_input("Linha Manual", value=25.5, step=0.5)
        api_key_available = bool(get_odds_api_key())
        use_market_line = st.toggle("Usar BetMGM", value=api_key_available, disabled=not api_key_available)
        season_scope_label = st.pills(
            "Recorte estatístico",
            ["Temporada Regular", "Playoffs", "Play-In", "Tudo"],
            default="Temporada Regular",
        )

        season_scope_map = {
            "Temporada Regular": "Regular Season",
            "Playoffs": "Playoffs",
            "Play-In": "PlayIn",
            "Tudo": "All",
        }

        season_scope = season_scope_map.get(season_scope_label, "Regular Season")
        diagnostic_mode = st.toggle(
            "Modo diagnóstico NBA",
            value=False,
            help="Testa os endpoints individualmente sem carregar o confronto completo.",
        )
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

    st.caption(f"Temporada detectada: {season} • Recorte estatístico: {season_scope_label}")

    if games.empty:
        st.warning(f"Sem jogos para {selected_date.strftime('%d/%m/%Y')}.")
        return

    game_label = st.selectbox("Escolha o jogo", games["label"].tolist())
    selected_game = games.loc[games["label"] == game_label].iloc[0]

    if diagnostic_mode:
        st.info(
            "Modo diagnóstico ativo: o confronto completo não será carregado. "
            "Os endpoints serão testados individualmente."
        )
        if st.button("Testar endpoints NBA", type="primary", use_container_width=True):
            with st.status("Executando diagnóstico...", expanded=True) as status:
                results = run_nba_endpoint_diagnostic(
                    int(selected_game["VISITOR_TEAM_ID"]),
                    season,
                )
                for item in results:
                    status.write(
                        f"{item['Endpoint']}: {item['Status']} • "
                        f"{item['Tempo (s)']} s • {item['Linhas']} linhas"
                    )
                status.update(label="Diagnóstico concluído.", state="complete")

            st.dataframe(results, use_container_width=True, hide_index=True)

            failed = [item for item in results if item["Status"] != "OK"]
            if failed:
                st.warning("Um ou mais endpoints falharam. Copie a tabela/erros para compararmos.")
            else:
                st.success("Os três endpoints responderam dentro do limite do teste.")
        return

    try:
        away_df, home_df = get_matchup_context(
            int(selected_game["VISITOR_TEAM_ID"]),
            int(selected_game["HOME_TEAM_ID"]),
            selected_game["away_team_name"],
            selected_game["home_team_name"],
            season,
            use_market_line,
            season_scope=season_scope,
        )
        
    except Exception as exc:
        st.error("A NBA demorou ou falhou ao responder nas estatísticas do confronto. Tente novamente em alguns segundos ou use o botão de atualização.")
        st.exception(exc)
        return

    render_matchup_header(selected_game)
    render_summary_cards(away_df, home_df, min_games, min_minutes, role_filter)
    render_game_rankings(away_df, home_df, min_games, min_minutes, role_filter, line_metric, line_value, use_market_line)

    selected_team = st.segmented_control(
        "Time em análise",
        [selected_game["away_team_name"], selected_game["home_team_name"]],
        default=selected_game["away_team_name"]
    )

    if selected_team == selected_game["away_team_name"]:
        target_df = away_df
        opp_abbr = selected_game.get("HOME_TEAM_ABBR", selected_game["home_team_name"])
    else:
        target_df = home_df
        opp_abbr = selected_game.get("VISITOR_TEAM_ABBR", selected_game["away_team_name"])

    sort_label = f"{line_metric} L10" if f"{line_metric} L10" in SORT_OPTIONS else "PRA L10"

    render_team_section_v2(
        selected_team,
        target_df,
        season,
        min_games,
        min_minutes,
        role_filter,
        sort_label,
        False,
        chart_mode,
        line_metric,
        line_value,
        use_market_line,
        cards_per_row,
        opp_abbr,
    )

if __name__ == "__main__":
    main()

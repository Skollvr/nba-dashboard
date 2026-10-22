import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta
from config import (
    TEAM_LOOKUP, SORT_OPTIONS, ROLE_OPTIONS, CHART_OPTIONS, 
    LINE_METRIC_OPTIONS, APP_TIMEZONE
)
from api_nba import get_games_for_date
from api_lineups import clear_lineup_cache
from espn_context import clear_espn_pregame_cache
from api_odds import get_odds_api_key, clear_odds_cache
from pdf_reader import get_season_string, clear_injury_cache
from processamento import get_matchup_context

# Importando as funções do ui_components.py
from ui_components import (
    inject_css, 
    render_matchup_header,
    render_summary_cards,
    render_game_rankings,
    render_best_game_tips,
    render_team_section_v2
)

def get_brasilia_today() -> date:
    return datetime.now(APP_TIMEZONE).date()


def _shift_selected_date(days: int) -> None:
    current = st.session_state.get("selected_game_date", get_brasilia_today())
    st.session_state["selected_game_date"] = current + timedelta(days=days)


def _reset_selected_date_to_today() -> None:
    st.session_state["selected_game_date"] = get_brasilia_today()


def get_analysis_season(selected_date: date, today: date) -> str:
    """Use the selected game's own NBA season, including the 2026/27 opener.

    2025/26 must never be silently substituted for 2026/27. Small samples are
    visible and labeled as preliminary instead of switching seasons.
    """
    return get_season_string(selected_date)

def main():
    st.set_page_config(page_title="NBA Props Dashboard", page_icon="🏀", layout="wide")
    st.sidebar.radio(
        "Visual do dashboard",
        options=["Renovado", "Clássico"],
        horizontal=True,
        key="dashboard_visual_mode",
        help="Troque o visual sem perder o confronto, os filtros ou os dados.",
    )
    inject_css()

    if st.session_state["dashboard_visual_mode"] == "Renovado":
        st.markdown(
            '<div class="nova-brand">'
            '<div class="nova-brand-mark">🏀</div>'
            '<div><div class="nova-brand-kicker">NBA · ANÁLISE DE JOGADORES</div>'
            '<div class="nova-brand-title">NBA Props Dashboard</div>'
            '<div class="nova-brand-sub">'
            'Projeções, contexto e dados da temporada num só lugar.'
            '</div></div></div>'
            '<div class="nova-strip">'
            '<span>● Temporada 2026/27 disponível</span>'
            '<span>Dados ESPN + boletim oficial NBA</span>'
            '<span>Horários de Brasília</span>'
            '<span>Linhas BetMGM / manual</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            "Amostra inicial da temporada: projeções experimentais. "
            "Estatísticas e critérios de análise permanecem iguais ao visual clássico."
        )
    else:
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
        st.caption(
            "Dados de jogo: ESPN · Lesões: boletim oficial NBA · "
            "Linhas: BetMGM quando disponíveis, com opção manual."
        )

    today = get_brasilia_today()
    if "selected_game_date" not in st.session_state:
        st.session_state["selected_game_date"] = today

    with st.sidebar:
        st.header("Configurações" if st.session_state["dashboard_visual_mode"] == "Clássico" else "Painel de controle")

        st.subheader("Jogos")
        selected_date = st.date_input(
            "Data dos jogos (Brasília)",
            key="selected_game_date",
            format="DD/MM/YYYY",
        )

        nav_prev, nav_today, nav_next = st.columns(3)
        with nav_prev:
            st.button(
                "◀",
                help="Dia anterior",
                use_container_width=True,
                on_click=_shift_selected_date,
                args=(-1,),
            )
        with nav_today:
            st.button(
                "Hoje",
                use_container_width=True,
                on_click=_reset_selected_date_to_today,
            )
        with nav_next:
            st.button(
                "▶",
                help="Próximo dia",
                use_container_width=True,
                on_click=_shift_selected_date,
                args=(1,),
            )

        st.caption(f"Agenda em horário de Brasília: {selected_date.strftime('%d/%m/%Y')}")
        search_games = st.button(
            "Buscar jogos",
            type="primary",
            use_container_width=True,
        )
        st.divider()

        chart_mode = st.pills("Gráfico", CHART_OPTIONS, default="Compacto")
        cards_per_row = st.pills("Cards/Linha", [1, 2], default=2)
        # During the 2026/27 opening weeks most players have <5 games:
        # do not hide them by default while we validate ESPN's new-season feed.
        early_2627_test = (
            get_season_string(today) == "2026-27"
            and get_season_string(selected_date) == "2026-27"
            and today < date(2026, 11, 16)
        )
        min_games = st.slider("Min Jogos", 0, 82, 0 if early_2627_test else 5)
        min_minutes = st.slider("Min Minutos", 0, 40, 0 if early_2627_test else 15)
        role_filter = st.pills("Jogadores", ROLE_OPTIONS, default="Todos")
        line_metric = st.pills("Métrica", LINE_METRIC_OPTIONS, default="PTS")
        line_value = None
        api_key_available = bool(get_odds_api_key())
        use_market_line = st.toggle(
            "Priorizar BetMGM", value=True,
            help="Busca automaticamente a BetMGM. Se não existir cotação, permite linha manual por jogador.",
        )
        if not api_key_available and use_market_line:
            st.caption(
                "BetMGM não configurada: adicione SPORTSGAMEODDS_API_KEY aos "
                "Secrets do Streamlit Cloud. Linha manual continua disponível."
            )
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
        st.divider()
        st.caption(
            "A agenda só é consultada quando você clicar em Buscar jogos. "
            "Use a atualização pré-jogo para renovar apenas dados voláteis."
        )

        if st.button("🔄 Atualizar dados pré-jogo", use_container_width=True):
            clear_lineup_cache()
            clear_espn_pregame_cache()
            clear_injury_cache()
            clear_odds_cache()
            st.session_state.pop("loaded_matchup_key", None)
            st.rerun()

        if st.button("🧹 Recarregar tudo", use_container_width=True):
            st.cache_data.clear()
            st.session_state.pop("agenda_games", None)
            st.session_state.pop("agenda_date_key", None)
            st.session_state.pop("loaded_matchup_key", None)
            st.rerun()

    season = get_season_string(selected_date)
    analysis_season = get_analysis_season(selected_date, today)
    selected_date_key = selected_date.isoformat()

    st.caption(
        f"Temporada do jogo: {season} • Base estatística ESPN: {analysis_season} "
        f"• Recorte: {season_scope_label} • Somente jogos anteriores à data escolhida (Brasília)"
    )

    if search_games:
        st.session_state.pop("loaded_matchup_key", None)
        try:
            with st.spinner(f"Buscando jogos de {selected_date.strftime('%d/%m/%Y')}..."):
                games = get_games_for_date(selected_date)
        except Exception as exc:
            st.session_state.pop("agenda_games", None)
            st.session_state.pop("agenda_date_key", None)
            st.error("A NBA demorou ou falhou ao responder na consulta dos jogos. Tente novamente em alguns segundos.")
            st.caption(f"Detalhe técnico: {type(exc).__name__}")
            return

        st.session_state["agenda_games"] = games
        st.session_state["agenda_date_key"] = selected_date_key

    agenda_date_key = st.session_state.get("agenda_date_key")
    games = st.session_state.get("agenda_games")

    if agenda_date_key != selected_date_key or games is None:
        st.info(
            f"Selecione a data desejada e clique em **Buscar jogos** para carregar a agenda de "
            f"{selected_date.strftime('%d/%m/%Y')}."
        )
        return

    if games.empty:
        st.warning(
            f"Sem jogos para {selected_date.strftime('%d/%m/%Y')}. "
            "Escolha outra data e clique em Buscar jogos."
        )
        return

    game_label = st.selectbox("Escolha o jogo", games["label"].tolist())
    selected_game = games.loc[games["label"] == game_label].iloc[0]

    selected_game_key = (
        f"{selected_date.isoformat()}::"
        f"{selected_game['GAME_ID']}::"
        f"{season_scope}::"
        f"{analysis_season}::"
        f"{int(use_market_line)}"
    )

    if st.session_state.get("loaded_matchup_key") != selected_game_key:
        st.info("Jogo selecionado. Clique abaixo para carregar estatísticas, projeções e matchup.")
        if not st.button("Carregar confronto", type="primary", use_container_width=True):
            return
        st.session_state["loaded_matchup_key"] = selected_game_key

    try:
        with st.status("Carregando dados do confronto...", expanded=True) as load_status:
            away_df, home_df = get_matchup_context(
                int(selected_game["VISITOR_TEAM_ID"]),
                int(selected_game["HOME_TEAM_ID"]),
                selected_game["away_team_name"],
                selected_game["home_team_name"],
                analysis_season,
                use_market_line,
                season_scope=season_scope,
                roster_season=season,
                target_date=selected_date,
                progress_callback=load_status.write,
            )
            load_status.update(
                label="Dados do confronto carregados.",
                state="complete",
                expanded=False,
            )
        
    except Exception as exc:
        st.session_state.pop("loaded_matchup_key", None)
        st.error("A NBA demorou ou falhou ao responder nas estatísticas do confronto. Tente novamente em alguns segundos ou use o botão de atualização.")
        st.exception(exc)
        return

    render_matchup_header(selected_game)

    def _team_gp(df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0

        if "TEAM_GP_CURRENT" in df.columns:
            gp = pd.to_numeric(df["TEAM_GP_CURRENT"], errors="coerce").dropna()
            if not gp.empty:
                return int(round(float(gp.iloc[0])))

        if "SEASON_GP" in df.columns:
            gp = pd.to_numeric(df["SEASON_GP"], errors="coerce").dropna()
            if not gp.empty:
                return int(round(float(gp.max())))

        return 0

    min_model_games = 10
    away_gp = _team_gp(away_df)
    home_gp = _team_gp(home_df)

    if away_gp >= min_model_games and home_gp >= min_model_games:
        st.success(
            f"Amostra mínima atingida: {selected_game['away_team_name']} {away_gp} jogos • "
            f"{selected_game['home_team_name']} {home_gp} jogos. "
            f"O modelo está usando dados de {analysis_season}."
        )
    else:
        st.warning(
            f"Amostra inicial da temporada — referência mínima: {min_model_games} jogos por time. "
            f"{selected_game['away_team_name']}: {away_gp}/{min_model_games} • "
            f"{selected_game['home_team_name']}: {home_gp}/{min_model_games}. "
            f"Base exclusiva {analysis_season}, sem misturar outras temporadas. "
            "As projeções com amostra reduzida são experimentais, não adequadas ainda para apostas."
        )

    # A completed game with no player logs is a source failure, not a true
    # zero-game roster. Surface it before any projection is interpreted.
    for team_df, team_gp, team_name in (
        (away_df, away_gp, selected_game["away_team_name"]),
        (home_df, home_gp, selected_game["home_team_name"]),
    ):
        if team_df is None or team_df.empty:
            continue
        player_gp = pd.to_numeric(
            team_df.get("SEASON_GP", 0), errors="coerce"
        ).fillna(0)
        if team_gp > 0 and not player_gp.gt(0).any():
            st.warning(
                f"{team_name}: a ESPN identifica {team_gp} jogo(s) da temporada, "
                "mas não retornou game logs aproveitáveis para o roster atual. "
                "Confira a fonte antes de interpretar GP 0 como ausência de jogos."
            )

    # Technical ESPN validation stays in processing/tests. Only surface
    # information that affects decisions; remove old diagnostics and backtests.
    defense_available = any(
        df is not None and not df.empty
        and "OPP_DEF_SAMPLE_GP" in df
        and pd.to_numeric(df["OPP_DEF_SAMPLE_GP"], errors="coerce").fillna(0).gt(0).any()
        for df in (away_df, home_df)
    )
    if not defense_available:
        st.warning(
            "Sem amostra defensiva confiável para este confronto; o modelo "
            "mantém o matchup neutro até receber dados."
        )

    # Automatic BetMGM has priority; manual per-player values remain available.
    # No model-derived or estimated betting line is ever invented.
    manual_col = f"MANUAL_LINE_{line_metric}"
    market_col = f"BETMGM_{line_metric}_LINE"
    if use_market_line:
        fetch_status = (
            str(away_df["ODDS_FETCH_STATUS"].iloc[0])
            if away_df is not None and not away_df.empty
            and "ODDS_FETCH_STATUS" in away_df.columns
            else "BetMGM: fonte não confirmou o retorno"
        )
        available_market = sum(
            int(
                pd.to_numeric(
                    df.get(market_col, pd.Series(dtype=float)),
                    errors="coerce",
                ).gt(0).sum()
            )
            for df in (away_df, home_df)
            if df is not None and not df.empty
        )
        st.caption(
            f"{fetch_status} • {available_market} jogador(es) com linha "
            f"BetMGM de {line_metric}. As demais linhas podem ser inseridas manualmente."
        )

    line_rows = []

    for team_df, team_name in [
        (away_df, selected_game["away_team_name"]),
        (home_df, selected_game["home_team_name"]),
    ]:
        if team_df is None or team_df.empty:
            continue

        for _, player_row in team_df.iterrows():
            player_id = pd.to_numeric(player_row.get("PLAYER_ID"), errors="coerce")
            if pd.isna(player_id):
                continue

            line_rows.append(
                {
                    "_PLAYER_ID": int(player_id),
                    "Jogador": str(player_row.get("PLAYER", "")),
                    "Time": str(team_name),
                    "BetMGM": pd.to_numeric(
                        player_row.get(market_col), errors="coerce"
                    ) if use_market_line else float("nan"),
                    "Linha manual": float("nan"),
                }
            )

    if line_rows:
        manual_lines_df = pd.DataFrame(line_rows)

        missing_market = int(
            pd.to_numeric(manual_lines_df["BetMGM"], errors="coerce")
            .le(0).sum()
            + manual_lines_df["BetMGM"].isna().sum()
        )
        with st.expander(
            f"✏️ Gerar linha manual — {line_metric} "
            f"({missing_market} sem cotação BetMGM)" if use_market_line
            else f"✏️ Linhas manuais — {line_metric}",
            expanded=False,
        ):
            st.caption(
                "BetMGM tem prioridade quando estiver ativada e disponível. "
                "Se aparecer Linha indisponível, informe a linha desejada "
                "na coluna Linha manual. Não geramos cotação fictícia."
            )

            edited_lines = st.data_editor(
                manual_lines_df,
                hide_index=True,
                use_container_width=True,
                disabled=["Jogador", "Time", "BetMGM"],
                column_config={
                    "_PLAYER_ID": None,
                    "Jogador": st.column_config.TextColumn("Jogador"),
                    "Time": st.column_config.TextColumn("Time"),
                    "BetMGM": st.column_config.NumberColumn(
                        "Linha BetMGM", format="%.1f"
                    ),
                    "Linha manual": st.column_config.NumberColumn(
                        f"Linha manual {line_metric}",
                        min_value=0.5,
                        step=0.5,
                        format="%.1f",
                    ),
                },
                key=f"manual_lines::{selected_date_key}::{selected_game['GAME_ID']}::{line_metric}",
            )

        valid_lines = edited_lines.copy()
        valid_lines["Linha manual"] = pd.to_numeric(
            valid_lines["Linha manual"], errors="coerce"
        )
        valid_lines = valid_lines[
            valid_lines["Linha manual"].notna()
            & (valid_lines["Linha manual"] > 0)
        ]
        manual_line_map = dict(
            zip(
                valid_lines["_PLAYER_ID"].astype(int),
                valid_lines["Linha manual"].astype(float),
            )
        )

        away_df = away_df.copy()
        home_df = home_df.copy()

        away_df[manual_col] = pd.to_numeric(
            away_df["PLAYER_ID"], errors="coerce"
        ).map(manual_line_map)
        home_df[manual_col] = pd.to_numeric(
            home_df["PLAYER_ID"], errors="coerce"
        ).map(manual_line_map)

    def _render_panorama() -> None:
        # Keep the Panorama as the separate, less-cluttered space the user prefers.
        if st.session_state["dashboard_visual_mode"] == "Renovado":
            st.markdown(
                '<div class="nova-workspace-label">'
                'Panorama da partida'
                '<span>Melhores linhas, líderes e ranking sem poluir os cards</span>'
                '</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div class="nova-workspace-sub">Compare destaques do confronto '
                'e explore as melhores projeções. Sem linhas válidas, não '
                'calculamos Edge nem exibimos recomendações artificiais.</div>',
                unsafe_allow_html=True,
            )
        render_best_game_tips(
            away_df, home_df, min_games, min_minutes,
            line_metric, line_value, use_market_line,
        )
        render_summary_cards(
            away_df, home_df, min_games, min_minutes, role_filter
        )
        render_game_rankings(
            away_df, home_df, min_games, min_minutes, role_filter,
            line_metric, line_value, use_market_line,
        )

    def _render_players() -> None:
        selected_team = st.segmented_control(
            "Time em análise",
            [selected_game["away_team_name"], selected_game["home_team_name"]],
            default=selected_game["away_team_name"],
        )
        if selected_team == selected_game["away_team_name"]:
            target_df = away_df
            opp_abbr = selected_game.get("HOME_TEAM_ABBR", selected_game["home_team_name"])
        else:
            target_df = home_df
            opp_abbr = selected_game.get("VISITOR_TEAM_ABBR", selected_game["away_team_name"])

        sort_label = (
            f"{line_metric} L10"
            if f"{line_metric} L10" in SORT_OPTIONS else "PRA L10"
        )
        render_team_section_v2(
            selected_team, target_df, analysis_season,
            min_games, min_minutes, role_filter, sort_label, False,
            chart_mode, line_metric, line_value, use_market_line,
            cards_per_row, opp_abbr, season_scope=season_scope,
        )

    if st.session_state["dashboard_visual_mode"] == "Renovado":
        # Put the player's decision-oriented card first. Keep panorama and
        # rankings available in a second tab instead of above the full roster.
        players_tab, panorama_tab = st.tabs(["🏀 Jogadores", "📊 Panorama do confronto"])
        with players_tab:
            _render_players()
        with panorama_tab:
            _render_panorama()
    else:
        # Exact classic ordering retained for visual A/B comparison.
        _render_panorama()
        _render_players()

if __name__ == "__main__":
    main()

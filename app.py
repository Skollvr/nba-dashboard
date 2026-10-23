import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta
from config import (
    TEAM_LOOKUP, SORT_OPTIONS, ROLE_OPTIONS, CHART_OPTIONS, 
    LINE_METRIC_OPTIONS, APP_TIMEZONE
)
from api_nba import get_games_for_date
from api_lineups import clear_lineup_cache, get_daily_lineups_safe
from lineup_rotation import merge_nba_lineup_evidence, summarize_recent_rotation
from espn_context import clear_espn_pregame_cache
from api_odds import get_odds_api_key, clear_odds_cache
from pdf_reader import get_season_string, clear_injury_cache
from processamento import get_matchup_context
from assisted_minutes import apply_manual_minutes

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

def _pregame_minutes_allowed(selected_date, today, game) -> bool:
    """Manual adjustments are only for games not yet started (BRT)."""
    if selected_date < today:
        return False
    status = str(game.get("GAME_STATUS_TEXT", "") or "").lower()
    blocked = (
        "final", "finished", "in progress", "live", "halftime",
        "half time", "end of", "1st q", "2nd q", "3rd q", "4th q",
        "1st quarter", "2nd quarter", "3rd quarter", "4th quarter",
        "ot ", "encerrado", "intervalo", "ao vivo", "andamento",
    )
    if any(token in status for token in blocked):
        return False
    kickoff = pd.to_datetime(game.get("GAME_DATETIME_BRT"), errors="coerce", utc=True)
    if pd.notna(kickoff) and kickoff <= pd.Timestamp.now(tz="UTC"):
        return False
    return True


def _save_minute_edit(scope_key: str, audit_key: str, pid: int, input_key: str):
    """Streamlit callback runs before rerun, so widget state is safe to read."""
    try:
        minutes = float(st.session_state[input_key])
    except (KeyError, ValueError, TypeError):
        return
    if not 0 < minutes <= 48:
        return
    edits = dict(st.session_state.get(scope_key, {}))
    edits[int(pid)] = minutes
    st.session_state[scope_key] = edits
    audit = dict(st.session_state.get(audit_key, {}))
    audit[int(pid)] = datetime.now(APP_TIMEZONE).strftime("%d/%m/%Y %H:%M")
    st.session_state[audit_key] = audit


def _reset_minute_edit(
    scope_key: str, audit_key: str, pid: int, input_key: str, automatic: float
):
    edits = dict(st.session_state.get(scope_key, {}))
    edits.pop(int(pid), None)
    st.session_state[scope_key] = edits
    audit = dict(st.session_state.get(audit_key, {}))
    audit.pop(int(pid), None)
    st.session_state[audit_key] = audit
    st.session_state[input_key] = float(automatic)


def _reset_all_minute_edits(
    scope_key: str, audit_key: str, automatic_by_player: dict, input_prefix: str
):
    st.session_state[scope_key] = {}
    st.session_state[audit_key] = {}
    for player_id, automatic in automatic_by_player.items():
        key = f"{input_prefix}{int(player_id)}"
        if key in st.session_state:
            st.session_state[key] = float(automatic)


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
            '<span>BetMGM + FanDuel / manual</span>'
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
            "Linhas: BetMGM e FanDuel quando disponíveis, com opção manual."
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
        enable_betmgm = st.toggle(
            "Usar BetMGM", value=True,
            help="Busca linhas e odds BetMGM pela SportsGameOdds.",
        )
        enable_fanduel = st.toggle(
            "Usar FanDuel", value=False,
            help="Consulta FanDuel (fanduel) pela SportsGameOdds, incluída no plano gratuito; disponibilidade varia por partida e mercado.",
        )
        use_market_line = enable_betmgm or enable_fanduel
        if enable_betmgm and enable_fanduel:
            priority_label = st.radio(
                "Casa prioritária quando ambas tiverem linha",
                ["BetMGM", "FanDuel"], horizontal=True,
            )
            bookmaker_priority = priority_label.lower()
        else:
            bookmaker_priority = "fanduel" if enable_fanduel else "betmgm"
        if not api_key_available and use_market_line:
            st.caption(
                "SportsGameOdds sem chave: configure SPORTSGAMEODDS_API_KEY "
                "nos Secrets. Linhas manuais continuam disponíveis."
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
        f"{int(enable_betmgm)}::{int(enable_fanduel)}::{bookmaker_priority}"
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
                enable_betmgm,
                season_scope=season_scope,
                include_fanduel=enable_fanduel,
                bookmaker_priority=bookmaker_priority,
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

    # Today's live NBA lineup feed is OPTIONAL and informational.
    # Fail closed: keep ESPN projected statuses when the feed is missing.
    nba_lineup_note = "ESPN Depth Chart (estimativa)"
    nba_lineup_checked = ""
    if selected_date == today and _pregame_minutes_allowed(
        selected_date, today, selected_game
    ):
        nba_rows, nba_lineup_note, nba_lineup_checked = get_daily_lineups_safe(selected_date)
        away_df = merge_nba_lineup_evidence(
            away_df, nba_rows, int(selected_game["VISITOR_TEAM_ID"]),
            checked_brt=nba_lineup_checked,
        )
        home_df = merge_nba_lineup_evidence(
            home_df, nba_rows, int(selected_game["HOME_TEAM_ID"]),
            checked_brt=nba_lineup_checked,
        )

    render_matchup_header(selected_game)

    # One match-wide control, before Players/Panorama/Edge consumption.
    # Never show manual controls for finished games or historical replays.
    if _pregame_minutes_allowed(selected_date, today, selected_game):
        scope_key = f"manual_minutes::{selected_date_key}::{selected_game['GAME_ID']}"
        audit_key = scope_key + "::timestamps"
        input_prefix = scope_key + "::editor::"
        overrides = dict(st.session_state.get(scope_key, {}))
        roster = pd.concat([away_df, home_df], ignore_index=True)
        eligible = (
            pd.to_numeric(roster["SEASON_GP"], errors="coerce").fillna(0).gt(0)
            & pd.to_numeric(roster["PROJ_MIN_V1"], errors="coerce").fillna(0).gt(0)
            & ~roster["INJ_STATUS"].astype(str).isin(["Out", "Doubtful"])
            & ~roster["IS_UNAVAILABLE"].fillna(False).astype(bool)
        )
        editable_roster = roster.loc[eligible].drop_duplicates("PLAYER_ID")
        editable_roster = editable_roster.sort_values(
            ["TEAM_NAME", "PLAYER"], kind="stable"
        )
        automatic_by_player = {
            int(row["PLAYER_ID"]): float(row["PROJ_MIN_V1"])
            for _, row in editable_roster.iterrows()
        }
        player_names = {
            int(row["PLAYER_ID"]): (
                f"{row['PLAYER']} — {row['TEAM_NAME']}"
            )
            for _, row in editable_roster.iterrows()
        }
        with st.expander("⏱️ Revisão pré-jogo de minutos", expanded=False):
            st.caption(
                "Ajuste opcional por jogador, mantendo o valor automático. "
                "A alteração repercute em PTS, REB, AST, PRA, 3PM, FGA, 3PA, "
                "Edge e rankings. Só vale nesta sessão e neste confronto."
            )
            confirmed = int(roster["ROLE"].eq("Titular confirmado").sum())
            projected = int(roster["ROLE"].eq("Titular projetado").sum())
            st.caption(
                f"Escalações: {confirmed} titular(es) confirmado(s) • "
                f"{projected} projetado(s) • Fonte NBA: {nba_lineup_note}"
                + (f" • Consulta: {nba_lineup_checked} BRT" if nba_lineup_checked else "")
            )
            if confirmed == 0:
                st.caption(
                    "Sem confirmação de titulares pelo feed oficial neste confronto. "
                    "Depth chart e Top 5 por minutos são somente estimativas."
                )
            with st.expander("Ver situação do elenco", expanded=False):
                columns = [
                    "PLAYER", "TEAM_NAME", "ROLE", "LINEUP_SOURCE",
                    "INJ_STATUS", "L5_MIN", "L10_MIN", "PROJ_MIN_V1",
                ]
                table = roster[[col for col in columns if col in roster.columns]].copy()
                table = table.rename(columns={
                    "PLAYER": "Jogador", "TEAM_NAME": "Time",
                    "ROLE": "Escalação", "LINEUP_SOURCE": "Fonte",
                    "INJ_STATUS": "Lesão", "L5_MIN": "MIN L5",
                    "L10_MIN": "MIN L10", "PROJ_MIN_V1": "MIN automático",
                })
                st.dataframe(table, use_container_width=True, hide_index=True)
            if not automatic_by_player:
                st.info("Nenhum jogador elegível com projeção de minutos validada.")
            else:
                selected_pid = st.selectbox(
                    "Jogador a revisar",
                    list(automatic_by_player),
                    format_func=lambda player_id: player_names[player_id],
                    key=scope_key + "::selected_player",
                )
                player = editable_roster[
                    pd.to_numeric(editable_roster["PLAYER_ID"], errors="coerce")
                    == selected_pid
                ].iloc[0]
                automatic = automatic_by_player[selected_pid]
                input_key = f"{input_prefix}{selected_pid}"
                auto_seen = input_key + "::auto_seen"
                if selected_pid not in overrides and (
                    input_key not in st.session_state
                    or st.session_state.get(auto_seen) != automatic
                ):
                    st.session_state[input_key] = automatic
                st.session_state[auto_seen] = automatic

                status = str(player.get("ROLE", "Informação não confirmada"))
                injury = str(player.get("INJ_STATUS", "—"))
                source = str(player.get("MINUTES_SOURCE", "Modelo interno"))
                lineup_source = str(player.get("LINEUP_SOURCE", "Modelo interno"))
                st.caption(
                    f"Escalação: {status} ({lineup_source}) • Lesão: {injury} • "
                    f"Fonte dos minutos automáticos: {source}"
                )

                rotation = summarize_recent_rotation(player)
                st.markdown("**Rotação recente (ESPN)**")
                if rotation["sample"]:
                    r1, r2, r3, r4 = st.columns(4)
                    def minute_text(value):
                        return "—" if value is None else f"{value:.1f}"
                    with r1:
                        st.metric("Média L5", minute_text(rotation["l5_mean"]))
                    with r2:
                        st.metric("Média L10", minute_text(rotation["l10_mean"]))
                    with r3:
                        st.metric("Último jogo", minute_text(rotation["last_game"]))
                    with r4:
                        st.metric("Variação L5 − L10", minute_text(rotation["l5_vs_l10"]))
                    if len(rotation["values_oldest_first"]) >= 2:
                        plotted = pd.DataFrame({
                            "Minutos": rotation["values_oldest_first"]
                        })
                        plotted.index = range(1, len(plotted) + 1)
                        st.line_chart(plotted, height=155)
                    st.caption(
                        f"{rotation['sample']} jogo(s) registrado(s) • "
                        f"mín/máx: {rotation['minimum']:.1f} / "
                        f"{rotation['maximum']:.1f} min • "
                        "ordem do gráfico: mais antigo → mais recente. "
                        "Não significa titularidade nos jogos anteriores."
                    )
                    if rotation["sample"] < 5:
                        st.caption(
                            "Amostra reduzida da temporada. "
                            "Compare com cautela antes de ajustar os minutos."
                        )
                else:
                    st.caption("Sem sequência recente de minutos utilizável nesta temporada.")

                left, right = st.columns(2)
                with left:
                    st.metric("Minutos automáticos", f"{automatic:.1f}")
                with right:
                    st.number_input(
                        "Minutos esperados por você",
                        min_value=1.0,
                        max_value=48.0,
                        step=0.5,
                        format="%.1f",
                        key=input_key,
                    )
                save, reset = st.columns(2)
                with save:
                    st.button(
                        "Aplicar ajuste",
                        key=scope_key + "::save",
                        type="primary",
                        use_container_width=True,
                        on_click=_save_minute_edit,
                        args=(scope_key, audit_key, selected_pid, input_key),
                    )
                with reset:
                    st.button(
                        "Restaurar automático",
                        key=scope_key + "::reset_one",
                        use_container_width=True,
                        on_click=_reset_minute_edit,
                        args=(scope_key, audit_key, selected_pid, input_key, automatic),
                    )
                active_ids = [pid for pid in overrides if pid in automatic_by_player]
                if active_ids:
                    st.markdown("**Minutos ajustados neste confronto**")
                    stamps = st.session_state.get(audit_key, {})
                    st.dataframe(
                        pd.DataFrame([
                            {
                                "Jogador": player_names[pid],
                                "Automático": round(automatic_by_player[pid], 1),
                                "Manual": round(float(overrides[pid]), 1),
                                "Editado (BRT)": stamps.get(pid, ""),
                            }
                            for pid in active_ids
                        ]),
                        hide_index=True,
                        use_container_width=True,
                    )
                    st.button(
                        "Restaurar todos os minutos automáticos",
                        key=scope_key + "::reset_all",
                        on_click=_reset_all_minute_edits,
                        args=(scope_key, audit_key, automatic_by_player, input_prefix),
                    )
        # Read current session state after widget events, ensuring all tabs
        # and line calculations receive the same consistent model output.
        live_overrides = dict(st.session_state.get(scope_key, {}))
        away_df = apply_manual_minutes(away_df, live_overrides)
        home_df = apply_manual_minutes(home_df, live_overrides)
        changed_count = sum(
            int(df["MANUAL_MINUTES_ACTIVE"].sum())
            for df in (away_df, home_df)
            if "MANUAL_MINUTES_ACTIVE" in df.columns
        )
        if changed_count:
            st.caption(
                f"✏️ {changed_count} jogador(es) com minutos ajustados "
                "manualmente neste confronto • restante: projeção automática."
            )


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

    # Selected line is computed per market/player without conflating odds
    # from the two bookmakers. Raw prices remain visible in the editor.
    manual_col = f"MANUAL_LINE_{line_metric}"
    market_col = f"MARKET_{line_metric}_LINE"
    if use_market_line:
        fetch_status = (
            str(away_df["ODDS_FETCH_STATUS"].iloc[0])
            if away_df is not None and not away_df.empty
            and "ODDS_FETCH_STATUS" in away_df.columns
            else "Odds não confirmadas para este confronto"
        )
        st.caption(fetch_status)
        for book, active in (("BetMGM", enable_betmgm), ("FanDuel", enable_fanduel)):
            if not active:
                continue
            col = f"{book.upper()}_{line_metric}_LINE"
            available = sum(
                int(pd.to_numeric(df[col], errors="coerce").gt(0).sum())
                for df in (away_df, home_df)
                if df is not None and not df.empty and col in df.columns
            )
            failures = [
                part.strip() for part in fetch_status.split(" • ")
                if part.strip().startswith(f"{book}: HTTP ")
                or part.strip().startswith(f"{book}: não foi possível")
            ]
            if failures:
                st.warning(
                    f"{failures[0]} • Cobertura não verificada; "
                    "isso não significa que não existam linhas."
                )
            else:
                st.caption(
                    f"{book}: {available} jogador(es) com linha {line_metric}"
                    + (
                        " • Fonte prioritária"
                        if bookmaker_priority == book.lower() else ""
                    )
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
                        player_row.get(f"BETMGM_{line_metric}_LINE"), errors="coerce"
                    ) if enable_betmgm else float("nan"),
                    "FanDuel": pd.to_numeric(
                        player_row.get(f"FANDUEL_{line_metric}_LINE"), errors="coerce"
                    ) if enable_fanduel else float("nan"),
                    "Linha escolhida": pd.to_numeric(
                        player_row.get(market_col), errors="coerce"
                    ) if use_market_line else float("nan"),
                    "Odd Over": pd.to_numeric(
                        player_row.get(f"MARKET_{line_metric}_OVER_DEC"),
                        errors="coerce",
                    ) if use_market_line else float("nan"),
                    "Odd Under": pd.to_numeric(
                        player_row.get(f"MARKET_{line_metric}_UNDER_DEC"),
                        errors="coerce",
                    ) if use_market_line else float("nan"),
                    "Linha manual": float("nan"),
                }
            )

    if line_rows:
        manual_lines_df = pd.DataFrame(line_rows)

        missing_market = int(
            pd.to_numeric(manual_lines_df["Linha escolhida"], errors="coerce")
            .le(0).sum()
            + manual_lines_df["Linha escolhida"].isna().sum()
        )
        with st.expander(
            f"✏️ Gerar linha manual — {line_metric} "
            f"({missing_market} sem cotação ativa)" if use_market_line
            else f"✏️ Linhas manuais — {line_metric}",
            expanded=False,
        ):
            st.caption(
                "A casa escolhida tem prioridade quando oferecer uma linha. "
                "Se aparecer Linha indisponível, informe a linha desejada "
                "na coluna Linha manual. Não geramos cotação fictícia."
            )

            edited_lines = st.data_editor(
                manual_lines_df,
                hide_index=True,
                use_container_width=True,
                disabled=[
                    "Jogador", "Time", "BetMGM", "FanDuel",
                    "Linha escolhida", "Odd Over", "Odd Under",
                ],
                column_config={
                    "_PLAYER_ID": None,
                    "Jogador": st.column_config.TextColumn("Jogador"),
                    "Time": st.column_config.TextColumn("Time"),
                    "BetMGM": st.column_config.NumberColumn(
                        "Linha BetMGM", format="%.1f"
                    ),
                    "FanDuel": st.column_config.NumberColumn(
                        "Linha FanDuel", format="%.1f"
                    ),
                    "Linha escolhida": st.column_config.NumberColumn(
                        "Linha prioritária", format="%.1f"
                    ),
                    "Odd Over": st.column_config.NumberColumn(
                        "Odd Over", format="%.2f"
                    ),
                    "Odd Under": st.column_config.NumberColumn(
                        "Odd Under", format="%.2f"
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
            show_today_lineups=(selected_date == today),
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

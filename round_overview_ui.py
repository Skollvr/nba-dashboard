"""Round-wide overview from real schedule and existing immutable predictions.

Never synthesize fresh predictions for unsnapshotted games or imply a quoted
market line is available when the data source returned none.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from api_nba import get_games_for_date
from model_tracking import SNAPSHOTS, load_jsonl

BRT = ZoneInfo("America/Sao_Paulo")


def build_round_board(schedule: pd.DataFrame, snapshots: list[dict]) -> pd.DataFrame:
    columns = ["game_id", "Jogo", "Horário BRT", "Situação",
               "Projeções guardadas", "Jogadores cobertos", "Linhas reais"]
    if schedule is None or schedule.empty:
        return pd.DataFrame(columns=columns)
    records = pd.DataFrame(snapshots)
    count_map = {}
    if not records.empty and "game_id" in records:
        for gid, rows in records.groupby("game_id"):
            count_map[str(gid)] = {
                "projections": len(rows),
                "players": rows["player_id"].nunique() if "player_id" in rows else 0,
                "quotes": int(pd.to_numeric(rows["line"], errors="coerce").gt(0).sum())
                if "line" in rows else 0,
            }
    result = []
    for _, game in schedule.iterrows():
        gid = str(game.get("GAME_ID") or "")
        counts = count_map.get(gid, {})
        result.append({
            "game_id": gid,
            "Jogo": str(game.get("label") or
                        (str(game.get("away_team_name") or "") + " x " +
                         str(game.get("home_team_name") or ""))),
            "Horário BRT": str(game.get("GAME_TIME_BRT") or "A confirmar"),
            "Situação": str(game.get("GAME_STATUS_TEXT") or "A confirmar"),
            "Projeções guardadas": counts.get("projections", 0),
            "Jogadores cobertos": counts.get("players", 0),
            "Linhas reais": counts.get("quotes", 0),
        })
    return pd.DataFrame(result, columns=columns)


def recorded_players(snapshots: list[dict], day: date, metric: str) -> pd.DataFrame:
    """Frozen observations only, from games with tipoff on BRT calendar day."""
    rows = []
    for snap in snapshots:
        if snap.get("metric") != metric:
            continue
        try:
            stamp = pd.to_datetime(snap.get("tipoff_utc"), utc=True)
            if pd.isna(stamp) or stamp.tz_convert(BRT).date() != day:
                continue
        except (ValueError, TypeError):
            continue
        rows.append(snap)
    columns = ["Jogador", "Projeção V4", "V4.1 experimental", "Minutos", "Amostra", "Linha", "Fonte", "Jogo ESPN"]
    if not rows:
        return pd.DataFrame(columns=columns)
    result = []
    for r in rows:
        result.append({
            "Jogador": r.get("player", ""),
            "Projeção V4": r.get("projection"),
            "V4.1 experimental": r.get("projection_v41"),
            "Minutos": r.get("minutes"),
            "Amostra": r.get("season_gp"),
            "Linha": r.get("line"),
            "Fonte": r.get("line_source"),
            "Jogo ESPN": str(r.get("game_id")),
        })
    return pd.DataFrame(result, columns=columns).sort_values(
        ["Projeção V4", "Jogador"], ascending=[False, True]
    ).reset_index(drop=True)


def render_round_overview() -> None:
    st.markdown("## 📅 Radar da rodada")
    st.caption(
        "Agenda ESPN da data selecionada e previsões V4 realmente congeladas "
        "antes das partidas. Jogos sem captura aparecem como sem dados, "
        "nunca com projeções inventadas."
    )
    today = datetime.now(BRT).date()
    selected = st.date_input("Data da rodada (Brasília)", today, key="radar_date", format="DD/MM/YYYY")
    if st.button("Consultar agenda da rodada", type="primary", use_container_width=True):
        try:
            with st.spinner("Consultando agenda ESPN..."):
                schedule = get_games_for_date(selected)
        except Exception as exc:
            st.error("Não foi possível consultar a agenda ESPN. Tente novamente.")
            st.caption(f"Fonte indisponível: {type(exc).__name__}")
            return
        st.session_state["radar_schedule"] = schedule
        st.session_state["radar_schedule_date"] = selected.isoformat()
    schedule = st.session_state.get("radar_schedule")
    if st.session_state.get("radar_schedule_date") != selected.isoformat() or schedule is None:
        st.info("Selecione a data e consulte a agenda para listar todas as partidas.")
        return
    if schedule.empty:
        st.info("A ESPN não retornou partidas para a data selecionada.")
        return

    snapshots = load_jsonl(SNAPSHOTS)
    board = build_round_board(schedule, snapshots)
    games_with_forecast = int(board["Projeções guardadas"].gt(0).sum())
    games_with_quotes = int(board["Linhas reais"].gt(0).sum())
    a, b, c = st.columns(3)
    a.metric("Partidas na agenda", len(board))
    b.metric("Com projeções congeladas", games_with_forecast)
    c.metric("Com mercado registrado", games_with_quotes)
    if games_with_forecast < len(board):
        st.warning("A cobertura está incompleta. Não interprete jogos sem captura como ausência de oportunidades.")
    st.dataframe(board.drop(columns=["game_id"]), use_container_width=True, hide_index=True)

    selected_game = st.selectbox(
        "Abrir um confronto específico",
        options=board["game_id"].tolist(),
        format_func=lambda game_id: str(board.loc[board["game_id"] == game_id, "Jogo"].iloc[0]),
        key="radar_game_selection",
    )
    if st.button("Analisar confronto selecionado", use_container_width=True):
        st.session_state["selected_game_date"] = selected
        st.session_state["agenda_games"] = schedule
        st.session_state["agenda_date_key"] = selected.isoformat()
        st.session_state["day_board_selected_game_id"] = selected_game
        st.session_state["main_section"] = "🏀 Analisar jogos"
        st.rerun()

    st.markdown("#### Jogadores com projeções realmente registradas")
    metric = st.pills(
        "Fundamento do radar",
        ["3PA", "AST", "PTS", "REB", "3PM", "FGA", "PRA"], default="3PA",
        key="radar_metric",
    )
    rows = recorded_players(snapshots, selected, metric or "3PA")
    if rows.empty:
        st.info("Não há projeções pré-jogo congeladas deste fundamento para esta data.")
    else:
        st.caption(f"{len(rows)} projeções congeladas. V4.1 é experimental e não altera o V4.")
        st.dataframe(rows, use_container_width=True, hide_index=True)

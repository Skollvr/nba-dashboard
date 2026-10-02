"""Minimal Streamlit probe for testing NBA public CDN access from cloud hosts.

This file is intentionally isolated from the dashboard motor. It verifies whether
the deployment can reach the NBA schedule feed and a known completed-game
boxscore before we change any production data source.
"""

from __future__ import annotations

import time
from typing import Any

import requests
import streamlit as st


SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
BOXSCORE_URL = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
}


def fetch_json(url: str, timeout: int = 12) -> tuple[int | None, float, Any, str | None]:
    started = time.perf_counter()
    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout)
        elapsed = time.perf_counter() - started
        response.raise_for_status()
        return response.status_code, elapsed, response.json(), None
    except Exception as exc:
        elapsed = time.perf_counter() - started
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status, elapsed, None, f"{type(exc).__name__}: {exc}"


def extract_players(payload: Any) -> list[dict[str, Any]]:
    game = payload.get("game", {}) if isinstance(payload, dict) else {}
    rows: list[dict[str, Any]] = []

    for side in ("awayTeam", "homeTeam"):
        team = game.get(side) or {}
        for player in team.get("players") or []:
            stats = player.get("statistics") or {}
            rows.append(
                {
                    "team": team.get("teamTricode"),
                    "player": player.get("name"),
                    "player_id": player.get("personId"),
                    "position": player.get("position"),
                    "starter": player.get("starter"),
                    "minutes": stats.get("minutesCalculated") or stats.get("minutes"),
                    "pts": stats.get("points"),
                    "reb": stats.get("reboundsTotal"),
                    "ast": stats.get("assists"),
                    "3pm": stats.get("threePointersMade"),
                    "3pa": stats.get("threePointersAttempted"),
                    "fga": stats.get("fieldGoalsAttempted"),
                }
            )
    return rows


st.set_page_config(page_title="NBA CDN Cloud Probe", page_icon="🏀", layout="wide")

st.title("🏀 NBA CDN — teste de acesso em cloud")
st.caption(
    "Teste isolado: não altera o motor do dashboard. "
    "Serve apenas para verificar se o host consegue acessar cdn.nba.com."
)

game_id = st.text_input(
    "Game ID concluído para testar o boxscore",
    value="0022500001",
    help="Use um jogo já encerrado. O padrão é um jogo da temporada 2025/26.",
).strip()

if st.button("Executar teste", type="primary"):
    with st.spinner("Consultando a NBA CDN..."):
        schedule_status, schedule_time, schedule_data, schedule_error = fetch_json(SCHEDULE_URL)
        box_status, box_time, box_data, box_error = fetch_json(
            BOXSCORE_URL.format(game_id=game_id)
        )

    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Agenda")
        st.metric("HTTP", schedule_status if schedule_status is not None else "erro")
        st.metric("Tempo", f"{schedule_time:.2f}s")
        if schedule_error:
            st.error(schedule_error)
        else:
            dates = (schedule_data or {}).get("leagueSchedule", {}).get("gameDates", [])
            st.success(f"NBA CDN respondeu. Datas encontradas: {len(dates)}")

    with c2:
        st.subheader("Boxscore")
        st.metric("HTTP", box_status if box_status is not None else "erro")
        st.metric("Tempo", f"{box_time:.2f}s")
        if box_error:
            st.error(box_error)
        else:
            players = extract_players(box_data)
            game = (box_data or {}).get("game", {})
            st.success(
                f"NBA CDN respondeu. Jogadores no payload: {len(players)} | "
                f"status do jogo: {game.get('gameStatusText', game.get('gameStatus'))}"
            )

    if box_data:
        players = extract_players(box_data)
        if players:
            st.subheader("Amostra das estatísticas recebidas")
            st.dataframe(players, use_container_width=True, hide_index=True)

    if schedule_error or box_error:
        st.warning(
            "Se o teste funcionar localmente mas falhar no Streamlit Cloud, "
            "o problema continua sendo o acesso do host à fonte. "
            "Se ambos responderem 200 na cloud, temos uma rota promissora para "
            "substituir os endpoints pesados de stats.nba.com."
        )
    else:
        st.success(
            "Os dois testes responderam com sucesso. Próxima etapa: montar game logs "
            "da temporada a partir dos boxscores e comparar com o modelo atual."
        )

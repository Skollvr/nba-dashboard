"""Minimal Streamlit probe for testing ESPN NBA endpoints from cloud hosts.

This file stays isolated from the dashboard motor. It checks whether Streamlit
Cloud can reach ESPN's public NBA scoreboard, game summary/boxscore, team
rosters, and injury endpoints before we replace any existing data source.
"""

from __future__ import annotations

import time
from typing import Any

import requests
import streamlit as st


ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
ESPN_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
)
ESPN_ROSTER_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}/roster"
)
ESPN_INJURIES_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}/injuries"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.espn.com/",
}


def fetch_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 12,
) -> tuple[int | None, float, Any, str | None]:
    started = time.perf_counter()
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        elapsed = time.perf_counter() - started
        response.raise_for_status()
        return response.status_code, elapsed, response.json(), None
    except Exception as exc:
        elapsed = time.perf_counter() - started
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status, elapsed, None, f"{type(exc).__name__}: {exc}"


def normalize_date(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits[:8]


def event_teams(event: dict[str, Any]) -> list[dict[str, Any]]:
    competitions = event.get("competitions") or []
    if not competitions:
        return []
    competitors = competitions[0].get("competitors") or []
    out: list[dict[str, Any]] = []
    for competitor in competitors:
        team = competitor.get("team") or {}
        out.append(
            {
                "id": str(team.get("id") or ""),
                "abbr": team.get("abbreviation") or team.get("shortDisplayName"),
                "name": team.get("displayName") or team.get("name"),
                "home_away": competitor.get("homeAway"),
            }
        )
    return out


def _stat_pairs(group: dict[str, Any], athlete_entry: dict[str, Any]) -> dict[str, Any]:
    values = athlete_entry.get("stats") or []
    labels = group.get("labels") or group.get("names") or group.get("keys") or []
    keys = group.get("keys") or labels

    out: dict[str, Any] = {}
    for idx, value in enumerate(values):
        if idx < len(labels):
            out[str(labels[idx])] = value
        if idx < len(keys):
            out.setdefault(str(keys[idx]), value)
    return out


def extract_boxscore_players(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    rows: list[dict[str, Any]] = []
    boxscore = payload.get("boxscore") or {}

    for team_block in boxscore.get("players") or []:
        team = team_block.get("team") or {}
        team_name = team.get("abbreviation") or team.get("shortDisplayName") or team.get("displayName")

        for group in team_block.get("statistics") or []:
            for entry in group.get("athletes") or []:
                athlete = entry.get("athlete") or {}
                stat_map = _stat_pairs(group, entry)
                row = {
                    "team": team_name,
                    "player": (
                        athlete.get("displayName")
                        or athlete.get("fullName")
                        or athlete.get("shortName")
                    ),
                    "player_id": athlete.get("id"),
                    "position": (athlete.get("position") or {}).get("abbreviation"),
                    "starter": entry.get("starter"),
                }

                # Preserve ESPN labels directly so we can see exactly what the
                # cloud response exposes (MIN, FG, 3PT, REB, AST, PTS, etc.).
                for key, value in stat_map.items():
                    if key and key not in row:
                        row[key] = value

                rows.append(row)

    return rows


def extract_roster_players(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    rows: list[dict[str, Any]] = []
    raw_athletes = payload.get("athletes") or []

    for entry in raw_athletes:
        candidates = entry.get("items") if isinstance(entry, dict) else None
        if not candidates:
            candidates = [entry]

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            athlete = candidate.get("athlete") or candidate
            if not isinstance(athlete, dict):
                continue

            player_id = athlete.get("id")
            name = athlete.get("displayName") or athlete.get("fullName")
            if not player_id or not name:
                continue

            position = athlete.get("position") or {}
            rows.append(
                {
                    "player_id": player_id,
                    "player": name,
                    "position": position.get("abbreviation") if isinstance(position, dict) else position,
                    "jersey": athlete.get("jersey"),
                }
            )

    # Deduplicate in case ESPN groups the same athlete more than once.
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        deduped[str(row["player_id"])] = row
    return list(deduped.values())


def count_injuries(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0

    injuries = payload.get("injuries")
    if isinstance(injuries, list):
        total = 0
        for item in injuries:
            if isinstance(item, dict) and isinstance(item.get("items"), list):
                total += len(item["items"])
            else:
                total += 1
        return total

    items = payload.get("items")
    return len(items) if isinstance(items, list) else 0


st.set_page_config(page_title="ESPN NBA Cloud Probe", page_icon="🏀", layout="wide")

st.title("🏀 ESPN NBA — teste de acesso em cloud")
st.caption(
    "Teste isolado: não altera o dashboard. Ele verifica se o Streamlit Cloud "
    "consegue acessar agenda, boxscore, roster e lesões pela ESPN."
)

date_raw = st.text_input(
    "Data de um dia com jogos concluídos",
    value="2025-10-21",
    help="Formato YYYY-MM-DD. O teste usa a agenda da ESPN para descobrir o event ID.",
).strip()

if st.button("Executar teste", type="primary"):
    date_key = normalize_date(date_raw)

    if len(date_key) != 8:
        st.error("Informe uma data válida no formato YYYY-MM-DD.")
        st.stop()

    with st.spinner("Consultando a ESPN..."):
        scoreboard_status, scoreboard_time, scoreboard_data, scoreboard_error = fetch_json(
            ESPN_SCOREBOARD_URL,
            params={"dates": date_key, "limit": 100},
        )

    events = (scoreboard_data or {}).get("events", []) if scoreboard_data else []

    preferred_event = None
    for event in events:
        status = ((event.get("status") or {}).get("type") or {})
        if status.get("completed") is True:
            preferred_event = event
            break
    if preferred_event is None and events:
        preferred_event = events[0]

    event_id = str((preferred_event or {}).get("id") or "")
    teams = event_teams(preferred_event or {})

    summary_status = roster_status = injuries_status = None
    summary_time = roster_time = injuries_time = 0.0
    summary_data = roster_data = injuries_data = None
    summary_error = roster_error = injuries_error = None

    if event_id:
        with st.spinner("Consultando boxscore e contexto do jogo..."):
            summary_status, summary_time, summary_data, summary_error = fetch_json(
                ESPN_SUMMARY_URL,
                params={"event": event_id},
            )

        team_id = teams[0]["id"] if teams else ""
        if team_id:
            with st.spinner("Consultando roster e lesões..."):
                roster_status, roster_time, roster_data, roster_error = fetch_json(
                    ESPN_ROSTER_URL.format(team_id=team_id)
                )
                injuries_status, injuries_time, injuries_data, injuries_error = fetch_json(
                    ESPN_INJURIES_URL.format(team_id=team_id)
                )

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Agenda ESPN")
        st.metric("HTTP", scoreboard_status if scoreboard_status is not None else "erro")
        st.metric("Tempo", f"{scoreboard_time:.2f}s")
        if scoreboard_error:
            st.error(scoreboard_error)
        else:
            st.success(f"ESPN respondeu. Jogos encontrados: {len(events)}")
            if preferred_event:
                st.write(
                    f"Evento usado: **{preferred_event.get('name') or preferred_event.get('shortName')}** "
                    f"(ID {event_id})"
                )

    with c2:
        st.subheader("Boxscore ESPN")
        st.metric("HTTP", summary_status if summary_status is not None else "—")
        st.metric("Tempo", f"{summary_time:.2f}s")
        if not event_id:
            st.warning("Nenhum jogo foi encontrado nessa data.")
        elif summary_error:
            st.error(summary_error)
        else:
            players = extract_boxscore_players(summary_data)
            st.success(f"Summary respondeu. Jogadores extraídos: {len(players)}")

    c3, c4 = st.columns(2)
    with c3:
        st.subheader("Roster ESPN")
        st.metric("HTTP", roster_status if roster_status is not None else "—")
        st.metric("Tempo", f"{roster_time:.2f}s")
        if roster_error:
            st.error(roster_error)
        elif roster_data is not None:
            roster_players = extract_roster_players(roster_data)
            team_label = teams[0]["name"] if teams else "time"
            st.success(f"{team_label}: {len(roster_players)} jogadores extraídos")

    with c4:
        st.subheader("Lesões ESPN")
        st.metric("HTTP", injuries_status if injuries_status is not None else "—")
        st.metric("Tempo", f"{injuries_time:.2f}s")
        if injuries_error:
            st.error(injuries_error)
        elif injuries_data is not None:
            st.success(f"Endpoint respondeu. Registros de lesão encontrados: {count_injuries(injuries_data)}")

    if summary_data:
        players = extract_boxscore_players(summary_data)
        if players:
            st.subheader("Amostra das estatísticas recebidas")
            st.dataframe(players, use_container_width=True, hide_index=True)

    if roster_data:
        roster_players = extract_roster_players(roster_data)
        if roster_players:
            with st.expander("Ver amostra do roster"):
                st.dataframe(roster_players, use_container_width=True, hide_index=True)

    statuses = [scoreboard_status, summary_status, roster_status]
    if all(status == 200 for status in statuses):
        st.success(
            "Os endpoints essenciais da ESPN responderam HTTP 200. "
            "Se este resultado estiver no Streamlit Cloud, a próxima etapa é "
            "montar game logs 2026/27 e comparar os dados com o modelo atual."
        )
    elif scoreboard_error or summary_error or roster_error:
        st.warning(
            "Algum endpoint essencial falhou. O painel acima mostra exatamente "
            "qual parte da ESPN está ou não acessível a partir deste host."
        )

"""Streamlit probe for ESPN NBA cloud access and ESPN-vs-NBA stat equivalence.

The dashboard motor is not touched. In Streamlit Cloud, ESPN should remain
reachable even when cdn.nba.com is blocked. Locally, where both sources are
reachable, this page compares the same completed game's player boxscore fields.
"""

from __future__ import annotations

import re
import time
import unicodedata
from typing import Any

import pandas as pd
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

NBA_BOXSCORE_URL = (
    "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"
)

HEADERS_ESPN = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.espn.com/",
}

HEADERS_NBA = {
    "User-Agent": HEADERS_ESPN["User-Agent"],
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
}


def fetch_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 12,
) -> tuple[int | None, float, Any, str | None]:
    started = time.perf_counter()
    try:
        response = requests.get(
            url,
            params=params,
            headers=headers or HEADERS_ESPN,
            timeout=timeout,
        )
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


def normalize_name(raw: Any) -> str:
    text = unicodedata.normalize("NFKD", str(raw or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", text.lower())


def to_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def made_attempted(value: Any) -> tuple[float | None, float | None]:
    if value is None:
        return None, None
    match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", str(value))
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def nba_minutes(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value)
    match = re.search(r"PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", text)
    if match:
        mins = float(match.group(1) or 0)
        secs = float(match.group(2) or 0)
        return round(mins + secs / 60.0, 2)
    try:
        return float(text)
    except ValueError:
        return None


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


def extract_espn_players(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    rows: list[dict[str, Any]] = []
    for team_block in (payload.get("boxscore") or {}).get("players") or []:
        team = team_block.get("team") or {}
        team_abbr = team.get("abbreviation") or team.get("shortDisplayName")

        for stat_block in team_block.get("statistics") or []:
            keys = stat_block.get("keys") or stat_block.get("names") or []
            for entry in stat_block.get("athletes") or []:
                athlete = entry.get("athlete") or {}
                stats = entry.get("stats") or []
                stat_map = {str(k).lower(): v for k, v in zip(keys, stats)}

                fg_made, fg_attempted = made_attempted(stat_map.get("fg"))
                three_made, three_attempted = made_attempted(
                    stat_map.get("3pt") or stat_map.get("3p")
                )

                name = (
                    athlete.get("displayName")
                    or athlete.get("fullName")
                    or athlete.get("shortName")
                )
                position = athlete.get("position") or {}

                rows.append(
                    {
                        "team": team_abbr,
                        "player": name,
                        "match_key": f"{team_abbr}:{normalize_name(name)}",
                        "provider_id": athlete.get("id"),
                        "position": position.get("abbreviation") if isinstance(position, dict) else position,
                        "starter": bool(entry.get("starter")),
                        "min": to_number(stat_map.get("min")),
                        "pts": to_number(stat_map.get("pts")),
                        "reb": to_number(stat_map.get("reb")),
                        "ast": to_number(stat_map.get("ast")),
                        "3pm": three_made,
                        "3pa": three_attempted,
                        "fgm": fg_made,
                        "fga": fg_attempted,
                    }
                )
    return rows


def extract_nba_players(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    game = payload.get("game") or {}
    rows: list[dict[str, Any]] = []

    for team in (game.get("awayTeam") or {}, game.get("homeTeam") or {}):
        team_abbr = team.get("teamTricode")
        for player in team.get("players") or []:
            stats = player.get("statistics") or {}
            name = player.get("name") or " ".join(
                x for x in [player.get("firstName"), player.get("familyName")] if x
            )
            rows.append(
                {
                    "team": team_abbr,
                    "player": name,
                    "match_key": f"{team_abbr}:{normalize_name(name)}",
                    "provider_id": player.get("personId"),
                    "position": player.get("position"),
                    "starter": str(player.get("starter")) in {"1", "true", "True"},
                    "min": nba_minutes(stats.get("minutesCalculated") or stats.get("minutes")),
                    "pts": to_number(stats.get("points")),
                    "reb": to_number(stats.get("reboundsTotal")),
                    "ast": to_number(stats.get("assists")),
                    "3pm": to_number(stats.get("threePointersMade")),
                    "3pa": to_number(stats.get("threePointersAttempted")),
                    "fgm": to_number(stats.get("fieldGoalsMade")),
                    "fga": to_number(stats.get("fieldGoalsAttempted")),
                }
            )
    return rows


def extract_roster_players(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    rows: list[dict[str, Any]] = []
    for entry in payload.get("athletes") or []:
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


def comparison_table(
    espn_rows: list[dict[str, Any]],
    nba_rows: list[dict[str, Any]],
) -> tuple[pd.DataFrame, int, int]:
    espn = pd.DataFrame(espn_rows)
    nba = pd.DataFrame(nba_rows)
    if espn.empty or nba.empty:
        return pd.DataFrame(), 0, 0

    fields = ["min", "pts", "reb", "ast", "3pm", "3pa", "fga"]
    merged = espn.merge(
        nba,
        on="match_key",
        how="inner",
        suffixes=("_espn", "_nba"),
    )

    if merged.empty:
        return merged, 0, 0

    for field in fields:
        left = pd.to_numeric(merged[f"{field}_espn"], errors="coerce")
        right = pd.to_numeric(merged[f"{field}_nba"], errors="coerce")
        if field == "min":
            merged[f"{field}_ok"] = (left - right).abs().le(1.0) | (left.isna() & right.isna())
        else:
            merged[f"{field}_ok"] = left.eq(right) | (left.isna() & right.isna())

    merged["starter_ok"] = merged["starter_espn"].eq(merged["starter_nba"])
    merged["position_ok"] = (
        merged["position_espn"].fillna("").astype(str).str.upper()
        == merged["position_nba"].fillna("").astype(str).str.upper()
    )

    stat_ok_cols = [f"{field}_ok" for field in fields]
    merged["stats_exact"] = merged[stat_ok_cols].all(axis=1)
    exact_players = int(merged["stats_exact"].sum())

    display_cols = [
        "team_espn",
        "player_espn",
        "position_espn",
        "position_nba",
        "starter_espn",
        "starter_nba",
    ]
    for field in fields:
        display_cols.extend([f"{field}_espn", f"{field}_nba", f"{field}_ok"])
    display_cols.append("stats_exact")

    return merged[display_cols], len(merged), exact_players


st.set_page_config(page_title="ESPN NBA Equivalence Probe", page_icon="🏀", layout="wide")

st.title("🏀 ESPN NBA — acesso cloud + equivalência de estatísticas")
st.caption(
    "A ESPN é testada na cloud. Quando a NBA CDN também estiver acessível (normalmente local), "
    "o mesmo jogo é comparado campo a campo. Nada do motor atual é alterado."
)

c_input1, c_input2 = st.columns(2)
with c_input1:
    date_raw = st.text_input(
        "Data de um jogo concluído",
        value="2025-10-21",
        help="Formato YYYY-MM-DD.",
    ).strip()
with c_input2:
    nba_game_id = st.text_input(
        "NBA Game ID do mesmo jogo",
        value="0022500001",
        help="Padrão atual: Houston Rockets @ Oklahoma City Thunder em 21/10/2025.",
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
            headers=HEADERS_ESPN,
        )

    events = (scoreboard_data or {}).get("events", []) if scoreboard_data else []

    # Prefer Houston @ Oklahoma City for the default known NBA game ID.
    preferred_event = None
    for event in events:
        short_name = str(event.get("shortName") or event.get("name") or "").upper()
        status = ((event.get("status") or {}).get("type") or {})
        if "HOU" in short_name and "OKC" in short_name and status.get("completed") is True:
            preferred_event = event
            break
    if preferred_event is None:
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
        summary_status, summary_time, summary_data, summary_error = fetch_json(
            ESPN_SUMMARY_URL,
            params={"event": event_id},
            headers=HEADERS_ESPN,
        )

        team_id = teams[0]["id"] if teams else ""
        if team_id:
            roster_status, roster_time, roster_data, roster_error = fetch_json(
                ESPN_ROSTER_URL.format(team_id=team_id),
                headers=HEADERS_ESPN,
            )
            injuries_status, injuries_time, injuries_data, injuries_error = fetch_json(
                ESPN_INJURIES_URL.format(team_id=team_id),
                headers=HEADERS_ESPN,
            )

    with st.spinner("Tentando a NBA CDN para comparação..."):
        nba_status, nba_time, nba_data, nba_error = fetch_json(
            NBA_BOXSCORE_URL.format(game_id=nba_game_id),
            headers=HEADERS_NBA,
        )

    row1 = st.columns(4)
    panels = [
        ("Agenda ESPN", scoreboard_status, scoreboard_time, scoreboard_error),
        ("Boxscore ESPN", summary_status, summary_time, summary_error),
        ("Roster ESPN", roster_status, roster_time, roster_error),
        ("NBA CDN referência", nba_status, nba_time, nba_error),
    ]
    for col, (title, status, elapsed, error) in zip(row1, panels):
        with col:
            st.subheader(title)
            st.metric("HTTP", status if status is not None else "—")
            st.metric("Tempo", f"{elapsed:.2f}s")
            if error:
                st.error(error)

    if preferred_event:
        st.info(
            f"Evento ESPN usado: {preferred_event.get('name') or preferred_event.get('shortName')} "
            f"(ID {event_id})"
        )

    espn_players = extract_espn_players(summary_data)
    nba_players = extract_nba_players(nba_data)

    if summary_status == 200:
        st.success(f"ESPN: {len(espn_players)} linhas de jogadores extraídas do boxscore.")

    if roster_data is not None and roster_status == 200:
        roster_players = extract_roster_players(roster_data)
        st.success(
            f"Roster ESPN: {len(roster_players)} jogadores. "
            f"Lesões retornadas: {count_injuries(injuries_data)}."
        )

    st.subheader("Campos essenciais recebidos da ESPN")
    if espn_players:
        espn_df = pd.DataFrame(espn_players).drop(columns=["match_key"], errors="ignore")
        st.dataframe(espn_df, use_container_width=True, hide_index=True)
    else:
        st.warning("Não foi possível extrair jogadores do summary da ESPN.")

    if nba_status == 200 and nba_players:
        st.subheader("Comparação ESPN × NBA CDN")
        comparison, matched, exact_players = comparison_table(espn_players, nba_players)

        total_espn = len(espn_players)
        total_nba = len(nba_players)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Jogadores ESPN", total_espn)
        c2.metric("Jogadores NBA CDN", total_nba)
        c3.metric("Jogadores pareados", matched)
        c4.metric("Stats essenciais iguais", f"{exact_players}/{matched}" if matched else "0/0")

        st.caption(
            "Pareamento por time + nome normalizado. IDs da ESPN e da NBA são de provedores "
            "diferentes e não são esperados ser iguais. Para minutos aceitamos diferença de até "
            "1 minuto por arredondamento; as demais estatísticas exigem igualdade."
        )

        if not comparison.empty:
            st.dataframe(comparison, use_container_width=True, hide_index=True)

            mismatch = comparison[comparison["stats_exact"] == False]  # noqa: E712
            if mismatch.empty:
                st.success(
                    "Todos os jogadores pareados bateram nos campos estatísticos essenciais "
                    "(MIN dentro da tolerância, PTS, REB, AST, 3PM, 3PA e FGA)."
                )
            else:
                st.warning(
                    f"{len(mismatch)} jogador(es) pareado(s) têm alguma divergência. "
                    "A tabela mostra exatamente em qual campo."
                )
    else:
        st.info(
            "A NBA CDN não respondeu neste host, então a comparação direta não pode ser feita aqui. "
            "Isso é esperado no Streamlit Cloud (403). Rode esta mesma branch localmente para obter "
            "a comparação ESPN × NBA do mesmo jogo."
        )

    if all(status == 200 for status in [scoreboard_status, summary_status, roster_status]):
        st.success(
            "A ESPN cobriu os três blocos essenciais neste host: agenda, boxscore e roster. "
            "Se a comparação local também bater, teremos base para montar os game logs 2026/27 "
            "sem depender dos endpoints pesados de stats.nba.com."
        )

"""ESPN data adapter used to replace blocked stats.nba.com endpoints in cloud.

Phase 1 intentionally covers current roster + player game logs while preserving
the column names expected by the existing processing layer. The dashboard is not
switched to these functions yet; they are validated independently first.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import re
from typing import Any

import pandas as pd
import requests
import streamlit as st

from config import TEAM_LOOKUP


ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_TEAMS_URL = f"{ESPN_BASE}/teams"
ESPN_ROSTER_URL = f"{ESPN_BASE}/teams/{{team_id}}/roster"
ESPN_SCHEDULE_URL = f"{ESPN_BASE}/teams/{{team_id}}/schedule"
ESPN_SUMMARY_URL = f"{ESPN_BASE}/summary"
ESPN_PLAYER_GAMELOG_URL = (
    "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/"
    "athletes/{player_id}/gamelog"
)

ESPN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.espn.com/",
}

OFFICIAL_TEAM_ID_BY_ABBR = {
    str(data.get("abbreviation", "")).upper(): int(team_id)
    for team_id, data in TEAM_LOOKUP.items()
    if data.get("abbreviation")
}

# ESPN uses different abbreviations for a small set of NBA teams. Keep these
# aliases in the provider adapter so every ESPN-backed feature resolves to the
# same official NBA team IDs used throughout the dashboard.
ESPN_TO_NBA_ABBR = {
    "NY": "NYK",
    "SA": "SAS",
    "NO": "NOP",
    "GS": "GSW",
    "WSH": "WAS",
    "UTAH": "UTA",
}

for espn_abbr, nba_abbr in ESPN_TO_NBA_ABBR.items():
    official_id = OFFICIAL_TEAM_ID_BY_ABBR.get(nba_abbr)
    if official_id:
        OFFICIAL_TEAM_ID_BY_ABBR[espn_abbr] = official_id


def normalize_espn_team_abbr(value: Any) -> str:
    """Convert ESPN-specific NBA abbreviations to the dashboard's NBA standard."""
    abbr = str(value or "").upper().strip()
    return ESPN_TO_NBA_ABBR.get(abbr, abbr)


def _request_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 12,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = requests.get(
                url,
                params=params,
                headers=ESPN_HEADERS,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                return payload
            raise RuntimeError("ESPN retornou um payload inesperado.")
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                continue
    raise RuntimeError(f"ESPN não respondeu: {type(last_error).__name__}: {last_error}") from last_error


def _season_end_year(season: str) -> int:
    match = re.fullmatch(r"(20\d{2})-(\d{2})", str(season).strip())
    if not match:
        raise ValueError(f"Temporada inválida: {season!r}")
    start_year = int(match.group(1))
    return (start_year // 100) * 100 + int(match.group(2))


def _scope_to_espn_type(season_scope: str) -> int | None:
    scope = str(season_scope or "").strip().lower()
    if scope == "regular season":
        return 2
    if scope == "playoffs":
        return 3
    # ESPN does not expose Play-In as consistently as stats.nba.com.
    # During validation we keep All/PlayIn unfiltered and label the source.
    return None


@st.cache_data(ttl=54000, show_spinner=False)
def get_espn_team_directory() -> pd.DataFrame:
    payload = _request_json(ESPN_TEAMS_URL, params={"limit": 100})
    sports = payload.get("sports") or []
    leagues = ((sports[0] or {}).get("leagues") or []) if sports else []
    entries = ((leagues[0] or {}).get("teams") or []) if leagues else []

    rows = []
    for entry in entries:
        team = (entry or {}).get("team") or entry or {}
        espn_abbr = str(team.get("abbreviation") or "").upper()
        abbr = normalize_espn_team_abbr(espn_abbr)
        official_team_id = OFFICIAL_TEAM_ID_BY_ABBR.get(espn_abbr)
        if not team.get("id") or not abbr or not official_team_id:
            continue
        rows.append(
            {
                "ESPN_TEAM_ID": str(team.get("id")),
                "TEAM_ID": int(official_team_id),
                "TEAM_ABBR": abbr,
                "TEAM_NAME": team.get("displayName") or team.get("name") or abbr,
            }
        )

    return pd.DataFrame(rows)


def _espn_team_id_for_official(team_id: int) -> str:
    directory = get_espn_team_directory()
    if directory.empty:
        raise RuntimeError("Diretório de times da ESPN está vazio.")

    match = directory[
        pd.to_numeric(directory["TEAM_ID"], errors="coerce") == int(team_id)
    ]
    if match.empty:
        raise RuntimeError(f"Time NBA {team_id} não encontrado no diretório ESPN.")
    return str(match.iloc[0]["ESPN_TEAM_ID"])


@st.cache_data(ttl=54000, show_spinner=False)
def get_espn_team_roster(team_id: int, season: str) -> pd.DataFrame:
    """Current ESPN roster using the schema expected by build_team_table."""
    espn_team_id = _espn_team_id_for_official(team_id)
    payload = _request_json(ESPN_ROSTER_URL.format(team_id=espn_team_id))

    rows = []
    for athlete in payload.get("athletes") or []:
        if not isinstance(athlete, dict):
            continue
        player_id = athlete.get("id")
        player_name = athlete.get("displayName") or athlete.get("fullName")
        if not player_id or not player_name:
            continue

        position = athlete.get("position") or {}
        status = athlete.get("status") or {}
        rows.append(
            {
                "PLAYER": str(player_name),
                "PLAYER_ID": pd.to_numeric(player_id, errors="coerce"),
                "POSITION": (
                    position.get("abbreviation")
                    if isinstance(position, dict)
                    else str(position or "")
                ),
                "TEAM_ID": int(team_id),
                "ESPN_PLAYER_ID": str(player_id),
                "ROSTER_STATUS": (
                    status.get("name")
                    or status.get("type")
                    or status.get("abbreviation")
                    or ""
                ),
                "DATA_SOURCE": "ESPN",
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "PLAYER", "PLAYER_ID", "POSITION", "TEAM_ID",
                "ESPN_PLAYER_ID", "ROSTER_STATUS", "DATA_SOURCE",
            ]
        )

    df["PLAYER_ID"] = pd.to_numeric(df["PLAYER_ID"], errors="coerce")
    return df.dropna(subset=["PLAYER_ID"]).reset_index(drop=True)


@st.cache_data(ttl=54000, show_spinner=False)
def get_espn_team_schedule(
    team_id: int,
    season: str,
    season_scope: str = "Regular Season",
) -> pd.DataFrame:
    espn_team_id = _espn_team_id_for_official(team_id)
    season_year = _season_end_year(season)
    season_type = _scope_to_espn_type(season_scope)

    params: dict[str, Any] = {"season": season_year}
    if season_type is not None:
        params["seasontype"] = season_type

    payload = _request_json(
        ESPN_SCHEDULE_URL.format(team_id=espn_team_id),
        params=params,
    )

    rows = []
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue

        event_season_type = event.get("seasonType") or {}
        event_type = event_season_type.get("type")
        if season_type is not None and event_type is not None and int(event_type) != season_type:
            continue

        competitions = event.get("competitions") or []
        competition = competitions[0] if competitions else {}
        competitors = (competition or {}).get("competitors") or []

        own = None
        opponent = None
        for comp in competitors:
            team = (comp or {}).get("team") or {}
            if str(team.get("id")) == str(espn_team_id):
                own = comp
            else:
                opponent = comp

        status_type = ((competition or {}).get("status") or {}).get("type") or {}
        completed = bool(status_type.get("completed"))
        if not status_type:
            completed = bool((competition or {}).get("boxscoreAvailable")) and bool(
                (own or {}).get("score")
            )

        opponent_team = (opponent or {}).get("team") or {}
        opponent_espn_abbr = str(opponent_team.get("abbreviation") or "").upper()
        opponent_abbr = normalize_espn_team_abbr(opponent_espn_abbr)
        opponent_official_id = OFFICIAL_TEAM_ID_BY_ABBR.get(opponent_espn_abbr)

        own_home_away = str((own or {}).get("homeAway") or "")
        winner = (own or {}).get("winner")

        rows.append(
            {
                "GAME_ID": str(event.get("id") or competition.get("id") or ""),
                "GAME_DATE": pd.to_datetime(event.get("date"), errors="coerce"),
                "TEAM_ID": int(team_id),
                "OPPONENT_TEAM_ID": (
                    int(opponent_official_id) if opponent_official_id else None
                ),
                "OPPONENT_ABBR": opponent_abbr,
                "HOME_AWAY": own_home_away,
                "COMPLETED": completed,
                "WINNER": winner,
                "SEASON_TYPE": int(event_type) if event_type is not None else None,
                "BOX_SCORE_AVAILABLE": bool((competition or {}).get("boxscoreAvailable")),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "GAME_ID", "GAME_DATE", "TEAM_ID", "OPPONENT_TEAM_ID",
                "OPPONENT_ABBR", "HOME_AWAY", "COMPLETED", "WINNER",
                "SEASON_TYPE", "BOX_SCORE_AVAILABLE",
            ]
        )

    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
    return df.sort_values("GAME_DATE", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=54000, show_spinner=False)
def get_espn_game_summary(game_id: str) -> dict[str, Any]:
    return _request_json(ESPN_SUMMARY_URL, params={"event": str(game_id)})


def _made_attempted(value: Any) -> tuple[float | None, float | None]:
    if value is None:
        return None, None
    match = re.search(r"(\d+(?:\.\d+)?)\s*[-/]\s*(\d+(?:\.\d+)?)", str(value))
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_espn_minutes(value: Any) -> float | None:
    """ESPN minutes can be a decimal number or MM:SS clock value."""
    if value is None or str(value).strip().upper() in {"", "DNP", "DND", "--"}:
        return None
    raw = str(value).strip()
    if ":" in raw:
        parts = raw.split(":")
        if len(parts) != 2:
            return None
        try:
            whole, seconds = int(parts[0]), int(parts[1])
        except (TypeError, ValueError):
            return None
        if whole < 0 or not (0 <= seconds < 60):
            return None
        return whole + seconds / 60.0
    return _to_float(value)


def parse_espn_shooting_stats(
    stat_map: dict[str, Any],
) -> tuple[float | None, float | None, float | None, tuple[str, ...]]:
    """
    Parse ESPN FG/3PT pairs without manufacturing unknown attempts as zero.

    ESPN changes field names between gamelog and boxscore payloads (FG, FGM-A,
    FGM-FGA, 3PM-A, etc.). Valid zero attempts and missing data are different.
    """
    fg_value = _first_stat(
        stat_map, "FG", "FGM-A", "FGM-FGA", "FG-FGA",
        "fieldGoalsMade-fieldGoalsAttempted", "fieldGoals",
        "fieldGoalsMade", "FGM/FGA", "FGM", "FG/A",
        "fieldGoalsMadeAttempted",
    )
    three_value = _first_stat(
        stat_map, "3PT", "3P", "3PM-A", "3PM-3PA", "3PM/3PA",
        "3FGM-3FGA", "3FGM-A",
        "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
        "threePointFieldGoals", "threePointsMade", "threePointFieldGoalsMade",
    )
    _, fga = _made_attempted(fg_value)
    fg3m, fg3a = _made_attempted(three_value)

    if fga is None:
        fga = _to_float(_first_stat(stat_map, "FGA", "fieldGoalsAttempted"))
    if fg3m is None:
        fg3m = _to_float(_first_stat(
            stat_map, "3PM", "3FGM", "threePointFieldGoalsMade",
            "threePointsMade",
        ))
    if fg3a is None:
        fg3a = _to_float(_first_stat(
            stat_map, "3PA", "3FGA", "threePointFieldGoalsAttempted",
        ))

    issues: list[str] = []
    if fga is None:
        issues.append("FGA_missing")
    if fg3a is None:
        issues.append("3PA_missing")
    if fga is not None and (fga < 0 or fga > 90):
        issues.append("FGA_out_of_range")
        fga = None
    if fg3a is not None and (fg3a < 0 or fg3a > 60):
        issues.append("3PA_out_of_range")
        fg3a = None
    if fg3m is not None and (fg3m < 0 or (fg3a is not None and fg3m > fg3a)):
        issues.append("3PM_exceeds_3PA")
        fg3m = None
    if fga is not None and fg3a is not None and fg3a > fga:
        issues.append("3PA_exceeds_FGA")
        fga = None
    return fga, fg3m, fg3a, tuple(issues)


def _extract_summary_rows(
    payload: dict[str, Any],
    *,
    game_meta: dict[str, Any],
    season_scope: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    boxscore = payload.get("boxscore") or {}

    for team_block in boxscore.get("players") or []:
        team = team_block.get("team") or {}
        team_espn_abbr = str(team.get("abbreviation") or "").upper()
        team_abbr = normalize_espn_team_abbr(team_espn_abbr)
        official_team_id = OFFICIAL_TEAM_ID_BY_ABBR.get(team_espn_abbr)
        if not official_team_id:
            continue

        for stat_block in team_block.get("statistics") or []:
            keys = stat_block.get("keys") or stat_block.get("names") or []
            for athlete_row in stat_block.get("athletes") or []:
                if athlete_row.get("didNotPlay") is True:
                    continue

                athlete = athlete_row.get("athlete") or {}
                stats = athlete_row.get("stats") or []
                stat_map = {
                    str(key).lower(): value
                    for key, value in zip(keys, stats)
                }

                minutes = parse_espn_minutes(_first_stat(stat_map, "min", "minutes"))
                points = _to_float(stat_map.get("pts") or stat_map.get("points"))
                rebounds = _to_float(
                    stat_map.get("reb")
                    or stat_map.get("rebounds")
                    or stat_map.get("totalrebounds")
                )
                assists = _to_float(stat_map.get("ast") or stat_map.get("assists"))
                fga, fg3m, fg3a, shooting_issues = parse_espn_shooting_stats(stat_map)

                played = minutes is not None and minutes > 0
                if not played:
                    played = any(
                        value is not None
                        for value in [points, rebounds, assists, fga, fg3a]
                    )
                if not played:
                    continue

                player_id = pd.to_numeric(athlete.get("id"), errors="coerce")
                if pd.isna(player_id):
                    continue

                if int(official_team_id) == int(game_meta["TEAM_ID"]):
                    opp_abbr = str(game_meta.get("OPPONENT_ABBR") or "")
                    home_away = str(game_meta.get("HOME_AWAY") or "")
                    matchup = (
                        f"{team_abbr} @ {opp_abbr}"
                        if home_away == "away"
                        else f"{team_abbr} vs. {opp_abbr}"
                    )
                    winner = game_meta.get("WINNER")
                    wl = "W" if winner is True else ("L" if winner is False else "")
                else:
                    own_abbr = next(
                        (
                            str(data.get("abbreviation") or "").upper()
                            for tid, data in TEAM_LOOKUP.items()
                            if int(tid) == int(game_meta["TEAM_ID"])
                        ),
                        "",
                    )
                    opponent_home_away = (
                        "home" if str(game_meta.get("HOME_AWAY")) == "away" else "away"
                    )
                    matchup = (
                        f"{team_abbr} @ {own_abbr}"
                        if opponent_home_away == "away"
                        else f"{team_abbr} vs. {own_abbr}"
                    )
                    winner = game_meta.get("WINNER")
                    wl = "L" if winner is True else ("W" if winner is False else "")

                rows.append(
                    {
                        "PLAYER_ID": int(player_id),
                        "PLAYER_NAME": (
                            athlete.get("displayName")
                            or athlete.get("fullName")
                            or athlete.get("shortName")
                            or ""
                        ),
                        "TEAM_ID": int(official_team_id),
                        "TEAM_ABBREVIATION": team_abbr,
                        "GAME_ID": str(game_meta.get("GAME_ID") or ""),
                        "GAME_DATE": game_meta.get("GAME_DATE"),
                        "MATCHUP": matchup,
                        "WL": wl,
                        "MIN": minutes or 0.0,
                        "PTS": points or 0.0,
                        "REB": rebounds or 0.0,
                        "AST": assists or 0.0,
                        "FG3M": fg3m if fg3m is not None else float("nan"),
                        "FGA": fga if fga is not None else float("nan"),
                        "FG3A": fg3a if fg3a is not None else float("nan"),
                        "SHOOTING_ISSUES": "|".join(shooting_issues),
                        "SEASON_SCOPE": season_scope,
                        "DATA_SOURCE": "ESPN",
                    }
                )

    return rows


@st.cache_data(ttl=54000, show_spinner=False)
def get_espn_team_player_logs(
    team_id: int,
    season: str,
    season_scope: str = "Regular Season",
    max_games: int | None = None,
) -> pd.DataFrame:
    """Build player game logs from ESPN summaries in NBA-compatible columns."""
    schedule = get_espn_team_schedule(team_id, season, season_scope)
    if schedule.empty:
        return pd.DataFrame()

    games = schedule[
        schedule["COMPLETED"].fillna(False)
        & schedule["BOX_SCORE_AVAILABLE"].fillna(False)
    ].copy()

    games = games.sort_values("GAME_DATE", ascending=False)
    if max_games is not None and int(max_games) > 0:
        games = games.head(int(max_games))

    if games.empty:
        return pd.DataFrame()

    metas = [row.to_dict() for _, row in games.iterrows()]
    payload_by_game: dict[str, dict[str, Any]] = {}

    # Small bounded concurrency keeps cloud loading quick without hammering ESPN.
    with ThreadPoolExecutor(max_workers=min(6, len(metas))) as pool:
        future_map = {
            pool.submit(get_espn_game_summary, str(meta["GAME_ID"])): meta
            for meta in metas
        }
        for future in as_completed(future_map):
            meta = future_map[future]
            try:
                payload_by_game[str(meta["GAME_ID"])] = future.result()
            except Exception:
                continue

    rows: list[dict[str, Any]] = []
    for meta in metas:
        payload = payload_by_game.get(str(meta["GAME_ID"]))
        if not payload:
            continue
        rows.extend(
            _extract_summary_rows(
                payload,
                game_meta=meta,
                season_scope=season_scope,
            )
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df[
        pd.to_numeric(df["TEAM_ID"], errors="coerce") == int(team_id)
    ].copy()

    for col in ["PLAYER_ID", "TEAM_ID", "MIN", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ["MIN", "PTS", "REB", "AST"]:
        df[col] = df[col].fillna(0.0)
    # Shooting fields stay NaN if ESPN did not provide a valid count.
    # Aggregation must not turn missing FGA into a real zero-attempt game.

    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
    df["PRA"] = df["PTS"] + df["REB"] + df["AST"]

    return df.sort_values(
        ["PLAYER_ID", "GAME_DATE"],
        ascending=[True, False],
    ).reset_index(drop=True)


def aggregate_espn_player_stats(
    logs: pd.DataFrame,
    last_n_games: int = 0,
) -> pd.DataFrame:
    """Aggregate ESPN logs into LeagueDashPlayerStats-compatible columns."""
    columns = [
        "PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GP", "MIN",
        "PTS", "REB", "AST", "FG3M", "FGA", "FG3A",
        "FGA_VALID_GP", "FG3A_VALID_GP", "FG3M_VALID_GP",
    ]
    if logs is None or logs.empty:
        return pd.DataFrame(columns=columns)

    work = logs.copy()
    work["GAME_DATE"] = pd.to_datetime(work["GAME_DATE"], errors="coerce")
    work = work.sort_values(["PLAYER_ID", "GAME_DATE"], ascending=[True, False])

    if last_n_games and int(last_n_games) > 0:
        work = work.groupby("PLAYER_ID", group_keys=False).head(int(last_n_games))

    numeric = ["MIN", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]
    for col in numeric:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    # Preserve NaN for missing shooting metrics. A genuine 0-0 is a valid zero.
    for col in ["MIN", "PTS", "REB", "AST"]:
        work[col] = work[col].fillna(0.0)

    rows = []
    for player_id, group in work.groupby("PLAYER_ID", dropna=False):
        rows.append(
            {
                "PLAYER_ID": player_id,
                "PLAYER_NAME": group["PLAYER_NAME"].iloc[0],
                "TEAM_ID": int(group["TEAM_ID"].iloc[0]),
                "GP": int(len(group)),
                **{col: float(group[col].mean()) for col in numeric},
                "FGA_VALID_GP": int(group["FGA"].notna().sum()),
                "FG3A_VALID_GP": int(group["FG3A"].notna().sum()),
                "FG3M_VALID_GP": int(group["FG3M"].notna().sum()),
            }
        )

    return pd.DataFrame(rows, columns=columns)


def _compact_stat_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _first_stat(stat_map: dict[str, Any], *aliases: str) -> Any:
    """Look up ESPN stats without conflating count and percentage columns.

    In ESPN gamelogs both FG='7-22' and FG%='31.8' are present. Removing
    punctuation before lookup maps both to 'fg', so FG% overwrote FG and caused
    false missing FGA. Prefer case-insensitive EXACT names, then a conservative
    normalized fallback excluding unrelated percentage columns.
    """
    exact = {str(k).strip().casefold(): value for k, value in stat_map.items()}
    for alias in aliases:
        key = str(alias).strip().casefold()
        if key in exact:
            return exact[key]

    for alias in aliases:
        candidate = _compact_stat_key(alias)
        alias_lower = str(alias).casefold()
        alias_is_percent = (
            "%" in alias_lower or "pct" in alias_lower or "percent" in alias_lower
        )
        for raw_key, value in stat_map.items():
            raw_lower = str(raw_key).casefold()
            key_is_percent = (
                "%" in raw_lower or "pct" in raw_lower or "percent" in raw_lower
            )
            if key_is_percent != alias_is_percent:
                continue
            if _compact_stat_key(raw_key) == candidate:
                return value
    return None


def _gamelog_type_id(season_type: dict[str, Any]) -> int | None:
    raw_type = season_type.get("id") or season_type.get("type")
    try:
        if raw_type is not None:
            return int(raw_type)
    except (TypeError, ValueError):
        pass

    name = str(
        season_type.get("name")
        or season_type.get("displayName")
        or season_type.get("label")
        or ""
    ).strip().lower()
    if "regular" in name:
        return 2
    if "playoff" in name or "postseason" in name or "post season" in name:
        return 3
    if "preseason" in name or "pre-season" in name:
        return 1
    return None


def _event_stat_names(
    payload: dict[str, Any],
    event: dict[str, Any],
    stats: list[Any],
) -> list[str]:
    """Return stat labels aligned to ESPN's per-game stats vector."""
    candidates = [
        event.get("_stat_names"),
        event.get("labels"),
        event.get("names"),
        payload.get("labels"),
        payload.get("names"),
        payload.get("displayNames"),
    ]

    for candidate in candidates:
        if isinstance(candidate, list) and len(candidate) == len(stats):
            return [str(value) for value in candidate]

    # Some current ESPN payloads include DATE/OPP/RESULT in labels/names while
    # the stats vector contains only the basketball columns. In that case the
    # stat columns are the trailing entries.
    for candidate in candidates:
        if isinstance(candidate, list) and len(candidate) > len(stats):
            return [str(value) for value in candidate[-len(stats):]]

    return [f"stat_{idx}" for idx in range(len(stats))]


def _iter_gamelog_events(
    payload: dict[str, Any],
    wanted_type: int | None,
) -> list[dict[str, Any]]:
    """Normalize both ESPN athlete-gamelog shapes into event dictionaries."""
    raw_events = payload.get("events") or {}
    event_meta: dict[str, dict[str, Any]] = {}
    flat_events: list[dict[str, Any]] = []

    if isinstance(raw_events, dict):
        for key, value in raw_events.items():
            if not isinstance(value, dict):
                continue
            event = dict(value)
            event_id = (
                event.get("eventId")
                or event.get("id")
                or (event.get("event") or {}).get("id")
                or key
            )
            event["_event_id"] = str(event_id or "")
            event_meta[str(event_id or key)] = event
            if isinstance(event.get("stats"), list):
                flat_events.append(event)
    elif isinstance(raw_events, list):
        for value in raw_events:
            if not isinstance(value, dict):
                continue
            event = dict(value)
            event_id = (
                event.get("eventId")
                or event.get("id")
                or (event.get("event") or {}).get("id")
                or ""
            )
            event["_event_id"] = str(event_id or "")
            if event["_event_id"]:
                event_meta[event["_event_id"]] = event
            if isinstance(event.get("stats"), list):
                flat_events.append(event)

    # Older/alternate ESPN payloads keep metadata in top-level events and the
    # stats vectors under seasonTypes[].categories[].events[].
    scoped_events: list[dict[str, Any]] = []
    for season_type in payload.get("seasonTypes") or []:
        if not isinstance(season_type, dict):
            continue
        type_id = _gamelog_type_id(season_type)
        if wanted_type is not None and type_id is not None and type_id != wanted_type:
            continue

        for category in season_type.get("categories") or []:
            if not isinstance(category, dict):
                continue
            if str(category.get("type") or "").strip().lower() == "total":
                continue

            category_names = (
                category.get("labels")
                or category.get("names")
                or payload.get("labels")
                or payload.get("names")
                or []
            )
            for stat_event in category.get("events") or []:
                if not isinstance(stat_event, dict):
                    continue
                stats = stat_event.get("stats") or stat_event.get("values")
                if not isinstance(stats, list):
                    continue

                event_id = str(
                    stat_event.get("eventId")
                    or stat_event.get("id")
                    or (stat_event.get("event") or {}).get("id")
                    or ""
                )
                merged = dict(event_meta.get(event_id, {}))
                merged.update(stat_event)
                merged["_event_id"] = event_id
                if isinstance(category_names, list):
                    merged["_stat_names"] = category_names
                scoped_events.append(merged)

    if scoped_events:
        deduped: dict[str, dict[str, Any]] = {}
        anonymous: list[dict[str, Any]] = []
        for event in scoped_events:
            event_id = str(event.get("_event_id") or "")
            if event_id:
                deduped[event_id] = event
            else:
                anonymous.append(event)
        return list(deduped.values()) + anonymous

    return flat_events


def _recover_verified_shooting_vector(
    stats: list[Any], points: float | None,
) -> tuple[float, float, float] | None:
    """Recover ESPN's fixed MIN/FG/3PT/FT/.../PTS vector only if verified.

    ESPN sometimes changes labels/names without changing the nine basketball
    values. Read the *values* only when they satisfy the basketball scoring
    identity: PTS = 2*FGM + 3PM + FTM, and FGA >= 3PA. This is a
    source-value recovery, never an invented attempt count.
    """
    if not isinstance(stats, list) or len(stats) < 9 or points is None:
        return None
    values = stats[-9:]
    fgm, fga = _made_attempted(values[1])
    three_made, three_attempted = _made_attempted(values[2])
    free_made, free_attempted = _made_attempted(values[3])
    if any(v is None for v in (
        fgm, fga, three_made, three_attempted,
        free_made, free_attempted,
    )):
        return None
    if not (
        0 <= fgm <= fga <= 90
        and 0 <= three_made <= three_attempted <= fga
        and 0 <= free_made <= free_attempted
    ):
        return None
    if abs((2 * fgm + three_made + free_made) - points) > 0.01:
        return None
    return float(fga), float(three_made), float(three_attempted)


def _parse_espn_player_gamelog_payload(
    payload: dict[str, Any],
    player_id: int,
    season_scope: str = "Regular Season",
) -> pd.DataFrame:
    wanted_type = _scope_to_espn_type(season_scope)
    rows: list[dict[str, Any]] = []

    for event in _iter_gamelog_events(payload, wanted_type):
        stats = event.get("stats") or event.get("values") or []
        if not isinstance(stats, list):
            continue

        names = _event_stat_names(payload, event, stats)
        stat_map = {
            str(name): value
            for name, value in zip(names, stats)
        }

        minutes = parse_espn_minutes(_first_stat(stat_map, "min", "minutes"))
        pts = _to_float(_first_stat(stat_map, "pts", "points"))
        reb = _to_float(_first_stat(stat_map, "reb", "rebounds", "totalRebounds"))
        ast = _to_float(_first_stat(stat_map, "ast", "assists"))
        fga, fg3m, fg3a, shooting_issues = parse_espn_shooting_stats(stat_map)
        # Recover only from the actual ESPN stats vector when the labels
        # are unrecognized. Verify against the recorded points and FT.
        if fga is None or fg3m is None or fg3a is None:
            recovered = _recover_verified_shooting_vector(stats, pts)
            if recovered is not None:
                candidate_fga, candidate_3pm, candidate_3pa = recovered
                if (
                    (fg3a is None or fg3a == candidate_3pa)
                    and (fg3m is None or fg3m == candidate_3pm)
                ):
                    fga, fg3m, fg3a = recovered
                    shooting_issues = tuple(
                        issue for issue in shooting_issues
                        if issue in {"FGA_out_of_range", "3PA_out_of_range",
                                     "3PA_exceeds_FGA", "3PM_exceeds_3PA"}
                    )
                    # Contradictory named fields cannot be silently repaired.
                    if shooting_issues:
                        fga = None

        # ESPN can include DNP/header rows with metadata but no participation.
        if minutes is None and all(
            value is None
            for value in [pts, reb, ast, fga, fg3a]
        ):
            continue

        opponent = event.get("opponent") or {}
        opp_abbr = (
            normalize_espn_team_abbr(opponent.get("abbreviation"))
            if isinstance(opponent, dict)
            else ""
        )

        home_away = str(event.get("homeAway") or "").strip().lower()
        if not home_away:
            at_vs = str(event.get("atVs") or event.get("at_vs") or "").strip().lower()
            if at_vs in {"@", "at", "away"}:
                home_away = "away"
            elif at_vs in {"vs", "vs.", "home"}:
                home_away = "home"

        matchup = (
            f"@ {opp_abbr}"
            if home_away == "away"
            else f"vs. {opp_abbr}"
        )

        game_result = str(
            event.get("gameResult")
            or event.get("result")
            or ""
        )
        wl = game_result[:1].upper() if game_result[:1].upper() in {"W", "L"} else ""

        event_id = str(
            event.get("_event_id")
            or event.get("eventId")
            or event.get("id")
            or (event.get("event") or {}).get("id")
            or ""
        )

        rows.append(
            {
                "PLAYER_ID": int(player_id),
                "GAME_ID": event_id,
                "GAME_DATE": pd.to_datetime(
                    event.get("date") or event.get("gameDate"),
                    errors="coerce",
                ),
                "MATCHUP": matchup,
                "WL": wl,
                "MIN": minutes or 0.0,
                "PTS": pts or 0.0,
                "REB": reb or 0.0,
                "AST": ast or 0.0,
                "FG3M": fg3m if fg3m is not None else float("nan"),
                "FGA": fga if fga is not None else float("nan"),
                "FG3A": fg3a if fg3a is not None else float("nan"),
                "SHOOTING_ISSUES": "|".join(shooting_issues),
                "SEASON_SCOPE": season_scope,
                "DATA_SOURCE": "ESPN",
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    for col in ["PLAYER_ID", "MIN", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # Never replace missing shot attempts with zero; doing so creates
    # fake 0-FGA games and a false zero-error shooting backtest.
    for col in ["MIN", "PTS", "REB", "AST"]:
        df[col] = df[col].fillna(0.0)

    df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
    return (
        df.drop_duplicates(subset=["GAME_ID"], keep="first")
        .sort_values("GAME_DATE", ascending=False)
        .reset_index(drop=True)
    )


@st.cache_data(ttl=3600, show_spinner=False)
def diagnose_espn_shooting_gamelog(
    player_id: int,
    season: str,
    season_scope: str = "Regular Season",
) -> pd.DataFrame:
    """Small, on-demand source diagnostic for ESPN shooting labels and values.

    Public boxscore data only; shows at most four events. Useful if ESPN changes
    its payload schema again, without storing or exposing the full response.
    """
    year = _season_end_year(season)
    type_id = _scope_to_espn_type(season_scope)
    params: dict[str, Any] = {"season": year}
    if type_id is not None:
        params["seasontype"] = type_id
    payload = _request_json(
        ESPN_PLAYER_GAMELOG_URL.format(player_id=int(player_id)),
        params=params,
    )
    rows: list[dict[str, Any]] = []
    for event in _iter_gamelog_events(payload, type_id)[:4]:
        stats = event.get("stats") or event.get("values") or []
        if not isinstance(stats, list):
            continue
        names = _event_stat_names(payload, event, stats)
        parsed = parse_espn_shooting_stats(dict(zip(names, stats)))
        pts = _to_float(_first_stat(dict(zip(names, stats)), "PTS", "points"))
        recovery = _recover_verified_shooting_vector(stats, pts)
        rows.append({
            "Jogo ESPN": str(event.get("_event_id") or event.get("id") or ""),
            "Data": str(event.get("gameDate") or event.get("date") or "")[:10],
            "Cabeçalhos": " | ".join(str(v) for v in names),
            "Valores": " | ".join(str(v) for v in stats),
            "FGA por nome": parsed[0],
            "FGA por vetor verificado": recovery[0] if recovery else None,
            "3PA": parsed[2],
            "Problemas": ", ".join(parsed[3]),
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=54000, show_spinner=False)
def get_espn_player_log(
    player_id: int,
    season: str,
    season_scope: str = "Regular Season",
) -> pd.DataFrame:
    """Player game log from ESPN in the same columns used by ui_components."""
    season_year = _season_end_year(season)
    wanted_type = _scope_to_espn_type(season_scope)

    params: dict[str, Any] = {"season": season_year}
    if wanted_type is not None:
        params["seasontype"] = wanted_type

    payload = _request_json(
        ESPN_PLAYER_GAMELOG_URL.format(player_id=int(player_id)),
        params=params,
    )
    return _parse_espn_player_gamelog_payload(
        payload,
        int(player_id),
        season_scope=season_scope,
    )

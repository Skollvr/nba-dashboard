"""Full ESPN context layer for the cloud-backed NBA model.

This module rebuilds the pieces that previously depended on stats.nba.com:
- current roster player histories (including transfers through ESPN player gamelogs)
- projected rotation from ESPN depth charts
- ESPN injury statuses
- team defensive percentiles
- defense versus position (G/F/C) and league positional baselines

Defense uses each team's current-season sample up to the most recent 20 completed
games. During the first 20 games this is the full season; afterwards it becomes a
recent-20 window to keep the Streamlit Cloud cold start bounded.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import re
import unicodedata
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

from api_espn import (
    ESPN_BASE,
    OFFICIAL_TEAM_ID_BY_ABBR,
    _espn_team_id_for_official,
    _request_json,
    get_espn_game_summary,
    get_espn_player_log,
    get_espn_team_directory,
    get_espn_team_roster,
    get_espn_team_schedule,
)


ESPN_DEPTHCHART_URL = f"{ESPN_BASE}/teams/{{team_id}}/depthcharts"
ESPN_INJURIES_URL = f"{ESPN_BASE}/injuries"
ESPN_TEAM_INJURIES_URL = f"{ESPN_BASE}/teams/{{team_id}}/injuries"
DEFENSE_WINDOW_GAMES = 20


def _name_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _position_group(value: Any) -> str:
    pos = str(value or "").upper().strip()
    if pos in {"PG", "SG", "G"} or "G" in pos:
        return "G"
    if pos in {"SF", "PF", "F"} or "F" in pos:
        return "F"
    if pos == "C" or "C" in pos:
        return "C"
    return "F"


def _cutoff_timestamp(as_of_date: str | date | None) -> pd.Timestamp | None:
    if as_of_date in (None, ""):
        return None
    try:
        return pd.Timestamp(as_of_date).normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    except Exception:
        return None


@st.cache_data(ttl=54000, show_spinner=False)
def get_espn_roster_player_logs(
    team_id: int,
    season: str,
    season_scope: str = "Regular Season",
    as_of_date: str | None = None,
) -> pd.DataFrame:
    """Fetch each current roster player's ESPN season gamelog concurrently."""
    roster = get_espn_team_roster(team_id, season)
    if roster is None or roster.empty:
        return pd.DataFrame()

    cutoff = _cutoff_timestamp(as_of_date)
    rows: list[pd.DataFrame] = []

    def fetch_one(item: dict[str, Any]) -> pd.DataFrame:
        pid = int(float(item["PLAYER_ID"]))
        df = get_espn_player_log(pid, season, season_scope=season_scope)
        if df is None or df.empty:
            return pd.DataFrame()
        work = df.copy()
        work["PLAYER_ID"] = pid
        work["PLAYER_NAME"] = str(item.get("PLAYER") or "")
        work["TEAM_ID"] = int(team_id)
        work["POSITION"] = str(item.get("POSITION") or "")
        if cutoff is not None:
            dates = pd.to_datetime(work["GAME_DATE"], errors="coerce")
            work = work[dates.dt.date <= cutoff.date()].copy()
        return work

    items = roster.to_dict("records")
    with ThreadPoolExecutor(max_workers=min(10, max(1, len(items)))) as pool:
        futures = [pool.submit(fetch_one, item) for item in items]
        for future in as_completed(futures):
            try:
                part = future.result()
                if part is not None and not part.empty:
                    rows.append(part)
            except Exception:
                continue

    if not rows:
        return pd.DataFrame()

    out = pd.concat(rows, ignore_index=True)
    out["GAME_DATE"] = pd.to_datetime(out["GAME_DATE"], errors="coerce")
    for col in ["PLAYER_ID", "TEAM_ID", "MIN", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A", "PRA"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.sort_values(["PLAYER_ID", "GAME_DATE"], ascending=[True, False]).reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False)
def get_espn_team_rotation(team_id: int) -> pd.DataFrame:
    """Projected depth chart: depth 1 at PG/SG/SF/PF/C = projected starter."""
    espn_team_id = _espn_team_id_for_official(team_id)
    payload = _request_json(ESPN_DEPTHCHART_URL.format(team_id=espn_team_id))

    rows: list[dict[str, Any]] = []
    for chart in payload.get("depthchart") or []:
        positions = chart.get("positions") or {}
        for pos_key, pos_data in positions.items():
            position = pos_data.get("position") or {}
            pos_abbr = str(position.get("abbreviation") or pos_key or "").upper()
            for index, athlete in enumerate(pos_data.get("athletes") or []):
                pid = pd.to_numeric(athlete.get("id"), errors="coerce")
                name = athlete.get("displayName") or athlete.get("fullName") or ""
                if pd.isna(pid) and not name:
                    continue
                depth = index + 1
                rows.append({
                    "PLAYER_ID": int(pid) if pd.notna(pid) else None,
                    "PLAYER": str(name),
                    "PLAYER_KEY": _name_key(name),
                    "TEAM_ID": int(team_id),
                    "POSITION": pos_abbr,
                    "DEPTH": depth,
                    "LINEUP_STATUS": "Titular projetado" if depth == 1 else "Rotação",
                    "IS_STARTER": depth == 1,
                    "PROJECTED_MINUTES_EXTERNAL": np.nan,
                    "LINEUP_SOURCE": "ESPN Depth Chart",
                })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # A player can appear at more than one position. Keep his best depth.
    df = df.sort_values(["DEPTH", "POSITION"]).drop_duplicates(
        subset=["PLAYER_ID", "PLAYER_KEY"], keep="first"
    )
    return df.reset_index(drop=True)


def _normalize_injury_status(raw_status: Any, fantasy_status: Any = "") -> str:
    text = f"{raw_status or ''} {fantasy_status or ''}".strip().lower()
    if any(token in text for token in ["out for season", "ofs", "out"]):
        return "Out"
    if "doubt" in text:
        return "Doubtful"
    if any(token in text for token in ["question", "gtd", "day-to-day", "day to day", "dtd"]):
        return "Questionable"
    if "probable" in text:
        return "Probable"
    return str(raw_status or fantasy_status or "Questionable").strip()


@st.cache_data(ttl=300, show_spinner=False)
def get_espn_injuries_standard(
    team_ids: tuple[int, ...] | None = None,
) -> pd.DataFrame:
    """Normalize ESPN injuries to the columns consumed by processing.

    Matchup loads pass the two official NBA team ids, which uses the per-team
    endpoint already validated in Streamlit Cloud. A no-argument call keeps a
    league-feed fallback for diagnostics.
    """
    directory = get_espn_team_directory()
    espn_to_official: dict[str, int] = {}
    if directory is not None and not directory.empty:
        espn_to_official = {
            str(row["ESPN_TEAM_ID"]): int(row["TEAM_ID"])
            for _, row in directory.iterrows()
        }

    entries: list[dict[str, Any]] = []
    successful_team_ids: set[int] = set()

    if team_ids:
        for official_id in team_ids:
            try:
                espn_id = _espn_team_id_for_official(int(official_id))
                payload = _request_json(
                    ESPN_TEAM_INJURIES_URL.format(team_id=espn_id)
                )
            except Exception:
                continue

            successful_team_ids.add(int(official_id))
            for entry in payload.get("injuries") or []:
                if not isinstance(entry, dict):
                    continue
                item = dict(entry)
                item["_OFFICIAL_TEAM_ID"] = int(official_id)
                entries.append(item)
    else:
        try:
            payload = _request_json(ESPN_INJURIES_URL)
        except Exception:
            payload = {}

        for raw in payload.get("injuries") or []:
            if not isinstance(raw, dict):
                continue

            # ESPN can expose either one injury per row or a team group with a
            # nested injuries list, depending on the endpoint/edge response.
            nested = raw.get("injuries")
            if isinstance(nested, list):
                team = raw.get("team") or raw
                espn_team_id = str(team.get("id") or "")
                official_id = espn_to_official.get(espn_team_id)
                for injury in nested:
                    if not isinstance(injury, dict):
                        continue
                    item = dict(injury)
                    item.setdefault("team", team)
                    item["_OFFICIAL_TEAM_ID"] = official_id
                    entries.append(item)
            else:
                entries.append(dict(raw))

    rows: list[dict[str, Any]] = []
    for injury in entries:
        team = injury.get("team") or {}
        official_team_id = injury.get("_OFFICIAL_TEAM_ID")
        if not official_team_id:
            team_abbr = str(team.get("abbreviation") or "").upper()
            official_team_id = OFFICIAL_TEAM_ID_BY_ABBR.get(team_abbr)
        if not official_team_id:
            official_team_id = espn_to_official.get(str(team.get("id") or ""))

        athlete = injury.get("athlete") or {}
        # Per-team endpoint uses injury={type, location}; some league payloads
        # use details/type directly.
        injury_detail = injury.get("injury") or {}
        details = injury.get("details") or {}
        fantasy = details.get("fantasyStatus") or {}
        fantasy_status = (
            fantasy.get("abbreviation")
            if isinstance(fantasy, dict)
            else fantasy
        )

        name = athlete.get("displayName") or athlete.get("fullName") or ""
        pid = pd.to_numeric(athlete.get("id"), errors="coerce")
        status = _normalize_injury_status(
            injury.get("status"),
            fantasy_status,
        )

        injury_type = injury.get("type") or injury_detail.get("type") or {}
        injury_type_text = (
            injury_type.get("description") or injury_type.get("name") or ""
            if isinstance(injury_type, dict)
            else str(injury_type or "")
        )
        reason = (
            injury.get("shortComment")
            or injury.get("longComment")
            or details.get("detail")
            or details.get("type")
            or injury_type_text
            or injury_detail.get("location")
            or ""
        )

        rows.append({
            "PLAYER_ID_IR": int(pid) if pd.notna(pid) else None,
            "PLAYER_KEY_IR": _name_key(name),
            "PLAYER_NAME_IR": str(name),
            "TEAM_ID_IR": int(official_team_id) if official_team_id else None,
            "INJ_STATUS": status,
            "INJ_REASON": str(reason or ""),
            "INJ_REPORT_URL": "",
            "INJ_SOURCE": "ESPN",
            "QUERY_OK": True,
        })

    # Sentinel rows distinguish "query succeeded and no injuries" from a failed
    # request, so the processing layer never marks a failed team as Available.
    for official_id in sorted(successful_team_ids):
        rows.append({
            "PLAYER_ID_IR": None,
            "PLAYER_KEY_IR": "",
            "PLAYER_NAME_IR": "",
            "TEAM_ID_IR": int(official_id),
            "INJ_STATUS": "",
            "INJ_REASON": "",
            "INJ_REPORT_URL": "",
            "INJ_SOURCE": "ESPN",
            "QUERY_OK": True,
        })

    return pd.DataFrame(rows)



def _stat_map(stat_block: dict[str, Any], athlete_row: dict[str, Any]) -> dict[str, Any]:
    keys = stat_block.get("keys") or stat_block.get("names") or stat_block.get("labels") or []
    values = athlete_row.get("stats") or []
    return {re.sub(r"[^a-z0-9]", "", str(k).lower()): v for k, v in zip(keys, values)}


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _made_attempted(value: Any) -> tuple[float | None, float | None]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", str(value or ""))
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def _first(stats: dict[str, Any], *aliases: str) -> Any:
    for alias in aliases:
        key = re.sub(r"[^a-z0-9]", "", alias.lower())
        if key in stats:
            return stats[key]
    return None


def _extract_game_rows(payload: dict[str, Any], game_id: str, game_date: Any) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for team_block in (payload.get("boxscore") or {}).get("players") or []:
        team = team_block.get("team") or {}
        abbr = str(team.get("abbreviation") or "").upper()
        official_team_id = OFFICIAL_TEAM_ID_BY_ABBR.get(abbr)
        if not official_team_id:
            continue

        for stat_block in team_block.get("statistics") or []:
            for athlete_row in stat_block.get("athletes") or []:
                if athlete_row.get("didNotPlay") is True:
                    continue
                athlete = athlete_row.get("athlete") or {}
                stats = _stat_map(stat_block, athlete_row)
                minutes = _num(_first(stats, "min", "minutes"))
                pts = _num(_first(stats, "pts", "points"))
                reb = _num(_first(stats, "reb", "rebounds", "totalRebounds"))
                ast = _num(_first(stats, "ast", "assists"))
                _, fga = _made_attempted(_first(stats, "fg", "fieldGoalsMade-fieldGoalsAttempted"))
                fg3m, fg3a = _made_attempted(
                    _first(stats, "3pt", "3p", "threePointFieldGoalsMade-threePointFieldGoalsAttempted")
                )
                if fga is None:
                    fga = _num(_first(stats, "fga", "fieldGoalsAttempted"))
                if fg3m is None:
                    fg3m = _num(_first(stats, "3pm", "threePointFieldGoalsMade"))
                if fg3a is None:
                    fg3a = _num(_first(stats, "3pa", "threePointFieldGoalsAttempted"))

                if minutes is None and all(v is None for v in [pts, reb, ast, fga, fg3a]):
                    continue

                pos = (athlete.get("position") or {}).get("abbreviation") or ""
                rows.append({
                    "GAME_ID": str(game_id),
                    "GAME_DATE": pd.to_datetime(game_date, errors="coerce"),
                    "TEAM_ID": int(official_team_id),
                    "TEAM_ABBR": abbr,
                    "PLAYER_ID": pd.to_numeric(athlete.get("id"), errors="coerce"),
                    "POSITION": str(pos),
                    "POSITION_GROUP": _position_group(pos),
                    "MIN": float(minutes or 0.0),
                    "PTS": float(pts or 0.0),
                    "REB": float(reb or 0.0),
                    "AST": float(ast or 0.0),
                    "FG3M": float(fg3m or 0.0),
                    "FGA": float(fga or 0.0),
                    "FG3A": float(fg3a or 0.0),
                })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
    return df


@st.cache_data(ttl=43200, show_spinner=False)
def build_espn_defense_context(
    season: str,
    season_scope: str = "Regular Season",
    as_of_date: str | None = None,
    window_games: int = DEFENSE_WINDOW_GAMES,
) -> dict[str, pd.DataFrame]:
    """Build league-wide defense and DvP from ESPN boxscores."""
    directory = get_espn_team_directory()
    if directory is None or directory.empty:
        return {"team_defense": pd.DataFrame(), "position_defense": pd.DataFrame(), "position_baseline": pd.DataFrame()}

    cutoff = _cutoff_timestamp(as_of_date)
    team_ids = [int(v) for v in directory["TEAM_ID"].dropna().tolist()]

    schedules: dict[int, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        future_map = {
            pool.submit(get_espn_team_schedule, tid, season, season_scope): tid
            for tid in team_ids
        }
        for future in as_completed(future_map):
            tid = future_map[future]
            try:
                schedules[tid] = future.result()
            except Exception:
                schedules[tid] = pd.DataFrame()

    sample_ids_by_team: dict[int, set[str]] = {}
    team_gp: dict[int, int] = {}
    game_dates: dict[str, Any] = {}

    for tid, schedule in schedules.items():
        if schedule is None or schedule.empty:
            sample_ids_by_team[tid] = set()
            team_gp[tid] = 0
            continue
        work = schedule.copy()
        dates = pd.to_datetime(work["GAME_DATE"], errors="coerce")
        mask = work["COMPLETED"].fillna(False)
        if cutoff is not None:
            mask &= dates.dt.date <= cutoff.date()
        completed = work[mask].copy().sort_values("GAME_DATE", ascending=False)
        team_gp[tid] = int(len(completed))
        sample = completed.head(int(window_games)) if window_games else completed
        ids = set(sample["GAME_ID"].astype(str).tolist())
        sample_ids_by_team[tid] = ids
        for _, row in sample.iterrows():
            game_dates[str(row["GAME_ID"])] = row.get("GAME_DATE")

    unique_game_ids = sorted(set().union(*sample_ids_by_team.values())) if sample_ids_by_team else []
    summaries: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        future_map = {
            pool.submit(get_espn_game_summary, gid): gid
            for gid in unique_game_ids
        }
        for future in as_completed(future_map):
            gid = future_map[future]
            try:
                summaries[gid] = future.result()
            except Exception:
                continue

    game_frames: dict[str, pd.DataFrame] = {}
    for gid, payload in summaries.items():
        frame = _extract_game_rows(payload, gid, game_dates.get(gid))
        if frame is not None and not frame.empty:
            game_frames[gid] = frame

    team_allowed_records: list[dict[str, Any]] = []
    pos_allowed_records: list[dict[str, Any]] = []
    baseline_rows: list[pd.DataFrame] = []

    for gid, frame in game_frames.items():
        baseline_rows.append(frame)
        teams_in_game = [int(v) for v in frame["TEAM_ID"].dropna().unique().tolist()]
        if len(teams_in_game) != 2:
            continue
        for defending in teams_in_game:
            if gid not in sample_ids_by_team.get(defending, set()):
                continue
            opponent_rows = frame[frame["TEAM_ID"] != defending].copy()
            if opponent_rows.empty:
                continue

            team_allowed_records.append({
                "TEAM_ID": defending,
                "GAME_ID": gid,
                "PTS": float(opponent_rows["PTS"].sum()),
                "REB": float(opponent_rows["REB"].sum()),
                "AST": float(opponent_rows["AST"].sum()),
                "PRA": float(opponent_rows["PRA"].sum()),
                "3PM": float(opponent_rows["FG3M"].sum()),
                "FGA": float(opponent_rows["FGA"].sum()),
                "3PA": float(opponent_rows["FG3A"].sum()),
            })

            for pos, pos_rows in opponent_rows.groupby("POSITION_GROUP"):
                for _, prow in pos_rows.iterrows():
                    pos_allowed_records.append({
                        "TEAM_ID": defending,
                        "GAME_ID": gid,
                        "POSITION_GROUP": str(pos),
                        "PTS": float(prow["PTS"]),
                        "REB": float(prow["REB"]),
                        "AST": float(prow["AST"]),
                        "PRA": float(prow["PRA"]),
                        "FG3M": float(prow["FG3M"]),
                        "FGA": float(prow["FGA"]),
                        "FG3A": float(prow["FG3A"]),
                    })

    metric_names = ["PTS", "REB", "AST", "PRA", "3PM", "FGA", "3PA"]
    allowed = pd.DataFrame(team_allowed_records)
    team_rows: list[dict[str, Any]] = []
    if not allowed.empty:
        for tid, group in allowed.groupby("TEAM_ID"):
            row: dict[str, Any] = {
                "TEAM_ID": int(tid),
                "TEAM_GP": int(team_gp.get(int(tid), group["GAME_ID"].nunique())),
                "TEAM_DEF_SAMPLE_GP": int(group["GAME_ID"].nunique()),
                "DEFENSE_SOURCE": f"ESPN L{int(window_games)}",
            }
            for metric in metric_names:
                row[f"TEAM_DEF_{metric}"] = float(group[metric].mean())
            team_rows.append(row)

    team_defense = pd.DataFrame(team_rows)
    if not team_defense.empty:
        team_count = len(team_defense)
        team_defense["TEAM_DEF_TEAM_COUNT"] = int(team_count)
        for metric in metric_names:
            value_col = f"TEAM_DEF_{metric}"
            team_defense[f"TEAM_DEF_PCT_{metric}"] = (
                team_defense[value_col].rank(method="average", pct=True, ascending=True).clip(0.0, 1.0)
            )
            team_defense[f"TEAM_DEF_RANK_{metric}"] = (
                team_defense[value_col].rank(method="average", ascending=True).round().astype(int)
            )

    pos_allowed = pd.DataFrame(pos_allowed_records)
    pos_rows_out: list[dict[str, Any]] = []
    if not pos_allowed.empty:
        for (tid, pos), group in pos_allowed.groupby(["TEAM_ID", "POSITION_GROUP"]):
            pos_rows_out.append({
                "TEAM_ID": int(tid),
                "POSITION_GROUP": str(pos),
                "GP": int(len(group)),
                "PTS": float(group["PTS"].mean()),
                "REB": float(group["REB"].mean()),
                "AST": float(group["AST"].mean()),
                "FG3M": float(group["FG3M"].mean()),
                "FGA": float(group["FGA"].mean()),
                "FG3A": float(group["FG3A"].mean()),
            })
    position_defense = pd.DataFrame(pos_rows_out)

    baseline = pd.concat(baseline_rows, ignore_index=True) if baseline_rows else pd.DataFrame()
    baseline_out: list[dict[str, Any]] = []
    if not baseline.empty:
        for pos, group in baseline.groupby("POSITION_GROUP"):
            baseline_out.append({
                "POSITION_GROUP": str(pos),
                "GP": int(len(group)),
                "PTS": float(group["PTS"].mean()),
                "REB": float(group["REB"].mean()),
                "AST": float(group["AST"].mean()),
                "FG3M": float(group["FG3M"].mean()),
                "FGA": float(group["FGA"].mean()),
                "FG3A": float(group["FG3A"].mean()),
            })
    position_baseline = pd.DataFrame(baseline_out)

    return {
        "team_defense": team_defense,
        "position_defense": position_defense,
        "position_baseline": position_baseline,
    }


def get_espn_team_defense_percentiles(
    season: str,
    season_scope: str = "Regular Season",
    as_of_date: str | None = None,
) -> pd.DataFrame:
    return build_espn_defense_context(season, season_scope, as_of_date)["team_defense"].copy()


def get_espn_position_allowed_profile(
    season: str,
    opponent_team_id: int,
    position_group: str,
    season_scope: str = "Regular Season",
    as_of_date: str | None = None,
) -> pd.DataFrame:
    df = build_espn_defense_context(season, season_scope, as_of_date)["position_defense"]
    if df is None or df.empty:
        return pd.DataFrame()
    return df[
        (pd.to_numeric(df["TEAM_ID"], errors="coerce") == int(opponent_team_id))
        & (df["POSITION_GROUP"].astype(str) == str(position_group))
    ][["GP", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]].copy()


def get_espn_league_position_baseline(
    season: str,
    position_group: str,
    season_scope: str = "Regular Season",
    as_of_date: str | None = None,
) -> pd.DataFrame:
    df = build_espn_defense_context(season, season_scope, as_of_date)["position_baseline"]
    if df is None or df.empty:
        return pd.DataFrame()
    return df[
        df["POSITION_GROUP"].astype(str) == str(position_group)
    ][["GP", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]].copy()


def clear_espn_pregame_cache() -> None:
    for fn in [get_espn_team_rotation, get_espn_injuries_standard]:
        try:
            fn.clear()
        except Exception:
            pass

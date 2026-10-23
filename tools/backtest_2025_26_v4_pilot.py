"""Historical Matchup V4 *pilot* using the unchanged native processing functions.

Run only on analysis/backtest-v4-2025-26-pilot. One fixture-date
per month (Nov-Apr) is selected using game schedules; roster/production
and all opponent defenses are as-of the Brazilian *start* of game day.

Historical pregame injury reports and historical depth charts are
not available in this test. Minutes are internal-only and injury unknown.
Output is conditional on a previously-seen player participating. A
success status does not validate real-money betting decisions.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

import processamento as proc
import espn_context as ec
from api_espn import (
    get_espn_game_summary, get_espn_player_log,
)
from projection_validation import _project_core, LOG_COLUMNS
from tools.backtest_2025_26_six_teams import (
    fetch_historical_boxscores, METRICS, SEASON, TEAMS,
)

warnings.filterwarnings("ignore", message=".*ScriptRunContext.*")
OUT = Path("v4_pilot_results")
OUT.mkdir(exist_ok=True)
TEAM_ID_TO_ABBR = {v: k for k, v in TEAMS.items()}
CACHE = {}
ACTIVE = {"roster": pd.DataFrame(), "logs": pd.DataFrame()}
FALLBACK_PLAYERS = set()
BRAZIL_TZ = "America/Sao_Paulo"

def _pregame(ts, iso_date):
    times = pd.to_datetime(ts, errors="coerce", utc=True).dt.tz_convert(BRAZIL_TZ)
    return times < pd.Timestamp(iso_date).tz_localize(BRAZIL_TZ)

def _historical_roster(team_rows, date_str):
    """Roster membership and position from up to five *previous* team boxscores."""
    before = team_rows[_pregame(team_rows["GAME_DATE"], date_str)]
    game_ids = (before[["GAME_ID", "GAME_DATE"]]
                .drop_duplicates("GAME_ID")
                .sort_values("GAME_DATE", ascending=False)
                .head(5)["GAME_ID"].astype(str).tolist())
    positions = {}
    for gid in game_ids:
        try:
            sample = team_rows[team_rows["GAME_ID"].astype(str).eq(gid)]
            sample_date = sample["GAME_DATE"].iloc[0]
            frame = ec._extract_game_rows(get_espn_game_summary(gid), gid, sample_date)
            frame = frame[frame["TEAM_ID"].eq(int(team_rows["TEAM_ID"].iloc[0]))]
            for row in frame.itertuples(index=False):
                if int(row.PLAYER_ID) not in positions:
                    positions[int(row.PLAYER_ID)] = str(row.POSITION or "")
        except Exception as exc:
            print("POSITION_LOOKUP_FAILURE", gid, type(exc).__name__, flush=True)
    recent = before[before["GAME_ID"].astype(str).isin(game_ids)]
    recent = recent.sort_values("GAME_DATE", ascending=False).drop_duplicates("PLAYER_ID")
    result = pd.DataFrame([{
        "PLAYER_ID": int(r.PLAYER_ID),
        "PLAYER": str(r.PLAYER_NAME),
        "POSITION": positions.get(int(r.PLAYER_ID), ""),
        "TEAM_ID": int(r.TEAM_ID),
    } for r in recent.itertuples(index=False) if positions.get(int(r.PLAYER_ID), "")])
    return result

def _fetch_athlete(pid, historical_games):
    if pid in CACHE:
        return CACHE[pid]
    try:
        df = get_espn_player_log(int(pid), SEASON, "Regular Season")
        if df.empty:
            raise RuntimeError("empty athlete history")
        df = df[df["GAME_ID"].astype(str).ne("401809839")].copy()
    except Exception:
        FALLBACK_PLAYERS.add(pid)
        df = historical_games[historical_games["PLAYER_ID"].eq(int(pid))].copy()
    if not df.empty:
        df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce", utc=True)
        df = df.dropna(subset=["GAME_DATE"]).sort_values("GAME_DATE")
        df["PLAYER_ID"] = int(pid)
        df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
        df = df.drop_duplicates(["GAME_ID"], keep="first")
    CACHE[pid] = df
    return df

def _past_player_logs(roster, full_rows, date_str):
    rows = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        future_to_pid = {
            pool.submit(_fetch_athlete, int(pid), full_rows): int(pid)
            for pid in roster["PLAYER_ID"].unique()
        }
        for f in as_completed(future_to_pid):
            pid = future_to_pid[f]
            try:
                part = f.result()
                part = part[_pregame(part["GAME_DATE"], date_str)].copy()
                if not part.empty:
                    rows.append(part)
            except Exception as exc:
                print("ATHLETE_LOG_FAILURE", pid, repr(exc), flush=True)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()

def _roster_provider(team_id, season):
    return ACTIVE["roster"].copy()

def _log_provider(team_id, season, season_scope="Regular Season", as_of_date=None):
    return ACTIVE["logs"].copy()

def _fixture_dates(rows):
    games = rows[["GAME_ID", "GAME_DATE", "TEAM_ID"]].drop_duplicates()
    games["DAY"] = games["GAME_DATE"].dt.tz_convert(BRAZIL_TZ).dt.date
    games = games[(games["DAY"] >= date(2025, 11, 16)) &
                  (games["DAY"] <= date(2026, 4, 12))].copy()
    games["MONTH"] = games["DAY"].astype(str).str[:7]
    selections = []
    for month, part in games.groupby("MONTH"):
        scores = part.groupby("DAY")["GAME_ID"].nunique().sort_values(ascending=False)
        if scores.empty:
            continue
        chosen = scores.index[0]
        selections.append((month, chosen, int(scores.loc[chosen])))
    return selections

def main():
    ground_truth, coverage = fetch_historical_boxscores()
    ground_truth["GAME_DATE"] = pd.to_datetime(
        ground_truth["GAME_DATE"], errors="coerce", utc=True
    )
    print("PILOT_DATES", _fixture_dates(ground_truth), flush=True)

    # Patch only data providers in this analysis process. Native build_team_table
    # and enrich_team_with_context remain unchanged, including Matchup V4 math.
    proc.get_espn_team_roster = _roster_provider
    proc.get_espn_roster_player_logs = _log_provider

    detail = []
    diagnostics = []
    for month, target_day, _ in _fixture_dates(ground_truth):
        cutoff = str(target_day)
        subset = ground_truth[ground_truth["LOCAL_DATE"].eq(target_day)]
        target_games = (subset[["TEAM_ID", "GAME_ID", "GAME_DATE", "HIST_TEAM"]]
                       .drop_duplicates(["TEAM_ID", "GAME_ID"]))
        for fixture in target_games.itertuples(index=False):
            team_id, gid = int(fixture.TEAM_ID), str(fixture.GAME_ID)
            before_team = ground_truth[ground_truth["TEAM_ID"].eq(team_id)]
            roster = _historical_roster(before_team, cutoff)
            if roster.empty:
                continue
            past_logs = _past_player_logs(roster, ground_truth, cutoff)
            if past_logs.empty:
                continue
            assert _pregame(past_logs["GAME_DATE"], cutoff).all(), "Future-game input!"
            ACTIVE["roster"], ACTIVE["logs"] = roster, past_logs
            summary = get_espn_game_summary(gid)
            game_info = (summary.get("header") or {}).get("competitions") or []
            competitors = game_info[0].get("competitors", []) if game_info else []
            opponents = []
            for comp in competitors:
                espn_abbr = str((comp.get("team") or {}).get("abbreviation") or "").upper()
                normalized = proc.TEAM_ABBR_LOOKUP
                opponent_tid = next(
                    (tid for tid, value in normalized.items()
                     if str(value).upper() == ec.normalize_espn_team_abbr(espn_abbr)), None
                )
                if opponent_tid != team_id and opponent_tid:
                    opponents.append(opponent_tid)
            if not opponents:
                print("SKIP_NO_OPPONENT", gid, flush=True)
                continue
            opp = int(opponents[0])
            try:
                base = proc.build_team_table(team_id, SEASON, as_of_date=cutoff)
                if base.empty:
                    continue
                base["INJ_STATUS"] = "Unknown"
                base["IS_UNAVAILABLE"] = False
                model = proc.enrich_team_with_context(
                    base, team_id, opp, "Opponent", SEASON,
                    season_scope="Regular Season", as_of_date=cutoff
                )
            except Exception as exc:
                print("REPLAY_ERROR", cutoff, gid, repr(exc), flush=True)
                continue

            observed = subset[(subset["TEAM_ID"].eq(team_id)) &
                              (subset["GAME_ID"].astype(str).eq(gid))]
            pred_count = 0
            evaluated_players = 0
            for row in model.to_dict("records"):
                pid = int(row["PLAYER_ID"])
                match = observed[observed["PLAYER_ID"].eq(pid)]
                hist = past_logs[past_logs["PLAYER_ID"].eq(pid)].copy()
                if match.empty or len(hist) < 10:
                    continue
                current = match.iloc[0]
                hist = hist.sort_values("GAME_DATE")
                core = _project_core(hist)
                if not core:
                    continue
                evaluated_players += 1
                for metric in METRICS:
                    col = "PRA" if metric == "PRA" else LOG_COLUMNS[metric]
                    v4 = pd.to_numeric(row.get("PROJ_" + metric + "_V1"), errors="coerce")
                    if metric not in core or not np.isfinite(v4) or pd.isna(current[col]):
                        continue
                    actual = float(current[col])
                    l10 = float(hist.tail(10)[col].mean()) if col != "PRA" else (
                        hist.tail(10)["PTS"].mean() +
                        hist.tail(10)["REB"].mean() +
                        hist.tail(10)["AST"].mean())
                    if not np.isfinite(l10):
                        continue
                    detail.append({
                        "DATA": cutoff, "MES": month, "GAME_ID": gid,
                        "PLAYER_ID": pid, "TEAM_ID": team_id,
                        "METRICA": metric, "REAL": actual,
                        "V4": float(v4), "CORE": float(core[metric]), "L10": l10,
                        "ERRO_V4": abs(float(v4) - actual),
                        "ERRO_CORE": abs(float(core[metric]) - actual),
                        "ERRO_L10": abs(l10 - actual),
                        "BIAS_V4": float(v4) - actual,
                        "MATCHUP_EFFECT": row.get("MATCHUP_EFFECT_PCT_" + metric + "_V2"),
                        "TEAM_DEF_SAMPLE_GP": row.get("OPP_DEF_SAMPLE_GP"),
                        "TEAM_DEF_TEAM_COUNT": row.get("TEAM_DEF_TEAM_COUNT"),
                    })
                    pred_count += 1
            print("PILOT_GAME", cutoff, gid, team_id,
                  "roster", len(roster), "actual", len(observed),
                  "eval_players", evaluated_players,
                  "comparisons", pred_count, flush=True)
            diagnostics.append({
                "day": cutoff, "game_id": gid, "team_id": team_id,
                "roster_count": len(roster),
                "actual_count": len(observed), "evaluated": evaluated_players,
                "metrics": pred_count,
                "def_sample_max": float(pd.to_numeric(
                    model.get("OPP_DEF_SAMPLE_GP"), errors="coerce"
                ).max()) if not model.empty else 0.0,
            })

    result = pd.DataFrame(detail)
    if not result.empty:
        result.to_csv(OUT / "v4_pilot_details.csv", index=False)
        summary = result.groupby("METRICA").agg(
            n=("V4", "size"), mae_v4=("ERRO_V4", "mean"),
            mae_core=("ERRO_CORE", "mean"), mae_l10=("ERRO_L10", "mean"),
            bias_v4=("BIAS_V4", "mean"),
            defense_games=("TEAM_DEF_SAMPLE_GP", "mean"),
            league_count=("TEAM_DEF_TEAM_COUNT", "mean"),
        ).reset_index()
        summary.to_csv(OUT / "v4_pilot_metrics.csv", index=False)
        print("PILOT_V4_RESULT_NOT_FINAL", summary.to_string(index=False), flush=True)
    manifest = {
        "season": SEASON, "teams": list(TEAMS), "dates": _fixture_dates(ground_truth),
        "n_metric_predictions": len(result),
        "source_coverage": coverage, "games": diagnostics,
        "athlete_gamelog_fallback_ids": sorted(FALLBACK_PLAYERS),
        "historic_roster": "union of previous five completed team-game boxscores",
        "player_stats": "ESPN per-athlete gamelog, only strictly prior dates",
        "defense": "native ESPN defense as_of_date and native Matchup V4",
        "historical_injuries": "Unavailable; neutral Unknown status",
        "historical_depth_chart": "Unavailable; internal minutes only",
        "selection": "previously seen roster; evaluation conditional on playing",
        "disclaimer": "PILOT, not 6-month production backtest",
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8"
    )
    if len(result) < 20:
        raise RuntimeError("V4 pilot did not produce a sufficiently populated evaluation")
    if pd.to_numeric(result["TEAM_DEF_SAMPLE_GP"], errors="coerce").fillna(0).max() <= 0:
        raise RuntimeError("V4 pilot lacked genuine prior opponent-defense sample")

if __name__ == "__main__":
    main()

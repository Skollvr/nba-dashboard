"""Forward-only NBA V4 monitoring. Never reconstruct predictions after the game.

CLI (GitHub Actions): python model_tracking.py snapshot|settle
Snapshots are immutable, saved before tipoff; results are joined by ESPN game/player.
The tracker does not update projection weights, minutes or game-facing state.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

METRICS = ("PTS", "REB", "AST", "PRA", "3PM", "FGA", "3PA")
STORE = Path(__file__).resolve().parent / "data"
SNAPSHOTS = STORE / "model_snapshots.jsonl"
RESULTS = STORE / "model_results.jsonl"
BRT = ZoneInfo("America/Sao_Paulo")
VERSION = "Matchup V4"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(row, dict):
            result.append(row)
    return result


def append_unique_jsonl(path: Path, records: list[dict], key: str = "key") -> int:
    """Append distinct immutable snapshots/results; never revise older forecasts."""
    seen = {str(row.get(key)) for row in load_jsonl(path)}
    fresh = [row for row in records if str(row.get(key)) not in seen
             and not seen.add(str(row.get(key)))]
    if not fresh:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as dest:
        for row in fresh:
            dest.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    return len(fresh)


def finite_float(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def snapshot_game(game: pd.Series, away: pd.DataFrame, home: pd.DataFrame,
                  captured_at: datetime) -> list[dict]:
    """Freeze V4 predictions only when a real future tipoff is verified."""
    tipoff = pd.to_datetime(game.get("GAME_DATETIME_BRT"), errors="coerce", utc=True)
    if pd.isna(tipoff):
        return []
    captured_utc = captured_at.astimezone(timezone.utc)
    minutes_to_tipoff = (tipoff.to_pydatetime() - captured_utc).total_seconds() / 60
    if not 20 <= minutes_to_tipoff <= 360:
        return []
    gid = str(game.get("GAME_ID") or "").strip()
    if not gid:
        return []
    records = []
    for df in (away, home):
        if df is None or df.empty:
            continue
        for _, player in df.iterrows():
            pid = finite_float(player.get("PLAYER_ID"))
            team_id = finite_float(player.get("TEAM_ID"))
            gp = finite_float(player.get("SEASON_GP"))
            if pid is None or team_id is None or team_id <= 0 or gp is None or gp < 1:
                continue
            if str(player.get("INJ_STATUS") or "").casefold() in {"out", "doubtful"}:
                continue
            unavailable = player.get("IS_UNAVAILABLE", False)
            if pd.notna(unavailable) and bool(unavailable):
                continue
            for metric in METRICS:
                pred = finite_float(player.get(f"PROJ_{metric}_V1"))
                if pred is None or pred < 0:
                    continue
                line = finite_float(player.get(f"MARKET_{metric}_LINE"))
                line = line if line is not None and line > 0 else None
                records.append({
                    "key": f"{gid}:{int(pid)}:{metric}",
                    "game_id": gid,
                    "game_date_brt": str(game.get("GAME_DATE_BRT") or tipoff.tz_convert(BRT).date()),
                    "tipoff_utc": tipoff.isoformat(),
                    "captured_utc": captured_utc.isoformat(),
                    "version": VERSION,
                    "player_id": int(pid),
                    "player": str(player.get("PLAYER") or ""),
                    "team_id": int(team_id),
                    "metric": metric,
                    "projection": round(pred, 4),
                    "minutes": finite_float(player.get("PROJ_MIN_V1")),
                    "season_gp": int(gp),
                    "line": line,
                    "line_source": str(player.get(f"MARKET_{metric}_SOURCE") or ""),
                })
    return records


def result_game(snaps: list[dict], summary: dict) -> list[dict]:
    """Settle only officially completed ESPN games with observed box scores."""
    competitions = ((summary.get("header") or {}).get("competitions") or [])
    finished = any(bool(((comp.get("status") or {}).get("type") or {}).get("completed"))
                   for comp in competitions if isinstance(comp, dict))
    if not finished or not snaps:
        return []
    from api_espn import _extract_summary_rows

    meta = {
        "TEAM_ID": int(snaps[0]["team_id"]),
        "GAME_ID": str(snaps[0]["game_id"]),
        "OPPONENT_ABBR": "",
        "HOME_AWAY": "",
        "GAME_DATE": pd.to_datetime(snaps[0]["tipoff_utc"], utc=True),
        "WINNER": None,
    }
    observed = _extract_summary_rows(
        summary, game_meta=meta, season_scope="Regular Season"
    )
    by_player = {(int(row["PLAYER_ID"]), int(row["TEAM_ID"])): row for row in observed}
    columns = {"PTS": "PTS", "REB": "REB", "AST": "AST", "PRA": "PRA",
               "3PM": "FG3M", "FGA": "FGA", "3PA": "FG3A"}
    results = []
    for snap in snaps:
        box = by_player.get((int(snap["player_id"]), int(snap["team_id"])))
        if box is None:
            continue  # DNP is not a zero-point forecast error
        metric = snap["metric"]
        actual = (finite_float(box.get("PTS")) or 0) + (finite_float(box.get("REB")) or 0) + (finite_float(box.get("AST")) or 0) if metric == "PRA" else finite_float(box.get(columns[metric]))
        if actual is None:
            continue  # unavailable shooting stats do not become zeros
        pred = finite_float(snap.get("projection"))
        if pred is None:
            continue
        line = finite_float(snap.get("line"))
        edge = pred - line if line is not None else None
        market_outcome = (
            "Push" if actual == line else "Over" if actual > line else "Under"
        ) if line is not None else None
        # Direction correctness is only assessed for settled outcomes and
        # nonzero model edges; it is not an ROI or an implied probability.
        direction_hit = (
            bool((edge > 0 and market_outcome == "Over") or
                 (edge < 0 and market_outcome == "Under"))
            if edge is not None and abs(edge) > 1e-9
               and market_outcome not in {None, "Push"} else None
        )
        results.append({
            "key": str(snap["key"]),
            "actual": float(actual),
            "error": round(pred - actual, 4),
            "absolute_error": round(abs(pred - actual), 4),
            # Target-attainment is asymmetric by design and distinct from MAE.
            # Exceeding the forecast counts as a reached target, not a shortfall.
            "target_reached": bool(actual >= pred),
            "shortfall": round(max(pred - actual, 0.0), 4),
            "market_outcome": market_outcome,
            "direction_hit": direction_hit,
            "settled_utc": datetime.now(timezone.utc).isoformat(),
        })
    return results


def tracking_frame(snapshots: list[dict], results: list[dict]) -> pd.DataFrame:
    """Join by immutable snapshot key, without retroactive recalculation."""
    if not snapshots:
        return pd.DataFrame()
    frame = pd.DataFrame(snapshots).drop_duplicates("key", keep="first")
    if results:
        resolved = pd.DataFrame(results).drop_duplicates("key", keep="first")
        frame = frame.merge(
            resolved[[col for col in ("key", "actual", "error", "absolute_error",
                                        "target_reached", "shortfall",
                                        "market_outcome", "direction_hit") if col in resolved]],
            on="key", how="left", validate="one_to_one",
        )
    else:
        frame["actual"] = float("nan")
        frame["error"] = float("nan")
        frame["absolute_error"] = float("nan")
        frame["target_reached"] = None
        frame["shortfall"] = float("nan")
        frame["market_outcome"] = None
        frame["direction_hit"] = None
    return frame


def capture(today=None, now=None):
    from api_nba import get_games_for_date
    from pdf_reader import get_season_string
    from processamento import get_matchup_context

    now = now or datetime.now(timezone.utc)
    today = today or now.astimezone(BRT).date()
    games = get_games_for_date(today)
    if games is None or games.empty:
        print("No games scheduled")
        return 0
    saved = {str(r.get("game_id")) for r in load_jsonl(SNAPSHOTS)}
    created = 0
    for _, game in games.iterrows():
        gid = str(game.get("GAME_ID") or "")
        tipoff = pd.to_datetime(game.get("GAME_DATETIME_BRT"), errors="coerce", utc=True)
        if not gid or gid in saved or pd.isna(tipoff):
            continue
        lead = (tipoff.to_pydatetime() - now.astimezone(timezone.utc)).total_seconds() / 60
        if not 20 <= lead <= 360:
            continue
        try:
            away, home = get_matchup_context(
                int(game["VISITOR_TEAM_ID"]), int(game["HOME_TEAM_ID"]),
                str(game["away_team_name"]), str(game["home_team_name"]),
                get_season_string(today), True,
                season_scope="Regular Season",
                include_fanduel=False,
                roster_season=get_season_string(today),
                target_date=today,
            )
            # build_team_table does not retain TEAM_ID in its card schema.
            # Add source-specific IDs only in the monitoring copy, without
            # changing app projections, rosters or the Matchup V4 logic.
            away = away.assign(TEAM_ID=int(game["VISITOR_TEAM_ID"]))
            home = home.assign(TEAM_ID=int(game["HOME_TEAM_ID"]))
            # Recheck the real clock AFTER network/model computation: a slow
            # provider must never turn a post-tipoff value into a pregame capture.
            captured_at = datetime.now(timezone.utc)
            rows = snapshot_game(game, away, home, captured_at)
            # Never write a partial/unusable forecast group.
            if rows:
                created += append_unique_jsonl(SNAPSHOTS, rows)
                print(f"Captured {gid}: {len(rows)} predictions")
        except Exception as exc:
            print(f"Skipped game {gid}: {type(exc).__name__}: {exc}")
    return created


def settle():
    from api_espn import get_espn_game_summary

    snaps = load_jsonl(SNAPSHOTS)
    done = {str(item.get("key")) for item in load_jsonl(RESULTS)}
    unresolved: dict[str, list[dict]] = {}
    for snap in snaps:
        if str(snap.get("key")) not in done:
            unresolved.setdefault(str(snap["game_id"]), []).append(snap)
    created = 0
    for gid, items in unresolved.items():
        try:
            tipoff = pd.to_datetime(items[0]["tipoff_utc"], utc=True)
            age_hours = (pd.Timestamp.now(tz="UTC") - tipoff).total_seconds() / 3600
            if age_hours < 3 or age_hours > 24 * 45:
                continue
            settled = result_game(items, get_espn_game_summary(gid))
            created += append_unique_jsonl(RESULTS, settled)
            if settled:
                print(f"Settled {gid}: {len(settled)} measurements")
        except Exception as exc:
            print(f"Game {gid}: settlement deferred ({type(exc).__name__})")
    return created


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("snapshot", "settle"))
    args = parser.parse_args()
    count = capture() if args.mode == "snapshot" else settle()
    print(f"{args.mode}: {count} new rows")

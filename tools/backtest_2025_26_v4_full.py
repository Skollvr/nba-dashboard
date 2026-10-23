"""Six-team full-window 2025/26 ESPN Matchup V4 backtest and minutes audit.

Production formulas in processamento.py are called UNCHANGED. All 30 teams'
regular-season historical ESPN boxscores are fetched once to reconstruct
league-wide defense/position baselines before each target Brazilian calendar day.
Six-team target set follows the earlier core test (OKC/BOS/NYK/DEN/MIN/GSW).

NO future game enters pregame player histories or matchup statistics.
Historical roster proxy = players seen in the team's preceding five games.
Historical injury/depth-chart snapshots are unavailable, so unknown injury
and internally estimated minutes are used. Results are conditional on playing,
do not validate player-availability forecasts or profitability.
"""
from __future__ import annotations
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
import json
import os
import warnings

import numpy as np
import pandas as pd

import api_espn as ae
import espn_context as ec
import processamento as proc
from projection_validation import _project_core, LOG_COLUMNS

warnings.filterwarnings("ignore", message=".*ScriptRunContext.*")

SEASON = "2025-26"
START, END = date(2025, 11, 1), date(2026, 4, 15)
TARGET_TEAMS = {
    "OKC": 1610612760, "BOS": 1610612738, "NYK": 1610612752,
    "DEN": 1610612743, "MIN": 1610612750, "GSW": 1610612744,
}
METRICS = ("PTS", "REB", "AST", "PRA", "3PM", "FGA", "3PA")
CUP_FINAL = "401809839"  # 2025 NBA Cup final excluded from regular season totals
OUT = Path("full_v4_results")
OUT.mkdir(exist_ok=True)
ACTIVE = {"roster": pd.DataFrame(), "logs": pd.DataFrame()}
ALL_SCHEDULES: dict[int, pd.DataFrame] = {}
PREPARSED_DEFENSE: dict[str, pd.DataFrame] = {}
PAYLOADS: dict[str, dict] = {}


def _local_day(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_convert(
        "America/Sao_Paulo"
    ).dt.date


def _fetch_schedule(tid: int) -> tuple[int, pd.DataFrame]:
    df = ae.get_espn_team_schedule(tid, SEASON, "Regular Season")
    if df is None or df.empty:
        raise ValueError("No team schedule")
    df = df.copy()
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce", utc=True)
    df = df[df["GAME_ID"].astype(str).ne(CUP_FINAL)].copy()
    # The cup final may be tagged regular-season in ESPN schedules but it does
    # not count in the 82-game regular-season total.
    if "SEASON_TYPE" in df:
        df = df[df["SEASON_TYPE"].fillna(2).eq(2)].copy()
    return tid, df


def collect_sources() -> tuple[pd.DataFrame, dict, dict]:
    all_ids = sorted(int(v) for v in proc.TEAM_LOOKUP)
    errors = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        jobs = {executor.submit(_fetch_schedule, tid): tid for tid in all_ids}
        for f in as_completed(jobs):
            try:
                tid, df = f.result()
                ALL_SCHEDULES[tid] = df
            except Exception as exc:
                errors.append({"team_id": jobs[f], "error": str(exc)[:250]})
    if len(ALL_SCHEDULES) < 30:
        raise RuntimeError("Incomplete league schedule; do not construct V4 defense: " + repr(errors))

    games = {}
    for tid, sched in ALL_SCHEDULES.items():
        for row in sched.to_dict("records"):
            if not row.get("COMPLETED") or not row.get("BOX_SCORE_AVAILABLE"):
                continue
            gid = str(row["GAME_ID"])
            if not gid or gid == CUP_FINAL:
                continue
            games.setdefault(gid, row)
    print("SOURCE_SCHEDULES", len(ALL_SCHEDULES), "games", len(games), flush=True)
    failures = []
    with ThreadPoolExecutor(max_workers=14) as executor:
        futures = {executor.submit(ae.get_espn_game_summary, gid): gid for gid in games}
        for number, f in enumerate(as_completed(futures), 1):
            gid = futures[f]
            try:
                payload = f.result()
                if not (payload.get("boxscore") or {}).get("players"):
                    raise ValueError("No boxscore players")
                PAYLOADS[gid] = payload
            except Exception as exc:
                failures.append({"game_id": gid, "error": str(exc)[:180]})
            if number % 200 == 0:
                print("FETCHED", number, "of", len(games), "ok", len(PAYLOADS), flush=True)
    print("SOURCE_BOXSCORES", len(PAYLOADS), "/", len(games), flush=True)
    if len(PAYLOADS) < max(1120, int(0.95 * len(games))):
        raise RuntimeError("Insufficient league boxscore coverage")

    player_frames, parser_conflicts = [], []
    for gid, payload in PAYLOADS.items():
        meta = games[gid]
        frame = ec._extract_game_rows(payload, gid, meta["GAME_DATE"])
        if frame is None or frame.empty:
            failures.append({"game_id": gid, "error": "Unparseable defensive boxscore"})
            continue
        frame = frame.drop_duplicates(["PLAYER_ID", "TEAM_ID", "GAME_ID"]).copy()
        PREPARSED_DEFENSE[gid] = frame
        rows = ae._extract_summary_rows(payload, game_meta=meta, season_scope="Regular Season")
        if not rows:
            failures.append({"game_id": gid, "error": "Unparseable player boxscore"})
            continue
        player = pd.DataFrame(rows)
        player = player.drop_duplicates(["PLAYER_ID", "TEAM_ID", "GAME_ID"])
        position = frame[["TEAM_ID", "PLAYER_ID", "POSITION", "POSITION_GROUP"]].drop_duplicates(
            ["TEAM_ID", "PLAYER_ID"]
        )
        player = player.merge(position, on=["TEAM_ID", "PLAYER_ID"], how="left")
        player_frames.append(player)

    if not player_frames:
        raise RuntimeError("No valid ESPN player boxscores")
    logs = pd.concat(player_frames, ignore_index=True)
    logs["GAME_DATE"] = pd.to_datetime(logs["GAME_DATE"], errors="coerce", utc=True)
    logs["PLAYER_ID"] = pd.to_numeric(logs["PLAYER_ID"], errors="coerce")
    logs["TEAM_ID"] = pd.to_numeric(logs["TEAM_ID"], errors="coerce")
    for col in ("MIN", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"):
        logs[col] = pd.to_numeric(logs[col], errors="coerce")
    logs = logs.dropna(subset=["PLAYER_ID", "TEAM_ID", "GAME_DATE", "MIN", "PTS", "REB", "AST"])
    logs = logs[logs["MIN"] > 0].drop_duplicates(["GAME_ID", "PLAYER_ID"]).copy()
    invalid = (
        logs["FG3M"].lt(0) | logs["FG3A"].lt(0) | logs["FGA"].lt(0)
        | logs["FG3M"].gt(logs["FG3A"]) | logs["FG3A"].gt(logs["FGA"])
    )
    logs.loc[invalid, ["FG3M", "FG3A", "FGA"]] = np.nan
    logs["PRA"] = logs["PTS"] + logs["REB"] + logs["AST"]
    logs["DAY"] = _local_day(logs["GAME_DATE"])
    logs = logs.sort_values(["GAME_DATE", "PLAYER_ID"]).reset_index(drop=True)
    print("SOURCE_PLAYERS", len(logs), "unique_players", logs["PLAYER_ID"].nunique(),
          "covered_games", logs["GAME_ID"].nunique(), flush=True)
    coverage = {
        "schedule_teams": len(ALL_SCHEDULES),
        "scheduled_completed_games": len(games),
        "received_boxscores": len(PAYLOADS),
        "parsed_defense_games": len(PREPARSED_DEFENSE),
        "parsed_player_games": int(logs["GAME_ID"].nunique()),
        "player_rows": len(logs), "failures": failures[:80],
        "schedule_failures": errors,
    }
    return logs, games, coverage


def _roster_provider(team_id, season):
    return ACTIVE["roster"].copy()


def _logs_provider(team_id, season, season_scope="Regular Season", as_of_date=None):
    return ACTIVE["logs"].copy()


def _dir_provider():
    return pd.DataFrame({"TEAM_ID": sorted(ALL_SCHEDULES)})


def _schedule_provider(tid, season, season_scope="Regular Season"):
    return ALL_SCHEDULES[int(tid)].copy()


def _summary_provider(gid):
    return PAYLOADS[str(gid)]


def _defense_rows_provider(payload, gid, game_date):
    return PREPARSED_DEFENSE.get(str(gid), pd.DataFrame()).copy()


def patch_read_only_data_providers():
    # Patches affect ONLY this standalone test process; untouched core math
    # functions from production are invoked by build_team_table/enrich.
    proc.get_espn_team_roster = _roster_provider
    proc.get_espn_roster_player_logs = _logs_provider
    ec.get_espn_team_directory = _dir_provider
    ec.get_espn_team_schedule = _schedule_provider
    ec.get_espn_game_summary = _summary_provider
    ec._extract_game_rows = _defense_rows_provider


def _historic_roster(tid: int, day: date, by_team: dict[int, pd.DataFrame]) -> pd.DataFrame:
    past = by_team[tid]
    past = past[past["DAY"] < day]
    gids = (past[["GAME_ID", "GAME_DATE"]].drop_duplicates("GAME_ID")
            .sort_values("GAME_DATE", ascending=False)
            .head(5)["GAME_ID"].tolist())
    recent = past[past["GAME_ID"].isin(gids)]
    # Only athletes who were known to be on the team before the target date.
    last = recent.sort_values("GAME_DATE", ascending=False).drop_duplicates("PLAYER_ID")
    return pd.DataFrame({
        "PLAYER": last["PLAYER_NAME"].astype(str).values,
        "PLAYER_ID": last["PLAYER_ID"].astype(int).values,
        "POSITION": last["POSITION"].fillna("").astype(str).values,
        "TEAM_ID": int(tid),
    })


def _pregame_player_histories(roster: pd.DataFrame, day: date,
                              by_player: dict[int, pd.DataFrame], tid: int) -> pd.DataFrame:
    history = []
    for pid in roster["PLAYER_ID"]:
        past = by_player.get(int(pid))
        if past is None:
            continue
        part = past[past["DAY"] < day].copy()
        if not part.empty:
            # Native ESPN provider assigns selected team to entire same-season
            # historical gamelog so transfer histories are retained.
            part["TEAM_ID"] = tid
            history.append(part)
    return pd.concat(history, ignore_index=True) if history else pd.DataFrame()


def _metric_actual(row: pd.Series, metric: str):
    if metric == "PRA":
        return float(row["PTS"]) + float(row["REB"]) + float(row["AST"])
    return pd.to_numeric(row.get(LOG_COLUMNS[metric]), errors="coerce")


def _summarize(frame: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    ans = frame.groupby(groups, dropna=False).agg(
        n=("ERR_V4", "size"), players=("PLAYER_ID", "nunique"),
        mae_v4=("ERR_V4", "mean"), mae_core=("ERR_CORE", "mean"),
        mae_l10=("ERR_L10", "mean"), bias_v4=("BIAS_V4", "mean"),
        matchup_abs_shift=("SHIFT_V4_CORE", lambda x: x.abs().mean()),
        min_mae=("MIN_ERR_ABS", "mean"),
    ).reset_index()
    ans["gain_v4_vs_core"] = ans["mae_core"] - ans["mae_v4"]
    return ans.round(4)


def _bootstrap_by_player(details: pd.DataFrame):
    rng = np.random.default_rng(2026)
    output = []
    for metric, m in details.groupby("METRIC"):
        gains = m.assign(GAIN=m["ERR_CORE"]-m["ERR_V4"]).groupby("PLAYER_ID")["GAIN"].agg(
            ["sum", "count"]
        )
        sums = gains["sum"].to_numpy(dtype=float)
        counts = gains["count"].to_numpy(dtype=float)
        choices = rng.integers(0, len(sums), size=(1200, len(sums)))
        diff = sums[choices].sum(axis=1) / counts[choices].sum(axis=1)
        output.append({"metric": metric, "players": len(sums),
                       "gain": float(sums.sum() / counts.sum()),
                       "ci95_low": float(np.quantile(diff, .025)),
                       "ci95_high": float(np.quantile(diff, .975))})
    return pd.DataFrame(output).round(4)


def main() -> None:
    logs, games, source_coverage = collect_sources()
    patch_read_only_data_providers()
    by_player = {int(k): df for k, df in logs.groupby("PLAYER_ID")}
    by_team = {int(k): df for k, df in logs.groupby("TEAM_ID")}
    by_game_team = {
        (str(gid), int(tid)): part
        for (gid, tid), part in logs.groupby(["GAME_ID", "TEAM_ID"])
    }
    target = []
    for tid in TARGET_TEAMS.values():
        sched = ALL_SCHEDULES[tid].copy()
        sched["DAY"] = _local_day(sched["GAME_DATE"])
        sched = sched[(sched["DAY"] >= START) & (sched["DAY"] <= END)]
        sched = sched[sched["COMPLETED"].fillna(False) &
                      sched["BOX_SCORE_AVAILABLE"].fillna(False)]
        for row in sched.to_dict("records"):
            if str(row["GAME_ID"]) in PAYLOADS:
                target.append(row)
    target.sort(key=lambda x: (x["GAME_DATE"], x["TEAM_ID"]))
    print("TARGET_GAMES", len(target), "first", str(target[0]["GAME_DATE"]) if target else "",
          "last", str(target[-1]["GAME_DATE"]) if target else "", flush=True)
    if len(target) < 400:
        raise RuntimeError("Not enough six-team games for full-season evaluation")

    metric_records, minute_records, game_coverage = [], [], []
    errors = []
    for index, game in enumerate(target, 1):
        day = _local_day(pd.Series([game["GAME_DATE"]])).iloc[0]
        tid = int(game["TEAM_ID"])
        gid = str(game["GAME_ID"])
        opp_raw = pd.to_numeric(game.get("OPPONENT_TEAM_ID"), errors="coerce")
        if not np.isfinite(opp_raw):
            errors.append({"game_id": gid, "error": "Missing opponent id"})
            continue
        opponent = int(opp_raw)
        roster = _historic_roster(tid, day, by_team)
        if roster.empty:
            continue
        histories = _pregame_player_histories(roster, day, by_player, tid)
        if histories.empty:
            continue
        assert bool((histories["DAY"] < day).all()), "Historical player leakage!"
        ACTIVE["roster"], ACTIVE["logs"] = roster, histories
        try:
            base = proc.build_team_table(tid, SEASON, as_of_date=day.isoformat())
            base["INJ_STATUS"] = "Unknown"
            base["IS_UNAVAILABLE"] = False
            model = proc.enrich_team_with_context(
                base, tid, opponent, proc.TEAM_ABBR_LOOKUP.get(opponent, "Opponent"),
                SEASON, "Regular Season", as_of_date=day.isoformat()
            )
        except Exception as exc:
            errors.append({"game_id": gid, "day": str(day),
                           "error": repr(exc)[:280]})
            if len(errors) < 12:
                print("GAME_ERROR", gid, type(exc).__name__, str(exc)[:160], flush=True)
            continue

        actual = by_game_team.get((gid, tid), pd.DataFrame())
        actual_by_id = {int(row.PLAYER_ID): row for row in actual.itertuples(index=False)}
        participants = 0
        evals = 0
        for model_row in model.to_dict("records"):
            pid = int(model_row["PLAYER_ID"])
            actual_row = actual_by_id.get(pid)
            if actual_row is None:
                continue
            past = by_player.get(pid)
            if past is None:
                continue
            past = past[past["DAY"] < day].sort_values("GAME_DATE")
            if len(past) < 10:
                continue
            forecast = _project_core(past)
            mins = pd.to_numeric(model_row.get("PROJ_MIN_V1"), errors="coerce")
            actual_min = float(actual_row.MIN)
            if not np.isfinite(mins):
                continue
            participants += 1
            min_error = float(mins) - actual_min
            gp_prev = len(past)
            if gp_prev < 5:
                gp_class = "<5"
            elif gp_prev < 10:
                gp_class = "5–9"
            elif gp_prev < 20:
                gp_class = "10–19"
            else:
                gp_class = "20+"
            min_band = ("<15" if mins < 15 else "15–25" if mins < 25
                        else "25–35" if mins < 35 else "35+")
            mrow = {
                "GAME_ID": gid, "DATE": str(day), "MONTH": str(day)[:7],
                "TEAM": proc.TEAM_ABBR_LOOKUP.get(tid, str(tid)),
                "PLAYER_ID": pid, "PLAYER": str(model_row.get("PLAYER", "")),
                "POSITION": str(model_row.get("POSITION_GROUP", "")),
                "PREV_GP": gp_prev, "GP_CLASS": gp_class,
                "MIN_BAND": min_band, "MIN_PRED": float(mins),
                "MIN_REAL": actual_min, "MIN_ERROR": min_error,
                "MIN_ERR_ABS": abs(min_error),
                "MIN_SEASON": float(pd.to_numeric(model_row.get("SEASON_MIN"), errors="coerce")),
                "MIN_L10_MEAN": float(pd.to_numeric(model_row.get("L10_MIN"), errors="coerce")),
                "MIN_L10_MED": float(pd.to_numeric(model_row.get("L10_MIN_MED"), errors="coerce")),
                "MIN_L5_MED": float(pd.to_numeric(model_row.get("L5_MIN_MED"), errors="coerce")),
            }
            minute_records.append(mrow)
            last10 = past.tail(10)
            for metric in METRICS:
                if metric not in forecast:
                    continue
                col = "PRA" if metric == "PRA" else LOG_COLUMNS[metric]
                actual_value = _metric_actual(pd.Series(actual_row._asdict()), metric)
                v4 = pd.to_numeric(model_row.get("PROJ_" + metric + "_V1"), errors="coerce")
                if not np.isfinite(v4) or not np.isfinite(actual_value):
                    continue
                l10 = float(last10[col].mean())
                if not np.isfinite(l10):
                    continue
                core = float(forecast[metric])
                record = dict(mrow)
                record.update({
                    "METRIC": metric, "REAL": float(actual_value),
                    "V4": float(v4), "CORE": core, "L10": l10,
                    "ERR_V4": abs(float(v4)-float(actual_value)),
                    "ERR_CORE": abs(core-float(actual_value)),
                    "ERR_L10": abs(l10-float(actual_value)),
                    "BIAS_V4": float(v4)-float(actual_value),
                    "SHIFT_V4_CORE": float(v4)-core,
                    "MATCHUP_EFFECT": float(pd.to_numeric(
                        model_row.get("MATCHUP_EFFECT_PCT_" + metric + "_V2"), errors="coerce"
                    )),
                    "OPP_DEF_GP": float(pd.to_numeric(model_row.get("OPP_DEF_SAMPLE_GP"), errors="coerce")),
                    "DEF_TEAM_COUNT": float(pd.to_numeric(model_row.get("TEAM_DEF_TEAM_COUNT"), errors="coerce")),
                })
                # Oracle minutes is strictly a POSTGAME error-attribution
                # counterfactual, NOT a legitimate pregame forecast.
                if metric in ("PTS", "REB", "AST", "PRA") and mins > 0:
                    ratio = float(pd.to_numeric(
                        model_row.get("BASE_" + metric + "_V1"), errors="coerce"
                    )) / float(mins)
                    oracle = (ratio * actual_min *
                              (1.0 + record["MATCHUP_EFFECT"]))
                    record["ORACLE_MIN_ERR_ABS"] = abs(oracle - float(actual_value))
                else:
                    record["ORACLE_MIN_ERR_ABS"] = np.nan
                metric_records.append(record)
                evals += 1
        game_coverage.append({
            "game_id": gid, "date": str(day), "team_id": tid,
            "actual_players": len(actual), "historical_roster": len(roster),
            "eval_players": participants, "metric_rows": evals,
        })
        if index % 25 == 0 or index == len(target):
            print("EVALUATED", index, "/", len(target), "metric_rows", len(metric_records),
                  "min_rows", len(minute_records), "errors", len(errors), flush=True)

    metrics = pd.DataFrame(metric_records)
    minutes = pd.DataFrame(minute_records)
    if metrics.empty or len(metrics) < 15000:
        raise RuntimeError("Insufficient metric comparisons for full backtest " + str(len(metrics)))
    if (pd.to_numeric(metrics["OPP_DEF_GP"], errors="coerce").fillna(0).gt(0).mean() < .95
        or pd.to_numeric(metrics["DEF_TEAM_COUNT"], errors="coerce").fillna(0).lt(28).mean() > .10):
        raise RuntimeError("Missing defensively valid league pregame samples")

    metrics.to_csv(OUT / "predictions_game_by_game.csv", index=False)
    minutes.to_csv(OUT / "minutes_game_by_game.csv", index=False)
    pd.DataFrame(game_coverage).to_csv(OUT / "game_coverage.csv", index=False)
    _summarize(metrics, ["METRIC"]).to_csv(OUT / "metrics_overall.csv", index=False)
    _summarize(metrics, ["MONTH", "METRIC"]).to_csv(OUT / "metrics_monthly.csv", index=False)
    _summarize(metrics, ["TEAM", "METRIC"]).to_csv(OUT / "metrics_by_team.csv", index=False)
    _summarize(metrics, ["MIN_BAND", "METRIC"]).to_csv(OUT / "metrics_by_minute_band.csv", index=False)
    _bootstrap_by_player(metrics).to_csv(OUT / "v4_vs_core_player_cluster_ci.csv", index=False)

    def minutes_summary(cols):
        output = minutes.groupby(cols, dropna=False).agg(
            n=("MIN_ERROR", "size"), players=("PLAYER_ID", "nunique"),
            mae_min=("MIN_ERR_ABS", "mean"),
            signed_bias=("MIN_ERROR", "mean"),
            actual_avg=("MIN_REAL", "mean"),
            predicted_avg=("MIN_PRED", "mean"),
            err_gt_5=("MIN_ERR_ABS", lambda x: (x > 5).mean()),
            err_gt_10=("MIN_ERR_ABS", lambda x: (x > 10).mean()),
            l10_mean_mae=("MIN_REAL", lambda x: np.nan),
        ).reset_index()
        return output.drop(columns=["l10_mean_mae"]).round(4)

    minutes_summary(["MIN_BAND"]).to_csv(OUT / "minutes_by_projected_band.csv", index=False)
    minutes_summary(["MONTH"]).to_csv(OUT / "minutes_monthly.csv", index=False)
    minutes_summary(["TEAM"]).to_csv(OUT / "minutes_by_team.csv", index=False)
    minutes_summary(["POSITION"]).to_csv(OUT / "minutes_by_position.csv", index=False)
    minutes_summary(["GP_CLASS"]).to_csv(OUT / "minutes_by_history_size.csv", index=False)
    oracle = metrics[metrics["METRIC"].isin(["PTS", "REB", "AST", "PRA"])].groupby("METRIC").agg(
        n=("ERR_V4", "size"), mae_v4=("ERR_V4", "mean"),
        mae_hindsight_minutes=("ORACLE_MIN_ERR_ABS", "mean")
    ).reset_index().round(4)
    oracle.to_csv(OUT / "minutes_hindsight_sensitivity_not_pregame.csv", index=False)

    overall = _summarize(metrics, ["METRIC"])
    print("FINAL_FULL_V4_VS_CORE", overall.to_string(index=False), flush=True)
    print("FINAL_MINUTES", minutes_summary(["MIN_BAND"]).to_string(index=False), flush=True)
    print("FINAL_MINUTES_ORACLE_DIAGNOSTIC", oracle.to_string(index=False), flush=True)
    print("FINAL_PLAYER_BOOTSTRAP", _bootstrap_by_player(metrics).to_string(index=False), flush=True)

    manifest = {
        "season": SEASON, "window": [str(START), str(END)],
        "teams_evaluated": list(TARGET_TEAMS),
        "teams_used_for_defensive_context": len(ALL_SCHEDULES),
        "source_coverage": source_coverage,
        "target_team_games": len(target), "replayed": len(game_coverage),
        "player_game_minute_observations": len(minutes),
        "metric_comparisons": len(metrics), "unique_players": int(metrics["PLAYER_ID"].nunique()),
        "production_math_modified": False,
        "source": "ESPN complete-season team schedules and individual game summary boxscores",
        "cutoff": "00:00 Sao Paulo time on target game's local date, exclusive",
        "league_defense": "Original espn_context.build_espn_defense_context, trailing 20 team games before date",
        "v4": "Original processamento.build_team_table and enrich_team_with_context",
        "core": "Original projection_validation._project_core",
        "historical_roster": "Players from preceding 5 games for each team, not future roster",
        "min_history_games": 10,
        "injuries": "No archived snapshots; default unknown; minutes internal fallback only",
        "availability": "Evaluation conditional on player having played; cannot measure DNP forecast",
        "minutes_hindsight": "Diagnostic only: uses actual postgame minutes to attribute projection error, not predictive",
        "source_errors": errors[:200],
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


if __name__ == "__main__":
    main()

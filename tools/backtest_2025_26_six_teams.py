"""Read-only, historical ESPN validation of the dashboard's projection core.

Cohort: historical boxscore rosters for six NBA teams (not current rosters).
Historical period: 2025/26 regular season, Nov 2025 to Apr 2026.
Only games STRICTLY before each target game enter its prediction.
Does NOT reproduce historical injury reports, market odds or live Matchup V4.
"""
from __future__ import annotations

import json
from pathlib import Path
import warnings

import numpy as np
import pandas as pd

from api_espn import get_espn_team_player_logs
from projection_validation import _project_core, LOG_COLUMNS

warnings.filterwarnings("ignore", message=".*ScriptRunContext.*")

SEASON = "2025-26"
START = pd.Timestamp("2025-11-01").date()
END = pd.Timestamp("2026-04-15").date()
TEAMS = {
    "OKC": 1610612760,
    "BOS": 1610612738,
    "NYK": 1610612752,
    "DEN": 1610612743,
    "MIN": 1610612750,
    "GSW": 1610612744,
}
METRICS = ("PTS", "REB", "AST", "PRA", "3PM", "FGA", "3PA")
OUT = Path("backtest_results")
OUT.mkdir(exist_ok=True)


def summarize(df: pd.DataFrame, group: list[str]) -> pd.DataFrame:
    def per_group(g: pd.DataFrame) -> pd.Series:
        return pd.Series({
            "previsoes": len(g),
            "jogadores": g["PLAYER_ID"].nunique(),
            "mae_modelo": round(float(g["ERRO_ABS"].mean()), 3),
            "mae_L10": round(float(g["ERRO_L10"].mean()), 3),
            "ganho_mae": round(float(g["ERRO_L10"].mean() - g["ERRO_ABS"].mean()), 3),
            "vies_proj_menos_real": round(float(g["PROJETADO"].sub(g["REAL"]).mean()), 3),
        })
    if df.empty:
        return pd.DataFrame()
    return df.groupby(group, dropna=False).apply(per_group, include_groups=False).reset_index()


def fetch_historical_boxscores() -> tuple[pd.DataFrame, list[dict]]:
    blocks, coverage = [], []
    for abbr, team_id in TEAMS.items():
        try:
            df = get_espn_team_player_logs(
                team_id,
                SEASON,
                season_scope="Regular Season",
                max_games=None,
            )
            if df is None or df.empty:
                raise RuntimeError("ESPN returned no valid boxscore player rows")
            df = df.copy()
            df["HIST_TEAM"] = abbr
            blocks.append(df)
            row = {
                "team": abbr,
                "players": int(df["PLAYER_ID"].nunique()),
                "rows": len(df),
                "games": int(df["GAME_ID"].nunique()),
                "status": "ok",
            }
        except Exception as exc:
            row = {
                "team": abbr, "players": 0, "rows": 0, "games": 0,
                "status": type(exc).__name__ + ": " + str(exc)[:140],
            }
        coverage.append(row)
        print("ESPN COVERAGE:", json.dumps(row, ensure_ascii=False), flush=True)

    if not blocks:
        raise RuntimeError("No historical ESPN data received; no validity claims can be made.")

    all_games = pd.concat(blocks, ignore_index=True)
    all_games["GAME_DATE"] = pd.to_datetime(
        all_games["GAME_DATE"], errors="coerce", utc=True
    )
    all_games = all_games.dropna(subset=["PLAYER_ID", "GAME_DATE", "GAME_ID"])
    all_games = all_games[all_games["GAME_ID"].astype(str).str.strip().ne("")]
    all_games = all_games.drop_duplicates(["PLAYER_ID", "GAME_ID"]).copy()
    for metric in ("MIN", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"):
        all_games[metric] = pd.to_numeric(all_games[metric], errors="coerce")
    all_games = all_games.dropna(subset=["MIN", "PTS", "REB", "AST"])
    all_games = all_games[all_games["MIN"] > 0]

    # Never turn invalid/missing shooting attempts into real zeros.
    missing_or_bad_fga = (
        all_games["FGA"].lt(0)
        | all_games["FG3A"].lt(0)
        | all_games["FG3A"].gt(all_games["FGA"])
    )
    all_games.loc[missing_or_bad_fga, ["FGA", "FG3A", "FG3M"]] = np.nan
    all_games["LOCAL_DATE"] = (
        all_games["GAME_DATE"].dt.tz_convert("America/Sao_Paulo").dt.date
    )
    all_games.sort_values(["PLAYER_ID", "GAME_DATE"], inplace=True)
    return all_games, coverage


def evaluate(logs: pd.DataFrame) -> pd.DataFrame:
    evaluations = []
    for player_id, player_logs in logs.groupby("PLAYER_ID"):
        player_logs = player_logs.sort_values("GAME_DATE")
        for _, target in player_logs.iterrows():
            day = target["LOCAL_DATE"]
            if day < START or day > END:
                continue
            prior = player_logs[player_logs["GAME_DATE"] < target["GAME_DATE"]]
            if len(prior) < 10:
                continue
            if pd.to_numeric(prior["MIN"], errors="coerce").mean() <= 0:
                continue
            forecast = _project_core(prior)
            last10 = prior.tail(10)
            for metric in METRICS:
                if metric not in forecast:
                    continue
                if metric == "PRA":
                    actual = sum(float(target[c]) for c in ("PTS", "REB", "AST"))
                    baseline = sum(float(last10[c].mean()) for c in ("PTS", "REB", "AST"))
                else:
                    col = LOG_COLUMNS[metric]
                    if pd.isna(target[col]) or last10[col].notna().sum() < 8:
                        continue
                    actual = float(target[col])
                    baseline = float(last10[col].mean())
                predicted = float(forecast[metric])
                if not all(np.isfinite(v) for v in (actual, baseline, predicted)):
                    continue
                evaluations.append({
                    "PLAYER_ID": int(player_id),
                    "DATA": str(day),
                    "MES": str(day)[:7],
                    "TIME_HISTORICO": target["HIST_TEAM"],
                    "GAME_ID": str(target["GAME_ID"]),
                    "METRICA": metric,
                    "GP_ANTERIORES": int(len(prior)),
                    "PROJETADO": round(predicted, 4),
                    "REAL": actual,
                    "MEDIA_L10": round(baseline, 4),
                    "ERRO_ABS": abs(predicted - actual),
                    "ERRO_L10": abs(baseline - actual),
                })
    return pd.DataFrame(evaluations)


def main() -> None:
    logs, coverage = fetch_historical_boxscores()
    print(
        "HISTORICAL COUNTS",
        "rows:", len(logs),
        "games:", logs["GAME_ID"].nunique(),
        "players:", logs["PLAYER_ID"].nunique(),
        flush=True,
    )
    details = evaluate(logs)
    if details.empty:
        print("NO EVALUATIONS: diagnostics and partial source coverage are available.")
    else:
        details.to_csv(OUT / "game_by_game.csv", index=False)
        overall = summarize(details, ["METRICA"])
        monthly = summarize(details, ["MES", "METRICA"])
        teams = summarize(details, ["TIME_HISTORICO", "METRICA"])
        overall.to_csv(OUT / "metrics.csv", index=False)
        monthly.to_csv(OUT / "metrics_by_month.csv", index=False)
        teams.to_csv(OUT / "metrics_by_team.csv", index=False)
        print("\nOVERALL CORE BACKTEST (NOT FULL V4):\n", overall.to_string(index=False), flush=True)
        print("\nPTS BY MONTH:\n", monthly[monthly["METRICA"].eq("PTS")].to_string(index=False), flush=True)

    manifest = {
        "source": "ESPN historical regular-season game summaries",
        "season": SEASON, "window": [str(START), str(END)],
        "selection": list(TEAMS), "coverage": coverage,
        "boxscore_rows": len(logs),
        "games": int(logs["GAME_ID"].nunique()),
        "players": int(logs["PLAYER_ID"].nunique()),
        "predictions": int(len(details)),
        "evaluates_live_matchup_v4": False,
        "evaluates_historical_injuries": False,
        "evaluates_historical_bookmaker_quotes": False,
        "leakage_protection": "Strict earlier game date per player; no present game or later data",
        "comparison": "Own model core vs naive last-ten-games average",
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if len([x for x in coverage if x["status"] == "ok"]) < 4 or len(details) < 100:
        raise RuntimeError(
            "Insufficient confirmed historical ESPN coverage. "
            "Do not interpret any partial numbers as validated performance."
        )


if __name__ == "__main__":
    main()

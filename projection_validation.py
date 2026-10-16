"""Leakage-safe retrospective validation of the model's minutes + recent-form core.

No historic/current roster, opponent matchup, injury, or market odds are used.
This is not a full backtest of the live V1 matchup model.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from processamento import blend_rate, project_minutes_v1


METRICS = ("PTS", "REB", "AST", "PRA", "3PM", "FGA", "3PA")
LOG_COLUMNS = {
    "PTS": "PTS",
    "REB": "REB",
    "AST": "AST",
    "3PM": "FG3M",
    "FGA": "FGA",
    "3PA": "FG3A",
}
REPORT_COLUMNS = [
    "Métrica", "Jogos avaliados", "Jogadores", "MAE modelo",
    "MAE média L10", "Viés (proj − real)", "Ganho MAE vs L10",
]


def _project_core(history: pd.DataFrame) -> dict[str, float]:
    """Project the next game using strictly prior games and neutral matchup."""
    l10 = history.tail(10)
    l5 = history.tail(5)
    season_min = float(history["MIN"].mean())
    proj_min = project_minutes_v1(
        season_min,
        float(l10["MIN"].median()),
        float(l5["MIN"].median()),
    )
    results: dict[str, float] = {}
    for metric in ("PTS", "REB", "AST", "3PM", "FGA", "3PA"):
        column = LOG_COLUMNS[metric]
        if column not in history:
            continue
        if history[column].notna().mean() < 0.8:
            continue
        mean_season = float(history[column].mean())
        mean_l10 = float(l10[column].mean())
        mean_l5 = float(l5[column].mean())
        mean_min_l10 = float(l10["MIN"].mean())
        mean_min_l5 = float(l5["MIN"].mean())
        rate = blend_rate(
            mean_season / season_min if season_min > 0 else 0.0,
            mean_l10 / mean_min_l10 if mean_min_l10 > 0 else 0.0,
            mean_l5 / mean_min_l5 if mean_min_l5 > 0 else 0.0,
        )
        results[metric] = max(0.0, proj_min * rate)
    if all(key in results for key in ("PTS", "REB", "AST")):
        results["PRA"] = results["PTS"] + results["REB"] + results["AST"]
    return results


def rolling_projection_backtest(
    logs: pd.DataFrame,
    min_history: int = 10,
) -> pd.DataFrame:
    """Backtest one-step-ahead predictions with no target/future game in history.

    For every target match, only previous timestamps for the SAME player enter
    season, L10/L5 and minutes estimates. Current roster membership selects
    whose logs are tested; it is NOT a historical-roster backtest.
    """
    if logs is None or logs.empty:
        return pd.DataFrame(columns=REPORT_COLUMNS)

    required = {"PLAYER_ID", "GAME_DATE", "MIN", "PTS", "REB", "AST"}
    if not required.issubset(logs.columns):
        return pd.DataFrame(columns=REPORT_COLUMNS)

    work = logs.copy()
    work["GAME_DATE"] = pd.to_datetime(
        work["GAME_DATE"], errors="coerce", utc=True
    )
    for column in ["MIN", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]:
        if column in work:
            work[column] = pd.to_numeric(work[column], errors="coerce")

    work = work.dropna(subset=["PLAYER_ID", "GAME_DATE", "MIN"])
    if "GAME_ID" in work:
        work = work.drop_duplicates(subset=["PLAYER_ID", "GAME_ID"], keep="first")
    work = work.sort_values(["PLAYER_ID", "GAME_DATE"])
    min_history = max(int(min_history), 5)

    evaluations: list[dict] = []
    for player_id, group in work.groupby("PLAYER_ID"):
        group = group.reset_index(drop=True)
        for i in range(min_history, len(group)):
            current = group.iloc[i]
            # Strictly earlier, never including the target or same-time games.
            prior = group[
                group["GAME_DATE"] < current["GAME_DATE"]
            ].copy()
            if len(prior) < min_history:
                continue
            if float(prior["MIN"].mean()) <= 0:
                continue

            predicted = _project_core(prior)
            l10 = prior.tail(10)
            for metric, prediction in predicted.items():
                if metric == "PRA":
                    actual = sum(float(current[v]) for v in ("PTS", "REB", "AST"))
                    naive = sum(float(l10[v].mean()) for v in ("PTS", "REB", "AST"))
                else:
                    col = LOG_COLUMNS[metric]
                    if col not in current or pd.isna(current[col]):
                        continue
                    actual = float(current[col])
                    naive = float(l10[col].mean())

                if not all(np.isfinite(value) for value in (prediction, actual, naive)):
                    continue
                evaluations.append({
                    "Métrica": metric,
                    "Jogador": player_id,
                    "Erro absoluto": abs(prediction - actual),
                    "Erro L10": abs(naive - actual),
                    "Viés": prediction - actual,
                })

    if not evaluations:
        return pd.DataFrame(columns=REPORT_COLUMNS)

    samples = pd.DataFrame(evaluations)
    summaries = []
    for metric in METRICS:
        part = samples[samples["Métrica"] == metric]
        if part.empty:
            continue
        mae = float(part["Erro absoluto"].mean())
        naive_mae = float(part["Erro L10"].mean())
        summaries.append({
            "Métrica": metric,
            "Jogos avaliados": int(len(part)),
            "Jogadores": int(part["Jogador"].nunique()),
            "MAE modelo": round(mae, 2),
            "MAE média L10": round(naive_mae, 2),
            "Viés (proj − real)": round(float(part["Viés"].mean()), 2),
            "Ganho MAE vs L10": round(naive_mae - mae, 2),
        })
    return pd.DataFrame(summaries, columns=REPORT_COLUMNS)

"""Forward-only quality metrics using immutable pregame snapshots only.

No retrospective fabrication of inputs, model weights, bookmaker lines or odds.
Historical snapshots lacking new V4.1 fields remain valid for original V4.
"""
from __future__ import annotations

import math
import pandas as pd
from model_tracking import projection_target


def _numeric(series):
    return pd.to_numeric(series, errors="coerce").replace([math.inf, -math.inf], math.nan)


def coverage(snapshots, results):
    snapshots = list(snapshots)
    results = list(results)
    known_keys = {str(x.get("key")) for x in snapshots}
    settled = {str(x.get("key")) for x in results if str(x.get("key")) in known_keys}
    games = {str(x.get("game_id")) for x in snapshots if x.get("game_id")}
    lines = [x for x in snapshots if (
        isinstance(x.get("line"), (int,float)) and math.isfinite(float(x.get("line")))
        and x.get("line") > 0 and x.get("line_source") in {"BetMGM", "FanDuel"}
    )]
    shadow = [x for x in snapshots if x.get("projection_v41") is not None]
    # Each result file repeats a player's minutes for the seven metrics.
    # Report distinct players, not seven copies of the same minutes.
    minutes = {
        str(x.get("key", "")).rsplit(":", 1)[0]
        for x in results
        if x.get("actual_minutes") is not None
    }
    return {
        "snapshots": len(snapshots),
        "games": len(games),
        "results": len(settled),
        "pending": len(known_keys - settled),
        "market_quoted": len(lines),
        "shadow_forecasts": len(shadow),
        "actual_minutes": len(minutes),
        "market_coverage": len(lines) / len(snapshots) if snapshots else 0.0,
        "settlement_coverage": len(settled) / len(known_keys) if known_keys else 0.0,
    }


def performance_buckets(frame, metric, bins=None):
    """Absolute forecast accuracy per forecast band; no gambling inference."""
    cols = ["Faixa", "N", "Previsto", "Real", "MAE", "Viés", "Metas"]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=cols)
    work = frame.copy()
    if "metric" in work:
        work = work[work["metric"] == metric].copy()
    if work.empty or not {"projection", "actual"}.issubset(work):
        return pd.DataFrame(columns=cols)
    work["projection"] = _numeric(work["projection"])
    work["actual"] = _numeric(work["actual"])
    work = work.dropna(subset=["projection", "actual"]).copy()
    if work.empty:
        return pd.DataFrame(columns=cols)
    edges = bins or ([0, 1, 2, 3, 4, 5, 7, 10, 1000]
                     if metric in {"AST", "3PM", "REB"} else
                     [0, 1, 2, 3, 4, 5, 6, 8, 10, 1000]
                     if metric == "3PA" else [0, 5, 10, 15, 20, 25, 30, 1000])
    work["band"] = pd.cut(work["projection"], bins=edges, right=False)
    work["error"] = work["projection"] - work["actual"]
    work["absolute"] = work["error"].abs()
    work["achieved"] = work.apply(
        lambda row: row["actual"] >= projection_target(row["projection"]), axis=1
    )
    grouped = work.groupby("band", observed=True)
    rows = []
    for name, part in grouped:
        rows.append({
            "Faixa": f"{name.left:g}–{name.right:g}" if name.right < 1000 else f"{name.left:g}+",
            "N": len(part),
            "Previsto": round(part["projection"].mean(), 2),
            "Real": round(part["actual"].mean(), 2),
            "MAE": round(part["absolute"].mean(), 2),
            "Viés": round(part["error"].mean(), 2),
            "Metas": round(part["achieved"].mean() * 100, 1),
        })
    return pd.DataFrame(rows, columns=cols)


def model_comparison(frame):
    """Paired MAE V4/V4.1 on the same future captured, settled observations."""
    columns = ["Métrica", "N", "MAE V4", "MAE V4.1", "Diferença MAE", "Viés V4", "Viés V4.1"]
    if frame is None or frame.empty or not {"actual", "projection", "projection_v41", "metric"}.issubset(frame):
        return pd.DataFrame(columns=columns)
    work = frame.copy()
    for col in ("actual", "projection", "projection_v41"):
        work[col] = _numeric(work[col])
    work = work.dropna(subset=["actual", "projection", "projection_v41"])
    rows = []
    for metric, part in work.groupby("metric"):
        a = part["projection"] - part["actual"]
        b = part["projection_v41"] - part["actual"]
        rows.append({
            "Métrica": metric, "N": len(part),
            "MAE V4": round(a.abs().mean(), 3),
            "MAE V4.1": round(b.abs().mean(), 3),
            "Diferença MAE": round(a.abs().mean() - b.abs().mean(), 3),
            "Viés V4": round(a.mean(), 3), "Viés V4.1": round(b.mean(), 3),
        })
    return pd.DataFrame(rows, columns=columns).sort_values("Métrica")


def minutes_comparison(frame):
    cols = ["N", "Erro médio minutos", "Viés minutos", "MAE minutos"]
    if frame is None or frame.empty or not {"minutes", "actual_minutes"}.issubset(frame):
        return None
    work = frame.copy()
    work["minutes"] = _numeric(work["minutes"])
    work["actual_minutes"] = _numeric(work["actual_minutes"])
    if "game_id" in work and "player_id" in work:
        work = work.drop_duplicates(["game_id", "player_id"])
    work = work.dropna(subset=["minutes", "actual_minutes"])
    if work.empty:
        return None
    diff = work["minutes"] - work["actual_minutes"]
    return {"N": len(work), "Erro médio minutos": round(diff.mean(), 2),
            "Viés minutos": round(diff.mean(), 2),
            "MAE minutos": round(diff.abs().mean(), 2)}


def market_performance(frame):
    """Settled Over/Under and theoretical unit-stake yield on captured odds.

    This is NOT a historical trading record, doesn't assume bets were made,
    and explicitly excludes absent quotes, pushes and equal-edge predictions.
    """
    result = {"n": 0, "correct": 0, "rate": None, "yield_n": 0, "yield_unit": None}
    needed = {"line", "line_source", "actual", "projection"}
    if frame is None or frame.empty or not needed.issubset(frame):
        return result
    work = frame.copy()
    for col in ["line", "actual", "projection"]:
        work[col] = _numeric(work[col])
    work = work.dropna(subset=["line", "actual", "projection"])
    work = work[(work["line"] > 0) & work["line_source"].isin(["BetMGM", "FanDuel"])]
    work = work[(work["actual"] != work["line"]) & (work["projection"] != work["line"])]
    if work.empty:
        return result
    success = (work["actual"] > work["line"]) == (work["projection"] > work["line"])
    result.update({"n": len(work), "correct": int(success.sum()),
                   "rate": float(success.mean())})
    if {"over_decimal", "under_decimal"}.issubset(work):
        over = _numeric(work["over_decimal"])
        under = _numeric(work["under_decimal"])
        odds = over.where(work["projection"] > work["line"], under)
        ok = odds.notna() & odds.gt(1)
        if ok.any():
            stake_profit = (odds.loc[ok] - 1).where(success.loc[ok], -1.0)
            result["yield_n"] = int(ok.sum())
            result["yield_unit"] = float(stake_profit.mean())
    return result

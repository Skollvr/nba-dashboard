"""Optional minute override applied AFTER original V4 calculations."""
import numpy as np
import pandas as pd

METRICS = ("PTS", "REB", "AST", "PRA", "3PM", "FGA", "3PA", "2PA")


def apply_manual_minutes(frame: pd.DataFrame, overrides: dict) -> pd.DataFrame:
    """Use freshly computed model rows; do not mutate source or V4 weights."""
    out = frame.copy()
    if out.empty:
        return out
    out["PROJ_MIN_AUTO"] = out["PROJ_MIN_V1"].copy()
    out["MINUTES_SOURCE_AUTO"] = out.get("MINUTES_SOURCE", "Modelo interno")
    out["MANUAL_MINUTES_ACTIVE"] = False
    for metric in METRICS:
        col = f"PROJ_{metric}_V1"
        if col in out:
            out[col + "_AUTO"] = out[col].copy()
    for i, row in out.iterrows():
        try:
            pid = int(row["PLAYER_ID"])
            chosen = float(overrides[pid])
            old = float(row["PROJ_MIN_V1"])
            gp = float(row["SEASON_GP"])
        except (KeyError, TypeError, ValueError):
            continue
        if (not np.isfinite(chosen) or not 0 < chosen <= 48
                or not np.isfinite(old) or old <= 0
                or not np.isfinite(gp) or gp <= 0
                or bool(row.get("IS_UNAVAILABLE", False))
                or row.get("INJ_STATUS") in ("Out", "Doubtful")):
            continue
        factor = chosen / old
        for metric in METRICS:
            for prefix in ("BASE", "PROJ"):
                col = f"{prefix}_{metric}_V1"
                if col in out:
                    v = pd.to_numeric(row.get(col), errors="coerce")
                    out.at[i, col] = max(0.0, float(v) * factor) if pd.notna(v) else np.nan
        for prefix in ("BASE", "PROJ"):
            parts = [f"{prefix}_{m}_V1" for m in ("PTS", "REB", "AST")]
            if all(c in out for c in parts):
                out.at[i, f"{prefix}_PRA_V1"] = sum(out.at[i, c] for c in parts)
        out.at[i, "PROJ_MIN_V1"] = chosen
        out.at[i, "MINUTES_SOURCE"] = "Ajuste manual pré-jogo"
        out.at[i, "MANUAL_MINUTES_ACTIVE"] = True
    return out

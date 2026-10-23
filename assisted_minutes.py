"""Transparent pregame manual-minute overlay on already computed Matchup V4 output.

This does not modify the automatic estimate, per-minute rates, matchup weights,
injury rules, or odds data. Call only with fresh model output, not previously
overridden rows.
"""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

_METRICS = ("PTS", "REB", "AST", "PRA", "3PM", "FGA", "3PA", "2PA")


def apply_manual_minutes(
    frame: pd.DataFrame,
    overrides: Mapping[int, float] | None = None,
) -> pd.DataFrame:
    """Apply per-player minute edits while keeping the original projections.

    Minutes scale existing BASE_/PROJ_ columns at unchanged per-minute rates and
    matchup factors. Missing/gated outputs remain missing, and PRA is always
    reconstructed from its components.
    """
    out = frame.copy()
    if out.empty:
        return out

    out["PROJ_MIN_AUTO"] = pd.to_numeric(out["PROJ_MIN_V1"], errors="coerce")
    out["MINUTES_SOURCE_AUTO"] = out.get("MINUTES_SOURCE", "Modelo interno")
    out["MANUAL_MINUTES_ACTIVE"] = False
    for metric in _METRICS:
        col = f"PROJ_{metric}_V1"
        if col in out:
            out[f"{col}_AUTO"] = out[col].copy()

    if not overrides:
        return out

    for idx, row in out.iterrows():
        try:
            pid = int(row["PLAYER_ID"])
            manual = float(overrides[pid])
            automatic = float(row["PROJ_MIN_AUTO"])
            played = float(row["SEASON_GP"])
        except (KeyError, TypeError, ValueError):
            continue

        unavailable = row.get("IS_UNAVAILABLE", False)
        unavailable = bool(unavailable) if pd.notna(unavailable) else False
        if (
            not np.isfinite(manual) or not 0 < manual <= 48
            or not np.isfinite(automatic) or automatic <= 0
            or not np.isfinite(played) or played <= 0
            or unavailable or str(row.get("INJ_STATUS", "")).strip() in ("Out", "Doubtful")
        ):
            continue

        factor = manual / automatic
        # Original model is homogeneous in minutes at fixed matchup/efficiency.
        for metric in _METRICS:
            for prefix in ("BASE", "PROJ"):
                column = f"{prefix}_{metric}_V1"
                if column not in out.columns:
                    continue
                base_value = pd.to_numeric(row.get(column), errors="coerce")
                out.at[idx, column] = (
                    max(0.0, float(base_value) * factor)
                    if pd.notna(base_value) and np.isfinite(base_value)
                    else np.nan
                )

        for prefix in ("BASE", "PROJ"):
            parts = [f"{prefix}_{m}_V1" for m in ("PTS", "REB", "AST")]
            if all(part in out for part in parts):
                values = [pd.to_numeric(out.at[idx, part], errors="coerce") for part in parts]
                out.at[idx, f"{prefix}_PRA_V1"] = (
                    float(sum(values)) if all(np.isfinite(v) for v in values)
                    else np.nan
                )
            # Preserve FGA=2PA+3PA from the unchanged production formulas.
            total = f"{prefix}_FGA_V1"
            shots = [f"{prefix}_{m}_V1" for m in ("2PA", "3PA")]
            was_valid = pd.notna(row.get(total))
            if was_valid and all(part in out for part in shots):
                values = [pd.to_numeric(out.at[idx, part], errors="coerce") for part in shots]
                out.at[idx, total] = (
                    float(sum(values)) if all(np.isfinite(v) for v in values)
                    else np.nan
                )

        out.at[idx, "PROJ_MIN_V1"] = manual
        out.at[idx, "MINUTES_SOURCE"] = "Ajuste manual pré-jogo"
        out.at[idx, "MANUAL_MINUTES_ACTIVE"] = True

    return out

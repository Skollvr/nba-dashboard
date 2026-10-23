"""Safe lineup evidence and observational rotation summary.

Never confuse projected NBA/ESPN lineups with verified starters. ESPN player
IDs and NBA player IDs may belong to different namespaces: cross-provider
matching therefore requires the same team AND a uniquely matching full name.
"""
from __future__ import annotations

import unicodedata
import re

import numpy as np
import pandas as pd


def _name_key(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", stripped.casefold())


def merge_nba_lineup_evidence(
    team_frame: pd.DataFrame, nba_rows: pd.DataFrame, team_id: int,
    checked_brt: str = "",
) -> pd.DataFrame:
    """Only overlay explicit current-day evidence for this exact team/player.

    No modification to projected minutes, season production, defensive context,
    market odds, or original V4 calculations.
    """
    if team_frame is None or team_frame.empty:
        return team_frame.copy() if isinstance(team_frame, pd.DataFrame) else pd.DataFrame()
    out = team_frame.copy()
    out["LINEUP_CHECKED_BRT"] = ""
    if nba_rows is None or nba_rows.empty or "TEAM_ID" not in nba_rows:
        return out

    team_ids = pd.to_numeric(nba_rows["TEAM_ID"], errors="coerce")
    entries = nba_rows.loc[team_ids.eq(int(team_id))].copy()
    if entries.empty or "PLAYER" not in entries or "LINEUP_STATUS" not in entries:
        return out

    names = out["PLAYER"].map(_name_key)
    entries["_NAME"] = entries["PLAYER"].map(_name_key)
    # Never match a partial name, unknown team or duplicated name.
    for _, entry in entries.iterrows():
        name = entry["_NAME"]
        if not name:
            continue
        matching_indices = out.index[names.eq(name)]
        if len(matching_indices) != 1:
            continue
        idx = matching_indices[0]
        if bool(out.at[idx, "IS_UNAVAILABLE"]):
            continue
        status = str(entry.get("LINEUP_STATUS", ""))
        starter = entry.get("IS_STARTER")
        # A recorded status without affirmative starter evidence is insufficient.
        starter_true = isinstance(starter, (bool, np.bool_)) and bool(starter)
        if status == "Titular confirmado" and starter_true:
            out.at[idx, "ROLE"] = "Titular confirmado"
            out.at[idx, "LINEUP_STATUS"] = "Titular confirmado"
        elif status == "Titular projetado" and starter_true:
            # Never downgrade an already confirmed player.
            if out.at[idx, "ROLE"] != "Titular confirmado":
                out.at[idx, "ROLE"] = "Titular projetado"
                out.at[idx, "LINEUP_STATUS"] = "Titular projetado"
        else:
            continue
        out.at[idx, "LINEUP_SOURCE"] = "NBA Daily Lineups"
        out.at[idx, "LINEUP_CHECKED_BRT"] = str(checked_brt)
    return out


def summarize_recent_rotation(row: pd.Series) -> dict:
    """Summarize actual recorded player minutes, newest to oldest.

    Uses observed ESPN values only; does not infer past starts or change the
    production minutes formula.
    """
    raw = row.get("RECENT_MIN_L10", [])
    if not isinstance(raw, (list, tuple, np.ndarray)):
        raw = []
    minutes = []
    for value in raw[:10]:
        numeric = pd.to_numeric(value, errors="coerce")
        if pd.notna(numeric) and np.isfinite(numeric) and numeric > 0:
            minutes.append(float(numeric))
    recent5, recent10 = minutes[:5], minutes[:10]
    l5_mean = float(np.mean(recent5)) if recent5 else None
    l10_mean = float(np.mean(recent10)) if recent10 else None
    return {
        "sample": len(recent10),
        "values_oldest_first": list(reversed(recent10)),
        "l5_mean": l5_mean,
        "l10_mean": l10_mean,
        "l5_vs_l10": (
            l5_mean - l10_mean
            if l5_mean is not None and l10_mean is not None else None
        ),
        "last_game": minutes[0] if minutes else None,
        "minimum": min(recent10) if recent10 else None,
        "maximum": max(recent10) if recent10 else None,
    }

"""Official NBA injury bulletin first; ESPN is a clearly labeled fallback.

The official PDF applies to its published matchups only. Never treat an empty
feed, another game's report, or NOT YET SUBMITTED as player availability.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from config import APP_TIMEZONE
from espn_context import get_espn_injuries_standard
from pdf_reader import fetch_latest_injury_report_df


def get_preferred_injury_report(
    team_ids: tuple[int, ...],
    matchup: str,
    target_date: date | None = None,
) -> pd.DataFrame:
    """Find authoritative team-specific entries for the selected game."""
    team_ids = tuple(int(v) for v in team_ids)
    matchup = str(matchup or "").strip().upper()
    rows: list[pd.DataFrame] = []
    official_team_ids: set[int] = set()

    # A latest official PDF is not a historical injury snapshot.
    # When testing an older game, current injuries should remain unconfirmed.
    today = datetime.now(APP_TIMEZONE).date()
    if target_date is None or target_date >= today:
        try:
            report = fetch_latest_injury_report_df()
        except Exception:
            report = pd.DataFrame()

        if report is not None and not report.empty and matchup:
            pdf_rows = report.copy()
            pdf_rows = pdf_rows[
                pdf_rows["MATCHUP"].fillna("").astype(str).str.upper().eq(matchup)
            ].copy()
            pdf_team_ids = pd.to_numeric(
                pdf_rows["TEAM_ID_IR"], errors="coerce"
            )
            pdf_rows = pdf_rows[pdf_team_ids.isin(team_ids)].copy()
            if not pdf_rows.empty:
                official_team_ids = set(
                    pd.to_numeric(pdf_rows["TEAM_ID_IR"], errors="coerce")
                    .dropna().astype(int).tolist()
                )
                rows.append(pdf_rows)

    # A "NOT YET SUBMITTED" official team is intentionally NOT replaced by
    # ESPN's current generic injury status; that would misrepresent the PDF.
    other_teams = tuple(tid for tid in team_ids if tid not in official_team_ids)
    if other_teams and (target_date is None or target_date >= today):
        try:
            espn = get_espn_injuries_standard(other_teams)
        except Exception:
            espn = pd.DataFrame()
        if espn is not None and not espn.empty:
            espn = espn.copy()
            espn["TEAM_REPORT_STATUS"] = "ESPN current feed (not official report)"
            espn["INJ_SOURCE"] = "ESPN (fallback)"
            rows.append(espn)

    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True, sort=False)

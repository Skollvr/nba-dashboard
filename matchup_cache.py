"""Short-lived cache for expensive full matchup reconstruction.

Forecast and market freshness is bounded; explicit pregame refresh clears it.
All manual minute and manual line edits remain OUTSIDE the cache.
"""
from __future__ import annotations

import streamlit as st
from processamento import get_matchup_context


@st.cache_data(ttl=180, max_entries=32, show_spinner=False)
def cached_matchup_context(
    away_team_id, home_team_id, away_team_name, home_team_name,
    season, include_market, season_scope="Regular Season",
    include_fanduel=False, bookmaker_priority="betmgm",
    roster_season=None, target_date=None, _progress_callback=None,
):
    return get_matchup_context(
        away_team_id, home_team_id, away_team_name, home_team_name,
        season, include_market, season_scope=season_scope,
        include_fanduel=include_fanduel,
        bookmaker_priority=bookmaker_priority, roster_season=roster_season,
        target_date=target_date, progress_callback=_progress_callback,
    )


def clear_cached_matchup() -> None:
    cached_matchup_context.clear()

"""Session-scoped 180-second cache for complete NBA matchup data.

The previous @st.cache_data wrapper replayed Streamlit elements created by the
progress callback (st.status.write), causing CacheReplayClosureError when a
user switched teams or otherwise triggered a rerun.

Store only data frames in the Streamlit *session state*, never UI elements or
callbacks. The original ESPN provider caches remain in effect. The cache is
per-browser-session to avoid cross-user state, bounded, and explicitly reset
by both refresh controls. Manual lines and minutes are applied downstream and
must not be cached here.
"""
from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from processamento import get_matchup_context

_CACHE_NAME = "_nba_matchup_context_cache_v2"
_TTL_SECONDS = 180.0
_MAX_ENTRIES = 8


def _arguments_key(
    away_team_id, home_team_id, away_team_name, home_team_name,
    season, include_market, season_scope, include_fanduel,
    bookmaker_priority, roster_season, target_date,
) -> tuple:
    """All inputs that change the returned figures or market quotes."""
    return (
        int(away_team_id), int(home_team_id),
        str(away_team_name), str(home_team_name), str(season),
        bool(include_market), str(season_scope), bool(include_fanduel),
        str(bookmaker_priority), str(roster_season), str(target_date),
    )


def cached_matchup_context(
    away_team_id, home_team_id, away_team_name, home_team_name,
    season, include_market, season_scope="Regular Season",
    include_fanduel=False, bookmaker_priority="betmgm",
    roster_season=None, target_date=None, _progress_callback=None,
):
    """Return independent data-frame copies, without Streamlit UI replay."""
    key = _arguments_key(
        away_team_id, home_team_id, away_team_name, home_team_name,
        season, include_market, season_scope, include_fanduel,
        bookmaker_priority, roster_season, target_date,
    )
    now = time.monotonic()
    saved = st.session_state.get(_CACHE_NAME, {})
    if isinstance(saved, dict):
        entry = saved.get(key)
        if entry is not None and entry[0] > now:
            # A new status container belongs to this run. Never replay a
            # callback into the status container of a previous run.
            return tuple(frame.copy(deep=True) for frame in entry[1])

    result = get_matchup_context(
        away_team_id, home_team_id, away_team_name, home_team_name,
        season, include_market, season_scope=season_scope,
        include_fanduel=include_fanduel,
        bookmaker_priority=bookmaker_priority, roster_season=roster_season,
        target_date=target_date, progress_callback=_progress_callback,
    )

    # This wrapper must contain only data. If a future provider changes its
    # result type, return it uncached rather than persisting UI or other state.
    if (
        isinstance(result, tuple) and len(result) == 2
        and all(isinstance(frame, pd.DataFrame) for frame in result)
    ):
        cache = {
            k: v for k, v in saved.items()
            if v[0] > now
        } if isinstance(saved, dict) else {}
        if len(cache) >= _MAX_ENTRIES and key not in cache:
            oldest = min(cache, key=lambda k: cache[k][0])
            cache.pop(oldest, None)
        cache[key] = (
            time.monotonic() + _TTL_SECONDS,
            tuple(frame.copy(deep=True) for frame in result),
        )
        st.session_state[_CACHE_NAME] = cache
    return result


def clear_cached_matchup() -> None:
    """Invalidate only the full-matchup cache of the current user session."""
    st.session_state.pop(_CACHE_NAME, None)

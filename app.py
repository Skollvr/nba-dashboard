import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
import os
from datetime import date, datetime
from typing import Optional
from pandas.io.formats.style import Styler


from config import (
    NBA_TEAM_COLORS, TEAM_LOOKUP, TEAM_ABBR_LOOKUP, TEAM_LOGO_URL,
    PLAYER_HEADSHOT_URL, SORT_OPTIONS, ROLE_OPTIONS, VIEW_OPTIONS,
    CHART_OPTIONS, LINE_METRIC_OPTIONS, PROJECTION_WEIGHTS,
    ODDS_API_BASE_URL, ODDS_BOOKMAKER, ODDS_STAT_MAP, ODDS_METRIC_COLUMNS,
    INACTIVE_STATUSES, WATCHLIST_STATUSES, PLAYER_STATUS_RE, GAME_PREFIX_RE,
    APP_TIMEZONE, EASTERN_TIMEZONE, UTC_TIMEZONE, TEAM_NAME_LOOKUP_NORM
)

from api_nba import (
    run_api_call_with_retry, get_games_for_date, get_team_roster,
    get_league_player_stats, get_player_log, get_team_player_logs,
    get_position_allowed_profile, get_league_position_baseline
)

from api_odds import (
    normalize_text, normalize_person_name, american_to_decimal, get_odds_api_key,
    fetch_nba_odds_events, find_matching_odds_event, extract_betmgm_player_props
)

from pdf_reader import (
    get_season_string, parse_injury_report_timestamp_from_url, 
    fetch_latest_injury_report_df
)

from processamento import (
    filter_and_sort_team_df, build_display_dataframes, build_summary_cards_data,
    get_matchup_context, get_matchup_injury_context, merge_injury_report,
    get_line_context, get_metric_projection_column, get_matchup_chip_class,
    build_team_table  # Adicione esta aqui se não estiver
)

from ui_components import (
    inject_css, 
    style_table,
    render_matchup_header,
    render_game_rankings,
    render_summary_cards, 
    render_team_section_v2, 
    render_player_cards_grid,
    render_player_card,
    render_injury_report_tab,
    render_lineup_report_tab,
    render_player_focus_panel,
    get_team_logo_url,
    get_player_headshot_url,
    format_number,
    format_signed_number,
    get_matchup_parts
)
st.set_page_config(
    page_title="NBA Props Dashboard",
    page_icon="🏀",
    layout="wide",
)

def get_brasilia_today() -> date:
    return datetime.now(APP_TIMEZONE).date()


def get_game_datetime_brasilia(game: dict) -> Optional[datetime]:
    candidate_fields = [
        (game.get("gameTimeUTC"), UTC_TIMEZONE),
        (game.get("gameDateTimeUTC"), UTC_TIMEZONE),
        (game.get("gameEt"), EASTERN_TIMEZONE),
        (game.get("gameDateEST"), EASTERN_TIMEZONE),
        (game.get("gameDateTimeEst"), EASTERN_TIMEZONE),
    ]

    for raw_value, source_tz in candidate_fields:
        if not raw_value:
            continue
        parsed = pd.to_datetime(raw_value, errors="coerce")
        if pd.isna(parsed):
            continue
        dt_value = parsed.to_pydatetime()
        if dt_value.tzinfo is None:
            dt_value = dt_value.replace(tzinfo=source_tz)
        return dt_value.astimezone(APP_TIMEZONE)

    game_code = str(game.get("gameCode", ""))
    if len(game_code) >= 8 and game_code[:8].isdigit():
        try:
            fallback_dt = datetime.strptime(game_code[:8], "%Y%m%d").replace(tzinfo=EASTERN_TIMEZONE)
            return fallback_dt.astimezone(APP_TIMEZONE)
        except ValueError:
            return None

    return None

    
if __name__ == "__main__":
    main()

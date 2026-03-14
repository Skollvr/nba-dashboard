from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from nba_api.stats.endpoints import (
    commonteamroster,
    leaguedashplayerstats,
    playergamelog,
    playergamelogs,
    scoreboardv2,
)
from nba_api.stats.static import teams

st.set_page_config(
    page_title="NBA Dashboard MVP",
    page_icon="🏀",
    layout="wide",
)

TEAM_LOOKUP = {team["id"]: team for team in teams.get_teams()}
TEAM_LOGO_URL = "https://cdn.nba.com/logos/nba/{team_id}/primary/L/logo.svg"
PLAYER_HEADSHOT_URL = "https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"

SORT_OPTIONS = {
    "PRA L10": "L10_PRA",
    "Δ PRA L10 vs Temp": "DELTA_PRA_L10",
    "PRA L5": "L5_PRA",
    "Δ PRA L5 vs Temp": "DELTA_PRA_L5",
    "PTS L10": "L10_PTS",
    "REB L10": "L10_REB",
    "AST L10": "L10_AST",
    "PRA temporada": "SEASON_PRA",
    "PTS temporada": "SEASON_PTS",
    "REB temporada": "SEASON_REB",
    "AST temporada": "SEASON_AST",
    "Minutos por jogo": "SEASON_MIN",
    "Jogos na temporada": "SEASON_GP",
    "Nome do jogador": "PLAYER",
}

ROLE_OPTIONS = ["Todos", "Titular provável", "Reserva"]
VIEW_OPTIONS = ["Cards", "Tabela"]
CHART_OPTIONS = ["Compacto", "Completo"]
LINE_METRIC_OPTIONS = ["PRA", "PTS", "REB", "AST"]
PROJECTION_WEIGHTS = {
    "season": 0.35,
    "l10": 0.40,
    "l5": 0.15,
    "matchup": 0.10,
}


def get_season_string(target_date: date) -> str:
    if target_date.month >= 10:
        start_year = target_date.year
        end_year = str(target_date.year + 1)[-2:]
    else:
        start_year = target_date.year - 1
        end_year = str(target_date.year)[-2:]
    return f"{start_year}-{end_year}"


def get_team_logo_url(team_id: int) -> str:
    return TEAM_LOGO_URL.format(team_id=team_id)


def get_player_headshot_url(player_id: int) -> str:
    return PLAYER_HEADSHOT_URL.format(player_id=player_id)


def format_number(value, decimals: int = 1) -> str:
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def format_signed_number(value, decimals: int = 1) -> str:
    try:
        value = float(value)
        return f"{value:+.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def format_ratio_text(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "-"
    return f"{int(numerator)}/{int(denominator)}"


def get_matchup_parts(matchup: str) -> tuple[str, str]:
    if not isinstance(matchup, str) or matchup.strip() == "":
        return "", ""

    cleaned = matchup.replace("vs.", "vs").strip()
    parts = cleaned.split()
    if len(parts) < 3:
        return "", ""

    venue = "vs" if "vs" in parts else "@"
    opponent_abbr = parts[-1].strip().upper()
    return venue, opponent_abbr


def normalize_position_group(position: str) -> str:
    pos = str(position or "").upper().strip()
    if not pos:
        return "F"

    primary = pos.split("-")[0].strip()
    if primary in {"G", "F", "C"}:
        return primary
    if "G" in pos:
        return "G"
    if "F" in pos:
        return "F"
    if "C" in pos:
        return "C"
    return "F"


def classify_oscillation(value: float) -> str:
    if value <= 4.5:
        return "Baixa"
    if value <= 7.5:
        return "Média"
    return "Alta"


def classify_form_signal(slope: float) -> str:
    if slope >= 1.0:
        return "↗ Em alta"
    if slope <= -1.0:
        return "↘ Em queda"
    return "→ Estável"


def classify_matchup_tier(diff_value: float) -> str:
    if diff_value >= 2.5:
        return "Favorável"
    if diff_value <= -2.5:
        return "Difícil"
    return "Neutro"


def get_matchup_chip_class(label: str) -> str:
    if label == "Favorável":
        return "matchup-good"
    if label == "Difícil":
        return "matchup-bad"
    return "matchup-neutral"


def calculate_projection(
    season_value: float,
    l10_value: float,
    l5_value: float,
    opp_allowed: float,
    league_allowed: float,
) -> float:
    matchup_adjusted = float(season_value) + (float(opp_allowed) - float(league_allowed))
    projection = (
        PROJECTION_WEIGHTS["season"] * float(season_value)
        + PROJECTION_WEIGHTS["l10"] * float(l10_value)
        + PROJECTION_WEIGHTS["l5"] * float(l5_value)
        + PROJECTION_WEIGHTS["matchup"] * matchup_adjusted
    )
    return max(0.0, projection)


def get_metric_projection_column(metric: str) -> str:
    return {
        "PRA": "PROJ_PRA",
        "PTS": "PROJ_PTS",
        "REB": "PROJ_REB",
        "AST": "PROJ_AST",
    }[metric]


def get_metric_recent_list_column(metric: str) -> str:
    return {
        "PRA": "RECENT_PRA_L10",
        "PTS": "RECENT_PTS_L10",
        "REB": "RECENT_REB_L10",
        "AST": "RECENT_AST_L10",
    }[metric]


def classify_line_edge(edge: float) -> str:
    if edge >= 1.5:
        return "Acima"
    if edge <= -1.5:
        return "Abaixo"
    return "Justa"


def get_line_context(row: pd.Series, metric: str, line_value: float) -> dict:
    projection_col = get_metric_projection_column(metric)
    recent_list_col = get_metric_recent_list_column(metric)

    projection = float(row.get(projection_col, 0.0))
    edge = projection - float(line_value)
    recent_values = row.get(recent_list_col, [])
    if not isinstance(recent_values, list):
        recent_values = []

    hit_l10 = sum(float(v) >= float(line_value) for v in recent_values)
    hit_l5 = sum(float(v) >= float(line_value) for v in recent_values[:5])

    return {
        "projection": projection,
        "edge": edge,
        "label": classify_line_edge(edge),
        "hit_l10": format_ratio_text(hit_l10, len(recent_values)),
        "hit_l5": format_ratio_text(hit_l5, min(len(recent_values), 5)),
    }


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.1rem;
            padding-bottom: 2rem;
        }
        .main-title {
            font-size: 2.1rem;
            font-weight: 800;
            margin-bottom: 0.15rem;
        }
        .subtitle {
            color: #94a3b8;
            margin-bottom: 1rem;
        }
        .matchup-shell {
            background: linear-gradient(180deg, rgba(17,24,39,0.92), rgba(15,23,42,0.92));
            border: 1px solid rgba(148,163,184,.12);
            border-radius: 22px;
            padding: 1rem 1.2rem;
            margin-bottom: 1rem;
        }
        .center-vs {
            text-align: center;
            color: #cbd5e1;
            font-size: 0.9rem;
            font-weight: 700;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            margin-top: 0.75rem;
        }
        .status-chip {
            display: inline-block;
            padding: 0.32rem 0.7rem;
            border-radius: 999px;
            background: rgba(139,92,246,0.12);
            border: 1px solid rgba(139,92,246,0.22);
            color: #e9d5ff;
            font-size: 0.84rem;
            font-weight: 600;
        }
        .team-title {
            font-size: 1.35rem;
            font-weight: 800;
            margin-bottom: 0.15rem;
        }
        .team-sub {
            color: #94a3b8;
            font-size: 0.9rem;
        }
        .summary-card {
            background: rgba(15,23,42,0.75);
            border: 1px solid rgba(148,163,184,.12);
            border-radius: 18px;
            padding: 0.9rem 1rem;
            min-height: 112px;
            margin-bottom: 0.35rem;
        }
        .summary-label {
            color: #94a3b8;
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.45rem;
        }
        .summary-value {
            font-size: 1.55rem;
            font-weight: 800;
            line-height: 1.1;
            color: #f8fafc;
            margin-bottom: 0.3rem;
        }
        .summary-meta {
            color: #cbd5e1;
            font-size: 0.9rem;
        }
        .info-pill {
            display: inline-block;
            padding: 0.35rem 0.6rem;
            border-radius: 999px;
            background: rgba(15,23,42,.65);
            border: 1px solid rgba(148,163,184,.18);
            color: #cbd5e1;
            font-size: 0.85rem;
            margin-right: 0.35rem;
            margin-bottom: 0.35rem;
        }
        .section-note {
            color: #cbd5e1;
            font-size: 0.92rem;
            margin-bottom: 0.55rem;
        }
        .small-note {
            color: #94a3b8;
            font-size: 0.88rem;
        }
        .badge-row {
            display: flex;
            gap: 0.35rem;
            flex-wrap: wrap;
            margin-top: 0.2rem;
            margin-bottom: 0.15rem;
        }
        .badge {
            display: inline-block;
            padding: 0.22rem 0.48rem;
            border-radius: 999px;
            font-size: 0.74rem;
            font-weight: 700;
        }
        .badge-starter {
            background: rgba(139,92,246,0.12);
            color: #f3e8ff;
            border: 1px solid rgba(139,92,246,0.18);
        }
        .badge-bench {
            background: rgba(148,163,184,0.10);
            color: #cbd5e1;
            border: 1px solid rgba(148,163,184,0.14);
        }
        .badge-good {
            background: rgba(34,197,94,0.10);
            color: #dcfce7;
            border: 1px solid rgba(34,197,94,0.16);
        }
        .badge-bad {
            background: rgba(239,68,68,0.10);
            color: #fee2e2;
            border: 1px solid rgba(239,68,68,0.16);
        }
        .badge-neutral {
            background: rgba(148,163,184,0.10);
            color: #e2e8f0;
            border: 1px solid rgba(148,163,184,0.14);
        }
        .player-headline-card {
            background: linear-gradient(180deg, rgba(76,29,149,0.36), rgba(15,23,42,0.92));
            border: 1px solid rgba(167,139,250,0.22);
            border-radius: 18px;
            padding: 0.8rem 0.9rem;
            margin-top: 0.3rem;
            margin-bottom: 0.3rem;
        }
        .player-headline-label {
            color: #c4b5fd;
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.15rem;
        }
        .player-headline-value {
            color: #f8fafc;
            font-size: 1.85rem;
            font-weight: 900;
            line-height: 1;
            margin-bottom: 0.35rem;
        }
        .player-headline-sub {
            color: #e2e8f0;
            font-size: 0.86rem;
            line-height: 1.35;
        }
        .player-quick-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 0.55rem;
            margin-top: 0.9rem;
            margin-bottom: 0.15rem;
        }
        .quick-stat {
            background: rgba(15,23,42,0.74);
            border: 1px solid rgba(148,163,184,.12);
            border-radius: 16px;
            padding: 0.72rem 0.78rem;
            min-height: 84px;
        }
        .quick-stat-primary {
            background: linear-gradient(180deg, rgba(76,29,149,0.58), rgba(30,41,59,0.9));
            border: 1px solid rgba(167,139,250,0.28);
        }
        .quick-stat-up {
            background: linear-gradient(180deg, rgba(21,128,61,0.38), rgba(30,41,59,0.9));
            border: 1px solid rgba(74,222,128,0.24);
        }
        .quick-stat-down {
            background: linear-gradient(180deg, rgba(153,27,27,0.34), rgba(30,41,59,0.9));
            border: 1px solid rgba(248,113,113,0.22);
        }
        .quick-stat-label {
            color: #94a3b8;
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.32rem;
        }
        .quick-stat-value {
            color: #f8fafc;
            font-size: 1.28rem;
            font-weight: 800;
            line-height: 1.05;
            margin-bottom: 0.28rem;
        }
        .quick-stat-meta {
            color: #cbd5e1;
            font-size: 0.77rem;
            line-height: 1.25;
        }
        .detail-box {
            background: rgba(15,23,42,0.74);
            border: 1px solid rgba(148,163,184,.12);
            border-radius: 18px;
            padding: 0.82rem;
            margin-bottom: 0.75rem;
        }
        .detail-box-top {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 0.5rem;
            margin-bottom: 0.75rem;
        }
        .detail-box-title {
            color: #f8fafc;
            font-size: 0.95rem;
            font-weight: 800;
            letter-spacing: 0.02em;
        }
        .delta-pill-row {
            display: flex;
            gap: 0.35rem;
            flex-wrap: wrap;
            justify-content: flex-end;
        }
        .delta-pill {
            display: inline-block;
            padding: 0.2rem 0.42rem;
            border-radius: 999px;
            font-size: 0.69rem;
            font-weight: 700;
            white-space: nowrap;
        }
        .delta-up {
            background: rgba(34,197,94,0.12);
            color: #dcfce7;
            border: 1px solid rgba(34,197,94,0.16);
        }
        .delta-down {
            background: rgba(239,68,68,0.12);
            color: #fee2e2;
            border: 1px solid rgba(239,68,68,0.16);
        }
        .delta-flat {
            background: rgba(148,163,184,0.10);
            color: #e2e8f0;
            border: 1px solid rgba(148,163,184,0.14);
        }
        .detail-mini-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.45rem;
        }
        .detail-mini {
            background: rgba(2,6,23,0.35);
            border: 1px solid rgba(148,163,184,.10);
            border-radius: 14px;
            padding: 0.52rem 0.58rem;
        }
        .detail-mini-highlight {
            background: rgba(30,41,59,0.82);
            border: 1px solid rgba(139,92,246,0.22);
        }
        .detail-mini-label {
            color: #94a3b8;
            font-size: 0.69rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.2rem;
        }
        .detail-mini-value {
            color: #f8fafc;
            font-size: 1rem;
            font-weight: 800;
            line-height: 1.05;
        }
        .hero-note {
            color: #94a3b8;
            font-size: 0.82rem;
            margin-top: 0.55rem;
            line-height: 1.35;
        }
        .matchup-chip {
            display: inline-block;
            padding: 0.28rem 0.54rem;
            border-radius: 999px;
            font-size: 0.73rem;
            font-weight: 800;
            letter-spacing: 0.02em;
        }
        .matchup-good {
            background: rgba(34,197,94,0.12);
            color: #dcfce7;
            border: 1px solid rgba(34,197,94,0.16);
        }
        .matchup-neutral {
            background: rgba(148,163,184,0.10);
            color: #e2e8f0;
            border: 1px solid rgba(148,163,184,0.14);
        }
        .matchup-bad {
            background: rgba(239,68,68,0.12);
            color: #fee2e2;
            border: 1px solid rgba(239,68,68,0.16);
        }
        @media (max-width: 1200px) {
            .player-quick-grid {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
        }
        @media (max-width: 760px) {
            .player-quick-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .detail-mini-grid {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=1800, show_spinner=False)
def get_games_for_date(target_date: date) -> pd.DataFrame:
    response = scoreboardv2.ScoreboardV2(
        game_date=target_date.strftime("%Y-%m-%d"),
        day_offset=0,
        league_id="00",
        timeout=30,
    )
    data = response.get_normalized_dict()
    games = pd.DataFrame(data.get("GameHeader", []))

    if games.empty:
        return games

    games["home_team_name"] = games["HOME_TEAM_ID"].map(
        lambda x: TEAM_LOOKUP.get(x, {}).get("full_name", str(x))
    )
    games["away_team_name"] = games["VISITOR_TEAM_ID"].map(
        lambda x: TEAM_LOOKUP.get(x, {}).get("full_name", str(x))
    )
    games["label"] = (
        games["away_team_name"]
        + " @ "
        + games["home_team_name"]
        + " • "
        + games["GAME_STATUS_TEXT"].fillna("Sem status")
    )

    return games[
        [
            "GAME_ID",
            "HOME_TEAM_ID",
            "VISITOR_TEAM_ID",
            "GAME_STATUS_TEXT",
            "home_team_name",
            "away_team_name",
            "label",
        ]
    ].copy()


@st.cache_data(ttl=21600, show_spinner=False)
def get_team_roster(team_id: int, season: str) -> pd.DataFrame:
    response = commonteamroster.CommonTeamRoster(
        team_id=team_id,
        season=season,
        timeout=30,
    )
    frames = response.get_data_frames()
    if not frames:
        return pd.DataFrame()

    roster = frames[0].copy()
    if roster.empty:
        return roster

    if "PLAYER" not in roster.columns and "PLAYER_NAME" in roster.columns:
        roster["PLAYER"] = roster["PLAYER_NAME"]
    if "PLAYER_ID" not in roster.columns and "PERSON_ID" in roster.columns:
        roster["PLAYER_ID"] = roster["PERSON_ID"]

    roster["PLAYER_ID"] = pd.to_numeric(roster["PLAYER_ID"], errors="coerce")
    roster["TEAM_ID"] = team_id
    return roster


@st.cache_data(ttl=3600, show_spinner=False)
def get_league_player_stats(season: str, last_n_games: int) -> pd.DataFrame:
    response = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season,
        season_type_all_star="Regular Season",
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Base",
        last_n_games=last_n_games,
        month=0,
        opponent_team_id=0,
        pace_adjust="N",
        plus_minus="N",
        rank="N",
        period=0,
        team_id_nullable="",
        timeout=30,
    )
    frames = response.get_data_frames()
    if not frames:
        return pd.DataFrame()

    df = frames[0].copy()
    if df.empty:
        return pd.DataFrame(
            columns=["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GP", "MIN", "PTS", "REB", "AST"]
        )

    keep_cols = ["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GP", "MIN", "PTS", "REB", "AST"]
    return df[[c for c in keep_cols if c in df.columns]].copy()


@st.cache_data(ttl=3600, show_spinner=False)
def get_player_log(player_id: int, season: str) -> pd.DataFrame:
    response = playergamelog.PlayerGameLog(
        player_id=player_id,
        season=season,
        season_type_all_star="Regular Season",
        timeout=30,
    )
    frames = response.get_data_frames()
    if not frames:
        return pd.DataFrame()

    df = frames[0].copy()
    if df.empty:
        return df

    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
    return df.sort_values("GAME_DATE", ascending=False)


@st.cache_data(ttl=3600, show_spinner=False)
def get_team_player_logs(team_id: int, season: str) -> pd.DataFrame:
    response = playergamelogs.PlayerGameLogs(
        team_id_nullable=team_id,
        season_nullable=season,
        season_type_nullable="Regular Season",
        timeout=30,
    )
    frames = response.get_data_frames()
    if not frames:
        return pd.DataFrame()

    df = frames[0].copy()
    if df.empty:
        return df

    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
    for col in ["PTS", "REB", "AST", "MIN"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
    return df.sort_values(["PLAYER_ID", "GAME_DATE"], ascending=[True, False])


@st.cache_data(ttl=7200, show_spinner=False)
def get_position_opponent_profile(season: str, opponent_team_id: int, position_group: str) -> dict:
    def fetch(position_value: str, opponent_value: int) -> pd.DataFrame:
        response = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season,
            season_type_all_star="Regular Season",
            per_mode_detailed="PerGame",
            measure_type_detailed_defense="Base",
            last_n_games=0,
            month=0,
            opponent_team_id=opponent_value,
            pace_adjust="N",
            plus_minus="N",
            rank="N",
            period=0,
            team_id_nullable="",
            player_position_abbreviation_nullable=position_value,
            timeout=30,
        )
        frames = response.get_data_frames()
        if not frames:
            return pd.DataFrame()
        return frames[0].copy()

    def weighted_profile(df: pd.DataFrame) -> dict:
        if df.empty or "GP" not in df.columns:
            return {"PTS": 0.0, "REB": 0.0, "AST": 0.0, "PRA": 0.0, "GP": 0.0}

        work_df = df.copy()
        for col in ["GP", "PTS", "REB", "AST"]:
            work_df[col] = pd.to_numeric(work_df[col], errors="coerce").fillna(0.0)

        total_gp = float(work_df["GP"].sum())
        if total_gp <= 0:
            return {"PTS": 0.0, "REB": 0.0, "AST": 0.0, "PRA": 0.0, "GP": 0.0}

        pts = float((work_df["PTS"] * work_df["GP"]).sum() / total_gp)
        reb = float((work_df["REB"] * work_df["GP"]).sum() / total_gp)
        ast = float((work_df["AST"] * work_df["GP"]).sum() / total_gp)
        return {"PTS": pts, "REB": reb, "AST": ast, "PRA": pts + reb + ast, "GP": total_gp}

    opp_profile = weighted_profile(fetch(position_group, opponent_team_id))
    league_profile = weighted_profile(fetch(position_group, 0))
    matchup_diff = opp_profile["PRA"] - league_profile["PRA"]

    return {
        "POSITION_GROUP": position_group,
        "OPP_PTS_ALLOWED": opp_profile["PTS"],
        "OPP_REB_ALLOWED": opp_profile["REB"],
        "OPP_AST_ALLOWED": opp_profile["AST"],
        "OPP_PRA_ALLOWED": opp_profile["PRA"],
        "LEAGUE_PTS_BASELINE": league_profile["PTS"],
        "LEAGUE_REB_BASELINE": league_profile["REB"],
        "LEAGUE_AST_BASELINE": league_profile["AST"],
        "LEAGUE_PRA_BASELINE": league_profile["PRA"],
        "MATCHUP_DIFF": matchup_diff,
        "MATCHUP_LABEL": classify_matchup_tier(matchup_diff),
    }


def build_team_table(team_id: int, season: str) -> pd.DataFrame:
    roster = get_team_roster(team_id, season)
    season_stats = get_league_player_stats(season, last_n_games=0)
    last5_stats = get_league_player_stats(season, last_n_games=5)
    last10_stats = get_league_player_stats(season, last_n_games=10)

    if roster.empty:
        return pd.DataFrame()

    roster_cols = ["PLAYER", "PLAYER_ID", "POSITION"]
    roster = roster[[c for c in roster_cols if c in roster.columns]].copy()
    if "POSITION" not in roster.columns:
        roster["POSITION"] = ""

    season_view = (
        pd.DataFrame(columns=["PLAYER_ID", "SEASON_GP", "SEASON_MIN", "SEASON_PTS", "SEASON_REB", "SEASON_AST"])
        if season_stats.empty
        else season_stats.rename(
            columns={
                "GP": "SEASON_GP",
                "MIN": "SEASON_MIN",
                "PTS": "SEASON_PTS",
                "REB": "SEASON_REB",
                "AST": "SEASON_AST",
            }
        )
    )

    last5_view = (
        pd.DataFrame(columns=["PLAYER_ID", "L5_GP", "L5_MIN", "L5_PTS", "L5_REB", "L5_AST"])
        if last5_stats.empty
        else last5_stats.rename(
            columns={
                "GP": "L5_GP",
                "MIN": "L5_MIN",
                "PTS": "L5_PTS",
                "REB": "L5_REB",
                "AST": "L5_AST",
            }
        )
    )

    last10_view = (
        pd.DataFrame(columns=["PLAYER_ID", "L10_GP", "L10_MIN", "L10_PTS", "L10_REB", "L10_AST"])
        if last10_stats.empty
        else last10_stats.rename(
            columns={
                "GP": "L10_GP",
                "MIN": "L10_MIN",
                "PTS": "L10_PTS",
                "REB": "L10_REB",
                "AST": "L10_AST",
            }
        )
    )

    team_df = roster.merge(
        season_view[["PLAYER_ID", "SEASON_GP", "SEASON_MIN", "SEASON_PTS", "SEASON_REB", "SEASON_AST"]],
        on="PLAYER_ID",
        how="left",
    ).merge(
        last5_view[["PLAYER_ID", "L5_GP", "L5_MIN", "L5_PTS", "L5_REB", "L5_AST"]],
        on="PLAYER_ID",
        how="left",
    ).merge(
        last10_view[["PLAYER_ID", "L10_GP", "L10_MIN", "L10_PTS", "L10_REB", "L10_AST"]],
        on="PLAYER_ID",
        how="left",
    )

    numeric_cols = [
        "SEASON_GP", "SEASON_MIN", "SEASON_PTS", "SEASON_REB", "SEASON_AST",
        "L5_GP", "L5_MIN", "L5_PTS", "L5_REB", "L5_AST",
        "L10_GP", "L10_MIN", "L10_PTS", "L10_REB", "L10_AST",
    ]
    for col in numeric_cols:
        if col not in team_df.columns:
            team_df[col] = 0.0
        team_df[col] = pd.to_numeric(team_df[col], errors="coerce").fillna(0.0)

    team_df["SEASON_PRA"] = team_df["SEASON_PTS"] + team_df["SEASON_REB"] + team_df["SEASON_AST"]
    team_df["L5_PRA"] = team_df["L5_PTS"] + team_df["L5_REB"] + team_df["L5_AST"]
    team_df["L10_PRA"] = team_df["L10_PTS"] + team_df["L10_REB"] + team_df["L10_AST"]
    team_df["DELTA_PRA_L5"] = team_df["L5_PRA"] - team_df["SEASON_PRA"]
    team_df["DELTA_PRA_L10"] = team_df["L10_PRA"] - team_df["SEASON_PRA"]

    def classify_trend(delta_pra_l10: float) -> str:
        if delta_pra_l10 >= 3.0:
            return "🔥 Forte"
        if delta_pra_l10 >= 1.0:
            return "⬆️ Boa"
        if delta_pra_l10 <= -3.0:
            return "🥶 Queda"
        if delta_pra_l10 <= -1.0:
            return "⬇️ Fraca"
        return "➖ Neutra"

    team_df["TREND"] = team_df["DELTA_PRA_L10"].apply(classify_trend)
    team_df["POSITION_GROUP"] = team_df["POSITION"].apply(normalize_position_group)

    team_df["ROLE"] = "Reserva"
    starter_ids = (
        team_df.sort_values(by=["SEASON_MIN", "SEASON_GP", "PLAYER"], ascending=[False, False, True])
        .head(5)["PLAYER_ID"]
        .tolist()
    )
    team_df.loc[team_df["PLAYER_ID"].isin(starter_ids), "ROLE"] = "Titular provável"

    return team_df[
        [
            "PLAYER_ID", "PLAYER", "POSITION", "POSITION_GROUP", "ROLE",
            "SEASON_GP", "SEASON_MIN",
            "SEASON_PTS", "L5_PTS", "L10_PTS",
            "SEASON_REB", "L5_REB", "L10_REB",
            "SEASON_AST", "L5_AST", "L10_AST",
            "SEASON_PRA", "L5_PRA", "L10_PRA",
            "DELTA_PRA_L5", "DELTA_PRA_L10", "TREND",
        ]
    ].copy()


def build_form_context(team_df: pd.DataFrame, team_logs: pd.DataFrame) -> pd.DataFrame:
    if team_df.empty:
        return team_df

    scalar_defaults = {
        "HIT_RATE_L10": 0.0,
        "HIT_RATE_L10_TEXT": "-",
        "PTS_HIT_RATE_L10": 0.0,
        "PTS_HIT_RATE_L10_TEXT": "-",
        "REB_HIT_RATE_L10": 0.0,
        "REB_HIT_RATE_L10_TEXT": "-",
        "AST_HIT_RATE_L10": 0.0,
        "AST_HIT_RATE_L10_TEXT": "-",
        "OSC_L10": 0.0,
        "OSC_CLASS": "-",
        "FORM_SIGNAL": "→ Estável",
    }
    list_defaults = {
        "RECENT_PRA_L10": [],
        "RECENT_PTS_L10": [],
        "RECENT_REB_L10": [],
        "RECENT_AST_L10": [],
    }

    if team_logs.empty:
        enriched = team_df.copy()
        for col, default in defaults_map.items():
            enriched[col] = default
        return enriched

    threshold_map = team_df.set_index("PLAYER_ID")[["SEASON_PRA", "SEASON_PTS", "SEASON_REB", "SEASON_AST"]].to_dict("index")
    metrics = []

    for player_id, player_logs in team_logs.groupby("PLAYER_ID"):
        recent10 = player_logs.sort_values("GAME_DATE", ascending=False).head(10).copy()
        sample_size = len(recent10)
        thresholds = threshold_map.get(player_id, {})

        if sample_size == 0:
            metrics.append({"PLAYER_ID": player_id, **scalar_defaults, **list_defaults})
            continue

        pra_threshold = float(thresholds.get("SEASON_PRA", 0.0))
        pts_threshold = float(thresholds.get("SEASON_PTS", 0.0))
        reb_threshold = float(thresholds.get("SEASON_REB", 0.0))
        ast_threshold = float(thresholds.get("SEASON_AST", 0.0))

        hit_count_pra = int((recent10["PRA"] >= pra_threshold).sum()) if pra_threshold > 0 else 0
        hit_count_pts = int((recent10["PTS"] >= pts_threshold).sum()) if pts_threshold > 0 else 0
        hit_count_reb = int((recent10["REB"] >= reb_threshold).sum()) if reb_threshold > 0 else 0
        hit_count_ast = int((recent10["AST"] >= ast_threshold).sum()) if ast_threshold > 0 else 0

        osc_value = float(recent10["PRA"].std(ddof=0)) if sample_size > 1 else 0.0
        ordered = recent10.sort_values("GAME_DATE")
        slope = float(np.polyfit(range(len(ordered)), ordered["PRA"], 1)[0]) if len(ordered) >= 3 else 0.0

        metrics.append(
            {
                "PLAYER_ID": player_id,
                "HIT_RATE_L10": float(hit_count_pra / sample_size),
                "HIT_RATE_L10_TEXT": format_ratio_text(hit_count_pra, sample_size),
                "PTS_HIT_RATE_L10": float(hit_count_pts / sample_size),
                "PTS_HIT_RATE_L10_TEXT": format_ratio_text(hit_count_pts, sample_size),
                "REB_HIT_RATE_L10": float(hit_count_reb / sample_size),
                "REB_HIT_RATE_L10_TEXT": format_ratio_text(hit_count_reb, sample_size),
                "AST_HIT_RATE_L10": float(hit_count_ast / sample_size),
                "AST_HIT_RATE_L10_TEXT": format_ratio_text(hit_count_ast, sample_size),
                "OSC_L10": osc_value,
                "OSC_CLASS": classify_oscillation(osc_value),
                "FORM_SIGNAL": classify_form_signal(slope),
                "RECENT_PRA_L10": recent10["PRA"].round(1).tolist(),
                "RECENT_PTS_L10": recent10["PTS"].round(1).tolist(),
                "RECENT_REB_L10": recent10["REB"].round(1).tolist(),
                "RECENT_AST_L10": recent10["AST"].round(1).tolist(),
            }
        )

    metrics_df = pd.DataFrame(metrics)
    enriched = team_df.merge(metrics_df, on="PLAYER_ID", how="left")

    for col, default in scalar_defaults.items():
        if isinstance(default, float):
            enriched[col] = pd.to_numeric(enriched[col], errors="coerce").fillna(default)
        else:
            enriched[col] = enriched[col].fillna(default)

    for col in list_defaults:
        if col not in enriched.columns:
            enriched[col] = [[] for _ in range(len(enriched))]
        enriched[col] = enriched[col].apply(lambda x: x if isinstance(x, list) else [])

    return enriched


def enrich_team_with_context(
    team_df: pd.DataFrame,
    team_id: int,
    opponent_team_id: int,
    opponent_team_name: str,
    season: str,
) -> pd.DataFrame:
    if team_df.empty:
        return team_df

    team_logs = get_team_player_logs(team_id, season)
    enriched = build_form_context(team_df, team_logs)

    matchup_rows = [
        get_position_opponent_profile(season, opponent_team_id, pos)
        for pos in ["G", "F", "C"]
    ]
    matchup_df = pd.DataFrame(matchup_rows)

    if matchup_df.empty:
        enriched["OPP_TEAM_NAME"] = opponent_team_name
        enriched["OPP_PTS_ALLOWED"] = 0.0
        enriched["OPP_REB_ALLOWED"] = 0.0
        enriched["OPP_AST_ALLOWED"] = 0.0
        enriched["OPP_PRA_ALLOWED"] = 0.0
        enriched["LEAGUE_PTS_BASELINE"] = 0.0
        enriched["LEAGUE_REB_BASELINE"] = 0.0
        enriched["LEAGUE_AST_BASELINE"] = 0.0
        enriched["LEAGUE_PRA_BASELINE"] = 0.0
        enriched["MATCHUP_DIFF"] = 0.0
        enriched["MATCHUP_LABEL"] = "Neutro"
        enriched["PROJ_PTS"] = enriched.apply(lambda row: calculate_projection(row["SEASON_PTS"], row["L10_PTS"], row["L5_PTS"], 0.0, 0.0), axis=1)
        enriched["PROJ_REB"] = enriched.apply(lambda row: calculate_projection(row["SEASON_REB"], row["L10_REB"], row["L5_REB"], 0.0, 0.0), axis=1)
        enriched["PROJ_AST"] = enriched.apply(lambda row: calculate_projection(row["SEASON_AST"], row["L10_AST"], row["L5_AST"], 0.0, 0.0), axis=1)
        enriched["PROJ_PRA"] = enriched.apply(lambda row: calculate_projection(row["SEASON_PRA"], row["L10_PRA"], row["L5_PRA"], 0.0, 0.0), axis=1)
        return enriched

    enriched = enriched.merge(matchup_df, on="POSITION_GROUP", how="left")
    enriched["OPP_TEAM_NAME"] = opponent_team_name

    for col in [
        "OPP_PTS_ALLOWED", "OPP_REB_ALLOWED", "OPP_AST_ALLOWED",
        "OPP_PRA_ALLOWED", "LEAGUE_PTS_BASELINE", "LEAGUE_REB_BASELINE",
        "LEAGUE_AST_BASELINE", "LEAGUE_PRA_BASELINE", "MATCHUP_DIFF",
    ]:
        if col not in enriched.columns:
            enriched[col] = 0.0
        enriched[col] = pd.to_numeric(enriched[col], errors="coerce").fillna(0.0)

    enriched["MATCHUP_LABEL"] = enriched["MATCHUP_LABEL"].fillna("Neutro")

    enriched["PROJ_PTS"] = enriched.apply(
        lambda row: calculate_projection(
            row["SEASON_PTS"], row["L10_PTS"], row["L5_PTS"], row["OPP_PTS_ALLOWED"], row["LEAGUE_PTS_BASELINE"]
        ),
        axis=1,
    )
    enriched["PROJ_REB"] = enriched.apply(
        lambda row: calculate_projection(
            row["SEASON_REB"], row["L10_REB"], row["L5_REB"], row["OPP_REB_ALLOWED"], row["LEAGUE_REB_BASELINE"]
        ),
        axis=1,
    )
    enriched["PROJ_AST"] = enriched.apply(
        lambda row: calculate_projection(
            row["SEASON_AST"], row["L10_AST"], row["L5_AST"], row["OPP_AST_ALLOWED"], row["LEAGUE_AST_BASELINE"]
        ),
        axis=1,
    )
    enriched["PROJ_PRA"] = enriched.apply(
        lambda row: calculate_projection(
            row["SEASON_PRA"], row["L10_PRA"], row["L5_PRA"], row["OPP_PRA_ALLOWED"], row["LEAGUE_PRA_BASELINE"]
        ),
        axis=1,
    )

    return enriched


def apply_filters(team_df: pd.DataFrame, min_games: int, min_minutes: int, role_filter: str) -> pd.DataFrame:
    filtered = team_df[
        (team_df["SEASON_GP"] >= min_games) & (team_df["SEASON_MIN"] >= min_minutes)
    ].copy()
    if role_filter != "Todos":
        filtered = filtered[filtered["ROLE"] == role_filter].copy()
    return filtered


def filter_and_sort_team_df(
    team_df: pd.DataFrame,
    min_games: int,
    min_minutes: int,
    role_filter: str,
    sort_column: str,
    ascending: bool,
) -> pd.DataFrame:
    if team_df.empty:
        return team_df

    filtered = apply_filters(team_df, min_games, min_minutes, role_filter)
    if filtered.empty:
        return filtered

    if sort_column == "PLAYER":
        filtered = filtered.sort_values(by=["PLAYER", "SEASON_MIN"], ascending=[ascending, False])
    else:
        filtered = filtered.sort_values(by=[sort_column, "SEASON_MIN", "PLAYER"], ascending=[ascending, False, True])
    return filtered.reset_index(drop=True)


def build_display_dataframes(team_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    display_df = team_df.copy()

    display_df["Jogador"] = display_df["PLAYER"]
    display_df["Pos"] = display_df["POSITION"].replace("", "-")
    display_df["Papel"] = display_df["ROLE"]
    display_df["GP"] = display_df["SEASON_GP"]
    display_df["MIN"] = display_df["SEASON_MIN"]

    display_df["PRA Temp"] = display_df["SEASON_PRA"]
    display_df["PRA L5"] = display_df["L5_PRA"]
    display_df["PRA L10"] = display_df["L10_PRA"]
    display_df["Δ PRA L5"] = display_df["DELTA_PRA_L5"]
    display_df["Δ PRA L10"] = display_df["DELTA_PRA_L10"]
    display_df["Trend"] = display_df["TREND"]

    display_df["PTS Temp"] = display_df["SEASON_PTS"]
    display_df["PTS L5"] = display_df["L5_PTS"]
    display_df["PTS L10"] = display_df["L10_PTS"]
    display_df["REB Temp"] = display_df["SEASON_REB"]
    display_df["REB L5"] = display_df["L5_REB"]
    display_df["REB L10"] = display_df["L10_REB"]
    display_df["AST Temp"] = display_df["SEASON_AST"]
    display_df["AST L5"] = display_df["L5_AST"]
    display_df["AST L10"] = display_df["L10_AST"]

    display_df["Proj PRA"] = display_df["PROJ_PRA"]
    display_df["Proj PTS"] = display_df["PROJ_PTS"]
    display_df["Proj REB"] = display_df["PROJ_REB"]
    display_df["Proj AST"] = display_df["PROJ_AST"]
    display_df["Matchup"] = display_df["MATCHUP_LABEL"]
    display_df["Hit PRA"] = display_df["HIT_RATE_L10_TEXT"]
    display_df["Hit PTS"] = display_df["PTS_HIT_RATE_L10_TEXT"]
    display_df["Hit REB"] = display_df["REB_HIT_RATE_L10_TEXT"]
    display_df["Hit AST"] = display_df["AST_HIT_RATE_L10_TEXT"]
    display_df["Sinal"] = display_df["FORM_SIGNAL"]
    display_df["Oscilação"] = display_df["OSC_CLASS"]
    display_df["PRA adv pos"] = display_df["OPP_PRA_ALLOWED"]
    display_df["Liga pos"] = display_df["LEAGUE_PRA_BASELINE"]

    summary_df = display_df[
        [
            "Jogador", "Papel", "GP", "MIN", "PRA Temp", "PRA L10", "Proj PRA", "Δ PRA L10",
            "Matchup", "Hit PRA", "Oscilação", "Sinal", "Trend",
        ]
    ].copy()

    detail_df = display_df[
        [
            "Jogador", "Pos", "Papel", "GP", "MIN",
            "PTS Temp", "PTS L5", "PTS L10", "Proj PTS", "Hit PTS",
            "REB Temp", "REB L5", "REB L10", "Proj REB", "Hit REB",
            "AST Temp", "AST L5", "AST L10", "Proj AST", "Hit AST",
            "PRA Temp", "PRA L5", "PRA L10", "Proj PRA", "Hit PRA",
            "Δ PRA L5", "Δ PRA L10", "PRA adv pos", "Liga pos",
            "Matchup", "Oscilação", "Sinal", "Trend",
        ]
    ].copy()

    return summary_df, detail_df


def style_delta(val) -> str:
    try:
        value = float(val)
    except (TypeError, ValueError):
        return ""
    if value > 0:
        return "background-color: rgba(34,197,94,0.12); color: #dcfce7; font-weight: 600;"
    if value < 0:
        return "background-color: rgba(239,68,68,0.12); color: #fee2e2; font-weight: 600;"
    return "color: #cbd5e1;"


def style_trend(val) -> str:
    if val in ["🔥 Forte", "⬆️ Boa"]:
        return "background-color: rgba(34,197,94,0.10); color: #dcfce7; font-weight: 700;"
    if val in ["🥶 Queda", "⬇️ Fraca"]:
        return "background-color: rgba(239,68,68,0.10); color: #fee2e2; font-weight: 700;"
    return "background-color: rgba(148,163,184,0.10); color: #e2e8f0; font-weight: 600;"


def style_role(val) -> str:
    if "Titular" in str(val):
        return "background-color: rgba(139,92,246,0.12); color: #f3e8ff; font-weight: 700;"
    if "Reserva" in str(val):
        return "background-color: rgba(148,163,184,0.08); color: #cbd5e1; font-weight: 600;"
    return ""


def style_pra(val) -> str:
    return "background-color: rgba(139,92,246,0.10); color: #f5f3ff; font-weight: 700;"


def style_matchup(val) -> str:
    if "Favorável" in str(val):
        return "background-color: rgba(34,197,94,0.10); color: #dcfce7; font-weight: 700;"
    if "Difícil" in str(val):
        return "background-color: rgba(239,68,68,0.10); color: #fee2e2; font-weight: 700;"
    return "background-color: rgba(148,163,184,0.10); color: #e2e8f0; font-weight: 600;"


def style_signal(val) -> str:
    if "↗" in str(val):
        return "color: #86efac; font-weight: 700;"
    if "↘" in str(val):
        return "color: #fca5a5; font-weight: 700;"
    return "color: #e2e8f0; font-weight: 600;"


def style_oscillation(val) -> str:
    if "Baixa" in str(val):
        return "color: #86efac; font-weight: 700;"
    if "Alta" in str(val):
        return "color: #fca5a5; font-weight: 700;"
    return "color: #fcd34d; font-weight: 700;"


def style_hit_rate(val) -> str:
    text = str(val)
    if "/" not in text:
        return ""
    try:
        hit, sample = text.split("/")
        ratio = float(hit) / max(float(sample), 1.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return ""

    if ratio >= 0.7:
        return "background-color: rgba(34,197,94,0.10); color: #dcfce7; font-weight: 700;"
    if ratio <= 0.4:
        return "background-color: rgba(239,68,68,0.10); color: #fee2e2; font-weight: 700;"
    return "background-color: rgba(148,163,184,0.10); color: #e2e8f0; font-weight: 600;"


def style_table(df: pd.DataFrame, quick_view: bool) -> pd.io.formats.style.Styler:
    text_cols = {
        "Jogador", "Pos", "Papel", "Trend", "Matchup", "Hit PRA", "Hit PTS", "Hit REB", "Hit AST", "Oscilação", "Sinal"
    }
    format_map = {}
    for col in df.columns:
        if col == "GP":
            format_map[col] = "{:.0f}"
        elif col not in text_cols:
            format_map[col] = "{:.1f}"

    styler = df.style.format(format_map, na_rep="-")

    pra_cols = [c for c in ["PRA Temp", "PRA L5", "PRA L10", "PRA adv pos", "Liga pos"] if c in df.columns]
    delta_cols = [c for c in ["Δ PRA L5", "Δ PRA L10"] if c in df.columns]
    hit_cols = [c for c in ["Hit PRA", "Hit PTS", "Hit REB", "Hit AST"] if c in df.columns]
    center_cols = [c for c in ["Papel", "GP", "MIN", "Trend", "Matchup", "Oscilação", "Sinal", "Hit PRA", "Hit PTS", "Hit REB", "Hit AST"] if c in df.columns]

    if pra_cols:
        styler = styler.map(style_pra, subset=pra_cols)
    if delta_cols:
        styler = styler.map(style_delta, subset=delta_cols)
    if hit_cols:
        styler = styler.map(style_hit_rate, subset=hit_cols)
    if "Trend" in df.columns:
        styler = styler.map(style_trend, subset=["Trend"])
    if "Papel" in df.columns:
        styler = styler.map(style_role, subset=["Papel"])
    if "Matchup" in df.columns:
        styler = styler.map(style_matchup, subset=["Matchup"])
    if "Sinal" in df.columns:
        styler = styler.map(style_signal, subset=["Sinal"])
    if "Oscilação" in df.columns:
        styler = styler.map(style_oscillation, subset=["Oscilação"])
    if "Jogador" in df.columns:
        styler = styler.set_properties(subset=["Jogador"], **{"font-weight": "700"})
    if center_cols:
        styler = styler.set_properties(subset=center_cols, **{"text-align": "center"})
    if quick_view:
        quick_cols = [c for c in ["PRA Temp", "PRA L10", "Δ PRA L10"] if c in df.columns]
        styler = styler.set_properties(subset=quick_cols, **{"font-weight": "700"})

    return styler


def render_matchup_header(game_row: pd.Series) -> None:
    away_team_id = int(game_row["VISITOR_TEAM_ID"])
    home_team_id = int(game_row["HOME_TEAM_ID"])

    st.markdown('<div class="matchup-shell">', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1.4, 0.9, 1.4])

    with c1:
        st.image(get_team_logo_url(away_team_id), width=96)
        st.markdown(f'<div class="team-title">{game_row["away_team_name"]}</div>', unsafe_allow_html=True)
        st.markdown('<div class="team-sub">Visitante</div>', unsafe_allow_html=True)

    with c2:
        st.markdown('<div class="center-vs">VS</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div style="text-align:center; margin-top:0.7rem;"><span class="status-chip">{game_row["GAME_STATUS_TEXT"]}</span></div>',
            unsafe_allow_html=True,
        )

    with c3:
        st.image(get_team_logo_url(home_team_id), width=96)
        st.markdown(f'<div class="team-title">{game_row["home_team_name"]}</div>', unsafe_allow_html=True)
        st.markdown('<div class="team-sub">Mandante</div>', unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


def build_summary_cards_data(
    away_df: pd.DataFrame,
    home_df: pd.DataFrame,
    min_games: int,
    min_minutes: int,
    role_filter: str,
) -> pd.DataFrame:
    away_filtered = apply_filters(away_df, min_games, min_minutes, role_filter).copy()
    home_filtered = apply_filters(home_df, min_games, min_minutes, role_filter).copy()
    return pd.concat([away_filtered, home_filtered], ignore_index=True)


def render_single_card(
    title: str,
    value: str,
    meta: str,
    left_label: str,
    left_value: str,
    right_label: str,
    right_value: str,
    right_highlight: bool = True,
) -> str:
    right_class = "detail-mini detail-mini-highlight" if right_highlight else "detail-mini"
    return f"""
    <div class="summary-card">
        <div class="summary-label">{title}</div>
        <div class="summary-value">{value}</div>
        <div class="summary-meta">{meta}</div>
        <div class="detail-mini-grid" style="margin-top:0.75rem; grid-template-columns: repeat(2, minmax(0, 1fr));">
            <div class="detail-mini">
                <div class="detail-mini-label">{left_label}</div>
                <div class="detail-mini-value">{left_value}</div>
            </div>
            <div class="{right_class}">
                <div class="detail-mini-label">{right_label}</div>
                <div class="detail-mini-value">{right_value}</div>
            </div>
        </div>
    </div>
    """


def render_summary_cards(
    away_df: pd.DataFrame,
    home_df: pd.DataFrame,
    min_games: int,
    min_minutes: int,
    role_filter: str,
) -> None:
    combined = build_summary_cards_data(away_df, home_df, min_games, min_minutes, role_filter)
    st.subheader("Destaques do confronto")

    if combined.empty:
        st.info("Nenhum jogador passou pelos filtros atuais para montar os cards.")
        return

    best_pra = combined.sort_values("L10_PRA", ascending=False).iloc[0]
    best_delta = combined.sort_values("DELTA_PRA_L10", ascending=False).iloc[0]
    best_matchup = combined.sort_values(["MATCHUP_DIFF", "L10_PRA"], ascending=[False, False]).iloc[0]
    best_consistency = combined.sort_values(["HIT_RATE_L10", "OSC_L10", "L10_PRA"], ascending=[False, True, False]).iloc[0]
    best_signal = combined.sort_values(["L10_PRA", "HIT_RATE_L10"], ascending=[False, False]).iloc[0]

    cards = [
        (
            "PRA L10 líder",
            format_number(best_pra["L10_PRA"]),
            f'{best_pra["PLAYER"]} • {best_pra["TEAM_NAME"]}',
            "Temp",
            format_number(best_pra["SEASON_PRA"]),
            "Hit L10",
            best_pra["HIT_RATE_L10_TEXT"],
        ),
        (
            "Maior alta L10",
            format_signed_number(best_delta["DELTA_PRA_L10"]),
            f'{best_delta["PLAYER"]} • {best_delta["TEAM_NAME"]}',
            "PRA L10",
            format_number(best_delta["L10_PRA"]),
            "Sinal",
            best_delta["FORM_SIGNAL"],
        ),
        (
            "Melhor matchup",
            best_matchup["MATCHUP_LABEL"],
            f'{best_matchup["PLAYER"]} • {best_matchup["TEAM_NAME"]}',
            "PRA ced.",
            format_number(best_matchup["OPP_PRA_ALLOWED"]),
            "Liga",
            format_number(best_matchup["LEAGUE_PRA_BASELINE"]),
        ),
        (
            "Mais consistente",
            best_consistency["HIT_RATE_L10_TEXT"],
            f'{best_consistency["PLAYER"]} • {best_consistency["TEAM_NAME"]}',
            "Osc",
            best_consistency["OSC_CLASS"],
            "PRA L10",
            format_number(best_consistency["L10_PRA"]),
        ),
        (
            "Melhor forma",
            best_signal["FORM_SIGNAL"],
            f'{best_signal["PLAYER"]} • {best_signal["TEAM_NAME"]}',
            "Δ L10",
            format_signed_number(best_signal["DELTA_PRA_L10"]),
            "Hit L10",
            best_signal["HIT_RATE_L10_TEXT"],
        ),
    ]

    cols = st.columns(5)
    for col, card in zip(cols, cards):
        with col:
            st.markdown(
                render_single_card(
                    title=card[0],
                    value=card[1],
                    meta=card[2],
                    left_label=card[3],
                    left_value=card[4],
                    right_label=card[5],
                    right_value=card[6],
                ),
                unsafe_allow_html=True,
            )


def render_player_chart(player_name: str, player_id: int, season: str, chart_mode: str) -> None:
    log = get_player_log(player_id, season)
    if log.empty:
        st.info("Sem histórico suficiente para esse jogador.")
        return

    needed_cols = ["GAME_DATE", "PTS", "REB", "AST"]
    if "MATCHUP" in log.columns:
        needed_cols.append("MATCHUP")

    recent = log[needed_cols].copy()
    recent = recent.dropna(subset=["GAME_DATE", "PTS", "REB", "AST"]).sort_values("GAME_DATE")
    if recent.empty:
        st.info("Sem histórico suficiente para esse jogador.")
        return

    recent["PRA"] = recent["PTS"] + recent["REB"] + recent["AST"]
    if "MATCHUP" in recent.columns:
        matchup_parts = recent["MATCHUP"].apply(get_matchup_parts)
        recent["VENUE"] = matchup_parts.apply(lambda x: x[0])
        recent["OPP_ABBR"] = matchup_parts.apply(lambda x: x[1])
    else:
        recent["VENUE"] = ""
        recent["OPP_ABBR"] = ""

    recent["SHORT_LABEL"] = recent.apply(
        lambda row: (
            f'{row["GAME_DATE"].strftime("%m/%d")}<br>{row["VENUE"]} {row["OPP_ABBR"]}'.strip()
            if row["OPP_ABBR"] else row["GAME_DATE"].strftime("%m/%d")
        ),
        axis=1,
    )

    top_left, top_right = st.columns([1, 6])
    with top_left:
        st.image(get_player_headshot_url(int(player_id)), width=82)
    with top_right:
        st.markdown(f"### Últimos jogos — {player_name}")
        st.caption("Visual compacto: barras, últimos 5 jogos." if chart_mode == "Compacto" else "Visual completo: linhas, últimos 10 jogos.")

    if chart_mode == "Compacto":
        metric = st.radio(
            "Métrica do gráfico",
            ["PRA", "PTS", "REB", "AST"],
            horizontal=True,
            key=f"metric_chart_{player_id}_{chart_mode}",
        )
        recent_view = recent.tail(5).copy()

        fig = go.Figure(
            go.Bar(
                x=recent_view["SHORT_LABEL"],
                y=recent_view[metric],
                text=recent_view[metric].round(1),
                textposition="outside",
                marker=dict(color="#4ade80"),
                hovertemplate=f"{metric}: %{{y:.1f}}<extra></extra>",
            )
        )
        fig.update_layout(
            template="plotly_dark",
            height=360,
            margin=dict(l=20, r=20, t=10, b=20),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(15,23,42,0.35)",
            showlegend=False,
            bargap=0.28,
        )
        fig.update_xaxes(title="", type="category", tickangle=0, showgrid=False, tickfont=dict(size=11))
        fig.update_yaxes(title="", showgrid=True, gridcolor="rgba(148,163,184,0.15)", zeroline=False)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"{metric} • Temp: {recent[metric].mean():.1f} | L5: {recent_view[metric].mean():.1f}")
    else:
        recent_view = recent.tail(10).copy()
        fig = go.Figure()
        for name, color, width, opacity in [
            ("PRA", "#8b5cf6", 4, 1.0),
            ("PTS", "#38bdf8", 2.2, 0.8),
            ("REB", "#34d399", 2.2, 0.8),
            ("AST", "#f59e0b", 2.2, 0.8),
        ]:
            fig.add_trace(
                go.Scatter(
                    x=recent_view["SHORT_LABEL"],
                    y=recent_view[name],
                    mode="lines+markers",
                    name=name,
                    line=dict(width=width, color=color),
                    opacity=opacity,
                    hovertemplate=f"{name}: %{{y:.1f}}<extra></extra>",
                )
            )

        fig.update_layout(
            template="plotly_dark",
            height=400,
            margin=dict(l=20, r=20, t=10, b=20),
            legend=dict(orientation="h", y=1.08, x=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(15,23,42,0.35)",
            hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155", font=dict(color="#f8fafc", size=13)),
        )
        fig.update_xaxes(title="", type="category", tickangle=0, showgrid=False, tickfont=dict(size=11))
        fig.update_yaxes(title="")
        st.plotly_chart(fig, use_container_width=True)


def render_badges(role: str, momentum: str, oscillation: str, matchup: str) -> None:
    role_class = "badge-starter" if role == "Titular provável" else "badge-bench"

    if str(momentum).startswith(("↗", "↑")):
        momentum_class = "badge-good"
    elif str(momentum).startswith(("↘", "↓")):
        momentum_class = "badge-bad"
    else:
        momentum_class = "badge-neutral"

    if oscillation == "Baixa":
        oscillation_class = "badge-good"
    elif oscillation == "Alta":
        oscillation_class = "badge-bad"
    else:
        oscillation_class = "badge-neutral"

    if matchup == "Favorável":
        matchup_class = "badge-good"
    elif matchup == "Difícil":
        matchup_class = "badge-bad"
    else:
        matchup_class = "badge-neutral"

    st.markdown(
        f"""
        <div class="badge-row">
            <span class="badge {role_class}">{role}</span>
            <span class="badge {momentum_class}">{momentum}</span>
            <span class="badge {oscillation_class}">Osc {oscillation}</span>
            <span class="badge {matchup_class}">Matchup {matchup}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def build_delta_pill_html(label: str, value: float) -> str:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        numeric_value = 0.0

    if numeric_value > 0.3:
        css_class = "delta-up"
    elif numeric_value < -0.3:
        css_class = "delta-down"
    else:
        css_class = "delta-flat"

    return f'<span class="delta-pill {css_class}">{label} {format_signed_number(numeric_value)}</span>'


def render_detail_metric_box_html(title: str, temp_val: float, l5_val: float, l10_val: float) -> str:
    delta_l5 = l5_val - temp_val
    delta_l10 = l10_val - temp_val
    return f"""
    <div class="detail-box">
        <div class="detail-box-top">
            <div class="detail-box-title">{title}</div>
            <div class="delta-pill-row">
                {build_delta_pill_html('Δ L5', delta_l5)}
                {build_delta_pill_html('Δ L10', delta_l10)}
            </div>
        </div>
        <div class="detail-mini-grid">
            <div class="detail-mini">
                <div class="detail-mini-label">Temp</div>
                <div class="detail-mini-value">{format_number(temp_val)}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">L5</div>
                <div class="detail-mini-value">{format_number(l5_val)}</div>
            </div>
            <div class="detail-mini detail-mini-highlight">
                <div class="detail-mini-label">L10</div>
                <div class="detail-mini-value">{format_number(l10_val)}</div>
            </div>
        </div>
    </div>
    """


def render_player_headline_html(row: pd.Series) -> str:
    hit_rate_pct = int(round(float(row.get("HIT_RATE_L10", 0.0)) * 100))
    hit_rate_text = row.get("HIT_RATE_L10_TEXT", "-")
    form_signal = row.get("FORM_SIGNAL", "→ Estável")
    osc_class = row.get("OSC_CLASS", "-")
    matchup_class = get_matchup_chip_class(row.get("MATCHUP_LABEL", "Neutro"))

    return f"""
    <div class="player-headline-card">
        <div class="player-headline-label">Leitura em 3 segundos</div>
        <div class="player-headline-value">{format_number(row['L10_PRA'])}</div>
        <div class="player-headline-sub">
            PRA L10 • Proj {format_number(row['PROJ_PRA'])} • Δ vs temp {format_signed_number(row['DELTA_PRA_L10'])}
            • Hit L10 {hit_rate_text} ({hit_rate_pct}%) • Oscilação {osc_class} • {form_signal}
        </div>
        <div class="hero-note">
            {row['OPP_TEAM_NAME']} cede {format_number(row['OPP_PRA_ALLOWED'])} PRA para {row['POSITION_GROUP']} • liga {format_number(row['LEAGUE_PRA_BASELINE'])}
            <span class="matchup-chip {matchup_class}" style="margin-left:0.4rem;">{row['MATCHUP_LABEL']} vs {row['POSITION_GROUP']}</span>
        </div>
    </div>
    """


def render_player_support_tiles(row: pd.Series, line_metric: str, line_value: float) -> None:
    matchup_class = "quick-stat"
    if row["MATCHUP_LABEL"] == "Favorável":
        matchup_class = "quick-stat quick-stat-up"
    elif row["MATCHUP_LABEL"] == "Difícil":
        matchup_class = "quick-stat quick-stat-down"

    line_context = get_line_context(row, line_metric, line_value)
    if line_context["edge"] > 0.75:
        line_class = "quick-stat quick-stat-up"
    elif line_context["edge"] < -0.75:
        line_class = "quick-stat quick-stat-down"
    else:
        line_class = "quick-stat quick-stat-primary"

    pts_hit = row.get("PTS_HIT_RATE_L10_TEXT", "-")
    reb_hit = row.get("REB_HIT_RATE_L10_TEXT", "-")
    ast_hit = row.get("AST_HIT_RATE_L10_TEXT", "-")

    st.markdown(
        f"""
        <div class="player-quick-grid">
            <div class="quick-stat">
                <div class="quick-stat-label">PTS L10</div>
                <div class="quick-stat-value">{format_number(row['L10_PTS'])}</div>
                <div class="quick-stat-meta">Proj {format_number(row['PROJ_PTS'])} • Hit {pts_hit}</div>
            </div>
            <div class="quick-stat">
                <div class="quick-stat-label">REB L10</div>
                <div class="quick-stat-value">{format_number(row['L10_REB'])}</div>
                <div class="quick-stat-meta">Proj {format_number(row['PROJ_REB'])} • Hit {reb_hit}</div>
            </div>
            <div class="quick-stat">
                <div class="quick-stat-label">AST L10</div>
                <div class="quick-stat-value">{format_number(row['L10_AST'])}</div>
                <div class="quick-stat-meta">Proj {format_number(row['PROJ_AST'])} • Hit {ast_hit}</div>
            </div>
            <div class="{matchup_class}">
                <div class="quick-stat-label">Matchup</div>
                <div class="quick-stat-value">{row['MATCHUP_LABEL']}</div>
                <div class="quick-stat-meta">PRA cedido {format_number(row['OPP_PRA_ALLOWED'])} • diff {format_signed_number(row['MATCHUP_DIFF'])}</div>
            </div>
            <div class="{line_class}">
                <div class="quick-stat-label">Linha {line_metric}</div>
                <div class="quick-stat-value">{format_signed_number(line_context['edge'])}</div>
                <div class="quick-stat-meta">Proj {format_number(line_context['projection'])} vs {format_number(line_value)} • L10 {line_context['hit_l10']}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_projection_detail_box_html(row: pd.Series) -> str:
    return f"""
    <div class="detail-box">
        <div class="detail-box-top">
            <div class="detail-box-title">Projeções do modelo</div>
            <div class="delta-pill-row">
                <span class="delta-pill delta-flat">peso L10 maior</span>
            </div>
        </div>
        <div class="detail-mini-grid" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
            <div class="detail-mini">
                <div class="detail-mini-label">Proj PTS</div>
                <div class="detail-mini-value">{format_number(row['PROJ_PTS'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Proj REB</div>
                <div class="detail-mini-value">{format_number(row['PROJ_REB'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Proj AST</div>
                <div class="detail-mini-value">{format_number(row['PROJ_AST'])}</div>
            </div>
            <div class="detail-mini detail-mini-highlight">
                <div class="detail-mini-label">Proj PRA</div>
                <div class="detail-mini-value">{format_number(row['PROJ_PRA'])}</div>
            </div>
        </div>
    </div>
    """


def render_manual_line_detail_box_html(row: pd.Series, line_metric: str, line_value: float) -> str:
    line_context = get_line_context(row, line_metric, line_value)
    line_chip_class = "delta-flat"
    if line_context["edge"] > 0.75:
        line_chip_class = "delta-up"
    elif line_context["edge"] < -0.75:
        line_chip_class = "delta-down"

    return f"""
    <div class="detail-box">
        <div class="detail-box-top">
            <div class="detail-box-title">Linha manual — {line_metric}</div>
            <div class="delta-pill-row">
                <span class="delta-pill {line_chip_class}">{line_context['label']}</span>
                <span class="delta-pill delta-flat">Linha {format_number(line_value)}</span>
            </div>
        </div>
        <div class="detail-mini-grid" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
            <div class="detail-mini">
                <div class="detail-mini-label">Projeção</div>
                <div class="detail-mini-value">{format_number(line_context['projection'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Edge</div>
                <div class="detail-mini-value">{format_signed_number(line_context['edge'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Hit L5</div>
                <div class="detail-mini-value">{line_context['hit_l5']}</div>
            </div>
            <div class="detail-mini detail-mini-highlight">
                <div class="detail-mini-label">Hit L10</div>
                <div class="detail-mini-value">{line_context['hit_l10']}</div>
            </div>
        </div>
    </div>
    """


def render_matchup_detail_box_html(row: pd.Series) -> str:
    matchup_class = get_matchup_chip_class(row["MATCHUP_LABEL"])
    return f"""
    <div class="detail-box">
        <div class="detail-box-top">
            <div class="detail-box-title">Contexto do adversário</div>
            <div class="delta-pill-row">
                <span class="matchup-chip {matchup_class}">{row['MATCHUP_LABEL']}</span>
            </div>
        </div>
        <div class="detail-mini-grid" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
            <div class="detail-mini">
                <div class="detail-mini-label">PTS ced.</div>
                <div class="detail-mini-value">{format_number(row['OPP_PTS_ALLOWED'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">REB ced.</div>
                <div class="detail-mini-value">{format_number(row['OPP_REB_ALLOWED'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">AST ced.</div>
                <div class="detail-mini-value">{format_number(row['OPP_AST_ALLOWED'])}</div>
            </div>
            <div class="detail-mini detail-mini-highlight">
                <div class="detail-mini-label">PRA ced.</div>
                <div class="detail-mini-value">{format_number(row['OPP_PRA_ALLOWED'])}</div>
            </div>
        </div>
        <div class="hero-note">{row['OPP_TEAM_NAME']} vs {row['POSITION_GROUP']} • liga {format_number(row['LEAGUE_PRA_BASELINE'])} • diferença {format_signed_number(row['MATCHUP_DIFF'])}</div>
    </div>
    """


def render_player_card(row: pd.Series, line_metric: str, line_value: float) -> None:
    with st.container(border=True):
        top_left, top_right = st.columns([1, 4])

        with top_left:
            st.image(get_player_headshot_url(int(row["PLAYER_ID"])), width=78)

        with top_right:
            st.markdown(f"**{row['PLAYER']}**")
            position = row["POSITION"] if str(row["POSITION"]).strip() else "-"
            st.caption(f"Pos {position} • GP {int(row['SEASON_GP'])} • MIN {format_number(row['SEASON_MIN'])}")
            st.markdown(render_player_headline_html(row), unsafe_allow_html=True)
            render_badges(
                row["ROLE"],
                row.get("FORM_SIGNAL", "→ Estável"),
                row.get("OSC_CLASS", "-"),
                row.get("MATCHUP_LABEL", "Neutro"),
            )

        render_player_support_tiles(row, line_metric, line_value)

        with st.expander("Ver detalhamento completo"):
            first_cols = st.columns(2)
            second_cols = st.columns(2)
            detail_items = [
                ("PRA", row["SEASON_PRA"], row["L5_PRA"], row["L10_PRA"]),
                ("PTS", row["SEASON_PTS"], row["L5_PTS"], row["L10_PTS"]),
                ("REB", row["SEASON_REB"], row["L5_REB"], row["L10_REB"]),
                ("AST", row["SEASON_AST"], row["L5_AST"], row["L10_AST"]),
            ]
            for col, item in zip([*first_cols, *second_cols], detail_items):
                with col:
                    st.markdown(render_detail_metric_box_html(item[0], item[1], item[2], item[3]), unsafe_allow_html=True)

            st.markdown(render_projection_detail_box_html(row), unsafe_allow_html=True)
            st.markdown(render_manual_line_detail_box_html(row, line_metric, line_value), unsafe_allow_html=True)
            st.markdown(render_matchup_detail_box_html(row), unsafe_allow_html=True)


def render_player_cards_grid(
    filtered_df: pd.DataFrame,
    line_metric: str,
    line_value: float,
    cards_per_row: int = 2,
) -> None:
    rows = [filtered_df.iloc[i:i + cards_per_row] for i in range(0, len(filtered_df), cards_per_row)]
    for row_df in rows:
        cols = st.columns(cards_per_row)
        for col_idx in range(cards_per_row):
            with cols[col_idx]:
                if col_idx < len(row_df):
                    render_player_card(row_df.iloc[col_idx], line_metric, line_value)


def render_team_section(
    team_name: str,
    team_df: pd.DataFrame,
    season: str,
    min_games: int,
    min_minutes: int,
    role_filter: str,
    sort_label: str,
    ascending: bool,
    view_mode: str,
    chart_mode: str,
    line_metric: str,
    line_value: float,
    cards_per_row: int,
) -> None:
    st.subheader(team_name)
    if team_df.empty:
        st.warning("Não consegui montar os dados desse time.")
        return

    sort_column = SORT_OPTIONS[sort_label]
    filtered_df = filter_and_sort_team_df(
        team_df=team_df,
        min_games=min_games,
        min_minutes=min_minutes,
        role_filter=role_filter,
        sort_column=sort_column,
        ascending=ascending,
    )
    if filtered_df.empty:
        st.warning("Nenhum jogador passou pelos filtros. Você apertou demais o funil, pequeno fiscal da amostra.")
        return

    st.markdown(
        f"""
        <div class="info-pill">Jogadores exibidos: {len(filtered_df)}</div>
        <div class="info-pill">GP mínimo: {min_games}</div>
        <div class="info-pill">MIN mínimo: {min_minutes}</div>
        <div class="info-pill">Papel: {role_filter}</div>
        <div class="info-pill">Ordenação: {sort_label}</div>
        <div class="info-pill">Visualização: {view_mode}</div>
        <div class="info-pill">Linha manual: {line_metric} {format_number(line_value)}</div>
        <div class="info-pill">Adversário: {filtered_df['OPP_TEAM_NAME'].iloc[0]}</div>
        """,
        unsafe_allow_html=True,
    )

    if view_mode == "Cards":
        st.markdown(
            '<div class="section-note">Cards com leitura principal de PRA, projeção, matchup por posição, hits de PRA/PTS/REB/AST e linha manual.</div>',
            unsafe_allow_html=True,
        )
        render_player_cards_grid(filtered_df, line_metric=line_metric, line_value=line_value, cards_per_row=cards_per_row)
    else:
        summary_df, detail_df = build_display_dataframes(filtered_df)
        quick_tab, detail_tab = st.tabs(["Leitura rápida", "Detalhamento"])
        with quick_tab:
            st.markdown(
                '<div class="section-note">Aqui o foco é no que bate rápido no olho: PRA, projeção, matchup, hit PRA, oscilação e sinal de forma.</div>',
                unsafe_allow_html=True,
            )
            st.dataframe(style_table(summary_df, quick_view=True), use_container_width=True, hide_index=True)
        with detail_tab:
            st.markdown(
                '<div class="section-note">Aqui entra a parte mais detalhada: PTS, REB, AST, PRA, projeções e hit rate separado por atributo.</div>',
                unsafe_allow_html=True,
            )
            st.dataframe(style_table(detail_df, quick_view=False), use_container_width=True, hide_index=True)

    options = filtered_df[["PLAYER", "PLAYER_ID"]].drop_duplicates()
    player_name = st.selectbox(
        f"Ver gráfico de jogador — {team_name}",
        options["PLAYER"].tolist(),
        key=f"player_select_{team_name}_{view_mode}_{chart_mode}",
    )
    selected_player_id = int(options.loc[options["PLAYER"] == player_name, "PLAYER_ID"].iloc[0])
    render_player_chart(player_name, selected_player_id, season, chart_mode)


def main() -> None:
    inject_css()

    st.markdown('<div class="main-title">NBA Dashboard MVP</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtitle">Escolha o jogo e veja PTS, REB, AST e PRA com cards ou tabela, sem sofrer à toa.</div>',
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Configurações")
        selected_date = st.date_input("Data dos jogos", value=date.today())

        st.divider()
        st.subheader("Visualização")
        view_mode = st.radio("Modo de exibição", VIEW_OPTIONS, index=0)
        chart_mode = st.radio("Modo do gráfico", CHART_OPTIONS, index=0)
        cards_per_row = st.select_slider("Cards por linha", options=[1, 2], value=2)

        st.divider()
        st.subheader("Filtros")
        min_games = st.slider("Mínimo de jogos na temporada", 0, 82, 5, 1)
        min_minutes = st.slider("Mínimo de minutos por jogo", 0, 40, 15, 1)
        role_filter = st.selectbox("Mostrar jogadores", ROLE_OPTIONS, index=0)

        st.divider()
        st.subheader("Linha manual")
        line_metric = st.selectbox("Métrica da linha", LINE_METRIC_OPTIONS, index=0)
        default_line_map = {"PRA": 25.5, "PTS": 20.5, "REB": 7.5, "AST": 5.5}
        line_value = st.number_input(
            "Valor da linha",
            min_value=0.0,
            value=float(default_line_map[line_metric]),
            step=0.5,
            key=f"manual_line_{line_metric}",
        )

        st.divider()
        st.subheader("Ordenação")
        sort_labels = list(SORT_OPTIONS.keys())
        sort_label = st.selectbox("Ordenar jogadores por", options=sort_labels, index=sort_labels.index("PRA L10"))
        ascending = st.toggle("Ordem crescente", value=False)

        st.divider()
        st.caption("Este app busca os dados ao abrir a página.")
        if st.button("Forçar atualização"):
            st.cache_data.clear()
            st.rerun()

    season = get_season_string(selected_date)

    try:
        games = get_games_for_date(selected_date)
    except Exception as exc:
        st.error("Deu ruim na consulta da NBA. A fonte externa, como sempre, decidiu ter personalidade.")
        st.exception(exc)
        return

    st.caption(f"Temporada detectada: {season}")

    if games.empty:
        st.warning("Não encontrei jogos nessa data. A NBA também sabe sabotar entretenimento.")
        return

    game_label = st.selectbox("Escolha o jogo", games["label"].tolist())
    selected_game = games.loc[games["label"] == game_label].iloc[0]

    try:
        away_df = build_team_table(int(selected_game["VISITOR_TEAM_ID"]), season)
        home_df = build_team_table(int(selected_game["HOME_TEAM_ID"]), season)

        away_df = enrich_team_with_context(
            team_df=away_df,
            team_id=int(selected_game["VISITOR_TEAM_ID"]),
            opponent_team_id=int(selected_game["HOME_TEAM_ID"]),
            opponent_team_name=selected_game["home_team_name"],
            season=season,
        )
        home_df = enrich_team_with_context(
            team_df=home_df,
            team_id=int(selected_game["HOME_TEAM_ID"]),
            opponent_team_id=int(selected_game["VISITOR_TEAM_ID"]),
            opponent_team_name=selected_game["away_team_name"],
            season=season,
        )
    except Exception as exc:
        st.error("Consegui pegar o jogo, mas a coleta das estatísticas falhou. MVP grátis também tem seus surtos.")
        st.exception(exc)
        return

    away_df["TEAM_NAME"] = selected_game["away_team_name"]
    home_df["TEAM_NAME"] = selected_game["home_team_name"]

    render_matchup_header(selected_game)
    render_summary_cards(
        away_df=away_df,
        home_df=home_df,
        min_games=min_games,
        min_minutes=min_minutes,
        role_filter=role_filter,
    )

    tab1, tab2 = st.tabs([selected_game["away_team_name"], selected_game["home_team_name"]])

    with tab1:
        render_team_section(
            team_name=selected_game["away_team_name"],
            team_df=away_df,
            season=season,
            min_games=min_games,
            min_minutes=min_minutes,
            role_filter=role_filter,
            sort_label=sort_label,
            ascending=ascending,
            view_mode=view_mode,
            chart_mode=chart_mode,
            line_metric=line_metric,
            line_value=line_value,
            cards_per_row=cards_per_row,
        )

    with tab2:
        render_team_section(
            team_name=selected_game["home_team_name"],
            team_df=home_df,
            season=season,
            min_games=min_games,
            min_minutes=min_minutes,
            role_filter=role_filter,
            sort_label=sort_label,
            ascending=ascending,
            view_mode=view_mode,
            chart_mode=chart_mode,
            line_metric=line_metric,
            line_value=line_value,
            cards_per_row=cards_per_row,
        )

    st.markdown(
        """
        <div class="small-note">
        Nota: "Titular provável" neste MVP significa os 5 jogadores com mais minutos por jogo na temporada.
        É um atalho útil para análise, não a escalação oficial confirmada do jogo.
        </div>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()

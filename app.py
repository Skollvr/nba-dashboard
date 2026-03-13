from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from nba_api.stats.endpoints import (
    commonteamroster,
    leaguedashplayerstats,
    playergamelog,
    scoreboardv2,
)
from nba_api.stats.static import teams

st.set_page_config(
    page_title="NBA Dashboard MVP",
    page_icon="🏀",
    layout="wide",
)

TEAM_LOOKUP = {team["id"]: team for team in teams.get_teams()}
TEAM_ABBR_LOOKUP = {team["abbreviation"]: team for team in teams.get_teams()}

TEAM_LOGO_URL = "https://cdn.nba.com/logos/nba/{team_id}/primary/L/logo.svg"
PLAYER_HEADSHOT_URL = "https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"

SORT_OPTIONS = {
    "PRA L5": "L5_PRA",
    "PRA L10": "L10_PRA",
    "PRA temporada": "SEASON_PRA",
    "Δ PRA L5 vs Temp": "DELTA_PRA_L5",
    "Δ PRA L10 vs Temp": "DELTA_PRA_L10",
    "PTS L5": "L5_PTS",
    "PTS L10": "L10_PTS",
    "PTS temporada": "SEASON_PTS",
    "REB L5": "L5_REB",
    "REB L10": "L10_REB",
    "REB temporada": "SEASON_REB",
    "AST L5": "L5_AST",
    "AST L10": "L10_AST",
    "AST temporada": "SEASON_AST",
    "Minutos por jogo": "SEASON_MIN",
    "Jogos na temporada": "SEASON_GP",
    "Nome do jogador": "PLAYER",
}

ROLE_OPTIONS = ["Todos", "Titular provável", "Reserva"]
VIEW_OPTIONS = ["Desktop", "Mobile"]


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


def get_opponent_label(matchup: str) -> str:
    if not isinstance(matchup, str) or matchup.strip() == "":
        return ""

    cleaned = matchup.replace("vs.", "vs").strip()
    parts = cleaned.split()

    if len(parts) < 3:
        return ""

    venue = "vs" if "vs" in parts else "@"
    opponent_abbr = parts[-1].strip().upper()

    opponent_team = TEAM_ABBR_LOOKUP.get(opponent_abbr, {})
    opponent_name = opponent_team.get("nickname") or opponent_team.get("full_name") or opponent_abbr

    return f"{venue} {opponent_name}"


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
        .summary-extra {
            color: #94a3b8;
            font-size: 0.82rem;
            margin-top: 0.25rem;
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
    available_cols = [c for c in keep_cols if c in df.columns]
    return df[available_cols].copy()


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
    df = df.sort_values("GAME_DATE", ascending=False)
    return df


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

    if season_stats.empty:
        season_view = pd.DataFrame(
            columns=["PLAYER_ID", "SEASON_GP", "SEASON_MIN", "SEASON_PTS", "SEASON_REB", "SEASON_AST"]
        )
    else:
        season_view = season_stats.rename(
            columns={
                "GP": "SEASON_GP",
                "MIN": "SEASON_MIN",
                "PTS": "SEASON_PTS",
                "REB": "SEASON_REB",
                "AST": "SEASON_AST",
            }
        )

    if last5_stats.empty:
        last5_view = pd.DataFrame(
            columns=["PLAYER_ID", "L5_GP", "L5_MIN", "L5_PTS", "L5_REB", "L5_AST"]
        )
    else:
        last5_view = last5_stats.rename(
            columns={
                "GP": "L5_GP",
                "MIN": "L5_MIN",
                "PTS": "L5_PTS",
                "REB": "L5_REB",
                "AST": "L5_AST",
            }
        )

    if last10_stats.empty:
        last10_view = pd.DataFrame(
            columns=["PLAYER_ID", "L10_GP", "L10_MIN", "L10_PTS", "L10_REB", "L10_AST"]
        )
    else:
        last10_view = last10_stats.rename(
            columns={
                "GP": "L10_GP",
                "MIN": "L10_MIN",
                "PTS": "L10_PTS",
                "REB": "L10_REB",
                "AST": "L10_AST",
            }
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
        "SEASON_GP",
        "SEASON_MIN",
        "SEASON_PTS",
        "SEASON_REB",
        "SEASON_AST",
        "L5_GP",
        "L5_MIN",
        "L5_PTS",
        "L5_REB",
        "L5_AST",
        "L10_GP",
        "L10_MIN",
        "L10_PTS",
        "L10_REB",
        "L10_AST",
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

    def classify_form(delta_pra_l5: float) -> str:
        if delta_pra_l5 >= 3.0:
            return "🔥 Forte"
        if delta_pra_l5 >= 1.0:
            return "⬆️ Boa"
        if delta_pra_l5 <= -3.0:
            return "🥶 Queda"
        if delta_pra_l5 <= -1.0:
            return "⬇️ Fraca"
        return "➖ Neutra"

    team_df["TREND"] = team_df["DELTA_PRA_L5"].apply(classify_form)

    team_df["ROLE"] = "Reserva"
    starter_pool = team_df.sort_values(
        by=["SEASON_MIN", "SEASON_GP", "PLAYER"],
        ascending=[False, False, True],
    ).head(5)
    starter_ids = starter_pool["PLAYER_ID"].tolist()
    team_df.loc[team_df["PLAYER_ID"].isin(starter_ids), "ROLE"] = "Titular provável"

    return team_df[
        [
            "PLAYER_ID",
            "PLAYER",
            "POSITION",
            "ROLE",
            "SEASON_GP",
            "SEASON_MIN",
            "SEASON_PTS",
            "L5_PTS",
            "L10_PTS",
            "SEASON_REB",
            "L5_REB",
            "L10_REB",
            "SEASON_AST",
            "L5_AST",
            "L10_AST",
            "SEASON_PRA",
            "L5_PRA",
            "L10_PRA",
            "DELTA_PRA_L5",
            "DELTA_PRA_L10",
            "TREND",
        ]
    ].copy()


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
        filtered = filtered.sort_values(
            by=["PLAYER", "SEASON_MIN"],
            ascending=[ascending, False],
        )
    else:
        filtered = filtered.sort_values(
            by=[sort_column, "SEASON_MIN", "PLAYER"],
            ascending=[ascending, False, True],
        )

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

    summary_df = display_df[
        [
            "Jogador",
            "Papel",
            "GP",
            "MIN",
            "PRA Temp",
            "PRA L5",
            "PRA L10",
            "Δ PRA L5",
            "Δ PRA L10",
            "Trend",
        ]
    ].copy()

    detail_df = display_df[
        [
            "Jogador",
            "Pos",
            "Papel",
            "GP",
            "MIN",
            "PTS Temp",
            "PTS L5",
            "PTS L10",
            "REB Temp",
            "REB L5",
            "REB L10",
            "AST Temp",
            "AST L5",
            "AST L10",
            "PRA Temp",
            "PRA L5",
            "PRA L10",
            "Δ PRA L5",
            "Δ PRA L10",
            "Trend",
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


def style_table(df: pd.DataFrame, quick_view: bool) -> pd.io.formats.style.Styler:
    format_map = {}
    for col in df.columns:
        if col == "GP":
            format_map[col] = "{:.0f}"
        elif col not in ["Jogador", "Pos", "Papel", "Trend"]:
            format_map[col] = "{:.1f}"

    styler = df.style.format(format_map, na_rep="-")

    pra_cols = [c for c in ["PRA Temp", "PRA L5", "PRA L10"] if c in df.columns]
    delta_cols = [c for c in ["Δ PRA L5", "Δ PRA L10"] if c in df.columns]
    center_cols = [c for c in ["Papel", "GP", "MIN", "Trend"] if c in df.columns]

    if pra_cols:
        styler = styler.map(style_pra, subset=pra_cols)

    if delta_cols:
        styler = styler.map(style_delta, subset=delta_cols)

    if "Trend" in df.columns:
        styler = styler.map(style_trend, subset=["Trend"])

    if "Papel" in df.columns:
        styler = styler.map(style_role, subset=["Papel"])

    if "Jogador" in df.columns:
        styler = styler.set_properties(subset=["Jogador"], **{"font-weight": "700"})

    if center_cols:
        styler = styler.set_properties(subset=center_cols, **{"text-align": "center"})

    if quick_view:
        quick_cols = [c for c in ["PRA Temp", "PRA L5", "PRA L10", "Δ PRA L5", "Δ PRA L10"] if c in df.columns]
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

    combined = pd.concat([away_filtered, home_filtered], ignore_index=True)

    if combined.empty:
        return combined

    return combined


def render_single_card(title: str, value: str, meta: str, extra: str = "") -> str:
    return f"""
    <div class="summary-card">
        <div class="summary-label">{title}</div>
        <div class="summary-value">{value}</div>
        <div class="summary-meta">{meta}</div>
        <div class="summary-extra">{extra}</div>
    </div>
    """


def render_summary_cards(
    away_df: pd.DataFrame,
    home_df: pd.DataFrame,
    min_games: int,
    min_minutes: int,
    role_filter: str,
) -> None:
    combined = build_summary_cards_data(
        away_df=away_df,
        home_df=home_df,
        min_games=min_games,
        min_minutes=min_minutes,
        role_filter=role_filter,
    )

    st.subheader("Destaques do confronto")

    if combined.empty:
        st.info("Nenhum jogador passou pelos filtros atuais para montar os cards.")
        return

    best_pra = combined.sort_values("L5_PRA", ascending=False).iloc[0]
    best_delta = combined.sort_values("DELTA_PRA_L5", ascending=False).iloc[0]
    best_pts = combined.sort_values("L5_PTS", ascending=False).iloc[0]
    best_reb = combined.sort_values("L5_REB", ascending=False).iloc[0]
    best_ast = combined.sort_values("L5_AST", ascending=False).iloc[0]

    cols = st.columns(5)

    cards = [
        (
            "PRA L5 líder",
            format_number(best_pra["L5_PRA"]),
            f'{best_pra["PLAYER"]} • {best_pra["TEAM_NAME"]}',
            f'Temporada: {format_number(best_pra["SEASON_PRA"])}',
        ),
        (
            "Maior alta L5",
            format_signed_number(best_delta["DELTA_PRA_L5"]),
            f'{best_delta["PLAYER"]} • {best_delta["TEAM_NAME"]}',
            f'PRA L5: {format_number(best_delta["L5_PRA"])}',
        ),
        (
            "Pontos L5",
            format_number(best_pts["L5_PTS"]),
            f'{best_pts["PLAYER"]} • {best_pts["TEAM_NAME"]}',
            f'Temporada: {format_number(best_pts["SEASON_PTS"])}',
        ),
        (
            "Rebotes L5",
            format_number(best_reb["L5_REB"]),
            f'{best_reb["PLAYER"]} • {best_reb["TEAM_NAME"]}',
            f'Temporada: {format_number(best_reb["SEASON_REB"])}',
        ),
        (
            "Assistências L5",
            format_number(best_ast["L5_AST"]),
            f'{best_ast["PLAYER"]} • {best_ast["TEAM_NAME"]}',
            f'Temporada: {format_number(best_ast["SEASON_AST"])}',
        ),
    ]

    for col, card in zip(cols, cards):
        with col:
            st.markdown(
                render_single_card(
                    title=card[0],
                    value=card[1],
                    meta=card[2],
                    extra=card[3],
                ),
                unsafe_allow_html=True,
            )

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


def render_player_chart(player_name: str, player_id: int, season: str, view_mode: str) -> None:
    log = get_player_log(player_id, season)

    if log.empty:
        st.info("Sem histórico suficiente para esse jogador.")
        return

    needed_cols = ["GAME_DATE", "PTS", "REB", "AST"]
    if "MATCHUP" in log.columns:
        needed_cols.append("MATCHUP")

    recent = log[needed_cols].copy()
    recent = recent.dropna(subset=["GAME_DATE", "PTS", "REB", "AST"])
    recent = recent.sort_values("GAME_DATE")

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
            if row["OPP_ABBR"]
            else row["GAME_DATE"].strftime("%m/%d")
        ),
        axis=1,
    )

    top_left, top_right = st.columns([1, 5])

    with top_left:
        st.image(get_player_headshot_url(int(player_id)), width=78)

    with top_right:
        st.markdown(f"### Últimos jogos — {player_name}")
        st.caption("No mobile, o gráfico mostra os últimos 5 jogos em barras. No desktop, mantém a visão de 10 jogos.")

    if view_mode == "Mobile":
        metric = st.radio(
            "Métrica do gráfico",
            ["PRA", "PTS", "REB", "AST"],
            horizontal=True,
            key=f"metric_chart_{player_id}_{view_mode}",
        )

        recent_mobile = recent.tail(5).copy()

        fig = go.Figure(
            go.Bar(
                x=recent_mobile["SHORT_LABEL"],
                y=recent_mobile[metric],
                text=recent_mobile[metric].round(1),
                textposition="outside",
                marker=dict(color="#4ade80"),
                hovertemplate=f"{metric}: %{{y:.1f}}<extra></extra>",
            )
        )

        fig.update_layout(
            template="plotly_dark",
            height=360,
            margin=dict(l=20, r=20, t=20, b=20),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(15,23,42,0.35)",
            showlegend=False,
            bargap=0.28,
        )
        fig.update_xaxes(
            title="",
            type="category",
            tickangle=0,
            showgrid=False,
            tickfont=dict(size=11),
        )
        fig.update_yaxes(
            title="",
            showgrid=True,
            gridcolor="rgba(148,163,184,0.15)",
            zeroline=False,
        )

        st.plotly_chart(fig, use_container_width=True)

        st.caption(
            f"{metric} • Temp: {recent[metric].mean():.1f} | L5: {recent_mobile[metric].mean():.1f}"
        )

    else:
        recent_desktop = recent.tail(10).copy()

        fig = go.Figure()

        fig.add_trace(
            go.Scatter(
                x=recent_desktop["SHORT_LABEL"],
                y=recent_desktop["PRA"],
                mode="lines+markers",
                name="PRA",
                line=dict(width=4, color="#8b5cf6"),
                hovertemplate="PRA: %{y:.1f}<extra></extra>",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=recent_desktop["SHORT_LABEL"],
                y=recent_desktop["PTS"],
                mode="lines+markers",
                name="PTS",
                line=dict(width=2.2, color="#38bdf8"),
                opacity=0.8,
                hovertemplate="PTS: %{y:.1f}<extra></extra>",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=recent_desktop["SHORT_LABEL"],
                y=recent_desktop["REB"],
                mode="lines+markers",
                name="REB",
                line=dict(width=2.2, color="#34d399"),
                opacity=0.8,
                hovertemplate="REB: %{y:.1f}<extra></extra>",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=recent_desktop["SHORT_LABEL"],
                y=recent_desktop["AST"],
                mode="lines+markers",
                name="AST",
                line=dict(width=2.2, color="#f59e0b"),
                opacity=0.8,
                hovertemplate="AST: %{y:.1f}<extra></extra>",
            )
        )

        fig.update_layout(
            template="plotly_dark",
            height=400,
            margin=dict(l=20, r=20, t=10, b=20),
            legend=dict(orientation="h", y=1.08, x=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(15,23,42,0.35)",
            hoverlabel=dict(
                bgcolor="#0f172a",
                bordercolor="#334155",
                font=dict(color="#f8fafc", size=13),
            ),
        )
        fig.update_xaxes(
            title="",
            type="category",
            tickangle=0,
            showgrid=False,
            tickfont=dict(size=11),
        )
        fig.update_yaxes(title="")

        st.plotly_chart(fig, use_container_width=True)


def render_mobile_badges(role: str, trend: str) -> None:
    role_class = "badge-starter" if role == "Titular provável" else "badge-bench"

    if trend in ["🔥 Forte", "⬆️ Boa"]:
        trend_class = "badge-good"
    elif trend in ["🥶 Queda", "⬇️ Fraca"]:
        trend_class = "badge-bad"
    else:
        trend_class = "badge-neutral"

    st.markdown(
        f"""
        <div class="badge-row">
            <span class="badge {role_class}">{role}</span>
            <span class="badge {trend_class}">{trend}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_mobile_detail_metric(title: str, temp_val: float, l5_val: float, l10_val: float) -> None:
    with st.container(border=True):
        st.markdown(f"**{title}**")

        c1, c2, c3 = st.columns(3)

        with c1:
            with st.container(border=True):
                st.caption("Temp")
                st.markdown(f"### {format_number(temp_val)}")

        with c2:
            with st.container(border=True):
                st.caption("L5")
                st.markdown(f"### {format_number(l5_val)}")

        with c3:
            with st.container(border=True):
                st.caption("L10")
                st.markdown(f"### {format_number(l10_val)}")


def render_mobile_player_card(row: pd.Series) -> None:
    with st.container(border=True):
        top_left, top_right = st.columns([1, 4])

        with top_left:
            st.image(get_player_headshot_url(int(row["PLAYER_ID"])), width=78)

        with top_right:
            st.markdown(f"**{row['PLAYER']}**")
            position = row["POSITION"] if str(row["POSITION"]).strip() else "-"
            st.caption(
                f"Pos {position} • GP {int(row['SEASON_GP'])} • MIN {format_number(row['SEASON_MIN'])}"
            )
            render_mobile_badges(row["ROLE"], row["TREND"])

        r1c1, r1c2, r1c3 = st.columns(3)
        with r1c1:
            st.metric("PRA L10", format_number(row["L10_PRA"]))
        with r1c2:
            st.metric("Δ PRA L10", format_signed_number(row["DELTA_PRA_L10"]))
        with r1c3:
            st.metric("PTS L10", format_number(row["L10_PTS"]))

        r2c1, r2c2 = st.columns(2)
        with r2c1:
            st.metric("AST L10", format_number(row["L10_AST"]))
        with r2c2:
            st.metric("REB L10", format_number(row["L10_REB"]))

        with st.expander("Ver detalhamento completo"):
            top_a, top_b = st.columns(2)
            with top_a:
                render_mobile_detail_metric(
                    "PRA",
                    row["SEASON_PRA"],
                    row["L5_PRA"],
                    row["L10_PRA"],
                )
            with top_b:
                render_mobile_detail_metric(
                    "PTS",
                    row["SEASON_PTS"],
                    row["L5_PTS"],
                    row["L10_PTS"],
                )

            bottom_a, bottom_b = st.columns(2)
            with bottom_a:
                render_mobile_detail_metric(
                    "REB",
                    row["SEASON_REB"],
                    row["L5_REB"],
                    row["L10_REB"],
                )
            with bottom_b:
                render_mobile_detail_metric(
                    "AST",
                    row["SEASON_AST"],
                    row["L5_AST"],
                    row["L10_AST"],
                )


def render_mobile_player_cards(filtered_df: pd.DataFrame) -> None:
    for _, row in filtered_df.iterrows():
        render_mobile_player_card(row)
        st.write("")


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
        st.warning(
            "Nenhum jogador passou pelos filtros. Você apertou demais o funil, pequeno fiscal da amostra."
        )
        return

    st.markdown(
        f"""
        <div class="info-pill">Jogadores exibidos: {len(filtered_df)}</div>
        <div class="info-pill">GP mínimo: {min_games}</div>
        <div class="info-pill">MIN mínimo: {min_minutes}</div>
        <div class="info-pill">Papel: {role_filter}</div>
        <div class="info-pill">Ordenação: {sort_label}</div>
        <div class="info-pill">Visualização: {view_mode}</div>
        """,
        unsafe_allow_html=True,
    )

    if view_mode == "Mobile":
        st.markdown(
            '<div class="section-note">Modo mobile: cards por jogador com foto e leitura rápida dos números que mais importam.</div>',
            unsafe_allow_html=True,
        )
        render_mobile_player_cards(filtered_df)
    else:
        summary_df, detail_df = build_display_dataframes(filtered_df)

        quick_tab, detail_tab = st.tabs(["Leitura rápida", "Detalhamento"])

        with quick_tab:
            st.markdown(
                '<div class="section-note">Aqui o foco é no que bate rápido no olho: PRA, tendência e papel do jogador.</div>',
                unsafe_allow_html=True,
            )
            st.dataframe(
                style_table(summary_df, quick_view=True),
                use_container_width=True,
                hide_index=True,
            )

        with detail_tab:
            st.markdown(
                '<div class="section-note">Aqui entra a parte mais detalhada: PTS, REB, AST e PRA no mesmo lugar.</div>',
                unsafe_allow_html=True,
            )
            st.dataframe(
                style_table(detail_df, quick_view=False),
                use_container_width=True,
                hide_index=True,
            )

    options = filtered_df[["PLAYER", "PLAYER_ID"]].drop_duplicates()
    player_name = st.selectbox(
        f"Ver gráfico de jogador — {team_name}",
        options["PLAYER"].tolist(),
        key=f"player_select_{team_name}_{view_mode}",
    )
    selected_player_id = int(
        options.loc[options["PLAYER"] == player_name, "PLAYER_ID"].iloc[0]
    )
    render_player_chart(player_name, selected_player_id, season, view_mode)


def main() -> None:
    inject_css()

    st.markdown('<div class="main-title">NBA Dashboard MVP</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtitle">Escolha o jogo e veja PTS, REB, AST e PRA com visual desktop ou cards mobile.</div>',
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Configurações")
        selected_date = st.date_input("Data dos jogos", value=date.today())

        st.divider()
        st.subheader("Visualização")
        view_mode = st.radio("Modo de exibição", VIEW_OPTIONS, index=0)

        st.divider()
        st.subheader("Filtros")
        min_games = st.slider("Mínimo de jogos na temporada", 0, 82, 5, 1)
        min_minutes = st.slider("Mínimo de minutos por jogo", 0, 40, 15, 1)
        role_filter = st.selectbox("Mostrar jogadores", ROLE_OPTIONS, index=0)

        st.divider()
        st.subheader("Ordenação")
        sort_labels = list(SORT_OPTIONS.keys())
        default_sort_index = sort_labels.index("PRA L5")
        sort_label = st.selectbox(
            "Ordenar jogadores por",
            options=sort_labels,
            index=default_sort_index,
        )
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
        st.error(
            "Deu ruim na consulta da NBA. A fonte externa, como sempre, decidiu ter personalidade."
        )
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
    except Exception as exc:
        st.error(
            "Consegui pegar o jogo, mas a coleta das estatísticas falhou. MVP grátis também tem seus surtos."
        )
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

    tab1, tab2 = st.tabs(
        [selected_game["away_team_name"], selected_game["home_team_name"]]
    )

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

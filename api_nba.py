import time
import pandas as pd
import streamlit as st

from nba_api.stats.endpoints import (
    scoreboardv2,
    commonteamroster,
    leaguedashplayerstats,
    playergamelog,
    playergamelogs,
)

# Puxando a configuração que salvamos no passo anterior!
from config import TEAM_LOOKUP

# ==========================================
# 1. FUNÇÃO MESTRE DE TENTATIVAS (RETRY)
# ==========================================
def run_api_call_with_retry(fetch_fn, endpoint_name: str, retries: int = 5, delay: float = 2.5):
    """Tenta chamar a API da NBA com pausas progressivas para evitar bloqueios."""
    last_error = None
    for attempt in range(retries):
        try:
            return fetch_fn()
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                # Pausa progressiva para acalmar os servidores da NBA (2.5s, 5s, 7.5s...)
                time.sleep(delay * (attempt + 1))
    raise RuntimeError(f"A NBA bloqueou a consulta de {endpoint_name}. Aguarde 2 minutos e recarregue a página.") from last_error

# ==========================================
# 2. BUSCA DE JOGOS E TIMES
# ==========================================
@st.cache_data(ttl=54000, show_spinner=False)
def get_games_for_date(target_date) -> pd.DataFrame:
    response = run_api_call_with_retry(
        lambda: scoreboardv2.ScoreboardV2(
            game_date=target_date.strftime("%Y-%m-%d"),
            day_offset="0",
            league_id="00",
            timeout=45,
        ),
        endpoint_name="ScoreboardV2",
    )

    game_header = response.game_header.get_data_frame()
    if game_header.empty:
        return pd.DataFrame(
            columns=["GAME_ID", "HOME_TEAM_ID", "VISITOR_TEAM_ID", "GAME_STATUS_TEXT", "home_team_name", "away_team_name", "label"]
        )

    rows = []
    for _, row in game_header.iterrows():
        home_team_id = int(row["HOME_TEAM_ID"])
        away_team_id = int(row["VISITOR_TEAM_ID"])

        home_team_name = TEAM_LOOKUP.get(home_team_id, {}).get("full_name", str(home_team_id))
        away_team_name = TEAM_LOOKUP.get(away_team_id, {}).get("full_name", str(away_team_id))
        game_status_text = row.get("GAME_STATUS_TEXT", "Sem status")

        rows.append({
            "GAME_ID": str(row["GAME_ID"]),
            "HOME_TEAM_ID": home_team_id,
            "VISITOR_TEAM_ID": away_team_id,
            "GAME_STATUS_TEXT": game_status_text,
            "home_team_name": home_team_name,
            "away_team_name": away_team_name,
            "label": f"{away_team_name} @ {home_team_name} • {game_status_text}",
        })

    return pd.DataFrame(rows)

@st.cache_data(ttl=54000, show_spinner=True)
def get_team_roster(team_id: int, season: str) -> pd.DataFrame:
    response = run_api_call_with_retry(
        lambda: commonteamroster.CommonTeamRoster(
            team_id=team_id,
            season=season,
            timeout=45,
        ),
        endpoint_name="CommonTeamRoster",
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

# ==========================================
# 3. BUSCA DE ESTATÍSTICAS E LOGS DE JOGADORES
# ==========================================
@st.cache_data(ttl=54000, show_spinner=False)
def get_league_player_stats(season: str, last_n_games: int) -> pd.DataFrame:
    response = run_api_call_with_retry(
        lambda: leaguedashplayerstats.LeagueDashPlayerStats(
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
            timeout=45,
        ),
        endpoint_name="LeagueDashPlayerStats",
    )
    frames = response.get_data_frames()
    if not frames:
        return pd.DataFrame()

    df = frames[0].copy()
    if df.empty:
        return pd.DataFrame(columns=["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GP", "MIN", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"])

    keep_cols = ["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GP", "MIN", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]
    return df[[c for c in keep_cols if c in df.columns]].copy()

@st.cache_data(ttl=54000, show_spinner=False)
def get_player_log(player_id: int, season: str) -> pd.DataFrame:
    season_types = ["Regular Season", "PlayIn", "Playoffs"]
    all_logs = []
    
    for stype in season_types:
        try:
            response = run_api_call_with_retry(
                lambda st=stype: playergamelog.PlayerGameLog(
                    player_id=player_id,
                    season=season,
                    season_type_all_star=st,
                    timeout=45,
                ),
                endpoint_name=f"PlayerGameLog_{stype}",
            )
            frames = response.get_data_frames()
            if frames and not frames[0].empty:
                all_logs.append(frames[0])
        except Exception:
            continue

    if not all_logs:
        return pd.DataFrame()

    df = pd.concat(all_logs, ignore_index=True)
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
    return df.sort_values("GAME_DATE", ascending=False)

@st.cache_data(ttl=54000, show_spinner=False)
def get_team_player_logs(team_id: int, season: str) -> pd.DataFrame:
    season_types = ["Regular Season", "PlayIn", "Playoffs"]
    all_logs = []
    
    for stype in season_types:
        try:
            response = run_api_call_with_retry(
                lambda st=stype: playergamelogs.PlayerGameLogs(
                    team_id_nullable=team_id,
                    season_nullable=season,
                    season_type_nullable=st,
                    timeout=45,
                ),
                endpoint_name=f"PlayerGameLogs_{stype}",
            )
            frames = response.get_data_frames()
            if frames and not frames[0].empty:
                all_logs.append(frames[0])
        except Exception:
            continue

    if not all_logs:
        return pd.DataFrame()

    df = pd.concat(all_logs, ignore_index=True)
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
    for col in ["PTS", "REB", "AST", "MIN", "FG3M", "FGA", "FG3A"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0
    df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
    return df.sort_values(["PLAYER_ID", "GAME_DATE"], ascending=[True, False])

# ==========================================
# 4. BUSCA DE MATCHUP DE DEFESA
# ==========================================
@st.cache_data(ttl=21600, show_spinner=False)
def get_position_allowed_profile(season: str, opponent_team_id: int, position_group: str) -> pd.DataFrame:
    try:
        response = run_api_call_with_retry(
            lambda: leaguedashplayerstats.LeagueDashPlayerStats(
                season=season,
                season_type_all_star="Regular Season",
                per_mode_detailed="PerGame",
                measure_type_detailed_defense="Base",
                last_n_games=0,
                month=0,
                opponent_team_id=opponent_team_id,
                pace_adjust="N",
                plus_minus="N",
                rank="N",
                period=0,
                team_id_nullable="",
                player_position_abbreviation_nullable=position_group,
                timeout=45,
            ),
            endpoint_name=f"LeagueDashPlayerStats OPP {position_group}",
            retries=2,
            delay=1.5,
        )
        frames = response.get_data_frames()
        return frames[0].copy() if frames else pd.DataFrame()
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=21600, show_spinner=False)
def get_league_position_baseline(season: str, position_group: str) -> pd.DataFrame:
    try:
        response = run_api_call_with_retry(
            lambda: leaguedashplayerstats.LeagueDashPlayerStats(
                season=season,
                season_type_all_star="Regular Season",
                per_mode_detailed="PerGame",
                measure_type_detailed_defense="Base",
                last_n_games=0,
                month=0,
                opponent_team_id=0,
                pace_adjust="N",
                plus_minus="N",
                rank="N",
                period=0,
                team_id_nullable="",
                player_position_abbreviation_nullable=position_group,
                timeout=45,
            ),
            endpoint_name=f"LeagueDashPlayerStats BASE {position_group}",
            retries=2,
            delay=1.5,
        )
        frames = response.get_data_frames()
        return frames[0].copy() if frames else pd.DataFrame()
    except Exception:
        return pd.DataFrame()

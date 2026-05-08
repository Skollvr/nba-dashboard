import time
import pandas as pd
import streamlit as st

from nba_api.stats.endpoints import (
    scoreboardv2,
    scoreboardv3,
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
@st.cache_data(ttl=1800, show_spinner=False)
def get_games_for_date(target_date) -> pd.DataFrame:
    """
    Busca jogos da NBA para a data selecionada.

    Primeiro tenta ScoreboardV2.
    Se o V2 voltar vazio, usa ScoreboardV3 como fallback.
    Isso é importante nos playoffs, porque o V2 pode retornar rowSet vazio
    mesmo quando existem jogos.
    """

    def empty_games_df() -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "GAME_ID",
                "HOME_TEAM_ID",
                "VISITOR_TEAM_ID",
                "GAME_STATUS_TEXT",
                "HOME_TEAM_ABBR",
                "VISITOR_TEAM_ABBR",
                "home_team_name",
                "away_team_name",
                "label",
            ]
        )

    # =====================================================
    # 1) Tentativa principal: ScoreboardV2
    # =====================================================
    try:
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

        if game_header is not None and not game_header.empty:
            rows = []

            for _, row in game_header.iterrows():
                home_team_id = int(row["HOME_TEAM_ID"])
                away_team_id = int(row["VISITOR_TEAM_ID"])

                home_team_name = TEAM_LOOKUP.get(home_team_id, {}).get("full_name", str(home_team_id))
                away_team_name = TEAM_LOOKUP.get(away_team_id, {}).get("full_name", str(away_team_id))

                home_abbr = TEAM_LOOKUP.get(home_team_id, {}).get("abbreviation", "")
                away_abbr = TEAM_LOOKUP.get(away_team_id, {}).get("abbreviation", "")

                game_status_text = row.get("GAME_STATUS_TEXT", "Sem status")

                rows.append({
                    "GAME_ID": str(row["GAME_ID"]),
                    "HOME_TEAM_ID": home_team_id,
                    "VISITOR_TEAM_ID": away_team_id,
                    "GAME_STATUS_TEXT": game_status_text,
                    "HOME_TEAM_ABBR": home_abbr,
                    "VISITOR_TEAM_ABBR": away_abbr,
                    "home_team_name": home_team_name,
                    "away_team_name": away_team_name,
                    "label": f"{away_team_name} @ {home_team_name} • {game_status_text}",
                })

            return pd.DataFrame(rows)

    except Exception:
        # Se o V2 falhar, não mata o app. Tenta V3 abaixo.
        pass

    # =====================================================
    # 2) Fallback: ScoreboardV3
    # =====================================================
    try:
        response_v3 = run_api_call_with_retry(
            lambda: scoreboardv3.ScoreboardV3(
                game_date=target_date.strftime("%Y-%m-%d"),
                league_id="00",
                timeout=45,
            ),
            endpoint_name="ScoreboardV3",
        )

        payload = response_v3.get_dict()
        games = payload.get("scoreboard", {}).get("games", [])

        if not games:
            return empty_games_df()

        rows = []

        for game in games:
            home = game.get("homeTeam", {}) or {}
            away = game.get("awayTeam", {}) or {}

            home_team_id = int(home.get("teamId", 0) or 0)
            away_team_id = int(away.get("teamId", 0) or 0)

            home_team_name = TEAM_LOOKUP.get(home_team_id, {}).get(
                "full_name",
                f"{home.get('teamCity', '')} {home.get('teamName', '')}".strip()
            )

            away_team_name = TEAM_LOOKUP.get(away_team_id, {}).get(
                "full_name",
                f"{away.get('teamCity', '')} {away.get('teamName', '')}".strip()
            )

            home_abbr = home.get("teamTricode", "") or TEAM_LOOKUP.get(home_team_id, {}).get("abbreviation", "")
            away_abbr = away.get("teamTricode", "") or TEAM_LOOKUP.get(away_team_id, {}).get("abbreviation", "")

            game_status_text = game.get("gameStatusText", "Sem status")

            rows.append({
                "GAME_ID": str(game.get("gameId", "")),
                "HOME_TEAM_ID": home_team_id,
                "VISITOR_TEAM_ID": away_team_id,
                "GAME_STATUS_TEXT": game_status_text,
                "HOME_TEAM_ABBR": home_abbr,
                "VISITOR_TEAM_ABBR": away_abbr,
                "home_team_name": home_team_name,
                "away_team_name": away_team_name,
                "label": f"{away_team_name} @ {home_team_name} • {game_status_text}",
            })

        return pd.DataFrame(rows)

    except Exception:
        return empty_games_df()

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
def get_league_player_stats(
    season: str,
    last_n_games: int,
    season_scope: str = "Regular Season",
) -> pd.DataFrame:
    season_types = get_season_types_for_scope(season_scope)
    all_frames = []

    for season_type in season_types:
        try:
            response = run_api_call_with_retry(
                lambda stype=season_type: leaguedashplayerstats.LeagueDashPlayerStats(
                    season=season,
                    season_type_all_star=stype,
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
                endpoint_name=f"LeagueDashPlayerStats_{season_type}",
            )

            frames = response.get_data_frames()

            if frames and not frames[0].empty:
                df = frames[0].copy()
                keep_cols = [
                    "PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GP", "MIN",
                    "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"
                ]
                df = df[[c for c in keep_cols if c in df.columns]].copy()
                all_frames.append(df)

        except Exception:
            continue

    if not all_frames:
        return pd.DataFrame(
            columns=[
                "PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GP", "MIN",
                "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"
            ]
        )

    combined = pd.concat(all_frames, ignore_index=True)

    return aggregate_player_stats_by_gp(combined)

def get_season_types_for_scope(season_scope: str) -> list[str]:
    """
    Converte o recorte escolhido na UI para os valores aceitos pela nba_api.
    """
    if season_scope == "Playoffs":
        return ["Playoffs"]

    if season_scope == "PlayIn":
        return ["PlayIn"]

    if season_scope == "All":
        return ["Regular Season", "PlayIn", "Playoffs"]

    return ["Regular Season"]


def aggregate_player_stats_by_gp(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega múltiplos recortes por jogador usando GP como peso.
    Necessário para o modo 'Tudo', porque o mesmo jogador pode aparecer
    em Regular Season, PlayIn e Playoffs.
    """
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GP", "MIN",
                "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"
            ]
        )

    work = df.copy()

    for col in ["PLAYER_ID", "TEAM_ID", "GP", "MIN", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]:
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0.0)

    rows = []

    for player_id, group in work.groupby("PLAYER_ID", dropna=False):
        total_gp = float(group["GP"].sum())

        if total_gp <= 0:
            weights = None
        else:
            weights = group["GP"] / total_gp

        def weighted_avg(col: str) -> float:
            if col not in group.columns:
                return 0.0
            if weights is None:
                return float(group[col].mean())
            return float((group[col] * weights).sum())

        rows.append({
            "PLAYER_ID": player_id,
            "PLAYER_NAME": group["PLAYER_NAME"].iloc[0] if "PLAYER_NAME" in group.columns else "",
            "TEAM_ID": int(group["TEAM_ID"].iloc[0]) if "TEAM_ID" in group.columns else 0,
            "GP": total_gp,
            "MIN": weighted_avg("MIN"),
            "PTS": weighted_avg("PTS"),
            "REB": weighted_avg("REB"),
            "AST": weighted_avg("AST"),
            "FG3M": weighted_avg("FG3M"),
            "FGA": weighted_avg("FGA"),
            "FG3A": weighted_avg("FG3A"),
        })

    return pd.DataFrame(rows)

@st.cache_data(ttl=54000, show_spinner=False)
def get_player_log(
    player_id: int,
    season: str,
    season_scope: str = "All",
) -> pd.DataFrame:
    season_types = get_season_types_for_scope(season_scope)
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
                temp_df = frames[0].copy()
                temp_df["SEASON_SCOPE"] = stype
                all_logs.append(temp_df)

        except Exception:
            continue

    if not all_logs:
        return pd.DataFrame()

    df = pd.concat(all_logs, ignore_index=True)
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")

    return df.sort_values("GAME_DATE", ascending=False)

@st.cache_data(ttl=54000, show_spinner=False)
def get_team_player_logs(
    team_id: int,
    season: str,
    season_scope: str = "All",
) -> pd.DataFrame:
    season_types = get_season_types_for_scope(season_scope)
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
                temp_df = frames[0].copy()
                temp_df["SEASON_SCOPE"] = stype
                all_logs.append(temp_df)

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

import time
import pandas as pd
import requests
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
def run_api_call_with_retry(fetch_fn, endpoint_name: str, retries: int = 2, delay: float = 1.0):
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
    raise RuntimeError(
        f"A consulta {endpoint_name} da NBA não respondeu a tempo após {retries} tentativa(s)."
    ) from last_error

# ==========================================
# 2. BUSCA DE JOGOS E TIMES
# ==========================================
NBA_CDN_SCHEDULE_URL = (
    "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
)

ESPN_NBA_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)

TEAM_ID_BY_ABBR = {
    str(team_data.get("abbreviation", "")).upper(): int(team_id)
    for team_id, team_data in TEAM_LOOKUP.items()
    if team_data.get("abbreviation")
}


def _empty_games_df() -> pd.DataFrame:
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


def _games_from_nba_scoreboard_v2(game_header: pd.DataFrame) -> pd.DataFrame:
    """Converte o GameHeader do ScoreboardV2 para o formato usado pelo app."""
    if game_header is None or game_header.empty:
        return _empty_games_df()

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

    return pd.DataFrame(rows, columns=_empty_games_df().columns)


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_nba_scoreboard_v2_once(target_date) -> pd.DataFrame:
    """Tenta a fonte original da NBA uma única vez, com timeout curto."""
    response = scoreboardv2.ScoreboardV2(
        game_date=target_date.strftime("%Y-%m-%d"),
        day_offset="0",
        league_id="00",
        timeout=8,
    )
    return _games_from_nba_scoreboard_v2(response.game_header.get_data_frame())


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_nba_cdn_schedule() -> dict:
    """Busca a agenda oficial da temporada atual no CDN público da NBA."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.nba.com",
        "Referer": "https://www.nba.com/",
    }

    response = requests.get(
        NBA_CDN_SCHEDULE_URL,
        headers=headers,
        timeout=(3, 8),
    )
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, dict) or "leagueSchedule" not in payload:
        raise RuntimeError("A agenda oficial da NBA retornou um formato inesperado.")

    return payload


def _games_from_nba_cdn_payload(payload: dict, target_date) -> pd.DataFrame:
    """Filtra no calendário oficial da NBA apenas os jogos da data escolhida."""
    league_schedule = payload.get("leagueSchedule", {}) or {}
    game_dates = league_schedule.get("gameDates", []) or []
    rows = []

    for date_block in game_dates:
        raw_date = date_block.get("gameDate")
        parsed_date = pd.to_datetime(raw_date, errors="coerce")

        if pd.isna(parsed_date) or parsed_date.date() != target_date:
            continue

        for game in date_block.get("games", []) or []:
            home = game.get("homeTeam", {}) or {}
            away = game.get("awayTeam", {}) or {}

            try:
                home_team_id = int(home.get("teamId", 0) or 0)
                away_team_id = int(away.get("teamId", 0) or 0)
            except (TypeError, ValueError):
                continue

            if not home_team_id or not away_team_id:
                continue

            home_abbr = str(
                home.get("teamTricode")
                or TEAM_LOOKUP.get(home_team_id, {}).get("abbreviation", "")
            ).upper()
            away_abbr = str(
                away.get("teamTricode")
                or TEAM_LOOKUP.get(away_team_id, {}).get("abbreviation", "")
            ).upper()

            home_team_name = TEAM_LOOKUP.get(home_team_id, {}).get(
                "full_name",
                " ".join(
                    part for part in [
                        str(home.get("teamCity", "") or "").strip(),
                        str(home.get("teamName", "") or "").strip(),
                    ]
                    if part
                ) or home_abbr,
            )
            away_team_name = TEAM_LOOKUP.get(away_team_id, {}).get(
                "full_name",
                " ".join(
                    part for part in [
                        str(away.get("teamCity", "") or "").strip(),
                        str(away.get("teamName", "") or "").strip(),
                    ]
                    if part
                ) or away_abbr,
            )

            game_status_text = str(game.get("gameStatusText") or "Agendado")

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

        break

    return pd.DataFrame(rows, columns=_empty_games_df().columns)


def _nba_team_from_espn(competitor: dict) -> tuple[int, str, str]:
    team = competitor.get("team", {}) or {}
    abbr = str(team.get("abbreviation", "") or "").upper().strip()
    team_id = TEAM_ID_BY_ABBR.get(abbr, 0)

    if team_id:
        team_name = TEAM_LOOKUP.get(team_id, {}).get(
            "full_name",
            str(team.get("displayName", "") or abbr),
        )
    else:
        team_name = str(team.get("displayName", "") or team.get("shortDisplayName", "") or abbr)

    return team_id, team_name, abbr


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_espn_games_for_date(target_date) -> dict:
    """Busca apenas a agenda do dia no scoreboard público da ESPN."""
    params = {"dates": target_date.strftime("%Y%m%d")}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    }

    try:
        response = requests.get(
            ESPN_NBA_SCOREBOARD_URL,
            params=params,
            headers=headers,
            timeout=(3, 8),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(
            "A agenda alternativa não respondeu rapidamente."
        ) from exc

    events = payload.get("events", [])
    if not isinstance(events, list):
        raise RuntimeError("A agenda alternativa retornou um formato inesperado.")

    return payload


def _games_from_espn_payload(payload: dict) -> pd.DataFrame:
    columns = [
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

    rows = []

    for event in payload.get("events", []) or []:
        competitions = event.get("competitions", []) or []
        if not competitions:
            continue

        competition = competitions[0] or {}
        competitors = competition.get("competitors", []) or []

        home_comp = next(
            (item for item in competitors if item.get("homeAway") == "home"),
            None,
        )
        away_comp = next(
            (item for item in competitors if item.get("homeAway") == "away"),
            None,
        )

        if not home_comp or not away_comp:
            continue

        home_team_id, home_team_name, home_abbr = _nba_team_from_espn(home_comp)
        away_team_id, away_team_name, away_abbr = _nba_team_from_espn(away_comp)

        # O restante do dashboard depende dos IDs oficiais da NBA.
        # Se a ESPN devolver uma sigla desconhecida, ignoramos a linha em vez
        # de carregar um confronto com IDs inválidos.
        if not home_team_id or not away_team_id:
            continue

        status_type = (event.get("status", {}) or {}).get("type", {}) or {}
        game_status_text = (
            status_type.get("shortDetail")
            or status_type.get("detail")
            or "Agendado"
        )

        rows.append({
            "GAME_ID": str(event.get("id", "")),
            "HOME_TEAM_ID": home_team_id,
            "VISITOR_TEAM_ID": away_team_id,
            "GAME_STATUS_TEXT": str(game_status_text),
            "HOME_TEAM_ABBR": home_abbr,
            "VISITOR_TEAM_ABBR": away_abbr,
            "home_team_name": home_team_name,
            "away_team_name": away_team_name,
            "label": f"{away_team_name} @ {home_team_name} • {game_status_text}",
        })

    return pd.DataFrame(rows, columns=columns)


@st.cache_data(ttl=1800, show_spinner=False)
def get_games_for_date(target_date) -> pd.DataFrame:
    """
    Busca os jogos da data selecionada usando três fontes.

    Ordem de prioridade:
    1. calendário oficial da temporada no CDN da NBA;
    2. ScoreboardV2 da NBA;
    3. ESPN como fallback complementar.

    As fontes são combinadas e os jogos duplicados são removidos pelo par
    visitante/casa. Assim, uma agenda parcial de uma fonte não apaga jogos
    encontrados por outra.
    """
    cdn_games = _empty_games_df()
    nba_games = _empty_games_df()
    espn_games = _empty_games_df()

    cdn_error = None
    nba_error = None
    espn_error = None

    try:
        cdn_payload = fetch_nba_cdn_schedule()
        cdn_games = _games_from_nba_cdn_payload(cdn_payload, target_date)
    except Exception as exc:
        cdn_error = exc

    try:
        nba_games = fetch_nba_scoreboard_v2_once(target_date)
    except Exception as exc:
        nba_error = exc

    try:
        espn_payload = fetch_espn_games_for_date(target_date)
        espn_games = _games_from_espn_payload(espn_payload)
    except Exception as exc:
        espn_error = exc

    frames = [
        df
        for df in [cdn_games, nba_games, espn_games]
        if df is not None and not df.empty
    ]

    if frames:
        combined = pd.concat(frames, ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["VISITOR_TEAM_ID", "HOME_TEAM_ID"],
            keep="first",
        ).reset_index(drop=True)
        return combined

    if cdn_error is not None and nba_error is not None and espn_error is not None:
        raise RuntimeError(
            "Não foi possível consultar a agenda: NBA CDN, ScoreboardV2 e ESPN falharam."
        ) from espn_error

    return _empty_games_df()


@st.cache_data(ttl=54000, show_spinner=True)
def get_team_roster(team_id: int, season: str) -> pd.DataFrame:
    response = run_api_call_with_retry(
        lambda: commonteamroster.CommonTeamRoster(
            team_id=team_id,
            season=season,
            timeout=15,
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
                    timeout=15,
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
                    timeout=15,
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
                    timeout=15,
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
def get_position_allowed_profile(
    season: str,
    opponent_team_id: int,
    position_group: str,
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
                    last_n_games=0,
                    month=0,
                    opponent_team_id=opponent_team_id,
                    pace_adjust="N",
                    plus_minus="N",
                    rank="N",
                    period=0,
                    team_id_nullable="",
                    player_position_abbreviation_nullable=position_group,
                    timeout=15,
                ),
                endpoint_name=f"LeagueDashPlayerStats OPP {position_group} {season_type}",
                retries=1,
                delay=1.0,
            )

            frames = response.get_data_frames()

            if frames and not frames[0].empty:
                temp_df = frames[0].copy()
                temp_df["SEASON_SCOPE"] = season_type
                all_frames.append(temp_df)

        except Exception:
            continue

    if not all_frames:
        return pd.DataFrame()

    return pd.concat(all_frames, ignore_index=True)

@st.cache_data(ttl=21600, show_spinner=False)
def get_league_position_baseline(
    season: str,
    position_group: str,
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
                    last_n_games=0,
                    month=0,
                    opponent_team_id=0,
                    pace_adjust="N",
                    plus_minus="N",
                    rank="N",
                    period=0,
                    team_id_nullable="",
                    player_position_abbreviation_nullable=position_group,
                    timeout=15,
                ),
                endpoint_name=f"LeagueDashPlayerStats BASE {position_group} {season_type}",
                retries=1,
                delay=1.0,
            )

            frames = response.get_data_frames()

            if frames and not frames[0].empty:
                temp_df = frames[0].copy()
                temp_df["SEASON_SCOPE"] = season_type
                all_frames.append(temp_df)

        except Exception:
            continue

    if not all_frames:
        return pd.DataFrame()

    return pd.concat(all_frames, ignore_index=True)
    

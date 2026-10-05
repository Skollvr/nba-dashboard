import time
import pandas as pd
import requests
import streamlit as st

from nba_api.stats.endpoints import (
    scoreboardv2,
    commonteamroster,
    leaguedashplayerstats,
    leaguedashteamstats,
    playergamelog,
    playergamelogs,
)

# Puxando a configuração que salvamos no passo anterior!
from config import TEAM_LOOKUP
from api_espn import get_espn_player_log

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
NBA_CDN_SCHEDULE_URLS = [
    # Arquivo usado atualmente pela página de calendário do NBA.com.
    "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json",
    # Fallback: em alguns períodos a NBA também publica o mesmo schema sem sufixo.
    "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json",
]

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
        "Connection": "keep-alive",
    }

    errors = []

    for url in NBA_CDN_SCHEDULE_URLS:
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=(3, 8),
            )
            response.raise_for_status()
            payload = response.json()

            league_schedule = payload.get("leagueSchedule", {}) if isinstance(payload, dict) else {}
            game_dates = league_schedule.get("gameDates", []) or []

            if game_dates:
                return payload

            errors.append(f"{url}: agenda vazia")
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}")

    raise RuntimeError(
        "A agenda oficial da NBA não respondeu em nenhum dos endpoints CDN conhecidos. "
        + " | ".join(errors)
    )


def _games_from_nba_cdn_payload(payload: dict, target_date) -> pd.DataFrame:
    """Filtra no calendário oficial da NBA apenas os jogos da data escolhida."""
    league_schedule = payload.get("leagueSchedule", {}) or {}
    game_dates = league_schedule.get("gameDates", []) or []
    rows = []

    target_date_iso = target_date.strftime("%Y-%m-%d")
    target_date_us = target_date.strftime("%m/%d/%Y")

    for date_block in game_dates:
        raw_date = str(date_block.get("gameDate") or "").strip()
        raw_date_prefix = raw_date[:10]

        # A NBA já usou tanto YYYY-MM-DD quanto MM/DD/YYYY nesse campo.
        # Evitamos depender apenas da inferência de formato do pandas.
        date_matches = raw_date_prefix in {target_date_iso, target_date_us}

        if not date_matches:
            parsed_date = pd.to_datetime(raw_date, errors="coerce")
            date_matches = not pd.isna(parsed_date) and parsed_date.date() == target_date

        if not date_matches:
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
    ESPN-first schedule lookup for cloud reliability.

    Streamlit Cloud is blocked by both stats.nba.com and cdn.nba.com in our
    tests, so ESPN is now the primary source. NBA sources remain local fallbacks
    if ESPN is temporarily unavailable.
    """
    espn_error = None
    try:
        espn_payload = fetch_espn_games_for_date(target_date)
        espn_games = _games_from_espn_payload(espn_payload)
        if espn_games is not None and not espn_games.empty:
            return espn_games.reset_index(drop=True)
    except Exception as exc:
        espn_error = exc

    cdn_error = None
    nba_error = None

    try:
        cdn_payload = fetch_nba_cdn_schedule()
        cdn_games = _games_from_nba_cdn_payload(cdn_payload, target_date)
        if cdn_games is not None and not cdn_games.empty:
            return cdn_games.reset_index(drop=True)
    except Exception as exc:
        cdn_error = exc

    try:
        nba_games = fetch_nba_scoreboard_v2_once(target_date)
        if nba_games is not None and not nba_games.empty:
            return nba_games.reset_index(drop=True)
    except Exception as exc:
        nba_error = exc

    if espn_error is not None and cdn_error is not None and nba_error is not None:
        raise RuntimeError(
            "Não foi possível consultar a agenda pela ESPN nem pelos fallbacks da NBA."
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



def clear_schedule_cache() -> None:
    """Limpa somente caches leves/voláteis da agenda."""
    for cached_fn in [
        fetch_nba_scoreboard_v2_once,
        fetch_nba_cdn_schedule,
        fetch_espn_games_for_date,
        get_games_for_date,
    ]:
        try:
            cached_fn.clear()
        except Exception:
            pass


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
    try:
        espn_log = get_espn_player_log(
            player_id,
            season,
            season_scope=season_scope,
        )
        if espn_log is not None and not espn_log.empty:
            return espn_log
    except Exception:
        pass

    # Local fallback: preserve the previous NBA Stats implementation when ESPN
    # is unavailable and stats.nba.com happens to be reachable.
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


@st.cache_data(ttl=54000, show_spinner=False)
def get_league_player_logs(
    season: str,
    season_scope: str = "All",
) -> pd.DataFrame:
    """
    Busca logs individuais de toda a liga para a temporada/recorte.

    Usamos esta base no motor de projeção para que jogadores transferidos
    mantenham L5/L10 e histórico H2H mesmo quando jogavam por outro time.
    """
    season_types = get_season_types_for_scope(season_scope)
    all_logs = []

    for stype in season_types:
        try:
            response = run_api_call_with_retry(
                lambda st=stype: playergamelogs.PlayerGameLogs(
                    team_id_nullable=0,
                    season_nullable=season,
                    season_type_nullable=st,
                    timeout=20,
                ),
                endpoint_name=f"LeaguePlayerGameLogs_{stype}",
                retries=2,
                delay=1.0,
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

    for col in ["PLAYER_ID", "TEAM_ID", "PTS", "REB", "AST", "MIN", "FG3M", "FGA", "FG3A"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ["PTS", "REB", "AST", "MIN", "FG3M", "FGA", "FG3A"]:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = df[col].fillna(0.0)

    df["PRA"] = df["PTS"] + df["REB"] + df["AST"]

    return df.sort_values(["PLAYER_ID", "GAME_DATE"], ascending=[True, False])

# ==========================================
# 4. BUSCA DE MATCHUP DE DEFESA
# ==========================================
@st.cache_data(ttl=43200, show_spinner=False)
def get_team_defense_percentiles(
    season: str,
    season_scope: str = "Regular Season",
) -> pd.DataFrame:
    """
    Retorna percentis defensivos dos 30 times usando estatísticas de oponente.

    Percentil alto = adversário mais permissivo naquele fundamento
    (matchup mais favorável ao jogador).
    Percentil baixo = adversário mais restritivo.
    """
    season_types = get_season_types_for_scope(season_scope)
    all_frames = []

    for season_type in season_types:
        try:
            response = run_api_call_with_retry(
                lambda stype=season_type: leaguedashteamstats.LeagueDashTeamStats(
                    season=season,
                    season_type_all_star=stype,
                    per_mode_detailed="PerGame",
                    measure_type_detailed_defense="Opponent",
                    last_n_games=0,
                    month=0,
                    opponent_team_id=0,
                    pace_adjust="N",
                    plus_minus="N",
                    rank="N",
                    period=0,
                    team_id_nullable="",
                    timeout=15,
                ),
                endpoint_name=f"LeagueDashTeamStats Opponent {season_type}",
                retries=2,
                delay=1.0,
            )

            frames = response.get_data_frames()
            if frames and not frames[0].empty:
                df = frames[0].copy()
                df["SEASON_SCOPE"] = season_type
                all_frames.append(df)

        except Exception:
            continue

    if not all_frames:
        return pd.DataFrame()

    raw = pd.concat(all_frames, ignore_index=True)

    if "TEAM_ID" not in raw.columns:
        return pd.DataFrame()

    raw["TEAM_ID"] = pd.to_numeric(raw["TEAM_ID"], errors="coerce")
    raw["GP"] = pd.to_numeric(raw.get("GP", 0), errors="coerce").fillna(0.0)

    source_map = {
        "PTS": ["OPP_PTS", "PTS"],
        "REB": ["OPP_REB", "REB"],
        "AST": ["OPP_AST", "AST"],
        "3PM": ["OPP_FG3M", "FG3M"],
        "FGA": ["OPP_FGA", "FGA"],
        "3PA": ["OPP_FG3A", "FG3A"],
    }

    for metric, candidates in source_map.items():
        source_col = next((col for col in candidates if col in raw.columns), None)
        if source_col is None:
            raw[f"_DEF_{metric}"] = 0.0
        else:
            raw[f"_DEF_{metric}"] = pd.to_numeric(
                raw[source_col], errors="coerce"
            ).fillna(0.0)

    raw["_DEF_PRA"] = raw["_DEF_PTS"] + raw["_DEF_REB"] + raw["_DEF_AST"]

    rows = []
    metric_names = ["PTS", "REB", "AST", "PRA", "3PM", "FGA", "3PA"]

    for team_id, group in raw.groupby("TEAM_ID", dropna=False):
        if pd.isna(team_id):
            continue

        gp_sum = float(group["GP"].sum())
        if gp_sum > 0:
            weights = group["GP"] / gp_sum
        else:
            weights = None

        row = {
            "TEAM_ID": int(team_id),
            "TEAM_GP": gp_sum,
        }

        for metric in metric_names:
            col = f"_DEF_{metric}"
            if weights is None:
                value = float(group[col].mean())
            else:
                value = float((group[col] * weights).sum())
            row[f"TEAM_DEF_{metric}"] = value

        rows.append(row)

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    team_count = int(len(result))
    result["TEAM_DEF_TEAM_COUNT"] = team_count

    for metric in metric_names:
        value_col = f"TEAM_DEF_{metric}"
        pct_col = f"TEAM_DEF_PCT_{metric}"
        rank_col = f"TEAM_DEF_RANK_{metric}"

        # Quanto mais o time permite, maior o percentil/rank e mais favorável
        # tende a ser o matchup para aquele fundamento.
        result[pct_col] = (
            result[value_col]
            .rank(method="average", pct=True, ascending=True)
            .clip(0.0, 1.0)
        )
        result[rank_col] = (
            result[value_col]
            .rank(method="average", ascending=True)
            .round()
            .astype(int)
        )

    return result


@st.cache_data(ttl=43200, show_spinner=False)
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

@st.cache_data(ttl=43200, show_spinner=False)
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
    

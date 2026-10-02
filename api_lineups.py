from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import requests
import streamlit as st


NBA_DAILY_LINEUPS_URL = (
    "https://stats.nba.com/js/data/leaders/00_daily_lineups_{date_key}.json"
)

NBA_BROWSER_HEADERS = {
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


def _date_key(target_date: date) -> str:
    return target_date.strftime("%Y%m%d")


@st.cache_data(ttl=300, show_spinner=False)
def fetch_daily_lineups_raw(target_date: date) -> dict[str, Any]:
    """
    Busca o feed diário de lineups do stats.nba.com.

    O parser abaixo é deliberadamente tolerante a pequenas mudanças de schema.
    Durante as primeiras semanas de 2026-27 vamos validar quais campos o feed
    realmente publica para projected/confirmed e minutos.
    """
    url = NBA_DAILY_LINEUPS_URL.format(date_key=_date_key(target_date))

    response = requests.get(
        url,
        headers=NBA_BROWSER_HEADERS,
        timeout=(3, 10),
    )
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("O feed diário de lineups da NBA retornou formato inesperado.")

    return payload


def get_daily_lineups_source_url(target_date: date) -> str:
    """Retorna a URL consultada, útil para diagnóstico durante os testes."""
    return NBA_DAILY_LINEUPS_URL.format(date_key=_date_key(target_date))


def _compact_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _dict_value(item: dict[str, Any], candidates: list[str]) -> Any:
    key_map = {_compact_key(key): key for key in item.keys()}
    for candidate in candidates:
        original = key_map.get(_compact_key(candidate))
        if original is not None:
            return item.get(original)
    return None


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None

    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "starter", "starting", "confirmed"}:
        return True
    if text in {"0", "false", "no", "n", "bench", "reserve"}:
        return False
    return None


def _to_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    return numeric


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _lineup_status_from_item(item: dict[str, Any]) -> tuple[str, bool | None, str]:
    raw_status = _dict_value(
        item,
        [
            "lineupStatus",
            "lineup_status",
            "starterStatus",
            "starter_status",
            "status",
            "lineupType",
            "lineup_type",
        ],
    )
    starter_raw = _dict_value(
        item,
        ["isStarter", "is_starter", "starter", "starting", "isStarting"],
    )
    confirmed_raw = _dict_value(
        item,
        ["isConfirmed", "is_confirmed", "confirmed", "lineupConfirmed"],
    )

    starter = _to_bool(starter_raw)
    confirmed = _to_bool(confirmed_raw)
    status_text = str(raw_status or "").strip()
    status_lower = status_text.lower()

    if confirmed is True or "confirm" in status_lower:
        return "Titular confirmado", True if starter is None else starter, status_text

    if starter is True:
        if any(word in status_lower for word in ["project", "probable", "expected"]):
            return "Titular projetado", True, status_text
        return "Titular projetado", True, status_text

    if starter is False:
        return "Rotação", False, status_text

    if any(word in status_lower for word in ["starter", "starting"]):
        return "Titular projetado", True, status_text

    if any(word in status_lower for word in ["bench", "reserve"]):
        return "Rotação", False, status_text

    return "", starter, status_text


def normalize_daily_lineups(payload: dict[str, Any]) -> pd.DataFrame:
    """
    Converte o feed diário em uma tabela mínima e estável.

    Como o schema do arquivo pode mudar, procuramos apenas campos inequívocos:
    identidade do jogador, time, sinal explícito de starter/status e minutos
    projetados quando existirem. Itens sem evidência de lineup são ignorados.
    """
    rows: list[dict[str, Any]] = []

    player_id_candidates = [
        "playerId", "player_id", "personId", "person_id", "PLAYER_ID", "PERSON_ID"
    ]
    player_name_candidates = [
        "playerName", "player_name", "displayName", "display_name",
        "fullName", "full_name", "PLAYER_NAME"
    ]
    team_id_candidates = ["teamId", "team_id", "TEAM_ID"]
    team_abbr_candidates = [
        "teamAbbreviation", "team_abbreviation", "teamTricode",
        "team_tricode", "teamAbbr", "team_abbr", "TEAM_ABBREVIATION"
    ]
    minute_candidates = [
        "projectedMinutes", "projected_minutes", "projMinutes", "proj_minutes",
        "minutesProjection", "minutes_projection", "projectedMin", "projMin"
    ]

    for item in _walk_dicts(payload):
        player_id = _dict_value(item, player_id_candidates)
        player_name = _dict_value(item, player_name_candidates)
        team_id = _dict_value(item, team_id_candidates)
        team_abbr = _dict_value(item, team_abbr_candidates)
        projected_minutes = _to_float(_dict_value(item, minute_candidates))
        lineup_status, starter, raw_status = _lineup_status_from_item(item)

        has_identity = player_id is not None or bool(str(player_name or "").strip())
        has_lineup_signal = bool(lineup_status) or projected_minutes is not None

        if not has_identity or not has_lineup_signal:
            continue

        try:
            player_id_num = int(float(player_id)) if player_id is not None else None
        except (TypeError, ValueError):
            player_id_num = None

        try:
            team_id_num = int(float(team_id)) if team_id is not None else None
        except (TypeError, ValueError):
            team_id_num = None

        rows.append(
            {
                "PLAYER_ID": player_id_num,
                "PLAYER": str(player_name or "").strip(),
                "TEAM_ID": team_id_num,
                "TEAM_ABBR": str(team_abbr or "").strip().upper(),
                "LINEUP_STATUS": lineup_status or "Informação de lineup",
                "IS_STARTER": starter,
                "PROJECTED_MINUTES_EXTERNAL": projected_minutes,
                "LINEUP_RAW_STATUS": raw_status,
                "LINEUP_SOURCE": "NBA Daily Lineups",
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "PLAYER_ID", "PLAYER", "TEAM_ID", "TEAM_ABBR", "LINEUP_STATUS",
                "IS_STARTER", "PROJECTED_MINUTES_EXTERNAL", "LINEUP_RAW_STATUS",
                "LINEUP_SOURCE",
            ]
        )

    df = pd.DataFrame(rows)

    # Um mesmo jogador pode aparecer mais de uma vez em objetos aninhados.
    # Preferimos a linha com mais informação e depois removemos duplicatas.
    df["_INFO_SCORE"] = (
        df["LINEUP_STATUS"].fillna("").ne("").astype(int)
        + df["PROJECTED_MINUTES_EXTERNAL"].notna().astype(int)
        + df["TEAM_ID"].notna().astype(int)
        + df["TEAM_ABBR"].fillna("").ne("").astype(int)
    )
    df = df.sort_values("_INFO_SCORE", ascending=False)

    if df["PLAYER_ID"].notna().any():
        with_id = df[df["PLAYER_ID"].notna()].drop_duplicates("PLAYER_ID", keep="first")
        without_id = df[df["PLAYER_ID"].isna()]
        df = pd.concat([with_id, without_id], ignore_index=True)

    if "PLAYER" in df.columns:
        no_id_mask = df["PLAYER_ID"].isna() & df["PLAYER"].ne("")
        no_id = df[no_id_mask].drop_duplicates("PLAYER", keep="first")
        with_id = df[~no_id_mask]
        df = pd.concat([with_id, no_id], ignore_index=True)

    return df.drop(columns=["_INFO_SCORE"], errors="ignore").reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False)
def get_daily_lineups(target_date: date) -> pd.DataFrame:
    """Busca e normaliza o feed; erros ficam para o chamador tratar como fallback."""
    return normalize_daily_lineups(fetch_daily_lineups_raw(target_date))

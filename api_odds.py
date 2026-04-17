import os
import requests
import unicodedata
import pandas as pd
import streamlit as st
from typing import Optional

# Importamos variáveis do nosso novo config.py
from config import (
    ODDS_API_BASE_URL, ODDS_BOOKMAKER, ODDS_STAT_MAP, ODDS_METRIC_COLUMNS
)
# E também usamos o "retry" que acabamos de criar no api_nba.py!
from api_nba import run_api_call_with_retry

# ==========================================
# 1. FUNÇÕES AUXILIARES DE TEXTO
# ==========================================
def normalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace(".", " ").replace("-", " ").replace("'", "").replace(",", " ")
    return " ".join(text.split())

def normalize_person_name(value: str) -> str:
    text = str(value or "").strip()
    if "," in text:
        last_part, first_part = text.split(",", 1)
        text = f"{first_part.strip()} {last_part.strip()}"
    return normalize_text(text)

# ==========================================
# 2. FUNÇÕES DE CÁLCULO E VALIDAÇÃO DE ODDS
# ==========================================
def american_to_decimal(american_odds) -> Optional[float]:
    try:
        odds_int = int(str(american_odds).replace("+", "").strip())
    except (TypeError, ValueError):
        return None
    if odds_int > 0:
        return round(1 + (odds_int / 100), 2)
    if odds_int < 0:
        return round(1 + (100 / abs(odds_int)), 2)
    return None

def get_odds_api_key() -> str:
    secrets_obj = getattr(st, "secrets", None)
    if secrets_obj:
        for key_name in ["SPORTSGAMEODDS_API_KEY", "sportsgameodds_api_key"]:
            if key_name in secrets_obj:
                return str(secrets_obj[key_name]).strip()
    return os.getenv("SPORTSGAMEODDS_API_KEY", "").strip()

# ==========================================
# 3. BUSCA DOS EVENTOS DA API
# ==========================================
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_nba_odds_events() -> list[dict]:
    api_key = get_odds_api_key()
    if not api_key:
        return []

    def _fetch():
        response = requests.get(
            f"{ODDS_API_BASE_URL}/events/?leagueID=NBA&oddsAvailable=true",
            headers={"x-api-key": api_key},
            timeout=45,
        )
        response.raise_for_status()
        return response

    response = run_api_call_with_retry(_fetch, endpoint_name="SportsGameOdds Events")
    payload = response.json()
    if not payload.get("success"):
        return []
    return payload.get("data", []) or []

def find_matching_odds_event(events: list[dict], home_team_name: str, away_team_name: str) -> Optional[dict]:
    target_home = normalize_text(home_team_name)
    target_away = normalize_text(away_team_name)

    def aliases(name: str) -> set[str]:
        base = normalize_text(name)
        alias_map = {
            "la clippers": {"los angeles clippers", "clippers", "lac"},
            "los angeles clippers": {"la clippers", "clippers", "lac"},
            "los angeles lakers": {"lakers", "lal"},
            "new york knicks": {"knicks", "nyk"},
            "golden state warriors": {"warriors", "gsw"},
            "oklahoma city thunder": {"oklahoma city", "thunder", "okc"},
            "phoenix suns": {"suns", "phx"},
            "philadelphia 76ers": {"76ers", "sixers", "phi"},
            "new orleans pelicans": {"pelicans", "nop"},
            "san antonio spurs": {"spurs", "sas"},
            "portland trail blazers": {"trail blazers", "blazers", "por"},
        }
        return {base, *alias_map.get(base, set())}

    home_aliases = aliases(home_team_name)
    away_aliases = aliases(away_team_name)

    for event in events:
        teams_payload = event.get("teams", {})
        event_home = normalize_text(teams_payload.get("home", {}).get("names", {}).get("long", ""))
        event_away = normalize_text(teams_payload.get("away", {}).get("names", {}).get("long", ""))

        if event_home in home_aliases and event_away in away_aliases:
            return event

    return None

# ==========================================
# 4. EXTRAÇÃO DOS PROPS DA BETMGM
# ==========================================
def extract_betmgm_player_props(event: Optional[dict]) -> pd.DataFrame:
    if not event:
        return pd.DataFrame()

    players = event.get("players", {}) or {}
    odds_payload = event.get("odds", {}) or {}
    rows: dict[str, dict] = {}

    for _, item in odds_payload.items():
        if not isinstance(item, dict):
            continue
        if item.get("periodID") != "game" or item.get("betTypeID") != "ou":
            continue

        stat_id = item.get("statID")
        metric = ODDS_STAT_MAP.get(stat_id)
        player_id = item.get("playerID")
        if not metric or not player_id:
            continue

        bookmaker_data = (item.get("byBookmaker") or {}).get(ODDS_BOOKMAKER)
        if not bookmaker_data or not bookmaker_data.get("available"):
            continue

        player_info = players.get(player_id, {}) if isinstance(players, dict) else {}
        first_name = player_info.get("firstName", "")
        last_name = player_info.get("lastName", "")
        player_name = f"{first_name} {last_name}".strip() or player_info.get("name") or item.get("marketName", "")
        line_value = item.get("bookOverUnder") or bookmaker_data.get("overUnder")
        side = item.get("sideID")
        key = normalize_person_name(player_name)

        if key not in rows:
            rows[key] = {
                "PLAYER_NAME_ODDS": player_name,
                "PLAYER_KEY_ODDS": key,
            }
            for _, cols in ODDS_METRIC_COLUMNS.items():
                rows[key][cols[0]] = None
                rows[key][cols[1]] = None
                rows[key][cols[2]] = None
                rows[key][cols[3]] = None

        line_col, over_col, under_col, updated_col = ODDS_METRIC_COLUMNS[metric]
        rows[key][line_col] = pd.to_numeric(line_value, errors="coerce")
        rows[key][updated_col] = bookmaker_data.get("lastUpdatedAt", "")

        decimal_value = american_to_decimal(bookmaker_data.get("odds"))
        if side == "over":
            rows[key][over_col] = decimal_value
        elif side == "under":
            rows[key][under_col] = decimal_value

    return pd.DataFrame(rows.values())

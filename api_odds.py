import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import requests
import time
import unicodedata
import pandas as pd
import streamlit as st
from typing import Optional

# Importamos variáveis do nosso novo config.py
from config import (
    ODDS_API_BASE_URL, ODDS_BOOKMAKER, ODDS_STAT_MAP, ODDS_METRIC_COLUMNS
)
# E também usamos o "retry" que acabamos de criar no api_nba.py!

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
    """Read SportsGameOdds credentials from Cloud secrets or environment.

    Never print the key to users or diagnostic messages.
    """
    env_key = os.getenv("SPORTSGAMEODDS_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        return str(st.secrets.get("SPORTSGAMEODDS_API_KEY", "")).strip()
    except Exception:
        return ""


class SportsGameOddsHTTPError(Exception):
    """Safe HTTP status from odds provider; never includes URL or API key."""

    def __init__(self, status_code: int | None):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code if status_code is not None else 'desconhecido'}")


def _request_events_page(params: dict, api_key: str):
    """Do not retry 4xx authorization/validation errors (except rate limits)."""
    for attempt in range(2):
        try:
            response = requests.get(
                f"{ODDS_API_BASE_URL}/events/",
                params=params,
                headers={"x-api-key": api_key},
                timeout=(5, 20),
            )
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                # Capture it before the retry helper discards the response.
                # Unlike str(exc), the status won't contain the secret-bearing URL.
                code = getattr(response, "status_code", None)
                if code is None:
                    code = getattr(getattr(exc, "response", None), "status_code", None)
                if code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(1)
                    continue
                raise SportsGameOddsHTTPError(code) from None
            return response
        except SportsGameOddsHTTPError:
            raise
        except requests.RequestException:
            if attempt == 0:
                time.sleep(1)
                continue
            raise RuntimeError("Não foi possível conectar à SportsGameOdds.") from None
    raise RuntimeError("Consulta de odds não foi concluída.")


# ==========================================
# 3. BUSCA DOS EVENTOS DA API
# ==========================================
def _brasilia_utc_window(selected_date: date) -> tuple[str, str]:
    """Events can cross midnight UTC while still belonging to a BRT game day."""
    start = datetime.combine(selected_date, time.min, tzinfo=ZoneInfo("America/Sao_Paulo"))
    end = start + timedelta(days=1)
    return (
        start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


@st.cache_data(ttl=240, show_spinner=False)
def fetch_nba_odds_events(
    target_date: date | None = None,
    bookmaker_ids: tuple[str, ...] = ("betmgm",),
) -> list[dict]:
    """Fetch chosen NBA bookmakers with API v2 cursor pagination.

    No key means no market data, never inferred betting lines. A failed
    authenticated request raises so the UI can identify it as a source error.
    """
    api_key = get_odds_api_key()
    if not api_key or not bookmaker_ids:
        return []
    if not set(bookmaker_ids).issubset({"betmgm", "fanduel"}):
        raise ValueError("Bookmaker inválida para o dashboard.")

    params = {
        "leagueID": "NBA",
        "bookmakerID": ",".join(
            book for book in ("betmgm", "fanduel") if book in bookmaker_ids
        ),
        "oddsAvailable": "true",
        "limit": 100,
        "includeAltLines": "false",
    }
    if target_date is not None:
        after, before = _brasilia_utc_window(target_date)
        params.update({"startsAfter": after, "startsBefore": before})

    events: list[dict] = []
    seen_ids: set[str] = set()
    seen_cursors: set[str] = set()
    cursor: str | None = None
    for _ in range(8):  # safety bound, normally one page per NBA game day
        page_params = dict(params)
        if cursor:
            page_params["cursor"] = cursor

        response = _request_events_page(page_params, api_key)
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise ValueError("SportsGameOdds retornou uma resposta inválida.")
        for event in payload.get("data") or []:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("eventID") or "")
            if event_id and event_id in seen_ids:
                continue
            if event_id:
                seen_ids.add(event_id)
            events.append(event)
        next_cursor = str(payload.get("nextCursor") or "")
        if not next_cursor or next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return events


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
def extract_betmgm_player_props(
    event: Optional[dict], bookmaker_id: str = "betmgm",
) -> pd.DataFrame:
    """Read only the chosen bookmaker's player props, not consensus odds."""
    if bookmaker_id not in {"betmgm", "fanduel"}:
        raise ValueError("Casa não suportada.")
    prefix = bookmaker_id.upper()
    if not event:
        return pd.DataFrame()

    players = event.get("players") or {}
    odds_payload = event.get("odds") or {}
    if not isinstance(odds_payload, dict):
        return pd.DataFrame()
    rows: dict[str, dict] = {}

    for item in odds_payload.values():
        if not isinstance(item, dict):
            continue
        if str(item.get("periodID") or "").lower() != "game":
            continue
        if str(item.get("betTypeID") or "").lower() != "ou":
            continue
        metric = ODDS_STAT_MAP.get(str(item.get("statID") or ""))
        side = str(item.get("sideID") or "").lower()
        player_id = item.get("playerID") or item.get("statEntityID")
        if (
            not player_id
            or str(player_id).lower() in {"home", "away", "all"}
            or not metric
            or side not in {"over", "under"}
        ):
            continue
        book = (item.get("byBookmaker") or {}).get(bookmaker_id)
        if not isinstance(book, dict) or book.get("available") is not True:
            continue
        # bookOverUnder is a consensus line; use the selected house instead.
        raw_line = book.get("overUnder")
        line = pd.to_numeric(raw_line, errors="coerce")
        if pd.isna(line) or float(line) <= 0:
            continue

        player_info = (
            players.get(str(player_id), players.get(player_id, {}))
            if isinstance(players, dict) else {}
        )
        if not isinstance(player_info, dict):
            player_info = {}
        player_names = player_info.get("names") or {}
        if not isinstance(player_names, dict):
            player_names = {}
        name = (
            (str(player_info.get("firstName") or player_names.get("first") or "")
             + " "
             + str(player_info.get("lastName") or player_names.get("last") or "")).strip()
            or str(
                player_info.get("name")
                or player_names.get("display")
                or player_names.get("long")
                or player_names.get("full")
                or ""
            ).strip()
        )
        if not name:
            continue
        key = normalize_person_name(name)
        if not key:
            continue

        if key not in rows:
            rows[key] = {
                "PLAYER_NAME_ODDS": name,
                "PLAYER_KEY_ODDS": key,
            }
            for cols in ODDS_METRIC_COLUMNS.values():
                for col in cols:
                    rows[key][col.replace("BETMGM_", f"{prefix}_")] = None

        line_col, over_col, under_col, updated_col = (
            name.replace("BETMGM_", f"{prefix}_")
            for name in ODDS_METRIC_COLUMNS[metric]
        )
        prev = pd.to_numeric(rows[key].get(line_col), errors="coerce")
        if pd.notna(prev) and float(prev) != float(line):
            # Never merge OVER odds from one line with UNDER from another.
            # Prefer the first coherent main line.
            continue
        rows[key][line_col] = float(line)
        updated = str(book.get("lastUpdatedAt") or "")
        if not rows[key][updated_col] or updated > str(rows[key][updated_col]):
            rows[key][updated_col] = updated
        decimal_value = american_to_decimal(book.get("odds"))
        rows[key][over_col if side == "over" else under_col] = decimal_value

    return pd.DataFrame(rows.values())


def clear_odds_cache() -> None:
    """Limpa apenas eventos/linhas de odds."""
    try:
        fetch_nba_odds_events.clear()
    except Exception:
        pass

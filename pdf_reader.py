import re
import time
from datetime import date, datetime
from io import BytesIO

import pandas as pd
import requests
import streamlit as st
from pypdf import PdfReader

# Importamos as configurações e o fuso horário
from config import APP_TIMEZONE, EASTERN_TIMEZONE, TEAM_NAME_LOOKUP_NORM, TEAM_LOOKUP
# Importamos a função de normalizar nomes que criámos no ficheiro das odds
from api_odds import normalize_person_name

# ==========================================
# 1. FUNÇÕES AUXILIARES DE TEMPO E TEXTO
# ==========================================
def get_season_string(target_date: date) -> str:
    if target_date.month >= 10:
        start_year = target_date.year
        end_year = str(target_date.year + 1)[-2:]
    else:
        start_year = target_date.year - 1
        end_year = str(target_date.year)[-2:]
    return f"{start_year}-{end_year}"

def clean_injury_pdf_line(line: str) -> str:
    line = str(line or "").strip()
    line = re.sub(r"Injury Report:.*$", "", line).strip()
    line = re.sub(r"Page\s+\d+\s+of\s+\d+$", "", line).strip()
    return line

def parse_report_dt_from_url(pdf_url: str) -> datetime | None:
    match = re.search(
        r"Injury[\s\-_]*Report[\s\-_]*(\d{4}-\d{2}-\d{2})[\s\-_]*(\d{1,2})[\s\-_]*(\d{2})\s*(AM|PM)\.pdf",
        str(pdf_url),
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    date_part = match.group(1)
    hour_part = int(match.group(2))
    minute_part = int(match.group(3))
    ampm_part = match.group(4).upper()

    if ampm_part == "AM":
        hour_24 = 0 if hour_part == 12 else hour_part
    else:
        hour_24 = 12 if hour_part == 12 else hour_part + 12

    return datetime.strptime(date_part, "%Y-%m-%d").replace(
        hour=hour_24,
        minute=minute_part,
        second=0,
        microsecond=0,
        tzinfo=EASTERN_TIMEZONE,
    )

def parse_injury_report_timestamp_from_url(pdf_url: str) -> dict:
    if not pdf_url:
        return {
            "report_label_et": "—",
            "report_label_brt": "—",
            "report_dt_et": None,
            "report_dt_brt": None,
        }

    dt_et = parse_report_dt_from_url(pdf_url)
    if dt_et is None:
        return {
            "report_label_et": "—",
            "report_label_brt": "—",
            "report_dt_et": None,
            "report_dt_brt": None,
        }

    dt_brt = dt_et.astimezone(APP_TIMEZONE)

    return {
        "report_label_et": dt_et.strftime("%d/%m %I:%M %p ET"),
        "report_label_brt": dt_brt.strftime("%d/%m %H:%M BRT"),
        "report_dt_et": dt_et,
        "report_dt_brt": dt_brt,
    }

def resolve_team_line(line: str) -> str:
    clean_line = str(line or "").replace("NOT YET SUBMITTED", "").strip()
    norm_line = clean_line.lower() # Simplificação para evitar dependência extra
    
    for norm_team, full_team in TEAM_NAME_LOOKUP_NORM.items():
        if norm_team in norm_line:
            return full_team
    return ""

# ==========================================
# 2. FUNÇÕES DE DOWNLOAD E LEITURA DO PDF
# ==========================================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_latest_injury_report_pdf_url() -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://official.nba.com/",
        "Cache-Control": "no-cache"
    }
    today = datetime.now(APP_TIMEZONE).date()
    season_str = get_season_string(today)
    cb = int(time.time())
    page_url = f"https://official.nba.com/nba-injury-report-{season_str}-season/?cb={cb}"
    
    try:
        response = requests.get(page_url, headers=headers, timeout=10)
        if response.status_code == 404:
            fallback_year = f"{today.year-1}-{str(today.year)[-2:]}"
            page_url = f"https://official.nba.com/nba-injury-report-{fallback_year}-season/?cb={cb}"
            response = requests.get(page_url, headers=headers, timeout=10)
        response.raise_for_status()
    except Exception:
        return ""

    html = response.text
    all_hrefs = re.findall(r'href="([^"]+\.pdf)"', html, flags=re.IGNORECASE)
    pdf_urls = []
    for href in all_hrefs:
        if "injury" in href.lower() and "report" in href.lower():
            if href.startswith("http"):
                pdf_urls.append(href)
            else:
                base = "https://official.nba.com"
                pdf_urls.append(base + href if href.startswith("/") else base + "/" + href)

    if not pdf_urls: 
        return ""

    dated_urls = []
    for url in pdf_urls:
        dt = parse_report_dt_from_url(url)
        if dt is not None: 
            dated_urls.append((dt, url))

    if dated_urls:
        dated_urls.sort(key=lambda x: x[0], reverse=True)
        return dated_urls[0][1]
    
    return pdf_urls[0]

def extract_pdf_text_lines(pdf_bytes: bytes) -> list[str]:
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        lines: list[str] = []
        for page in reader.pages:
            try:
                text = page.extract_text(extraction_mode="layout") or ""
            except TypeError:
                text = page.extract_text() or ""
                
            page_lines = [clean_injury_pdf_line(x) for x in text.splitlines() if x.strip()]
            lines.extend(page_lines)
        return lines
    except Exception as e:
        st.error(f"🚨 DIAGNÓSTICO: O PDF foi baixado, mas não consegui ler o texto interno. Erro: {e}")
        return []

_NBA_INJURY_STATUS_RE = re.compile(
    r"(?P<player>[A-Za-zÀ-ÿ'’.\-]+(?:\s+[A-Za-zÀ-ÿ'’.\-]+)*,\s*"
    r"[A-Za-zÀ-ÿ'’.\-]+(?:\s+[A-Za-zÀ-ÿ'’.\-]+)*)\s+"
    r"(?P<status>Out|Questionable|Probable|Doubtful|Available)\b",
    flags=re.IGNORECASE,
)
_NBA_REPORT_TEAM_IDS = {
    str(team.get("full_name") or ""): int(team_id)
    for team_id, team in TEAM_LOOKUP.items()
    if team.get("full_name")
}
# "LA Clippers" is the short name used on some NBA documents.
_NBA_REPORT_TEAM_IDS["LA Clippers"] = next(
    (team_id for name, team_id in _NBA_REPORT_TEAM_IDS.items()
     if "Clippers" in name), 0
)
_NBA_REPORT_TEAM_IDS = {
    name: tid for name, tid in _NBA_REPORT_TEAM_IDS.items() if tid
}
_NBA_REPORT_TEAM_NAMES = sorted(
    _NBA_REPORT_TEAM_IDS, key=len, reverse=True
)


def parse_official_injury_report_lines(
    lines: list[str], pdf_url: str,
) -> pd.DataFrame:
    """Read the official NBA report, preserving team, matchup and submission.

    The PDF groups multiple players under a team, often with the team name
    appearing only once. A "NOT YET SUBMITTED" team is not assumed healthy.
    This parser accepts the line structure produced by pypdf layout extraction
    and keeps teams separate (unlike the legacy regex over the full PDF).
    """
    rows: list[dict] = []
    game_date = ""
    matchup = ""
    team_name = ""
    latest_player: dict | None = None

    def finish_player():
        nonlocal latest_player
        if latest_player is not None:
            reason = str(latest_player.get("INJ_REASON", "") or "")
            latest_player["INJ_REASON"] = re.sub(r"\s+", " ", reason).strip()[:240]
            rows.append(latest_player)
            latest_player = None

    def record(status: str, player: str = "", reason: str = "") -> dict:
        return {
            "GAME_DATE": game_date,
            "GAME_TIME_ET": "",
            "MATCHUP": matchup,
            "TEAM_NAME_IR": team_name,
            "TEAM_ID_IR": _NBA_REPORT_TEAM_IDS.get(team_name),
            "PLAYER_ID_IR": None,
            "PLAYER_NAME_IR": player,
            "PLAYER_KEY_IR": normalize_person_name(player) if player else "",
            "INJ_STATUS": status if player else "",
            "INJ_REASON": reason,
            "INJ_REPORT_URL": pdf_url,
            "INJ_SOURCE": "NBA Official PDF",
            "TEAM_REPORT_STATUS": "Reported" if player else status,
            "QUERY_OK": status not in {"NOT YET SUBMITTED", "Unavailable"},
        }

    for raw in lines:
        line = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not line:
            continue
        if line.startswith("Injury Report:") or line.startswith("Page "):
            continue
        if (
            ("Game Date" in line and "Player Name" in line)
            or (line.startswith("Game Time") and "Matchup" in line)
        ):
            continue

        date_match = re.search(r"\b\d{2}/\d{2}/\d{4}\b", line)
        new_matchup = re.search(r"\b[A-Z]{2,3}@[A-Z]{2,3}\b", line)
        new_team = next(
            (name for name in _NBA_REPORT_TEAM_NAMES
             if re.search(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])",
                          line, flags=re.IGNORECASE)), None
        )

        if date_match or new_matchup or new_team:
            finish_player()
        if date_match:
            game_date = date_match.group(0)
        if new_matchup:
            matchup = new_matchup.group(0).upper()
        if new_team:
            team_name = new_team

        if "NOT YET SUBMITTED" in line.upper():
            finish_player()
            if team_name:
                rows.append(record("NOT YET SUBMITTED"))
            continue

        # The NBA explicitly uses "None" for submitted teams with no injuries.
        cleaned = line
        if new_team:
            team_position = re.search(re.escape(new_team), cleaned, flags=re.IGNORECASE)
            cleaned = cleaned[team_position.end():] if team_position else cleaned

        match = _NBA_INJURY_STATUS_RE.search(cleaned)
        if match and team_name:
            finish_player()
            player = match.group("player").strip()
            status = match.group("status").capitalize()
            latest_player = record(status, player, cleaned[match.end():].strip())
            continue

        if team_name and re.search(r"\bNone\b", cleaned, flags=re.IGNORECASE):
            finish_player()
            rows.append(record("No injuries listed"))
            continue

        # Injury reason continuation lines usually appear below the player.
        if latest_player is not None and not (new_team or new_matchup or date_match):
            if not re.search(r"\b(Page|Injury Report|Game Date)\b", line):
                latest_player["INJ_REASON"] = (
                    str(latest_player.get("INJ_REASON", "") or "") + " " + line
                ).strip()

    finish_player()
    return pd.DataFrame(rows)


@st.cache_data(ttl=300, show_spinner=False)
def fetch_latest_injury_report_df() -> pd.DataFrame:
    """Retrieve NBA's latest 15-minute PDF injury bulletin, if available."""
    pdf_url = fetch_latest_injury_report_pdf_url()
    if not pdf_url:
        return pd.DataFrame()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/pdf,*/*",
        "Referer": "https://official.nba.com/",
    }
    try:
        response = requests.get(pdf_url, headers=headers, timeout=(4, 12))
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            return pd.DataFrame()
    except Exception:
        return pd.DataFrame()

    lines = extract_pdf_text_lines(response.content)
    if not lines:
        return pd.DataFrame()
    return parse_official_injury_report_lines(lines, pdf_url)


def clear_injury_cache() -> None:
    """Limpa somente os caches do injury report."""
    for cached_fn in [fetch_latest_injury_report_pdf_url, fetch_latest_injury_report_df]:
        try:
            cached_fn.clear()
        except Exception:
            pass

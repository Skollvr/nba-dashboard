import re
import time
from datetime import date, datetime
from io import BytesIO

import pandas as pd
import requests
import streamlit as st
from pypdf import PdfReader

# Importamos as configurações e o fuso horário
from config import APP_TIMEZONE, EASTERN_TIMEZONE, TEAM_NAME_LOOKUP_NORM
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

@st.cache_data(ttl=300, show_spinner=False)
def fetch_latest_injury_report_df() -> pd.DataFrame:
    pdf_url = fetch_latest_injury_report_pdf_url()
    if not pdf_url: 
        return pd.DataFrame()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/pdf,*/*",
        "Referer": "https://official.nba.com/"
    }
    
    try:
        response = requests.get(pdf_url, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        st.error(f"🚨 DIAGNÓSTICO: Achei o link, mas a NBA bloqueou o download. Erro: {e}")
        return pd.DataFrame()

    lines = extract_pdf_text_lines(response.content)
    if not lines:
        return pd.DataFrame()

    full_text = " ".join(lines)
    full_text = re.sub(r'\s+', ' ', full_text)

    rows = []
    name_regex = r"([A-Za-zÀ-ÿ'\.\-]+\s*(?:III|II|IV|V|Jr\.|Sr\.)?\s*,\s*[A-Za-zÀ-ÿ'\.\-]+(?:[\s\-][A-Za-zÀ-ÿ'\.\-]+)?)"
    status_regex = r"(Available|Out|Questionable|Probable|Doubtful)"
    
    matches = list(re.finditer(rf"{name_regex}\s+{status_regex}\b", full_text, flags=re.IGNORECASE))
    
    for i, match in enumerate(matches):
        player_name = match.group(1).strip()
        status = match.group(2).capitalize()
        
        start_reason = match.end()
        end_reason = matches[i+1].start() if i + 1 < len(matches) else start_reason + 120
        raw_reason = full_text[start_reason:end_reason].strip()
        
        reason = re.split(r'\d{2}/\d{2}/\d{4}|NOT YET SUBMITTED', raw_reason)[0].strip()
        if len(reason) > 120:
            reason = reason[:120] + "..."

        current_row = {
            "GAME_DATE": "", "GAME_TIME_ET": "", "MATCHUP": "", "TEAM_NAME_IR": "", 
            "PLAYER_NAME_IR": player_name, 
            "PLAYER_KEY_IR": normalize_person_name(player_name),
            "INJ_STATUS": status,
            "INJ_REASON": reason,
            "INJ_REPORT_URL": pdf_url,
        }
        rows.append(current_row)

    injury_df = pd.DataFrame(rows)
    if not injury_df.empty:
        injury_df["INJ_REASON"] = injury_df["INJ_REASON"].str.replace(r"\s+", " ", regex=True).str.strip()
    
    return injury_df

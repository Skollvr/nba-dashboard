from datetime import date, datetime
from io import BytesIO
import os
import time
import time
from config import (
    NBA_TEAM_COLORS, TEAM_LOOKUP, TEAM_ABBR_LOOKUP, TEAM_LOGO_URL,
    PLAYER_HEADSHOT_URL, SORT_OPTIONS, ROLE_OPTIONS, VIEW_OPTIONS,
    CHART_OPTIONS, LINE_METRIC_OPTIONS, PROJECTION_WEIGHTS,
    ODDS_API_BASE_URL, ODDS_BOOKMAKER, ODDS_STAT_MAP, ODDS_METRIC_COLUMNS,
    INACTIVE_STATUSES, WATCHLIST_STATUSES, PLAYER_STATUS_RE, GAME_PREFIX_RE,
    APP_TIMEZONE, EASTERN_TIMEZONE, UTC_TIMEZONE, TEAM_NAME_LOOKUP_NORM
)
from typing import Optional
from pandas.io.formats.style import Styler

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from pypdf import PdfReader
import requests
import streamlit as st

from nba_api.stats.endpoints import scoreboardv2
from nba_api.stats.endpoints import (
    commonteamroster,
    leaguedashplayerstats,
    playergamelog,
    playergamelogs,
)


st.markdown("""
<style>
    /* --- AJUSTES EXCLUSIVOS PARA MOBILE (Telas menores que 768px) --- */
    @media (max-width: 768px) {
        /* 1. Ajusta o tamanho da fonte do nome do jogador */
        h1 {
            font-size: 22px !important;
        }
        
        /* 2. Compacta o Banner do Jogador */
        div[style*="padding: 20px"] {
            padding: 10px !important;
            margin-bottom: 10px !important;
        }

        /* 3. Faz os cards da galeria ocuparem a largura toda no celular */
        [data-testid="column"] {
            width: 100% !important;
            flex: 1 1 auto !important;
        }

        /* 4. Ajusta o tamanho das imagens para não estourarem */
        img {
            max-width: 60px !important;
        }
        
        /* 5. Ajusta os botões para ficarem mais fáceis de tocar com o polegar */
        .stButton button {
            width: 100% !important;
            height: 45px !important;
        }

        /* Faz a tabela ter rolagem lateral suave no dedo */
        .stDataFrame {
            overflow-x: auto !important;
        }
    }
</style>
""", unsafe_allow_html=True)
# --- LOGO ABAIXO DOS IMPORTS OU DO DICIONÁRIO DE CORES ---

st.markdown("""
    <style>
        /* 1. Cor dos botões de seleção (Projeções, Linha Manual, etc.) */
        div[data-testid="stHorizontalBlock"] button {
            background-color: #1e2633 !important; /* Fundo escuro padrão */
            color: white !important;
            border: 1px solid #3e4a5e !important;
        }

        /* 2. Cor do botão quando ele está selecionado ou focado */
        div[data-testid="stHorizontalBlock"] button:focus, 
        div[data-testid="stHorizontalBlock"] button:active {
            background-color: #ff4b4b !important; /* Verde Água Neon */
            color: #000 !important;
            border: none !important;
            box-shadow: 0 0 15px rgba(0, 255, 204, 0.4);
        }

        /* 3. Cor da "bolinha" e da barra do Slider (Minutos, Jogos) */
        .stSlider [data-baseweb="slider"] > div > div {
            background-color: #00ffcc !important;
        }
        .stSlider [data-baseweb="slider"] [data-testid="stTickBar"] {
            display: none; /* Limpa os risquinhos do slider pra ficar mais clean */
        }

        /* 4. Cor do Switch (Onde liga "Usar linha BetMGM") */
        .stCheckbox [data-testid="stWidgetLabel"] p {
            color: #00ffcc !important;
            font-weight: bold;
        }
    </style>
""", unsafe_allow_html=True)
st.markdown("""
    <style>
        /* Fundo principal do App */
        .stApp {
            background-color: #0a0e14;
            background-image: radial-gradient(circle at 50% 0%, #1a1f2c 0%, #0a0e14 100%);
        }

        /* Fundo da Sidebar (Lateral) */
        [data-testid="stSidebar"] {
            background-color: #0e121a;
            border-right: 1px solid #1e2633;
        }

        /* Cor global dos textos */
        h1, h2, h3, p, span {
            color: #e0e6ed !important;
        }
    </style>
""", unsafe_allow_html=True)

st.markdown("""
    <style>
        /* Faz os containers (cards) terem efeito de vidro */
        div[data-testid="stVerticalBlock"] > div > div[style*="border"] {
            background: rgba(255, 255, 255, 0.03) !important;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
        }
    </style>
""", unsafe_allow_html=True)

st.set_page_config(
    page_title="NBA Props Dashboard",
    page_icon="🏀",
    layout="wide",
)

def get_brasilia_today() -> date:
    return datetime.now(APP_TIMEZONE).date()


def get_game_datetime_brasilia(game: dict) -> Optional[datetime]:
    candidate_fields = [
        (game.get("gameTimeUTC"), UTC_TIMEZONE),
        (game.get("gameDateTimeUTC"), UTC_TIMEZONE),
        (game.get("gameEt"), EASTERN_TIMEZONE),
        (game.get("gameDateEST"), EASTERN_TIMEZONE),
        (game.get("gameDateTimeEst"), EASTERN_TIMEZONE),
    ]

    for raw_value, source_tz in candidate_fields:
        if not raw_value:
            continue
        parsed = pd.to_datetime(raw_value, errors="coerce")
        if pd.isna(parsed):
            continue
        dt_value = parsed.to_pydatetime()
        if dt_value.tzinfo is None:
            dt_value = dt_value.replace(tzinfo=source_tz)
        return dt_value.astimezone(APP_TIMEZONE)

    game_code = str(game.get("gameCode", ""))
    if len(game_code) >= 8 and game_code[:8].isdigit():
        try:
            fallback_dt = datetime.strptime(game_code[:8], "%Y%m%d").replace(tzinfo=EASTERN_TIMEZONE)
            return fallback_dt.astimezone(APP_TIMEZONE)
        except ValueError:
            return None

    return None


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


def format_ratio_text(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "-"
    return f"{int(numerator)}/{int(denominator)}"


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


def normalize_position_group(position: str) -> str:
    pos = str(position or "").upper().strip()
    if not pos:
        return "F"

    primary = pos.split("-")[0].strip()
    if primary in {"G", "F", "C"}:
        return primary
    if "G" in pos:
        return "G"
    if "F" in pos:
        return "F"
    if "C" in pos:
        return "C"
    return "F"


def classify_oscillation(value: float) -> str:
    if value <= 4.5:
        return "Baixa"
    if value <= 7.5:
        return "Média"
    return "Alta"


def classify_form_signal(slope: float) -> str:
    if slope >= 1.0:
        return "↗ Em alta"
    if slope <= -1.0:
        return "↘ Em queda"
    return "→ Estável"


def classify_matchup_tier(diff_value: float) -> str:
    if diff_value >= 2.5:
        return "Favorável"
    if diff_value <= -2.5:
        return "Difícil"
    return "Neutro"


def get_matchup_chip_class(label: str) -> str:
    if label == "Favorável":
        return "matchup-good"
    if label == "Difícil":
        return "matchup-bad"
    return "matchup-neutral"


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

    
def get_team_name_aliases(team_id: int, team_name: str = "") -> set[str]:
    team_meta = TEAM_LOOKUP.get(team_id, {}) or {}

    aliases = {
        normalize_text(team_name),
        normalize_text(team_meta.get("full_name", "")),
        normalize_text(team_meta.get("abbreviation", "")),
        normalize_text(team_meta.get("city", "")),
        normalize_text(team_meta.get("nickname", "")),
        normalize_text(team_meta.get("state", "")),
    }

    full_name = str(team_meta.get("full_name", "") or "")
    city = str(team_meta.get("city", "") or "")
    nickname = str(team_meta.get("nickname", "") or "")

    if city and nickname:
        aliases.add(normalize_text(f"{city} {nickname}"))
    if nickname:
        aliases.add(normalize_text(nickname))
    if city:
        aliases.add(normalize_text(city))

    special_aliases = {
        "oklahoma city thunder": {"oklahoma city", "thunder", "okc"},
        "portland trail blazers": {"portland", "trail blazers", "blazers", "por"},
        "philadelphia 76ers": {"philadelphia", "76ers", "sixers", "phi"},
        "phoenix suns": {"phoenix", "suns", "phx"},
        "new york knicks": {"new york", "knicks", "nyk"},
        "new orleans pelicans": {"new orleans", "pelicans", "nop"},
        "san antonio spurs": {"san antonio", "spurs", "sas"},
        "golden state warriors": {"golden state", "warriors", "gsw"},
        "los angeles lakers": {"lakers", "lal"},
        "los angeles clippers": {"clippers", "lac"},
    }

    normalized_full = normalize_text(full_name)
    aliases.update(special_aliases.get(normalized_full, set()))

    return {x for x in aliases if x}


def clean_injury_pdf_line(line: str) -> str:
    line = str(line or "").strip()
    line = re.sub(r"Injury Report:.*$", "", line).strip()
    line = re.sub(r"Page\s+\d+\s+of\s+\d+$", "", line).strip()
    return line

def parse_report_dt_from_url(pdf_url: str) -> datetime | None:
    # Lupa ultra-flexível para achar a data mesmo se a NBA digitar o nome do arquivo errado
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


TEAM_NAME_LOOKUP_NORM = {
    normalize_text(team["full_name"]): team["full_name"]
    for team in teams.get_teams()
}


def resolve_team_line(line: str) -> str:
    clean_line = str(line or "").replace("NOT YET SUBMITTED", "").strip()
    norm_line = normalize_text(clean_line)
    
    # Proteção extra: se o nome do time estiver misturado com outra frase
    for norm_team, full_team in TEAM_NAME_LOOKUP_NORM.items():
        if norm_team in norm_line:
            return full_team
    return ""

@st.cache_data(ttl=300, show_spinner=False)
def fetch_latest_injury_report_pdf_url() -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://official.nba.com/",
        "Cache-Control": "no-cache" # Pede gentilmente para não usar cache
    }
    today = datetime.now(APP_TIMEZONE).date()
    season_str = get_season_string(today)
    
    # CACHE-BUSTER: Adiciona os segundos exatos do relógio no link.
    # O servidor da NBA vai achar que é uma página inédita e mandar a versão mais atual!
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
        # Pega a maior data lida
        dated_urls.sort(key=lambda x: x[0], reverse=True)
        return dated_urls[0][1]
    
    return pdf_urls[0]

def extract_pdf_text_lines(pdf_bytes: bytes) -> list[str]:
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        lines: list[str] = []
        for page in reader.pages:
            # O Modo Layout força o pypdf a manter a tabela alinhada horizontalmente
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

    # TÁTICA NUCLEAR: Transforma o PDF inteiro numa única linha de texto
    full_text = " ".join(lines)
    full_text = re.sub(r'\s+', ' ', full_text)

    rows = []
    
    # Busca todas as ocorrências de "Sobrenome, Nome Status" em qualquer lugar do documento
    name_regex = r"([A-Za-zÀ-ÿ'\.\-]+\s*(?:III|II|IV|V|Jr\.|Sr\.)?\s*,\s*[A-Za-zÀ-ÿ'\.\-]+(?:[\s\-][A-Za-zÀ-ÿ'\.\-]+)?)"
    status_regex = r"(Available|Out|Questionable|Probable|Doubtful)"
    
    matches = list(re.finditer(rf"{name_regex}\s+{status_regex}\b", full_text, flags=re.IGNORECASE))
    
    for i, match in enumerate(matches):
        player_name = match.group(1).strip()
        status = match.group(2).capitalize()
        
        # Pega o motivo da lesão lendo o que está entre este jogador e o próximo
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

@st.cache_data(ttl=54000, show_spinner=False)
def get_matchup_context(
    away_team_id: int,
    home_team_id: int,
    away_team_name: str,
    home_team_name: str,
    season: str,
    include_market: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    away_df = build_team_table(away_team_id, season)
    home_df = build_team_table(home_team_id, season)

    away_df = enrich_team_with_context(
        team_df=away_df,
        team_id=away_team_id,
        opponent_team_id=home_team_id,
        opponent_team_name=home_team_name,
        season=season,
    )
    home_df = enrich_team_with_context(
        team_df=home_df,
        team_id=home_team_id,
        opponent_team_id=away_team_id,
        opponent_team_name=away_team_name,
        season=season,
    )

    away_df["TEAM_NAME"] = away_team_name
    home_df["TEAM_NAME"] = home_team_name
    
    # --- NOVO: FLAG DE MANDANTE/VISITANTE ---
    away_df["IS_HOME"] = False
    home_df["IS_HOME"] = True

    odds_df = pd.DataFrame()
    if include_market:
        odds_events = fetch_nba_odds_events()
        selected_odds_event = find_matching_odds_event(
            odds_events,
            home_team_name=home_team_name,
            away_team_name=away_team_name,
        )
        odds_df = extract_betmgm_player_props(selected_odds_event)

    away_df = merge_betmgm_odds(away_df, odds_df)
    home_df = merge_betmgm_odds(home_df, odds_df)

    return away_df, home_df

@st.cache_data(ttl=36000, show_spinner=False)
def get_matchup_injury_context(
    away_team_id: int,
    home_team_id: int,
    away_team_name: str,
    home_team_name: str,
    away_df: pd.DataFrame,
    home_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    try:
        injury_df = fetch_latest_injury_report_df()
    except Exception:
        injury_df = pd.DataFrame()

    injury_report_url = ""
    if not injury_df.empty and "INJ_REPORT_URL" in injury_df.columns:
        valid_urls = injury_df["INJ_REPORT_URL"].dropna().astype(str)
        valid_urls = valid_urls[valid_urls.str.strip() != ""]
        if not valid_urls.empty:
            injury_report_url = valid_urls.iloc[0]

    injury_report_meta = parse_injury_report_timestamp_from_url(injury_report_url)

    game_matchup = f"{TEAM_ABBR_LOOKUP[int(away_team_id)]}@{TEAM_ABBR_LOOKUP[int(home_team_id)]}"

    away_injury_df = merge_injury_report(
        away_df,
        injury_df,
        away_team_name,
        away_team_id,
        game_matchup=game_matchup,
    )

    home_injury_df = merge_injury_report(
        home_df,
        injury_df,
        home_team_name,
        home_team_id,
        game_matchup=game_matchup,
    )

    return away_injury_df, home_injury_df, injury_report_meta

def merge_injury_report(
    team_df: pd.DataFrame,
    injury_df: pd.DataFrame,
    team_name: str,
    team_id: int,
    game_matchup: str = "",
) -> pd.DataFrame:
    if team_df.empty:
        return team_df

    enriched = team_df.copy()
    enriched["INJ_STATUS"] = "—"
    enriched["INJ_REASON"] = ""
    enriched["INJ_REPORT_URL"] = ""
    enriched["IS_UNAVAILABLE"] = False
    enriched["INJ_MATCHUP_FOUND"] = False

    if injury_df.empty:
        return enriched

    roster_keys = set(enriched["PLAYER_KEY"].fillna("").astype(str).tolist())
    
    # TRADUTOR: Resolve o problema do "Jimmy Butler III" vs "Jimmy Butler"
    def fuzzy_match(ir_key: str) -> str:
        if ir_key in roster_keys: return ir_key
        # Remove sufixos que atrapalham o cruzamento
        for suffix in [" iii", " ii", " iv", " v", " jr", " sr"]:
            if ir_key.endswith(suffix):
                clean = ir_key[:-len(suffix)].strip()
                if clean in roster_keys: return clean
        # Se um nome contiver o outro (ex: noah clowney)
        for rk in roster_keys:
            if ir_key in rk or rk in ir_key: return rk
        return ir_key

    work_ir = injury_df.copy()
    # Aplica o tradutor nos nomes que vieram do PDF
    work_ir["PLAYER_KEY_IR"] = work_ir["PLAYER_KEY_IR"].fillna("").astype(str).apply(fuzzy_match)

    # Como a Lupa Global já achou tudo perfeito, só cruzamos os nomes que batem com o time
    work_match = work_ir[work_ir["PLAYER_KEY_IR"].isin(roster_keys)].copy()

    if work_match.empty:
        return enriched

    work_match = work_match.drop_duplicates(subset=["PLAYER_KEY_IR"], keep="last")

    enriched["INJ_STATUS"] = "Available"
    enriched["INJ_MATCHUP_FOUND"] = True

    merge_cols = [c for c in ["PLAYER_KEY_IR", "INJ_STATUS", "INJ_REASON", "INJ_REPORT_URL"] if c in work_match.columns]

    merged = enriched.merge(
        work_match[merge_cols],
        left_on="PLAYER_KEY",
        right_on="PLAYER_KEY_IR",
        how="left",
        suffixes=("", "_IR"),
    )

    if "INJ_STATUS_IR" in merged.columns:
        merged["INJ_STATUS"] = merged["INJ_STATUS_IR"].fillna(merged["INJ_STATUS"])
    if "INJ_REASON_IR" in merged.columns:
        merged["INJ_REASON"] = merged["INJ_REASON_IR"].fillna(merged["INJ_REASON"])
    if "INJ_REPORT_URL_IR" in merged.columns:
        merged["INJ_REPORT_URL"] = merged["INJ_REPORT_URL_IR"].fillna(merged["INJ_REPORT_URL"])

    merged["IS_UNAVAILABLE"] = merged["INJ_STATUS"].isin(INACTIVE_STATUSES)

    drop_cols = [c for c in ["PLAYER_KEY_IR", "INJ_STATUS_IR", "INJ_REASON_IR", "INJ_REPORT_URL_IR"] if c in merged.columns]
    if drop_cols:
        merged = merged.drop(columns=drop_cols)

    return merged
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


def run_api_call_with_retry(fetch_fn, endpoint_name: str, retries: int = 5, delay: float = 2.5):
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


def calculate_projection(
    season_value: float,
    l10_value: float,
    l5_value: float,
    opp_allowed: float,
    league_allowed: float,
) -> float:
    matchup_adjusted = float(season_value) + (float(opp_allowed) - float(league_allowed))
    projection = (
        PROJECTION_WEIGHTS["season"] * float(season_value)
        + PROJECTION_WEIGHTS["l10"] * float(l10_value)
        + PROJECTION_WEIGHTS["l5"] * float(l5_value)
        + PROJECTION_WEIGHTS["matchup"] * matchup_adjusted
    )
    return max(0.0, projection)


def get_metric_projection_column(metric: str) -> str:
    return {
        "PRA": "PROJ_PRA",
        "PTS": "PROJ_PTS",
        "REB": "PROJ_REB",
        "AST": "PROJ_AST",
        "3PM": "PROJ_3PM",
        "FGA": "PROJ_FGA",
        "3PA": "PROJ_3PA",
    }[metric]


def get_metric_recent_list_column(metric: str) -> str:
    return {
        "PRA": "RECENT_PRA_L10",
        "PTS": "RECENT_PTS_L10",
        "REB": "RECENT_REB_L10",
        "AST": "RECENT_AST_L10",
        "3PM": "RECENT_3PM_L10",
        "FGA": "RECENT_FGA_L10",
        "3PA": "RECENT_3PA_L10",
    }[metric]


def classify_line_edge(edge: float) -> str:
    if edge >= 1.5:
        return "Acima"
    if edge <= -1.5:
        return "Abaixo"
    return "Justa"


def get_metric_hit_text_column(metric: str) -> str:
    return {
        "PRA": "HIT_RATE_L10_TEXT",
        "PTS": "PTS_HIT_RATE_L10_TEXT",
        "REB": "REB_HIT_RATE_L10_TEXT",
        "AST": "AST_HIT_RATE_L10_TEXT",
        "3PM": "THREE_PM_HIT_RATE_L10_TEXT",
        "FGA": "FGA_HIT_RATE_L10_TEXT",
        "3PA": "THREE_PA_HIT_RATE_L10_TEXT",
    }[metric]


def get_metric_hit_rate_column(metric: str) -> str:
    return {
        "PRA": "HIT_RATE_L10",
        "PTS": "PTS_HIT_RATE_L10",
        "REB": "REB_HIT_RATE_L10",
        "AST": "AST_HIT_RATE_L10",
        "3PM": "THREE_PM_HIT_RATE_L10",
        "FGA": "FGA_HIT_RATE_L10",
        "3PA": "THREE_PA_HIT_RATE_L10",
    }[metric]


def get_metric_market_columns(metric: str) -> tuple[str, str, str, str]:
    return ODDS_METRIC_COLUMNS[metric]


def get_market_line_for_metric(row: pd.Series, metric: str) -> dict:
    line_col, over_col, under_col, updated_col = get_metric_market_columns(metric)
    return {
        "line": row.get(line_col),
        "over_dec": row.get(over_col),
        "under_dec": row.get(under_col),
        "updated_at": row.get(updated_col),
    }


def get_line_context(row: pd.Series, metric: str, line_value: float, use_market_line: bool = False) -> dict:
    projection_col = get_metric_projection_column(metric)
    recent_list_col = get_metric_recent_list_column(metric)

    projection = float(row.get(projection_col, 0.0))
    market_info = get_market_line_for_metric(row, metric)
    market_line = pd.to_numeric(market_info.get("line"), errors="coerce")
    use_market = bool(use_market_line and pd.notna(market_line))
    active_line = float(market_line) if use_market else float(line_value)

    edge = projection - active_line
    recent_values = row.get(recent_list_col, [])
    if not isinstance(recent_values, list):
        recent_values = []

    hit_l10 = sum(float(v) >= active_line for v in recent_values)
    hit_l5 = sum(float(v) >= active_line for v in recent_values[:5])

    # Criação da sequência visual (Tracker): da esquerda (mais antigo) para a direita (mais recente)
    # recent_values originalmente vem do mais recente para o mais antigo, por isso usamos reversed()
    hit_sequence = "".join(["✅" if float(v) >= active_line else "❌" for v in reversed(recent_values)])

    source_name = "BetMGM" if use_market else "Manual"
    icon = "🎯" if use_market else "✏️"
    tooltip = f"Calculado com linha {source_name} ({active_line})"
    
    hit_l10_str = format_ratio_text(hit_l10, len(recent_values))
    hit_l10_html = f'<span title="{tooltip}" style="cursor:help;">{hit_l10_str} {icon}</span>'

    return {
        "projection": projection,
        "edge": edge,
        "label": classify_line_edge(edge),
        "line_value": active_line,
        "line_source": source_name,
        "has_market_line": use_market,
        "over_dec": market_info.get("over_dec") if use_market else None,
        "under_dec": market_info.get("under_dec") if use_market else None,
        "updated_at": market_info.get("updated_at") if use_market else "",
        "hit_l10": hit_l10_str,
        "hit_l10_html": hit_l10_html,
        "hit_sequence": hit_sequence,
        "icon": icon,
        "tooltip": tooltip,
        "hit_l5": format_ratio_text(hit_l5, min(len(recent_values), 5)),
    }


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 2rem;
            padding-bottom: 2rem;
        }

        .main-title {
            font-size: 2.35rem;
            font-weight: 800;
            line-height: 1.2;
            letter-spacing: -0.03em;
            color: #f8fbff;
            margin: 0 0 0.45rem 0;
            padding-top: 0.4rem;
        }

        .subtitle {
           font-size: 1rem;
           line-height: 1.55;
           color: #9db0c9;
           max-width: 820px;
           margin: 0 0 0.9rem 0;
       } 
        .hero-pills {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin: 0 0 1.25rem 0;
        }

        .hero-pill {
            display: inline-flex;
            align-items: center;
            padding: 0.42rem 0.8rem;
            border-radius: 999px;
            background: rgba(143, 107, 255, 0.14);
            border: 1px solid rgba(143, 107, 255, 0.28);
            color: #d9cbff;
            font-size: 0.78rem;
            font-weight: 600;
            letter-spacing: 0.01em;
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
        .player-headline-card {
            background: linear-gradient(180deg, rgba(76,29,149,0.36), rgba(15,23,42,0.92));
            border: 1px solid rgba(167,139,250,0.22);
            border-radius: 18px;
            padding: 0.8rem 0.9rem;
            margin-top: 0.3rem;
            margin-bottom: 0.3rem;
        }
        .player-headline-label {
            color: #c4b5fd;
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.15rem;
        }
        .player-headline-value {
            color: #f8fafc;
            font-size: 1.85rem;
            font-weight: 900;
            line-height: 1;
            margin-bottom: 0.35rem;
        }
        .player-headline-sub {
            color: #e2e8f0;
            font-size: 0.86rem;
            line-height: 1.35;
        }
        .player-quick-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 0.55rem;
            margin-top: 0.9rem;
            margin-bottom: 0.15rem;
        }
        .quick-stat {
            background: rgba(15,23,42,0.74);
            border: 1px solid rgba(148,163,184,.12);
            border-radius: 16px;
            padding: 0.72rem 0.78rem;
            min-height: 84px;
        }
        .quick-stat-primary {
            background: linear-gradient(180deg, rgba(76,29,149,0.58), rgba(30,41,59,0.9));
            border: 1px solid rgba(167,139,250,0.28);
        }
        .quick-stat-up {
            background: linear-gradient(180deg, rgba(21,128,61,0.38), rgba(30,41,59,0.9));
            border: 1px solid rgba(74,222,128,0.24);
        }
        .quick-stat-down {
            background: linear-gradient(180deg, rgba(153,27,27,0.34), rgba(30,41,59,0.9));
            border: 1px solid rgba(248,113,113,0.22);
        }
        .quick-stat-label {
            color: #94a3b8;
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.32rem;
        }
        .quick-stat-value {
            color: #f8fafc;
            font-size: 1.28rem;
            font-weight: 800;
            line-height: 1.05;
            margin-bottom: 0.28rem;
        }
        .quick-stat-meta {
            color: #cbd5e1;
            font-size: 0.77rem;
            line-height: 1.25;
        }
        .detail-box {
            background: rgba(15,23,42,0.74);
            border: 1px solid rgba(148,163,184,.12);
            border-radius: 18px;
            padding: 0.82rem;
            margin-bottom: 0.75rem;
        }
        .detail-box-top {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 0.5rem;
            margin-bottom: 0.75rem;
        }
        .detail-box-title {
            color: #f8fafc;
            font-size: 0.95rem;
            font-weight: 800;
            letter-spacing: 0.02em;
        }
        .delta-pill-row {
            display: flex;
            gap: 0.35rem;
            flex-wrap: wrap;
            justify-content: flex-end;
        }
        .delta-pill {
            display: inline-block;
            padding: 0.2rem 0.42rem;
            border-radius: 999px;
            font-size: 0.69rem;
            font-weight: 700;
            white-space: nowrap;
        }
        .delta-up {
            background: rgba(34,197,94,0.12);
            color: #dcfce7;
            border: 1px solid rgba(34,197,94,0.16);
        }
        .delta-down {
            background: rgba(239,68,68,0.12);
            color: #fee2e2;
            border: 1px solid rgba(239,68,68,0.16);
        }
        .delta-flat {
            background: rgba(148,163,184,0.10);
            color: #e2e8f0;
            border: 1px solid rgba(148,163,184,0.14);
        }
        .detail-mini-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.45rem;
        }
        .detail-mini {
            background: rgba(2,6,23,0.35);
            border: 1px solid rgba(148,163,184,.10);
            border-radius: 14px;
            padding: 0.52rem 0.58rem;
        }
        .detail-mini-highlight {
            background: rgba(30,41,59,0.82);
            border: 1px solid rgba(139,92,246,0.22);
        }
        .detail-mini-label {
            color: #94a3b8;
            font-size: 0.69rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.2rem;
        }
        .detail-mini-value {
            color: #f8fafc;
            font-size: 1rem;
            font-weight: 800;
            line-height: 1.05;
        }
        .hero-note {
            color: #94a3b8;
            font-size: 0.82rem;
            margin-top: 0.55rem;
            line-height: 1.35;
        }
        .matchup-chip {
            display: inline-block;
            padding: 0.28rem 0.54rem;
            border-radius: 999px;
            font-size: 0.73rem;
            font-weight: 800;
            letter-spacing: 0.02em;
        }
        .matchup-good {
            background: rgba(34,197,94,0.12);
            color: #dcfce7;
            border: 1px solid rgba(34,197,94,0.16);
        }
        .matchup-neutral {
            background: rgba(148,163,184,0.10);
            color: #e2e8f0;
            border: 1px solid rgba(148,163,184,0.14);
        }
        .matchup-bad {
            background: rgba(239,68,68,0.12);
            color: #fee2e2;
            border: 1px solid rgba(239,68,68,0.16);
        }
        .ranking-shell {
            background: rgba(15,23,42,0.72);
            border: 1px solid rgba(148,163,184,.12);
            border-radius: 20px;
            padding: 0.9rem 1rem 0.65rem 1rem;
            margin-top: 0.85rem;
            margin-bottom: 0.85rem;
        }
        .ranking-row {
            display: grid;
            grid-template-columns: 44px 1.6fr 0.9fr 0.9fr 0.9fr;
            gap: 0.45rem;
            align-items: center;
            padding: 0.58rem 0.1rem;
            border-bottom: 1px solid rgba(148,163,184,.08);
        }
        .ranking-row:last-child {
            border-bottom: none;
        }
        .ranking-rank {
            width: 32px;
            height: 32px;
            border-radius: 999px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: rgba(139,92,246,0.16);
            color: #f3e8ff;
            font-size: 0.78rem;
            font-weight: 800;
        }
        .ranking-player {
            color: #f8fafc;
            font-size: 0.92rem;
            font-weight: 700;
            line-height: 1.15;
        }
        .ranking-sub {
            color: #94a3b8;
            font-size: 0.77rem;
            margin-top: 0.08rem;
        }
        .ranking-stat {
            text-align: center;
        }
        .ranking-stat-label {
            color: #94a3b8;
            font-size: 0.66rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.08rem;
        }
        .ranking-stat-value {
            color: #f8fafc;
            font-size: 0.92rem;
            font-weight: 800;
        }
        .ranking-good .ranking-stat-value {
            color: #86efac;
        }
        .ranking-bad .ranking-stat-value {
            color: #fca5a5;
        }
        .focus-shell {
            background: linear-gradient(180deg, rgba(17,24,39,0.94), rgba(15,23,42,0.94));
            border: 1px solid rgba(148,163,184,.14);
            border-radius: 22px;
            padding: 1rem 1rem 0.75rem 1rem;
            margin-top: 1rem;
            margin-bottom: 0.75rem;
        }
        .focus-title {
            color: #f8fafc;
            font-size: 1.2rem;
            font-weight: 800;
            margin-bottom: 0.15rem;
        }
        .focus-sub {
            color: #94a3b8;
            font-size: 0.88rem;
            margin-bottom: 0.5rem;
        }
        .micro-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.45rem;
            margin-top: 0.65rem;
            margin-bottom: 0.1rem;
        }
        .micro-stat {
            background: rgba(2,6,23,0.34);
            border: 1px solid rgba(148,163,184,.10);
            border-radius: 14px;
            padding: 0.52rem 0.58rem;
        }
        .micro-stat-emph {
            background: linear-gradient(180deg, rgba(76,29,149,0.46), rgba(30,41,59,0.9));
            border: 1px solid rgba(167,139,250,0.24);
        }
        .micro-stat-good {
            background: linear-gradient(180deg, rgba(21,128,61,0.34), rgba(30,41,59,0.9));
            border: 1px solid rgba(74,222,128,0.22);
        }
        .micro-stat-bad {
            background: linear-gradient(180deg, rgba(153,27,27,0.34), rgba(30,41,59,0.9));
            border: 1px solid rgba(248,113,113,0.22);
        }
        .micro-label {
            color: #94a3b8;
            font-size: 0.68rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.18rem;
        }
        .micro-value {
            color: #f8fafc;
            font-size: 1.02rem;
            font-weight: 800;
            line-height: 1.05;
            margin-bottom: 0.15rem;
        }
        .micro-meta {
            color: #cbd5e1;
            font-size: 0.73rem;
            line-height: 1.2;
        }
        /* OTIMIZAÇÕES PARA TABLETS E TELAS MÉDIAS */
        @media (max-width: 1200px) {
            .player-quick-grid {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
        }
        
        /* OTIMIZAÇÕES PARA SMARTPHONES (MOBILE) */
        @media (max-width: 768px) {
            .main-title {
                font-size: 1.7rem;
                padding-top: 0.2rem;
            }
            .subtitle {
                font-size: 0.9rem;
                margin-bottom: 0.8rem;
            }
            .hero-pills {
                gap: 0.3rem;
                margin-bottom: 0.8rem;
            }
            .hero-pill {
                font-size: 0.65rem;
                padding: 0.3rem 0.6rem;
            }
            .matchup-shell {
                padding: 0.8rem 0.5rem;
            }
            .team-title {
                font-size: 1.1rem;
            }
            .ranking-row {
                grid-template-columns: 32px 1fr 0.8fr 0.8fr; 
                gap: 0.2rem;
            }
            .ranking-row .ranking-stat:last-child {
                display: none; 
            }
            .ranking-player {
                font-size: 0.85rem;
            }
            .micro-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr)); 
            }
            .player-quick-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr)); 
            }
            .detail-mini-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr)) !important; 
            }
            .quick-stat, .detail-box, .summary-card {
                padding: 0.6rem;
            }
            .player-headline-value {
                font-size: 1.5rem;
            }
        }
        
        /* PARA TELAS MUITO PEQUENAS (Ex: iPhone SE) */
        @media (max-width: 380px) {
            .player-quick-grid, .micro-grid {
                grid-template-columns: 1fr; 
            }
            .ranking-row {
                grid-template-columns: 28px 1fr 0.8fr; 
            }
            .ranking-row .ranking-stat:nth-last-child(2) {
                display: none;
            }
        }
        </style>        
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=54000, show_spinner=False)
def get_games_for_date(target_date: date) -> pd.DataFrame:
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
            columns=[
                "GAME_ID",
                "HOME_TEAM_ID",
                "VISITOR_TEAM_ID",
                "GAME_STATUS_TEXT",
                "home_team_name",
                "away_team_name",
                "label",
            ]
        )

    rows = []
    for _, row in game_header.iterrows():
        home_team_id = int(row["HOME_TEAM_ID"])
        away_team_id = int(row["VISITOR_TEAM_ID"])

        home_team_name = TEAM_LOOKUP.get(home_team_id, {}).get("full_name", str(home_team_id))
        away_team_name = TEAM_LOOKUP.get(away_team_id, {}).get("full_name", str(away_team_id))
        game_status_text = row.get("GAME_STATUS_TEXT", "Sem status")

        rows.append(
            {
                "GAME_ID": str(row["GAME_ID"]),
                "HOME_TEAM_ID": home_team_id,
                "VISITOR_TEAM_ID": away_team_id,
                "GAME_STATUS_TEXT": game_status_text,
                "home_team_name": home_team_name,
                "away_team_name": away_team_name,
                "label": f"{away_team_name} @ {home_team_name} • {game_status_text}",
            }
        )

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
        return pd.DataFrame(
            columns=["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GP", "MIN", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]
        )

    keep_cols = ["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GP", "MIN", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]
    return df[[c for c in keep_cols if c in df.columns]].copy()


@st.cache_data(ttl=54000, show_spinner=False)
def get_player_log(player_id: int, season: str) -> pd.DataFrame:
    # Agora buscamos todas as fases da temporada para não perder nenhum jogo!
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

def _weighted_profile_from_df(df: pd.DataFrame) -> dict:
    if df.empty or "GP" not in df.columns:
        return {
            "PTS": 0.0,
            "REB": 0.0,
            "AST": 0.0,
            "FG3M": 0.0,
            "FGA": 0.0,
            "FG3A": 0.0,
            "PRA": 0.0,
            "GP": 0.0,
        }

    work_df = df.copy()
    for col in ["GP", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]:
        work_df[col] = pd.to_numeric(work_df[col], errors="coerce").fillna(0.0)

    total_gp = float(work_df["GP"].sum())
    if total_gp <= 0:
        return {
            "PTS": 0.0,
            "REB": 0.0,
            "AST": 0.0,
            "FG3M": 0.0,
            "FGA": 0.0,
            "FG3A": 0.0,
            "PRA": 0.0,
            "GP": 0.0,
        }

    pts = float((work_df["PTS"] * work_df["GP"]).sum() / total_gp)
    reb = float((work_df["REB"] * work_df["GP"]).sum() / total_gp)
    ast = float((work_df["AST"] * work_df["GP"]).sum() / total_gp)
    fg3m = float((work_df["FG3M"] * work_df["GP"]).sum() / total_gp)
    fga = float((work_df["FGA"] * work_df["GP"]).sum() / total_gp)
    fg3a = float((work_df["FG3A"] * work_df["GP"]).sum() / total_gp)

    return {
        "PTS": pts,
        "REB": reb,
        "AST": ast,
        "FG3M": fg3m,
        "FGA": fga,
        "FG3A": fg3a,
        "PRA": pts + reb + ast,
        "GP": total_gp,
    }


@st.cache_data(ttl=21600, show_spinner=False)
def get_position_allowed_profile(season: str, opponent_team_id: int, position_group: str) -> dict:
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
        raw_df = frames[0].copy() if frames else pd.DataFrame()
    except Exception:
        raw_df = pd.DataFrame()

    return _weighted_profile_from_df(raw_df)


@st.cache_data(ttl=21600, show_spinner=False)
def get_league_position_baseline(season: str, position_group: str) -> dict:
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
        raw_df = frames[0].copy() if frames else pd.DataFrame()
    except Exception:
        raw_df = pd.DataFrame()

    return _weighted_profile_from_df(raw_df)


def get_position_opponent_profile(season: str, opponent_team_id: int, position_group: str) -> dict:
    opp_profile = get_position_allowed_profile(season, opponent_team_id, position_group)
    league_profile = get_league_position_baseline(season, position_group)
    matchup_diff = opp_profile["PRA"] - league_profile["PRA"]

    return {
        "POSITION_GROUP": position_group,
        "OPP_PTS_ALLOWED": opp_profile["PTS"],
        "OPP_REB_ALLOWED": opp_profile["REB"],
        "OPP_AST_ALLOWED": opp_profile["AST"],
        "OPP_PRA_ALLOWED": opp_profile["PRA"],
        "OPP_3PM_ALLOWED": opp_profile["FG3M"],
        "OPP_FGA_ALLOWED": opp_profile["FGA"],
        "OPP_3PA_ALLOWED": opp_profile["FG3A"],
        "LEAGUE_PTS_BASELINE": league_profile["PTS"],
        "LEAGUE_REB_BASELINE": league_profile["REB"],
        "LEAGUE_AST_BASELINE": league_profile["AST"],
        "LEAGUE_3PM_BASELINE": league_profile["FG3M"],
        "LEAGUE_FGA_BASELINE": league_profile["FGA"],
        "LEAGUE_3PA_BASELINE": league_profile["FG3A"],
        "LEAGUE_PRA_BASELINE": league_profile["PRA"],
        "MATCHUP_DIFF": matchup_diff,
        "MATCHUP_LABEL": classify_matchup_tier(matchup_diff),
    }


    def weighted_profile(df: pd.DataFrame) -> dict:
        if df.empty or "GP" not in df.columns:
            return {
                "PTS": 0.0,
                "REB": 0.0,
                "AST": 0.0,
                "FG3M": 0.0,
                "FGA": 0.0,
                "FG3A": 0.0,
                "PRA": 0.0,
                "GP": 0.0,
            }

        work_df = df.copy()
        for col in ["GP", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]:
            work_df[col] = pd.to_numeric(work_df[col], errors="coerce").fillna(0.0)

        total_gp = float(work_df["GP"].sum())
        if total_gp <= 0:
            return {
                "PTS": 0.0,
                "REB": 0.0,
                "AST": 0.0,
                "FG3M": 0.0,
                "FGA": 0.0,
                "FG3A": 0.0,
                "PRA": 0.0,
                "GP": 0.0,
            }

        pts = float((work_df["PTS"] * work_df["GP"]).sum() / total_gp)
        reb = float((work_df["REB"] * work_df["GP"]).sum() / total_gp)
        ast = float((work_df["AST"] * work_df["GP"]).sum() / total_gp)
        fg3m = float((work_df["FG3M"] * work_df["GP"]).sum() / total_gp)
        fga = float((work_df["FGA"] * work_df["GP"]).sum() / total_gp)
        fg3a = float((work_df["FG3A"] * work_df["GP"]).sum() / total_gp)

        return {
            "PTS": pts,
            "REB": reb,
            "AST": ast,
            "FG3M": fg3m,
            "FGA": fga,
            "FG3A": fg3a,
            "PRA": pts + reb + ast,
            "GP": total_gp,
        }

    opp_profile = weighted_profile(fetch(position_group, opponent_team_id))
    league_profile = weighted_profile(fetch(position_group, 0))
    matchup_diff = opp_profile["PRA"] - league_profile["PRA"]

    return {
        "POSITION_GROUP": position_group,
        "OPP_PTS_ALLOWED": opp_profile["PTS"],
        "OPP_REB_ALLOWED": opp_profile["REB"],
        "OPP_AST_ALLOWED": opp_profile["AST"],
        "OPP_PRA_ALLOWED": opp_profile["PRA"],
        "OPP_3PM_ALLOWED": opp_profile["FG3M"],
        "OPP_FGA_ALLOWED": opp_profile["FGA"],
        "OPP_3PA_ALLOWED": opp_profile["FG3A"],
        "LEAGUE_PTS_BASELINE": league_profile["PTS"],
        "LEAGUE_REB_BASELINE": league_profile["REB"],
        "LEAGUE_AST_BASELINE": league_profile["AST"],
        "LEAGUE_3PM_BASELINE": league_profile["FG3M"],
        "LEAGUE_FGA_BASELINE": league_profile["FGA"],
        "LEAGUE_3PA_BASELINE": league_profile["FG3A"],
        "LEAGUE_PRA_BASELINE": league_profile["PRA"],
        "MATCHUP_DIFF": matchup_diff,
        "MATCHUP_LABEL": classify_matchup_tier(matchup_diff),
    }

    def weighted_profile(df: pd.DataFrame) -> dict:
        if df.empty or "GP" not in df.columns:
            return {
                "PTS": 0.0,
                "REB": 0.0,
                "AST": 0.0,
                "FG3M": 0.0,
                "FGA": 0.0,
                "FG3A": 0.0,
                "PRA": 0.0,
                "GP": 0.0,
            }

        work_df = df.copy()
        for col in ["GP", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]:
            work_df[col] = pd.to_numeric(work_df[col], errors="coerce").fillna(0.0)

        total_gp = float(work_df["GP"].sum())
        if total_gp <= 0:
            return {
                "PTS": 0.0,
                "REB": 0.0,
                "AST": 0.0,
                "FG3M": 0.0,
                "FGA": 0.0,
                "FG3A": 0.0,
                "PRA": 0.0,
                "GP": 0.0,
            }

        pts = float((work_df["PTS"] * work_df["GP"]).sum() / total_gp)
        reb = float((work_df["REB"] * work_df["GP"]).sum() / total_gp)
        ast = float((work_df["AST"] * work_df["GP"]).sum() / total_gp)
        fg3m = float((work_df["FG3M"] * work_df["GP"]).sum() / total_gp)
        fga = float((work_df["FGA"] * work_df["GP"]).sum() / total_gp)
        fg3a = float((work_df["FG3A"] * work_df["GP"]).sum() / total_gp)

        return {
            "PTS": pts,
            "REB": reb,
            "AST": ast,
            "FG3M": fg3m,
            "FGA": fga,
            "FG3A": fg3a,
            "PRA": pts + reb + ast,
            "GP": total_gp,
        }

    opp_profile = weighted_profile(fetch(position_group, opponent_team_id))
    league_profile = weighted_profile(fetch(position_group, 0))
    matchup_diff = opp_profile["PRA"] - league_profile["PRA"]

    return {
        "POSITION_GROUP": position_group,
        "OPP_PTS_ALLOWED": opp_profile["PTS"],
        "OPP_REB_ALLOWED": opp_profile["REB"],
        "OPP_AST_ALLOWED": opp_profile["AST"],
        "OPP_PRA_ALLOWED": opp_profile["PRA"],
        "OPP_3PM_ALLOWED": opp_profile["FG3M"],
        "OPP_FGA_ALLOWED": opp_profile["FGA"],
        "OPP_3PA_ALLOWED": opp_profile["FG3A"],
        "LEAGUE_PTS_BASELINE": league_profile["PTS"],
        "LEAGUE_REB_BASELINE": league_profile["REB"],
        "LEAGUE_AST_BASELINE": league_profile["AST"],
        "LEAGUE_3PM_BASELINE": league_profile["FG3M"],
        "LEAGUE_FGA_BASELINE": league_profile["FGA"],
        "LEAGUE_3PA_BASELINE": league_profile["FG3A"],
        "LEAGUE_PRA_BASELINE": league_profile["PRA"],
        "MATCHUP_DIFF": matchup_diff,
        "MATCHUP_LABEL": classify_matchup_tier(matchup_diff),
    }
    def weighted_profile(df: pd.DataFrame) -> dict:
        if df.empty or "GP" not in df.columns:
            return {"PTS": 0.0, "REB": 0.0, "AST": 0.0, "FG3M": 0.0, "FGA": 0.0, "FG3A": 0.0, "PRA": 0.0, "GP": 0.0}

        work_df = df.copy()
        for col in ["GP", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]:
            work_df[col] = pd.to_numeric(work_df[col], errors="coerce").fillna(0.0)

        total_gp = float(work_df["GP"].sum())
        if total_gp <= 0:
            return {"PTS": 0.0, "REB": 0.0, "AST": 0.0, "FG3M": 0.0, "FGA": 0.0, "FG3A": 0.0, "PRA": 0.0, "GP": 0.0}

        pts = float((work_df["PTS"] * work_df["GP"]).sum() / total_gp)
        reb = float((work_df["REB"] * work_df["GP"]).sum() / total_gp)
        ast = float((work_df["AST"] * work_df["GP"]).sum() / total_gp)
        fg3m = float((work_df["FG3M"] * work_df["GP"]).sum() / total_gp)
        fga = float((work_df["FGA"] * work_df["GP"]).sum() / total_gp)
        fg3a = float((work_df["FG3A"] * work_df["GP"]).sum() / total_gp)
        return {"PTS": pts, "REB": reb, "AST": ast, "FG3M": fg3m, "FGA": fga, "FG3A": fg3a, "PRA": pts + reb + ast, "GP": total_gp}

    opp_profile = weighted_profile(fetch(position_group, opponent_team_id))
    league_profile = weighted_profile(fetch(position_group, 0))
    matchup_diff = opp_profile["PRA"] - league_profile["PRA"]

    return {
        "POSITION_GROUP": position_group,
        "OPP_PTS_ALLOWED": opp_profile["PTS"],
        "OPP_REB_ALLOWED": opp_profile["REB"],
        "OPP_AST_ALLOWED": opp_profile["AST"],
        "OPP_PRA_ALLOWED": opp_profile["PRA"],
        "OPP_3PM_ALLOWED": opp_profile["FG3M"],
        "OPP_FGA_ALLOWED": opp_profile["FGA"],
        "OPP_3PA_ALLOWED": opp_profile["FG3A"],
        "LEAGUE_PTS_BASELINE": league_profile["PTS"],
        "LEAGUE_REB_BASELINE": league_profile["REB"],
        "LEAGUE_AST_BASELINE": league_profile["AST"],
        "LEAGUE_3PM_BASELINE": league_profile["FG3M"],
        "LEAGUE_FGA_BASELINE": league_profile["FGA"],
        "LEAGUE_3PA_BASELINE": league_profile["FG3A"],
        "LEAGUE_PRA_BASELINE": league_profile["PRA"],
        "MATCHUP_DIFF": matchup_diff,
        "MATCHUP_LABEL": classify_matchup_tier(matchup_diff),
    }


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


@st.cache_data(ttl=54000, show_spinner=False)
def build_team_table(team_id: int, season: str) -> pd.DataFrame:
    roster = get_team_roster(team_id, season)
    season_stats = get_league_player_stats(season, last_n_games=0)
    last5_stats = get_league_player_stats(season, last_n_games=5)
    last10_stats = get_league_player_stats(season, last_n_games=10)

    if roster.empty:
        return pd.DataFrame()

    roster = roster[[c for c in ["PLAYER", "PLAYER_ID", "POSITION"] if c in roster.columns]].copy()
    if "POSITION" not in roster.columns:
        roster["POSITION"] = ""

    season_view = (
        pd.DataFrame(columns=["PLAYER_ID", "SEASON_GP", "SEASON_MIN", "SEASON_PTS", "SEASON_REB", "SEASON_AST", "SEASON_3PM", "SEASON_FGA", "SEASON_3PA"])
        if season_stats.empty
        else season_stats.rename(
            columns={
                "GP": "SEASON_GP",
                "MIN": "SEASON_MIN",
                "PTS": "SEASON_PTS",
                "REB": "SEASON_REB",
                "AST": "SEASON_AST",
                "FG3M": "SEASON_3PM",
                "FGA": "SEASON_FGA",
                "FG3A": "SEASON_3PA",
            }
        )
    )

    last5_view = (
        pd.DataFrame(columns=["PLAYER_ID", "L5_GP", "L5_MIN", "L5_PTS", "L5_REB", "L5_AST", "L5_3PM", "L5_FGA", "L5_3PA"])
        if last5_stats.empty
        else last5_stats.rename(
            columns={
                "GP": "L5_GP",
                "MIN": "L5_MIN",
                "PTS": "L5_PTS",
                "REB": "L5_REB",
                "AST": "L5_AST",
                "FG3M": "L5_3PM",
                "FGA": "L5_FGA",
                "FG3A": "L5_3PA",
            }
        )
    )

    last10_view = (
        pd.DataFrame(columns=["PLAYER_ID", "L10_GP", "L10_MIN", "L10_PTS", "L10_REB", "L10_AST", "L10_3PM", "L10_FGA", "L10_3PA"])
        if last10_stats.empty
        else last10_stats.rename(
            columns={
                "GP": "L10_GP",
                "MIN": "L10_MIN",
                "PTS": "L10_PTS",
                "REB": "L10_REB",
                "AST": "L10_AST",
                "FG3M": "L10_3PM",
                "FGA": "L10_FGA",
                "FG3A": "L10_3PA",
            }
        )
    )

    team_df = roster.merge(
        season_view[["PLAYER_ID", "SEASON_GP", "SEASON_MIN", "SEASON_PTS", "SEASON_REB", "SEASON_AST", "SEASON_3PM", "SEASON_FGA", "SEASON_3PA"]],
        on="PLAYER_ID",
        how="left",
    ).merge(
        last5_view[["PLAYER_ID", "L5_GP", "L5_MIN", "L5_PTS", "L5_REB", "L5_AST", "L5_3PM", "L5_FGA", "L5_3PA"]],
        on="PLAYER_ID",
        how="left",
    ).merge(
        last10_view[["PLAYER_ID", "L10_GP", "L10_MIN", "L10_PTS", "L10_REB", "L10_AST", "L10_3PM", "L10_FGA", "L10_3PA"]],
        on="PLAYER_ID",
        how="left",
    )

    numeric_cols = [
        "SEASON_GP", "SEASON_MIN", "SEASON_PTS", "SEASON_REB", "SEASON_AST", "SEASON_3PM", "SEASON_FGA", "SEASON_3PA",
        "L5_GP", "L5_MIN", "L5_PTS", "L5_REB", "L5_AST", "L5_3PM", "L5_FGA", "L5_3PA",
        "L10_GP", "L10_MIN", "L10_PTS", "L10_REB", "L10_AST", "L10_3PM", "L10_FGA", "L10_3PA",
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

    def classify_trend(delta_pra_l10: float) -> str:
        if delta_pra_l10 >= 3.0:
            return "🔥 Forte"
        if delta_pra_l10 >= 1.0:
            return "⬆️ Boa"
        if delta_pra_l10 <= -3.0:
            return "🥶 Queda"
        if delta_pra_l10 <= -1.0:
            return "⬇️ Fraca"
        return "➖ Neutra"

    team_df["TREND"] = team_df["DELTA_PRA_L10"].apply(classify_trend)
    team_df["POSITION_GROUP"] = team_df["POSITION"].apply(normalize_position_group)
    team_df["PLAYER_KEY"] = team_df["PLAYER"].apply(normalize_text)

    team_df["ROLE"] = "Reserva"
    starter_ids = (
        team_df.sort_values(by=["SEASON_MIN", "SEASON_GP", "PLAYER"], ascending=[False, False, True])
        .head(5)["PLAYER_ID"]
        .tolist()
    )
    team_df.loc[team_df["PLAYER_ID"].isin(starter_ids), "ROLE"] = "Titular provável"

    return team_df[
        [
            "PLAYER_ID", "PLAYER", "PLAYER_KEY", "POSITION", "POSITION_GROUP", "ROLE",
            "SEASON_GP", "SEASON_MIN",
            "SEASON_PTS", "L5_PTS", "L10_PTS",
            "SEASON_REB", "L5_REB", "L10_REB",
            "SEASON_AST", "L5_AST", "L10_AST",
            "SEASON_3PM", "L5_3PM", "L10_3PM",
            "SEASON_FGA", "L5_FGA", "L10_FGA",
            "SEASON_3PA", "L5_3PA", "L10_3PA",
            "SEASON_PRA", "L5_PRA", "L10_PRA",
            "DELTA_PRA_L5", "DELTA_PRA_L10", "TREND",
        ]
    ].copy()


def build_form_context(team_df: pd.DataFrame, team_logs: pd.DataFrame) -> pd.DataFrame:
    if team_df.empty:
        return team_df

    scalar_defaults = {
        "HIT_RATE_L10": 0.0,
        "HIT_RATE_L10_TEXT": "-",
        "PTS_HIT_RATE_L10": 0.0,
        "PTS_HIT_RATE_L10_TEXT": "-",
        "REB_HIT_RATE_L10": 0.0,
        "REB_HIT_RATE_L10_TEXT": "-",
        "AST_HIT_RATE_L10": 0.0,
        "AST_HIT_RATE_L10_TEXT": "-",
        "THREE_PM_HIT_RATE_L10": 0.0,
        "THREE_PM_HIT_RATE_L10_TEXT": "-",
        "FGA_HIT_RATE_L10": 0.0,
        "FGA_HIT_RATE_L10_TEXT": "-",
        "THREE_PA_HIT_RATE_L10": 0.0,
        "THREE_PA_HIT_RATE_L10_TEXT": "-",
        "OSC_L10": 0.0,
        "OSC_CLASS": "-",
        "FORM_SIGNAL": "→ Estável",
        # --- NOVO: VARIÁVEIS DO SPLIT CASA/FORA ---
        "HOME_PRA": 0.0, "AWAY_PRA": 0.0,
        "HOME_PTS": 0.0, "AWAY_PTS": 0.0,
        "HOME_REB": 0.0, "AWAY_REB": 0.0,
        "HOME_AST": 0.0, "AWAY_AST": 0.0,
        "HOME_3PM": 0.0, "AWAY_3PM": 0.0,
        "HOME_FGA": 0.0, "AWAY_FGA": 0.0,
        "HOME_3PA": 0.0, "AWAY_3PA": 0.0,
    }
    
    list_defaults = {
        "RECENT_PRA_L10": [],
        "RECENT_PTS_L10": [],
        "RECENT_REB_L10": [],
        "RECENT_AST_L10": [],
        "RECENT_3PM_L10": [],
        "RECENT_FGA_L10": [],
        "RECENT_3PA_L10": [],
    }

    if team_logs.empty:
        enriched = team_df.copy()
        for col, default in scalar_defaults.items():
            enriched[col] = default
        for col, default in list_defaults.items():
            enriched[col] = [default.copy() for _ in range(len(enriched))]
        return enriched

    threshold_map = team_df.set_index("PLAYER_ID")[["SEASON_PRA", "SEASON_PTS", "SEASON_REB", "SEASON_AST", "SEASON_3PM", "SEASON_FGA", "SEASON_3PA"]].to_dict("index")
    metrics = []

    for player_id, player_logs in team_logs.groupby("PLAYER_ID"):
        recent10 = player_logs.sort_values("GAME_DATE", ascending=False).head(10).copy()
        sample_size = len(recent10)
        thresholds = threshold_map.get(player_id, {})
        
        
        if sample_size == 0:
            metrics.append({"PLAYER_ID": player_id, **scalar_defaults, **list_defaults})
            continue

        pra_threshold = float(thresholds.get("SEASON_PRA", 0.0))
        pts_threshold = float(thresholds.get("SEASON_PTS", 0.0))
        reb_threshold = float(thresholds.get("SEASON_REB", 0.0))
        ast_threshold = float(thresholds.get("SEASON_AST", 0.0))
        three_pm_threshold = float(thresholds.get("SEASON_3PM", 0.0))
        fga_threshold = float(thresholds.get("SEASON_FGA", 0.0))
        three_pa_threshold = float(thresholds.get("SEASON_3PA", 0.0))

        hit_count_pra = int((recent10["PRA"] >= pra_threshold).sum()) if pra_threshold > 0 else 0
        hit_count_pts = int((recent10["PTS"] >= pts_threshold).sum()) if pts_threshold > 0 else 0
        hit_count_reb = int((recent10["REB"] >= reb_threshold).sum()) if reb_threshold > 0 else 0
        hit_count_ast = int((recent10["AST"] >= ast_threshold).sum()) if ast_threshold > 0 else 0
        hit_count_3pm = int((recent10["FG3M"] >= three_pm_threshold).sum()) if three_pm_threshold > 0 else 0
        hit_count_fga = int((recent10["FGA"] >= fga_threshold).sum()) if fga_threshold > 0 else 0
        hit_count_3pa = int((recent10["FG3A"] >= three_pa_threshold).sum()) if three_pa_threshold > 0 else 0

        osc_value = float(recent10["PRA"].std(ddof=0)) if sample_size > 1 else 0.0
        ordered = recent10.sort_values("GAME_DATE")
        slope = float(np.polyfit(range(len(ordered)), ordered["PRA"], 1)[0]) if len(ordered) >= 3 else 0.0

        # --- NOVO: CÁLCULO DE SPLIT CASA/FORA ---
        home_logs = player_logs[player_logs["MATCHUP"].str.contains("vs.", regex=False, na=False)]
        away_logs = player_logs[player_logs["MATCHUP"].str.contains("@", regex=False, na=False)]

        metrics.append(
            {
                "PLAYER_ID": player_id,
                "HIT_RATE_L10": float(hit_count_pra / sample_size),
                "HIT_RATE_L10_TEXT": format_ratio_text(hit_count_pra, sample_size),
                "PTS_HIT_RATE_L10": float(hit_count_pts / sample_size),
                "PTS_HIT_RATE_L10_TEXT": format_ratio_text(hit_count_pts, sample_size),
                "REB_HIT_RATE_L10": float(hit_count_reb / sample_size),
                "REB_HIT_RATE_L10_TEXT": format_ratio_text(hit_count_reb, sample_size),
                "AST_HIT_RATE_L10": float(hit_count_ast / sample_size),
                "AST_HIT_RATE_L10_TEXT": format_ratio_text(hit_count_ast, sample_size),
                "THREE_PM_HIT_RATE_L10": float(hit_count_3pm / sample_size),
                "THREE_PM_HIT_RATE_L10_TEXT": format_ratio_text(hit_count_3pm, sample_size),
                "FGA_HIT_RATE_L10": float(hit_count_fga / sample_size),
                "FGA_HIT_RATE_L10_TEXT": format_ratio_text(hit_count_fga, sample_size),
                "THREE_PA_HIT_RATE_L10": float(hit_count_3pa / sample_size),
                "THREE_PA_HIT_RATE_L10_TEXT": format_ratio_text(hit_count_3pa, sample_size),
                "OSC_L10": osc_value,
                "OSC_CLASS": classify_oscillation(osc_value),
                "FORM_SIGNAL": classify_form_signal(slope),
                "RECENT_PRA_L10": recent10["PRA"].round(1).tolist(),
                "RECENT_PTS_L10": recent10["PTS"].round(1).tolist(),
                "RECENT_REB_L10": recent10["REB"].round(1).tolist(),
                "RECENT_AST_L10": recent10["AST"].round(1).tolist(),
                "RECENT_3PM_L10": recent10["FG3M"].round(1).tolist(),
                "RECENT_FGA_L10": recent10["FGA"].round(1).tolist(),
                "RECENT_3PA_L10": recent10["FG3A"].round(1).tolist(),
                "RECENT_3PA_L10": recent10["FG3A"].round(1).tolist(),
                # --- NOVO: SALVANDO AS MÉDIAS DO SPLIT ---
                "HOME_PRA": float(home_logs["PRA"].mean()) if not home_logs.empty else 0.0,
                "AWAY_PRA": float(away_logs["PRA"].mean()) if not away_logs.empty else 0.0,
                "HOME_PTS": float(home_logs["PTS"].mean()) if not home_logs.empty else 0.0,
                "AWAY_PTS": float(away_logs["PTS"].mean()) if not away_logs.empty else 0.0,
                "HOME_REB": float(home_logs["REB"].mean()) if not home_logs.empty else 0.0,
                "AWAY_REB": float(away_logs["REB"].mean()) if not away_logs.empty else 0.0,
                "HOME_AST": float(home_logs["AST"].mean()) if not home_logs.empty else 0.0,
                "AWAY_AST": float(away_logs["AST"].mean()) if not away_logs.empty else 0.0,
                "HOME_3PM": float(home_logs["FG3M"].mean()) if not home_logs.empty else 0.0,
                "AWAY_3PM": float(away_logs["FG3M"].mean()) if not away_logs.empty else 0.0,
                "HOME_FGA": float(home_logs["FGA"].mean()) if not home_logs.empty else 0.0,
                "AWAY_FGA": float(away_logs["FGA"].mean()) if not away_logs.empty else 0.0,
                "HOME_3PA": float(home_logs["FG3A"].mean()) if not home_logs.empty else 0.0,
                "AWAY_3PA": float(away_logs["FG3A"].mean()) if not away_logs.empty else 0.0,
            }
        )

    metrics_df = pd.DataFrame(metrics)
    enriched = team_df.merge(metrics_df, on="PLAYER_ID", how="left")

    for col, default in scalar_defaults.items():
        if isinstance(default, float):
            enriched[col] = pd.to_numeric(enriched[col], errors="coerce").fillna(default)
        else:
            enriched[col] = enriched[col].fillna(default)

    for col in list_defaults:
        if col not in enriched.columns:
            enriched[col] = [[] for _ in range(len(enriched))]
        enriched[col] = enriched[col].apply(lambda x: x if isinstance(x, list) else [])

    return enriched


def enrich_team_with_context(
    team_df: pd.DataFrame,
    team_id: int,
    opponent_team_id: int,
    opponent_team_name: str,
    season: str,
) -> pd.DataFrame:
    if team_df.empty:
        return team_df

    team_logs = get_team_player_logs(team_id, season)
    enriched = build_form_context(team_df, team_logs)

    matchup_rows = [get_position_opponent_profile(season, opponent_team_id, pos) for pos in ["G", "F", "C"]]
    matchup_df = pd.DataFrame(matchup_rows)

    if matchup_df.empty:
        enriched["OPP_TEAM_NAME"] = opponent_team_name
        fallback_cols = [
            "OPP_PTS_ALLOWED", "OPP_REB_ALLOWED", "OPP_AST_ALLOWED", "OPP_PRA_ALLOWED",
            "OPP_3PM_ALLOWED", "OPP_FGA_ALLOWED", "OPP_3PA_ALLOWED",
            "LEAGUE_PTS_BASELINE", "LEAGUE_REB_BASELINE", "LEAGUE_AST_BASELINE", "LEAGUE_PRA_BASELINE",
            "LEAGUE_3PM_BASELINE", "LEAGUE_FGA_BASELINE", "LEAGUE_3PA_BASELINE", "MATCHUP_DIFF",
        ]
        for col in fallback_cols:
            enriched[col] = 0.0
        enriched["MATCHUP_LABEL"] = "Neutro"
    else:
        enriched = enriched.merge(matchup_df, on="POSITION_GROUP", how="left")
        enriched["OPP_TEAM_NAME"] = opponent_team_name
        for col in [
            "OPP_PTS_ALLOWED", "OPP_REB_ALLOWED", "OPP_AST_ALLOWED", "OPP_PRA_ALLOWED", "OPP_3PM_ALLOWED", "OPP_FGA_ALLOWED", "OPP_3PA_ALLOWED",
            "LEAGUE_PTS_BASELINE", "LEAGUE_REB_BASELINE", "LEAGUE_AST_BASELINE", "LEAGUE_PRA_BASELINE",
            "LEAGUE_3PM_BASELINE", "LEAGUE_FGA_BASELINE", "LEAGUE_3PA_BASELINE", "MATCHUP_DIFF",
        ]:
            enriched[col] = pd.to_numeric(enriched[col], errors="coerce").fillna(0.0)
        enriched["MATCHUP_LABEL"] = enriched["MATCHUP_LABEL"].fillna("Neutro")

    enriched["PROJ_PTS"] = enriched.apply(lambda row: calculate_projection(row["SEASON_PTS"], row["L10_PTS"], row["L5_PTS"], row["OPP_PTS_ALLOWED"], row["LEAGUE_PTS_BASELINE"]), axis=1)
    enriched["PROJ_REB"] = enriched.apply(lambda row: calculate_projection(row["SEASON_REB"], row["L10_REB"], row["L5_REB"], row["OPP_REB_ALLOWED"], row["LEAGUE_REB_BASELINE"]), axis=1)
    enriched["PROJ_AST"] = enriched.apply(lambda row: calculate_projection(row["SEASON_AST"], row["L10_AST"], row["L5_AST"], row["OPP_AST_ALLOWED"], row["LEAGUE_AST_BASELINE"]), axis=1)
    enriched["PROJ_3PM"] = enriched.apply(lambda row: calculate_projection(row["SEASON_3PM"], row["L10_3PM"], row["L5_3PM"], row["OPP_3PM_ALLOWED"], row["LEAGUE_3PM_BASELINE"]), axis=1)
    enriched["PROJ_FGA"] = enriched.apply(lambda row: calculate_projection(row["SEASON_FGA"], row["L10_FGA"], row["L5_FGA"], row["OPP_FGA_ALLOWED"], row["LEAGUE_FGA_BASELINE"]), axis=1)
    enriched["PROJ_3PA"] = enriched.apply(lambda row: calculate_projection(row["SEASON_3PA"], row["L10_3PA"], row["L5_3PA"], row["OPP_3PA_ALLOWED"], row["LEAGUE_3PA_BASELINE"]), axis=1)
    enriched["PROJ_PRA"] = enriched.apply(lambda row: calculate_projection(row["SEASON_PRA"], row["L10_PRA"], row["L5_PRA"], row["OPP_PRA_ALLOWED"], row["LEAGUE_PRA_BASELINE"]), axis=1)

    return enriched


def merge_betmgm_odds(team_df: pd.DataFrame, odds_df: pd.DataFrame) -> pd.DataFrame:
    if team_df.empty:
        return team_df

    enriched = team_df.copy()

    # Garante as colunas esperadas quando não houver odds
    all_odds_cols = [col for cols in ODDS_METRIC_COLUMNS.values() for col in cols]

    if odds_df.empty:
        for col in all_odds_cols:
            if col not in enriched.columns:
                enriched[col] = None
        return enriched

    # Chave canônica de merge
    enriched["_PLAYER_KEY_MERGE"] = (
        enriched["PLAYER_KEY"]
        .fillna("")
        .astype(str)
        .apply(normalize_person_name)
    )

    odds_work = odds_df.copy()
    odds_work["_PLAYER_KEY_MERGE"] = (
        odds_work["PLAYER_KEY_ODDS"]
        .fillna("")
        .astype(str)
        .apply(normalize_person_name)
    )

    # Mantém só o necessário do lado das odds
    odds_keep_cols = ["_PLAYER_KEY_MERGE", "PLAYER_KEY_ODDS", "PLAYER_NAME_ODDS"] + [
        col for col in all_odds_cols if col in odds_work.columns
    ]
    odds_work = odds_work[[c for c in odds_keep_cols if c in odds_work.columns]].copy()

    # Remove colunas BetMGM antigas do team_df para evitar sufixos _x/_y
    existing_betmgm_cols = [col for col in all_odds_cols if col in enriched.columns]
    if existing_betmgm_cols:
        enriched = enriched.drop(columns=existing_betmgm_cols)

    merged = enriched.merge(odds_work, on="_PLAYER_KEY_MERGE", how="left")

    drop_cols = [c for c in ["_PLAYER_KEY_MERGE", "PLAYER_KEY_ODDS", "PLAYER_NAME_ODDS"] if c in merged.columns]
    if drop_cols:
        merged = merged.drop(columns=drop_cols)

    # Garante presença das colunas mesmo se nenhuma linha casar
    for col in all_odds_cols:
        if col not in merged.columns:
            merged[col] = None

    return merged


def apply_filters(team_df: pd.DataFrame, min_games: int, min_minutes: int, role_filter: str) -> pd.DataFrame:
    filtered = team_df[(team_df["SEASON_GP"] >= min_games) & (team_df["SEASON_MIN"] >= min_minutes)].copy()
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
        filtered = filtered.sort_values(by=["PLAYER", "SEASON_MIN"], ascending=[ascending, False])
    else:
        filtered = filtered.sort_values(by=[sort_column, "SEASON_MIN", "PLAYER"], ascending=[ascending, False, True])
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
    display_df["3PM Temp"] = display_df["SEASON_3PM"]
    display_df["3PM L5"] = display_df["L5_3PM"]
    display_df["3PM L10"] = display_df["L10_3PM"]
    display_df["FGA Temp"] = display_df["SEASON_FGA"]
    display_df["FGA L5"] = display_df["L5_FGA"]
    display_df["FGA L10"] = display_df["L10_FGA"]
    display_df["3PA Temp"] = display_df["SEASON_3PA"]
    display_df["3PA L5"] = display_df["L5_3PA"]
    display_df["3PA L10"] = display_df["L10_3PA"]

    display_df["Proj PRA"] = display_df["PROJ_PRA"]
    display_df["Proj PTS"] = display_df["PROJ_PTS"]
    display_df["Proj REB"] = display_df["PROJ_REB"]
    display_df["Proj AST"] = display_df["PROJ_AST"]
    display_df["Proj 3PM"] = display_df["PROJ_3PM"]
    display_df["Proj FGA"] = display_df["PROJ_FGA"]
    display_df["Proj 3PA"] = display_df["PROJ_3PA"]
    display_df["Matchup"] = display_df["MATCHUP_LABEL"]
    display_df["Hit PRA"] = display_df["HIT_RATE_L10_TEXT"]
    display_df["Hit PTS"] = display_df["PTS_HIT_RATE_L10_TEXT"]
    display_df["Hit REB"] = display_df["REB_HIT_RATE_L10_TEXT"]
    display_df["Hit AST"] = display_df["AST_HIT_RATE_L10_TEXT"]
    display_df["Hit 3PM"] = display_df["THREE_PM_HIT_RATE_L10_TEXT"]
    display_df["Hit FGA"] = display_df["FGA_HIT_RATE_L10_TEXT"]
    display_df["Hit 3PA"] = display_df["THREE_PA_HIT_RATE_L10_TEXT"]
    display_df["Sinal"] = display_df["FORM_SIGNAL"]
    display_df["Oscilação"] = display_df["OSC_CLASS"]
    display_df["PRA adv pos"] = display_df["OPP_PRA_ALLOWED"]
    display_df["Liga pos"] = display_df["LEAGUE_PRA_BASELINE"]

    summary_df = display_df[
        [
            "Jogador", "Papel", "GP", "MIN", "PRA Temp", "PRA L10", "Proj PRA", "Δ PRA L10",
            "Matchup", "Hit PRA", "Oscilação", "Sinal", "Trend",
        ]
    ].copy()

    detail_df = display_df[
        [
            "Jogador", "Pos", "Papel", "GP", "MIN",
            "PTS Temp", "PTS L5", "PTS L10", "Proj PTS", "Hit PTS",
            "REB Temp", "REB L5", "REB L10", "Proj REB", "Hit REB",
            "AST Temp", "AST L5", "AST L10", "Proj AST", "Hit AST",
            "3PM Temp", "3PM L5", "3PM L10", "Proj 3PM", "Hit 3PM",
            "FGA Temp", "FGA L5", "FGA L10", "Proj FGA", "Hit FGA",
            "3PA Temp", "3PA L5", "3PA L10", "Proj 3PA", "Hit 3PA",
            "PRA Temp", "PRA L5", "PRA L10", "Proj PRA", "Hit PRA",
            "Δ PRA L5", "Δ PRA L10", "PRA adv pos", "Liga pos",
            "Matchup", "Oscilação", "Sinal", "Trend",
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


def style_matchup(val) -> str:
    if "Favorável" in str(val):
        return "background-color: rgba(34,197,94,0.10); color: #dcfce7; font-weight: 700;"
    if "Difícil" in str(val):
        return "background-color: rgba(239,68,68,0.10); color: #fee2e2; font-weight: 700;"
    return "background-color: rgba(148,163,184,0.10); color: #e2e8f0; font-weight: 600;"


def style_signal(val) -> str:
    if "↗" in str(val):
        return "color: #86efac; font-weight: 700;"
    if "↘" in str(val):
        return "color: #fca5a5; font-weight: 700;"
    return "color: #e2e8f0; font-weight: 600;"


def style_oscillation(val) -> str:
    if "Baixa" in str(val):
        return "color: #86efac; font-weight: 700;"
    if "Alta" in str(val):
        return "color: #fca5a5; font-weight: 700;"
    return "color: #fcd34d; font-weight: 700;"


def style_hit_rate(val) -> str:
    text = str(val)
    if "/" not in text:
        return ""
    try:
        hit, sample = text.split("/")
        ratio = float(hit) / max(float(sample), 1.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return ""

    if ratio >= 0.7:
        return "background-color: rgba(34,197,94,0.10); color: #dcfce7; font-weight: 700;"
    if ratio <= 0.4:
        return "background-color: rgba(239,68,68,0.10); color: #fee2e2; font-weight: 700;"
    return "background-color: rgba(148,163,184,0.10); color: #e2e8f0; font-weight: 600;"
def style_table(df: pd.DataFrame, quick_view: bool) -> Styler:
    text_cols = {
        "Jogador",
        "Pos",
        "Papel",
        "Trend",
        "Matchup",
        "Hit PRA",
        "Hit PTS",
        "Hit REB",
        "Hit AST",
        "Hit 3PM",
        "Hit FGA",
        "Hit 3PA",
        "Oscilação",
        "Sinal",
        "Status",
        "Status oficial",
        "Motivo",
        "Última atualização",
    }

    format_map = {}
    for col in df.columns:
        if col in text_cols:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            format_map[col] = "{:.0f}" if col == "GP" else "{:.1f}"

    styler = df.style.format(format_map, na_rep="-")

    pra_cols = [c for c in ["PRA Temp", "PRA L5", "PRA L10", "PRA adv pos", "Liga pos"] if c in df.columns]
    delta_cols = [c for c in ["Δ PRA L5", "Δ PRA L10"] if c in df.columns]
    hit_cols = [c for c in ["Hit PRA", "Hit PTS", "Hit REB", "Hit AST", "Hit 3PM", "Hit FGA", "Hit 3PA"] if c in df.columns]
    center_cols = [
        c for c in [
            "Papel",
            "GP",
            "MIN",
            "Trend",
            "Matchup",
            "Oscilação",
            "Sinal",
            "Hit PRA",
            "Hit PTS",
            "Hit REB",
            "Hit AST",
            "Hit 3PM",
            "Hit FGA",
            "Hit 3PA",
            "Status",
        ] if c in df.columns
    ]

    if pra_cols:
        styler = styler.map(style_pra, subset=pra_cols)
    if delta_cols:
        styler = styler.map(style_delta, subset=delta_cols)
    if hit_cols:
        styler = styler.map(style_hit_rate, subset=hit_cols)
    if "Trend" in df.columns:
        styler = styler.map(style_trend, subset=["Trend"])
    if "Papel" in df.columns:
        styler = styler.map(style_role, subset=["Papel"])
    if "Matchup" in df.columns:
        styler = styler.map(style_matchup, subset=["Matchup"])
    if "Sinal" in df.columns:
        styler = styler.map(style_signal, subset=["Sinal"])
    if "Oscilação" in df.columns:
        styler = styler.map(style_oscillation, subset=["Oscilação"])
    if "Jogador" in df.columns:
        styler = styler.set_properties(subset=["Jogador"], **{"font-weight": "700"})
    if center_cols:
        styler = styler.set_properties(subset=center_cols, **{"text-align": "center"})
    if quick_view:
        quick_cols = [c for c in ["PRA Temp", "PRA L10", "Δ PRA L10"] if c in df.columns]
        if quick_cols:
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
    return pd.concat([away_filtered, home_filtered], ignore_index=True)


def render_single_card(
    title: str,
    value: str,
    meta: str,
    left_label: str,
    left_value: str,
    right_label: str,
    right_value: str,
    right_highlight: bool = True,
) -> str:
    right_class = "detail-mini detail-mini-highlight" if right_highlight else "detail-mini"
    return f"""
    <div class="summary-card">
        <div class="summary-label">{title}</div>
        <div class="summary-value">{value}</div>
        <div class="summary-meta">{meta}</div>
        <div class="detail-mini-grid" style="margin-top:0.75rem; grid-template-columns: repeat(2, minmax(0, 1fr));">
            <div class="detail-mini">
                <div class="detail-mini-label">{left_label}</div>
                <div class="detail-mini-value">{left_value}</div>
            </div>
            <div class="{right_class}">
                <div class="detail-mini-label">{right_label}</div>
                <div class="detail-mini-value">{right_value}</div>
            </div>
        </div>
    </div>
    """


def render_summary_cards(
    away_df: pd.DataFrame,
    home_df: pd.DataFrame,
    min_games: int,
    min_minutes: int,
    role_filter: str,
) -> None:
    combined = build_summary_cards_data(away_df, home_df, min_games, min_minutes, role_filter)
    st.subheader("Destaques do confronto")

    if combined.empty:
        st.info("Nenhum jogador passou pelos filtros atuais para montar os cards.")
        return

    best_pra = combined.sort_values("L10_PRA", ascending=False).iloc[0]
    best_delta = combined.sort_values("DELTA_PRA_L10", ascending=False).iloc[0]
    best_matchup = combined.sort_values(["MATCHUP_DIFF", "L10_PRA"], ascending=[False, False]).iloc[0]
    best_consistency = combined.sort_values(["HIT_RATE_L10", "OSC_L10", "L10_PRA"], ascending=[False, True, False]).iloc[0]
    best_signal = combined.sort_values(["L10_PRA", "HIT_RATE_L10"], ascending=[False, False]).iloc[0]

    cards = [
        (
            "PRA L10 líder",
            format_number(best_pra["L10_PRA"]),
            f'{best_pra["PLAYER"]} • {best_pra["TEAM_NAME"]}',
            "Temp",
            format_number(best_pra["SEASON_PRA"]),
            "Hit L10",
            best_pra["HIT_RATE_L10_TEXT"],
        ),
        (
            "Maior alta L10",
            format_signed_number(best_delta["DELTA_PRA_L10"]),
            f'{best_delta["PLAYER"]} • {best_delta["TEAM_NAME"]}',
            "PRA L10",
            format_number(best_delta["L10_PRA"]),
            "Sinal",
            best_delta["FORM_SIGNAL"],
        ),
        (
            "Melhor matchup",
            best_matchup["MATCHUP_LABEL"],
            f'{best_matchup["PLAYER"]} • {best_matchup["TEAM_NAME"]}',
            "PRA ced.",
            format_number(best_matchup["OPP_PRA_ALLOWED"]),
            "Liga",
            format_number(best_matchup["LEAGUE_PRA_BASELINE"]),
        ),
        (
            "Mais consistente",
            best_consistency["HIT_RATE_L10_TEXT"],
            f'{best_consistency["PLAYER"]} • {best_consistency["TEAM_NAME"]}',
            "Osc",
            best_consistency["OSC_CLASS"],
            "PRA L10",
            format_number(best_consistency["L10_PRA"]),
        ),
        (
            "Melhor forma",
            best_signal["FORM_SIGNAL"],
            f'{best_signal["PLAYER"]} • {best_signal["TEAM_NAME"]}',
            "Δ L10",
            format_signed_number(best_signal["DELTA_PRA_L10"]),
            "Hit L10",
            best_signal["HIT_RATE_L10_TEXT"],
        ),
    ]

    cols = st.columns(5)
    for col, card in zip(cols, cards):
        with col:
            st.markdown(
                render_single_card(
                    title=card[0],
                    value=card[1],
                    meta=card[2],
                    left_label=card[3],
                    left_value=card[4],
                    right_label=card[5],
                    right_value=card[6],
                ),
                unsafe_allow_html=True,
            )


def render_compact_ranking_html(rank_df: pd.DataFrame, mode: str) -> str:
    rows_html = []
    for idx, (_, row) in enumerate(rank_df.iterrows(), start=1):
        if mode == "projection":
            stat1_label, stat1_value = "Proj", format_number(row["RANK_PROJ"])
            stat2_label, stat2_value = "Hit", row["RANK_HIT_HTML"]
            stat3_label, stat3_value = "Match", row["MATCHUP_LABEL"]
            stat3_class = "ranking-good" if row["MATCHUP_LABEL"] == "Favorável" else ("ranking-bad" if row["MATCHUP_LABEL"] == "Difícil" else "")
        elif mode == "edge":
            stat1_label, stat1_value = "Edge", format_signed_number(row["RANK_EDGE"])
            stat2_label, stat2_value = "Linha", row["RANK_LINE_HTML"]
            stat3_label, stat3_value = "Proj", format_number(row["RANK_PROJ"])
            stat3_class = ""
        else:
            stat1_label, stat1_value = "Hit", row["RANK_HIT_HTML"]
            stat2_label, stat2_value = "Osc", row["OSC_CLASS"]
            stat3_label, stat3_value = "Proj", format_number(row["RANK_PROJ"])
            stat3_class = ""

        stat1_class = "ranking-good" if mode == "edge" and row["RANK_EDGE"] > 0.75 else ("ranking-bad" if mode == "edge" and row["RANK_EDGE"] < -0.75 else "")
        stat2_class = "ranking-good" if mode == "consistency" and row["OSC_CLASS"] == "Baixa" else ("ranking-bad" if mode == "consistency" and row["OSC_CLASS"] == "Alta" else "")

        row_html = (
            f'<div class="ranking-row">'
            f'<div class="ranking-rank">{idx}</div>'
            f'<div><div class="ranking-player">{row["PLAYER"]}</div><div class="ranking-sub">{row["TEAM_NAME"]} • {row["ROLE"]}</div></div>'
            f'<div class="ranking-stat {stat1_class}"><div class="ranking-stat-label">{stat1_label}</div><div class="ranking-stat-value">{stat1_value}</div></div>'
            f'<div class="ranking-stat {stat2_class}"><div class="ranking-stat-label">{stat2_label}</div><div class="ranking-stat-value">{stat2_value}</div></div>'
            f'<div class="ranking-stat {stat3_class}"><div class="ranking-stat-label">{stat3_label}</div><div class="ranking-stat-value">{stat3_value}</div></div>'
            f'</div>'
        )
        rows_html.append(row_html)

    return f'<div class="ranking-shell">{"".join(rows_html)}</div>'


def render_game_rankings(
    away_df: pd.DataFrame,
    home_df: pd.DataFrame,
    min_games: int,
    min_minutes: int,
    role_filter: str,
    line_metric: str,
    line_value: float,
    use_market_line: bool,
) -> None:
    combined = build_summary_cards_data(away_df, home_df, min_games, min_minutes, role_filter)
    if combined.empty:
        return

    projection_col = get_metric_projection_column(line_metric)

    def parse_ratio_text(text: str) -> float:
        try:
            hit, sample = str(text).split("/")
            sample_n = max(float(sample), 1.0)
            return float(hit) / sample_n
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0

    rank_df = combined.copy()

    rank_df["RANK_PROJ"] = pd.to_numeric(rank_df[projection_col], errors="coerce").fillna(0.0)

    rank_df["LINE_CONTEXT"] = rank_df.apply(
        lambda row: get_line_context(
            row,
            line_metric,
            line_value,
            use_market_line=use_market_line,
        ),
        axis=1,
    )

    rank_df["RANK_LINE"] = rank_df["LINE_CONTEXT"].apply(lambda ctx: float(ctx.get("line_value", 0.0)))
    rank_df["RANK_EDGE"] = rank_df["LINE_CONTEXT"].apply(lambda ctx: float(ctx.get("edge", 0.0)))
    rank_df["RANK_HIT_TEXT"] = rank_df["LINE_CONTEXT"].apply(lambda ctx: ctx.get("hit_l10", "-"))
    rank_df["RANK_HIT_HTML"] = rank_df["LINE_CONTEXT"].apply(lambda ctx: ctx.get("hit_l10_html", "-"))
    rank_df["RANK_LINE_HTML"] = rank_df["LINE_CONTEXT"].apply(lambda ctx: f'<span title="{ctx.get("tooltip", "")}" style="cursor:help;">{format_number(ctx.get("line_value", 0.0))} {ctx.get("icon", "")}</span>')
    rank_df["RANK_HIT_RATE"] = rank_df["RANK_HIT_TEXT"].apply(parse_ratio_text)

    if "LINE_CONTEXT" in rank_df.columns:
        rank_df = rank_df.drop(columns=["LINE_CONTEXT"])

    proj_df = rank_df.sort_values(
        ["RANK_PROJ", "RANK_HIT_RATE"],
        ascending=[False, False],
    ).head(5)

    edge_df = rank_df.sort_values(
        ["RANK_EDGE", "RANK_HIT_RATE"],
        ascending=[False, False],
    ).head(5)

    consistency_df = rank_df.sort_values(
        ["RANK_HIT_RATE", "OSC_L10", "RANK_PROJ"],
        ascending=[False, True, False],
    ).head(5)

    st.subheader(f"Ranking do confronto — {line_metric}")
    st.caption("Bloco compacto para bater o olho rápido, usando BetMGM quando houver linha disponível.")

    tab_proj, tab_edge, tab_cons = st.tabs(["Projeção", "Edge da linha", "Consistência"])

    with tab_proj:
        st.markdown(render_compact_ranking_html(proj_df, mode="projection"), unsafe_allow_html=True)

    with tab_edge:
        st.markdown(render_compact_ranking_html(edge_df, mode="edge"), unsafe_allow_html=True)

    with tab_cons:
        st.markdown(render_compact_ranking_html(consistency_df, mode="consistency"), unsafe_allow_html=True)

def render_player_chart(player_name: str, player_id: int, season: str, chart_mode: str, visual_metric: str) -> None:
    log = get_player_log(player_id, season)
    if log.empty:
        st.info("Sem histórico suficiente para esse jogador.")
        return

    # Garante que puxa o FGA também do banco de dados
    needed_cols = ["GAME_DATE", "PTS", "REB", "AST", "FGA", "FG3M", "FG3A"]
    if "MATCHUP" in log.columns:
        needed_cols.append("MATCHUP")

    recent = log[[c for c in needed_cols if c in log.columns]].copy()
    recent = recent.dropna(subset=["GAME_DATE", "PTS", "REB", "AST"]).sort_values("GAME_DATE")
    if recent.empty:
        st.info("Sem histórico suficiente para esse jogador.")
        return

    # Padroniza as siglas para bater com o resto do App
    recent["PRA"] = recent["PTS"] + recent["REB"] + recent["AST"]
    recent["3PM"] = recent.get("FG3M", 0)
    recent["3PA"] = recent.get("FG3A", 0)
    if "FGA" not in recent.columns:
        recent["FGA"] = 0

    if "MATCHUP" in recent.columns:
        matchup_parts = recent["MATCHUP"].apply(get_matchup_parts)
        recent["VENUE"] = matchup_parts.apply(lambda x: x[0])
        recent["OPP_ABBR"] = matchup_parts.apply(lambda x: x[1])
    else:
        recent["VENUE"] = ""
        recent["OPP_ABBR"] = ""

    recent["SHORT_LABEL"] = recent.apply(
        lambda row: f'{row["GAME_DATE"].strftime("%m/%d")}<br>{row["VENUE"]} {row["OPP_ABBR"]}'.strip() if row["OPP_ABBR"] else row["GAME_DATE"].strftime("%m/%d"),
        axis=1,
    )

    top_left, top_right = st.columns([1, 6])
    with top_left:
        st.image(get_player_headshot_url(int(player_id)), width=82)
    with top_right:
        st.markdown(f"### Últimos jogos — {player_name}")
        st.caption("Visual compacto: barras, últimos 5 jogos." if chart_mode == "Compacto" else "Visual completo: linha contínua, últimos 10 jogos.")

    if chart_mode == "Compacto":
        recent_view = recent.tail(5).copy()

        fig = go.Figure(
            go.Bar(
                x=recent_view["SHORT_LABEL"],
                y=recent_view[visual_metric],
                text=recent_view[visual_metric].round(1),
                textposition="outside",
                marker=dict(color="#4ade80"),
                hovertemplate=f"{visual_metric}: %{{y:.1f}}<extra></extra>",
            )
        )
        fig.update_layout(
            template="plotly_dark",
            height=360,
            margin=dict(l=20, r=20, t=10, b=20),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(15,23,42,0.35)",
            showlegend=False,
            bargap=0.28,
            dragmode=False,
        )
        fig.update_xaxes(title="", type="category", tickangle=0, showgrid=False, tickfont=dict(size=11))
        fig.update_yaxes(title="", showgrid=True, gridcolor="rgba(148,163,184,0.15)", zeroline=False)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"{visual_metric} • Média na Temp: {recent[visual_metric].mean():.1f} | Média no L5: {recent_view[visual_metric].mean():.1f}")
    else:
        recent_view = recent.tail(10).copy()
        fig = go.Figure()
        
        # Agora o Completo foca em 1 linha só, para não virar bagunça!
        fig.add_trace(
            go.Scatter(
                x=recent_view["SHORT_LABEL"],
                y=recent_view[visual_metric],
                mode="lines+markers+text",
                name=visual_metric,
                text=recent_view[visual_metric].round(1),
                textposition="top center",
                line=dict(width=4, color="#38bdf8"),
                marker=dict(size=8),
                opacity=1.0,
                hovertemplate=f"{visual_metric}: %{{y:.1f}}<extra></extra>",
            )
        )

        fig.update_layout(
            template="plotly_dark",
            height=400,
            margin=dict(l=20, r=20, t=10, b=20),
            showlegend=False,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(15,23,42,0.35)",
            hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155", font=dict(color="#f8fafc", size=13)),
            dragmode=False,
        )
        fig.update_xaxes(title="", type="category", tickangle=0, showgrid=False, tickfont=dict(size=11))
        fig.update_yaxes(title="", showgrid=True, gridcolor="rgba(148,163,184,0.15)", zeroline=False)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"{visual_metric} • Média na Temp: {recent[visual_metric].mean():.1f} | Média no L10: {recent_view[visual_metric].mean():.1f}")
def render_badges(role: str, momentum: str, oscillation: str, matchup: str) -> None:
    role_class = "badge-starter" if role == "Titular provável" else "badge-bench"

    if str(momentum).startswith(("↗", "↑")):
        momentum_class = "badge-good"
    elif str(momentum).startswith(("↘", "↓")):
        momentum_class = "badge-bad"
    else:
        momentum_class = "badge-neutral"

    if oscillation == "Baixa":
        oscillation_class = "badge-good"
    elif oscillation == "Alta":
        oscillation_class = "badge-bad"
    else:
        oscillation_class = "badge-neutral"

    if matchup == "Favorável":
        matchup_class = "badge-good"
    elif matchup == "Difícil":
        matchup_class = "badge-bad"
    else:
        matchup_class = "badge-neutral"

    st.markdown(
        f"""
        <div class="badge-row">
            <span class="badge {role_class}">{role}</span>
            <span class="badge {momentum_class}">{momentum}</span>
            <span class="badge {oscillation_class}">Osc {oscillation}</span>
            <span class="badge {matchup_class}">Matchup {matchup}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def build_delta_pill_html(label: str, value: float) -> str:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        numeric_value = 0.0

    if numeric_value > 0.3:
        css_class = "delta-up"
    elif numeric_value < -0.3:
        css_class = "delta-down"
    else:
        css_class = "delta-flat"

    return f'<span class="delta-pill {css_class}">{label} {format_signed_number(numeric_value)}</span>'


def render_detail_metric_box_html(title: str, temp_val: float, l5_val: float, l10_val: float) -> str:
    delta_l5 = l5_val - temp_val
    delta_l10 = l10_val - temp_val
    return f"""
    <div class="detail-box">
        <div class="detail-box-top">
            <div class="detail-box-title">{title}</div>
            <div class="delta-pill-row">
                {build_delta_pill_html('Δ L5', delta_l5)}
                {build_delta_pill_html('Δ L10', delta_l10)}
            </div>
        </div>
        <div class="detail-mini-grid">
            <div class="detail-mini">
                <div class="detail-mini-label">Temp</div>
                <div class="detail-mini-value">{format_number(temp_val)}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">L5</div>
                <div class="detail-mini-value">{format_number(l5_val)}</div>
            </div>
            <div class="detail-mini detail-mini-highlight">
                <div class="detail-mini-label">L10</div>
                <div class="detail-mini-value">{format_number(l10_val)}</div>
            </div>
        </div>
    </div>
    """


def render_player_headline_html(row: pd.Series) -> str:
    hit_rate_pct = int(round(float(row.get("HIT_RATE_L10", 0.0)) * 100))
    hit_rate_text = row.get("HIT_RATE_L10_TEXT", "-")
    form_signal = row.get("FORM_SIGNAL", "→ Estável")
    osc_class = row.get("OSC_CLASS", "-")
    matchup_class = get_matchup_chip_class(row.get("MATCHUP_LABEL", "Neutro"))

    return f"""
    <div class="player-headline-card">
        <div class="player-headline-label">Leitura em 3 segundos</div>
        <div class="player-headline-value">{format_number(row['L10_PRA'])}</div>
        <div class="player-headline-sub">
            PRA L10 • Proj {format_number(row['PROJ_PRA'])} • Δ vs temp {format_signed_number(row['DELTA_PRA_L10'])}
            • Hit L10 {hit_rate_text} ({hit_rate_pct}%) • Oscilação {osc_class} • {form_signal}
        </div>
        <div class="hero-note">
            {row['OPP_TEAM_NAME']} cede {format_number(row['OPP_PRA_ALLOWED'])} PRA para {row['POSITION_GROUP']} • liga {format_number(row['LEAGUE_PRA_BASELINE'])}
            <span class="matchup-chip {matchup_class}" style="margin-left:0.4rem;">{row['MATCHUP_LABEL']} vs {row['POSITION_GROUP']}</span>
        </div>
    </div>
    """


def render_player_support_tiles(row: pd.Series, line_metric: str, line_value: float, use_market_line: bool) -> None:
    matchup_class = "quick-stat"
    if row["MATCHUP_LABEL"] == "Favorável":
        matchup_class = "quick-stat quick-stat-up"
    elif row["MATCHUP_LABEL"] == "Difícil":
        matchup_class = "quick-stat quick-stat-down"

    line_context = get_line_context(row, line_metric, line_value, use_market_line=use_market_line)
    if line_context["edge"] > 0.75:
        line_class = "quick-stat quick-stat-up"
    elif line_context["edge"] < -0.75:
        line_class = "quick-stat quick-stat-down"
    else:
        line_class = "quick-stat quick-stat-primary"

    pts_hit = row.get("PTS_HIT_RATE_L10_TEXT", "-")
    reb_hit = row.get("REB_HIT_RATE_L10_TEXT", "-")
    ast_hit = row.get("AST_HIT_RATE_L10_TEXT", "-")

    odds_meta = ""
    if line_context["has_market_line"] and line_context["over_dec"] and line_context["under_dec"]:
        odds_meta = f" • O {format_number(line_context['over_dec'], 2)} • U {format_number(line_context['under_dec'], 2)}"

    st.markdown(
        f"""
        <div class="player-quick-grid">
            <div class="quick-stat">
                <div class="quick-stat-label">PTS L10</div>
                <div class="quick-stat-value">{format_number(row['L10_PTS'])}</div>
                <div class="quick-stat-meta">Proj {format_number(row['PROJ_PTS'])} • Hit {pts_hit}</div>
            </div>
            <div class="quick-stat">
                <div class="quick-stat-label">REB L10</div>
                <div class="quick-stat-value">{format_number(row['L10_REB'])}</div>
                <div class="quick-stat-meta">Proj {format_number(row['PROJ_REB'])} • Hit {reb_hit}</div>
            </div>
            <div class="quick-stat">
                <div class="quick-stat-label">AST L10</div>
                <div class="quick-stat-value">{format_number(row['L10_AST'])}</div>
                <div class="quick-stat-meta">Proj {format_number(row['PROJ_AST'])} • Hit {ast_hit}</div>
            </div>
            <div class="{matchup_class}">
                <div class="quick-stat-label">Matchup</div>
                <div class="quick-stat-value">{row['MATCHUP_LABEL']}</div>
                <div class="quick-stat-meta">PRA cedido {format_number(row['OPP_PRA_ALLOWED'])} • diff {format_signed_number(row['MATCHUP_DIFF'])}</div>
            </div>
            <div class="{line_class}">
                <div class="quick-stat-label">{line_context['line_source']} {line_metric}</div>
                <div class="quick-stat-value">{format_signed_number(line_context['edge'])}</div>
                <div class="quick-stat-meta">Proj {format_number(line_context['projection'])} vs {format_number(line_context['line_value'])} • L10 {line_context['hit_l10_html']}{odds_meta}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_projection_detail_box_html(row: pd.Series) -> str:
    return f"""
    <div class="detail-box">
        <div class="detail-box-top">
            <div class="detail-box-title">Projeções do modelo</div>
            <div class="delta-pill-row">
                <span class="delta-pill delta-flat">peso L10 maior</span>
            </div>
        </div>
        <div class="detail-mini-grid" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
            <div class="detail-mini">
                <div class="detail-mini-label">Proj PTS</div>
                <div class="detail-mini-value">{format_number(row['PROJ_PTS'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Proj REB</div>
                <div class="detail-mini-value">{format_number(row['PROJ_REB'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Proj AST</div>
                <div class="detail-mini-value">{format_number(row['PROJ_AST'])}</div>
            </div>
            <div class="detail-mini detail-mini-highlight">
                <div class="detail-mini-label">Proj PRA</div>
                <div class="detail-mini-value">{format_number(row['PROJ_PRA'])}</div>
            </div>
        </div>
        <div class="detail-mini-grid" style="grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top:0.55rem;">
            <div class="detail-mini">
                <div class="detail-mini-label">Proj 3PM</div>
                <div class="detail-mini-value">{format_number(row['PROJ_3PM'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Proj FGA</div>
                <div class="detail-mini-value">{format_number(row['PROJ_FGA'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Proj 3PA</div>
                <div class="detail-mini-value">{format_number(row['PROJ_3PA'])}</div>
            </div>
        </div>
    </div>
    """

def render_split_detail_box_html(row: pd.Series, line_metric: str) -> str:
    is_home = row.get("IS_HOME", False)
    
    home_val = row.get(f"HOME_{line_metric}", 0.0)
    away_val = row.get(f"AWAY_{line_metric}", 0.0)
    season_val = row.get(f"SEASON_{line_metric}", 0.0)
    
    # Calcula se o jogador cresce ou some baseado no local do jogo de hoje
    active_val = home_val if is_home else away_val
    diff = active_val - season_val
    
    if diff >= 1.0:
        diff_pill = f'<span class="delta-pill delta-up">Rende +{diff:.1f} hoje</span>'
    elif diff <= -1.0:
        diff_pill = f'<span class="delta-pill delta-down">Cai {diff:.1f} hoje</span>'
    else:
        diff_pill = '<span class="delta-pill delta-flat">Sem impacto relevante</span>'
        
    home_class = "detail-mini detail-mini-highlight" if is_home else "detail-mini"
    away_class = "detail-mini detail-mini-highlight" if not is_home else "detail-mini"
    
    return f"""
    <div class="detail-box">
        <div class="detail-box-top">
            <div class="detail-box-title">Efeito de Mando de Quadra — {line_metric}</div>
            <div class="delta-pill-row">
                {diff_pill}
            </div>
        </div>
        <div class="detail-mini-grid" style="grid-template-columns: repeat(3, minmax(0, 1fr));">
            <div class="detail-mini">
                <div class="detail-mini-label">Média Geral</div>
                <div class="detail-mini-value">{format_number(season_val)}</div>
            </div>
            <div class="{home_class}">
                <div class="detail-mini-label">Jogando em Casa</div>
                <div class="detail-mini-value">{format_number(home_val)}</div>
            </div>
            <div class="{away_class}">
                <div class="detail-mini-label">Jogando Fora</div>
                <div class="detail-mini-value">{format_number(away_val)}</div>
            </div>
        </div>
        <div class="hero-note" style="margin-top: 0.55rem;">
            Hoje o jogador atua <strong>{'EM CASA' if is_home else 'FORA DE CASA'}</strong>. Card destacado indica o cenário ativo da partida.
        </div>
    </div>
    """

def render_manual_line_detail_box_html(row: pd.Series, line_metric: str, line_value: float, use_market_line: bool) -> str:
    line_context = get_line_context(row, line_metric, line_value, use_market_line=use_market_line)
    line_chip_class = "delta-flat"
    if line_context["edge"] > 0.75:
        line_chip_class = "delta-up"
    elif line_context["edge"] < -0.75:
        line_chip_class = "delta-down"

    odds_note = "Usando linha manual."
    if line_context["has_market_line"]:
        over_text = format_number(line_context["over_dec"], 2) if line_context["over_dec"] else "-"
        under_text = format_number(line_context["under_dec"], 2) if line_context["under_dec"] else "-"
        odds_note = f"BetMGM • Over {over_text} • Under {under_text}"

    return f"""
    <div class="detail-box">
        <div class="detail-box-top">
            <div class="detail-box-title">Linha {line_context['line_source']} — {line_metric}</div>
            <div class="delta-pill-row">
                <span class="delta-pill {line_chip_class}">{line_context['label']}</span>
                <span class="delta-pill delta-flat">Linha {format_number(line_context['line_value'])}</span>
            </div>
        </div>
        <div class="detail-mini-grid" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
            <div class="detail-mini">
                <div class="detail-mini-label">Projeção</div>
                <div class="detail-mini-value">{format_number(line_context['projection'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Edge</div>
                <div class="detail-mini-value">{format_signed_number(line_context['edge'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">Hit L5</div>
                <div class="detail-mini-value">{line_context['hit_l5']}</div>
            </div>
            <div class="detail-mini detail-mini-highlight">
                <div class="detail-mini-label" title="{line_context['tooltip']}" style="cursor:help;">Hit L10 {line_context['icon']}</div>
                <div class="detail-mini-value">{line_context['hit_l10']}</div>
            </div>
        </div>
        <div style="margin-top: 0.8rem; padding-top: 0.6rem; border-top: 1px solid rgba(148,163,184,.12);">
            <div class="detail-mini-label" style="margin-bottom: 0.2rem;">Sequência L10 (→ mais recente)</div>
            <div style="font-size: 1.1rem; letter-spacing: 0.1rem;">{line_context['hit_sequence']}</div>
        </div>
        <div class="hero-note" style="margin-top: 0.6rem;">{odds_note}</div>
    </div>
    """

def render_matchup_detail_box_html(row: pd.Series) -> str:
    matchup_class = get_matchup_chip_class(row["MATCHUP_LABEL"])
    return f"""
    <div class="detail-box">
        <div class="detail-box-top">
            <div class="detail-box-title">Contexto do adversário</div>
            <div class="delta-pill-row">
                <span class="matchup-chip {matchup_class}">{row['MATCHUP_LABEL']}</span>
            </div>
        </div>
        <div class="detail-mini-grid" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
            <div class="detail-mini">
                <div class="detail-mini-label">PTS ced.</div>
                <div class="detail-mini-value">{format_number(row['OPP_PTS_ALLOWED'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">REB ced.</div>
                <div class="detail-mini-value">{format_number(row['OPP_REB_ALLOWED'])}</div>
            </div>
            <div class="detail-mini">
                <div class="detail-mini-label">AST ced.</div>
                <div class="detail-mini-value">{format_number(row['OPP_AST_ALLOWED'])}</div>
            </div>
            <div class="detail-mini detail-mini-highlight">
                <div class="detail-mini-label">PRA ced.</div>
                <div class="detail-mini-value">{format_number(row['OPP_PRA_ALLOWED'])}</div>
            </div>
        </div>
        <div class="hero-note">{row['OPP_TEAM_NAME']} vs {row['POSITION_GROUP']} • liga {format_number(row['LEAGUE_PRA_BASELINE'])} • diferença {format_signed_number(row['MATCHUP_DIFF'])}</div>
    </div>
    """


def render_focus_summary_tiles(row: pd.Series, line_metric: str, line_value: float, use_market_line: bool) -> None:
    line_context = get_line_context(row, line_metric, line_value, use_market_line=use_market_line)
    line_class = "micro-stat micro-stat-emph"
    if line_context["edge"] > 0.75:
        line_class = "micro-stat micro-stat-good"
    elif line_context["edge"] < -0.75:
        line_class = "micro-stat micro-stat-bad"

    matchup_class = "micro-stat"
    if row.get("MATCHUP_LABEL") == "Favorável":
        matchup_class = "micro-stat micro-stat-good"
    elif row.get("MATCHUP_LABEL") == "Difícil":
        matchup_class = "micro-stat micro-stat-bad"

    st.markdown(
        f"""
        <div class="micro-grid">
            <div class="micro-stat micro-stat-emph">
                <div class="micro-label">Proj PRA</div>
                <div class="micro-value">{format_number(row['PROJ_PRA'])}</div>
                <div class="micro-meta">Temp {format_number(row['SEASON_PRA'])} • L10 {format_number(row['L10_PRA'])}</div>
            </div>
            <div class="{line_class}">
                <div class="micro-label">{line_context['line_source']} {line_metric}</div>
                <div class="micro-value">{format_signed_number(line_context['edge'])}</div>
                <div class="micro-meta">Proj {format_number(line_context['projection'])} vs {format_number(line_context['line_value'])} • L10 {line_context['hit_l10']}</div>
            </div>
            <div class="{matchup_class}">
                <div class="micro-label">Matchup</div>
                <div class="micro-value">{row['MATCHUP_LABEL']}</div>
                <div class="micro-meta">{row['OPP_TEAM_NAME']} vs {row['POSITION_GROUP']}</div>
            </div>
            <div class="micro-stat">
                <div class="micro-label">Oscilação</div>
                <div class="micro-value">{row['OSC_CLASS']}</div>
                <div class="micro-meta">{row['FORM_SIGNAL']}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_player_focus_panel(
    row: pd.Series,
    line_metric: str,
    line_value: float,
    use_market_line: bool,
    season: str,
    chart_mode: str,
) -> None:
   # --- BUSCADOR DE CORES DEFINITIVO (HARDCODED) ---
    # Pegamos todo o texto disponível sobre o time do jogador
    search_text = f"{row.get('TEAM_NAME', '')} {row.get('TEAM_ABBR', '')}".upper()
    
    # 1. Definimos a sigla padrão (NBA)
    tk = 'NBA'
    
    # --- RASTREADOR INTELIGENTE PARA O BANNER GRANDE ---
    search_text = f"{row.get('TEAM_NAME', '') or ''} {row.get('TEAM_ABBR', '') or ''}".upper()
    tk = 'NBA'
    
    # Loop automático pelo dicionário que resolve todos os times de uma vez
    for abbr, info in NBA_TEAM_COLORS.items():
        team_keyword = info.get('name', '').upper()
        if (team_keyword and team_keyword in search_text) or (abbr in search_text):
            tk = abbr
            break
            
    colors = NBA_TEAM_COLORS.get(tk, {'primary': '#1d222d', 'secondary': '#ffcc00'})
    
    # 3. Puxamos as cores do dicionário global
    colors = NBA_TEAM_COLORS.get(tk, {'primary': '#1d222d', 'secondary': '#ffcc00'})

    # --- DEBUG: APAGUE ESSA LINHA ABAIXO APÓS TESTAR ---
    # st.write(f"DEBUG: Time identificado como: {tk}")


    st.markdown('<div class="focus-shell">', unsafe_allow_html=True)

    top_left, top_right = st.columns([1, 5])
    with top_left:
        st.image(get_player_headshot_url(int(row["PLAYER_ID"])), width=92)

    with top_right:
        # Banner com a cor primária do time e borda na cor secundária
        st.markdown(f"""
            <div style="
                background: linear-gradient(135deg, {colors['primary']} 0%, {colors['secondary']} 150%);
                padding: 20px; 
                border-radius: 12px; 
                border-left: 15px solid {colors['secondary']};
                margin-bottom: 20px;
                box-shadow: 0 8px 20px rgba(0,0,0,0.4);
            ">
                <h1 style="color: {colors['secondary']}; margin: 0; font-size: 32px; font-weight: 800; letter-spacing: -1px;">
                    {row['PLAYER']}
                </h1>
                <div style="color: {colors['secondary']}; opacity: 0.9; font-weight: 600; font-size: 14px;">
                    {row.get('TEAM_NAME', 'NBA') or ''} | #{row.get('JERSEY_NUMBER', '')} | {row.get('POSITION', '')}
                </div>
            </div>
        """, unsafe_allow_html=True)
        position = row["POSITION"] if str(row["POSITION"]).strip() else "-"
        st.markdown(
            f'<div class="focus-sub">Pos {position} • GP {int(row["SEASON_GP"])} • MIN {format_number(row["SEASON_MIN"])} • Time {row["TEAM_NAME"]}</div>',
            unsafe_allow_html=True,
        )
        render_badges(
            row["ROLE"],
            row.get("FORM_SIGNAL", "→ Estável"),
            row.get("OSC_CLASS", "-"),
            row.get("MATCHUP_LABEL", "Neutro"),
        )
        render_focus_summary_tiles(row, line_metric, line_value, use_market_line)
    
    # --- CONTROLE MESTRE DE MÉTRICA ---
    # Ele fica fora das abas, então aparece o tempo todo no topo
    _visual_metric = st.pills(
        "Métrica em análise detalhada",
        ["PRA", "PTS", "REB", "AST", "3PM", "FGA", "3PA"],
        default=line_metric,
        key=f"global_visual_metric_{row['PLAYER_ID']}"
    )
    visual_metric = _visual_metric if _visual_metric else line_metric


    # NOVIDADE: As 3 abas organizando a tela!
    overview_tab, detail_tab, visual_tab, market_tab = st.tabs(["Resumo", "Detalhamento", "📈 Raio-X Visual", "💰 Tendências Market"])

    with overview_tab:
        render_player_support_tiles(row, line_metric, line_value, use_market_line)
        st.markdown(render_split_detail_box_html(row, visual_metric), unsafe_allow_html=True)
        st.markdown(render_projection_detail_box_html(row), unsafe_allow_html=True)
        st.markdown(
            render_manual_line_detail_box_html(row, line_metric, line_value, use_market_line),
            unsafe_allow_html=True,
        )

    with detail_tab:
        first_cols = st.columns(2)
        second_cols = st.columns(2)
        detail_items = [
            ("PRA", row["SEASON_PRA"], row["L5_PRA"], row["L10_PRA"]),
            ("PTS", row["SEASON_PTS"], row["L5_PTS"], row["L10_PTS"]),
            ("REB", row["SEASON_REB"], row["L5_REB"], row["L10_REB"]),
            ("AST", row["SEASON_AST"], row["L5_AST"], row["L10_AST"]),
        ]
        for col, item in zip([*first_cols, *second_cols], detail_items):
            with col:
                st.markdown(
                    render_detail_metric_box_html(item[0], item[1], item[2], item[3]),
                    unsafe_allow_html=True,
                )

        extra_cols = st.columns(3)
        extra_detail_items = [
            ("3PM", row["SEASON_3PM"], row["L5_3PM"], row["L10_3PM"]),
            ("FGA", row["SEASON_FGA"], row["L5_FGA"], row["L10_FGA"]),
            ("3PA", row["SEASON_3PA"], row["L5_3PA"], row["L10_3PA"]),
        ]
        for col, item in zip(extra_cols, extra_detail_items):
            with col:
                st.markdown(
                    render_detail_metric_box_html(item[0], item[1], item[2], item[3]),
                    unsafe_allow_html=True,
                )

        st.markdown(render_matchup_detail_box_html(row), unsafe_allow_html=True)

    with visual_tab:
        # CONTROLE MESTRE: Controla ambos os gráficos simultaneamente!
        
        # 1. Gráfico Histórico Clássico (Agora recebe a ordem do Controle Mestre)
        render_player_chart(row["PLAYER"], int(row["PLAYER_ID"]), season, chart_mode, visual_metric)
        
        st.divider()
        
        # 2. Gráfico Piso e Teto (Histograma)
        st.markdown(f"### Frequência na Temporada — {visual_metric}")
        st.caption("Veja os montinhos: concentrados à esquerda (Piso seguro), espalhados à direita (Teto alto).")
        
        log = get_player_log(int(row["PLAYER_ID"]), season)
        if not log.empty:
            log["PRA"] = log["PTS"] + log["REB"] + log["AST"]
            log["3PM"] = log["FG3M"]
            log["3PA"] = log.get("FG3A", log["FGA"])
            
            # Recalcula a linha e a odd (se BetMGM) para a nova métrica selecionada!
            visual_ctx = get_line_context(row, visual_metric, line_value, use_market_line)
            active_line = float(visual_ctx["line_value"])
            
            log_col_map = {"PRA": "PRA", "PTS": "PTS", "REB": "REB", "AST": "AST", "3PM": "3PM", "FGA": "FGA", "3PA": "3PA"}
            active_col = log_col_map.get(visual_metric, "PRA")
            
            if active_col in log.columns:
                hist_data = log[active_col].dropna()
                
                fig = go.Figure()
                fig.add_trace(go.Histogram(
                    x=hist_data,
                    # Força tamanho 1. Começando em -0.5 garante que o número exato (ex: 2) fique bem no centro da barra!
                    xbins=dict(start=-0.5, end=max(hist_data.max(), active_line) + 5, size=1),
                    marker_color="rgba(139,92,246, 0.65)",
                    marker_line_color="rgba(139,92,246, 1)",
                    marker_line_width=1.5,
                    opacity=0.9,
                    # Agora o tooltip vai mostrar apenas o número cravado (ex: 2)
                    hovertemplate=f"Valor exato de {visual_metric}: %{{x}}<br>Jogos atingidos: %{{y}}<extra></extra>"
                ))
                
                # Desenha a linha vertical da aposta
                line_color = "#10b981" if visual_ctx["edge"] >= 0 else "#ef4444"
                fig.add_vline(
                    x=active_line, 
                    line_dash="dash", 
                    line_color=line_color, 
                    line_width=3,
                    annotation_text=f"Linha ({visual_ctx['line_source']}): {active_line}", 
                    annotation_position="top right",
                    annotation_font_color="#cbd5e1"
                )

                # UX: Se for manual e o usuário trocar a métrica visual, damos um aviso!
                if not use_market_line and visual_metric != line_metric:
                    st.warning(f"Atenção: A linha vertical está usando o valor manual da barra lateral ({active_line}), que originalmente foi digitado para {line_metric}.")

                fig.update_layout(
                    template="plotly_dark",
                    height=380,
                    margin=dict(l=20, r=20, t=40, b=20),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(15,23,42,0.35)",
                    xaxis_title=f"Valor de {visual_metric} na partida",
                    yaxis_title="Quantidade de Jogos",
                    dragmode=False,
                    bargap=0.15
                )
                # Força o eixo X a mostrar apenas números inteiros (0, 1, 2, 3...)
                fig.update_xaxes(showgrid=True, gridcolor="rgba(148,163,184,0.1)", tick0=0, dtick=1)
                fig.update_yaxes(showgrid=True, gridcolor="rgba(148,163,184,0.15)", zeroline=False)
                
                st.plotly_chart(fig, use_container_width=True)
                
                # --- NOVO: RESUMO OVER/UNDER MATEMÁTICO ---
                over_count = int((hist_data > active_line).sum())
                under_count = int((hist_data < active_line).sum())
                push_count = int((hist_data == active_line).sum())
                
                push_html = f'<span style="margin: 0 0.8rem; color: #334155;">|</span><span style="color: #fbbf24;">PUSH (DEVOLUÇÃO) = {push_count}</span>' if push_count > 0 else ''
                
                st.markdown(
                    f'''
                    <div style="margin-top: 0.2rem; text-align: center; padding: 0.85rem; background: rgba(15,23,42,0.8); border: 1px solid rgba(148,163,184,0.15); border-radius: 12px; font-weight: 800; font-size: 0.9rem; letter-spacing: 0.05em;">
                        <span style="color: #94a3b8;">LINHA {visual_ctx['line_source'].upper()}: {active_line}</span>
                        <span style="margin: 0 0.8rem; color: #334155;">|</span>
                        <span style="color: #10b981;">OVER NA TEMPORADA = {over_count}</span>
                        <span style="margin: 0 0.8rem; color: #334155;">|</span>
                        <span style="color: #ef4444;">UNDER NA TEMPORADA = {under_count}</span>
                        {push_html}
                    </div>
                    ''',
                    unsafe_allow_html=True
                )
                st.divider()

                # --- 3. NOVO: GRÁFICO DE DISPERSÃO (MINUTOS VS PRODUÇÃO) ---
                st.markdown(f"### Eficiência: Minutos vs {visual_metric}")
                st.caption("Cada ponto é um jogo. A linha de tendência mostra se a produção sobe junto com os minutos.")
                
                scatter_df = log[["MIN", active_col, "GAME_DATE"]].copy().dropna()
                # Adiciona informação de matchup se existir
                if "MATCHUP" in log.columns: scatter_df["MATCHUP"] = log["MATCHUP"]
                else: scatter_df["MATCHUP"] = ""
                
                if not scatter_df.empty:
                    # Calcula a linha de tendência (Regressão Linear Simples)
                    x = scatter_df["MIN"]
                    y = scatter_df[active_col]
                    z = np.polyfit(x, y, 1)
                    p = np.poly1d(z)
                    trend_x = np.array([x.min(), x.max()])
                    trend_y = p(trend_x)

                    fig_scatter = go.Figure()

                    # Adiciona os pontos (Jogos)
                    fig_scatter.add_trace(go.Scatter(
                        x=x, y=y,
                        mode='markers',
                        marker=dict(
                            size=12,
                            color=np.where(y >= active_line, '#10b981', '#ef4444'), # Verde se Over, Vermelho se Under
                            line=dict(width=1, color='#f8fafc'),
                            opacity=0.8
                        ),
                        text=scatter_df.apply(lambda r: f"Data: {r['GAME_DATE'].strftime('%d/%m')}<br>Matchup: {r['MATCHUP']}<br>Minutos: {r['MIN']}<br>{visual_metric}: {r[active_col]}", axis=1),
                        hoverinfo='text',
                        name='Jogos'
                    ))

                    # Adiciona a Linha de Tendência
                    fig_scatter.add_trace(go.Scatter(
                        x=trend_x, y=trend_y,
                        mode='lines',
                        line=dict(color='rgba(255, 255, 255, 0.4)', width=2, dash='dot'),
                        name='Tendência',
                        hoverinfo='skip'
                    ))

                    # Linha horizontal da aposta
                    fig_scatter.add_hline(
                        y=active_line, 
                        line_dash="dash", 
                        line_color=line_color, 
                        line_width=2,
                        annotation_text="Linha",
                        annotation_position="bottom right"
                    )

                    fig_scatter.update_layout(
                        template="plotly_dark",
                        height=400,
                        margin=dict(l=20, r=20, t=20, b=20),
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(15,23,42,0.35)",
                        xaxis_title="Minutos Jogados",
                        yaxis_title=f"Valor de {visual_metric}",
                        showlegend=False,
                        dragmode=False
                    )
                    fig_scatter.update_xaxes(showgrid=True, gridcolor="rgba(148,163,184,0.1)")
                    fig_scatter.update_yaxes(showgrid=True, gridcolor="rgba(148,163,184,0.1)")

                    st.plotly_chart(fig_scatter, use_container_width=True)
                    
                    # Explicação da correlação
                    correl = x.corr(y)
                    if correl > 0.7:
                        st.success(f"📈 **Alta Correlação ({correl:.2f}):** O desempenho do jogador é extremamente dependente dos minutos. Se o tempo de quadra subir hoje, o Over é muito provável.")
                    elif correl > 0.4:
                        st.info(f"📊 **Correlação Moderada ({correl:.2f}):** Mais minutos costumam trazer mais {visual_metric}, mas outros fatores (eficiência/matchup) também pesam.")
                    else:
                        st.warning(f"📉 **Baixa Correlação ({correl:.2f}):** Esse jogador produz de forma oscilante, independente de quanto tempo fica em quadra.")
            else:
                st.info(f"Dados indisponíveis para a métrica {visual_metric}.")
        else:
            st.info("Sem histórico suficiente para gerar o gráfico.")
            
    with market_tab:
        # 1. MAPEAMENTO COMPLETO
        team_map = {
            'Hawks': 'ATL', 'Celtics': 'BOS', 'Nets': 'BKN', 'Hornets': 'CHA', 'Bulls': 'CHI', 
            'Cavaliers': 'CLE', 'Mavericks': 'DAL', 'Nuggets': 'DEN', 'Pistons': 'DET', 
            'Warriors': 'GSW', 'Rockets': 'HOU', 'Pacers': 'IND', 'Clippers': 'LAC', 
            'Lakers': 'LAL', 'Grizzlies': 'MEM', 'Heat': 'MIA', 'Bucks': 'MIL', 
            'Timberwolves': 'MIN', 'Pelicans': 'NOP', 'Knicks': 'NYK', 'Thunder': 'OKC', 
            'Magic': 'ORL', '76ers': 'PHI', 'Suns': 'PHX', 'Kings': 'SAC', 
            'Spurs': 'SAS', 'Raptors': 'TOR', 'Jazz': 'UTA', 'Wizards': 'WAS', 'Trail Blazers': 'POR'
        }

        # 2. BUSCA BRUTA (Varre cada coluna do jogador para achar o adversário)
        opp_abbr = None
        player_team_name = str(row.get('TEAM_NAME', ''))
        
        # O "Pulo do Gato": olhamos todos os valores da linha em busca de um time
        for col_val in row.values:
            val_str = str(col_val)
            for team_name, abbr in team_map.items():
                # Se acharmos um nome de time (ex: Memphis) e não for o time do Jaylen (Celtics)
                if team_name in val_str and team_name not in player_team_name:
                    opp_abbr = abbr
                    break
            if opp_abbr: break

        # 3. LOGICA DE EXIBIÇÃO
        if opp_abbr:
            st.markdown(f"### ⚔️ Histórico de Confronto: vs {opp_abbr}")
            st.caption(f"Desempenho real de {row['PLAYER']} nos últimos 5 jogos contra a defesa do {opp_abbr}.")
            
            if not log.empty:
                # Filtra o histórico procurando a sigla no campo MATCHUP do log
                h2h_log = log[log['MATCHUP'].str.contains(opp_abbr)].copy()
                h2h_log = h2h_log.sort_values('GAME_DATE', ascending=False).head(5)
                
                if not h2h_log.empty:
                    # Cálculos
                    h2h_log["PRA"] = h2h_log["PTS"] + h2h_log["REB"] + h2h_log["AST"]
                    h2h_log["3PM"] = h2h_log.get("FG3M", 0)
                    
                    m_ctx = get_line_context(row, visual_metric, line_value, use_market_line)
                    m_line = float(m_ctx["line_value"])
                    h2h_log['Status'] = h2h_log[visual_metric].apply(lambda x: "OVER" if x > m_line else "UNDER")
                    h2h_hits = (h2h_log[visual_metric] > m_line).sum()
                    h2h_total = len(h2h_log)
                    h2h_pct = (h2h_hits / h2h_total) * 100
                    
                    # Cards de métricas
                    c1, c2, c3 = st.columns(3)
                    with c1: st.metric("Encontros", h2h_total)
                    with c2: 
                        avg_h2h = h2h_log[visual_metric].mean()
                        st.metric(f"Média vs {opp_abbr}", f"{avg_h2h:.1f}", delta=f"{avg_h2h - m_line:.1f}")
                    with c3:
                        st.markdown("**Sequência H2H:**")
                        # Cria as bolinhas baseadas no status
                        dots = "".join(['<span style="color:#28a745;font-size:22px">🟢</span>' if "OVER" in s 
                                   else '<span style="color:#dc3545;font-size:22px">🔴</span>' 
                                   for s in h2h_log['Status'].iloc[::-1]])
                        st.markdown(f'<div style="letter-spacing:2px">{dots}</div>', unsafe_allow_html=True)
                        st.caption(f"Aproveitamento: {h2h_pct:.0f}%")

                    # Tabela
                    # --- TABELA DETALHADA COM COLUNA DE LINHA ---
                    h2h_display = h2h_log[['GAME_DATE', 'MATCHUP', visual_metric, 'MIN']].copy()
                    
                    # Adicionamos a linha atual para comparação direta
                    h2h_display['Linha'] = m_line 
                    
                    # Formata a data para um padrão mais curto (ganha espaço)
                    h2h_display['GAME_DATE'] = h2h_display['GAME_DATE'].dt.strftime('%d/%m/%y')
                    
                    # Define o Status comparando o Real vs a Linha
                    h2h_display['Status'] = h2h_display[visual_metric].apply(lambda x: "✅ OVER" if x > m_line else "❌ UNDER")
                    
                    # REORDENAR COLUNAS: Colocamos a Linha ao lado do resultado real
                    cols_order = ['GAME_DATE', 'MATCHUP', 'Linha', visual_metric, 'MIN', 'Status']
                    h2h_display = h2h_display[cols_order]
                    
                    st.dataframe(
                        h2h_display, 
                        hide_index=True, 
                        use_container_width=True,
                        column_config={
                            "GAME_DATE": st.column_config.TextColumn("Data", width="small"),
                            "MATCHUP": st.column_config.TextColumn("Confronto", width="medium"),
                            "Linha": st.column_config.NumberColumn("Linha", format="%.1f"),
                            visual_metric: st.column_config.NumberColumn(f"Real ({visual_metric})", format="%.0f"),
                            "MIN": st.column_config.NumberColumn("Min", format="%d", width="small"),
                            "Status": st.column_config.TextColumn("Status", width="small")
                        }
                    )
                else:
                    st.info(f"Nenhum jogo registrado contra {opp_abbr} nesta temporada.")
            else:
                st.info("Log de histórico indisponível.")
        else:
            # Caso a busca falhe, mostramos as chaves para debugar (ajuda a gente a consertar)
            st.warning("Não foi possível detectar o adversário automaticamente.")
            with st.expander("Ver dados brutos (Debug)"):
                st.write(row.to_dict())


def render_player_card(row: pd.Series, line_metric: str, line_value: float, use_market_line: bool) -> None:
    # --- RASTREADOR DE CORES PARA O CARD ---
    search_text = f"{row.get('TEAM_NAME', '') or ''} {row.get('TEAM_ABBR', '') or ''}".upper()
    tk = 'NBA'
    
    # --- RASTREADOR DE CORES INTELIGENTE PARA O CARD ---
    # Pegamos o texto disponível do time (TEAM_NAME e TEAM_ABBR)
    search_text = f"{row.get('TEAM_NAME', '') or ''} {row.get('TEAM_ABBR', '') or ''}".upper()
    
    tk = 'NBA' # Default caso não encontre nada
    
    # Loop automático pelo dicionário NBA_TEAM_COLORS que está no topo do arquivo
    # Isso evita termos que escrever if/elif para os 30 times
    for abbr, info in NBA_TEAM_COLORS.items():
        # Pega o nome do time no dicionário (ex: 'PISTONS')
        team_keyword = info.get('name', '').upper()
        
        # Se 'PISTONS' estiver no texto de busca ('DETROIT PISTONS'), achamos o time!
        if team_keyword and team_keyword in search_text:
            tk = abbr
            break
        # Ou se a sigla 'DET' estiver no texto de busca
        elif abbr in search_text:
            tk = abbr
            break
            
    # Puxa as cores finais baseadas na sigla encontrada (tk)
    colors = NBA_TEAM_COLORS.get(tk, {'primary': '#1d222d', 'secondary': '#ffcc00'})
    
    colors = NBA_TEAM_COLORS.get(tk, {'primary': '#1d222d', 'secondary': '#ffcc00'})
    with st.container(border=True):
        top_left, top_right = st.columns([1, 4])

        with top_left:
            st.image(get_player_headshot_url(int(row["PLAYER_ID"])), width=72)

        with top_right:
            # Mini Banner no topo do card com as cores do time
            st.markdown(f"""
            <div style="
                background: linear-gradient(90deg, {colors['primary']} 0%, {colors['secondary']} 250%);
                border-left: 8px solid {colors['secondary']};
                padding: 12px;
                border-radius: 8px;
                margin-bottom: 12px;
                box-shadow: 2px 4px 10px rgba(0,0,0,0.3);
            ">
                <div style="color: {colors['secondary']}; font-weight: 800; font-size: 15px; line-height: 1.1;">
                    {row['PLAYER']}
                </div>
                <div style="color: {colors['secondary']}; opacity: 0.8; font-size: 10px; margin-top: 1px;">
                    {tk} | {row.get('POSITION', '')}
                </div>
            </div>
        """, unsafe_allow_html=True)
            position = row["POSITION"] if str(row["POSITION"]).strip() else "-"
            st.caption(f"Pos {position} • GP {int(row['SEASON_GP'])} • MIN {format_number(row['SEASON_MIN'])}")
            st.markdown(render_player_headline_html(row), unsafe_allow_html=True)
            render_badges(
                row["ROLE"],
                row.get("FORM_SIGNAL", "→ Estável"),
                row.get("OSC_CLASS", "-"),
                row.get("MATCHUP_LABEL", "Neutro"),
            )

        render_player_support_tiles(row, line_metric, line_value, use_market_line)
        line_context = get_line_context(row, line_metric, line_value, use_market_line=use_market_line)
        if line_context["has_market_line"] and line_context["over_dec"] and line_context["under_dec"]:
            st.caption(f"BetMGM • Linha {format_number(line_context['line_value'])} • Over {format_number(line_context['over_dec'], 2)} • Under {format_number(line_context['under_dec'], 2)}")
        else:
            st.caption("Detalhamento completo no painel abaixo.")


def render_player_cards_grid(
    filtered_df: pd.DataFrame,
    line_metric: str,
    line_value: float,
    use_market_line: bool,
    cards_per_row: int = 2,
) -> None:
    rows = [filtered_df.iloc[i:i + cards_per_row] for i in range(0, len(filtered_df), cards_per_row)]
    for row_df in rows:
        cols = st.columns(cards_per_row)
        for col_idx in range(cards_per_row):
            with cols[col_idx]:
                if col_idx < len(row_df):
                    render_player_card(row_df.iloc[col_idx], line_metric, line_value, use_market_line)


def render_team_section_legacy(
    team_name: str,
    team_df: pd.DataFrame,
    season: str,
    min_games: int,
    min_minutes: int,
    role_filter: str,
    sort_label: str,
    ascending: bool,
    view_mode: str,
    chart_mode: str,
    line_metric: str,
    line_value: float,
    use_market_line: bool,
    cards_per_row: int,
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
        st.warning("Nenhum jogador passou pelos filtros. Você apertou demais o funil, pequeno fiscal da amostra.")
        return

    active_source = "BetMGM quando disponível" if use_market_line else "Linha manual"
    st.markdown(
        f"""
        <div class="info-pill">Jogadores exibidos: {len(filtered_df)}</div>
        <div class="info-pill">GP mínimo: {min_games}</div>
        <div class="info-pill">MIN mínimo: {min_minutes}</div>
        <div class="info-pill">Papel: {role_filter}</div>
        <div class="info-pill">Ordenação: {sort_label}</div>
        <div class="info-pill">Visualização: {view_mode}</div>
        <div class="info-pill">Linha ativa: {active_source}</div>
        <div class="info-pill">Adversário: {filtered_df['OPP_TEAM_NAME'].iloc[0]}</div>
        """,
        unsafe_allow_html=True,
    )

    if view_mode == "Cards":
        st.markdown(
            '<div class="section-note">Cards curtos no topo e painel fixo do jogador abaixo para facilitar consulta no celular.</div>',
            unsafe_allow_html=True,
        )
        render_player_cards_grid(filtered_df, line_metric=line_metric, line_value=line_value, use_market_line=use_market_line, cards_per_row=cards_per_row)
    else:
        summary_df, detail_df = build_display_dataframes(filtered_df)
        quick_tab, detail_tab = st.tabs(["Leitura rápida", "Detalhamento"])
        with quick_tab:
            st.markdown(
                '<div class="section-note">Aqui o foco é no que bate rápido no olho: PRA, projeção, matchup, hit PRA, oscilação e sinal de forma.</div>',
                unsafe_allow_html=True,
            )
            st.dataframe(style_table(summary_df, quick_view=True), use_container_width=True, hide_index=True)
        with detail_tab:
            st.markdown(
                '<div class="section-note">Aqui entra a parte mais detalhada: PTS, REB, AST, PRA, projeções e hit rate separado por atributo.</div>',
                unsafe_allow_html=True,
            )
            st.dataframe(style_table(detail_df, quick_view=False), use_container_width=True, hide_index=True)

    options = filtered_df[["PLAYER", "PLAYER_ID"]].drop_duplicates()
    player_name = st.selectbox(
        f"Jogador em foco — {team_name}",
        options["PLAYER"].tolist(),
        key=f"player_focus_{team_name}_{view_mode}_{chart_mode}_{line_metric}",
    )
    selected_row = filtered_df.loc[filtered_df["PLAYER"] == player_name].iloc[0]
    render_player_focus_panel(selected_row, line_metric, line_value, use_market_line, season, chart_mode)


def main() -> None:
    inject_css()

    st.markdown('<div class="main-title">NBA Props Dashboard</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtitle">Escolha o jogo, defina a métrica e compare projeção, consistência e linha ativa por jogador.</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="hero-pills">
            <span class="hero-pill">Projeções</span>
            <span class="hero-pill">Linha manual / BetMGM</span>
            <span class="hero-pill">Leitura rápida mobile</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Configurações")
        selected_date = get_brasilia_today()
        st.caption(f"Jogos do dia em Brasília • {selected_date.strftime('%d/%m/%Y')}")

        st.divider()
        # Troca as bolinhas de seleção por pílulas clicáveis
        _selected_chart = st.pills("Modo do gráfico", CHART_OPTIONS, default="Compacto")
        # Trava de segurança: se desmarcar, volta pro Compacto
        chart_mode = _selected_chart if _selected_chart else "Compacto"
        # Troca o slider por pílulas clicáveis
        _selected_cards = st.pills("Cards por linha", [1, 2], default=2)
        # Trava de segurança: se desmarcar, volta para 2 cards por padrão
        cards_per_row = _selected_cards if _selected_cards else 2

        st.divider()
        st.subheader("Filtros")
        min_games = st.slider("Mínimo de jogos na temporada", 0, 82, 5, 1)
        min_minutes = st.slider("Mínimo de minutos por jogo", 0, 40, 15, 1)
        # Troca a caixa de seleção por botões clicáveis (pills)
        _selected_role = st.pills("Mostrar jogadores", ROLE_OPTIONS, default="Todos")
        # Trava de segurança: se desmarcar tudo sem querer, volta a mostrar o time inteiro
        role_filter = _selected_role if _selected_role else "Todos"

        st.divider()
        st.subheader("Linha")
        
        # Cria os botões visíveis na barra lateral
        _selected_metric = st.pills("Métrica da linha", LINE_METRIC_OPTIONS, default="PRA")
        # Trava de segurança: se o usuário desmarcar tudo, forçamos o PRA para não quebrar a lógica
        line_metric = _selected_metric if _selected_metric else "PRA"
        
        default_line_map = {"PRA": 25.5, "PTS": 20.5, "REB": 7.5, "AST": 5.5, "3PM": 2.5, "FGA": 15.5, "3PA": 6.5}
        line_value = st.number_input(
            "Valor da linha manual",
            min_value=0.0,
            value=float(default_line_map[line_metric]),
            step=0.5,
            key=f"manual_line_{line_metric}",
        )
        api_key_available = bool(get_odds_api_key())
        use_market_line = st.toggle(
            "Usar linha BetMGM",
            value=api_key_available,
            disabled=not api_key_available,
        )
        if not api_key_available:
            st.caption("Adicione SPORTSGAMEODDS_API_KEY em st.secrets ou variável de ambiente para usar BetMGM.")

        # Ordenação Automática "Invisível"
        # Ele pega a métrica selecionada (Ex: "PTS") e avisa pro app ordenar por "PTS L10" do maior pro menor
        sort_label = f"{line_metric} L10"
        if sort_label not in SORT_OPTIONS:
            sort_label = "PRA L10" # Trava de segurança
        ascending = False

        st.divider()
        st.caption("Este app busca os dados ao abrir a página.")
        if st.button("Forçar atualização"):
            st.cache_data.clear()
            st.rerun()

    season = get_season_string(selected_date)

    try:
        games = get_games_for_date(selected_date)
    except Exception as exc:
        st.error("A NBA demorou ou falhou ao responder na consulta dos jogos. Tente novamente em alguns segundos ou use o botão de atualização.")
        st.exception(exc)
        return

    st.caption(f"Temporada detectada: {season}")

    if games.empty:
        st.warning("Não encontrei jogos para hoje. A NBA também sabe sabotar entretenimento.")
        return

    game_label = st.selectbox("Escolha o jogo de hoje", games["label"].tolist())
    selected_game = games.loc[games["label"] == game_label].iloc[0]

    try:
        away_df, home_df = get_matchup_context(
            away_team_id=int(selected_game["VISITOR_TEAM_ID"]),
            home_team_id=int(selected_game["HOME_TEAM_ID"]),
            away_team_name=selected_game["away_team_name"],
            home_team_name=selected_game["home_team_name"],
            season=season,
            include_market=api_key_available,
        )
    except Exception as exc:
        st.error("A NBA demorou ou falhou ao responder nas estatísticas do confronto. Tente novamente em alguns segundos ou use o botão de atualização.")
        st.exception(exc)
        return    
        
   

    render_matchup_header(selected_game)
    st.caption("Injury report oficial temporariamente desativado para teste de performance.")    
    
        
    render_summary_cards(
        away_df=away_df,
        home_df=home_df,
        min_games=min_games,
        min_minutes=min_minutes,
        role_filter=role_filter,
    )
    render_game_rankings(
        away_df=away_df,
        home_df=home_df,
        min_games=min_games,
        min_minutes=min_minutes,
        role_filter=role_filter,
        line_metric=line_metric,
        line_value=line_value,
        use_market_line=use_market_line,
    )



    selected_team_view = st.segmented_control(
        "Time em análise",
        options=[selected_game["away_team_name"], selected_game["home_team_name"]],
        default=selected_game["away_team_name"],
        key=f"team_view_{selected_game['GAME_ID']}",
    )

    if selected_team_view == selected_game["away_team_name"]:
        render_team_section_v2(
            team_name=selected_game["away_team_name"],
            team_df=away_df,
            season=season,
            min_games=min_games,
            min_minutes=min_minutes,
            role_filter=role_filter,
            sort_label=sort_label,
            ascending=ascending,
            chart_mode=chart_mode,
            line_metric=line_metric,
            line_value=line_value,
            use_market_line=use_market_line,
            cards_per_row=cards_per_row,
        )
    else:
        render_team_section_v2(
            team_name=selected_game["home_team_name"],
            team_df=home_df,
            season=season,
            min_games=min_games,
            min_minutes=min_minutes,
            role_filter=role_filter,
            sort_label=sort_label,
            ascending=ascending,
            chart_mode=chart_mode,
            line_metric=line_metric,
            line_value=line_value,
            use_market_line=use_market_line,
            cards_per_row=cards_per_row,
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
def render_injury_report_tab(team_df: pd.DataFrame, team_name: str) -> None:
    st.markdown(
        '<div class="section-note">Status oficial do injury report da NBA para o elenco do time.</div>',
        unsafe_allow_html=True,
    )

    if "INJ_STATUS" not in team_df.columns:
        st.info("Injury report ainda não integrado nesta execução.")
        return

    report_url = ""
    if "INJ_REPORT_URL" in team_df.columns:
        valid_urls = team_df["INJ_REPORT_URL"].dropna().astype(str)
        valid_urls = valid_urls[valid_urls.str.strip() != ""]
        if not valid_urls.empty:
            report_url = valid_urls.iloc[0]

    report_meta = parse_injury_report_timestamp_from_url(report_url)

    top_cols = st.columns([1.4, 1.2, 1.4])
    with top_cols[0]:
        st.caption(f"PDF oficial: {report_meta['report_label_et']}")
    with top_cols[1]:
        st.caption(f"Brasília: {report_meta['report_label_brt']}")
    with top_cols[2]:
        if report_url:
            st.caption("Fonte oficial carregada")
        else:
            st.caption("Fonte oficial não identificada")

    if "INJ_MATCHUP_FOUND" in team_df.columns and not bool(team_df["INJ_MATCHUP_FOUND"].any()):
        st.warning("Não encontrei linhas do injury report oficial para este matchup. O app não deve assumir disponibilidade oficial aqui.")

    report_df = team_df[["PLAYER", "INJ_STATUS", "INJ_REASON"]].copy()
    report_df = report_df.rename(
        columns={
            "PLAYER": "Jogador",
            "INJ_STATUS": "Status oficial",
            "INJ_REASON": "Motivo",
        }
    )

    st.dataframe(report_df, use_container_width=True)

    unavailable = team_df[team_df["INJ_STATUS"].isin(["Out", "Doubtful"])]
    if not unavailable.empty:
        st.warning(
            f"{len(unavailable)} jogador(es) marcados como indisponíveis e que devem sair da leitura da provável escalação."
        )


def render_lineup_report_tab(team_df: pd.DataFrame, team_name: str) -> None:
    st.markdown(
        '<div class="section-note">Estrutura de rotação do time já filtrada por indisponibilidade oficial quando o injury report estiver carregado.</div>',
        unsafe_allow_html=True,
    )

    lineup_df = team_df.copy()
    if "IS_UNAVAILABLE" in lineup_df.columns:
        lineup_df = lineup_df[~lineup_df["IS_UNAVAILABLE"]].copy()

    starters = lineup_df[lineup_df["ROLE"] == "Titular provável"].copy()
    bench = lineup_df[lineup_df["ROLE"] != "Titular provável"].copy()

    top_cols = st.columns([1.2, 1.2, 1.6])
    with top_cols[0]:
        st.markdown(
            render_single_card(
                "Titulares prováveis",
                str(len(starters)),
                team_name,
                "Média MIN",
                format_number(starters["SEASON_MIN"].mean() if not starters.empty else 0),
                "PRA médio",
                format_number(starters["SEASON_PRA"].mean() if not starters.empty else 0),
            ),
            unsafe_allow_html=True,
        )
    with top_cols[1]:
        st.markdown(
            render_single_card(
                "Reservas",
                str(len(bench)),
                team_name,
                "Média MIN",
                format_number(bench["SEASON_MIN"].mean() if not bench.empty else 0),
                "PRA médio",
                format_number(bench["SEASON_PRA"].mean() if not bench.empty else 0),
            ),
            unsafe_allow_html=True,
        )
    with top_cols[2]:
        st.info("Jogadores Out/Doubtful saem automaticamente da leitura quando o injury report oficial estiver carregado.")

    st.markdown("#### Provável escalação")
    starter_view = (
        starters[["PLAYER", "POSITION", "SEASON_MIN", "SEASON_PRA", "L10_PRA", "TREND", "INJ_STATUS"]].copy()
        if not starters.empty
        else pd.DataFrame(columns=["PLAYER", "POSITION", "SEASON_MIN", "SEASON_PRA", "L10_PRA", "TREND", "INJ_STATUS"])
    )
    starter_view = starter_view.rename(
        columns={
            "PLAYER": "Jogador",
            "POSITION": "Pos",
            "SEASON_MIN": "MIN",
            "SEASON_PRA": "PRA Temp",
            "L10_PRA": "PRA L10",
            "TREND": "Trend",
            "INJ_STATUS": "Status",
        }
    )
    st.dataframe(style_table(starter_view, quick_view=True), use_container_width=True)

    st.markdown("#### Rotação / banco")
    bench_view = (
        bench[["PLAYER", "POSITION", "SEASON_MIN", "SEASON_PRA", "L10_PRA", "TREND", "INJ_STATUS"]].copy()
        if not bench.empty
        else pd.DataFrame(columns=["PLAYER", "POSITION", "SEASON_MIN", "SEASON_PRA", "L10_PRA", "TREND", "INJ_STATUS"])
    )
    bench_view = bench_view.rename(
        columns={
            "PLAYER": "Jogador",
            "POSITION": "Pos",
            "SEASON_MIN": "MIN",
            "SEASON_PRA": "PRA Temp",
            "L10_PRA": "PRA L10",
            "TREND": "Trend",
            "INJ_STATUS": "Status",
        }
    )
    st.dataframe(style_table(bench_view, quick_view=True), use_container_width=True)


def render_team_section_v2(
    team_name: str,
    team_df: pd.DataFrame,
    season: str,
    min_games: int,
    min_minutes: int,
    role_filter: str,
    sort_label: str,
    ascending: bool,
    chart_mode: str,
    line_metric: str,
    line_value: float,
    use_market_line: bool,
    cards_per_row: int,
) -> None:
    if team_df.empty:
        st.warning(f"Não encontrei dados para {team_name}.")
        return

    sort_column = SORT_OPTIONS[sort_label]
    filtered_df = filter_and_sort_team_df(team_df, min_games, min_minutes, role_filter, sort_column, ascending)
    if filtered_df.empty:
        st.info("Nenhum jogador desse time passou pelos filtros atuais.")
        return

    st.markdown(f"### {team_name}")

    line_info_text = (
        f"Linha ativa: {line_metric} • mercado por jogador (BetMGM quando houver)"
        if use_market_line
        else f"Linha ativa: {line_metric} {format_number(line_value)} • manual"
    )

    st.markdown(
        f'<div><span class="info-pill">Jogadores: {len(filtered_df)}</span><span class="info-pill">{line_info_text}</span><span class="info-pill">Modo mercado: {"BetMGM quando houver" if use_market_line else "Manual"}</span></div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="section-note">Cards curtos no topo e painel detalhado do jogador sob demanda, para não carregar tranqueira à toa.</div>',
        unsafe_allow_html=True,
    )

    render_player_cards_grid(
        filtered_df,
        line_metric=line_metric,
        line_value=line_value,
        use_market_line=use_market_line,
        cards_per_row=cards_per_row,
    )

    options = filtered_df[["PLAYER", "PLAYER_ID"]].drop_duplicates()
    player_name = st.selectbox(
        f"Jogador em foco — {team_name}",
        options["PLAYER"].tolist(),
        key=f"player_focus_v2_{team_name}",
    )

    show_focus_panel = st.toggle(
        f"Mostrar análise detalhada — {team_name}",
        value=False,
        key=f"show_focus_panel_{team_name}",
    )

    if show_focus_panel:
        selected_row = filtered_df.loc[filtered_df["PLAYER"] == player_name].iloc[0]
        render_player_focus_panel(
            selected_row,
            line_metric,
            line_value,
            use_market_line,
            season,
            chart_mode,
        )

    st.divider()
    show_injury = st.toggle(f"🏥 Carregar Status Oficial — {team_name}", key=f"ir_toggle_{team_name}")

    if show_injury:
        with st.spinner("Buscando PDF oficial da NBA..."):
            injury_df = fetch_latest_injury_report_df()
            team_id = next((tid for tid, t in TEAM_LOOKUP.items() if t.get("full_name") == team_name), 0)
            
            enriched_team_df = merge_injury_report(
                team_df=team_df,
                injury_df=injury_df,
                team_name=team_name,
                team_id=team_id
            )
            
            ir_tab, lineup_tab = st.tabs(["Status Oficial", "Rotação Atualizada"])
            with ir_tab: render_injury_report_tab(enriched_team_df, team_name)
            with lineup_tab: render_lineup_report_tab(enriched_team_df, team_name)
    
if __name__ == "__main__":
    main()

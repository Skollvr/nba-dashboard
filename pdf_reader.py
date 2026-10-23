import re
import time
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib.parse import urljoin, urlsplit
from datetime import date, datetime, timedelta
from io import BytesIO

import pandas as pd
import requests
import streamlit as st
from pypdf import PdfReader

# Importamos as configurações e o fuso horário
from config import (
    APP_TIMEZONE, EASTERN_TIMEZONE, TEAM_NAME_LOOKUP_NORM,
    TEAM_LOOKUP, TEAM_ABBR_LOOKUP,
)
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
        urlsplit(str(pdf_url)).path,
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
def _injury_links_from_html(html: str, page_url: str) -> list[str]:
    """Recognize NBA PDF hrefs even if page markup/query arguments change."""
    links = re.findall(
        r"""(?:href|data-href)\s*=\s*["']([^"']+?\.pdf(?:\?[^"']*)?)["']""",
        html, flags=re.IGNORECASE,
    )
    links += re.findall(
        r"""https?://[^\s"'<>]+?Injury[-_]Report[^\s"'<>]*?\.pdf""",
        html, flags=re.IGNORECASE,
    )
    result: list[str] = []
    seen_paths: set[str] = set()
    for raw in links:
        url = urljoin(page_url, unescape(raw.strip()))
        pathname = urlsplit(url).path.lower()
        if "injury" in pathname and "report" in pathname and pathname.endswith(".pdf"):
            # The same PDF can occur once in href (with a query string) and
            # again in the raw URL regex (without it). Preserve the original
            # first URL, but never fetch the same report twice.
            canonical_path = pathname.rsplit("/", 1)[-1]
            if canonical_path not in seen_paths:
                result.append(url)
                seen_paths.add(canonical_path)
    return result


def _official_pdf_recent_candidates(now_et: datetime) -> list[str]:
    """Generate the NBA CDN's existing 15-minute PDF filename convention."""
    rounded = now_et.replace(minute=(now_et.minute // 15) * 15, second=0, microsecond=0)
    prefix = "https://ak-static.cms.nba.com/referee/injury/"
    return [
        prefix + f"Injury-Report_{(rounded - timedelta(minutes=15*i)):%Y-%m-%d_%I_%M%p}.pdf"
        for i in range(9)
    ]


def _pdf_header_ok(url: str, timeout=(3, 5)) -> bool:
    """Stream first bytes of a PDF; do not rely on CDN support for Range."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
        ),
        "Accept": "application/pdf,*/*",
        "Referer": "https://official.nba.com/",
    }
    try:
        with requests.get(url, headers=headers, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            chunk = next(response.iter_content(chunk_size=64), b"")
            return chunk.startswith(b"%PDF")
    except Exception:
        return False


@st.cache_data(ttl=300, show_spinner=False)
def fetch_latest_injury_report_pdf_url() -> str:
    """Locate the latest official NBA bulletin, even if the index is blocked."""
    now_et = datetime.now(EASTERN_TIMEZONE)
    season = get_season_string(now_et.date())
    page_url = f"https://official.nba.com/nba-injury-report-{season}-season/"
    links: list[str] = []
    try:
        response = requests.get(
            page_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,*/*",
            },
            timeout=(3, 7),
        )
        response.raise_for_status()
        links = _injury_links_from_html(response.text, page_url)
    except Exception:
        pass

    # Robust fallback: the static NBA CDN may work even if official.nba.com
    # cannot be fetched from Streamlit Cloud.
    candidates = links + _official_pdf_recent_candidates(now_et)
    unique: dict[str, datetime] = {}
    for link in candidates:
        stamp = parse_report_dt_from_url(link)
        # A previous day's stale PDF is not a valid report for today's game.
        if stamp is not None and now_et - timedelta(hours=12) <= stamp <= now_et:
            unique[link] = stamp
    ordered = [
        url for url, _ in sorted(
            unique.items(), key=lambda item: item[1], reverse=True
        )
    ]
    # Verify candidate PDFs in short parallel batches to avoid a long sequence
    # of Cloud network timeouts if the NBA CDN is temporarily inaccessible.
    for start in range(0, len(ordered), 4):
        batch = ordered[start:start + 4]
        with ThreadPoolExecutor(max_workers=4) as pool:
            verified = list(pool.map(_pdf_header_ok, batch))
        for url, ok in zip(batch, verified):
            if ok:
                return url
    return ""


def _page_text_is_reversed(lines: list[str]) -> bool | None:
    """Detect upside-down text extraction independently of PDF page order.

    Some NBA PDFs are laid out correctly but pypdf's layout extractor emits
    table rows bottom-to-top. A stateful parser then assigns the next team's
    injuries to the previous team. The printed header and row chronology make
    this reversible without guessing team membership from player names.
    """
    valid = [str(line).strip() for line in lines if str(line).strip()]
    if not valid:
        return None
    # Date/time runs are stronger evidence than header position: some PDF
    # extractors place the title first while returning BODY rows reversed.
    # Date/time runs proceed forward in the NBA report. Prefer the report
    # date if multiple calendar dates occur on a page.
    dates: list[date] = []
    for line in valid:
        match = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", line)
        if match:
            try:
                dates.append(date(int(match.group(3)), int(match.group(1)), int(match.group(2))))
            except ValueError:
                continue
    if len(dates) >= 2 and dates[0] != dates[-1]:
        return dates[0] > dates[-1]
    times = []
    for line in valid:
        match = re.search(r"(\d{2}):(\d{2})\s*\(ET\)", line)
        if match:
            times.append(int(match.group(1)) * 60 + int(match.group(2)))
    if len(times) >= 2 and times[0] != times[-1]:
        return times[0] > times[-1]
    for idx, line in enumerate(valid):
        if "Game Date" in line and "Player Name" in line:
            return idx > len(valid) / 2
    return None


def extract_pdf_text_lines(pdf_bytes: bytes) -> list[str]:
    """Extract NBA PDF rows in visual TOP-to-bottom order for each page."""
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        lines: list[str] = []
        first_page_reversed: bool | None = None

        for page in reader.pages:
            try:
                text = page.extract_text(extraction_mode="layout") or ""
            except TypeError:
                text = page.extract_text() or ""

            page_lines = [x for x in text.splitlines() if x.strip()]
            reversed_page = _page_text_is_reversed(page_lines)
            if first_page_reversed is None and reversed_page is not None:
                first_page_reversed = reversed_page
            if reversed_page is None:
                reversed_page = first_page_reversed
            if reversed_page:
                page_lines.reverse()
            lines.extend(
                clean_injury_pdf_line(x)
                for x in page_lines if clean_injury_pdf_line(x)
            )
        return lines
    except Exception as exc:
        st.error(
            "Diagnóstico do boletim: PDF recebido, mas texto não pôde ser "
            f"extraído ({type(exc).__name__})."
        )
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


def _team_belongs_to_matchup(team_name: str, matchup: str) -> bool:
    """Reject rows with team IDs impossible for the named matchup."""
    if not team_name or not matchup or "@" not in matchup:
        return False
    team_id = _NBA_REPORT_TEAM_IDS.get(team_name)
    team_abbr = TEAM_ABBR_LOOKUP.get(team_id, "")
    return team_abbr in matchup.split("@")


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
            # A new matchup cannot inherit the previous game's team.
            team_name = ""
        if new_team:
            # If the PDF extraction is still out of reading order, fail safe
            # instead of assigning a 76ers player to DEN@OKC, for example.
            team_name = (
                new_team if _team_belongs_to_matchup(new_team, matchup) else ""
            )

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
        if match and team_name and _team_belongs_to_matchup(team_name, matchup):
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
def fetch_latest_injury_report_result() -> dict:
    """Return PDF rows plus the exact stage of any failed download/parse."""
    url = fetch_latest_injury_report_pdf_url()
    info = {
        "pdf_url": url,
        "stage": "Localização do PDF",
        "detail": "",
        "pdf_bytes": 0,
        "text_lines": 0,
        "parsed_rows": 0,
        "teams": 0,
        "frame": pd.DataFrame(),
    }
    if not url:
        info["detail"] = (
            "Nenhum PDF validado no índice da NBA nem nos URLs recentes "
            "do CDN. Verifique acesso do Streamlit Cloud aos dois domínios."
        )
        return info

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/pdf,*/*",
        "Referer": "https://official.nba.com/",
    }
    try:
        response = requests.get(url, headers=headers, timeout=(4, 12))
        response.raise_for_status()
        info["pdf_bytes"] = len(response.content)
        if not response.content.startswith(b"%PDF"):
            info["stage"] = "Download"
            info["detail"] = "URL retornou conteúdo que não é PDF."
            return info
    except Exception as exc:
        info["stage"] = "Download"
        info["detail"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return info

    lines = extract_pdf_text_lines(response.content)
    info["text_lines"] = len(lines)
    if not lines:
        info["stage"] = "Extração de texto"
        info["detail"] = "PDF recebido, mas não foi possível extrair o texto."
        return info

    frame = parse_official_injury_report_lines(lines, url)
    info["parsed_rows"] = len(frame)
    if frame.empty:
        info["stage"] = "Parser do boletim"
        info["detail"] = "Texto presente no PDF, mas nenhum jogador/time foi identificado."
        return info

    info["frame"] = frame
    info["teams"] = frame["TEAM_ID_IR"].dropna().nunique()
    info["stage"] = "Concluído"
    info["detail"] = "PDF oficial lido; conferir correspondência do confronto e equipe."
    return info


@st.cache_data(ttl=300, show_spinner=False)
def fetch_latest_injury_report_df() -> pd.DataFrame:
    return fetch_latest_injury_report_result()["frame"]


def clear_injury_cache() -> None:
    """Limpa somente os caches do injury report."""
    for cached_fn in [fetch_latest_injury_report_pdf_url, fetch_latest_injury_report_result, fetch_latest_injury_report_df]:
        try:
            cached_fn.clear()
        except Exception:
            pass

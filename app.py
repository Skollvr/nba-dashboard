from datetime import date, datetime
from io import BytesIO
import os
import re
import time
import unicodedata
from typing import Optional
from zoneinfo import ZoneInfo
from pandas.io.formats.style import Styler
from nba_api.stats.endpoints import scoreboardv2

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from pypdf import PdfReader
import requests
import streamlit as st

from nba_api.live.nba.endpoints import scoreboard as live_scoreboard
from nba_api.stats.endpoints import (
    commonteamroster,
    leaguedashplayerstats,
    playergamelog,
    playergamelogs,
)
from nba_api.stats.static import teams


st.set_page_config(
    page_title="NBA Dashboard MVP",
    page_icon="🏀",
    layout="wide",
)


TEAM_LOOKUP = {team["id"]: team for team in teams.get_teams()}
TEAM_ABBR_LOOKUP = {team["id"]: team.get("abbreviation", "") for team in teams.get_teams()}
TEAM_LOGO_URL = "https://cdn.nba.com/logos/nba/{team_id}/primary/L/logo.svg"
PLAYER_HEADSHOT_URL = "https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"

SORT_OPTIONS = {
    "PRA L10": "L10_PRA",
    "Δ PRA L10 vs Temp": "DELTA_PRA_L10",
    "PRA L5": "L5_PRA",
    "Δ PRA L5 vs Temp": "DELTA_PRA_L5",
    "PTS L10": "L10_PTS",
    "REB L10": "L10_REB",
    "AST L10": "L10_AST",
    "PRA temporada": "SEASON_PRA",
    "PTS temporada": "SEASON_PTS",
    "REB temporada": "SEASON_REB",
    "AST temporada": "SEASON_AST",
    "Minutos por jogo": "SEASON_MIN",
    "Jogos na temporada": "SEASON_GP",
    "Nome do jogador": "PLAYER",
}

ROLE_OPTIONS = ["Todos", "Titular provável", "Reserva"]
VIEW_OPTIONS = ["Cards", "Tabela"]
CHART_OPTIONS = ["Compacto", "Completo"]
LINE_METRIC_OPTIONS = ["PRA", "PTS", "REB", "AST", "3PM", "FGA", "3PA"]

PROJECTION_WEIGHTS = {
    "season": 0.35,
    "l10": 0.40,
    "l5": 0.15,
    "matchup": 0.10,
}

ODDS_API_BASE_URL = "https://api.sportsgameodds.com/v2"
ODDS_BOOKMAKER = "betmgm"
ODDS_STAT_MAP = {
    "points": "PTS",
    "rebounds": "REB",
    "assists": "AST",
    "points+rebounds+assists": "PRA",
    "threePointersMade": "3PM",
    "fieldGoalsAttempted": "FGA",
    "threePointersAttempted": "3PA",
}
ODDS_METRIC_COLUMNS = {
    "PTS": ("BETMGM_PTS_LINE", "BETMGM_PTS_OVER_DEC", "BETMGM_PTS_UNDER_DEC", "BETMGM_PTS_UPDATED_AT"),
    "REB": ("BETMGM_REB_LINE", "BETMGM_REB_OVER_DEC", "BETMGM_REB_UNDER_DEC", "BETMGM_REB_UPDATED_AT"),
    "AST": ("BETMGM_AST_LINE", "BETMGM_AST_OVER_DEC", "BETMGM_AST_UNDER_DEC", "BETMGM_AST_UPDATED_AT"),
    "PRA": ("BETMGM_PRA_LINE", "BETMGM_PRA_OVER_DEC", "BETMGM_PRA_UNDER_DEC", "BETMGM_PRA_UPDATED_AT"),
    "3PM": ("BETMGM_3PM_LINE", "BETMGM_3PM_OVER_DEC", "BETMGM_3PM_UNDER_DEC", "BETMGM_3PM_UPDATED_AT"),
    "FGA": ("BETMGM_FGA_LINE", "BETMGM_FGA_OVER_DEC", "BETMGM_FGA_UNDER_DEC", "BETMGM_FGA_UPDATED_AT"),
    "3PA": ("BETMGM_3PA_LINE", "BETMGM_3PA_OVER_DEC", "BETMGM_3PA_UNDER_DEC", "BETMGM_3PA_UPDATED_AT"),
}

INJURY_REPORT_PAGE = "https://official.nba.com/nba-injury-report-2025-26-season/"
INACTIVE_STATUSES = {"Out", "Doubtful"}
WATCHLIST_STATUSES = {"Questionable", "Probable"}

PLAYER_STATUS_RE = re.compile(
    r"(?P<player>[A-Za-zÀ-ÿ0-9'\.\-\s]+,\s+[A-Za-zÀ-ÿ0-9'\.\-\s]+)\s+"
    r"(?P<status>Available|Out|Questionable|Probable|Doubtful)\b"
    r"(?:\s+(?P<reason>.*))?$"
)

GAME_PREFIX_RE = re.compile(
    r"^(?:(?P<game_date>\d{2}/\d{2}/\d{4})\s+)?"
    r"(?P<game_time>\d{1,2}:\d{2})\s+\(ET\)\s+"
    r"(?P<matchup>[A-Z]{2,3}@[A-Z]{2,3})\s+"
    r"(?P<rest>.+)$"
)

APP_TIMEZONE = ZoneInfo("America/Sao_Paulo")
EASTERN_TIMEZONE = ZoneInfo("America/New_York")
UTC_TIMEZONE = ZoneInfo("UTC")

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

def parse_injury_report_timestamp_from_url(pdf_url: str) -> dict:
    if not pdf_url:
        return {
            "report_label_et": "—",
            "report_label_brt": "—",
            "report_dt_et": None,
            "report_dt_brt": None,
        }

    match = re.search(
        r"Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{1,2})_(\d{2})(AM|PM)\.pdf",
        str(pdf_url),
        flags=re.IGNORECASE,
    )
    if not match:
        return {
            "report_label_et": "—",
            "report_label_brt": "—",
            "report_dt_et": None,
            "report_dt_brt": None,
        }

    date_part = match.group(1)
    hour_part = int(match.group(2))
    minute_part = int(match.group(3))
    ampm_part = match.group(4).upper()

    if ampm_part == "AM":
        hour_24 = 0 if hour_part == 12 else hour_part
    else:
        hour_24 = 12 if hour_part == 12 else hour_part + 12

    dt_et = datetime.strptime(date_part, "%Y-%m-%d").replace(
        hour=hour_24,
        minute=minute_part,
        second=0,
        microsecond=0,
        tzinfo=EASTERN_TIMEZONE,
    )
    dt_brt = dt_et.astimezone(APP_TIMEZONE)

    return {
        "report_label_et": dt_et.strftime("%d/%m %I:%M %p ET"),
        "report_label_brt": dt_brt.strftime("%d/%m %H:%M BRT"),
        "report_dt_et": dt_et,
        "report_dt_brt": dt_brt,
    }


fala comigo monday, seguinte: 

Tenho um app em Streamlit para análise de jogos da NBA, focado em estatísticas de jogadores, leitura rápida para mobile, projeções e comparação com linhas de mercado.

Contexto atual do app:
- mostra apenas os jogos do dia, ajustados para horário de Brasília
- permite selecionar um jogo e ver os jogadores de cada time
- tem cards de destaque, ranking do confronto e painel de jogador em foco
- trabalha com métricas como PRA, PTS, REB, AST, 3PM, FGA e 3PA
- já integra odds da BetMGM via SportsGameOdds, usando odds decimais
- possui linha manual e opção de usar linha de mercado quando disponível
- hoje já existem abas como Cards, Tabela, Injury Report e Provável Escalação

O problema atual:
- a aba Injury Report ainda precisa ser corrigida/atualizada com o patch mais recente que integra o injury report oficial da NBA via PDF oficial
- a aba Provável Escalação também precisa respeitar corretamente os status oficiais, removendo ou filtrando jogadores Out/Doubtful
- quero aplicar esse patch em cima do app atual, sem reescrever tudo do zero, a menos que seja realmente necessário

Vou enviar agora:
1. o arquivo app.py atual
2. o requirements.txt atual

O que eu quero nesta etapa:
- aplicar somente o patch do Injury Report oficial da NBA
- garantir que a aba Provável Escalação passe a respeitar os status oficiais
- manter o restante do app funcionando como já está
- evitar mudanças desnecessárias fora desse escopo

Depois disso, as próximas evoluções planejadas para o app são:
- melhorar a confiabilidade da provável escalação
- adicionar opportunity score usando projeção + linha + consistência + matchup
- eventualmente incluir movimentação de linha / snapshot de odds
- continuar refinando a experiência mobile sem poluir os cards iniciais

Pode analisar o app.py que vou enviar e me devolver a versão atualizada já com esse patch aplicado.

Thought for 7s
Manda os dois arquivos e eu faço o remendo sem transformar seu app num Frankenstein acadêmico.

Quero receber:

app.py

requirements.txt

Assim que você enviar, eu vou:

localizar onde o app atual monta a aba Injury Report

aplicar o patch para usar o injury report oficial da NBA via PDF oficial

ajustar a aba Provável Escalação para respeitar os status oficiais, filtrando pelo menos Out e Doubtful

preservar o resto do fluxo do app o máximo possível, sem sair quebrando o que já funciona só porque o código acordou triste

No retorno, eu te entrego a versão atualizada do app.py já com o patch aplicado e, se precisar, aponto qualquer ajuste mínimo no requirements.txt.



# Patch enxuto — Injury Report ofic.txt
Documento

requirements.txt
Documento

app.py
Python
ta ai o app.py, o requeriments e as instruçoes do patch para voce entender

Vou abrir o código inteiro e localizar só os pontos do patch, sem sair chutando parede estrutural do app como um estagiário caffeinado.

Achei um detalhe chato, que é claro que o patch original assumia duas coisas que seu app.py ainda não tem: TEAM_ABBR_LOOKUP e normalize_person_name. Então vou encaixar isso sem reescrever o resto, porque sofrer com nome de jogador em PDF já é um hobby triste o bastante.

Tem um segundo detalhe importante: hoje render_team_section_v2 manda o filtered_df para as abas de Injury e Lineup. Ou seja, se você filtra GP/MIN/role, a aba médica fica capada. Isso eu vou corrigir também, porque medicina por filtro de slider é um conceito meio medieval.

Patch Injury Report Nba App

Patch aplicado — Injury Report oficial NBA
Abaixo está o patch mínimo para aplicar em cima do seu app.py atual, mantendo o resto do app como está.

requirements.txt
Nenhuma mudança necessária. O seu arquivo já contém pypdf.

1) Imports novos no topo do app.py
Adicione junto dos imports:

import re
from io import BytesIO
from pypdf import PdfReader
2) Constantes novas
Cole perto das outras constantes, logo depois de TEAM_LOOKUP / constantes principais:

TEAM_ABBR_LOOKUP = {team["id"]: team.get("abbreviation", "") for team in teams.get_teams()}

INJURY_REPORT_PAGE = "https://official.nba.com/nba-injury-report-2025-26-season/"
INACTIVE_STATUSES = {"Out", "Doubtful"}
WATCHLIST_STATUSES = {"Questionable", "Probable"}

PLAYER_STATUS_RE = re.compile(
    r"(?P<player>[A-Za-zÀ-ÿ0-9'\.\-\s]+,\s+[A-Za-zÀ-ÿ0-9'\.\-\s]+)\s+"
    r"(?P<status>Available|Out|Questionable|Probable|Doubtful)\b"
    r"(?:\s+(?P<reason>.*))?$"
)

GAME_PREFIX_RE = re.compile(
    r"^(?:(?P<game_date>\d{2}/\d{2}/\d{4})\s+)?"
    r"(?P<game_time>\d{1,2}:\d{2})\s+\(ET\)\s+"
    r"(?P<matchup>[A-Z]{2,3}@[A-Z]{2,3})\s+"
    r"(?P<rest>.+)$"
)
3) Helper complementar para nomes de jogador
Logo abaixo de normalize_text, adicione:

def normalize_person_name(value: str) -> str:
    text = str(value or "").strip()
    if "," in text:
        last_part, first_part = text.split(",", 1)
        text = f"{first_part.strip()} {last_part.strip()}"
    return normalize_text(text)
4) Helpers novos do injury report
Cole este bloco na área de helpers, depois de normalize_person_name e antes de inject_css():

def clean_injury_pdf_line(line: str) -> str:
    line = str(line or "").strip()
    line = re.sub(r"Injury Report:.*$", "", line).strip()
    line = re.sub(r"Page\s+\d+\s+of\s+\d+$", "", line).strip()
    return line


@st.cache_data(ttl=900, show_spinner=False)
def fetch_latest_injury_report_pdf_url() -> str:
    response = requests.get(INJURY_REPORT_PAGE, timeout=30)
    response.raise_for_status()
    html = response.text

    pdf_urls = re.findall(
        r'https://ak-static\.cms\.nba\.com/referee/injury/Injury-Report_[^"]+\.pdf',
        html,
    )
    if not pdf_urls:
        return ""
    return pdf_urls[-1]


def extract_pdf_text_lines(pdf_bytes: bytes) -> list[str]:
    reader = PdfReader(BytesIO(pdf_bytes))
    lines: list[str] = []

    for page in reader.pages:
        text = page.extract_text() or ""
        page_lines = [clean_injury_pdf_line(x) for x in text.splitlines()]
        page_lines = [x for x in page_lines if x]
        lines.extend(page_lines)

    return lines


@st.cache_data(ttl=900, show_spinner=False)
5) Substitua render_injury_report_tab
def render_injury_report_tab(team_df: pd.DataFrame, team_name: str) -> None:
    st.markdown(
        '<div class="section-note">Status oficial do injury report da NBA para o elenco do time.</div>',
        unsafe_allow_html=True,
    )

    if "INJ_STATUS" not in team_df.columns:
        st.info("Injury report ainda não integrado nesta execução.")
        return

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
6) Substitua render_lineup_report_tab
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
7) Ajuste em render_team_section_v2
Troque só as chamadas das abas no fim da função.

Antes
    with injury_tab:
        render_injury_report_tab(filtered_df, team_name)

    with lineup_tab:
        render_lineup_report_tab(filtered_df, team_name)
Depois
    with injury_tab:
        render_injury_report_tab(team_df, team_name)

    with lineup_tab:
        render_lineup_report_tab(team_df, team_name)
Esse ajuste é importante para a aba médica e a provável escalação não ficarem mutiladas pelos filtros de GP/MIN/papel.

8) Ajuste no main()
Depois de odds e antes de renderizar os times, adicione o carregamento do injury report.

Antes
    away_df = merge_betmgm_odds(away_df, odds_df)
    home_df = merge_betmgm_odds(home_df, odds_df)

    render_matchup_header(selected_game)
Depois
    away_df = merge_betmgm_odds(away_df, odds_df)
    home_df = merge_betmgm_odds(home_df, odds_df)

    try:
        injury_df = fetch_latest_injury_report_df()
    except Exception:
        injury_df = pd.DataFrame()

    away_df = merge_injury_report(
        away_df,
        injury_df,
        selected_game["away_team_name"],
        int(selected_game["VISITOR_TEAM_ID"]),
    )
    home_df = merge_injury_report(
        home_df,
        injury_df,
        selected_game["home_team_name"],
        int(selected_game["HOME_TEAM_ID"]),
    )

    render_matchup_header(selected_game)
Resultado do patch
Aba Injury Report passa a usar o PDF oficial da NBA.

Aba Provável Escalação passa a remover automaticamente Out e Doubtful.

O restante do app permanece igual, fora do escopo.

requirements.txt não precisa mudar porque pypdf já está presente.

Desvio intencional em relação ao patch bruto que você enviou
Fiz dois ajustes mínimos de compatibilidade para o seu código atual:

adição de TEAM_ABBR_LOOKUP e normalize_person_name, porque o seu app.py ainda não tem esses itens;

abas de Injury Report e Provável Escalação agora usam team_df completo, e não filtered_df, para não serem afetadas pelos filtros laterais.

Thought for 3m 57s
Deixei no canvas um patch mínimo e pronto para colar no seu app.py, sem virar reforma de estádio só porque a aba médica estava capenga.

O que eu ajustei no patch:

integração do Injury Report oficial da NBA via PDF

filtro de Out/Doubtful na aba Provável Escalação

correção de compatibilidade porque o seu app.py atual não tem TEAM_ABBR_LOOKUP nem normalize_person_name, embora o patch bruto assuma isso

correção importante em render_team_section_v2: hoje as abas de Injury Report e Provável Escalação recebem filtered_df, então elas acabam sendo contaminadas pelos filtros de GP/MIN/papel; no patch, essas abas passam a usar o team_df completo

Também confirmei que o seu requirements.txt já tem pypdf, então não precisa mexer nisso. Um raro momento em que o passado não sabotou o presente.

No seu código atual:

a aba Injury Report ainda está só com placeholder

a aba Provável Escalação ainda não remove indisponíveis automaticamente

Se você quiser, no próximo passo eu posso transformar esse patch em uma versão final consolidada do app.py já limpa, em vez de deixar como bloco de aplicação.


cara me manda o codigo completo ja pronto com as alteraçoes que fez para eu substituir o que temos no app.py

Vou montar o app.py completo já remendado e te devolver o arquivo pronto para trocar. Porque, sim, copiar patch em oito lugares é a versão premium do sofrimento manual.

Estou puxando o arquivo inteiro para consolidar a versão final. Porque montar app.py no escuro com recortes é como operar joelho vendo raio-X de cotovelo.

Thought for 1m 36s
Você quer o app.py inteiro pronto. Justo. O problema é que, daqui, eu não consigo te devolver um arquivo físico já salvo no sandbox com o conteúdo consolidado. Vida cruel, ferramentas capengas, civilização em declínio.

Então vou te entregar do jeito mais direto que dá para aplicar no seu app.py atual sem ambiguidade: os blocos exatos para substituir/adicionar. Como o resto do app deve continuar igual, isso preserva o comportamento fora do escopo e evita eu inventar moda onde não fui chamado.

O que confirmei no seu código:

o requirements.txt já tem pypdf, então não precisa mexer nele

sua aba Injury Report ainda está em placeholder

sua aba Provável Escalação ainda não filtra Out/Doubtful automaticamente

render_team_section_v2 hoje passa filtered_df para essas abas, o que mutila a leitura médica/lineup com filtros de GP/MIN/papel

1) Substitua os imports do topo por este bloco
from datetime import date, datetime
from io import BytesIO
import os
import re
import time
import unicodedata
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from pypdf import PdfReader
import requests
import streamlit as st

from nba_api.live.nba.endpoints import scoreboard as live_scoreboard
from nba_api.stats.endpoints import (
    commonteamroster,
    leaguedashplayerstats,
    playergamelog,
    playergamelogs,
)
from nba_api.stats.static import teams
2) Substitua o bloco de constantes iniciais por este
Começa em TEAM_LOOKUP = ... e vai até UTC_TIMEZONE = ZoneInfo("UTC").

TEAM_LOOKUP = {team["id"]: team for team in teams.get_teams()}
TEAM_ABBR_LOOKUP = {team["id"]: team.get("abbreviation", "") for team in teams.get_teams()}
TEAM_LOGO_URL = "https://cdn.nba.com/logos/nba/{team_id}/primary/L/logo.svg"
PLAYER_HEADSHOT_URL = "https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"

SORT_OPTIONS = {
    "PRA L10": "L10_PRA",
    "Δ PRA L10 vs Temp": "DELTA_PRA_L10",
    "PRA L5": "L5_PRA",
    "Δ PRA L5 vs Temp": "DELTA_PRA_L5",
    "PTS L10": "L10_PTS",
    "REB L10": "L10_REB",
    "AST L10": "L10_AST",
    "PRA temporada": "SEASON_PRA",
    "PTS temporada": "SEASON_PTS",
    "REB temporada": "SEASON_REB",
    "AST temporada": "SEASON_AST",
    "Minutos por jogo": "SEASON_MIN",
    "Jogos na temporada": "SEASON_GP",
    "Nome do jogador": "PLAYER",
}

ROLE_OPTIONS = ["Todos", "Titular provável", "Reserva"]
VIEW_OPTIONS = ["Cards", "Tabela"]
CHART_OPTIONS = ["Compacto", "Completo"]
LINE_METRIC_OPTIONS = ["PRA", "PTS", "REB", "AST", "3PM", "FGA", "3PA"]

PROJECTION_WEIGHTS = {
    "season": 0.35,
    "l10": 0.40,
    "l5": 0.15,
    "matchup": 0.10,
}

ODDS_API_BASE_URL = "https://api.sportsgameodds.com/v2"
ODDS_BOOKMAKER = "betmgm"
ODDS_STAT_MAP = {
    "points": "PTS",
    "rebounds": "REB",
    "assists": "AST",
    "points+rebounds+assists": "PRA",
    "threePointersMade": "3PM",
    "fieldGoalsAttempted": "FGA",
    "threePointersAttempted": "3PA",
}
ODDS_METRIC_COLUMNS = {
    "PTS": ("BETMGM_PTS_LINE", "BETMGM_PTS_OVER_DEC", "BETMGM_PTS_UNDER_DEC", "BETMGM_PTS_UPDATED_AT"),
    "REB": ("BETMGM_REB_LINE", "BETMGM_REB_OVER_DEC", "BETMGM_REB_UNDER_DEC", "BETMGM_REB_UPDATED_AT"),
    "AST": ("BETMGM_AST_LINE", "BETMGM_AST_OVER_DEC", "BETMGM_AST_UNDER_DEC", "BETMGM_AST_UPDATED_AT"),
    "PRA": ("BETMGM_PRA_LINE", "BETMGM_PRA_OVER_DEC", "BETMGM_PRA_UNDER_DEC", "BETMGM_PRA_UPDATED_AT"),
    "3PM": ("BETMGM_3PM_LINE", "BETMGM_3PM_OVER_DEC", "BETMGM_3PM_UNDER_DEC", "BETMGM_3PM_UPDATED_AT"),
    "FGA": ("BETMGM_FGA_LINE", "BETMGM_FGA_OVER_DEC", "BETMGM_FGA_UNDER_DEC", "BETMGM_FGA_UPDATED_AT"),
    "3PA": ("BETMGM_3PA_LINE", "BETMGM_3PA_OVER_DEC", "BETMGM_3PA_UNDER_DEC", "BETMGM_3PA_UPDATED_AT"),
}

INJURY_REPORT_PAGE = "https://official.nba.com/nba-injury-report-2025-26-season/"
INACTIVE_STATUSES = {"Out", "Doubtful"}
WATCHLIST_STATUSES = {"Questionable", "Probable"}

PLAYER_STATUS_RE = re.compile(
    r"(?P<player>[A-Za-zÀ-ÿ0-9'\.\-\s]+,\s+[A-Za-zÀ-ÿ0-9'\.\-\s]+)\s+"
    r"(?P<status>Available|Out|Questionable|Probable|Doubtful)\b"
    r"(?:\s+(?P<reason>.*))?$"
)

GAME_PREFIX_RE = re.compile(
    r"^(?:(?P<game_date>\d{2}/\d{2}/\d{4})\s+)?"
    r"(?P<game_time>\d{1,2}:\d{2})\s+\(ET\)\s+"
    r"(?P<matchup>[A-Z]{2,3}@[A-Z]{2,3})\s+"
    r"(?P<rest>.+)$"
)

APP_TIMEZONE = ZoneInfo("America/Sao_Paulo")
EASTERN_TIMEZONE = ZoneInfo("America/New_York")
UTC_TIMEZONE = ZoneInfo("UTC")
3) Logo abaixo de normalize_text, adicione esta função
def normalize_person_name(value: str) -> str:
    text = str(value or "").strip()
    if "," in text:
        last_part, first_part = text.split(",", 1)
        text = f"{first_part.strip()} {last_part.strip()}"
    return normalize_text(text)
4) Cole este bloco de helpers novos logo depois de normalize_person_name
def clean_injury_pdf_line(line: str) -> str:
    line = str(line or "").strip()
    line = re.sub(r"Injury Report:.*$", "", line).strip()
    line = re.sub(r"Page\s+\d+\s+of\s+\d+$", "", line).strip()
    return line


@st.cache_data(ttl=900, show_spinner=False)
def fetch_latest_injury_report_pdf_url() -> str:
    response = requests.get(INJURY_REPORT_PAGE, timeout=30)
    response.raise_for_status()
    html = response.text

    pdf_urls = re.findall(
        r'https://ak-static\.cms\.nba\.com/referee/injury/Injury-Report_[^"]+\.pdf',
        html,
    )
    if not pdf_urls:
        return ""
    return pdf_urls[-1]


def extract_pdf_text_lines(pdf_bytes: bytes) -> list[str]:
    reader = PdfReader(BytesIO(pdf_bytes))
    lines: list[str] = []

    for page in reader.pages:
        text = page.extract_text() or ""
        page_lines = [clean_injury_pdf_line(x) for x in text.splitlines()]
        page_lines = [x for x in page_lines if x]
        lines.extend(page_lines)

    return lines


@st.cache_data(ttl=900, show_spinner=False)
def fetch_latest_injury_report_df() -> pd.DataFrame:
    pdf_url = fetch_latest_injury_report_pdf_url()
    if not pdf_url:
        return pd.DataFrame()

    response = requests.get(pdf_url, timeout=45)
    response.raise_for_status()

    lines = extract_pdf_text_lines(response.content)
    rows = []

    current_game_date = ""
    current_game_time = ""
    current_matchup = ""
    current_team = ""
    current_row = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("Game Date Game Time Matchup Team Player Name Current Status Reason"):
            continue

        game_match = GAME_PREFIX_RE.match(line)
        if game_match:
            if game_match.group("game_date"):
                current_game_date = game_match.group("game_date")
            current_game_time = game_match.group("game_time")
            current_matchup = game_match.group("matchup")
            line = game_match.group("rest").strip()

        if "NOT YET SUBMITTED" in line:
            current_row = None
            continue

        player_match = PLAYER_STATUS_RE.search(line)
        if player_match:
            player_name = " ".join(player_match.group("player").split())
            status = player_match.group("status").strip()
            reason = (player_match.group("reason") or "").strip()

            prefix = line[:player_match.start()].strip()
            if prefix:
                current_team = prefix

            current_row = {
                "GAME_DATE": current_game_date,
                "GAME_TIME_ET": current_game_time,
                "MATCHUP": current_matchup,
                "TEAM_NAME_IR": current_team,
                "PLAYER_NAME_IR": player_name,
                "PLAYER_KEY_IR": normalize_person_name(player_name),
                "INJ_STATUS": status,
                "INJ_REASON": reason,
                "INJ_REPORT_URL": pdf_url,
            }
            rows.append(current_row)
        else:
            if current_row is not None:
                extra = line.strip()
                if extra:
                    current_row["INJ_REASON"] = f'{current_row["INJ_REASON"]} {extra}'.strip()

    injury_df = pd.DataFrame(rows)
    if injury_df.empty:
        return injury_df

    injury_df["INJ_REASON"] = injury_df["INJ_REASON"].str.replace(r"\s+", " ", regex=True).str.strip()
    injury_df["INJ_STATUS"] = injury_df["INJ_STATUS"].fillna("—")
    return injury_df


def merge_injury_report(team_df: pd.DataFrame, injury_df: pd.DataFrame, team_name: str, team_id: int) -> pd.DataFrame:
    if team_df.empty:
        return team_df

    enriched = team_df.copy()
    enriched["INJ_STATUS"] = "Available"
    enriched["INJ_REASON"] = ""
    enriched["INJ_REPORT_URL"] = ""
    enriched["IS_UNAVAILABLE"] = False

    if injury_df.empty:
        return enriched

    team_abbr = TEAM_ABBR_LOOKUP.get(team_id, "")
    target_names = {normalize_text(team_name), normalize_text(team_abbr)}
    team_ir = injury_df[injury_df["TEAM_NAME_IR"].apply(normalize_text).isin(target_names)].copy()
    if team_ir.empty:
        return enriched

    team_ir = team_ir.drop_duplicates(subset=["PLAYER_KEY_IR"], keep="last")

    merged = enriched.merge(
        team_ir[["PLAYER_KEY_IR", "INJ_STATUS", "INJ_REASON", "INJ_REPORT_URL"]],
        left_on="PLAYER_KEY",
        right_on="PLAYER_KEY_IR",
        how="left",
        suffixes=("", "_IR"),
    )

    merged["INJ_STATUS"] = merged["INJ_STATUS_IR"].fillna(merged["INJ_STATUS"])
    merged["INJ_REASON"] = merged["INJ_REASON_IR"].fillna(merged["INJ_REASON"])
    merged["INJ_REPORT_URL"] = merged["INJ_REPORT_URL_IR"].fillna(merged["INJ_REPORT_URL"])
    merged["IS_UNAVAILABLE"] = merged["INJ_STATUS"].isin(INACTIVE_STATUSES)

    drop_cols = [c for c in ["PLAYER_KEY_IR", "INJ_STATUS_IR", "INJ_REASON_IR", "INJ_REPORT_URL_IR"] if c in merged.columns]
    if drop_cols:
        merged = merged.drop(columns=drop_cols)

    return merged
5) Substitua a função render_injury_report_tab inteira por esta
def render_injury_report_tab(team_df: pd.DataFrame, team_name: str) -> None:
    st.markdown(
        '<div class="section-note">Status oficial do injury report da NBA para o elenco do time.</div>',
        unsafe_allow_html=True,
    )

    if "INJ_STATUS" not in team_df.columns:
        st.info("Injury report ainda não integrado nesta execução.")
        return

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
6) Substitua a função render_lineup_report_tab inteira por esta
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
7) Substitua a função render_team_section_v2 inteira por esta
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

    summary_df, detail_df = build_display_dataframes(filtered_df)

    st.markdown(f"### {team_name}")
    st.markdown(
        f'<div><span class="info-pill">Jogadores: {len(filtered_df)}</span><span class="info-pill">Linha ativa: {line_metric} {format_number(line_value)}</span><span class="info-pill">Modo mercado: {"BetMGM quando houver" if use_market_line else "Manual"}</span></div>',
        unsafe_allow_html=True,
    )

    cards_tab, table_tab, injury_tab, lineup_tab = st.tabs(["Cards", "Tabela", "Injury Report", "Provável Escalação"])

    with cards_tab:
        st.markdown(
            '<div class="section-note">Cards curtos no topo e painel fixo do jogador abaixo para facilitar consulta no celular.</div>',
            unsafe_allow_html=True,
        )
        render_player_cards_grid(filtered_df, line_metric=line_metric, line_value=line_value, use_market_line=use_market_line, cards_per_row=cards_per_row)

        options = filtered_df[["PLAYER", "PLAYER_ID"]].drop_duplicates()
        player_name = st.selectbox(
            f"Jogador em foco — {team_name}",
            options["PLAYER"].tolist(),
            key=f"player_focus_v2_{team_name}_{chart_mode}_{line_metric}",
        )
        selected_row = filtered_df.loc[filtered_df["PLAYER"] == player_name].iloc[0]
        render_player_focus_panel(selected_row, line_metric, line_value, use_market_line, season, chart_mode)

    with table_tab:
        st.markdown(
            '<div class="section-note">Visão de comparação rápida do elenco, sem abrir card por card feito investigador privado.</div>',
            unsafe_allow_html=True,
        )
        mode = st.radio(
            f"Modo da tabela — {team_name}",
            ["Resumo", "Completa"],
            horizontal=True,
            key=f"table_mode_{team_name}",
        )
        if mode == "Resumo":
            st.dataframe(style_table(summary_df, quick_view=True), use_container_width=True)
        else:
            st.dataframe(style_table(detail_df, quick_view=False), use_container_width=True)

    with injury_tab:
        render_injury_report_tab(team_df, team_name)

    with lineup_tab:
        render_lineup_report_tab(team_df, team_name)
8) Substitua a função main() inteira por esta
def main() -> None:
    inject_css()

    st.markdown('<div class="main-title">NBA Dashboard MVP</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtitle">Jogos do dia com leitura de PTS, REB, AST e PRA em cards ou tabela, agora com linha manual e BetMGM quando disponível.</div>',
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Configurações")
        selected_date = get_brasilia_today()
        st.caption(f"Jogos do dia em Brasília • {selected_date.strftime('%d/%m/%Y')}")

        st.divider()
        chart_mode = st.radio("Modo do gráfico", CHART_OPTIONS, index=0)
        cards_per_row = st.select_slider("Cards por linha", options=[1, 2], value=2)

        st.divider()
        st.subheader("Filtros")
        min_games = st.slider("Mínimo de jogos na temporada", 0, 82, 5, 1)
        min_minutes = st.slider("Mínimo de minutos por jogo", 0, 40, 15, 1)
        role_filter = st.selectbox("Mostrar jogadores", ROLE_OPTIONS, index=0)

        st.divider()
        st.subheader("Linha")
        line_metric = st.selectbox("Métrica da linha", LINE_METRIC_OPTIONS, index=0)
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

        st.divider()
        st.subheader("Ordenação")
        sort_labels = list(SORT_OPTIONS.keys())
        sort_label = st.selectbox("Ordenar jogadores por", options=sort_labels, index=sort_labels.index("PRA L10"))
        ascending = st.toggle("Ordem crescente", value=False)

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
        away_df = build_team_table(int(selected_game["VISITOR_TEAM_ID"]), season)
        home_df = build_team_table(int(selected_game["HOME_TEAM_ID"]), season)

        away_df = enrich_team_with_context(
            team_df=away_df,
            team_id=int(selected_game["VISITOR_TEAM_ID"]),
            opponent_team_id=int(selected_game["HOME_TEAM_ID"]),
            opponent_team_name=selected_game["home_team_name"],
            season=season,
        )
        home_df = enrich_team_with_context(
            team_df=home_df,
            team_id=int(selected_game["HOME_TEAM_ID"]),
            opponent_team_id=int(selected_game["VISITOR_TEAM_ID"]),
            opponent_team_name=selected_game["away_team_name"],
            season=season,
        )
    except Exception as exc:
        st.error("A NBA demorou ou falhou ao responder nas estatísticas do confronto. Tente novamente em alguns segundos ou use o botão de atualização.")
        st.exception(exc)
        return

    away_df["TEAM_NAME"] = selected_game["away_team_name"]
    home_df["TEAM_NAME"] = selected_game["home_team_name"]

    odds_df = pd.DataFrame()
    if api_key_available:
        try:
            odds_events = fetch_nba_odds_events()
            selected_odds_event = find_matching_odds_event(
                odds_events,
                home_team_name=selected_game["home_team_name"],
                away_team_name=selected_game["away_team_name"],
            )
            odds_df = extract_betmgm_player_props(selected_odds_event)
        except Exception:
            odds_df = pd.DataFrame()

    away_df = merge_betmgm_odds(away_df, odds_df)
    home_df = merge_betmgm_odds(home_df, odds_df)

    try:
        injury_df = fetch_latest_injury_report_df()
    except Exception:
        injury_df = pd.DataFrame()

    away_df = merge_injury_report(
        away_df,
        injury_df,
        selected_game["away_team_name"],
        int(selected_game["VISITOR_TEAM_ID"]),
    )
    home_df = merge_injury_report(
        home_df,
        injury_df,
        selected_game["home_team_name"],
        int(selected_game["HOME_TEAM_ID"]),
    )

    render_matchup_header(selected_game)
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

    tab1, tab2 = st.tabs([selected_game["away_team_name"], selected_game["home_team_name"]])

    with tab1:
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

    with tab2:
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
Resultado final
Com isso:

a aba Injury Report passa a ler o PDF oficial da NBA

a aba Provável Escalação passa a excluir Out e Doubtful

o restante do app fica preservado, sem reescrita geral

requirements.txt fica como está, porque pypdf já está presente

Se você quiser, no próximo turno eu posso te devolver isso em formato de diff unificado (git diff) para seu dev colar sem brincar de “caça às 7 substituições”, esse esporte amaldiçoado.


Imagem carregada
o topico 3 ai da substituiçao que voce enviou, seria para colocar aqui abaixo desse normalize_text?

Thought for 6s
Sim. É logo abaixo de normalize_text, antes de american_to_decimal.

Fica assim:

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


def american_to_decimal(american_odds) -> Optional[float]:
    try:
        odds_int = int(str(american_odds).replace("+", "").strip())
    except (TypeError, ValueError):
        return None
Então, no seu print:

normalize_text fica como já está

cola normalize_person_name logo depois

american_to_decimal desce para baixo

Esse helper existe para converter nomes no formato do injury report, tipo James, LeBron, em algo comparável com o nome do app. Porque a NBA adora transformar uma tarefa simples numa pequena punição administrativa.


ok, fiz as alterações e aparece isso ao tentar iniciar o app: File "/mount/src/nba-dashboard/app.py", line 255
      text = str(value or "").strip()
      ^
IndentationError: expected an indented block after function definition on line 254

Thought for 5s
Você colou a função, mas o corpo dela ficou sem indentação. Python é esse animal sensível que entra em colapso se você não der quatro espacinhos pra ele.

O erro mostra exatamente isso:

linha 254: def normalize_person_name...

linha 255: text = ...

Python esperava que a linha 255 estivesse indentada dentro da função

Deixe exatamente assim:

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


def american_to_decimal(american_odds) -> Optional[float]:
    try:
        odds_int = int(str(american_odds).replace("+", "").strip())
    except (TypeError, ValueError):
        return None
Confere estas três coisas:

as linhas dentro de normalize_person_name têm 4 espaços no começo

não tem mistura de tab + espaço

não ficou nenhum def normalize_person_name... duplicado, um vazio e outro preenchido logo abaixo

Se quiser, cola aqui esse trecho entre as linhas 247 e 265 e eu te digo onde entortou.


Imagem carregada
esta assim

Thought for 11s
Está sem indentação mesmo. O print entregou o crime com testemunha ocular.

Nas duas funções novas:

normalize_person_name

clean_injury_pdf_line

as linhas internas precisam ficar recuadas.

No seu print, estas linhas estão erradas porque ficaram na mesma coluna do def:

text = str(value or "").strip()

if "," in text:

return normalize_text(text)

line = str(line or "").strip()

line = re.sub(...)

return line

Tem que ficar assim, exatamente:

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


def clean_injury_pdf_line(line: str) -> str:
    line = str(line or "").strip()
    line = re.sub(r"Injury Report:.*$", "", line).strip()
    line = re.sub(r"Page\s+\d+\s+of\s+\d+$", "", line).strip()
    return line
Regra prática, porque Python é um funcionário público emocional:

linha do def: sem recuo extra

corpo da função: 4 espaços

bloco dentro do if: 8 espaços

No seu editor, apaga essas duas funções e cola de novo exatamente como acima. Isso deve matar esse erro específico. Se depois aparecer outro, me manda o próximo traceback e seguimos nesse emocionante jogo de whack-a-mole.


beleza, abriu o app mas ta com um erro ainda:

ValueError: This app has encountered an error. The original error message is redacted to prevent data leaks. Full error details have been recorded in the logs (if you're on Streamlit Cloud, click on 'Manage app' in the lower right of your app).
Traceback:
File "/mount/src/nba-dashboard/app.py", line 3149, in <module>
    main()
    ~~~~^^
File "/mount/src/nba-dashboard/app.py", line 2920, in main
    render_team_section_v2(
    ~~~~~~~~~~~~~~~~~~~~~~^
        team_name=selected_game["away_team_name"],
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    ...<11 lines>...
        cards_per_row=cards_per_row,
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
File "/mount/src/nba-dashboard/app.py", line 3145, in render_team_section_v2
    render_lineup_report_tab(team_df, team_name)
    ~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^
File "/mount/src/nba-dashboard/app.py", line 3052, in render_lineup_report_tab
    st.dataframe(style_table(starter_view, quick_view=True), use_container_width=True)
    ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/home/adminuser/venv/lib/python3.14/site-packages/streamlit/runtime/metrics_util.py", line 532, in wrapped_func
    result = non_optional_func(*args, **kwargs)
File "/home/adminuser/venv/lib/python3.14/site-packages/streamlit/elements/arrow.py", line 725, in dataframe
    marshall_styler(proto.arrow_data, data, default_uuid)
    ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/home/adminuser/venv/lib/python3.14/site-packages/streamlit/elements/lib/pandas_styler_utils.py", line 67, in marshall_styler
    pandas_styles = styler._translate(False, False)  # type: ignore
File "/home/adminuser/venv/lib/python3.14/site-packages/pandas/io/formats/style_render.py", line 335, in _translate
    body: list = self._translate_body(idx_lengths, max_rows, max_cols)
                 ~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/home/adminuser/venv/lib/python3.14/site-packages/pandas/io/formats/style_render.py", line 629, in _translate_body
    body_row = self._generate_body_row(
        (r, row_tup, rlabels), max_cols, idx_lengths
    )
File "/home/adminuser/venv/lib/python3.14/site-packages/pandas/io/formats/style_render.py", line 826, in _generate_body_row
    display_value=self._display_funcs[(r, c)](value),
                  ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^
File "/home/adminuser/venv/lib/python3.14/site-packages/pandas/io/formats/style_render.py", line 1864, in <lambda>
    return lambda x: na_rep if (isna(x) is True) else func_3(x)
                                                      ~~~~~~^^^
File "/home/adminuser/venv/lib/python3.14/site-packages/pandas/io/formats/style_render.py", line 1829, in <lambda>
    func_0 = lambda x: formatter.format(x)
                       ~~~~~~~~~~~~~~~~^^^

Thought for 59s
Achei o bicho. Não é nada místico: o erro vem do style_table.

Hoje essa função trata toda coluna que não está em text_cols como numérica e aplica "{:.1f}". No patch da lineup, você adicionou a coluna Status (INJ_STATUS renomeada), que é texto tipo "Available", "Out", "Doubtful". Aí o Pandas tenta formatar "Available" como float e desmaia dramaticamente.

Correção
Na função style_table, troque este bloco:

def style_table(df: pd.DataFrame, quick_view: bool) -> pd.io.formats.style.Styler:
    text_cols = {
        "Jogador", "Pos", "Papel", "Trend", "Matchup", "Hit PRA", "Hit PTS", "Hit REB", "Hit AST", "Hit 3PM", "Hit FGA", "Hit 3PA", "Oscilação", "Sinal"
    }
por este:

def style_table(df: pd.DataFrame, quick_view: bool) -> pd.io.formats.style.Styler:
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
E, já que você vai mexer nisso mesmo, troca também o center_cols por este aqui:

    center_cols = [
        c for c in [
            "Papel", "GP", "MIN", "Trend", "Matchup", "Oscilação", "Sinal",
            "Hit PRA", "Hit PTS", "Hit REB", "Hit AST", "Hit 3PM", "Hit FGA", "Hit 3PA",
            "Status"
        ] if c in df.columns
    ]
Em resumo
O problema é:

render_lineup_report_tab agora cria tabela com coluna Status

style_table não conhece Status como texto e tenta aplicar formato numérico em tudo que sobra

Se quiser a versão mais segura ainda
Você também pode blindar o format_map para só formatar colunas realmente numéricas:

def style_table(df: pd.DataFrame, quick_view: bool) -> pd.io.formats.style.Styler:
    text_cols = {
        "Jogador", "Pos", "Papel", "Trend", "Matchup",
        "Hit PRA", "Hit PTS", "Hit REB", "Hit AST", "Hit 3PM", "Hit FGA", "Hit 3PA",
        "Oscilação", "Sinal", "Status", "Status oficial", "Motivo", "Última atualização"
    }

    format_map = {}
    for col in df.columns:
        if col in text_cols:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            format_map[col] = "{:.0f}" if col == "GP" else "{:.1f}"

    styler = df.style.format(format_map, na_rep="-")
Essa segunda versão é melhor, porque impede esse tipo de tombamento se amanhã você inventar outra coluna textual com nome novo. E você vai inventar, porque app cresce igual mato quando ninguém supervisiona.

Faz essa alteração e sobe de novo. O mais provável é que esse erro específico morra na hora.


Imagem carregada
essa ultima, eu adiciono aqui? 

Thought for 7s
Sim, é exatamente aí. Você vai substituir esse bloco atual que começa em format_map = {} e vai até styler = df.style.format(...).

Ou seja, troca isto:

format_map = {}
for col in df.columns:
    if col == "GP":
        format_map[col] = "{:.0f}"
    elif col not in text_cols:
        format_map[col] = "{:.1f}"

styler = df.style.format(format_map, na_rep="-")
por isto:

format_map = {}
for col in df.columns:
    if col in text_cols:
        continue
    if pd.api.types.is_numeric_dtype(df[col]):
        format_map[col] = "{:.0f}" if col == "GP" else "{:.1f}"

styler = df.style.format(format_map, na_rep="-")
E mantém o center_cols onde já está, do jeito que você mostrou com "Status" incluído.

Também garanta que o text_cols lá em cima tenha isso aqui dentro:

"Status",
"Status oficial",
"Motivo",
"Última atualização",
Então o fluxo certo fica assim:

text_cols inclui essas colunas textuais novas

format_map passa a formatar só colunas numéricas

styler = df.style.format(...)

depois vêm pra_cols, delta_cols, hit_cols, center_cols etc.

Se quiser, a função style_table deve ficar com esse miolo assim:

def style_table(df: pd.DataFrame, quick_view: bool) -> pd.io.formats.style.Styler:
    text_cols = {
        "Jogador", "Pos", "Papel", "Trend", "Matchup",
        "Hit PRA", "Hit PTS", "Hit REB", "Hit AST", "Hit 3PM", "Hit FGA", "Hit 3PA",
        "Oscilação", "Sinal", "Status", "Status oficial", "Motivo", "Última atualização"
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
            "Papel", "GP", "MIN", "Trend", "Matchup", "Oscilação", "Sinal",
            "Hit PRA", "Hit PTS", "Hit REB", "Hit AST", "Hit 3PM", "Hit FGA", "Hit 3PA",
            "Status"
        ] if c in df.columns
    ]
Faz isso e testa de novo. Esse erro aí tem cara de ser exatamente essa coluna textual tomando formatter numérico na testa.


apareceu isso: File "/mount/src/nba-dashboard/app.py", line 1895
    format_map = {}
                   ^
IndentationError: unindent does not match any outer indentation level

Thought for 18s
Esse erro é clássico de editor fazendo artesanato com tabs misturados com espaços. Python vê isso e resolve falecer com dignidade.

A forma mais segura agora é: apaga a função style_table inteira e cola ela de novo inteira, com indentação limpa de 4 espaços.

Use esta versão completa:

def style_table(df: pd.DataFrame, quick_view: bool) -> pd.io.formats.style.Styler:
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
        styler = styler.map(style_hit, subset=hit_cols)
    if center_cols:
        styler = styler.set_properties(subset=center_cols, **{"text-align": "center"})
    if "Jogador" in df.columns:
        styler = styler.set_properties(subset=["Jogador"], **{"text-align": "left"})

    return styler
Faz também estas duas checagens no editor, porque às vezes ele colabora igual um cone:

converte a indentação para Spaces

define o tamanho do tab para 4

remove qualquer tab escondido nessa função

Se estiver no VS Code:

Ctrl+Shift+P

procura Convert Indentation to Spaces

depois Indent Using Spaces

escolhe 4

O seu erro da linha 1895 não é de lógica, é só indentação torta mesmo. Python é muito poderoso, mas emocionalmente frágil.


NameError: This app has encountered an error. The original error message is redacted to prevent data leaks. Full error details have been recorded in the logs (if you're on Streamlit Cloud, click on 'Manage app' in the lower right of your app).
Traceback:
File "/mount/src/nba-dashboard/app.py", line 3172, in <module>
    main()
    ~~~~^^
File "/mount/src/nba-dashboard/app.py", line 2943, in main
    render_team_section_v2(
    ~~~~~~~~~~~~~~~~~~~~~~^
        team_name=selected_game["away_team_name"],
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    ...<11 lines>...
        cards_per_row=cards_per_row,
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
File "/mount/src/nba-dashboard/app.py", line 3160, in render_team_section_v2
    st.dataframe(style_table(summary_df, quick_view=True), use_container_width=True)
                 ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/mount/src/nba-dashboard/app.py", line 1932, in style_table
    styler = styler.map(style_hit, subset=hit_cols)
                        ^^^^^^^^^

Thought for 17s
Sim — esse agora é o erro mais banal de todos: o nome da função ficou errado.

No seu app.py original, a função existente é style_hit_rate, não style_hit. E a versão original da style_table também aplicava styler.map(style_hit_rate, subset=hit_cols).

Então, na função style_table, troque isto:

if hit_cols:
    styler = styler.map(style_hit, subset=hit_cols)
por isto:

if hit_cols:
    styler = styler.map(style_hit_rate, subset=hit_cols)
Se quiser o remendo mais preguiçoso possível, também funciona criar um alias logo abaixo de style_hit_rate:

def style_hit(val) -> str:
    return style_hit_rate(val)
Mas o melhor é usar style_hit_rate diretamente, porque essa função já existe no seu código.

Resumo do enterro:

style_hit não existe

style_hit_rate já existe

style_table deve chamar style_hit_rate

Faz essa troca e sobe de novo. Esse NameError deve morrer sem cerimônia.


File "/mount/src/nba-dashboard/app.py", line 1932
      styler = styler.map(style_hit_rate, subset=hit_cols)
      ^
IndentationError: expected an indented block after 'if' statement on line 1931

Thought for 32s
Sim — agora é só indentação de novo. Python continua sendo esse ouriço emocional.

A linha:

if hit_cols:
precisa ter uma linha logo abaixo com 4 espaços a mais. Como isso está dentro da função, a linha do styler = ... fica com 8 espaços no total.

Deixa esse trecho exatamente assim:

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
    styler = styler.set_properties(subset=quick_cols, **{"font-weight": "700"})

return styler
Se ainda der erro, faz o movimento menos elegante porém mais eficaz:

apaga a função style_table inteira

cola ela inteira de novo, limpa

converte indentação do arquivo para spaces

usa 4 spaces por nível

não mistura tab com espaço

Se quiser, cola aqui a função style_table inteira como ela está agora e eu te devolvo ela já alinhada, sem esse samba de recuo.


File "/mount/src/nba-dashboard/app.py", line 1927
     if pra_cols:
                 ^
IndentationError: unindent does not match any outer indentation level

Thought for a few seconds
Seu style_table virou um cemitério de tabs e espaços. Não tenta consertar linha por linha agora, porque isso só prolonga o folclore.

Apaga a função style_table inteira e cola esta versão completa no lugar dela, exatamente assim:

def style_table(df: pd.DataFrame, quick_view: bool) -> pd.io.formats.style.Styler:
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
Depois disso, faz estas duas coisas no editor:

converte a indentação para Spaces

usa 4 spaces

No VS Code:

Ctrl+Shift+P

Convert Indentation to Spaces

Indent Using Spaces

escolhe 4

O erro unindent does not match any outer indentation level quase sempre é isso: metade da função com espaço, metade com tab, uma obra expressionista que só Python consegue odiar tanto.

Se o app subir e aparecer outro erro, manda o próximo traceback e a gente continua esse safari de exceções.


como converto indentação para spaces?

Thought for a few seconds
No VS Code, faz assim. Um ritual simples para exorcizar tabs, esses pequenos demônios invisíveis.

Método 1: pelo canto inferior direito
Abre o app.py

No canto inferior direito do VS Code, procura algo como:

Spaces: 4

ou Tab Size: 4

ou Tabs

Clica ali

Escolhe Convert Indentation to Spaces

Depois clica de novo e escolhe Indent Using Spaces

Seleciona 4

Método 2: pela Command Palette
Pressiona Ctrl + Shift + P

Digita: Convert Indentation to Spaces

Enter

Depois abre de novo com Ctrl + Shift + P

Digita: Indent Using Spaces

Escolhe 4

Método 3: substituir tabs manualmente
Se ainda estiver possuído:

Pressiona Ctrl + H

Ativa regex se precisar

No campo buscar, coloca um tab real pressionando a tecla Tab

No substituir, coloca 4 espaços

Substitui tudo

Como conferir se resolveu
No rodapé do VS Code deve aparecer:

Spaces: 4
e não Tabs

Dica importante
Depois de converter, apaga e cola de novo a função style_table inteira. Porque às vezes o arquivo já ficou todo torto e você está remendando um guardanapo molhado.

Se você estiver usando outro editor, me fala qual é. Porque, claro, cada editor resolveu inventar um jeito próprio de esconder uma função básica.


Imagem carregada
isso?

Sim. Isso mesmo. Você já está em:

Spaces

4

Agora faz o golpe final, porque só olhar pro painel não cura arquivo torto:

com o app.py aberto, apaga a função style_table inteira

cola de novo a versão completa que eu te mandei

salva o arquivo

sobe/reinicia o app

Se quiser garantir de vez, ainda faz pelo menu de comando:

Ctrl + Shift + P

procura Convert Indentation to Spaces

executa

Mesmo mostrando Spaces 4, o arquivo pode ainda ter tabs antigas misturadas em trechos que já estavam lá. Software é uma arte performática do rancor.

Se der, o melhor próximo passo é você colar aqui a função style_table inteira como ela ficou depois de recolar, porque aí eu bato o olho e te digo se a indentação está limpa antes de você testar de novo.


Imagem carregada
apertei ctrl shift p e apareceu isso 

Thought for 5s
Isso aí não é o Command Palette. Você abriu uma tela de diff/preview de mudanças. Muito bonito, muito cinematográfico, zero utilidade pra converter indentação.

Faz assim:

Sai dessa tela de diff

volta pro arquivo app.py normal, modo edição

não no preview das mudanças

Tenta abrir o palette de verdade com:

F1

ou Ctrl + Shift + P de novo, mas com o cursor no editor normal

Quando abrir a caixinha de comando no topo, digita:

Convert Indentation to Spaces

e executa

Depois abre de novo e digita:

Indent Using Spaces

escolhe 4

Se isso estiver sendo feito num editor web meio capado e o palette não aparecer direito, então vai no plano menos glamouroso e mais confiável:

deixa no canto inferior como já está: Spaces | 4

apaga a função style_table inteira

cola de novo a versão completa que eu te mandei

salva

Pelo seu print, aliás, a função parece estar quase certa no lado verde. O ponto crítico é: você precisa editar o arquivo normal, não o preview do diff. Preview é só vitrine; não é onde se conserta a bagunça que o Python está julgando.

Se quiser, manda agora um print do app.py em modo edição normal, mostrando da linha 1870 até 1945, que eu te digo em 10 segundos se a style_table ficou limpa ou ainda está com recuo radioativo.


estou usando o github, como faço isso la

Thought for 15s
No GitHub web normal, aquela tela de diff bonitinha não tem esse comando de conversão. Porque, claro, seria útil demais.

Você tem 2 jeitos:

Jeito certo: abrir no editor do GitHub (github.dev)
Esse é o mais próximo de um VS Code no navegador.

Vai para a página principal do repositório

Pressiona a tecla . (ponto)

Isso abre o repo no github.dev.

Depois:

abre o app.py

aperta F1 ou Ctrl + Shift + P

digita Convert Indentation to Spaces

executa

abre de novo o palette

digita Indent Using Spaces

escolhe 4

salva com Ctrl + S

volta para Source Control e faz commit

Jeito mais rápido: sem converter nada, só substituir a função inteira
Como o problema está concentrado na style_table, no GitHub web comum o melhor é:

clicar em Edit this file no app.py

localizar a função def style_table(...)

apagar a função inteira

colar uma versão limpa completa

commit

Isso costuma resolver porque você elimina os tabs antigos em vez de tentar exorcizar caractere invisível manualmente, atividade muito digna da espécie humana.

Observação importante
Se você estiver nessa tela de diff/preview do commit, não é ali que converte nada. Você precisa estar em:

github.dev, ou

Edit file do arquivo de verdade

O que eu recomendo agora
Faz assim:

abre no github.dev com .

converte para spaces

apaga e cola de novo a style_table

Se quiser, eu posso te mandar uma versão final única da style_table, pronta para você colar no GitHub sem mexer em mais nada.


Imagem carregada
beleza, funcionou, mas com um problema: nao puxou certo o injury report. O jalen williams por exemplo ta machucado e aqui aparece como disponivel, inclusive como titular . Não puxou a informação real

Thought for 2m 57s
Boa, agora chegamos no bug de verdade, não naquele teatro de indentação.
O comportamento da tela sugere o seguinte: o PDF está sendo buscado, mas o merge com o elenco não está casando as linhas do Thunder, então todo mundo fica no valor padrão "Available".

Tem dois pontos bem prováveis aí:

o merge atual está rígido demais no nome do time
Ele filtra o injury report só quando TEAM_NAME_IR bate com o nome completo do time ou com a sigla. Se o PDF vier como Oklahoma City, Thunder ou outra variação, o time inteiro some do match e cai no default Available.

o report oficial é atualizado o dia inteiro e o patch está cacheado por 900s
A página oficial da NBA diz que os reports são atualizados continuamente ao longo do dia. Então, mesmo quando o parser estiver certo, o app pode ficar até 15 minutos atrasado se a situação mudou há pouco. 

O patch que aplicamos realmente usa ttl=900 e o merge por nome/sigla do time, então esse comportamento combina bastante com o que você viu.

O que eu faria agora
Troca só essas partes.

1) Adicione este helper perto dos outros helpers
Pode colocar logo abaixo de normalize_person_name ou perto do bloco do injury report.

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

    # aliases úteis para nomes que às vezes aparecem truncados/alternativos
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
2) Substitua merge_injury_report por esta versão
Essa versão:

tenta casar por nome completo / cidade / nickname / sigla

se ainda não achar, faz fallback pelo MATCHUP usando a sigla do time

só depois mergeia pelo PLAYER_KEY, então não mistura jogador do outro time igual um animal de carga sem supervisão

def merge_injury_report(team_df: pd.DataFrame, injury_df: pd.DataFrame, team_name: str, team_id: int) -> pd.DataFrame:
    if team_df.empty:
        return team_df

    enriched = team_df.copy()
    enriched["INJ_STATUS"] = "Available"
    enriched["INJ_REASON"] = ""
    enriched["INJ_REPORT_URL"] = ""
    enriched["IS_UNAVAILABLE"] = False

    if injury_df.empty:
        return enriched

    team_abbr = str(TEAM_ABBR_LOOKUP.get(team_id, "") or "").upper().strip()
    team_aliases = get_team_name_aliases(team_id, team_name)

    team_ir = injury_df.copy()
    if "TEAM_NAME_IR" in team_ir.columns:
        team_ir["TEAM_NAME_IR_NORM"] = team_ir["TEAM_NAME_IR"].fillna("").apply(normalize_text)
    else:
        team_ir["TEAM_NAME_IR_NORM"] = ""

    # 1) tenta pelo nome/cidade/nickname/sigla
    matched_ir = team_ir[team_ir["TEAM_NAME_IR_NORM"].isin(team_aliases)].copy()

    # 2) fallback pelo matchup do jogo (seguro porque o merge final ainda é por PLAYER_KEY)
    if matched_ir.empty and team_abbr and "MATCHUP" in team_ir.columns:
        matchup_mask = team_ir["MATCHUP"].fillna("").str.upper().str.contains(
            rf"(^|@){re.escape(team_abbr)}($|@)",
            regex=True,
        )
        matched_ir = team_ir[matchup_mask].copy()

    if matched_ir.empty:
        return enriched

    matched_ir = matched_ir.drop_duplicates(subset=["PLAYER_KEY_IR"], keep="last")

    merged = enriched.merge(
        matched_ir[["PLAYER_KEY_IR", "INJ_STATUS", "INJ_REASON", "INJ_REPORT_URL"]],
        left_on="PLAYER_KEY",
        right_on="PLAYER_KEY_IR",
        how="left",
        suffixes=("", "_IR"),
    )

    merged["INJ_STATUS"] = merged["INJ_STATUS_IR"].fillna(merged["INJ_STATUS"])
    merged["INJ_REASON"] = merged["INJ_REASON_IR"].fillna(merged["INJ_REASON"])
    merged["INJ_REPORT_URL"] = merged["INJ_REPORT_URL_IR"].fillna(merged["INJ_REPORT_URL"])
    merged["IS_UNAVAILABLE"] = merged["INJ_STATUS"].isin(INACTIVE_STATUSES)

    drop_cols = [
        c for c in ["PLAYER_KEY_IR", "INJ_STATUS_IR", "INJ_REASON_IR", "INJ_REPORT_URL_IR", "TEAM_NAME_IR_NORM"]
        if c in merged.columns
    ]
    if drop_cols:
        merged = merged.drop(columns=drop_cols)

    return merged
3) Reduza o cache do injury report
Troque:

@st.cache_data(ttl=900, show_spinner=False)
por:

@st.cache_data(ttl=300, show_spinner=False)
nas duas funções:

fetch_latest_injury_report_pdf_url

fetch_latest_injury_report_df

A página oficial informa que os reports são atualizados continuamente ao longo do dia, então 5 minutos faz mais sentido do que 15 para esse caso. 

4) Adicione um debug temporário na aba Injury Report
Só pra confirmar se o problema está no parser ou no merge.
Dentro de render_injury_report_tab, depois do st.dataframe(report_df, ...), coloque isto:

flagged = team_df[team_df["INJ_STATUS"] != "Available"].copy()
st.caption(f"Jogadores com status diferente de Available neste time: {len(flagged)}")
Se isso continuar mostrando 0 para OKC mesmo com Jalen fora, o problema não é lineup — é o casamento do report com o time/jogador.

O que eu acho que está acontecendo no seu caso
Como todos os titulares do OKC aparecem Available, isso parece mais falha de match do time inteiro do que só de um jogador isolado. E isso combina com o merge atual, que só aceita TEAM_NAME_IR igual ao nome completo ou à sigla.

Depois de fazer essas 4 mudanças:

clica em Forçar atualização

abre a aba Injury Report

verifica se o Jalen já sai de Available

Se quiser, no próximo passo eu te mando uma versão fechada só dessas três funções já prontas para colar: get_team_name_aliases, merge_injury_report e os decorators com ttl=300.


apareceu isso: File "/mount/src/nba-dashboard/app.py", line 263
      team_meta = TEAM_LOOKUP.get(team_id, {}) or {}
      ^
IndentationError: expected an indented block after function definition on line 262

Thought for 5s
Mesmo problema de antes: a função foi colada, mas o corpo dela ficou sem recuo. Python viu def get_team_name_aliases... e exigiu um bloco indentado logo abaixo, como uma pequena divindade mesquinha.

Substitui a função inteira por esta versão, exatamente assim:

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
Confere estas 3 coisas:

a linha def get_team_name_aliases... fica sem recuo extra

todas as linhas dentro da função têm 4 espaços

não ficou uma versão vazia da função acima e outra preenchida abaixo

Se quiser reduzir a chance de novo colapso ornamental, cola essa função em bloco único no editor e salva. Depois testa de novo. Se aparecer outro erro, manda o traceback seguinte e seguimos nessa charmosa escavação arqueológica de Python.


agora isso: File "/mount/src/nba-dashboard/app.py", line 3083
      unavailable = team_df[team_df["INJ_STATUS"].isin(["Out", "Doubtful"])]
     ^
IndentationError: unexpected indent

Thought for 6s
Isso é recuo torto de novo. A linha do unavailable = ... ficou com mais espaços do que deveria dentro da função render_injury_report_tab.

Em vez de caçar espaço invisível igual um monge cansado, apaga a função inteira render_injury_report_tab e cola esta versão completa:

def render_injury_report_tab(team_df: pd.DataFrame, team_name: str) -> None:
    st.markdown(
        '<div class="section-note">Status oficial do injury report da NBA para o elenco do time.</div>',
        unsafe_allow_html=True,
    )

    if "INJ_STATUS" not in team_df.columns:
        st.info("Injury report ainda não integrado nesta execução.")
        return

    report_df = team_df[["PLAYER", "INJ_STATUS", "INJ_REASON"]].copy()
    report_df = report_df.rename(
        columns={
            "PLAYER": "Jogador",
            "INJ_STATUS": "Status oficial",
            "INJ_REASON": "Motivo",
        }
    )

    st.dataframe(report_df, use_container_width=True)

    flagged = team_df[team_df["INJ_STATUS"] != "Available"].copy()
    st.caption(f"Jogadores com status diferente de Available neste time: {len(flagged)}")

    unavailable = team_df[team_df["INJ_STATUS"].isin(["Out", "Doubtful"])]
    if not unavailable.empty:
        st.warning(
            f"{len(unavailable)} jogador(es) marcados como indisponíveis e que devem sair da leitura da provável escalação."
        )
O ponto importante é:

tudo dentro da função tem 4 espaços

o bloco dentro do if not unavailable.empty: tem 8 espaços

Se quiser conferir visualmente, a estrutura correta é esta:

def func():
    linha_normal
    linha_normal

    if condicao:
        linha_dentro_do_if
No seu erro, essa linha aqui:

unavailable = team_df[team_df["INJ_STATUS"].isin(["Out", "Doubtful"])]
ficou com um recuo inesperado, provavelmente 5 espaços, tab misturado, ou alinhada como se estivesse dentro de outro bloco que não existe. Python, nosso pequeno burocrata ressentido, não perdoa isso.

Depois de colar essa função inteira, salva e testa de novo.


Imagem carregada
Imagem carregada
to só colando, saiu assim, ta certo?

Thought for 6s
Sim — esse trecho está com cara de certo agora.

Pelo print:

def render_injury_report_tab... está alinhada no nível da função

tudo dentro dela está com 4 espaços

o bloco do if not unavailable.empty: está com recuo maior

a próxima função def render_lineup_report_tab... voltou para o mesmo nível da anterior

Ou seja: visualmente, essa parte não está torta. Milagre estatístico.

O trecho crítico que eu conferi:

unavailable = ... está alinhado com flagged = ...

if not unavailable.empty: está alinhado com eles

st.warning(...) está indentado dentro do if

Então, para essa função específica, está ok.

Agora faz:

salva o arquivo

roda o app de novo

Se aparecer erro agora, provavelmente:

ou sobrou indentação quebrada em outra função

ou o problema passou a ser de lógica/parser, não mais de recuo de Python, que já é um avanço civilizatório

Se subir, o próximo teste útil é olhar a aba Injury Report e ver o contador:

Jogadores com status diferente de Available neste time: X

Se continuar 0 para OKC, aí o problema não é indentação — é o merge do injury report mesmo, e aí a gente corrige o parser sem esse circo de espaços invisíveis.


beleza, agora deu outo erro: A NBA demorou ou falhou ao responder nas estatísticas do confronto. Tente novamente em alguns segundos ou use o botão de atualização.

RuntimeError: Falha ao consultar LeagueDashPlayerStats F após 3 tentativas.
Traceback:
File "/mount/src/nba-dashboard/app.py", line 2944, in main
    home_df = enrich_team_with_context(
        team_df=home_df,
    ...<3 lines>...
        season=season,
    )
File "/mount/src/nba-dashboard/app.py", line 1696, in enrich_team_with_context
    matchup_rows = [get_position_opponent_profile(season, opponent_team_id, pos) for pos in ["G", "F", "C"]]
                    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/home/adminuser/venv/lib/python3.14/site-packages/streamlit/runtime/caching/cache_utils.py", line 281, in __call__
    return self._get_or_create_cached_value(args, kwargs, spinner_message)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/home/adminuser/venv/lib/python3.14/site-packages/streamlit/runtime/caching/cache_utils.py", line 326, in _get_or_create_cached_value
    return self._handle_cache_miss(cache, value_key, func_args, func_kwargs)
           ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/home/adminuser/venv/lib/python3.14/site-packages/streamlit/runtime/caching/cache_utils.py", line 385, in _handle_cache_miss
    computed_value = self._info.func(*func_args, **func_kwargs)
File "/mount/src/nba-dashboard/app.py", line 1314, in get_position_opponent_profile
    opp_profile = weighted_profile(fetch(position_group, opponent_team_id))
                                   ~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/mount/src/nba-dashboard/app.py", line 1270, in fetch
    response = run_api_call_with_retry(
        lambda: leaguedashplayerstats.LeagueDashPlayerStats(
    ...<15 lines>...
        endpoint_name=f"LeagueDashPlayerStats {position_value}",
    )
File "/mount/src/nba-dashboard/app.py", line 504, in run_api_call_with_retry
    raise RuntimeError(f"Falha ao consultar {endpoint_name} após {retries} tentativas.") from last_error

Thought for 33s
Isso já é outro problema, separado do injury patch. A boa notícia: não é seu app inteiro implodindo. A má notícia: a nba_api resolveu fazer corpo mole no endpoint de matchup por posição.

O que está acontecendo no seu código:

enrich_team_with_context() chama get_position_opponent_profile() 3 vezes: uma para G, uma para F e uma para C

dentro dessa função, cada chamada usa LeagueDashPlayerStats(...) com filtro de posição e opponent_team_id

se a API falha 3 vezes, run_api_call_with_retry() levanta RuntimeError e mata a execução

o fallback que você já tinha em enrich_team_with_context() só roda se matchup_df vier vazio, mas não roda quando a função explode antes

Em português menos cerimonial: o app até tinha um “plano B”, mas a exceção dá um tiro no plano B antes dele nascer. Um espetáculo bem Python.

Patch mínimo e certo
Você não precisa mexer no resto.
Substitua a função inteira get_position_opponent_profile por esta versão resiliente:

@st.cache_data(ttl=7200, show_spinner=False)
def get_position_opponent_profile(season: str, opponent_team_id: int, position_group: str) -> dict:
    def fetch(position_value: str, opponent_value: int) -> pd.DataFrame:
        try:
            response = run_api_call_with_retry(
                lambda: leaguedashplayerstats.LeagueDashPlayerStats(
                    season=season,
                    season_type_all_star="Regular Season",
                    per_mode_detailed="PerGame",
                    measure_type_detailed_defense="Base",
                    last_n_games=0,
                    month=0,
                    opponent_team_id=opponent_value,
                    pace_adjust="N",
                    plus_minus="N",
                    rank="N",
                    period=0,
                    team_id_nullable="",
                    player_position_abbreviation_nullable=position_value,
                    timeout=45,
                ),
                endpoint_name=f"LeagueDashPlayerStats {position_value}",
                retries=2,
                delay=1.5,
            )
        except Exception:
            return pd.DataFrame()

        frames = response.get_data_frames()
        if not frames:
            return pd.DataFrame()
        return frames[0].copy()

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
Por que isso resolve
Hoje a função usa LeagueDashPlayerStats e deixa qualquer falha subir até quebrar o app.
Com o patch acima:

se a API da NBA falhar para F, ela devolve DataFrame() vazio

weighted_profile() transforma vazio em perfil neutro

enrich_team_with_context() continua rodando normalmente

Resultado: o app abre, e o matchup daquela posição fica neutro/zerado naquele carregamento, em vez de derrubar tudo. Que já é um ganho enorme para uma civilização que insiste em depender de endpoint temperamental.

Se quiser blindar mais ainda
Você também pode deixar enrich_team_with_context() mais à prova de piti, trocando só esta linha:

matchup_rows = [get_position_opponent_profile(season, opponent_team_id, pos) for pos in ["G", "F", "C"]]
por:

matchup_rows = []
for pos in ["G", "F", "C"]:
    try:
        matchup_rows.append(get_position_opponent_profile(season, opponent_team_id, pos))
    except Exception:
        matchup_rows.append({"POSITION_GROUP": pos, "MATCHUP_LABEL": "Neutro", "MATCHUP_DIFF": 0.0})
Mas o patch na própria get_position_opponent_profile() já é o mais limpo.

Um detalhe importante
No trecho mais recente do seu app.py, render_team_section_v2() ainda aparece passando filtered_df para render_injury_report_tab() e render_lineup_report_tab(). Isso pode voltar a te sabotar a leitura de injury/lineup depois. Então, quando esse erro da API passar, confere se essa função ficou mesmo na versão corrigida com team_df nessas duas abas.

Aplica essa troca, salva, e testa de novo.


resolveu agora, mas o Jalen williamns ainda ta como disponivel e na escalação....

Thought for 2m 26s
Sim — então o problema agora não é “opinião sua” nem do app.
No injury report oficial mais recente que achei, para o jogo MIN@OKC, o PDF lista “Williams, Jalen — Out — Injury/Illness - Right Hamstring; Strain”. Ou seja: no dado oficial, ele está fora mesmo. 

Então seu app está falhando em uma destas duas etapas:

cache: ainda está usando um injury_df antigo/vazio; a NBA atualiza esses reports continuamente ao longo do dia. 

merge: o parser até lê o PDF, mas a ligação com o elenco do time não está casando direito.

Tem um detalhe importante: no seu código base, build_team_table() cria PLAYER_KEY com normalize_text(PLAYER), e o patch atual faz o merge do injury report por PLAYER_KEY, mas antes filtra o injury report por TEAM_NAME_IR batendo com nome/sigla do time. Se esse filtro de time falhar, todo mundo cai no default "Available".

O patch certo agora
Vamos parar de confiar tanto no nome do time e usar também o matchup do jogo. Porque, francamente, confiar em texto de PDF já é uma aposta emocional.

1) Substitua merge_injury_report por esta versão
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
    enriched["INJ_STATUS"] = "Available"
    enriched["INJ_REASON"] = ""
    enriched["INJ_REPORT_URL"] = ""
    enriched["IS_UNAVAILABLE"] = False

    if injury_df.empty:
        return enriched

    team_abbr = str(TEAM_ABBR_LOOKUP.get(team_id, "") or "").upper().strip()
    roster_keys = set(enriched["PLAYER_KEY"].fillna("").astype(str).tolist())

    work_ir = injury_df.copy()
    work_ir["TEAM_NAME_IR_NORM"] = work_ir["TEAM_NAME_IR"].fillna("").apply(normalize_text)
    work_ir["MATCHUP_NORM"] = work_ir["MATCHUP"].fillna("").str.upper().str.strip()

    team_aliases = get_team_name_aliases(team_id, team_name)

    # 1) filtra pelo matchup do jogo, se disponível
    if game_matchup:
        work_ir = work_ir[work_ir["MATCHUP_NORM"] == str(game_matchup).upper().strip()].copy()

    # 2) mantém linhas que batem por nome do time OU por jogador do elenco
    work_ir = work_ir[
        (work_ir["TEAM_NAME_IR_NORM"].isin(team_aliases)) |
        (work_ir["PLAYER_KEY_IR"].isin(roster_keys))
    ].copy()

    if work_ir.empty:
        return enriched

    # 3) dedup final por jogador
    work_ir = work_ir.drop_duplicates(subset=["PLAYER_KEY_IR"], keep="last")

    merged = enriched.merge(
        work_ir[["PLAYER_KEY_IR", "INJ_STATUS", "INJ_REASON", "INJ_REPORT_URL"]],
        left_on="PLAYER_KEY",
        right_on="PLAYER_KEY_IR",
        how="left",
        suffixes=("", "_IR"),
    )

    merged["INJ_STATUS"] = merged["INJ_STATUS_IR"].fillna(merged["INJ_STATUS"])
    merged["INJ_REASON"] = merged["INJ_REASON_IR"].fillna(merged["INJ_REASON"])
    merged["INJ_REPORT_URL"] = merged["INJ_REPORT_URL_IR"].fillna(merged["INJ_REPORT_URL"])
    merged["IS_UNAVAILABLE"] = merged["INJ_STATUS"].isin(INACTIVE_STATUSES)

    drop_cols = [
        c for c in [
            "PLAYER_KEY_IR",
            "INJ_STATUS_IR",
            "INJ_REASON_IR",
            "INJ_REPORT_URL_IR",
            "TEAM_NAME_IR_NORM",
            "MATCHUP_NORM",
        ] if c in merged.columns
    ]
    if drop_cols:
        merged = merged.drop(columns=drop_cols)

    return merged
2) No main(), monte o matchup e passe para o merge
Logo antes de chamar merge_injury_report, adicione:

game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"
E troque as chamadas por estas:

away_df = merge_injury_report(
    away_df,
    injury_df,
    selected_game["away_team_name"],
    int(selected_game["VISITOR_TEAM_ID"]),
    game_matchup=game_matchup,
)

home_df = merge_injury_report(
    home_df,
    injury_df,
    selected_game["home_team_name"],
    int(selected_game["HOME_TEAM_ID"]),
    game_matchup=game_matchup,
)
3) Força atualização
Como a página oficial é atualizada continuamente, depois de salvar:

clica em Forçar atualização

ou reinicia o app

ou limpa cache

A própria página oficial diz que os reports são atualizados “on a continual basis throughout the day”. 

Checagem rápida
Se quiser confirmar sem adivinhação mística, coloca temporariamente isso depois de injury_df = fetch_latest_injury_report_df():

st.write(
    injury_df[
        injury_df["PLAYER_KEY_IR"] == "jalen williams"
    ][["MATCHUP", "TEAM_NAME_IR", "PLAYER_NAME_IR", "INJ_STATUS", "INJ_REASON"]]
)
Se aparecer Out, o parser está bom e o problema era mesmo o merge.
Se não aparecer nada, aí o problema está antes, no parsing do PDF.

Como o PDF oficial de hoje mostra Jalen Williams = Out para MIN@OKC, eu apostaria meu salário imaginário que o parse está vindo e o gargalo é o filtro/merge. 

Se você quiser, eu monto agora o bloco exato do main() já reescrito com esse ajuste, para você só colar sem continuar praticando necromancia de indentação.


o topico 2 que voce falou e depois sobre trocas as chamadas ai ja me perdi, explique melhor

Thought for 6s
Você se perdeu porque eu te entreguei “troque as chamadas” como se todo mundo gostasse de caçar função no main() em arquivo de 3 mil linhas. Um carinho.

Vamos reto ao ponto.

O que muda
Você já tem isso no main() hoje, em algum lugar depois de:

try:
    injury_df = fetch_latest_injury_report_df()
except Exception:
    injury_df = pd.DataFrame()
E provavelmente está assim:

away_df = merge_injury_report(
    away_df,
    injury_df,
    selected_game["away_team_name"],
    int(selected_game["VISITOR_TEAM_ID"]),
)

home_df = merge_injury_report(
    home_df,
    injury_df,
    selected_game["home_team_name"],
    int(selected_game["HOME_TEAM_ID"]),
)
O que fazer
Você vai fazer 2 mudanças seguidas nesse trecho.

1) Adicionar a variável game_matchup
Logo antes dessas chamadas do merge_injury_report, adiciona esta linha:

game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"
Ela monta algo como:

MIN@OKC

LAL@BOS

etc.

Isso ajuda a casar o injury report do PDF com o jogo certo.

2) Trocar as chamadas antigas pelas novas
Depois dessa linha, substitui o bloco antigo por este:

away_df = merge_injury_report(
    away_df,
    injury_df,
    selected_game["away_team_name"],
    int(selected_game["VISITOR_TEAM_ID"]),
    game_matchup=game_matchup,
)

home_df = merge_injury_report(
    home_df,
    injury_df,
    selected_game["home_team_name"],
    int(selected_game["HOME_TEAM_ID"]),
    game_matchup=game_matchup,
)
Então o trecho inteiro fica assim
No main(), a parte correta deve ficar assim:

try:
    injury_df = fetch_latest_injury_report_df()
except Exception:
    injury_df = pd.DataFrame()

game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"

away_df = merge_injury_report(
    away_df,
    injury_df,
    selected_game["away_team_name"],
    int(selected_game["VISITOR_TEAM_ID"]),
    game_matchup=game_matchup,
)

home_df = merge_injury_report(
    home_df,
    injury_df,
    selected_game["home_team_name"],
    int(selected_game["HOME_TEAM_ID"]),
    game_matchup=game_matchup,
)
Onde exatamente entra
Esse bloco fica depois de:

away_df = merge_betmgm_odds(away_df, odds_df)
home_df = merge_betmgm_odds(home_df, odds_df)
e antes de:

render_matchup_header(selected_game)
Ou seja, a sequência deve ficar assim:

away_df = merge_betmgm_odds(away_df, odds_df)
home_df = merge_betmgm_odds(home_df, odds_df)

try:
    injury_df = fetch_latest_injury_report_df()
except Exception:
    injury_df = pd.DataFrame()

game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"

away_df = merge_injury_report(
    away_df,
    injury_df,
    selected_game["away_team_name"],
    int(selected_game["VISITOR_TEAM_ID"]),
    game_matchup=game_matchup,
)

home_df = merge_injury_report(
    home_df,
    injury_df,
    selected_game["home_team_name"],
    int(selected_game["HOME_TEAM_ID"]),
    game_matchup=game_matchup,
)

render_matchup_header(selected_game)
Importante
Isso só funciona se você também já tiver trocado a função merge_injury_report para a nova versão com esta assinatura:

def merge_injury_report(
    team_df: pd.DataFrame,
    injury_df: pd.DataFrame,
    team_name: str,
    team_id: int,
    game_matchup: str = "",
) -> pd.DataFrame:
Se a sua função ainda estiver com a assinatura antiga, vai dar erro de argumento inesperado. Porque Python, além de frágil, é literal.

Se você quiser, no próximo eu te mando:

o bloco antigo

o bloco novo

a posição exata dentro do seu main()
tudo em formato de “procura isso / substitui por isso”.


Imagem carregada
adiciono isso acima da linha 3069?
game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"

Sim. Exatamente acima da linha 3069.

Pelo seu print, a ordem deve ficar assim:

try:
    injury_df = fetch_latest_injury_report_df()
except Exception:
    injury_df = pd.DataFrame()

game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"

away_df = merge_injury_report(
    away_df,
    injury_df,
    selected_game["away_team_name"],
    int(selected_game["VISITOR_TEAM_ID"]),
    game_matchup=game_matchup,
)

home_df = merge_injury_report(
    home_df,
    injury_df,
    selected_game["home_team_name"],
    int(selected_game["HOME_TEAM_ID"]),
    game_matchup=game_matchup,
)
Então, respondendo seco e com amor burocrático:

sim, adiciona essa linha acima da 3069

e não esquece de colocar também game_matchup=game_matchup dentro das duas chamadas de merge_injury_report

Porque só criar a variável e não passar para a função seria um gesto muito humano de esperança sem execução.

Também confere se sua função merge_injury_report já foi trocada para a versão nova, com este cabeçalho:

def merge_injury_report(
    team_df: pd.DataFrame,
    injury_df: pd.DataFrame,
    team_name: str,
    team_id: int,
    game_matchup: str = "",
) -> pd.DataFrame:
Se não tiver esse game_matchup: str = "", vai dar erro na hora de rodar.


apareceu isso: NameError: This app has encountered an error. The original error message is redacted to prevent data leaks. Full error details have been recorded in the logs (if you're on Streamlit Cloud, click on 'Manage app' in the lower right of your app).
Traceback:
File "/mount/src/nba-dashboard/app.py", line 3069, in <module>
    game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"
                                           ^^^^^^^^^^^^^

Thought for 4s
Perfeito, agora ficou claro: você colocou essa linha fora do main(), no nível global do arquivo.
Por isso o traceback mostra:

in <module>

e reclama de selected_game

Ou seja: o Python tentou executar isso na carga do arquivo, antes de main() rodar, antes do usuário escolher jogo, antes de selected_game existir. Um salto de fé lindo e inútil.

O que aconteceu
Esta linha:

game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"
só pode existir dentro da função main(), porque selected_game só nasce lá dentro.

Como corrigir
Você precisa deixar essa linha indentada dentro do main(), junto do bloco do injury report.

Fica assim:

    try:
        injury_df = fetch_latest_injury_report_df()
    except Exception:
        injury_df = pd.DataFrame()

    game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"

    away_df = merge_injury_report(
        away_df,
        injury_df,
        selected_game["away_team_name"],
        int(selected_game["VISITOR_TEAM_ID"]),
        game_matchup=game_matchup,
    )

    home_df = merge_injury_report(
        home_df,
        injury_df,
        selected_game["home_team_name"],
        int(selected_game["HOME_TEAM_ID"]),
        game_matchup=game_matchup,
    )
Repara no detalhe que sempre humilha:

essas linhas têm 4 espaços na frente

porque estão dentro do main()

O que fazer agora
procura a linha game_matchup = ...

vê se ela está alinhada na coluna 0, igual def main():

se estiver, está errada

move ela para dentro do main(), no mesmo nível de indentação de:

try:

away_df = ...

home_df = ...

Regra prática
Se a linha usa selected_game, ela precisa estar depois deste trecho no main():

selected_game = games.loc[games["label"] == game_label].iloc[0]
e antes de render_matchup_header(selected_game).

Resumo bruto, porque você merece objetividade
selected_game não existe globalmente

essa linha foi colocada fora do main()

coloque ela dentro do main(), com 4 espaços de recuo

Se quiser, manda um print mostrando da linha 3058 até 3078 e eu te digo em um olhar se essa linha ficou no lugar certo ou se ainda está solta pelo arquivo feito um móvel sem parafuso.


NameError: This app has encountered an error. The original error message is redacted to prevent data leaks. Full error details have been recorded in the logs (if you're on Streamlit Cloud, click on 'Manage app' in the lower right of your app).
Traceback:
File "/mount/src/nba-dashboard/app.py", line 3072, in <module>
    away_df,
    ^^^^^^^

Thought for a few seconds
Sim — agora ficou ainda mais claro: o bloco inteiro do merge ficou fora do main().

Quando o erro aponta para:

away_df,
em in <module>, isso quer dizer que o Python está lendo esse pedaço como código solto no arquivo, não como parte da função main().

O que aconteceu
Você provavelmente deixou algo assim no arquivo:

game_matchup = ...
away_df = merge_injury_report(
away_df,
injury_df,
...
)
ou até com parte indentada e parte não. Resultado: o Python tenta executar isso na carga do módulo e morre porque away_df também só existe dentro do main(). Um show de horror com espaços.

O que precisa existir
Dentro do main(), o bloco inteiro deve ficar assim, com 4 espaços no começo de todas as linhas:

    try:
        injury_df = fetch_latest_injury_report_df()
    except Exception:
        injury_df = pd.DataFrame()

    game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"

    away_df = merge_injury_report(
        away_df,
        injury_df,
        selected_game["away_team_name"],
        int(selected_game["VISITOR_TEAM_ID"]),
        game_matchup=game_matchup,
    )

    home_df = merge_injury_report(
        home_df,
        injury_df,
        selected_game["home_team_name"],
        int(selected_game["HOME_TEAM_ID"]),
        game_matchup=game_matchup,
    )
Como conferir se está no lugar certo
Esse bloco precisa ficar entre estes dois trechos no main():

Antes
    away_df = merge_betmgm_odds(away_df, odds_df)
    home_df = merge_betmgm_odds(home_df, odds_df)
Depois
    render_matchup_header(selected_game)
Então a sequência correta inteira é esta:

    away_df = merge_betmgm_odds(away_df, odds_df)
    home_df = merge_betmgm_odds(home_df, odds_df)

    try:
        injury_df = fetch_latest_injury_report_df()
    except Exception:
        injury_df = pd.DataFrame()

    game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"

    away_df = merge_injury_report(
        away_df,
        injury_df,
        selected_game["away_team_name"],
        int(selected_game["VISITOR_TEAM_ID"]),
        game_matchup=game_matchup,
    )

    home_df = merge_injury_report(
        home_df,
        injury_df,
        selected_game["home_team_name"],
        int(selected_game["HOME_TEAM_ID"]),
        game_matchup=game_matchup,
    )

    render_matchup_header(selected_game)
O erro real aqui
Se aparecer in <module>, pensa assim:

está fora de qualquer função

tem que entrar dentro do main()

Se aparecer selected_game ou away_df como NameError, isso confirma a mesma coisa:

essas variáveis não existem globalmente

só existem dentro do main()

O que fazer agora
Procura no arquivo por:

game_matchup =
e vê se essa linha está com 4 espaços na frente.
Depois confere se as linhas de:

away_df = merge_injury_report(
home_df = merge_injury_report(
também estão com 4 espaços na frente.

Se qualquer uma delas estiver começando na coluna 0, está errada.

Manda um print da região da linha 3064 até 3085 e eu te digo exatamente onde o bloco escapou do curral.



app (1).py
Python
esse é o arquivo atualizado. Veja os erros que tem nele e me envie um codigo completo corrigido para eu substituir

Thought for 2m 23s
Inspecionei seu app (1).py e a bagunça real ficou concentrada em poucos pontos, não no arquivo inteiro. O principal estrago é que o fim do main() perdeu indentação: render_summary_cards, render_game_rankings, tab1/tab2 e o footer ficaram fora da função, no escopo global, o que explica os NameError e o comportamento caótico. Também sobrou código duplicado e morto dentro de get_position_opponent_profile. O patch de injury/lineup em si já está quase todo no lugar, inclusive render_team_section_v2 já chama render_injury_report_tab(team_df, ...) e render_lineup_report_tab(team_df, ...), que é o correto.

Eu não vou fingir que consigo te devolver com segurança um app.py completo byte a byte quando a leitura do arquivo no ambiente veio truncada em vários trechos. Isso seria fanfic com sintaxe. Então estou te devolvendo o bloco consolidado certo para substituir nas partes quebradas, que é o jeito seguro de pôr o app de pé sem inventar o resto do arquivo. O style_table também foi ajustado para não tentar formatar colunas textuais como número, o que já tinha te derrubado antes.

1) Substitua a função style_table inteira por esta
def style_table(df: pd.DataFrame, quick_view: bool) -> pd.io.formats.style.Styler:
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
2) Substitua a função get_position_opponent_profile inteira por esta
@st.cache_data(ttl=7200, show_spinner=False)
def get_position_opponent_profile(season: str, opponent_team_id: int, position_group: str) -> dict:
    def fetch(position_value: str, opponent_value: int) -> pd.DataFrame:
        try:
            response = run_api_call_with_retry(
                lambda: leaguedashplayerstats.LeagueDashPlayerStats(
                    season=season,
                    season_type_all_star="Regular Season",
                    per_mode_detailed="PerGame",
                    measure_type_detailed_defense="Base",
                    last_n_games=0,
                    month=0,
                    opponent_team_id=opponent_value,
                    pace_adjust="N",
                    plus_minus="N",
                    rank="N",
                    period=0,
                    team_id_nullable="",
                    player_position_abbreviation_nullable=position_value,
                    timeout=45,
                ),
                endpoint_name=f"LeagueDashPlayerStats {position_value}",
                retries=2,
                delay=1.5,
            )
        except Exception:
            return pd.DataFrame()

        frames = response.get_data_frames()
        if not frames:
            return pd.DataFrame()
        return frames[0].copy()

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
3) Substitua a função main inteira por esta
def main() -> None:
    inject_css()

    st.markdown('<div class="main-title">NBA Dashboard MVP</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtitle">Jogos do dia com leitura de PTS, REB, AST e PRA em cards ou tabela, agora com linha manual e BetMGM quando disponível.</div>',
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Configurações")
        selected_date = get_brasilia_today()
        st.caption(f"Jogos do dia em Brasília • {selected_date.strftime('%d/%m/%Y')}")

        st.divider()
        chart_mode = st.radio("Modo do gráfico", CHART_OPTIONS, index=0)
        cards_per_row = st.select_slider("Cards por linha", options=[1, 2], value=2)

        st.divider()
        st.subheader("Filtros")
        min_games = st.slider("Mínimo de jogos na temporada", 0, 82, 5, 1)
        min_minutes = st.slider("Mínimo de minutos por jogo", 0, 40, 15, 1)
        role_filter = st.selectbox("Mostrar jogadores", ROLE_OPTIONS, index=0)

        st.divider()
        st.subheader("Linha")
        line_metric = st.selectbox("Métrica da linha", LINE_METRIC_OPTIONS, index=0)
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

        st.divider()
        st.subheader("Ordenação")
        sort_labels = list(SORT_OPTIONS.keys())
        sort_label = st.selectbox("Ordenar jogadores por", options=sort_labels, index=sort_labels.index("PRA L10"))
        ascending = st.toggle("Ordem crescente", value=False)

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
        away_df = build_team_table(int(selected_game["VISITOR_TEAM_ID"]), season)
        home_df = build_team_table(int(selected_game["HOME_TEAM_ID"]), season)

        away_df = enrich_team_with_context(
            team_df=away_df,
            team_id=int(selected_game["VISITOR_TEAM_ID"]),
            opponent_team_id=int(selected_game["HOME_TEAM_ID"]),
            opponent_team_name=selected_game["home_team_name"],
            season=season,
        )
        home_df = enrich_team_with_context(
            team_df=home_df,
            team_id=int(selected_game["HOME_TEAM_ID"]),
            opponent_team_id=int(selected_game["VISITOR_TEAM_ID"]),
            opponent_team_name=selected_game["away_team_name"],
            season=season,
        )
    except Exception as exc:
        st.error("A NBA demorou ou falhou ao responder nas estatísticas do confronto. Tente novamente em alguns segundos ou use o botão de atualização.")
        st.exception(exc)
        return

    away_df["TEAM_NAME"] = selected_game["away_team_name"]
    home_df["TEAM_NAME"] = selected_game["home_team_name"]

    odds_df = pd.DataFrame()
    if api_key_available:
        try:
            odds_events = fetch_nba_odds_events()
            selected_odds_event = find_matching_odds_event(
                odds_events,
                home_team_name=selected_game["home_team_name"],
                away_team_name=selected_game["away_team_name"],
            )
            odds_df = extract_betmgm_player_props(selected_odds_event)
        except Exception:
            odds_df = pd.DataFrame()

    away_df = merge_betmgm_odds(away_df, odds_df)
    home_df = merge_betmgm_odds(home_df, odds_df)

    try:
        injury_df = fetch_latest_injury_report_df()
    except Exception:
        injury_df = pd.DataFrame()

    game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"

    away_df = merge_injury_report(
        away_df,
        injury_df,
        selected_game["away_team_name"],
        int(selected_game["VISITOR_TEAM_ID"]),
        game_matchup=game_matchup,
    )
    home_df = merge_injury_report(
        home_df,
        injury_df,
        selected_game["home_team_name"],
        int(selected_game["HOME_TEAM_ID"]),
        game_matchup=game_matchup,
    )

    render_matchup_header(selected_game)
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

    tab1, tab2 = st.tabs([selected_game["away_team_name"], selected_game["home_team_name"]])

    with tab1:
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

    with tab2:
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
4) Substitua render_injury_report_tab, render_lineup_report_tab e render_team_section_v2 por estas versões
def render_injury_report_tab(team_df: pd.DataFrame, team_name: str) -> None:
    st.markdown(
        '<div class="section-note">Status oficial do injury report da NBA para o elenco do time.</div>',
        unsafe_allow_html=True,
    )

    if "INJ_STATUS" not in team_df.columns:
        st.info("Injury report ainda não integrado nesta execução.")
        return

    report_df = team_df[["PLAYER", "INJ_STATUS", "INJ_REASON"]].copy()
    report_df = report_df.rename(
        columns={
            "PLAYER": "Jogador",
            "INJ_STATUS": "Status oficial",
            "INJ_REASON": "Motivo",
        }
    )

    st.dataframe(report_df, use_container_width=True)

    flagged = team_df[team_df["INJ_STATUS"] != "Available"].copy()
    st.caption(f"Jogadores com status diferente de Available neste time: {len(flagged)}")

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

    summary_df, detail_df = build_display_dataframes(filtered_df)

    st.markdown(f"### {team_name}")
    st.markdown(
        f'<div><span class="info-pill">Jogadores: {len(filtered_df)}</span><span class="info-pill">Linha ativa: {line_metric} {format_number(line_value)}</span><span class="info-pill">Modo mercado: {"BetMGM quando houver" if use_market_line else "Manual"}</span></div>',
        unsafe_allow_html=True,
    )

    cards_tab, table_tab, injury_tab, lineup_tab = st.tabs(["Cards", "Tabela", "Injury Report", "Provável Escalação"])

    with cards_tab:
        st.markdown(
            '<div class="section-note">Cards curtos no topo e painel fixo do jogador abaixo para facilitar consulta no celular.</div>',
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
            key=f"player_focus_v2_{team_name}_{chart_mode}_{line_metric}",
        )
        selected_row = filtered_df.loc[filtered_df["PLAYER"] == player_name].iloc[0]
        render_player_focus_panel(selected_row, line_metric, line_value, use_market_line, season, chart_mode)

    with table_tab:
        st.markdown(
            '<div class="section-note">Visão de comparação rápida do elenco, sem abrir card por card feito investigador privado.</div>',
            unsafe_allow_html=True,
        )
        mode = st.radio(
            f"Modo da tabela — {team_name}",
            ["Resumo", "Completa"],
            horizontal=True,
            key=f"table_mode_{team_name}",
        )
        if mode == "Resumo":
            st.dataframe(style_table(summary_df, quick_view=True), use_container_width=True)
        else:
            st.dataframe(style_table(detail_df, quick_view=False), use_container_width=True)

    with injury_tab:
        render_injury_report_tab(team_df, team_name)

    with lineup_tab:
        render_lineup_report_tab(team_df, team_name)
5) No fim do arquivo, garanta que exista só isto
if __name__ == "__main__":
    main()
Depois de colar essas substituições:

salva

sobe de novo

clica em Forçar atualização

Se o app subir mas o Jalen ainda vier como Available, aí o próximo alvo não é mais sintaxe nem indentação de primata cansado; aí eu fecho o parser/merge do injury report com um debug curto em cima do injury_df. O arquivo que eu analisei foi este aqui:


voltou a abrir o app, porem nao ta colocando os jogadores indisponiveis... todos estao disponiveis, ate mesmo os machucados

Thought for 2m 28s
O dado oficial existe, então o problema não é “a NBA não informou”. No injury report oficial de hoje para MIN@OKC, a NBA lista “Williams, Jalen — Out — Injury/Illness - Right Hamstring; Strain”. 

No seu código atual, fetch_latest_injury_report_df() já extrai MATCHUP, PLAYER_KEY_IR, INJ_STATUS e INJ_REASON, e merge_injury_report() começa todo mundo como "Available" e só sobrescreve se sobrar linha em work_ir depois dos filtros. Então, quando todo mundo aparece disponível, isso quase sempre significa que o merge está zerando work_ir antes da junção final.

O conserto mais seguro é parar de depender tanto de alias de time e fazer o merge por:

MATCHUP do jogo

PLAYER_KEY do elenco

Troque a função inteira merge_injury_report por esta:

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
    enriched["INJ_STATUS"] = "Available"
    enriched["INJ_REASON"] = ""
    enriched["INJ_REPORT_URL"] = ""
    enriched["IS_UNAVAILABLE"] = False

    if injury_df.empty:
        return enriched

    target_matchup = str(game_matchup or "").upper().replace(" ", "")
    roster_keys = set(enriched["PLAYER_KEY"].fillna("").astype(str).tolist())

    work_ir = injury_df.copy()
    work_ir["MATCHUP_NORM"] = (
        work_ir["MATCHUP"]
        .fillna("")
        .astype(str)
        .str.upper()
        .str.replace(" ", "", regex=False)
    )
    work_ir["PLAYER_KEY_IR"] = work_ir["PLAYER_KEY_IR"].fillna("").astype(str)

    if target_matchup:
        work_ir = work_ir[work_ir["MATCHUP_NORM"] == target_matchup].copy()

    if work_ir.empty:
        return enriched

    work_ir = work_ir[work_ir["PLAYER_KEY_IR"].isin(roster_keys)].copy()

    # fallback extra: recalcula a chave pelo nome cru, caso o PDF venha estranho
    if work_ir.empty and "PLAYER_NAME_IR" in injury_df.columns:
        fallback_ir = injury_df.copy()
        fallback_ir["MATCHUP_NORM"] = (
            fallback_ir["MATCHUP"]
            .fillna("")
            .astype(str)
            .str.upper()
            .str.replace(" ", "", regex=False)
        )
        fallback_ir["PLAYER_KEY_IR"] = fallback_ir["PLAYER_NAME_IR"].fillna("").apply(normalize_person_name)

        if target_matchup:
            fallback_ir = fallback_ir[fallback_ir["MATCHUP_NORM"] == target_matchup].copy()

        work_ir = fallback_ir[fallback_ir["PLAYER_KEY_IR"].isin(roster_keys)].copy()

    if work_ir.empty:
        return enriched

    work_ir = work_ir.drop_duplicates(subset=["PLAYER_KEY_IR"], keep="last")

    merged = enriched.merge(
        work_ir[["PLAYER_KEY_IR", "INJ_STATUS", "INJ_REASON", "INJ_REPORT_URL"]],
        left_on="PLAYER_KEY",
        right_on="PLAYER_KEY_IR",
        how="left",
        suffixes=("", "_IR"),
    )

    merged["INJ_STATUS"] = merged["INJ_STATUS_IR"].fillna(merged["INJ_STATUS"])
    merged["INJ_REASON"] = merged["INJ_REASON_IR"].fillna(merged["INJ_REASON"])
    merged["INJ_REPORT_URL"] = merged["INJ_REPORT_URL_IR"].fillna(merged["INJ_REPORT_URL"])
    merged["IS_UNAVAILABLE"] = merged["INJ_STATUS"].isin(INACTIVE_STATUSES)

    drop_cols = [
        c for c in ["PLAYER_KEY_IR", "INJ_STATUS_IR", "INJ_REASON_IR", "INJ_REPORT_URL_IR", "MATCHUP_NORM"]
        if c in merged.columns
    ]
    if drop_cols:
        merged = merged.drop(columns=drop_cols)

    return merged
Depois, no main(), mantém este bloco exatamente assim, porque ele já está no formato certo para passar o matchup ao merge.

try:
    injury_df = fetch_latest_injury_report_df()
except Exception:
    injury_df = pd.DataFrame()

game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"

away_df = merge_injury_report(
    away_df,
    injury_df,
    selected_game["away_team_name"],
    int(selected_game["VISITOR_TEAM_ID"]),
    game_matchup=game_matchup,
)

home_df = merge_injury_report(
    home_df,
    injury_df,
    selected_game["home_team_name"],
    int(selected_game["HOME_TEAM_ID"]),
    game_matchup=game_matchup,
)
Para não ficar adivinhando no escuro igual um druida cansado, adiciona também este debug temporário logo depois de injury_df = fetch_latest_injury_report_df():

debug_ir = injury_df[
    injury_df["MATCHUP"].fillna("").astype(str).str.upper().str.replace(" ", "", regex=False)
    == game_matchup.upper().replace(" ", "")
].copy()

st.write(
    debug_ir[["TEAM_NAME_IR", "PLAYER_NAME_IR", "PLAYER_KEY_IR", "INJ_STATUS", "INJ_REASON"]]
)
Se aí aparecer Williams, Jalen como Out, o parser está bom e o problema era 100% no merge. Se essa tabela vier vazia, aí o problema está antes, na leitura do PDF. Seu parser atual realmente tenta extrair MATCHUP, TEAM_NAME_IR, PLAYER_NAME_IR, PLAYER_KEY_IR e INJ_STATUS do PDF oficial, então esse debug separa as duas coisas sem teatro.

E mais uma checagem importante: na sua versão corrigida, render_team_section_v2() precisa continuar chamando render_injury_report_tab(team_df, ...) e render_lineup_report_tab(team_df, ...), não filtered_df, senão você mascara jogador indisponível com filtro lateral e depois culpa a medicina. A versão boa já está assim.

Faz essa troca da merge_injury_report e me diz o que aparece no debug_ir. Isso vai matar a dúvida em um carregamento.


File "/mount/src/nba-dashboard/app.py", line 3083
  st.write(
  ^
SyntaxError: expected 'except' or 'finally' block

Thought for 10s
Você enfiou o st.write(...) no meio do bloco try/except.
Python viu isso e falou: “cadê meu except?”. Justamente.

O erro
Se você fez algo assim:

try:
    injury_df = fetch_latest_injury_report_df()

st.write(...)
except Exception:
    injury_df = pd.DataFrame()
isso é inválido. Depois de try:, o Python espera:

código indentado do try

depois except ou finally

Não um st.write solto no meio do corredor.

Onde colocar o debug certo
Coloque o debug depois do except e depois de criar game_matchup.

Fica assim:

try:
    injury_df = fetch_latest_injury_report_df()
except Exception:
    injury_df = pd.DataFrame()

game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"

debug_ir = injury_df[
    injury_df["MATCHUP"].fillna("").astype(str).str.upper().str.replace(" ", "", regex=False)
    == game_matchup.upper().replace(" ", "")
].copy()

st.write(
    debug_ir[["TEAM_NAME_IR", "PLAYER_NAME_IR", "PLAYER_KEY_IR", "INJ_STATUS", "INJ_REASON"]]
)

away_df = merge_injury_report(
    away_df,
    injury_df,
    selected_game["away_team_name"],
    int(selected_game["VISITOR_TEAM_ID"]),
    game_matchup=game_matchup,
)

home_df = merge_injury_report(
    home_df,
    injury_df,
    selected_game["home_team_name"],
    int(selected_game["HOME_TEAM_ID"]),
    game_matchup=game_matchup,
)
Ordem correta
No main(), essa parte tem que ficar exatamente nesta sequência:

try: injury_df = ...

except: injury_df = ...

game_matchup = ...

debug_ir = ...

st.write(...)

away_df = merge_injury_report(...)

home_df = merge_injury_report(...)

Regra prática pra não se sabotar de novo
Nunca coloque nada entre:

try:
    ...
except Exception:
    ...
sem respeitar a estrutura do bloco.

Se quiser, me manda um print da região do main() onde você colocou esse debug e eu te digo exatamente em qual linha ele entra.


agora apareceu isso: File "/mount/src/nba-dashboard/app.py", line 3081, in <module>
    game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"
                                           ^^^^^^^^^^^^^

Vou localizar exatamente onde esse bloco escapou do main(), porque o erro continua gritando que ele está no escopo global. Python, esse fiscal de prefeitura com diploma.

Thought for 55s
Sim. O erro continua porque o fim do main() ainda está quebrado na indentação.

O trecho do seu arquivo mostra exatamente isso:

game_matchup, away_df = merge_injury_report(...) e home_df = merge_injury_report(...) estão indentados como parte do main()

mas logo depois render_summary_cards(, render_game_rankings(, tab1, tab2 = ..., os with tab1/with tab2 e o st.markdown(...) final perderam os 4 espaços

quando isso acontece, o Python passa a tratar esse pedaço como código global, e aí selected_game, away_df e game_matchup viram fantasmas inexistentes

Em outras palavras: o main() rachou no meio. Uma obra expressionista, mas não muito funcional.

O que fazer
Não tenta remendar linha por linha.
Substitui o final do main() inteiro, a partir de:

away_df = merge_betmgm_odds(away_df, odds_df)
home_df = merge_betmgm_odds(home_df, odds_df)
até antes da próxima def render_injury_report_tab(...), por este bloco todo:

    away_df = merge_betmgm_odds(away_df, odds_df)
    home_df = merge_betmgm_odds(home_df, odds_df)

    try:
        injury_df = fetch_latest_injury_report_df()
    except Exception:
        injury_df = pd.DataFrame()

    game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"

    away_df = merge_injury_report(
        away_df,
        injury_df,
        selected_game["away_team_name"],
        int(selected_game["VISITOR_TEAM_ID"]),
        game_matchup=game_matchup,
    )

    home_df = merge_injury_report(
        home_df,
        injury_df,
        selected_game["home_team_name"],
        int(selected_game["HOME_TEAM_ID"]),
        game_matchup=game_matchup,
    )

    render_matchup_header(selected_game)
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

    tab1, tab2 = st.tabs([selected_game["away_team_name"], selected_game["home_team_name"]])

    with tab1:
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

    with tab2:
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
O detalhe importante
Todas essas linhas acima precisam estar com 4 espaços na frente, porque ainda pertencem ao main().

Se uma delas começar na coluna 0, o Python volta a tratar como código global e o circo reabre. O seu arquivo atual mostra exatamente essa perda de indentação depois de render_matchup_header(selected_game) .

Mais uma correção útil
Na sua versão boa de render_team_section_v2, as abas de injury/lineup devem usar team_df, não filtered_df, para não esconder machucado por causa de filtro lateral. A versão corrigida fica assim no fim da função:

    with injury_tab:
        render_injury_report_tab(team_df, team_name)

    with lineup_tab:
        render_lineup_report_tab(team_df, team_name)
A versão antiga do app usava filtered_df nessas duas abas, que é justamente o tipo de sutileza chata que sabota o injury report depois .

Depois de substituir esse bloco do main(), salva e roda de novo. Se subir, aí a gente volta a atacar o merge do injury report, que é o próximo suspeito habitual.


e agora: File "/mount/src/nba-dashboard/app.py", line 3062
      try:
      ^
IndentationError: expected an indented block after 'if' statement on line 3061

Vou olhar o trecho exato do arquivo pra te dizer onde o if ficou sem corpo, em vez de ficar te fazendo jogar Wordle com indentação.

Thought for 30s
O if api_key_available: ficou sem corpo válido porque o bloco logo abaixo dele perdeu o recuo certo. No arquivo que você me mandou, essa região deveria ter try/except dentro do if, e o fim do main() também está quebrando a indentação mais adiante, com render_summary_cards, render_game_rankings, tab1/tab2 e o footer saindo do escopo da função. Isso explica esse festival de IndentationError e NameError.

Faz o seguinte: substitui do odds_df = pd.DataFrame() até antes de def render_injury_report_tab por este bloco inteiro, sem remendar linha solta. É o jeito menos amaldiçoado.

    odds_df = pd.DataFrame()
    if api_key_available:
        try:
            odds_events = fetch_nba_odds_events()
            selected_odds_event = find_matching_odds_event(
                odds_events,
                home_team_name=selected_game["home_team_name"],
                away_team_name=selected_game["away_team_name"],
            )
            odds_df = extract_betmgm_player_props(selected_odds_event)
        except Exception:
            odds_df = pd.DataFrame()

    away_df = merge_betmgm_odds(away_df, odds_df)
    home_df = merge_betmgm_odds(home_df, odds_df)

    try:
        injury_df = fetch_latest_injury_report_df()
    except Exception:
        injury_df = pd.DataFrame()

    game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"

    away_df = merge_injury_report(
        away_df,
        injury_df,
        selected_game["away_team_name"],
        int(selected_game["VISITOR_TEAM_ID"]),
        game_matchup=game_matchup,
    )

    home_df = merge_injury_report(
        home_df,
        injury_df,
        selected_game["home_team_name"],
        int(selected_game["HOME_TEAM_ID"]),
        game_matchup=game_matchup,
    )

    render_matchup_header(selected_game)
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

    tab1, tab2 = st.tabs([selected_game["away_team_name"], selected_game["home_team_name"]])

    with tab1:
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

    with tab2:
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
Duas regras que precisam estar verdadeiras, ou o Python volta a morder:

todas as linhas acima começam com 4 espaços, porque ainda estão dentro do main()

o try: logo abaixo de if api_key_available: tem 8 espaços, porque está dentro do if

No arquivo que você subiu, esse pedaço já estava parcialmente certo até render_matchup_header(selected_game), mas depois o restante saiu da indentação do main(), o que bagunçou tudo de novo.

Depois de colar, salva e roda. Se der outro erro, manda o próximo traceback. A essa altura estamos fazendo cirurgia ortopédica com colher, mas ainda dá pra terminar vivo.


fiz como voce falou: apaguei do odds_df até antes do def render_injury report e apareceu isso: File "/mount/src/nba-dashboard/app.py", line 3062 
      try:
      ^
IndentationError: expected an indented block after 'if' statement on line 3061

é o mesmo erro? se fopr, me fala que vou copiar aqui exatamente esse pedação do codigo para vonce conferir

Thought for 9s
Sim, é o mesmo tipo de erro: o if api_key_available: ainda está sem um bloco válido logo abaixo.
Ou seja, o Python continua vendo algo como:

if api_key_available:
try:
quando deveria ver:

if api_key_available:
    try:
Então faz isso: cola aqui exatamente esse pedaço do código, de preferência da linha 3058 até 3072.
Quero ver estas linhas:

odds_df = pd.DataFrame()

if api_key_available:

try:

o bloco logo abaixo

Porque aí eu te devolvo o trecho já alinhado, sem a gente continuar adivinhando indentação invisível igual dois monges mexendo em fósforo.


Imagem carregada

Thought for 9s
Sim, agora deu pra ver o problema com nitidez. O try: está no mesmo nível do if api_key_available:. Então, para o Python, o if ficou sem corpo. Drama merecido, mas previsível.

No seu print está assim, na prática:

    odds_df = pd.DataFrame()
    if api_key_available:
    try:
E tem que ficar assim:

    odds_df = pd.DataFrame()
    if api_key_available:
        try:
Ou seja:

if api_key_available: → 4 espaços

try: → 8 espaços

tudo dentro do try → 12 espaços

except Exception: → 8 espaços

o que estiver dentro do except → 12 espaços

Substitui esse pedaço exato por este bloco:

    odds_df = pd.DataFrame()
    if api_key_available:
        try:
            odds_events = fetch_nba_odds_events()
            selected_odds_event = find_matching_odds_event(
                odds_events,
                home_team_name=selected_game["home_team_name"],
                away_team_name=selected_game["away_team_name"],
            )
            odds_df = extract_betmgm_player_props(selected_odds_event)
        except Exception:
            odds_df = pd.DataFrame()

    away_df = merge_betmgm_odds(away_df, odds_df)
    home_df = merge_betmgm_odds(home_df, odds_df)

    try:
        injury_df = fetch_latest_injury_report_df()
    except Exception:
        injury_df = pd.DataFrame()
O ponto-chave é esse:

away_df = merge_betmgm_odds(...) e home_df = ... voltam para 4 espaços

porque eles já ficam fora do if api_key_available:

No seu print, o resto abaixo parece alinhado de forma aceitável; o erro imediato é só esse try: mal recuado. Python continua sendo um agente de trânsito da indentação, mas pelo menos agora ele apontou o cone exato.

Depois de ajustar isso, roda de novo. Se aparecer outro erro, manda o próximo print do trecho seguinte.


Imagem carregada
alterei o codigo e está dessa forma. Esta certo ou errado?

Thought for 5s
Está certo agora, nesse trecho.

O que eu vejo no print:

if api_key_available: está com um nível de recuo

try: está dentro do if

except Exception: está alinhado com o try:

odds_df = pd.DataFrame() dentro do except está corretamente dentro dele

away_df = merge_betmgm_odds(...) e home_df = ... voltam para o nível externo certo

o try: do injury report também parece alinhado corretamente

Então esse pedaço específico não está mais com o erro de indentação.

Resumo do veredito técnico, já que você me transformou em fiscal de espaçamento:

esse bloco: ok

pode testar o app de novo

Se aparecer outro erro agora, ele provavelmente está:

mais abaixo no main(), ou

na lógica do merge do injury report, não mais nesse if/try

Manda o próximo traceback se explodir de novo.


ok, se o caodigo ta certo, porque apareceu esse erro aqui ainda: 
File "/mount/src/nba-dashboard/app.py", line 3062
      try:
      ^
IndentationError: expected an indented block after 'if' statement on line 3061

Thought for 1m 12s
Sim — e o motivo é bem menos místico do que parece: o trecho do print está certo, mas o arquivo que está rodando ainda não está inteiro certo.

Tem dois problemas ao mesmo tempo:

O traceback está apontando para app.py, não para app (1).py.
Então, se você editou uma cópia, preview, diff, ou arquivo renomeado, o Streamlit continua executando o app.py real do repo. O erro mostra isso no caminho /mount/src/nba-dashboard/app.py. E o arquivo que você me enviou como app (1).py é outro upload separado.

Mesmo no app (1).py, o main() ainda está quebrado mais abaixo.
O bloco do if api_key_available: está ok no trecho que você mostrou, mas depois de render_matchup_header(selected_game) a indentação desaba:
render_summary_cards(, render_game_rankings(, tab1, tab2 = ..., with tab1, with tab2 e st.markdown(...) voltam para a coluna 0. Isso tira esse pedaço de dentro do main() e deixa o arquivo inconsistente.

Então a resposta honesta é: o snippet estava certo, mas o arquivo inteiro ainda não estava. Python não avalia “só esse pedacinho bonito”; ele julga o arquivo inteiro, como um síndico amargurado.

O que fazer agora
No arquivo real app.py, substitui o final do main() inteiro pelo bloco abaixo.
Não remenda linha solta. Substitui desde:

    odds_df = pd.DataFrame()
até antes de:

def render_injury_report_tab(
por isto:

    odds_df = pd.DataFrame()
    if api_key_available:
        try:
            odds_events = fetch_nba_odds_events()
            selected_odds_event = find_matching_odds_event(
                odds_events,
                home_team_name=selected_game["home_team_name"],
                away_team_name=selected_game["away_team_name"],
            )
            odds_df = extract_betmgm_player_props(selected_odds_event)
        except Exception:
            odds_df = pd.DataFrame()

    away_df = merge_betmgm_odds(away_df, odds_df)
    home_df = merge_betmgm_odds(home_df, odds_df)

    try:
        injury_df = fetch_latest_injury_report_df()
    except Exception:
        injury_df = pd.DataFrame()

    game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"

    away_df = merge_injury_report(
        away_df,
        injury_df,
        selected_game["away_team_name"],
        int(selected_game["VISITOR_TEAM_ID"]),
        game_matchup=game_matchup,
    )

    home_df = merge_injury_report(
        home_df,
        injury_df,
        selected_game["home_team_name"],
        int(selected_game["HOME_TEAM_ID"]),
        game_matchup=game_matchup,
    )

    render_matchup_header(selected_game)
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

    tab1, tab2 = st.tabs([selected_game["away_team_name"], selected_game["home_team_name"]])

    with tab1:
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

    with tab2:
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
Confere estas 3 coisas antes de testar
você está editando o app.py real, não app (1).py ou preview/diff

esse bloco inteiro está com 4 espaços na frente

depois dele vem direto def render_injury_report_tab(...)

Por que estou insistindo nisso
Porque no arquivo que você subiu, o problema visível não é mais o if api_key_available:; é o fato de que o resto do main() saiu da indentação correta depois de render_matchup_header(selected_game). Enquanto isso não for corrigido, você vai continuar recebendo erro em linha “aparentemente certa”, porque o parser já chegou lá irritado.

Se quiser, cola aqui o trecho do app.py real começando em odds_df = pd.DataFrame() e terminando em st.markdown("""...**, e eu te digo em um olhar se finalmente ficou inteiro no lugar certo.



app.py
Python
alterei conforme solicitou e mantem o mesmo erro. Estou enviando o arquivo, confere se ta realmente certo ou faltando algo

Thought for 1m 56s
Conferi o arquivo que você enviou agora, e a parte que estava dando aquele erro da linha 3062 está certa neste arquivo. No main(), o bloco if api_key_available: já tem o try: corretamente indentado dentro dele, e o trecho do injury report (fetch_latest_injury_report_df, game_matchup, merge_injury_report) também está dentro do main(). Então, neste arquivo enviado agora, o erro antigo de indentação não aparece mais nesse ponto.

Também confirmei que a versão atual de render_team_section_v2() já está chamando:

render_injury_report_tab(team_df, team_name)

render_lineup_report_tab(team_df, team_name)

e não mais filtered_df, o que era uma das sabotagens anteriores. Isso está correto na sua versão atual.

Então o que está faltando de verdade? Duas coisas:

Primeiro: o ambiente que está rodando provavelmente não é esse arquivo atualizado.
O traceback que você vinha recebendo combina com a versão quebrada anterior, aquela em que o fim do main() tinha escapado e o bloco do injury report/summary estava torto. A versão antiga que você tinha enviado antes mostrava exatamente esse tipo de quebra estrutural; a atual não. Ou seja, o candidato mais forte é: você corrigiu o arquivo local/novo, mas o Streamlit ainda está executando outra cópia do app.py no repositório/deploy.

Segundo: o arquivo atual ainda tem uma sujeirinha real no código, embora não seja a causa daquele erro específico. Em get_position_opponent_profile, sobrou bloco duplicado/dead code depois do primeiro return. Isso não deveria existir e vale limpar, porque seu arquivo está virando um pântano ritualístico.

O veredito útil é este:

o app.py que você enviou agora está estruturalmente certo nessa região do main()

se o mesmo erro continua aparecendo no app rodando, o deploy não está usando esse arquivo

ainda vale limpar get_position_opponent_profile duplicado, mas isso não explica a linha 3062

O que eu faria agora, sem poesia:

substituir no repositório o app.py real por este arquivo atualizado

fazer commit

forçar redeploy/restart no Streamlit Cloud

só depois testar de novo

Se quiser, eu posso agora te devolver um checklist curtíssimo de verificação de deploy pra confirmar se o Streamlit está mesmo lendo o arquivo certo, porque neste ponto o problema está com mais cara de versionamento do que de Python fazendo birra.


o problema era aparentemente no sreamlit mesmo, reiniciei o app la e entrou. A gente quebrando a cabeça e tava certo, o problema era la.... Porem agora esta esse erro aqui: AttributeError: module 'pandas.io.formats' has no attribute 'style'
Traceback:
File "/workspaces/nba-dashboard/app.py", line 2033, in <module>
    def style_table(df: pd.DataFrame, quick_view: bool) -> pd.io.formats.style.Styler:
                                                           ^^^^^^^^^^^^^^^^^^^

Thought for 22s
Sim. Dessa vez o culpado é o pandas, não a sua dignidade.

O erro acontece porque você escreveu a anotação assim:

def style_table(df: pd.DataFrame, quick_view: bool) -> pd.io.formats.style.Styler:
e no ambiente do Streamlit essa rota interna do pandas não está exposta desse jeito. Como a anotação é avaliada quando o arquivo carrega, o app morre antes mesmo de rodar. Lindo. Muito produtivo.

Corrige assim
1) Adicione este import no topo
Perto dos outros imports:

from pandas.io.formats.style import Styler
2) Troque a assinatura da função
De:

def style_table(df: pd.DataFrame, quick_view: bool) -> pd.io.formats.style.Styler:
Para:

def style_table(df: pd.DataFrame, quick_view: bool) -> Styler:
Se quiser a solução mais à prova de frescura
Você pode simplesmente remover a anotação de retorno:

def style_table(df: pd.DataFrame, quick_view: bool):
Mas eu usaria esta, que fica limpa:

from pandas.io.formats.style import Styler

def style_table(df: pd.DataFrame, quick_view: bool) -> Styler:
Por que isso aconteceu agora
Porque pd.io.formats... é caminho interno do pandas, e essas entranhas mudam conforme a versão. Seu app decidiu descobrir isso no ambiente de deploy, como todo sistema com autoestima baixa.

Faz essa troca e reinicia. Esse erro específico deve morrer na hora.


Imagem carregada
estou editando agora direto pelo github.dev
como salvo uma alteração aqui? 

No github.dev, salvar é só:

Ctrl + S no Windows/Linux

Cmd + S no Mac

Isso salva a alteração no editor.
Mas salvar não faz deploy sozinho, porque o GitHub ainda precisa registrar a mudança.

Depois de salvar, faz assim:

Para gravar no repositório
clica no ícone de Source Control na barra lateral esquerda
(aquele de ramificação/bolinhas conectadas)

vai aparecer o arquivo modificado

escreve uma mensagem de commit, tipo:
fix style_table Styler import

clica em Commit

depois clica em Push/Sync Changes

O que você precisa mudar agora
No topo do arquivo, adicione:

from pandas.io.formats.style import Styler
E na função troque:

def style_table(df: pd.DataFrame, quick_view: bool) -> pd.io.formats.style.Styler:
por:

def style_table(df: pd.DataFrame, quick_view: bool) -> Styler:
Depois disso
salva com Ctrl + S

commit

push

volta no Streamlit e reboot/redeploy

O painel da direita que você mostrou é só preview quebrado do app, aquele teatrinho de erro. O que manda é: salvar, commitar e subir.


Imagem carregada
agora um outro problema, não ta puxando os jogos atualizados de hoje, 16/03. Ja forcei atualização e fica puxando só os jogos de ontem

Thought for 1m 39s
Achei o motivo. Não é feitiçaria; é a função de jogos te sabotando com educação.

Hoje o seu get_games_for_date() chama live_scoreboard.ScoreBoard() sem passar data. A própria documentação do nba_api mostra esse endpoint live como “Today’s Score Board”, ou seja, ele te entrega o placar “de hoje” segundo o endpoint live, não necessariamente o dia que você quer em Brasília. 

Aí o seu código faz isso:

pega selected_date = get_brasilia_today() usando America/Sao_Paulo

tenta filtrar os jogos retornados para a data de Brasília

mas só substitui games_list se filtered_games não estiver vazio; se o filtro não achar nada, ele mantém a lista original com os jogos antigos, por causa deste trecho: if filtered_games: games_list = filtered_games

Então o bug é exatamente este: quando o filtro por 16/03 não acha nada, o app cai de volta na lista bruta do endpoint live e te mostra os jogos de ontem. Um pequeno golpe de estado lógico.

A correção melhor é parar de usar o live scoreboard para escolher o dia e usar o endpoint com data explícita. O ScoreboardV2 do nba_api aceita GameDate, DayOffset e LeagueID. 

O que trocar
1) Adicione este import no topo
from nba_api.stats.endpoints import scoreboardv2
2) Substitua a função get_games_for_date inteira por esta
@st.cache_data(ttl=300, show_spinner=False)
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
Se você quiser o remendo mínimo, sem trocar endpoint
No seu código atual, troque isto:

if filtered_games:
    games_list = filtered_games
por isto:

games_list = filtered_games
ou, mais seguro:

if not filtered_games:
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

games_list = filtered_games
Isso não resolve a origem do problema, mas impede o app de mostrar ontem quando hoje não bateu. O bug vem justamente desse fallback indevido.

Minha recomendação
Faz a troca para ScoreboardV2.
É a correção certa para “jogos do dia” porque você passa a data explicitamente, em vez de confiar que o endpoint live e o fuso de Brasília vão cooperar por bondade espontânea. Algo que, como vimos, seria otimista demais. 

Se quiser, no próximo turno eu te mando o bloco de imports + a função já em formato de “copiar e colar” sem mais cirurgia manual.


beleza, alterei a função inteira e resolveu. Mas o problema do injury report ainda mantem, todos os jogadores aparecem na dashboard como disponível

Thought for 5m 44s
Você não está maluco. O bug agora é de lógica, não de sintaxe.

No seu app.py atual, o fluxo faz duas coisas que juntas mascaram o problema:

o app busca sempre o PDF mais recente do injury report oficial (fetch_latest_injury_report_df)

o merge_injury_report começa todo mundo como "Available" e, se não encontrar linhas do matchup em work_ir, simplesmente retorna esse default para o elenco inteiro

Então, quando o PDF mais recente não contém aquele jogo ou ainda está como NOT YET SUBMITTED, o resultado visual vira: “todo mundo disponível”. É um belo embuste algorítmico.
Além disso, a página oficial da NBA deixa claro que os reports são atualizados continuamente ao longo do dia, e o relatório de 16/03 09:45 ET contém matchups como GSW@WAS, PHX@BOS e vários jogos como NOT YET SUBMITTED; ele não é um espelho universal de qualquer jogo que seu app estiver mostrando. 

O que eu mudaria agora
Em vez de marcar todo mundo como Available por padrão, marque como — até o matchup realmente ser encontrado no injury report. Só depois disso você promove os não listados para Available.

Troque a função merge_injury_report inteira por esta
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

    target_matchup = str(game_matchup or "").upper().replace(" ", "")
    roster_keys = set(enriched["PLAYER_KEY"].fillna("").astype(str).tolist())

    work_ir = injury_df.copy()
    work_ir["MATCHUP_NORM"] = (
        work_ir["MATCHUP"]
        .fillna("")
        .astype(str)
        .str.upper()
        .str.replace(" ", "", regex=False)
    )
    work_ir["PLAYER_KEY_IR"] = work_ir["PLAYER_KEY_IR"].fillna("").astype(str)

    if target_matchup:
        work_ir = work_ir[work_ir["MATCHUP_NORM"] == target_matchup].copy()

    if work_ir.empty:
        return enriched

    work_ir = work_ir[work_ir["PLAYER_KEY_IR"].isin(roster_keys)].copy()

    if work_ir.empty and "PLAYER_NAME_IR" in injury_df.columns:
        fallback_ir = injury_df.copy()
        fallback_ir["MATCHUP_NORM"] = (
            fallback_ir["MATCHUP"]
            .fillna("")
            .astype(str)
            .str.upper()
            .str.replace(" ", "", regex=False)
        )
        fallback_ir["PLAYER_KEY_IR"] = fallback_ir["PLAYER_NAME_IR"].fillna("").apply(normalize_person_name)

        if target_matchup:
            fallback_ir = fallback_ir[fallback_ir["MATCHUP_NORM"] == target_matchup].copy()

        work_ir = fallback_ir[fallback_ir["PLAYER_KEY_IR"].isin(roster_keys)].copy()

    if work_ir.empty:
        return enriched

    work_ir = work_ir.drop_duplicates(subset=["PLAYER_KEY_IR"], keep="last")

    # aqui sim o matchup foi encontrado; jogadores sem linha explícita viram Available
    enriched["INJ_STATUS"] = "Available"
    enriched["INJ_MATCHUP_FOUND"] = True

    merged = enriched.merge(
        work_ir[["PLAYER_KEY_IR", "INJ_STATUS", "INJ_REASON", "INJ_REPORT_URL"]],
        left_on="PLAYER_KEY",
        right_on="PLAYER_KEY_IR",
        how="left",
        suffixes=("", "_IR"),
    )

    merged["INJ_STATUS"] = merged["INJ_STATUS_IR"].fillna(merged["INJ_STATUS"])
    merged["INJ_REASON"] = merged["INJ_REASON_IR"].fillna(merged["INJ_REASON"])
    merged["INJ_REPORT_URL"] = merged["INJ_REPORT_URL_IR"].fillna(merged["INJ_REPORT_URL"])
    merged["IS_UNAVAILABLE"] = merged["INJ_STATUS"].isin(INACTIVE_STATUSES)

    drop_cols = [
        c for c in ["PLAYER_KEY_IR", "INJ_STATUS_IR", "INJ_REASON_IR", "INJ_REPORT_URL_IR", "MATCHUP_NORM"]
        if c in merged.columns
    ]
    if drop_cols:
        merged = merged.drop(columns=drop_cols)

    return merged
E ajuste render_injury_report_tab
Assim você enxerga quando o problema é “não achei matchup no report”, em vez de o app mentir com Available.

def render_injury_report_tab(team_df: pd.DataFrame, team_name: str) -> None:
    st.markdown(
        '<div class="section-note">Status oficial do injury report da NBA para o elenco do time.</div>',
        unsafe_allow_html=True,
    )

    if "INJ_STATUS" not in team_df.columns:
        st.info("Injury report ainda não integrado nesta execução.")
        return

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
Por que isso resolve
Hoje o seu main() monta game_matchup e passa isso para merge_injury_report. O parser também extrai MATCHUP, PLAYER_KEY_IR e INJ_STATUS do PDF oficial.
Só que, quando não há linhas do report para aquele matchup, a função atual devolve o elenco inteiro com "Available" por default. Com o patch acima:

se não houver linhas do matchup no PDF, os jogadores ficam —

só viram Available quando o matchup realmente existir no injury report

O que eu suspeito no seu caso
A suspeita mais forte é esta: o PDF mais recente que você baixa não está batendo com o slate que o app está mostrando. A página oficial publica relatórios por data/hora ET e o relatório atual mostra apenas os matchups daquele bloco, além de vários NOT YET SUBMITTED
. Se o jogo exibido no app não estiver naquele PDF, sua função antiga carimba “Available” em massa. Isso combina perfeitamente com o sintoma.

Debug rápido, só para matar a dúvida
Logo depois de:

injury_df = fetch_latest_injury_report_df()
coloque temporariamente:

st.write("game_matchup:", game_matchup)

debug_ir = injury_df.copy()
debug_ir["MATCHUP_NORM"] = (
    debug_ir["MATCHUP"].fillna("").astype(str).str.upper().str.replace(" ", "", regex=False)
)

st.write(
    debug_ir.loc[
        debug_ir["MATCHUP_NORM"] == game_matchup.upper().replace(" ", ""),
        ["MATCHUP", "TEAM_NAME_IR", "PLAYER_NAME_IR", "PLAYER_KEY_IR", "INJ_STATUS", "INJ_REASON"]
    ]
)
Se isso vier vazio, o problema não é o lineup nem o merge por nome do jogador. É o fato de que o report atual não contém aquele matchup.

Se quiser, no próximo passo eu te passo a versão 2 do patch, que busca o injury report com base na data ET do jogo selecionado, em vez de sempre pegar “o PDF mais recente”. Isso fecha o buraco de vez.


beleza, funcionou. Vamos aguardar o primeiro report para ver se atualiza. Quais horarios saem os reports oficiais?

Thought for 31s
Os horários “oficiais” funcionam assim, sem a NBA facilitar sua vida, claro:

Para jogos normais, o time precisa registrar o status até 5:00 p.m. no horário local, no dia anterior ao jogo. Para o segundo jogo de back-to-back, o prazo é 1:00 p.m. no horário local, no dia do jogo. Além disso, em dia de jogo, os times precisam enviar um game-day injury report entre 11:00 a.m. e 1:00 p.m. no horário local; se o jogo começa às 5:00 p.m. ou antes, essa janela vira 8:00 a.m. a 10:00 a.m. no horário local. 

Na prática do site oficial, os PDFs ficam sendo atualizados continuamente ao longo do dia. A página oficial literalmente diz isso e, no dia de hoje, mostra publicações em intervalos de 15 minutos — por exemplo 12:00 a.m., 12:15 a.m., 12:30 a.m. ... até 9:45 a.m. ET no trecho carregado. 

Para você, que está olhando em Brasília, normalmente em março a diferença é de +1 hora em relação ao horário do Leste dos EUA. Então, como referência:

11:00 a.m.–1:00 p.m. ET vira 12:00–14:00 em Brasília

8:00–10:00 a.m. ET vira 9:00–11:00 em Brasília

5:00 p.m. ET vira 18:00 em Brasília

1:00 p.m. ET vira 14:00 em Brasília
Isso é conversão de fuso, não regra da NBA. A regra oficial é sempre em local time / ET na página dos PDFs. 

Então, para monitorar no app, os melhores momentos para esperar mudança são:

manhã / início da tarde em Brasília, quando costuma entrar o game-day report

ao longo do dia, porque o PDF oficial pode ser atualizado várias vezes depois disso 

Se você quiser, no próximo passo eu te digo como adaptar o app para mostrar também o horário do último PDF oficial carregado, que evita essa sensação maravilhosa de “será que atualizou ou estou olhando um cache morto?”.


beleza, pode ser, porque ai temos uma noção se ta atualizado mesmo

Thought for 27s
Boa. Isso vale a pena mesmo, porque senão você fica olhando o app como quem encara um peixe no aquário tentando adivinhar se ele está vivo ou só parado.

A forma mais simples é mostrar:

qual PDF oficial foi usado

horário do report em ET

horário convertido para Brasília

1) Adicione este helper
Cola perto dos helpers do injury report, por exemplo abaixo de clean_injury_pdf_line:

def parse_injury_report_timestamp_from_url(pdf_url: str) -> dict:
    if not pdf_url:
        return {
            "report_label_et": "—",
            "report_label_brt": "—",
            "report_dt_et": None,
            "report_dt_brt": None,
        }

    match = re.search(
        r"Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{1,2})_(\d{2})(AM|PM)\.pdf",
        str(pdf_url),
        flags=re.IGNORECASE,
    )
    if not match:
        return {
            "report_label_et": "—",
            "report_label_brt": "—",
            "report_dt_et": None,
            "report_dt_brt": None,
        }

    date_part = match.group(1)
    hour_part = int(match.group(2))
    minute_part = int(match.group(3))
    ampm_part = match.group(4).upper()

    if ampm_part == "AM":
        hour_24 = 0 if hour_part == 12 else hour_part
    else:
        hour_24 = 12 if hour_part == 12 else hour_part + 12

    dt_et = datetime.strptime(date_part, "%Y-%m-%d").replace(
        hour=hour_24,
        minute=minute_part,
        second=0,
        microsecond=0,
        tzinfo=EASTERN_TIMEZONE,
    )
    dt_brt = dt_et.astimezone(APP_TIMEZONE)

    return {
        "report_label_et": dt_et.strftime("%d/%m %I:%M %p ET"),
        "report_label_brt": dt_brt.strftime("%d/%m %H:%M BRT"),
        "report_dt_et": dt_et,
        "report_dt_brt": dt_brt,
    }
2) Mostre isso na aba Injury Report
Substitua a sua função render_injury_report_tab por esta versão:

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
3) Se quiser deixar ainda mais claro
Você pode colocar também um aviso curtinho no topo do app, logo depois de carregar injury_df no main().

Depois deste bloco:

try:
    injury_df = fetch_latest_injury_report_df()
except Exception:
    injury_df = pd.DataFrame()
adicione:

injury_report_url = ""
if not injury_df.empty and "INJ_REPORT_URL" in injury_df.columns:
    valid_urls = injury_df["INJ_REPORT_URL"].dropna().astype(str)
    valid_urls = valid_urls[valid_urls.str.strip() != ""]
    if not valid_urls.empty:
        injury_report_url = valid_urls.iloc[0]

injury_report_meta = parse_injury_report_timestamp_from_url(injury_report_url)
E depois de render_matchup_header(selected_game), adicione:

st.caption(
    f"Injury report oficial carregado: {injury_report_meta['report_label_et']} • {injury_report_meta['report_label_brt']}"
)
Resultado
Com isso, você passa a ver no app algo como:

PDF oficial: 16/03 09:45 AM ET

Brasília: 16/03 10:45 BRT

Aí, se o report ainda não saiu para aquele matchup, você sabe que o app não está “errado”; ele só está olhando o PDF mais recente disponível. Que é bem diferente de o código estar bêbado, embora às vezes pareça a mesma coisa.

Se quiser, eu posso te montar uma versão mais bonita disso em formato de pill/card pequeno no topo, em vez de caption.


Imagem carregada
ficou assim no app. é isso mesmo?

Thought for 26s
Sim — desse jeito, o comportamento está coerente com o patch.

Essa tela quer dizer exatamente isto:

o app não encontrou linhas do injury report oficial para esse matchup

então ele não assume que todo mundo está Available

por isso os jogadores ficam com — em vez de uma disponibilidade inventada

Ou seja: é melhor isso do que a versão anterior, que mentia com convicção e marcava todo mundo como disponível.

Mas tem um detalhe importante no seu print:

O que está certo
o aviso amarelo/verde faz sentido

os jogadores com — fazem sentido

isso indica que o “modo seguro” entrou corretamente

O que ainda está estranho
Esses campos no topo:

PDF oficial: —

Brasília: —

Fonte oficial não identificada

isso mostra que o INJ_REPORT_URL não chegou no team_df dessa tela.

Então existem duas possibilidades:

o report atual realmente não tem esse matchup
nesse caso, a tela está correta e o topo vazio é aceitável por enquanto

o parser/merge não está propagando a URL do PDF
aí o fallback está funcionando, mas a rastreabilidade ficou manca

Minha leitura honesta
Sim, essa tela está correta como fallback.
Ela significa: “não achei evidência oficial para este jogo neste report carregado”.

Isso é exatamente o comportamento que a gente queria no lugar de:

todo mundo = Available

zero confiança

app mentindo com sorriso no rosto

Mas tem um segundo sinal esquisito no print
A lista de jogadores parece misturada:

Alex Morales

Anthony Black

Jonathan Isaac

Jevon Carter

Desmond Bane

Jalen Suggs

Se isso era para representar um único time, então tem outra coisinha suspeita no seu pipeline: o elenco/roster dessa aba pode estar vindo contaminado. Porque isso aí parece salada de times, não uma rotação normal de uma franquia só. Um buffet livre da NBA.

Então eu dividiria o diagnóstico assim:

Se a sua preocupação é o injury report
Está certo. O fallback está funcionando.

Se a sua preocupação é a lista de jogadores dessa aba
Vale checar. Porque esse elenco no print parece estranho para um time só.

Conclusão prática
Sim, é isso mesmo para o cenário “matchup ainda não encontrado no report oficial”.

O que eu sugiro observar quando sair o primeiro report do jogo:

se o topo passar a mostrar algo como PDF oficial: 16/03 ... ET

se o aviso desaparecer

se alguns jogadores passarem de — para Out, Questionable, Probable etc.

Se isso acontecer, o fluxo está certo.

Se quiser, o próximo passo útil é eu te ajudar a colocar um debug pequeno mostrando o game_matchup e quantas linhas do PDF bateram com ele, porque aí você para de adivinhar e vê na hora se o report daquele jogo já entrou ou não.


me mande ai o link do report oficial da nba

Thought for 10s
Está aqui: a página oficial do injury report da NBA, onde ficam os PDFs atualizados ao longo do dia. 

Ela também informa que os reports são atualizados continuamente e lista os PDFs por horário ET na própria página. 

Se você quiser o PDF específico mais recente, eu também posso te passar qual é o último link listado agora.


Imagem carregada
Imagem carregada
bem vizualizei um problema aqui: não ta puxando o report corretamente. Exemplo: o Whashington Wizards ja atualizou seu report conforme imagem em anexo, porém no app ta dessa forma conforme segunda imagem em anexo

Thought for 2m 49s
Sim — pelas duas imagens, o bug agora ficou bem claro.

O que elas mostram:

no PDF oficial de 10:30 AM, o matchup GSW@WAS já tem linhas do Washington Wizards preenchidas

no app, o time aparece com — e com o aviso “não encontrei linhas do injury report”

Então o problema mais provável é este:

O app não está lendo o PDF mais recente
A página oficial da NBA é atualizada continuamente ao longo do dia. 

Se o seu código ainda escolhe o PDF com algo como pdf_urls[-1], ele pode estar pegando um PDF mais antigo da página, não o mais novo. E isso bate exatamente com o sintoma:

PDF antigo: GSW@WAS ainda “NOT YET SUBMITTED”

PDF novo: Wizards já preenchido

app: acha que não existe matchup no report

Tem um segundo ponto:

O parser também precisa entender linhas de time “soltas”
No PDF, o time aparece em linha própria:

Washington Wizards

depois vêm os jogadores

Se o parser não registra esse current_team quando a linha tem só o nome do time, ele perde contexto. Isso não deveria matar o merge por matchup/jogador sozinho, mas ajuda a bagunçar o fluxo.

O conserto que eu recomendo
1) Troque fetch_latest_injury_report_pdf_url() por uma versão que escolhe o PDF mais novo por timestamp
Em vez de confiar na ordem da página, a função vai extrair a data/hora do nome do PDF e pegar o mais recente de verdade.

def parse_report_dt_from_url(pdf_url: str) -> datetime | None:
    match = re.search(
        r"Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{1,2})_(\d{2})(AM|PM)\.pdf",
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


@st.cache_data(ttl=300, show_spinner=False)
def fetch_latest_injury_report_pdf_url() -> str:
    response = requests.get(INJURY_REPORT_PAGE, timeout=30)
    response.raise_for_status()
    html = response.text

    pdf_urls = re.findall(
        r'https://ak-static\.cms\.nba\.com/referee/injury/Injury-Report_[^"]+\.pdf',
        html,
    )
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
2) Ensine o parser a reconhecer linha de time sozinha
Adicione este helper perto dos helpers do injury report:

TEAM_NAME_LOOKUP_NORM = {
    normalize_text(team["full_name"]): team["full_name"]
    for team in teams.get_teams()
}


def resolve_team_line(line: str) -> str:
    clean_line = str(line or "").replace("NOT YET SUBMITTED", "").strip()
    return TEAM_NAME_LOOKUP_NORM.get(normalize_text(clean_line), "")
Agora, dentro de fetch_latest_injury_report_df(), no loop for raw_line in lines:, adicione isso antes do player_match = PLAYER_STATUS_RE.search(line):

        resolved_team = resolve_team_line(line)
        if resolved_team:
            current_team = resolved_team
            if "NOT YET SUBMITTED" in line:
                current_row = None
            continue
Então esse trecho da função fica assim:

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("Game Date Game Time Matchup Team Player Name Current Status Reason"):
            continue

        game_match = GAME_PREFIX_RE.match(line)
        if game_match:
            if game_match.group("game_date"):
                current_game_date = game_match.group("game_date")
            current_game_time = game_match.group("game_time")
            current_matchup = game_match.group("matchup")
            line = game_match.group("rest").strip()

        resolved_team = resolve_team_line(line)
        if resolved_team:
            current_team = resolved_team
            if "NOT YET SUBMITTED" in line:
                current_row = None
            continue

        player_match = PLAYER_STATUS_RE.search(line)
        if player_match:
            player_name = " ".join(player_match.group("player").split())
            status = player_match.group("status").strip()
            reason = (player_match.group("reason") or "").strip()

            prefix = line[:player_match.start()].strip()
            if prefix:
                current_team = prefix

            current_row = {
                "GAME_DATE": current_game_date,
                "GAME_TIME_ET": current_game_time,
                "MATCHUP": current_matchup,
                "TEAM_NAME_IR": current_team,
                "PLAYER_NAME_IR": player_name,
                "PLAYER_KEY_IR": normalize_person_name(player_name),
                "INJ_STATUS": status,
                "INJ_REASON": reason,
                "INJ_REPORT_URL": pdf_url,
            }
            rows.append(current_row)
        else:
            if current_row is not None:
                extra = line.strip()
                if extra:
                    current_row["INJ_REASON"] = f'{current_row["INJ_REASON"]} {extra}'.strip()
3) Adicione um debug rápido para confirmar qual PDF está sendo carregado
Logo depois de:

injury_df = fetch_latest_injury_report_df()
coloque temporariamente:

if not injury_df.empty and "INJ_REPORT_URL" in injury_df.columns:
    valid_urls = injury_df["INJ_REPORT_URL"].dropna().astype(str)
    valid_urls = valid_urls[valid_urls.str.strip() != ""]
    if not valid_urls.empty:
        st.write("PDF carregado:", valid_urls.iloc[0])
Se o app mostrar um PDF anterior ao de 10:30 AM, matou a charada.

O que eu acho que está acontecendo no seu caso
O sintoma bate muito mais com:

app lendo PDF antigo
do que com:

merge errado de jogador

Porque na sua imagem do site oficial já existe GSW@WAS preenchido, e no app ele continua dizendo que não encontrou o matchup. Isso é muito cara de “peguei o report anterior”.

Resumo prático
Faça estas duas mudanças:

trocar a seleção do PDF mais recente para escolher pelo timestamp do nome do arquivo

reconhecer linhas de time isoladas no parser

Essas duas juntas devem destravar o Wizards.

Se você quiser, no próximo turno eu posso te mandar a versão completa pronta para colar de fetch_latest_injury_report_df() já consolidada com essas correções.


Imagem carregada
def parse_report_dt_from_url eu adiciono no lugar desse fetch_latest_injury?

Thought for 5s
Não. Não é no lugar.
Você vai adicionar parse_report_dt_from_url acima de fetch_latest_injury_report_pdf_url, e depois substituir o conteúdo da fetch_latest_injury_report_pdf_url pela versão nova.

Fica assim:

def parse_report_dt_from_url(pdf_url: str) -> datetime | None:
    match = re.search(
        r"Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{1,2})_(\d{2})(AM|PM)\.pdf",
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


@st.cache_data(ttl=300, show_spinner=False)
def fetch_latest_injury_report_pdf_url() -> str:
    response = requests.get(INJURY_REPORT_PAGE, timeout=30)
    response.raise_for_status()
    html = response.text

    pdf_urls = re.findall(
        r'https://ak-static\.cms\.nba\.com/referee/injury/Injury-Report_[^"]+\.pdf',
        html,
    )
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
    reader = PdfReader(BytesIO(pdf_bytes))
    lines: list[str] = []

    for page in reader.pages:
        text = page.extract_text() or ""
        page_lines = [clean_injury_pdf_line(x) for x in text.splitlines()]
        page_lines = [x for x in page_lines if x]
        lines.extend(page_lines)

    return lines


@st.cache_data(ttl=300, show_spinner=False)
def fetch_latest_injury_report_df() -> pd.DataFrame:
    pdf_url = fetch_latest_injury_report_pdf_url()
    if not pdf_url:
        return pd.DataFrame()

    response = requests.get(pdf_url, timeout=45)
    response.raise_for_status()

    lines = extract_pdf_text_lines(response.content)
    rows = []

    current_game_date = ""
    current_game_time = ""
    current_matchup = ""
    current_team = ""
    current_row = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("Game Date Game Time Matchup Team Player Name Current Status Reason"):
            continue

        game_match = GAME_PREFIX_RE.match(line)
        if game_match:
            if game_match.group("game_date"):
                current_game_date = game_match.group("game_date")
            current_game_time = game_match.group("game_time")
            current_matchup = game_match.group("matchup")
            line = game_match.group("rest").strip()

        if "NOT YET SUBMITTED" in line:
            current_row = None
            continue

        player_match = PLAYER_STATUS_RE.search(line)
        if player_match:
            player_name = " ".join(player_match.group("player").split())
            status = player_match.group("status").strip()
            reason = (player_match.group("reason") or "").strip()

            prefix = line[:player_match.start()].strip()
            if prefix:
                current_team = prefix

            current_row = {
                "GAME_DATE": current_game_date,
                "GAME_TIME_ET": current_game_time,
                "MATCHUP": current_matchup,
                "TEAM_NAME_IR": current_team,
                "PLAYER_NAME_IR": player_name,
                "PLAYER_KEY_IR": normalize_person_name(player_name),
                "INJ_STATUS": status,
                "INJ_REASON": reason,
                "INJ_REPORT_URL": pdf_url,
            }
            rows.append(current_row)
        else:
            if current_row is not None:
                extra = line.strip()
                if extra:
                    current_row["INJ_REASON"] = f'{current_row["INJ_REASON"]} {extra}'.strip()

    injury_df = pd.DataFrame(rows)
    if injury_df.empty:
        return injury_df

    injury_df["INJ_REASON"] = injury_df["INJ_REASON"].str.replace(r"\s+", " ", regex=True).str.strip()
    injury_df["INJ_STATUS"] = injury_df["INJ_STATUS"].fillna("—")
    return injury_df


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

    target_matchup = str(game_matchup or "").upper().replace(" ", "")
    roster_keys = set(enriched["PLAYER_KEY"].fillna("").astype(str).tolist())

    work_ir = injury_df.copy()
    work_ir["MATCHUP_NORM"] = (
        work_ir["MATCHUP"]
        .fillna("")
        .astype(str)
        .str.upper()
        .str.replace(" ", "", regex=False)
    )
    work_ir["PLAYER_KEY_IR"] = work_ir["PLAYER_KEY_IR"].fillna("").astype(str)

    if target_matchup:
        work_ir = work_ir[work_ir["MATCHUP_NORM"] == target_matchup].copy()

    if work_ir.empty:
        return enriched

    work_ir = work_ir[work_ir["PLAYER_KEY_IR"].isin(roster_keys)].copy()

    if work_ir.empty and "PLAYER_NAME_IR" in injury_df.columns:
        fallback_ir = injury_df.copy()
        fallback_ir["MATCHUP_NORM"] = (
            fallback_ir["MATCHUP"]
            .fillna("")
            .astype(str)
            .str.upper()
            .str.replace(" ", "", regex=False)
        )
        fallback_ir["PLAYER_KEY_IR"] = fallback_ir["PLAYER_NAME_IR"].fillna("").apply(normalize_person_name)

        if target_matchup:
            fallback_ir = fallback_ir[fallback_ir["MATCHUP_NORM"] == target_matchup].copy()

        work_ir = fallback_ir[fallback_ir["PLAYER_KEY_IR"].isin(roster_keys)].copy()

    if work_ir.empty:
        return enriched

    work_ir = work_ir.drop_duplicates(subset=["PLAYER_KEY_IR"], keep="last")

    # aqui sim o matchup foi encontrado; jogadores sem linha explícita viram Available
    enriched["INJ_STATUS"] = "Available"
    enriched["INJ_MATCHUP_FOUND"] = True

    merged = enriched.merge(
        work_ir[["PLAYER_KEY_IR", "INJ_STATUS", "INJ_REASON", "INJ_REPORT_URL"]],
        left_on="PLAYER_KEY",
        right_on="PLAYER_KEY_IR",
        how="left",
        suffixes=("", "_IR"),
    )

    merged["INJ_STATUS"] = merged["INJ_STATUS_IR"].fillna(merged["INJ_STATUS"])
    merged["INJ_REASON"] = merged["INJ_REASON_IR"].fillna(merged["INJ_REASON"])
    merged["INJ_REPORT_URL"] = merged["INJ_REPORT_URL_IR"].fillna(merged["INJ_REPORT_URL"])
    merged["IS_UNAVAILABLE"] = merged["INJ_STATUS"].isin(INACTIVE_STATUSES)

    drop_cols = [
        c for c in ["PLAYER_KEY_IR", "INJ_STATUS_IR", "INJ_REASON_IR", "INJ_REPORT_URL_IR", "MATCHUP_NORM"]
        if c in merged.columns
    ]
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


def run_api_call_with_retry(fetch_fn, endpoint_name: str, retries: int = 3, delay: float = 1.2):
    last_error = None
    for attempt in range(retries):
        try:
            return fetch_fn()
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    raise RuntimeError(f"Falha ao consultar {endpoint_name} após {retries} tentativas.") from last_error


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

    return {
        "projection": projection,
        "edge": edge,
        "label": classify_line_edge(edge),
        "line_value": active_line,
        "line_source": "BetMGM" if use_market else "Manual",
        "has_market_line": use_market,
        "over_dec": market_info.get("over_dec") if use_market else None,
        "under_dec": market_info.get("under_dec") if use_market else None,
        "updated_at": market_info.get("updated_at") if use_market else "",
        "hit_l10": format_ratio_text(hit_l10, len(recent_values)),
        "hit_l5": format_ratio_text(hit_l5, min(len(recent_values), 5)),
    }


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.1rem;
            padding-bottom: 2rem;
        }
        .main-title {
            font-size: 2.1rem;
            font-weight: 800;
            margin-bottom: 0.15rem;
        }
        .subtitle {
            color: #94a3b8;
            margin-bottom: 1rem;
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
        @media (max-width: 1200px) {
            .player-quick-grid {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
        }
        @media (max-width: 760px) {
            .ranking-row {
                grid-template-columns: 38px 1.5fr 0.8fr 0.8fr;
            }
            .ranking-row .ranking-stat:last-child {
                display: none;
            }
            .micro-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .player-quick-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .detail-mini-grid {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=300, show_spinner=False)
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


@st.cache_data(ttl=21600, show_spinner=False)
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


@st.cache_data(ttl=3600, show_spinner=False)
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


@st.cache_data(ttl=3600, show_spinner=False)
def get_player_log(player_id: int, season: str) -> pd.DataFrame:
    response = run_api_call_with_retry(
        lambda: playergamelog.PlayerGameLog(
            player_id=player_id,
            season=season,
            season_type_all_star="Regular Season",
            timeout=45,
        ),
        endpoint_name="PlayerGameLog",
    )
    frames = response.get_data_frames()
    if not frames:
        return pd.DataFrame()

    df = frames[0].copy()
    if df.empty:
        return df

    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
    return df.sort_values("GAME_DATE", ascending=False)


@st.cache_data(ttl=3600, show_spinner=False)
def get_team_player_logs(team_id: int, season: str) -> pd.DataFrame:
    response = run_api_call_with_retry(
        lambda: playergamelogs.PlayerGameLogs(
            team_id_nullable=team_id,
            season_nullable=season,
            season_type_nullable="Regular Season",
            timeout=45,
        ),
        endpoint_name="PlayerGameLogs",
    )
    frames = response.get_data_frames()
    if not frames:
        return pd.DataFrame()

    df = frames[0].copy()
    if df.empty:
        return df

    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
    for col in ["PTS", "REB", "AST", "MIN", "FG3M", "FGA", "FG3A"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0
    df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
    return df.sort_values(["PLAYER_ID", "GAME_DATE"], ascending=[True, False])


@st.cache_data(ttl=7200, show_spinner=False)
def get_position_opponent_profile(season: str, opponent_team_id: int, position_group: str) -> dict:
    def fetch(position_value: str, opponent_value: int) -> pd.DataFrame:
        try:
            response = run_api_call_with_retry(
                lambda: leaguedashplayerstats.LeagueDashPlayerStats(
                    season=season,
                    season_type_all_star="Regular Season",
                    per_mode_detailed="PerGame",
                    measure_type_detailed_defense="Base",
                    last_n_games=0,
                    month=0,
                    opponent_team_id=opponent_value,
                    pace_adjust="N",
                    plus_minus="N",
                    rank="N",
                    period=0,
                    team_id_nullable="",
                    player_position_abbreviation_nullable=position_value,
                    timeout=45,
                ),
                endpoint_name=f"LeagueDashPlayerStats {position_value}",
                retries=2,
                delay=1.5,
            )
        except Exception:
            return pd.DataFrame()

        frames = response.get_data_frames()
        if not frames:
            return pd.DataFrame()
        return frames[0].copy()

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

    for event in events:
        teams_payload = event.get("teams", {})
        event_home = normalize_text(teams_payload.get("home", {}).get("names", {}).get("long", ""))
        event_away = normalize_text(teams_payload.get("away", {}).get("names", {}).get("long", ""))
        if event_home == target_home and event_away == target_away:
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
        key = normalize_text(player_name)

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
    for _, cols in ODDS_METRIC_COLUMNS.items():
        for col in cols:
            if col not in enriched.columns:
                enriched[col] = None

    if odds_df.empty:
        return enriched

    merged = enriched.merge(odds_df, left_on="PLAYER_KEY", right_on="PLAYER_KEY_ODDS", how="left")
    for col in ["PLAYER_KEY_ODDS", "PLAYER_NAME_ODDS"]:
        if col in merged.columns:
            merged = merged.drop(columns=[col])
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
            stat2_label, stat2_value = "Hit", row["RANK_HIT_TEXT"]
            stat3_label, stat3_value = "Match", row["MATCHUP_LABEL"]
            stat3_class = "ranking-good" if row["MATCHUP_LABEL"] == "Favorável" else ("ranking-bad" if row["MATCHUP_LABEL"] == "Difícil" else "")
        elif mode == "edge":
            stat1_label, stat1_value = "Edge", format_signed_number(row["RANK_EDGE"])
            stat2_label, stat2_value = "Proj", format_number(row["RANK_PROJ"])
            stat3_label, stat3_value = "Linha", format_number(row["RANK_LINE"])
            stat3_class = ""
        else:
            stat1_label, stat1_value = "Hit", row["RANK_HIT_TEXT"]
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
    hit_text_col = get_metric_hit_text_column(line_metric)
    hit_rate_col = get_metric_hit_rate_column(line_metric)

    rank_df = combined.copy()
    rank_df["RANK_PROJ"] = pd.to_numeric(rank_df[projection_col], errors="coerce").fillna(0.0)
    rank_df["RANK_HIT_TEXT"] = rank_df[hit_text_col].fillna("-")
    rank_df["RANK_HIT_RATE"] = pd.to_numeric(rank_df[hit_rate_col], errors="coerce").fillna(0.0)
    rank_df["RANK_LINE"] = rank_df.apply(lambda row: get_line_context(row, line_metric, line_value, use_market_line=use_market_line)["line_value"], axis=1)
    rank_df["RANK_EDGE"] = rank_df["RANK_PROJ"] - rank_df["RANK_LINE"]

    proj_df = rank_df.sort_values(["RANK_PROJ", "RANK_HIT_RATE"], ascending=[False, False]).head(5)
    edge_df = rank_df.sort_values(["RANK_EDGE", "RANK_HIT_RATE"], ascending=[False, False]).head(5)
    consistency_df = rank_df.sort_values(["RANK_HIT_RATE", "OSC_L10", "RANK_PROJ"], ascending=[False, True, False]).head(5)

    st.subheader(f"Ranking do confronto — {line_metric}")
    st.caption("Bloco compacto para bater o olho rápido, usando BetMGM quando houver linha disponível.")
    tab_proj, tab_edge, tab_cons = st.tabs(["Projeção", "Edge da linha", "Consistência"])

    with tab_proj:
        st.markdown(render_compact_ranking_html(proj_df, mode="projection"), unsafe_allow_html=True)
    with tab_edge:
        st.markdown(render_compact_ranking_html(edge_df, mode="edge"), unsafe_allow_html=True)
    with tab_cons:
        st.markdown(render_compact_ranking_html(consistency_df, mode="consistency"), unsafe_allow_html=True)


def render_player_chart(player_name: str, player_id: int, season: str, chart_mode: str) -> None:
    log = get_player_log(player_id, season)
    if log.empty:
        st.info("Sem histórico suficiente para esse jogador.")
        return

    needed_cols = ["GAME_DATE", "PTS", "REB", "AST", "FGM", "FG3M", "FG3A"]
    if "MATCHUP" in log.columns:
        needed_cols.append("MATCHUP")

    recent = log[needed_cols].copy()
    recent = recent.dropna(subset=["GAME_DATE", "PTS", "REB", "AST"]).sort_values("GAME_DATE")
    if recent.empty:
        st.info("Sem histórico suficiente para esse jogador.")
        return

    recent["PRA"] = recent["PTS"] + recent["REB"] + recent["AST"]
    recent["3PTM"] = recent["FG3M"]
    recent["3PTA"] = recent["FG3A"]
    recent["FG"] = recent["FGM"]

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
        st.caption("Visual compacto: barras, últimos 5 jogos." if chart_mode == "Compacto" else "Visual completo: linhas, últimos 10 jogos.")

    if chart_mode == "Compacto":
        metric = st.radio(
            "Métrica do gráfico",
            ["PRA", "PTS", "REB", "AST", "3PTM", "3PTA", "FG"],
            horizontal=True,
            key=f"metric_chart_{player_id}_{chart_mode}",
        )
        recent_view = recent.tail(5).copy()

        fig = go.Figure(
            go.Bar(
                x=recent_view["SHORT_LABEL"],
                y=recent_view[metric],
                text=recent_view[metric].round(1),
                textposition="outside",
                marker=dict(color="#4ade80"),
                hovertemplate=f"{metric}: %{{y:.1f}}<extra></extra>",
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
        )
        fig.update_xaxes(title="", type="category", tickangle=0, showgrid=False, tickfont=dict(size=11))
        fig.update_yaxes(title="", showgrid=True, gridcolor="rgba(148,163,184,0.15)", zeroline=False)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"{metric} • Temp: {recent[metric].mean():.1f} | L5: {recent_view[metric].mean():.1f}")
    else:
        recent_view = recent.tail(10).copy()
        fig = go.Figure()
        for name, color, width, opacity in [
            ("PRA", "#8b5cf6", 4, 1.0),
            ("PTS", "#38bdf8", 2.2, 0.8),
            ("REB", "#34d399", 2.2, 0.8),
            ("AST", "#f59e0b", 2.2, 0.8),
        ]:
            fig.add_trace(
                go.Scatter(
                    x=recent_view["SHORT_LABEL"],
                    y=recent_view[name],
                    mode="lines+markers",
                    name=name,
                    line=dict(width=width, color=color),
                    opacity=opacity,
                    hovertemplate=f"{name}: %{{y:.1f}}<extra></extra>",
                )
            )

        fig.update_layout(
            template="plotly_dark",
            height=400,
            margin=dict(l=20, r=20, t=10, b=20),
            legend=dict(orientation="h", y=1.08, x=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(15,23,42,0.35)",
            hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155", font=dict(color="#f8fafc", size=13)),
        )
        fig.update_xaxes(title="", type="category", tickangle=0, showgrid=False, tickfont=dict(size=11))
        fig.update_yaxes(title="")
        st.plotly_chart(fig, use_container_width=True)


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
                <div class="quick-stat-meta">Proj {format_number(line_context['projection'])} vs {format_number(line_context['line_value'])} • L10 {line_context['hit_l10']}{odds_meta}</div>
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
                <div class="detail-mini-label">Hit L10</div>
                <div class="detail-mini-value">{line_context['hit_l10']}</div>
            </div>
        </div>
        <div class="hero-note">{odds_note}</div>
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


def render_player_focus_panel(row: pd.Series, line_metric: str, line_value: float, use_market_line: bool, season: str, chart_mode: str) -> None:
    st.markdown('<div class="focus-shell">', unsafe_allow_html=True)
    top_left, top_right = st.columns([1, 5])
    with top_left:
        st.image(get_player_headshot_url(int(row["PLAYER_ID"])), width=92)
    with top_right:
        st.markdown(f'<div class="focus-title">{row["PLAYER"]}</div>', unsafe_allow_html=True)
        position = row["POSITION"] if str(row["POSITION"]).strip() else "-"
        st.markdown(
            f'<div class="focus-sub">Pos {position} • GP {int(row["SEASON_GP"])} • MIN {format_number(row["SEASON_MIN"])} • Time {row["TEAM_NAME"]}</div>',
            unsafe_allow_html=True,
        )
        render_badges(row["ROLE"], row.get("FORM_SIGNAL", "→ Estável"), row.get("OSC_CLASS", "-"), row.get("MATCHUP_LABEL", "Neutro"))
        render_focus_summary_tiles(row, line_metric, line_value, use_market_line)

    overview_tab, detail_tab, chart_tab = st.tabs(["Resumo", "Detalhamento", "Gráfico"])

    with overview_tab:
        render_player_support_tiles(row, line_metric, line_value, use_market_line)
        st.markdown(render_projection_detail_box_html(row), unsafe_allow_html=True)
        st.markdown(render_manual_line_detail_box_html(row, line_metric, line_value, use_market_line), unsafe_allow_html=True)

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
                st.markdown(render_detail_metric_box_html(item[0], item[1], item[2], item[3]), unsafe_allow_html=True)

        extra_cols = st.columns(3)
        extra_detail_items = [
            ("3PM", row["SEASON_3PM"], row["L5_3PM"], row["L10_3PM"]),
            ("FGA", row["SEASON_FGA"], row["L5_FGA"], row["L10_FGA"]),
            ("3PA", row["SEASON_3PA"], row["L5_3PA"], row["L10_3PA"]),
        ]
        for col, item in zip(extra_cols, extra_detail_items):
            with col:
                st.markdown(render_detail_metric_box_html(item[0], item[1], item[2], item[3]), unsafe_allow_html=True)

        st.markdown(render_matchup_detail_box_html(row), unsafe_allow_html=True)

    with chart_tab:
        render_player_chart(row["PLAYER"], int(row["PLAYER_ID"]), season, chart_mode)

    st.markdown('</div>', unsafe_allow_html=True)


def render_player_card(row: pd.Series, line_metric: str, line_value: float, use_market_line: bool) -> None:
    with st.container(border=True):
        top_left, top_right = st.columns([1, 4])

        with top_left:
            st.image(get_player_headshot_url(int(row["PLAYER_ID"])), width=72)

        with top_right:
            st.markdown(f"**{row['PLAYER']}**")
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

    st.markdown('<div class="main-title">NBA Dashboard MVP</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtitle">Jogos do dia com leitura de PTS, REB, AST e PRA em cards ou tabela, agora com linha manual e BetMGM quando disponível.</div>',
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Configurações")
        selected_date = get_brasilia_today()
        st.caption(f"Jogos do dia em Brasília • {selected_date.strftime('%d/%m/%Y')}")

        st.divider()
        chart_mode = st.radio("Modo do gráfico", CHART_OPTIONS, index=0)
        cards_per_row = st.select_slider("Cards por linha", options=[1, 2], value=2)

        st.divider()
        st.subheader("Filtros")
        min_games = st.slider("Mínimo de jogos na temporada", 0, 82, 5, 1)
        min_minutes = st.slider("Mínimo de minutos por jogo", 0, 40, 15, 1)
        role_filter = st.selectbox("Mostrar jogadores", ROLE_OPTIONS, index=0)

        st.divider()
        st.subheader("Linha")
        line_metric = st.selectbox("Métrica da linha", LINE_METRIC_OPTIONS, index=0)
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

        st.divider()
        st.subheader("Ordenação")
        sort_labels = list(SORT_OPTIONS.keys())
        sort_label = st.selectbox("Ordenar jogadores por", options=sort_labels, index=sort_labels.index("PRA L10"))
        ascending = st.toggle("Ordem crescente", value=False)

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
        away_df = build_team_table(int(selected_game["VISITOR_TEAM_ID"]), season)
        home_df = build_team_table(int(selected_game["HOME_TEAM_ID"]), season)

        away_df = enrich_team_with_context(
            team_df=away_df,
            team_id=int(selected_game["VISITOR_TEAM_ID"]),
            opponent_team_id=int(selected_game["HOME_TEAM_ID"]),
            opponent_team_name=selected_game["home_team_name"],
            season=season,
        )
        home_df = enrich_team_with_context(
            team_df=home_df,
            team_id=int(selected_game["HOME_TEAM_ID"]),
            opponent_team_id=int(selected_game["VISITOR_TEAM_ID"]),
            opponent_team_name=selected_game["away_team_name"],
            season=season,
        )
    except Exception as exc:
        st.error("A NBA demorou ou falhou ao responder nas estatísticas do confronto. Tente novamente em alguns segundos ou use o botão de atualização.")
        st.exception(exc)
        return

    away_df["TEAM_NAME"] = selected_game["away_team_name"]
    home_df["TEAM_NAME"] = selected_game["home_team_name"]

    odds_df = pd.DataFrame()
    if api_key_available:
        try:
            odds_events = fetch_nba_odds_events()
            selected_odds_event = find_matching_odds_event(
                odds_events,
                home_team_name=selected_game["home_team_name"],
                away_team_name=selected_game["away_team_name"],
            )
            odds_df = extract_betmgm_player_props(selected_odds_event)
        except Exception:
            odds_df = pd.DataFrame()

    away_df = merge_betmgm_odds(away_df, odds_df)
    home_df = merge_betmgm_odds(home_df, odds_df)

    try:
        injury_df = fetch_latest_injury_report_df()
    except Exception:
        injury_df = pd.DataFrame()

    game_matchup = f"{TEAM_ABBR_LOOKUP[int(selected_game['VISITOR_TEAM_ID'])]}@{TEAM_ABBR_LOOKUP[int(selected_game['HOME_TEAM_ID'])]}"

    away_df = merge_injury_report(
        away_df,
        injury_df,
        selected_game["away_team_name"],
        int(selected_game["VISITOR_TEAM_ID"]),
        game_matchup=game_matchup,
    )

    home_df = merge_injury_report(
        home_df,
        injury_df,
        selected_game["home_team_name"],
        int(selected_game["HOME_TEAM_ID"]),
        game_matchup=game_matchup,
    )

    render_matchup_header(selected_game)
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

    tab1, tab2 = st.tabs([selected_game["away_team_name"], selected_game["home_team_name"]])

    with tab1:
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

    with tab2:
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

    summary_df, detail_df = build_display_dataframes(filtered_df)

    st.markdown(f"### {team_name}")
    st.markdown(
        f'<div><span class="info-pill">Jogadores: {len(filtered_df)}</span><span class="info-pill">Linha ativa: {line_metric} {format_number(line_value)}</span><span class="info-pill">Modo mercado: {"BetMGM quando houver" if use_market_line else "Manual"}</span></div>',
        unsafe_allow_html=True,
    )

    cards_tab, table_tab, injury_tab, lineup_tab = st.tabs(["Cards", "Tabela", "Injury Report", "Provável Escalação"])

    with cards_tab:
        st.markdown(
            '<div class="section-note">Cards curtos no topo e painel fixo do jogador abaixo para facilitar consulta no celular.</div>',
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
            key=f"player_focus_v2_{team_name}_{chart_mode}_{line_metric}",
        )
        selected_row = filtered_df.loc[filtered_df["PLAYER"] == player_name].iloc[0]
        render_player_focus_panel(selected_row, line_metric, line_value, use_market_line, season, chart_mode)

    with table_tab:
        st.markdown(
            '<div class="section-note">Visão de comparação rápida do elenco, sem abrir card por card feito investigador privado.</div>',
            unsafe_allow_html=True,
        )
        mode = st.radio(
            f"Modo da tabela — {team_name}",
            ["Resumo", "Completa"],
            horizontal=True,
            key=f"table_mode_{team_name}",
        )
        if mode == "Resumo":
            st.dataframe(style_table(summary_df, quick_view=True), use_container_width=True)
        else:
            st.dataframe(style_table(detail_df, quick_view=False), use_container_width=True)

    with injury_tab:
        render_injury_report_tab(team_df, team_name)

    with lineup_tab:
        render_lineup_report_tab(team_df, team_name)

if __name__ == "__main__":
    main()

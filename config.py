import re
import unicodedata
from zoneinfo import ZoneInfo
from nba_api.stats.static import teams

# ==========================================
# 1. CORES DOS TIMES (UI/UX)
# ==========================================
NBA_TEAM_COLORS = {
    'ATL': {'primary': '#E03A3E', 'secondary': '#C1D32F', 'name': 'Hawks'},
    'BOS': {'primary': '#007A33', 'secondary': '#BA9653', 'name': 'Celtics'},
    'BKN': {'primary': '#000000', 'secondary': '#FFFFFF', 'name': 'Nets'},
    'CHA': {'primary': '#1D1160', 'secondary': '#00788C', 'name': 'Hornets'},
    'CHI': {'primary': '#CE1141', 'secondary': '#000000', 'name': 'Bulls'},
    'CLE': {'primary': '#6F263D', 'secondary': '#FFB81C', 'name': 'Cavaliers'},
    'DAL': {'primary': '#00538C', 'secondary': '#002B5E', 'name': 'Mavericks'},
    'DEN': {'primary': '#0E2240', 'secondary': '#FEC524', 'name': 'Nuggets'},
    'DET': {'primary': '#C8102E', 'secondary': '#1D428A', 'name': 'Pistons'},
    'GSW': {'primary': '#1D428A', 'secondary': '#FFC72C', 'name': 'Warriors'},
    'HOU': {'primary': '#CE1141', 'secondary': '#000000', 'name': 'Rockets'},
    'IND': {'primary': '#002D62', 'secondary': '#FDBB30', 'name': 'Pacers'},
    'LAC': {'primary': '#C8102E', 'secondary': '#1D428A', 'name': 'Clippers'},
    'LAL': {'primary': '#552583', 'secondary': '#FDB927', 'name': 'Lakers'},
    'MEM': {'primary': '#5D76A9', 'secondary': '#12173F', 'name': 'Grizzlies'},
    'MIA': {'primary': '#98002E', 'secondary': '#F9A01B', 'name': 'Heat'},
    'MIL': {'primary': '#00471B', 'secondary': '#EEE1C6', 'name': 'Bucks'},
    'MIN': {'primary': '#0C2340', 'secondary': '#236192', 'name': 'Timberwolves'},
    'NOP': {'primary': '#0C2340', 'secondary': '#C8102E', 'name': 'Pelicans'},
    'NYK': {'primary': '#006BB6', 'secondary': '#F58426', 'name': 'Knicks'},
    'OKC': {'primary': '#007AC1', 'secondary': '#EF3B24', 'name': 'Thunder'},
    'ORL': {'primary': '#0077C0', 'secondary': '#C4CED4', 'name': 'Magic'},
    'PHI': {'primary': '#006BB6', 'secondary': '#ED174C', 'name': '76ers'},
    'PHX': {'primary': '#1D1060', 'secondary': '#E56020', 'name': 'Suns'},
    'POR': {'primary': '#E03A3E', 'secondary': '#000000', 'name': 'Trail Blazers'},
    'SAC': {'primary': '#5A2D81', 'secondary': '#63727A', 'name': 'Kings'},
    'SAS': {'primary': '#C4CED4', 'secondary': '#000000', 'name': 'Spurs'},
    'TOR': {'primary': '#CE1141', 'secondary': '#000000', 'name': 'Raptors'},
    'UTA': {'primary': '#002B5C', 'secondary': '#00471B', 'name': 'Jazz'},
    'WAS': {'primary': '#002B5C', 'secondary': '#E31837', 'name': 'Wizards'}
}

# ==========================================
# 2. DADOS ESTÁTICOS DA NBA E URLs
# ==========================================
TEAM_LOOKUP = {team["id"]: team for team in teams.get_teams()}
TEAM_ABBR_LOOKUP = {team["id"]: team.get("abbreviation", "") for team in teams.get_teams()}

TEAM_LOGO_URL = "https://cdn.nba.com/logos/nba/{team_id}/primary/L/logo.svg"
PLAYER_HEADSHOT_URL = "https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"

# ==========================================
# 3. OPÇÕES DE MENUS E FILTROS DO APP
# ==========================================
SORT_OPTIONS = {
    "PRA L10": "L10_PRA",
    "PTS L10": "L10_PTS",
    "REB L10": "L10_REB",
    "AST L10": "L10_AST",
    "3PM L10": "L10_3PM",
    "FGA L10": "L10_FGA",
    "3PA L10": "L10_3PA",
    "Δ PRA L10 vs Temp": "DELTA_PRA_L10",
    "PRA L5": "L5_PRA",
    "Δ PRA L5 vs Temp": "DELTA_PRA_L5",
    "PRA temporada": "SEASON_PRA",
    "Minutos por jogo": "SEASON_MIN",
    "Jogos na temporada": "SEASON_GP",
    "Nome do jogador": "PLAYER",
}
ROLE_OPTIONS = ["Todos", "Titular provável", "Reserva"]
VIEW_OPTIONS = ["Cards", "Tabela"]
CHART_OPTIONS = ["Compacto", "Completo"]
LINE_METRIC_OPTIONS = ["PRA", "PTS", "REB", "AST", "3PM", "FGA", "3PA"]

# ==========================================
# 4. PESOS E CONFIGURAÇÕES DE CÁLCULO
# ==========================================
PROJECTION_WEIGHTS = {
    "season": 0.35,
    "l10": 0.40,
    "l5": 0.15,
    "matchup": 0.10,
}

# ==========================================
# 5. CONFIGURAÇÕES DA API DE ODDS (BetMGM)
# ==========================================
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

# ==========================================
# 6. CONFIGURAÇÕES DO INJURY REPORT E REGEX
# ==========================================
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

# ==========================================
# 7. TIMEZONES (FUSOS HORÁRIOS)
# ==========================================
APP_TIMEZONE = ZoneInfo("America/Sao_Paulo")
EASTERN_TIMEZONE = ZoneInfo("America/New_York")
UTC_TIMEZONE = ZoneInfo("UTC")

# ==========================================
# 8. UTILITÁRIOS BASEADOS NAS CONSTANTES
# ==========================================
def _normalize_text(value: str) -> str:
    """Função interna para ajudar a criar o dicionário de busca de times."""
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace(".", " ").replace("-", " ").replace("'", "").replace(",", " ")
    return " ".join(text.split())

TEAM_NAME_LOOKUP_NORM = {
    _normalize_text(team["full_name"]): team["full_name"]
    for team in teams.get_teams()
}

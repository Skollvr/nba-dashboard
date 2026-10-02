import pandas as pd
import numpy as np
import streamlit as st

# 1. Configurações Estáticas
from config import (
    PROJECTION_WEIGHTS, ROLE_OPTIONS, TEAM_LOOKUP, TEAM_ABBR_LOOKUP,
    INACTIVE_STATUSES, ODDS_METRIC_COLUMNS, ODDS_BOOKMAKER, ODDS_STAT_MAP
)

# 2. API da NBA
from api_nba import (
    get_team_roster, get_league_player_stats, get_team_player_logs,
    get_league_player_logs, get_position_allowed_profile,
    get_league_position_baseline
)

# 3. API de Odds
from api_odds import (
    normalize_text, normalize_person_name, fetch_nba_odds_events,
    find_matching_odds_event, extract_betmgm_player_props
)

# 4. Leitor de PDF (Lesões)
from pdf_reader import (
    fetch_latest_injury_report_df, parse_injury_report_timestamp_from_url
)

# ---------------------------------------------------------
# 1. FUNÇÕES AUXILIARES DE FORMATAÇÃO E CLASSIFICAÇÃO
# ---------------------------------------------------------
def format_ratio_text(numerator: int, denominator: int) -> str:
    if denominator <= 0: return "-"
    return f"{int(numerator)}/{int(denominator)}"

def normalize_position_group(position: str) -> str:
    pos = str(position or "").upper().strip()
    if not pos: return "F"
    primary = pos.split("-")[0].strip()
    if primary in {"G", "F", "C"}: return primary
    if "G" in pos: return "G"
    if "F" in pos: return "F"
    if "C" in pos: return "C"
    return "F"

def classify_oscillation(value: float) -> str:
    if value <= 4.5: return "Baixa"
    if value <= 7.5: return "Média"
    return "Alta"

def classify_form_signal(slope: float) -> str:
    if slope >= 1.0: return "↗ Em alta"
    if slope <= -1.0: return "↘ Em queda"
    return "→ Estável"

def classify_matchup_tier_by_metric(metric: str, diff_value: float) -> str:
    thresholds = {
        "PTS": 1.5,
        "REB": 1.0,
        "AST": 0.8,
        "PRA": 2.0,
        "3PM": 0.4,
        "FGA": 1.2,
        "3PA": 0.8,
    }
    t = thresholds.get(metric, 1.2)

    if diff_value >= t:
        return "Favorável"
    if diff_value <= -t:
        return "Difícil"
    return "Neutro"
def classify_matchup_tier_by_metric(metric: str, diff_value: float) -> str:
    thresholds = {
        "PTS": 1.5,
        "REB": 1.0,
        "AST": 0.8,
        "PRA": 2.0,
        "3PM": 0.4,
        "FGA": 1.2,
        "3PA": 0.8,
    }
    t = thresholds.get(metric, 1.2)

    if diff_value >= t:
        return "Favorável"
    if diff_value <= -t:
        return "Difícil"
    return "Neutro"

def classify_line_edge(edge: float) -> str:
    if edge >= 1.5: return "Acima"
    if edge <= -1.5: return "Abaixo"
    return "Justa"

def get_matchup_chip_class(label: str) -> str:
    if label == "Favorável": return "matchup-good"
    if label == "Difícil": return "matchup-bad"
    return "matchup-neutral"

def classify_trend(delta_pra: float) -> str:
    """Classifica a tendência baseada na diferença entre L10 e média da temporada."""
    if delta_pra >= 2.0:
        return "🔥 Alta"
    if delta_pra <= -2.0:
        return "⬇️ Fraca"
    return "➖ Neutra"
    
# ---------------------------------------------------------
# 2. COLUNAS E MAPEAMENTOS
# ---------------------------------------------------------
def get_metric_projection_column(metric: str) -> str:
    return {"PRA": "PROJ_PRA", "PTS": "PROJ_PTS", "REB": "PROJ_REB", "AST": "PROJ_AST", "3PM": "PROJ_3PM", "FGA": "PROJ_FGA", "3PA": "PROJ_3PA"}.get(metric, "PROJ_PRA")
def get_metric_allowed_column(metric: str) -> str:
    return {
        "PTS": "OPP_PTS_ALLOWED",
        "REB": "OPP_REB_ALLOWED",
        "AST": "OPP_AST_ALLOWED",
        "PRA": "OPP_PRA_ALLOWED",
        "3PM": "OPP_3PM_ALLOWED",
        "FGA": "OPP_FGA_ALLOWED",
        "3PA": "OPP_3PA_ALLOWED",
    }.get(metric, "OPP_PRA_ALLOWED")


def get_metric_baseline_column(metric: str) -> str:
    return {
        "PTS": "LEAGUE_PTS_BASELINE",
        "REB": "LEAGUE_REB_BASELINE",
        "AST": "LEAGUE_AST_BASELINE",
        "PRA": "LEAGUE_PRA_BASELINE",
        "3PM": "LEAGUE_3PM_BASELINE",
        "FGA": "LEAGUE_FGA_BASELINE",
        "3PA": "LEAGUE_3PA_BASELINE",
    }.get(metric, "LEAGUE_PRA_BASELINE")


def get_metric_matchup_diff_column(metric: str) -> str:
    return {
        "PTS": "MATCHUP_DIFF_PTS",
        "REB": "MATCHUP_DIFF_REB",
        "AST": "MATCHUP_DIFF_AST",
        "PRA": "MATCHUP_DIFF_PRA",
        "3PM": "MATCHUP_DIFF_3PM",
        "FGA": "MATCHUP_DIFF_FGA",
        "3PA": "MATCHUP_DIFF_3PA",
    }.get(metric, "MATCHUP_DIFF_PRA")


def get_metric_matchup_label_column(metric: str) -> str:
    return {
        "PTS": "MATCHUP_LABEL_PTS",
        "REB": "MATCHUP_LABEL_REB",
        "AST": "MATCHUP_LABEL_AST",
        "PRA": "MATCHUP_LABEL_PRA",
        "3PM": "MATCHUP_LABEL_3PM",
        "FGA": "MATCHUP_LABEL_FGA",
        "3PA": "MATCHUP_LABEL_3PA",
    }.get(metric, "MATCHUP_LABEL_PRA")


def get_metric_matchup_context(row: pd.Series, metric: str) -> dict:
    allowed_col = get_metric_allowed_column(metric)
    baseline_col = get_metric_baseline_column(metric)
    diff_col = get_metric_matchup_diff_column(metric)
    label_col = get_metric_matchup_label_column(metric)

    return {
        "allowed": float(pd.to_numeric(row.get(allowed_col, 0.0), errors="coerce") or 0.0),
        "baseline": float(pd.to_numeric(row.get(baseline_col, 0.0), errors="coerce") or 0.0),
        "diff": float(pd.to_numeric(row.get(diff_col, 0.0), errors="coerce") or 0.0),
        "label": str(row.get(label_col, "Neutro")),
        "allowed_col": allowed_col,
        "baseline_col": baseline_col,
        "diff_col": diff_col,
        "label_col": label_col,
    }

def get_metric_recent_list_column(metric: str) -> str:
    return {"PRA": "RECENT_PRA_L10", "PTS": "RECENT_PTS_L10", "REB": "RECENT_REB_L10", "AST": "RECENT_AST_L10", "3PM": "RECENT_3PM_L10", "FGA": "RECENT_FGA_L10", "3PA": "RECENT_3PA_L10"}.get(metric, "RECENT_PRA_L10")

def get_metric_market_columns(metric: str) -> tuple:
    return ODDS_METRIC_COLUMNS.get(metric, ("", "", "", ""))
    
def get_metric_boxscore_column(metric: str) -> str:
    return {
        "PRA": "PRA",
        "PTS": "PTS",
        "REB": "REB",
        "AST": "AST",
        "3PM": "FG3M",
        "FGA": "FGA",
        "3PA": "FG3A",
    }.get(metric, "PRA")


def safe_rate(stat_value: float, minutes_value: float) -> float:
    try:
        stat_value = float(stat_value)
        minutes_value = float(minutes_value)
        if minutes_value <= 0:
            return 0.0
        return stat_value / minutes_value
    except Exception:
        return 0.0


def blend_rate(season_pm: float, l10_pm: float, l5_pm: float) -> float:
    return (
        0.50 * float(season_pm)
        + 0.30 * float(l10_pm)
        + 0.20 * float(l5_pm)
    )


def project_minutes_v1(
    season_min: float,
    l10_min: float,
    l5_min: float,
    role: str,
    inj_status: str = "Available",
) -> float:
    proj = (
        0.55 * float(season_min)
        + 0.30 * float(l10_min)
        + 0.15 * float(l5_min)
    )

    role = str(role or "")
    inj_status = str(inj_status or "Available")

    if role == "Titular provável":
        proj += 1.0
    elif role == "Reserva":
        proj -= 0.8

    if inj_status == "Questionable":
        proj -= 1.5
    elif inj_status in {"Doubtful", "Out"}:
        proj = 0.0

    return max(0.0, proj)

def get_metric_matchup_scale(metric: str) -> float:
    return {
        "PTS": 1.0,
        "REB": 0.7,
        "AST": 0.5,
        "PRA": 1.5,
        "3PM": 0.25,
        "FGA": 0.8,
        "3PA": 0.5,
    }.get(metric, 0.8)


def clamp_value(value: float, min_value: float, max_value: float) -> float:
    try:
        value = float(value)
    except Exception:
        return min_value
    return max(min_value, min(max_value, value))


def classify_matchup_score_label(score: float) -> str:
    if score >= 0.75:
        return "Muito favorável"
    if score >= 0.25:
        return "Favorável"
    if score <= -0.75:
        return "Muito difícil"
    if score <= -0.25:
        return "Difícil"
    return "Neutro"


def build_context_adj_v1(row: pd.Series) -> float:
    score = 0.0

    role = str(row.get("ROLE", ""))
    inj_status = str(row.get("INJ_STATUS", "Available"))
    form_signal = str(row.get("FORM_SIGNAL", "→ Estável"))

    if role == "Titular provável":
        score += 0.10
    elif role == "Reserva":
        score -= 0.05

    if "↗" in form_signal:
        score += 0.05
    elif "↘" in form_signal:
        score -= 0.05

    if inj_status == "Questionable":
        score -= 0.15
    elif inj_status in {"Doubtful", "Out"}:
        score -= 1.00

    return clamp_value(score, -1.0, 1.0)

def get_market_line_for_metric(row: pd.Series, metric: str) -> dict:
    line_col, over_col, under_col, updated_col = get_metric_market_columns(metric)
    return {
        "line": row.get(line_col),
        "over_dec": row.get(over_col),
        "under_dec": row.get(under_col),
        "updated_at": row.get(updated_col),
    }

# ---------------------------------------------------------
# 3. MATEMÁTICA E CONTEXTO DE LINHA
# ---------------------------------------------------------
def calculate_projection(season_value: float, l10_value: float, l5_value: float, opp_allowed: float, league_allowed: float) -> float:
    matchup_adjusted = float(season_value) + (float(opp_allowed) - float(league_allowed))
    projection = (
        PROJECTION_WEIGHTS["season"] * float(season_value)
        + PROJECTION_WEIGHTS["l10"] * float(l10_value)
        + PROJECTION_WEIGHTS["l5"] * float(l5_value)
        + PROJECTION_WEIGHTS["matchup"] * matchup_adjusted
    )
    return max(0.0, projection)

def get_line_context(row: pd.Series, metric: str, line_value: float | None, use_market_line: bool = False) -> dict:
    projection_col = get_metric_projection_column(metric)
    projection_v1_col = f"PROJ_{metric}_V1"
    recent_list_col = get_metric_recent_list_column(metric)

    # Prefer the context-aware V1 projection when available. It incorporates
    # projected minutes, recent rate, matchup and injury status.
    projection_raw = row.get(projection_v1_col, row.get(projection_col, 0.0))
    projection_num = pd.to_numeric(projection_raw, errors="coerce")
    projection = float(projection_num) if pd.notna(projection_num) else 0.0

    market_info = get_market_line_for_metric(row, metric)
    market_line = pd.to_numeric(market_info.get("line"), errors="coerce")
    use_market = bool(use_market_line and pd.notna(market_line))

    manual_col = f"MANUAL_LINE_{metric}"
    manual_line = pd.to_numeric(row.get(manual_col), errors="coerce")
    fallback_line = pd.to_numeric(line_value, errors="coerce")

    has_manual_line = bool(pd.notna(manual_line) and float(manual_line) > 0)
    has_fallback_line = bool(pd.notna(fallback_line) and float(fallback_line) > 0)

    if use_market:
        active_line = float(market_line)
        source_name = "BetMGM"
        icon = "🎯"
    elif has_manual_line:
        active_line = float(manual_line)
        source_name = "Manual"
        icon = "✏️"
    elif has_fallback_line:
        # Compatibilidade com telas antigas que ainda possam enviar uma linha global.
        active_line = float(fallback_line)
        source_name = "Manual"
        icon = "✏️"
    else:
        return {
            "projection": projection,
            "edge": 0.0,
            "label": "Sem linha",
            "line_value": None,
            "line_source": "Sem linha",
            "has_market_line": False,
            "has_active_line": False,
            "over_dec": None,
            "under_dec": None,
            "updated_at": "",
            "hit_l10": "-",
            "hit_l10_html": "-",
            "under_l10": "-",
            "under_l10_html": "-",
            "hit_sequence": "",
            "under_sequence": "",
            "icon": "",
            "tooltip": "Nenhuma linha informada para este jogador.",
            "hit_l5": "-",
            "under_l5": "-",
        }

    edge = projection - active_line
    recent_values = row.get(recent_list_col, [])
    if not isinstance(recent_values, list):
        recent_values = []

    hit_l10 = sum(float(v) >= active_line for v in recent_values)
    hit_l5 = sum(float(v) >= active_line for v in recent_values[:5])
    under_l10 = sum(float(v) < active_line for v in recent_values)
    under_l5 = sum(float(v) < active_line for v in recent_values[:5])

    hit_sequence = "".join(
        ["✅" if float(v) >= active_line else "❌" for v in reversed(recent_values)]
    )
    under_sequence = "".join(
        ["✅" if float(v) < active_line else "❌" for v in reversed(recent_values)]
    )

    tooltip = f"Calculado com linha {source_name} ({active_line})"
    hit_l10_str = format_ratio_text(hit_l10, len(recent_values))
    hit_l10_html = f'<span title="{tooltip}" style="cursor:help;">{hit_l10_str} {icon}</span>'
    under_l10_str = format_ratio_text(under_l10, len(recent_values))
    under_l10_html = f'<span title="{tooltip}" style="cursor:help;">{under_l10_str} {icon}</span>'

    return {
        "projection": projection,
        "edge": edge,
        "label": classify_line_edge(edge),
        "line_value": active_line,
        "line_source": source_name,
        "has_market_line": use_market,
        "has_active_line": True,
        "over_dec": market_info.get("over_dec") if use_market else None,
        "under_dec": market_info.get("under_dec") if use_market else None,
        "updated_at": market_info.get("updated_at") if use_market else "",
        "hit_l10": hit_l10_str,
        "hit_l10_html": hit_l10_html,
        "under_l10": under_l10_str,
        "under_l10_html": under_l10_html,
        "hit_sequence": hit_sequence,
        "under_sequence": under_sequence,
        "icon": icon,
        "tooltip": tooltip,
        "hit_l5": format_ratio_text(hit_l5, min(len(recent_values), 5)),
        "under_l5": format_ratio_text(under_l5, min(len(recent_values), 5)),
    }

@st.cache_data(ttl=21600, show_spinner=False)
def get_position_opponent_profile_v2(
    season: str,
    opponent_team_id: int,
    position_group: str,
    season_scope: str = "Regular Season",
) -> dict:
    fallback = {
        "POSITION_GROUP": str(position_group),
        "OPP_PTS_ALLOWED": 0.0,
        "OPP_REB_ALLOWED": 0.0,
        "OPP_AST_ALLOWED": 0.0,
        "OPP_PRA_ALLOWED": 0.0,
        "OPP_3PM_ALLOWED": 0.0,
        "OPP_FGA_ALLOWED": 0.0,
        "OPP_3PA_ALLOWED": 0.0,
        "LEAGUE_PTS_BASELINE": 0.0,
        "LEAGUE_REB_BASELINE": 0.0,
        "LEAGUE_AST_BASELINE": 0.0,
        "LEAGUE_PRA_BASELINE": 0.0,
        "LEAGUE_3PM_BASELINE": 0.0,
        "LEAGUE_FGA_BASELINE": 0.0,
        "LEAGUE_3PA_BASELINE": 0.0,
        "MATCHUP_DIFF": 0.0,
        "MATCHUP_LABEL": "Neutro",
        "MATCHUP_DIFF_PTS": 0.0,
        "MATCHUP_LABEL_PTS": "Neutro",
        "MATCHUP_DIFF_REB": 0.0,
        "MATCHUP_LABEL_REB": "Neutro",
        "MATCHUP_DIFF_AST": 0.0,
        "MATCHUP_LABEL_AST": "Neutro",
        "MATCHUP_DIFF_PRA": 0.0,
        "MATCHUP_LABEL_PRA": "Neutro",
        "MATCHUP_DIFF_3PM": 0.0,
        "MATCHUP_LABEL_3PM": "Neutro",
        "MATCHUP_DIFF_FGA": 0.0,
        "MATCHUP_LABEL_FGA": "Neutro",
        "MATCHUP_DIFF_3PA": 0.0,
        "MATCHUP_LABEL_3PA": "Neutro",
    }

    try:
        def weighted_profile(df: pd.DataFrame) -> dict:
            if df is None or df.empty or "GP" not in df.columns:
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
                work_df[col] = pd.to_numeric(work_df.get(col, 0), errors="coerce").fillna(0.0)

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

        opp_raw = get_position_allowed_profile(
            season,
            opponent_team_id,
            position_group,
            season_scope=season_scope,
        )

        league_raw = get_league_position_baseline(
            season,
            position_group,
            season_scope=season_scope,
        )        

        opp_profile = weighted_profile(opp_raw)
        league_profile = weighted_profile(league_raw)

        diff_pts = float(opp_profile["PTS"]) - float(league_profile["PTS"])
        diff_reb = float(opp_profile["REB"]) - float(league_profile["REB"])
        diff_ast = float(opp_profile["AST"]) - float(league_profile["AST"])
        diff_pra = float(opp_profile["PRA"]) - float(league_profile["PRA"])
        diff_3pm = float(opp_profile["FG3M"]) - float(league_profile["FG3M"])
        diff_fga = float(opp_profile["FGA"]) - float(league_profile["FGA"])
        diff_3pa = float(opp_profile["FG3A"]) - float(league_profile["FG3A"])

        return {
            "POSITION_GROUP": str(position_group),
            "OPP_PTS_ALLOWED": float(opp_profile["PTS"]),
            "OPP_REB_ALLOWED": float(opp_profile["REB"]),
            "OPP_AST_ALLOWED": float(opp_profile["AST"]),
            "OPP_PRA_ALLOWED": float(opp_profile["PRA"]),
            "OPP_3PM_ALLOWED": float(opp_profile["FG3M"]),
            "OPP_FGA_ALLOWED": float(opp_profile["FGA"]),
            "OPP_3PA_ALLOWED": float(opp_profile["FG3A"]),
            "LEAGUE_PTS_BASELINE": float(league_profile["PTS"]),
            "LEAGUE_REB_BASELINE": float(league_profile["REB"]),
            "LEAGUE_AST_BASELINE": float(league_profile["AST"]),
            "LEAGUE_PRA_BASELINE": float(league_profile["PRA"]),
            "LEAGUE_3PM_BASELINE": float(league_profile["FG3M"]),
            "LEAGUE_FGA_BASELINE": float(league_profile["FGA"]),
            "LEAGUE_3PA_BASELINE": float(league_profile["FG3A"]),
            "MATCHUP_DIFF": diff_pra,
            "MATCHUP_LABEL": classify_matchup_tier_by_metric("PRA", diff_pra),
            "MATCHUP_DIFF_PTS": diff_pts,
            "MATCHUP_LABEL_PTS": classify_matchup_tier_by_metric("PTS", diff_pts),
            "MATCHUP_DIFF_REB": diff_reb,
            "MATCHUP_LABEL_REB": classify_matchup_tier_by_metric("REB", diff_reb),
            "MATCHUP_DIFF_AST": diff_ast,
            "MATCHUP_LABEL_AST": classify_matchup_tier_by_metric("AST", diff_ast),
            "MATCHUP_DIFF_PRA": diff_pra,
            "MATCHUP_LABEL_PRA": classify_matchup_tier_by_metric("PRA", diff_pra),
            "MATCHUP_DIFF_3PM": diff_3pm,
            "MATCHUP_LABEL_3PM": classify_matchup_tier_by_metric("3PM", diff_3pm),
            "MATCHUP_DIFF_FGA": diff_fga,
            "MATCHUP_LABEL_FGA": classify_matchup_tier_by_metric("FGA", diff_fga),
            "MATCHUP_DIFF_3PA": diff_3pa,
            "MATCHUP_LABEL_3PA": classify_matchup_tier_by_metric("3PA", diff_3pa),
        }
    except Exception:
        return fallback
        
# ---------------------------------------------------------
# 4. CONSTRUÇÃO DE DADOS DOS JOGADORES (PANDAS MÁGICO)
# ---------------------------------------------------------
def build_form_context(
    team_df: pd.DataFrame,
    player_logs: pd.DataFrame,
    opponent_abbr: str = "",
) -> pd.DataFrame:
    """
    Enriquece o roster com forma recente, oscilação por fundamento, splits e H2H.

    player_logs deve preferencialmente conter logs da liga inteira. Assim, um jogador
    transferido continua carregando seus jogos da temporada anterior mesmo que eles
    tenham sido disputados por outra franquia.
    """
    if team_df.empty:
        return team_df

    metric_source = {
        "PRA": "PRA",
        "PTS": "PTS",
        "REB": "REB",
        "AST": "AST",
        "3PM": "FG3M",
        "FGA": "FGA",
        "3PA": "FG3A",
    }
    season_col = {
        "PRA": "SEASON_PRA",
        "PTS": "SEASON_PTS",
        "REB": "SEASON_REB",
        "AST": "SEASON_AST",
        "3PM": "SEASON_3PM",
        "FGA": "SEASON_FGA",
        "3PA": "SEASON_3PA",
    }

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
        "HOME_PRA": 0.0,
        "AWAY_PRA": 0.0,
        "HOME_PTS": 0.0,
        "AWAY_PTS": 0.0,
        "HOME_REB": 0.0,
        "AWAY_REB": 0.0,
        "HOME_AST": 0.0,
        "AWAY_AST": 0.0,
        "HOME_3PM": 0.0,
        "AWAY_3PM": 0.0,
        "HOME_FGA": 0.0,
        "AWAY_FGA": 0.0,
        "HOME_3PA": 0.0,
        "AWAY_3PA": 0.0,
        "L10_MIN": 0.0,
        "L5_MIN": 0.0,
        "H2H_GP": 0.0,
    }

    for metric in metric_source:
        scalar_defaults[f"OSC_{metric}_L10"] = 0.0
        scalar_defaults[f"OSC_{metric}_CLASS"] = "-"
        scalar_defaults[f"FORM_{metric}_SIGNAL"] = "→ Estável"
        scalar_defaults[f"H2H_{metric}"] = 0.0

    list_defaults = {
        "RECENT_PRA_L10": [],
        "RECENT_PTS_L10": [],
        "RECENT_REB_L10": [],
        "RECENT_AST_L10": [],
        "RECENT_3PM_L10": [],
        "RECENT_FGA_L10": [],
        "RECENT_3PA_L10": [],
    }

    if player_logs is None or player_logs.empty:
        enriched = team_df.copy()
        for col, default in scalar_defaults.items():
            enriched[col] = default
        for col, default in list_defaults.items():
            enriched[col] = [default.copy() for _ in range(len(enriched))]
        return enriched

    logs = player_logs.copy()
    logs["PLAYER_ID"] = pd.to_numeric(logs.get("PLAYER_ID"), errors="coerce")

    threshold_map = team_df.set_index("PLAYER_ID")[
        list(season_col.values())
    ].to_dict("index")

    metrics_rows = []

    for player_id in team_df["PLAYER_ID"].tolist():
        player_id_num = pd.to_numeric(player_id, errors="coerce")
        if pd.isna(player_id_num):
            continue

        one = logs[logs["PLAYER_ID"] == player_id_num].copy()
        if one.empty:
            metrics_rows.append(
                {"PLAYER_ID": player_id, **scalar_defaults, **list_defaults}
            )
            continue

        one = one.sort_values("GAME_DATE", ascending=False)
        recent10 = one.head(10).copy()
        recent5 = recent10.head(5).copy()
        sample_size = len(recent10)
        thresholds = threshold_map.get(player_id, {})

        home_logs = one[one["MATCHUP"].str.contains("vs.", regex=False, na=False)]
        away_logs = one[one["MATCHUP"].str.contains("@", regex=False, na=False)]

        if opponent_abbr:
            h2h_logs = one[
                one["MATCHUP"].str.contains(opponent_abbr, case=False, regex=False, na=False)
            ].copy()
        else:
            h2h_logs = pd.DataFrame(columns=one.columns)

        row = {
            "PLAYER_ID": player_id,
            "L10_MIN": float(recent10["MIN"].mean()) if "MIN" in recent10.columns and not recent10.empty else 0.0,
            "L5_MIN": float(recent5["MIN"].mean()) if "MIN" in recent5.columns and not recent5.empty else 0.0,
            "H2H_GP": float(len(h2h_logs)),
        }

        for metric, source_col in metric_source.items():
            recent_values = pd.to_numeric(recent10.get(source_col, pd.Series(dtype=float)), errors="coerce").dropna()
            recent5_values = pd.to_numeric(recent5.get(source_col, pd.Series(dtype=float)), errors="coerce").dropna()
            season_threshold = float(thresholds.get(season_col[metric], 0.0) or 0.0)

            if len(recent_values) > 0 and season_threshold > 0:
                hit_count = int((recent_values >= season_threshold).sum())
                hit_rate = float(hit_count / len(recent_values))
                hit_text = format_ratio_text(hit_count, len(recent_values))
            else:
                hit_rate = 0.0
                hit_text = "-"

            osc = float(recent_values.std(ddof=0)) if len(recent_values) > 1 else 0.0
            ordered = one.sort_values("GAME_DATE").tail(10)
            ordered_values = pd.to_numeric(
                ordered.get(source_col, pd.Series(dtype=float)), errors="coerce"
            ).dropna()
            slope = (
                float(np.polyfit(range(len(ordered_values)), ordered_values, 1)[0])
                if len(ordered_values) >= 3
                else 0.0
            )

            row[f"OSC_{metric}_L10"] = osc
            row[f"OSC_{metric}_CLASS"] = classify_oscillation(osc)
            row[f"FORM_{metric}_SIGNAL"] = classify_form_signal(slope)
            row[f"RECENT_{metric}_L10"] = recent_values.round(1).tolist()

            h2h_values = pd.to_numeric(
                h2h_logs.get(source_col, pd.Series(dtype=float)), errors="coerce"
            ).dropna()
            row[f"H2H_{metric}"] = float(h2h_values.mean()) if len(h2h_values) else 0.0

            home_values = pd.to_numeric(
                home_logs.get(source_col, pd.Series(dtype=float)), errors="coerce"
            ).dropna()
            away_values = pd.to_numeric(
                away_logs.get(source_col, pd.Series(dtype=float)), errors="coerce"
            ).dropna()
            row[f"HOME_{metric}"] = float(home_values.mean()) if len(home_values) else 0.0
            row[f"AWAY_{metric}"] = float(away_values.mean()) if len(away_values) else 0.0

            if metric == "PRA":
                row["HIT_RATE_L10"] = hit_rate
                row["HIT_RATE_L10_TEXT"] = hit_text
            elif metric == "PTS":
                row["PTS_HIT_RATE_L10"] = hit_rate
                row["PTS_HIT_RATE_L10_TEXT"] = hit_text
            elif metric == "REB":
                row["REB_HIT_RATE_L10"] = hit_rate
                row["REB_HIT_RATE_L10_TEXT"] = hit_text
            elif metric == "AST":
                row["AST_HIT_RATE_L10"] = hit_rate
                row["AST_HIT_RATE_L10_TEXT"] = hit_text
            elif metric == "3PM":
                row["THREE_PM_HIT_RATE_L10"] = hit_rate
                row["THREE_PM_HIT_RATE_L10_TEXT"] = hit_text
            elif metric == "FGA":
                row["FGA_HIT_RATE_L10"] = hit_rate
                row["FGA_HIT_RATE_L10_TEXT"] = hit_text
            elif metric == "3PA":
                row["THREE_PA_HIT_RATE_L10"] = hit_rate
                row["THREE_PA_HIT_RATE_L10_TEXT"] = hit_text

        # Mantém compatibilidade com componentes antigos, mas agora o valor
        # genérico representa PRA e os cards novos usam o fundamento específico.
        row["OSC_L10"] = row.get("OSC_PRA_L10", 0.0)
        row["OSC_CLASS"] = row.get("OSC_PRA_CLASS", "-")
        row["FORM_SIGNAL"] = row.get("FORM_PRA_SIGNAL", "→ Estável")

        metrics_rows.append(row)

    metrics_df = pd.DataFrame(metrics_rows)
    enriched = team_df.merge(metrics_df, on="PLAYER_ID", how="left")

    for col, default in scalar_defaults.items():
        if col not in enriched.columns:
            enriched[col] = default
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
    season_scope: str = "Regular Season",
) -> pd.DataFrame:
    
    if team_df.empty: return team_df

    # Preferimos logs da liga inteira para preservar L5/L10 e H2H de
    # jogadores que trocaram de time. Se a consulta falhar, recuamos para
    # os logs do time atual.
    league_logs = get_league_player_logs(
        season,
        season_scope=season_scope,
    )

    if league_logs is None or league_logs.empty:
        league_logs = get_team_player_logs(
            team_id,
            season,
            season_scope=season_scope,
        )

    opponent_abbr = TEAM_ABBR_LOOKUP.get(int(opponent_team_id), "")
    enriched = build_form_context(
        team_df,
        league_logs,
        opponent_abbr=opponent_abbr,
    )

    matchup_rows = [
    get_position_opponent_profile_v2(
        season,
        opponent_team_id,
        pos,
        season_scope=season_scope,
    )
    for pos in ["G", "F", "C"]
]
    matchup_df = pd.DataFrame(matchup_rows)

    if matchup_df.empty or "POSITION_GROUP" not in matchup_df.columns:
        enriched["OPP_TEAM_NAME"] = opponent_team_name

        fallback_cols = [
            "OPP_PTS_ALLOWED", "OPP_REB_ALLOWED", "OPP_AST_ALLOWED", "OPP_PRA_ALLOWED",
            "OPP_3PM_ALLOWED", "OPP_FGA_ALLOWED", "OPP_3PA_ALLOWED",
            "LEAGUE_PTS_BASELINE", "LEAGUE_REB_BASELINE", "LEAGUE_AST_BASELINE", "LEAGUE_PRA_BASELINE",
            "LEAGUE_3PM_BASELINE", "LEAGUE_FGA_BASELINE", "LEAGUE_3PA_BASELINE",
            "MATCHUP_DIFF",
            "MATCHUP_DIFF_PTS", "MATCHUP_DIFF_REB", "MATCHUP_DIFF_AST", "MATCHUP_DIFF_PRA",
            "MATCHUP_DIFF_3PM", "MATCHUP_DIFF_FGA", "MATCHUP_DIFF_3PA",
        ]
        for col in fallback_cols:
            enriched[col] = 0.0

        for col in [
            "MATCHUP_LABEL", "MATCHUP_LABEL_PTS", "MATCHUP_LABEL_REB", "MATCHUP_LABEL_AST",
            "MATCHUP_LABEL_PRA", "MATCHUP_LABEL_3PM", "MATCHUP_LABEL_FGA", "MATCHUP_LABEL_3PA",
        ]:
            enriched[col] = "Neutro"

    else:
        enriched = enriched.merge(matchup_df, on="POSITION_GROUP", how="left")
        enriched["OPP_TEAM_NAME"] = opponent_team_name

        for col in [
            "OPP_PTS_ALLOWED", "OPP_REB_ALLOWED", "OPP_AST_ALLOWED", "OPP_PRA_ALLOWED",
            "OPP_3PM_ALLOWED", "OPP_FGA_ALLOWED", "OPP_3PA_ALLOWED",
            "LEAGUE_PTS_BASELINE", "LEAGUE_REB_BASELINE", "LEAGUE_AST_BASELINE", "LEAGUE_PRA_BASELINE",
            "LEAGUE_3PM_BASELINE", "LEAGUE_FGA_BASELINE", "LEAGUE_3PA_BASELINE",
            "MATCHUP_DIFF",
            "MATCHUP_DIFF_PTS", "MATCHUP_DIFF_REB", "MATCHUP_DIFF_AST", "MATCHUP_DIFF_PRA",
            "MATCHUP_DIFF_3PM", "MATCHUP_DIFF_FGA", "MATCHUP_DIFF_3PA",
        ]:
            enriched[col] = pd.to_numeric(enriched[col], errors="coerce").fillna(0.0)

        for col in [
            "MATCHUP_LABEL",
            "MATCHUP_LABEL_PTS",
            "MATCHUP_LABEL_REB",
            "MATCHUP_LABEL_AST",
            "MATCHUP_LABEL_PRA",
            "MATCHUP_LABEL_3PM",
            "MATCHUP_LABEL_FGA",
            "MATCHUP_LABEL_3PA",
        ]:
            if col not in enriched.columns:
                enriched[col] = "Neutro"
            else:
                enriched[col] = enriched[col].fillna("Neutro")   

    enriched["PROJ_PTS"] = enriched.apply(lambda row: calculate_projection(row["SEASON_PTS"], row["L10_PTS"], row["L5_PTS"], row["OPP_PTS_ALLOWED"], row["LEAGUE_PTS_BASELINE"]), axis=1)
    enriched["PROJ_REB"] = enriched.apply(lambda row: calculate_projection(row["SEASON_REB"], row["L10_REB"], row["L5_REB"], row["OPP_REB_ALLOWED"], row["LEAGUE_REB_BASELINE"]), axis=1)
    enriched["PROJ_AST"] = enriched.apply(lambda row: calculate_projection(row["SEASON_AST"], row["L10_AST"], row["L5_AST"], row["OPP_AST_ALLOWED"], row["LEAGUE_AST_BASELINE"]), axis=1)
    enriched["PROJ_3PM"] = enriched.apply(lambda row: calculate_projection(row["SEASON_3PM"], row["L10_3PM"], row["L5_3PM"], row["OPP_3PM_ALLOWED"], row["LEAGUE_3PM_BASELINE"]), axis=1)
    enriched["PROJ_FGA"] = enriched.apply(lambda row: calculate_projection(row["SEASON_FGA"], row["L10_FGA"], row["L5_FGA"], row["OPP_FGA_ALLOWED"], row["LEAGUE_FGA_BASELINE"]), axis=1)
    enriched["PROJ_3PA"] = enriched.apply(lambda row: calculate_projection(row["SEASON_3PA"], row["L10_3PA"], row["L5_3PA"], row["OPP_3PA_ALLOWED"], row["LEAGUE_3PA_BASELINE"]), axis=1)
    enriched["PROJ_PRA"] = enriched.apply(lambda row: calculate_projection(row["SEASON_PRA"], row["L10_PRA"], row["L5_PRA"], row["OPP_PRA_ALLOWED"], row["LEAGUE_PRA_BASELINE"]), axis=1)

    enriched["PROJ_MIN_V1"] = enriched.apply(
        lambda row: project_minutes_v1(
            row.get("SEASON_MIN", 0.0),
            row.get("L10_MIN", 0.0),
            row.get("L5_MIN", 0.0),
            row.get("ROLE", ""),
            row.get("INJ_STATUS", "Available"),
        ),
        axis=1,
    )

    metric_map = {
        "PTS": ("SEASON_PTS", "L10_PTS", "L5_PTS"),
        "REB": ("SEASON_REB", "L10_REB", "L5_REB"),
        "AST": ("SEASON_AST", "L10_AST", "L5_AST"),
        "PRA": ("SEASON_PRA", "L10_PRA", "L5_PRA"),
        "3PM": ("SEASON_3PM", "L10_3PM", "L5_3PM"),
        "FGA": ("SEASON_FGA", "L10_FGA", "L5_FGA"),
        "3PA": ("SEASON_3PA", "L10_3PA", "L5_3PA"),
    }

    for metric, (season_col, l10_col, l5_col) in metric_map.items():
        enriched[f"SEASON_PM_{metric}"] = enriched.apply(
            lambda row: safe_rate(row.get(season_col, 0.0), row.get("SEASON_MIN", 0.0)),
            axis=1,
        )
        enriched[f"L10_PM_{metric}"] = enriched.apply(
            lambda row: safe_rate(row.get(l10_col, 0.0), row.get("L10_MIN", 0.0)),
            axis=1,
        )
        enriched[f"L5_PM_{metric}"] = enriched.apply(
            lambda row: safe_rate(row.get(l5_col, 0.0), row.get("L5_MIN", 0.0)),
            axis=1,
        )

        enriched[f"RATE_{metric}_V1"] = enriched.apply(
            lambda row: blend_rate(
                row.get(f"SEASON_PM_{metric}", 0.0),
                row.get(f"L10_PM_{metric}", 0.0),
                row.get(f"L5_PM_{metric}", 0.0),
            ),
            axis=1,
        )

        enriched[f"BASE_{metric}_V1"] = (
            enriched["PROJ_MIN_V1"] * enriched[f"RATE_{metric}_V1"]
        )

    enriched["CONTEXT_ADJ_V1"] = enriched.apply(build_context_adj_v1, axis=1)

    for metric in metric_map.keys():
        scale = get_metric_matchup_scale(metric)

        diff_col = get_metric_matchup_diff_column(metric)

        enriched[f"DEF_ADJ_{metric}_V1"] = enriched[diff_col].apply(
            lambda x: clamp_value(float(x) / scale if scale > 0 else 0.0, -2.0, 2.0)
        )

        enriched[f"FORM_ADJ_{metric}_V1"] = enriched.apply(
            lambda row: clamp_value(
                (
                    0.60 * (
                        (row.get(f"L10_PM_{metric}", 0.0) - row.get(f"SEASON_PM_{metric}", 0.0))
                        / max(row.get(f"SEASON_PM_{metric}", 0.0), 0.01)
                    )
                    + 0.40 * (
                        (row.get(f"L5_PM_{metric}", 0.0) - row.get(f"SEASON_PM_{metric}", 0.0))
                        / max(row.get(f"SEASON_PM_{metric}", 0.0), 0.01)
                    )
                ),
                -1.5,
                1.5,
            ),
            axis=1,
        )

        enriched[f"MATCHUP_SCORE_{metric}_V1"] = (
            0.65 * enriched[f"DEF_ADJ_{metric}_V1"]
            + 0.20 * enriched[f"FORM_ADJ_{metric}_V1"]
            + 0.15 * enriched["CONTEXT_ADJ_V1"]
        )

        enriched[f"PROJ_{metric}_V1"] = (
        enriched[f"BASE_{metric}_V1"]
            * (1 + 0.10 * enriched[f"MATCHUP_SCORE_{metric}_V1"])
        ).clip(lower=0.0)

        enriched[f"MATCHUP_LABEL_{metric}_V1"] = enriched[f"MATCHUP_SCORE_{metric}_V1"].apply(
            classify_matchup_score_label
        )
    return enriched

    

def merge_betmgm_odds(team_df: pd.DataFrame, odds_df: pd.DataFrame) -> pd.DataFrame:
    if team_df.empty: return team_df
    enriched = team_df.copy()
    all_odds_cols = [col for cols in ODDS_METRIC_COLUMNS.values() for col in cols]

    if odds_df.empty:
        for col in all_odds_cols:
            if col not in enriched.columns: enriched[col] = None
        return enriched

    enriched["_PLAYER_KEY_MERGE"] = enriched["PLAYER_KEY"].fillna("").astype(str).apply(normalize_person_name)
    odds_work = odds_df.copy()
    odds_work["_PLAYER_KEY_MERGE"] = odds_work["PLAYER_KEY_ODDS"].fillna("").astype(str).apply(normalize_person_name)

    odds_keep_cols = ["_PLAYER_KEY_MERGE", "PLAYER_KEY_ODDS", "PLAYER_NAME_ODDS"] + [col for col in all_odds_cols if col in odds_work.columns]
    odds_work = odds_work[[c for c in odds_keep_cols if c in odds_work.columns]].copy()

    existing_betmgm_cols = [col for col in all_odds_cols if col in enriched.columns]
    if existing_betmgm_cols: enriched = enriched.drop(columns=existing_betmgm_cols)

    merged = enriched.merge(odds_work, on="_PLAYER_KEY_MERGE", how="left")
    drop_cols = [c for c in ["_PLAYER_KEY_MERGE", "PLAYER_KEY_ODDS", "PLAYER_NAME_ODDS"] if c in merged.columns]
    if drop_cols: merged = merged.drop(columns=drop_cols)

    for col in all_odds_cols:
        if col not in merged.columns: merged[col] = None

    return merged

@st.cache_data(ttl=54000, show_spinner=False)
def build_team_table(
    team_id: int,
    season: str,
    season_scope: str = "Regular Season",
    roster_season: str | None = None,
) -> pd.DataFrame:
    # LeagueDashPlayerStats já traz PLAYER_ID, PLAYER_NAME e TEAM_ID.
    # Buscamos essa base primeiro para termos um fallback caso
    # CommonTeamRoster não responda no Streamlit Cloud.
    season_stats = get_league_player_stats(
        season,
        last_n_games=0,
        season_scope=season_scope,
    )

    roster_season = roster_season or season

    try:
        roster = get_team_roster(team_id, roster_season)
    except Exception:
        roster = pd.DataFrame()

    fallback_source = season_stats

    if roster.empty and roster_season != season:
        try:
            current_roster_stats = get_league_player_stats(
                roster_season,
                last_n_games=0,
                season_scope="Regular Season",
            )
            if not current_roster_stats.empty:
                fallback_source = current_roster_stats
        except Exception:
            pass

    if roster.empty and not fallback_source.empty and "TEAM_ID" in fallback_source.columns:
        fallback_roster = fallback_source.copy()
        fallback_roster["TEAM_ID"] = pd.to_numeric(
            fallback_roster["TEAM_ID"],
            errors="coerce",
        )
        fallback_roster = fallback_roster[
            fallback_roster["TEAM_ID"] == int(team_id)
        ].copy()

        if not fallback_roster.empty:
            fallback_roster = fallback_roster.rename(
                columns={"PLAYER_NAME": "PLAYER"}
            )
            roster = fallback_roster[
                [c for c in ["PLAYER", "PLAYER_ID"] if c in fallback_roster.columns]
            ].copy()
            roster["POSITION"] = ""

    last5_stats = get_league_player_stats(
        season,
        last_n_games=5,
        season_scope=season_scope,
    )

    last10_stats = get_league_player_stats(
        season,
        last_n_games=10,
        season_scope=season_scope,
    )

    if roster.empty:
        return pd.DataFrame()

    roster = roster[[c for c in ["PLAYER", "PLAYER_ID", "POSITION"] if c in roster.columns]].copy()
    if "POSITION" not in roster.columns: roster["POSITION"] = ""

    season_view = pd.DataFrame(columns=["PLAYER_ID", "SEASON_GP", "SEASON_MIN", "SEASON_PTS", "SEASON_REB", "SEASON_AST", "SEASON_3PM", "SEASON_FGA", "SEASON_3PA"]) if season_stats.empty else season_stats.rename(columns={"GP": "SEASON_GP", "MIN": "SEASON_MIN", "PTS": "SEASON_PTS", "REB": "SEASON_REB", "AST": "SEASON_AST", "FG3M": "SEASON_3PM", "FGA": "SEASON_FGA", "FG3A": "SEASON_3PA"})
    last5_view = pd.DataFrame(columns=["PLAYER_ID", "L5_GP", "L5_MIN", "L5_PTS", "L5_REB", "L5_AST", "L5_3PM", "L5_FGA", "L5_3PA"]) if last5_stats.empty else last5_stats.rename(columns={"GP": "L5_GP", "MIN": "L5_MIN", "PTS": "L5_PTS", "REB": "L5_REB", "AST": "L5_AST", "FG3M": "L5_3PM", "FGA": "L5_FGA", "FG3A": "L5_3PA"})
    last10_view = pd.DataFrame(columns=["PLAYER_ID", "L10_GP", "L10_MIN", "L10_PTS", "L10_REB", "L10_AST", "L10_3PM", "L10_FGA", "L10_3PA"]) if last10_stats.empty else last10_stats.rename(columns={"GP": "L10_GP", "MIN": "L10_MIN", "PTS": "L10_PTS", "REB": "L10_REB", "AST": "L10_AST", "FG3M": "L10_3PM", "FGA": "L10_FGA", "FG3A": "L10_3PA"})

    team_df = roster.merge(season_view[["PLAYER_ID", "SEASON_GP", "SEASON_MIN", "SEASON_PTS", "SEASON_REB", "SEASON_AST", "SEASON_3PM", "SEASON_FGA", "SEASON_3PA"]], on="PLAYER_ID", how="left").merge(last5_view[["PLAYER_ID", "L5_GP", "L5_MIN", "L5_PTS", "L5_REB", "L5_AST", "L5_3PM", "L5_FGA", "L5_3PA"]], on="PLAYER_ID", how="left").merge(last10_view[["PLAYER_ID", "L10_GP", "L10_MIN", "L10_PTS", "L10_REB", "L10_AST", "L10_3PM", "L10_FGA", "L10_3PA"]], on="PLAYER_ID", how="left")

    numeric_cols = ["SEASON_GP", "SEASON_MIN", "SEASON_PTS", "SEASON_REB", "SEASON_AST", "SEASON_3PM", "SEASON_FGA", "SEASON_3PA", "L5_GP", "L5_MIN", "L5_PTS", "L5_REB", "L5_AST", "L5_3PM", "L5_FGA", "L5_3PA", "L10_GP", "L10_MIN", "L10_PTS", "L10_REB", "L10_AST", "L10_3PM", "L10_FGA", "L10_3PA"]
    for col in numeric_cols:
        if col not in team_df.columns: team_df[col] = 0.0
        team_df[col] = pd.to_numeric(team_df[col], errors="coerce").fillna(0.0)

    team_df["SEASON_PRA"] = team_df["SEASON_PTS"] + team_df["SEASON_REB"] + team_df["SEASON_AST"]
    team_df["L5_PRA"] = team_df["L5_PTS"] + team_df["L5_REB"] + team_df["L5_AST"]
    team_df["L10_PRA"] = team_df["L10_PTS"] + team_df["L10_REB"] + team_df["L10_AST"]
    team_df["DELTA_PRA_L5"] = team_df["L5_PRA"] - team_df["SEASON_PRA"]
    team_df["DELTA_PRA_L10"] = team_df["L10_PRA"] - team_df["SEASON_PRA"]

    team_df["TREND"] = team_df["DELTA_PRA_L10"].apply(classify_trend)
    team_df["POSITION_GROUP"] = team_df["POSITION"].apply(normalize_position_group)
    team_df["PLAYER_KEY"] = team_df["PLAYER"].apply(normalize_text)

    team_df["ROLE"] = "Reserva"
    starter_ids = team_df.sort_values(by=["SEASON_MIN", "SEASON_GP", "PLAYER"], ascending=[False, False, True]).head(5)["PLAYER_ID"].tolist()
    team_df.loc[team_df["PLAYER_ID"].isin(starter_ids), "ROLE"] = "Titular provável"

    return team_df[["PLAYER_ID", "PLAYER", "PLAYER_KEY", "POSITION", "POSITION_GROUP", "ROLE", "SEASON_GP", "SEASON_MIN", "SEASON_PTS", "L5_PTS", "L10_PTS", "SEASON_REB", "L5_REB", "L10_REB", "SEASON_AST", "L5_AST", "L10_AST", "SEASON_3PM", "L5_3PM", "L10_3PM", "SEASON_FGA", "L5_FGA", "L10_FGA", "SEASON_3PA", "L5_3PA", "L10_3PA", "SEASON_PRA", "L5_PRA", "L10_PRA", "DELTA_PRA_L5", "DELTA_PRA_L10", "TREND"]].copy()

def get_matchup_context(
    away_team_id: int,
    home_team_id: int,
    away_team_name: str,
    home_team_name: str,
    season: str,
    include_market: bool,
    season_scope: str = "Regular Season",
    roster_season: str | None = None,
    progress_callback=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:

    def report(message: str) -> None:
        if progress_callback is not None:
            try:
                progress_callback(message)
            except Exception:
                pass

    report(f"Carregando elenco e médias de {away_team_name}...")
    away_df = build_team_table(
        away_team_id,
        season,
        season_scope=season_scope,
        roster_season=roster_season,
    )

    report(f"Carregando elenco e médias de {home_team_name}...")
    home_df = build_team_table(
        home_team_id,
        season,
        season_scope=season_scope,
        roster_season=roster_season,
    )

    report("Consultando Injury Report...")
    # Injuries must be merged before projections are calculated so
    # Questionable/Doubtful/Out can influence minutes/context and rankings.
    try:
        injury_df = fetch_latest_injury_report_df()
    except Exception:
        injury_df = pd.DataFrame()

    game_matchup = f"{TEAM_ABBR_LOOKUP.get(int(away_team_id), '')}@{TEAM_ABBR_LOOKUP.get(int(home_team_id), '')}"

    away_df = merge_injury_report(
        away_df,
        injury_df,
        away_team_name,
        away_team_id,
        game_matchup=game_matchup,
    )
    home_df = merge_injury_report(
        home_df,
        injury_df,
        home_team_name,
        home_team_id,
        game_matchup=game_matchup,
    )

    report(f"Carregando histórico e matchup de {away_team_name}...")
    away_df = enrich_team_with_context(
        team_df=away_df,
        team_id=away_team_id,
        opponent_team_id=home_team_id,
        opponent_team_name=home_team_name,
        season=season,
        season_scope=season_scope,
    )

    report(f"Carregando histórico e matchup de {home_team_name}...")
    home_df = enrich_team_with_context(
        team_df=home_df,
        team_id=home_team_id,
        opponent_team_id=away_team_id,
        opponent_team_name=away_team_name,
        season=season,
        season_scope=season_scope,
    )

    away_df["TEAM_NAME"] = away_team_name
    home_df["TEAM_NAME"] = home_team_name

    away_df["IS_HOME"] = False
    home_df["IS_HOME"] = True

    odds_df = pd.DataFrame()

    if include_market:
        report("Consultando linhas de mercado...")
        odds_events = fetch_nba_odds_events()
        selected_odds_event = find_matching_odds_event(
            odds_events,
            home_team_name=home_team_name,
            away_team_name=away_team_name,
        )
        odds_df = extract_betmgm_player_props(selected_odds_event)

    report("Finalizando projeções e cards...")
    away_df = merge_betmgm_odds(away_df, odds_df)
    home_df = merge_betmgm_odds(home_df, odds_df)

    return away_df, home_df

def merge_injury_report(team_df: pd.DataFrame, injury_df: pd.DataFrame, team_name: str, team_id: int, game_matchup: str = "") -> pd.DataFrame:
    if team_df.empty: return team_df
    enriched = team_df.copy()
    enriched["INJ_STATUS"] = "—"
    enriched["INJ_REASON"] = ""
    enriched["INJ_REPORT_URL"] = ""
    enriched["IS_UNAVAILABLE"] = False
    enriched["INJ_MATCHUP_FOUND"] = False

    if injury_df.empty: return enriched

    roster_keys = set(enriched["PLAYER_KEY"].fillna("").astype(str).tolist())
    
    def fuzzy_match(ir_key: str) -> str:
        if ir_key in roster_keys: return ir_key
        for suffix in [" iii", " ii", " iv", " v", " jr", " sr"]:
            if ir_key.endswith(suffix):
                clean = ir_key[:-len(suffix)].strip()
                if clean in roster_keys: return clean
        for rk in roster_keys:
            if ir_key in rk or rk in ir_key: return rk
        return ir_key

    work_ir = injury_df.copy()
    work_ir["PLAYER_KEY_IR"] = work_ir["PLAYER_KEY_IR"].fillna("").astype(str).apply(fuzzy_match)
    work_match = work_ir[work_ir["PLAYER_KEY_IR"].isin(roster_keys)].copy()

    if work_match.empty: return enriched

    work_match = work_match.drop_duplicates(subset=["PLAYER_KEY_IR"], keep="last")
    enriched["INJ_STATUS"] = "Available"
    enriched["INJ_MATCHUP_FOUND"] = True

    merge_cols = [c for c in ["PLAYER_KEY_IR", "INJ_STATUS", "INJ_REASON", "INJ_REPORT_URL"] if c in work_match.columns]
    merged = enriched.merge(work_match[merge_cols], left_on="PLAYER_KEY", right_on="PLAYER_KEY_IR", how="left", suffixes=("", "_IR"))

    if "INJ_STATUS_IR" in merged.columns: merged["INJ_STATUS"] = merged["INJ_STATUS_IR"].fillna(merged["INJ_STATUS"])
    if "INJ_REASON_IR" in merged.columns: merged["INJ_REASON"] = merged["INJ_REASON_IR"].fillna(merged["INJ_REASON"])
    if "INJ_REPORT_URL_IR" in merged.columns: merged["INJ_REPORT_URL"] = merged["INJ_REPORT_URL_IR"].fillna(merged["INJ_REPORT_URL"])

    merged["IS_UNAVAILABLE"] = merged["INJ_STATUS"].isin(INACTIVE_STATUSES)
    drop_cols = [c for c in ["PLAYER_KEY_IR", "INJ_STATUS_IR", "INJ_REASON_IR", "INJ_REPORT_URL_IR"] if c in merged.columns]
    if drop_cols: merged = merged.drop(columns=drop_cols)

    return merged

@st.cache_data(ttl=36000, show_spinner=False)
def get_matchup_injury_context(away_team_id: int, home_team_id: int, away_team_name: str, home_team_name: str, away_df: pd.DataFrame, home_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    try:
        injury_df = fetch_latest_injury_report_df()
    except Exception:
        injury_df = pd.DataFrame()

    injury_report_url = ""
    if not injury_df.empty and "INJ_REPORT_URL" in injury_df.columns:
        valid_urls = injury_df["INJ_REPORT_URL"].dropna().astype(str)
        valid_urls = valid_urls[valid_urls.str.strip() != ""]
        if not valid_urls.empty: injury_report_url = valid_urls.iloc[0]

    injury_report_meta = parse_injury_report_timestamp_from_url(injury_report_url)
    game_matchup = f"{TEAM_ABBR_LOOKUP[int(away_team_id)]}@{TEAM_ABBR_LOOKUP[int(home_team_id)]}"

    away_injury_df = merge_injury_report(away_df, injury_df, away_team_name, away_team_id, game_matchup=game_matchup)
    home_injury_df = merge_injury_report(home_df, injury_df, home_team_name, home_team_id, game_matchup=game_matchup)

    return away_injury_df, home_injury_df, injury_report_meta

# ---------------------------------------------------------
# 5. FUNÇÕES DE FILTRO E VISUALIZAÇÃO DE TABELAS
# ---------------------------------------------------------
def apply_filters(team_df: pd.DataFrame, min_games: int, min_minutes: int, role_filter: str) -> pd.DataFrame:
    filtered = team_df[(team_df["SEASON_GP"] >= min_games) & (team_df["SEASON_MIN"] >= min_minutes)].copy()
    if "IS_UNAVAILABLE" in filtered.columns:
        filtered = filtered[~filtered["IS_UNAVAILABLE"].fillna(False)].copy()
    if role_filter != "Todos":
        filtered = filtered[filtered["ROLE"] == role_filter].copy()
    return filtered

def filter_and_sort_team_df(team_df: pd.DataFrame, min_games: int, min_minutes: int, role_filter: str, sort_column: str, ascending: bool) -> pd.DataFrame:
    if team_df.empty: return team_df
    filtered = apply_filters(team_df, min_games, min_minutes, role_filter)
    if filtered.empty: return filtered

    if sort_column == "PLAYER":
        filtered = filtered.sort_values(by=["PLAYER", "SEASON_MIN"], ascending=[ascending, False])
    else:
        filtered = filtered.sort_values(by=[sort_column, "SEASON_MIN", "PLAYER"], ascending=[ascending, False, True])
    return filtered.reset_index(drop=True)

def build_summary_cards_data(away_df: pd.DataFrame, home_df: pd.DataFrame, min_games: int, min_minutes: int, role_filter: str) -> pd.DataFrame:
    away_filtered = apply_filters(away_df, min_games, min_minutes, role_filter).copy()
    home_filtered = apply_filters(home_df, min_games, min_minutes, role_filter).copy()
    return pd.concat([away_filtered, home_filtered], ignore_index=True)

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
    display_df["Hit PRA"] = display_df.get("HIT_RATE_L10_TEXT", "-")
    display_df["Hit PTS"] = display_df.get("PTS_HIT_RATE_L10_TEXT", "-")
    display_df["Hit REB"] = display_df.get("REB_HIT_RATE_L10_TEXT", "-")
    display_df["Hit AST"] = display_df.get("AST_HIT_RATE_L10_TEXT", "-")
    display_df["Hit 3PM"] = display_df.get("THREE_PM_HIT_RATE_L10_TEXT", "-")
    display_df["Hit FGA"] = display_df.get("FGA_HIT_RATE_L10_TEXT", "-")
    display_df["Hit 3PA"] = display_df.get("THREE_PA_HIT_RATE_L10_TEXT", "-")
    display_df["Sinal"] = display_df["FORM_SIGNAL"]
    display_df["Oscilação"] = display_df["OSC_CLASS"]
    display_df["PRA adv pos"] = display_df["OPP_PRA_ALLOWED"]
    display_df["Liga pos"] = display_df["LEAGUE_PRA_BASELINE"]

    summary_df = display_df[["Jogador", "Papel", "GP", "MIN", "PRA Temp", "PRA L10", "Proj PRA", "Δ PRA L10", "Matchup", "Hit PRA", "Oscilação", "Sinal", "Trend"]].copy()
    detail_df = display_df[["Jogador", "Pos", "Papel", "GP", "MIN", "PTS Temp", "PTS L5", "PTS L10", "Proj PTS", "Hit PTS", "REB Temp", "REB L5", "REB L10", "Proj REB", "Hit REB", "AST Temp", "AST L5", "AST L10", "Proj AST", "Hit AST", "3PM Temp", "3PM L5", "3PM L10", "Proj 3PM", "Hit 3PM", "FGA Temp", "FGA L5", "FGA L10", "Proj FGA", "Hit FGA", "3PA Temp", "3PA L5", "3PA L10", "Proj 3PA", "Hit 3PA", "PRA Temp", "PRA L5", "PRA L10", "Proj PRA", "Hit PRA", "Δ PRA L5", "Δ PRA L10", "PRA adv pos", "Liga pos", "Matchup", "Oscilação", "Sinal", "Trend"]].copy()

    return summary_df, detail_df

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
    

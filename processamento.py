import pandas as pd
import numpy as np
import streamlit as st

# 1. Importar as nossas configurações estáticas
from config import PROJECTION_WEIGHTS, ROLE_OPTIONS, TEAM_LOOKUP

# 2. Importar os motores de busca da NBA (para alimentar os cálculos)
from api_nba import (
    get_team_roster, get_league_player_stats, get_team_player_logs,
    get_position_allowed_profile, get_league_position_baseline
)

# ==========================================
# CÓDIGO DE PROCESSAMENTO E PANDAS ABAIXO
# ==========================================
def classify_matchup_tier(diff_value: float) -> str:
    if diff_value >= 2.5:
        return "Favorável"
    if diff_value <= -2.5:
        return "Difícil"
    return "Neutro"

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

@st.cache_data(ttl=21600, show_spinner=False)
def get_position_opponent_profile_v2(season: str, opponent_team_id: int, position_group: str) -> dict:
    fallback = {
        "POSITION_GROUP": str(position_group),
        "OPP_PTS_ALLOWED": 0.0, "OPP_REB_ALLOWED": 0.0, "OPP_AST_ALLOWED": 0.0, 
        "OPP_PRA_ALLOWED": 0.0, "OPP_3PM_ALLOWED": 0.0, "OPP_FGA_ALLOWED": 0.0, "OPP_3PA_ALLOWED": 0.0,
        "LEAGUE_PTS_BASELINE": 0.0, "LEAGUE_REB_BASELINE": 0.0, "LEAGUE_AST_BASELINE": 0.0, 
        "LEAGUE_PRA_BASELINE": 0.0, "LEAGUE_3PM_BASELINE": 0.0, "LEAGUE_FGA_BASELINE": 0.0, "LEAGUE_3PA_BASELINE": 0.0, 
        "MATCHUP_DIFF": 0.0, "MATCHUP_LABEL": "Neutro",
    }
    
    try:
        def weighted_profile(df: pd.DataFrame) -> dict:
            if df is None or df.empty or "GP" not in df.columns:
                return {"PTS": 0.0, "REB": 0.0, "AST": 0.0, "FG3M": 0.0, "FGA": 0.0, "FG3A": 0.0, "PRA": 0.0, "GP": 0.0}

            work_df = df.copy()
            for col in ["GP", "PTS", "REB", "AST", "FG3M", "FGA", "FG3A"]:
                work_df[col] = pd.to_numeric(work_df.get(col, 0), errors="coerce").fillna(0.0)

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

        opp_df_raw = get_position_allowed_profile(season, opponent_team_id, position_group)
        league_df_raw = get_league_position_baseline(season, position_group)
        
        opp_profile = weighted_profile(opp_df_raw)
        league_profile = weighted_profile(league_df_raw)

        matchup_diff = float(opp_profile["PRA"]) - float(league_profile["PRA"])

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
            "LEAGUE_3PM_BASELINE": float(league_profile["FG3M"]),
            "LEAGUE_FGA_BASELINE": float(league_profile["FGA"]),
            "LEAGUE_3PA_BASELINE": float(league_profile["FG3A"]),
            "LEAGUE_PRA_BASELINE": float(league_profile["PRA"]),
            "MATCHUP_DIFF": matchup_diff,
            "MATCHUP_LABEL": classify_matchup_tier(matchup_diff),
        }
    except Exception:
        return fallback
        
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

    # MUDANÇA: Usando a V2 da função para fugir do cache
    matchup_rows = [get_position_opponent_profile_v2(season, opponent_team_id, pos) for pos in ["G", "F", "C"]]
    matchup_df = pd.DataFrame(matchup_rows)

    # TRAVA DE SEGURANÇA: Só tenta juntar se a coluna existir, senão usa o plano B.
    if matchup_df.empty or "POSITION_GROUP" not in matchup_df.columns:
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

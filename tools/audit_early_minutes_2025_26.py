"""Minutes-only walk-forward check for players with 1-9 prior games in 2025-26.

Same project_minutes_v1 native formula; only PREVIOUS appearances by the
same player are used. Excludes DNP and roster eligibility; this is a
conditional-on-playing analysis, not a starter/availability forecast.
"""
from __future__ import annotations
import json
from pathlib import Path
import warnings
import numpy as np
import pandas as pd
from api_espn import get_espn_team_player_logs
from processamento import project_minutes_v1

warnings.filterwarnings("ignore", message=".*ScriptRunContext.*")
TEAMS={"OKC":1610612760,"BOS":1610612738,"NYK":1610612752,
       "DEN":1610612743,"MIN":1610612750,"GSW":1610612744}
START=pd.Timestamp("2025-11-01").date()
END=pd.Timestamp("2026-04-15").date()
OUT=Path("early_minutes_results")
OUT.mkdir(exist_ok=True)

def main():
    all_data=[]
    coverage=[]
    for abbr,tid in TEAMS.items():
        df=get_espn_team_player_logs(tid,"2025-26",season_scope="Regular Season")
        df=df[df.GAME_ID.astype(str).ne("401809839")].copy()
        df["TEAM"]=abbr
        all_data.append(df)
        coverage.append({"team":abbr,"games":df.GAME_ID.nunique(),"players":df.PLAYER_ID.nunique()})
        print("ESPN_MINUTES_SOURCE",coverage[-1],flush=True)
    logs=pd.concat(all_data,ignore_index=True)
    logs["GAME_DATE"]=pd.to_datetime(logs["GAME_DATE"],errors="coerce",utc=True)
    logs["MIN"]=pd.to_numeric(logs["MIN"],errors="coerce")
    logs=logs.dropna(subset=["PLAYER_ID","GAME_ID","GAME_DATE","MIN"])
    logs=logs[logs.MIN>0].drop_duplicates(["PLAYER_ID","GAME_ID"]).copy()
    logs["DAY"]=logs.GAME_DATE.dt.tz_convert("America/Sao_Paulo").dt.date
    logs=logs.sort_values(["PLAYER_ID","GAME_DATE"])
    rows=[]
    for pid,df in logs.groupby("PLAYER_ID"):
        df=df.sort_values("GAME_DATE")
        for _,target in df.iterrows():
            if not START<=target.DAY<=END:
                continue
            prior=df[df.DAY<target.DAY]
            n=len(prior)
            if not (1<=n<=9):
                continue
            l10=prior.tail(10); l5=prior.tail(5)
            val=project_minutes_v1(float(prior.MIN.mean()),
                                   float(l10.MIN.median()),
                                   float(l5.MIN.median()))
            real=float(target.MIN)
            rows.append({"PLAYER_ID":pid,"TEAM":target.TEAM,"DATE":str(target.DAY),
                "MONTH":str(target.DAY)[:7],"GP_PRIOR":n,
                "HISTORY":"1" if n==1 else "2-4" if n<=4 else "5-9",
                "MIN_PROJ":val,"MIN_REAL":real,
                "ERR_ABS":abs(val-real),"BIAS":val-real,
                "ERR_L10_MEAN":abs(float(l10.MIN.mean())-real),
                "PREV_MIN":float(prior.MIN.iloc[-1]),
                "IS_40_PLUS_PROJ":bool(val>=40),
                "CRASH_15_PLUS":bool(val-real>=15)})
    d=pd.DataFrame(rows)
    if len(d)<100:
        raise RuntimeError("No sufficient 1-9 game minute samples.")
    d.to_csv(OUT/"minutes_1_to_9_games_game_by_game.csv",index=False)
    def summary(groups):
        ans=d.groupby(groups).agg(n=("ERR_ABS","size"),players=("PLAYER_ID","nunique"),
        mae_model=("ERR_ABS","mean"),mae_l10=("ERR_L10_MEAN","mean"),
        bias=("BIAS","mean"),pct_gt5=("ERR_ABS",lambda s:(s>5).mean()),
        pct_gt10=("ERR_ABS",lambda s:(s>10).mean()),
        projected40=("IS_40_PLUS_PROJ","sum"),
        badly_overprojected15=("CRASH_15_PLUS","sum")).reset_index()
        return ans.round(4)
    summary(["HISTORY"]).to_csv(OUT/"early_minutes_by_sample_size.csv",index=False)
    summary(["GP_PRIOR"]).to_csv(OUT/"early_minutes_by_exact_gp.csv",index=False)
    summary(["MONTH"]).to_csv(OUT/"early_minutes_by_month.csv",index=False)
    print("EARLY_MINUTES_OVERALL",summary(["HISTORY"]).to_string(index=False),flush=True)
    print("EARLY_MINUTES_BY_GP",summary(["GP_PRIOR"]).to_string(index=False),flush=True)
    (OUT/"manifest.json").write_text(json.dumps({
        "source":"ESPN historical boxscores for 6 selected teams",
        "coverage":coverage,"sample_rows":len(d),
        "history_before_target_only":True,
        "definition":"1-9 earlier same-player appearances available in six-team sample",
        "model":"unchanged project_minutes_v1",
        "not_included":"historical injuries, lineup changes, DNP players; no FGA/other prop evaluation"
    },ensure_ascii=False,indent=2),encoding="utf-8")

if __name__=="__main__":
    main()

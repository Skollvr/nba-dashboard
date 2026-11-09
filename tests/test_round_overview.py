from datetime import date
import pandas as pd

from round_overview_ui import build_round_board, recorded_players


def test_round_includes_games_without_model_snapshot():
    games=pd.DataFrame([
        {"GAME_ID":"a","label":"Away x Home","GAME_TIME_BRT":"19:00","GAME_STATUS_TEXT":"A jogar"},
        {"GAME_ID":"b","label":"Two x Other","GAME_TIME_BRT":"21:00","GAME_STATUS_TEXT":"A jogar"},
    ])
    snap=[{"game_id":"a","player_id":4,"line":None,"metric":"AST","projection":4.1},
          {"game_id":"a","player_id":4,"line":4.5,"metric":"3PA","projection":5.2}]
    board=build_round_board(games,snap)
    assert len(board)==2
    assert board.iloc[0]["Projeções guardadas"]==2
    assert board.iloc[0]["Jogadores cobertos"]==1
    assert board.iloc[1]["Projeções guardadas"]==0
    assert board.iloc[0]["Linhas reais"]==1


def test_radar_only_uses_real_utc_tipoffs_not_loose_calendar_strings():
    snap=[
        {"tipoff_utc":"2026-11-10T01:00:00+00:00","metric":"3PA","player":"X",
         "projection":6.1,"game_id":"a","minutes":28},
        {"tipoff_utc":"2026-11-10T05:00:00+00:00","metric":"3PA","player":"Y",
         "projection":7.1,"game_id":"b","minutes":32},
    ]
    rows=recorded_players(snap,date(2026,11,9),"3PA")
    assert len(rows)==1 and rows.iloc[0]["Jogador"]=="X"

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from nba_api.stats.endpoints import (
    commonteamroster,
    leaguedashplayerstats,
    playergamelog,
    scoreboardv2,
)
from nba_api.stats.static import teams

st.set_page_config(
    page_title="NBA Dashboard MVP",
    page_icon="🏀",
    layout="wide",
)

TEAM_LOOKUP = {team["id"]: team for team in teams.get_teams()}

SORT_OPTIONS = {
    "PRA L5": "L5_PRA",
    "PRA L10": "L10_PRA",
    "PRA temporada": "SEASON_PRA",
    "Δ PRA L5 vs Temp": "DELTA_PRA_L5",
    "Δ PRA L10 vs Temp": "DELTA_PRA_L10",
    "PTS L5": "L5_PTS",
    "PTS L10": "L10_PTS",
    "PTS temporada": "SEASON_PTS",
    "REB L5": "L5_REB",
    "REB L10": "L10_REB",
    "REB temporada": "SEASON_REB",
    "AST L5": "L5_AST",
    "AST L10": "L10_AST",
    "AST temporada": "SEASON_AST",
    "Minutos por jogo": "SEASON_MIN",
    "Jogos na temporada": "SEASON_GP",
    "Nome do jogador": "PLAYER",
}

ROLE_OPTIONS = ["Todos", "Titular provável", "Reserva"]


def get_season_string(target_date: date) -> str:
    if target_date.month >= 10:
        start_year = target_date.year
        end_year = str(target_date.year + 1)[-2:]
    else:
        start_year = target_date.year - 1
        end_year = str(target_date.year)[-2:]
    return f"{start_year}-{end_year}"


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.4rem;
            padding-bottom: 2rem;
        }
        .main-title {
            font-size: 2.2rem;
            font-weight: 800;
            margin-bottom: 0.2rem;
        }
        .subtitle {
            color: #94a3b8;
            margin-bottom: 1.2rem;
        }
        .game-card {
            background: linear-gradient(135deg, rgba(29,78,216,.22), rgba(124,58,237,.18));
            border: 1px solid rgba(148,163,184,.16);
            border-radius: 18px;
            padding: 1rem 1.1rem;
            margin-bottom: 0.8rem;
        }
        .team-name {
            font-size: 1.15rem;
            font-weight: 700;
        }
        .vs-text {
            color: #94a3b8;
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }
        .status-pill {
            display: inline-block;
            padding: 0.25rem 0.55rem;
            border-radius: 999px;
            background: rgba(124,58,237,.18);
            border: 1px solid rgba(124,58,237,.35);
            color: #e9d5ff;
            font-size: 0.82rem;
            margin-top: 0.55rem;
        }
        .small-note {
            color: #94a3b8;
            font-size: 0.88rem;
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
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=1800, show_spinner=False)
def get_games_for_date(target_date: date) -> pd.DataFrame:
    response = scoreboardv2.ScoreboardV2(
        game_date=target_date.strftime("%Y-%m-%d"),
        day_offset=0,
        league_id="00",
        timeout=30,
    )
    data = response.get_normalized_dict()
    games = pd.DataFrame(data.get("GameHeader", []))

    if games.empty:
        return games

    games["home_team_name"] = games["HOME_TEAM_ID"].map(
        lambda x: TEAM_LOOKUP.get(x, {}).get("full_name", str(x))
    )
    games["away_team_name"] = games["VISITOR_TEAM_ID"].map(
        lambda x: TEAM_LOOKUP.get(x, {}).get("full_name", str(x))
    )
    games["label"] = (
        games["away_team_name"]
        + " @ "
        + games["home_team_name"]
        + " • "
        + games["GAME_STATUS_TEXT"].fillna("Sem status")
    )

    return games[
        [
            "GAME_ID",
            "HOME_TEAM_ID",
            "VISITOR_TEAM_ID",
            "GAME_STATUS_TEXT",
            "home_team_name",
            "away_team_name",
            "label",
        ]
    ].copy()


@st.cache_data(ttl=21600, show_spinner=False)
def get_team_roster(team_id: int, season: str) -> pd.DataFrame:
    response = commonteamroster.CommonTeamRoster(
        team_id=team_id,
        season=season,
        timeout=30,
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
    response = leaguedashplayerstats.LeagueDashPlayerStats(
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
        timeout=30,
    )
    frames = response.get_data_frames()
    if not frames:
        return pd.DataFrame()

    df = frames[0].copy()
    if df.empty:
        return pd.DataFrame(
            columns=["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GP", "MIN", "PTS", "REB", "AST"]
        )

    keep_cols = ["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GP", "MIN", "PTS", "REB", "AST"]
    available_cols = [c for c in keep_cols if c in df.columns]
    return df[available_cols].copy()


@st.cache_data(ttl=3600, show_spinner=False)
def get_player_log(player_id: int, season: str) -> pd.DataFrame:
    response = playergamelog.PlayerGameLog(
        player_id=player_id,
        season=season,
        season_type_all_star="Regular Season",
        timeout=30,
    )
    frames = response.get_data_frames()
    if not frames:
        return pd.DataFrame()

    df = frames[0].copy()
    if df.empty:
        return df

    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
    df = df.sort_values("GAME_DATE", ascending=False)
    return df


def build_team_table(team_id: int, season: str) -> pd.DataFrame:
    roster = get_team_roster(team_id, season)
    season_stats = get_league_player_stats(season, last_n_games=0)
    last5_stats = get_league_player_stats(season, last_n_games=5)
    last10_stats = get_league_player_stats(season, last_n_games=10)

    if roster.empty:
        return pd.DataFrame()

    roster_cols = ["PLAYER", "PLAYER_ID", "POSITION"]
    roster = roster[[c for c in roster_cols if c in roster.columns]].copy()

    if "POSITION" not in roster.columns:
        roster["POSITION"] = ""

    if season_stats.empty:
        season_view = pd.DataFrame(
            columns=["PLAYER_ID", "SEASON_GP", "SEASON_MIN", "SEASON_PTS", "SEASON_REB", "SEASON_AST"]
        )
    else:
        season_view = season_stats.rename(
            columns={
                "GP": "SEASON_GP",
                "MIN": "SEASON_MIN",
                "PTS": "SEASON_PTS",
                "REB": "SEASON_REB",
                "AST": "SEASON_AST",
            }
        )

    if last5_stats.empty:
        last5_view = pd.DataFrame(
            columns=["PLAYER_ID", "L5_GP", "L5_MIN", "L5_PTS", "L5_REB", "L5_AST"]
        )
    else:
        last5_view = last5_stats.rename(
            columns={
                "GP": "L5_GP",
                "MIN": "L5_MIN",
                "PTS": "L5_PTS",
                "REB": "L5_REB",
                "AST": "L5_AST",
            }
        )

    if last10_stats.empty:
        last10_view = pd.DataFrame(
            columns=["PLAYER_ID", "L10_GP", "L10_MIN", "L10_PTS", "L10_REB", "L10_AST"]
        )
    else:
        last10_view = last10_stats.rename(
            columns={
                "GP": "L10_GP",
                "MIN": "L10_MIN",
                "PTS": "L10_PTS",
                "REB": "L10_REB",
                "AST": "L10_AST",
            }
        )

    team_df = roster.merge(
        season_view[["PLAYER_ID", "SEASON_GP", "SEASON_MIN", "SEASON_PTS", "SEASON_REB", "SEASON_AST"]],
        on="PLAYER_ID",
        how="left",
    ).merge(
        last5_view[["PLAYER_ID", "L5_GP", "L5_MIN", "L5_PTS", "L5_REB", "L5_AST"]],
        on="PLAYER_ID",
        how="left",
    ).merge(
        last10_view[["PLAYER_ID", "L10_GP", "L10_MIN", "L10_PTS", "L10_REB", "L10_AST"]],
        on="PLAYER_ID",
        how="left",
    )

    numeric_cols = [
        "SEASON_GP",
        "SEASON_MIN",
        "SEASON_PTS",
        "SEASON_REB",
        "SEASON_AST",
        "L5_GP",
        "L5_MIN",
        "L5_PTS",
        "L5_REB",
        "L5_AST",
        "L10_GP",
        "L10_MIN",
        "L10_PTS",
        "L10_REB",
        "L10_AST",
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

    def classify_form(delta_pra_l5: float) -> str:
        if delta_pra_l5 >= 3.0:
            return "🔥 Forte"
        if delta_pra_l5 >= 1.0:
            return "⬆️ Boa"
        if delta_pra_l5 <= -3.0:
            return "🥶 Queda"
        if delta_pra_l5 <= -1.0:
            return "⬇️ Fraca"
        return "➖ Neutra"

    team_df["TREND"] = team_df["DELTA_PRA_L5"].apply(classify_form)

    team_df["ROLE"] = "Reserva"
    starter_pool = team_df.sort_values(
        by=["SEASON_MIN", "SEASON_GP", "PLAYER"],
        ascending=[False, False, True],
    ).head(5)
    starter_ids = starter_pool["PLAYER_ID"].tolist()
    team_df.loc[team_df["PLAYER_ID"].isin(starter_ids), "ROLE"] = "Titular provável"

    return team_df[
        [
            "PLAYER_ID",
            "PLAYER",
            "POSITION",
            "ROLE",
            "SEASON_GP",
            "SEASON_MIN",
            "SEASON_PTS",
            "L5_PTS",
            "L10_PTS",
            "SEASON_REB",
            "L5_REB",
            "L10_REB",
            "SEASON_AST",
            "L5_AST",
            "L10_AST",
            "SEASON_PRA",
            "L5_PRA",
            "L10_PRA",
            "DELTA_PRA_L5",
            "DELTA_PRA_L10",
            "TREND",
        ]
    ].copy()


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

    filtered = team_df[
        (team_df["SEASON_GP"] >= min_games) & (team_df["SEASON_MIN"] >= min_minutes)
    ].copy()

    if role_filter != "Todos":
        filtered = filtered[filtered["ROLE"] == role_filter].copy()

    if filtered.empty:
        return filtered

    if sort_column == "PLAYER":
        filtered = filtered.sort_values(
            by=["PLAYER", "SEASON_MIN"],
            ascending=[ascending, False],
        )
    else:
        filtered = filtered.sort_values(
            by=[sort_column, "SEASON_MIN", "PLAYER"],
            ascending=[ascending, False, True],
        )

    return filtered.reset_index(drop=True)


def format_team_display_df(team_df: pd.DataFrame) -> pd.DataFrame:
    display_df = team_df.copy()

    display_df["Jogador"] = display_df["PLAYER"]
    display_df["Pos"] = display_df["POSITION"].replace("", "-")
    display_df["Papel"] = display_df["ROLE"]
    display_df["GP"] = display_df["SEASON_GP"].round(0).astype(int)
    display_df["MIN"] = display_df["SEASON_MIN"].round(1)

    display_df["PTS Temp"] = display_df["SEASON_PTS"].round(1)
    display_df["PTS L5"] = display_df["L5_PTS"].round(1)
    display_df["PTS L10"] = display_df["L10_PTS"].round(1)

    display_df["REB Temp"] = display_df["SEASON_REB"].round(1)
    display_df["REB L5"] = display_df["L5_REB"].round(1)
    display_df["REB L10"] = display_df["L10_REB"].round(1)

    display_df["AST Temp"] = display_df["SEASON_AST"].round(1)
    display_df["AST L5"] = display_df["L5_AST"].round(1)
    display_df["AST L10"] = display_df["L10_AST"].round(1)

    display_df["PRA Temp"] = display_df["SEASON_PRA"].round(1)
    display_df["PRA L5"] = display_df["L5_PRA"].round(1)
    display_df["PRA L10"] = display_df["L10_PRA"].round(1)

    display_df["Δ PRA L5"] = display_df["DELTA_PRA_L5"].round(1)
    display_df["Δ PRA L10"] = display_df["DELTA_PRA_L10"].round(1)
    display_df["Trend"] = display_df["TREND"]

    return display_df[
        [
            "Jogador",
            "Pos",
            "Papel",
            "GP",
            "MIN",
            "PTS Temp",
            "PTS L5",
            "PTS L10",
            "REB Temp",
            "REB L5",
            "REB L10",
            "AST Temp",
            "AST L5",
            "AST L10",
            "PRA Temp",
            "PRA L5",
            "PRA L10",
            "Δ PRA L5",
            "Δ PRA L10",
            "Trend",
        ]
    ].copy()


def render_game_card(game_row: pd.Series) -> None:
    st.markdown(
        f"""
        <div class="game-card">
            <div class="team-name">{game_row["away_team_name"]}</div>
            <div class="vs-text">vs</div>
            <div class="team-name">{game_row["home_team_name"]}</div>
            <div class="status-pill">{game_row["GAME_STATUS_TEXT"]}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_player_chart(player_name: str, player_id: int, season: str) -> None:
    log = get_player_log(player_id, season)

    if log.empty:
        st.info("Sem histórico suficiente para esse jogador.")
        return

    recent = log[["GAME_DATE", "PTS", "REB", "AST"]].copy()
    recent = recent.dropna().sort_values("GAME_DATE").tail(10)

    if recent.empty:
        st.info("Sem histórico suficiente para esse jogador.")
        return

    recent["PRA"] = recent["PTS"] + recent["REB"] + recent["AST"]

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=recent["GAME_DATE"],
            y=recent["PTS"],
            mode="lines+markers",
            name="PTS",
            line=dict(width=3),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=recent["GAME_DATE"],
            y=recent["REB"],
            mode="lines+markers",
            name="REB",
            line=dict(width=3),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=recent["GAME_DATE"],
            y=recent["AST"],
            mode="lines+markers",
            name="AST",
            line=dict(width=3),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=recent["GAME_DATE"],
            y=recent["PRA"],
            mode="lines+markers",
            name="PRA",
            line=dict(width=3, dash="dot"),
        )
    )

    fig.update_layout(
        title=f"Últimos 10 jogos — {player_name}",
        template="plotly_dark",
        height=380,
        margin=dict(l=20, r=20, t=60, b=20),
        legend=dict(orientation="h", y=1.08, x=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15,23,42,0.35)",
    )
    fig.update_xaxes(title="")
    fig.update_yaxes(title="")

    st.plotly_chart(fig, use_container_width=True)


def render_team_section(
    team_name: str,
    team_df: pd.DataFrame,
    season: str,
    min_games: int,
    min_minutes: int,
    role_filter: str,
    sort_label: str,
    ascending: bool,
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
        st.warning(
            "Nenhum jogador passou pelos filtros. Você apertou demais o funil, pequeno curador de amostra."
        )
        return

    st.markdown(
        f"""
        <div class="info-pill">Jogadores exibidos: {len(filtered_df)}</div>
        <div class="info-pill">Filtro GP mínimo: {min_games}</div>
        <div class="info-pill">Filtro MIN mínimo: {min_minutes}</div>
        <div class="info-pill">Papel: {role_filter}</div>
        <div class="info-pill">Ordenação: {sort_label}</div>
        """,
        unsafe_allow_html=True,
    )

    display_df = format_team_display_df(filtered_df)

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
    )

    options = filtered_df[["PLAYER", "PLAYER_ID"]].drop_duplicates()
    player_name = st.selectbox(
        f"Ver gráfico de jogador — {team_name}",
        options["PLAYER"].tolist(),
        key=f"player_select_{team_name}",
    )
    selected_player_id = int(
        options.loc[options["PLAYER"] == player_name, "PLAYER_ID"].iloc[0]
    )
    render_player_chart(player_name, selected_player_id, season)


def main() -> None:
    inject_css()

    st.markdown('<div class="main-title">NBA Dashboard MVP</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtitle">Escolha o jogo e veja PTS, REB, AST e PRA com temporada, últimos 5 jogos e últimos 10 jogos.</div>',
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Configurações")
        selected_date = st.date_input("Data dos jogos", value=date.today())

        st.divider()
        st.subheader("Filtros")
        min_games = st.slider("Mínimo de jogos na temporada", 0, 82, 5, 1)
        min_minutes = st.slider("Mínimo de minutos por jogo", 0, 40, 15, 1)
        role_filter = st.selectbox("Mostrar jogadores", ROLE_OPTIONS, index=0)

        st.divider()
        st.subheader("Ordenação")
        sort_labels = list(SORT_OPTIONS.keys())
        default_sort_index = sort_labels.index("PRA L5")
        sort_label = st.selectbox(
            "Ordenar jogadores por",
            options=sort_labels,
            index=default_sort_index,
        )
        ascending = st.toggle("Ordem crescente", value=False)

        st.divider()
        st.caption("Este MVP busca os dados ao abrir a página.")
        if st.button("Forçar atualização"):
            st.cache_data.clear()
            st.rerun()

    season = get_season_string(selected_date)

    try:
        games = get_games_for_date(selected_date)
    except Exception as exc:
        st.error(
            "Deu ruim na consulta da NBA. Às vezes a fonte externa resolve implicar com a existência humana."
        )
        st.exception(exc)
        return

    st.caption(f"Temporada detectada: {season}")

    if games.empty:
        st.warning("Não encontrei jogos nessa data. A NBA também sabe estragar um calendário.")
        return

    game_label = st.selectbox("Escolha o jogo", games["label"].tolist())
    selected_game = games.loc[games["label"] == game_label].iloc[0]

    render_game_card(selected_game)

    home_team_id = int(selected_game["HOME_TEAM_ID"])
    away_team_id = int(selected_game["VISITOR_TEAM_ID"])

    try:
        away_df = build_team_table(away_team_id, season)
        home_df = build_team_table(home_team_id, season)
    except Exception as exc:
        st.error(
            "Consegui pegar o jogo, mas a coleta das estatísticas falhou. MVP grátis também tem seus surtos de diva."
        )
        st.exception(exc)
        return

    tab1, tab2 = st.tabs(
        [selected_game["away_team_name"], selected_game["home_team_name"]]
    )

    with tab1:
        render_team_section(
            team_name=selected_game["away_team_name"],
            team_df=away_df,
            season=season,
            min_games=min_games,
            min_minutes=min_minutes,
            role_filter=role_filter,
            sort_label=sort_label,
            ascending=ascending,
        )

    with tab2:
        render_team_section(
            team_name=selected_game["home_team_name"],
            team_df=home_df,
            season=season,
            min_games=min_games,
            min_minutes=min_minutes,
            role_filter=role_filter,
            sort_label=sort_label,
            ascending=ascending,
        )

    st.markdown(
        """
        <div class="small-note">
        Nota: "Titular provável" neste MVP significa os 5 jogadores do time com mais minutos por jogo na temporada.
        É um atalho útil para análise, não a escalação oficial confirmada do jogo.
        </div>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()

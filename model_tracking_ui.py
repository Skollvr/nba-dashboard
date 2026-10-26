"""Read-only model-quality dashboard based only on immutable pregame captures."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from model_tracking import METRICS, RESULTS, SNAPSHOTS, load_jsonl, tracking_frame

BRT = ZoneInfo("America/Sao_Paulo")


def model_performance_metrics(frame: pd.DataFrame) -> dict:
    """Strictly observed error metrics: no synthetic/backfilled predictions."""
    evaluated = frame.dropna(subset=["actual", "projection"]).copy()
    if evaluated.empty:
        return {"count": 0, "mae": None, "bias": None,
                "games": 0, "direction_n": 0, "direction_rate": None}
    evaluated["absolute_error"] = (
        pd.to_numeric(evaluated["projection"], errors="coerce") -
        pd.to_numeric(evaluated["actual"], errors="coerce")
    ).abs()
    edge_decisions = evaluated.loc[
        evaluated["direction_hit"].map(lambda x: isinstance(x, bool))
    ] if "direction_hit" in evaluated.columns else pd.DataFrame()
    return {
        "count": len(evaluated),
        "games": evaluated["game_id"].nunique(),
        "mae": float(evaluated["absolute_error"].mean()),
        "bias": float((evaluated["projection"] - evaluated["actual"]).mean()),
        "direction_n": len(edge_decisions),
        "direction_rate": (
            float(edge_decisions["direction_hit"].mean())
            if not edge_decisions.empty else None
        ),
    }


def render_model_performance() -> None:
    st.markdown("## 📈 Desempenho do Modelo V4")
    st.caption(
        "Monitoramento prospectivo: previsões congeladas antes dos jogos, "
        "comparadas depois com os números oficiais da ESPN. "
        "Não são backtests reconstruídos após o resultado."
    )
    snapshots = load_jsonl(SNAPSHOTS)
    results = load_jsonl(RESULTS)
    if not snapshots:
        st.info(
            "Nenhuma projeção pré-jogo registrada ainda. O histórico começa "
            "quando a rotina automática do GitHub Actions capturar um confronto "
            "futuro com dados válidos. Resultados passados não são inventados."
        )
        st.caption("Fonte do registro: GitHub Actions · rotina Monitor V4")
        return

    frame = tracking_frame(snapshots, results)
    dates = pd.to_datetime(frame["game_date_brt"], errors="coerce")
    frame = frame.loc[dates.notna()].copy()
    if frame.empty:
        st.warning("Os registros disponíveis não contêm datas válidas.")
        return
    frame["date"] = pd.to_datetime(frame["game_date_brt"], errors="coerce")
    today = datetime.now(BRT).date()
    timeframe = st.segmented_control(
        "Período de avaliação", ["Últimos 7 dias", "Últimos 30 dias", "Tudo"],
        default="Últimos 30 dias",
    )
    if timeframe != "Tudo":
        days = 7 if timeframe == "Últimos 7 dias" else 30
        cutoff = pd.Timestamp(today - pd.Timedelta(days=days - 1))
        frame = frame.loc[frame["date"] >= cutoff].copy()
    metric = st.pills("Métrica para acompanhar", list(METRICS), default="PTS")
    metric = metric or "PTS"
    frame = frame[frame["metric"] == metric].copy()
    if frame.empty:
        st.info("Nenhuma previsão registrada para esta métrica e período.")
        return
    frame["actual"] = pd.to_numeric(frame["actual"], errors="coerce")
    frame["projection"] = pd.to_numeric(frame["projection"], errors="coerce")
    confirmed = frame.dropna(subset=["actual", "projection"]).copy()
    summary = model_performance_metrics(confirmed)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Previsões registradas", len(frame))
    c2.metric("Já avaliadas", summary["count"])
    c3.metric(f"MAE · {metric}", f'{summary["mae"]:.2f}' if summary["mae"] is not None else "—")
    c4.metric("Viés médio", f'{summary["bias"]:+.2f}' if summary["bias"] is not None else "—")
    st.caption(
        f"Jogos avaliados: {summary['games']} · "
        f"Previsões aguardando resultado: {len(frame) - summary['count']} · "
        "MAE = média do erro absoluto; viés positivo = modelo projetou acima do realizado."
    )
    if summary["direction_n"]:
        st.metric(
            "Direção da linha (Over/Under)",
            f'{summary["direction_rate"]:.1%}',
            help=(
                "Frequência em que o sinal do Edge coincidiu com Over/Under "
                "nos registros com linha válida, Edge diferente de zero e sem push. "
                "Não representa retorno financeiro nem probabilidade calibrada."
            ),
        )
        st.caption(f"{summary['direction_n']} decisões avaliadas com cotação válida.")

    if confirmed.empty:
        st.info(
            "Já existem previsões anteriores aos jogos, mas ainda não há "
            "resultados oficiais correspondentes. Os gráficos serão preenchidos "
            "somente após a conferência."
        )
        return

    confirmed["absolute_error"] = (confirmed["projection"] - confirmed["actual"]).abs()
    daily = confirmed.groupby("date", as_index=False).agg(
        MAE=("absolute_error", "mean"), Amostra=("key", "size")
    ).sort_values("date")
    st.markdown("#### Evolução do erro médio")
    fig = go.Figure(go.Scatter(
        x=daily["date"], y=daily["MAE"], mode="lines+markers",
        line=dict(color="#57cdbd", width=3), marker=dict(size=8),
        customdata=daily["Amostra"],
        hovertemplate="Data: %{x|%d/%m/%Y}<br>MAE: %{y:.2f}<br>Previsões: %{customdata}<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_dark", height=270, margin=dict(l=15, r=18, t=20, b=25),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(13,28,43,0.75)",
        showlegend=False,
    )
    fig.update_yaxes(title_text="MAE", rangemode="tozero", gridcolor="rgba(157,189,208,.12)")
    fig.update_xaxes(title_text="", tickformat="%d/%m")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    st.markdown("#### Últimas projeções conferidas")
    table = confirmed.sort_values(["date", "game_id"], ascending=False).head(60)
    cols = {
        "date": "Data", "player": "Jogador", "projection": "Proj.",
        "actual": "Real", "absolute_error": "Erro abs.", "season_gp": "Jogos na época",
        "line": "Linha", "line_source": "Casa",
    }
    display = table[[k for k in cols if k in table]].rename(columns=cols).copy()
    display["Data"] = pd.to_datetime(display["Data"]).dt.strftime("%d/%m/%Y")
    st.dataframe(display, use_container_width=True, hide_index=True)
    st.download_button(
        "Baixar registros avaliados (CSV)",
        data=confirmed.to_csv(index=False).encode("utf-8"),
        file_name=f"monitor_v4_{metric.lower()}.csv",
        mime="text/csv",
    )

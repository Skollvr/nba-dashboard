"""Read-only model-quality dashboard based only on immutable pregame captures."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from model_tracking import (
    METRICS, RESULTS, SNAPSHOTS, load_jsonl, tracking_frame,
    projection_target,
)

BRT = ZoneInfo("America/Sao_Paulo")


def parse_tracking_dates(values: pd.Series) -> pd.Series:
    """Parse Brazilian and ISO dates without reinterpreting ISO months/days."""
    raw = values.astype("string").str.strip()
    is_iso = raw.str.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}").fillna(False)
    iso_dates = pd.to_datetime(
        raw.where(is_iso), format="%Y-%m-%d", errors="coerce"
    )
    br_dates = pd.to_datetime(
        raw.where(~is_iso), format="%d/%m/%Y", errors="coerce"
    )
    return iso_dates.fillna(br_dates)


def model_performance_metrics(frame: pd.DataFrame) -> dict:
    """Assess target attainment separately from conventional forecast accuracy.

    Target met: actual >= floor(projection), using whole-count NBA stats;
    shortfall: max(floor(projection) - actual, 0).
    MAE stays as a technical measure of numerical forecast accuracy.
    Unresolved and nonnumeric observations must never count as successes.
    """
    evaluated = frame.copy()
    if evaluated.empty or not {"actual", "projection"}.issubset(evaluated.columns):
        return {
            "count": 0, "games": 0, "target_count": 0, "target_rate": None,
            "miss_count": 0, "shortfall_avg": None, "shortfall_on_misses": None,
            "mae": None, "bias": None, "direction_n": 0, "direction_rate": None,
        }
    evaluated["projection"] = pd.to_numeric(
        evaluated["projection"], errors="coerce"
    )
    evaluated["actual"] = pd.to_numeric(evaluated["actual"], errors="coerce")
    evaluated = evaluated.replace([float("inf"), -float("inf")], float("nan"))
    evaluated = evaluated.dropna(subset=["actual", "projection"]).copy()
    evaluated["target_minimum"] = evaluated["projection"].map(projection_target)
    evaluated = evaluated.dropna(subset=["target_minimum"]).copy()
    if evaluated.empty:
        return model_performance_metrics(pd.DataFrame())

    difference = evaluated["projection"] - evaluated["actual"]
    shortfall = (evaluated["target_minimum"] - evaluated["actual"]).clip(lower=0)
    reached = evaluated["actual"] >= evaluated["target_minimum"]
    misses = ~reached
    edge_decisions = (
        evaluated.loc[
            evaluated["direction_hit"].map(
                lambda value: isinstance(value, (bool, __import__("numpy").bool_))
            )
        ]
        if "direction_hit" in evaluated.columns else pd.DataFrame()
    )
    return {
        "count": len(evaluated),
        "games": evaluated["game_id"].nunique()
        if "game_id" in evaluated.columns else 0,
        "target_count": int(reached.sum()),
        "target_rate": float(reached.mean()),
        "miss_count": int(misses.sum()),
        # Includes zero for above-target results; interpreted per projection.
        "shortfall_avg": float(shortfall.mean()),
        "shortfall_on_misses": (
            float(shortfall.loc[misses].mean()) if misses.any() else 0.0
        ),
        "mae": float(difference.abs().mean()),
        "bias": float(difference.mean()),
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
    # ESPN schedules currently return DD/MM/YYYY while other sources and
    # tests use ISO dates. Both must remain readable without rewriting the
    # immutable pregame snapshots already committed to GitHub.
    dates = parse_tracking_dates(frame["game_date_brt"])
    frame = frame.loc[dates.notna()].copy()
    if frame.empty:
        st.warning("Os registros disponíveis não contêm datas válidas.")
        return
    frame["date"] = dates.loc[frame.index]
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
    c3.metric(
        "Metas atingidas",
        f'{summary["target_rate"]:.1%}' if summary["target_rate"] is not None else "—",
        help=(
            "Resultado real igual ou superior à parte inteira da projeção V4: "
            "27,4 exige 27; 4,7 exige 4. Não é o acerto de uma linha de aposta."
        ),
    )
    c4.metric(
        f"Déficit médio · {metric}",
        f'{summary["shortfall_avg"]:.2f}'
        if summary["shortfall_avg"] is not None else "—",
        help=(
            "Média de max(parte inteira da projeção − resultado real, 0). "
            "Atingir ou superar a meta inteira gera déficit zero."
        ),
    )
    st.caption(
        f"Metas cumpridas: {summary['target_count']} · "
        f"Abaixo da meta: {summary['miss_count']} · "
        f"Jogos avaliados: {summary['games']} · "
        f"Aguardando resultado: {len(frame) - summary['count']}. "
        "Meta mínima = parte inteira da projeção (sem arredondar para cima). "
        "Este critério mede alcance da meta, não precisão da previsão ou ganho em apostas."
    )
    with st.expander("Ver precisão estatística tradicional (MAE e viés)", expanded=False):
        st.caption(
            "A diferença numérica é analisada nos dois sentidos aqui: "
            "superar ou ficar abaixo da projeção contribui para o MAE. "
            "Esses diagnósticos são separados da taxa de metas atingidas."
        )
        first, second, third = st.columns(3)
        first.metric(
            f"MAE · {metric}",
            f'{summary["mae"]:.2f}' if summary["mae"] is not None else "—",
        )
        second.metric(
            "Viés médio",
            f'{summary["bias"]:+.2f}' if summary["bias"] is not None else "—",
        )
        third.metric(
            "Déficit médio entre falhas",
            f'{summary["shortfall_on_misses"]:.2f}'
            if summary["shortfall_on_misses"] is not None else "—",
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
    confirmed["target_minimum"] = confirmed["projection"].map(projection_target)
    confirmed["shortfall"] = (
        confirmed["target_minimum"] - confirmed["actual"]
    ).clip(lower=0)
    confirmed["target_reached"] = (
        confirmed["actual"] >= confirmed["target_minimum"]
    )
    confirmed["status"] = confirmed["target_reached"].map({
        True: "Meta atingida", False: "Abaixo da meta"
    })
    daily = confirmed.groupby("date", as_index=False).agg(
        Deficit=("shortfall", "mean"), Amostra=("key", "size"),
        Metas=("target_reached", "mean"),
    ).sort_values("date")
    st.markdown("#### Evolução do déficit médio por previsão")
    fig = go.Figure(go.Scatter(
        x=daily["date"], y=daily["Deficit"], mode="lines+markers",
        line=dict(color="#57cdbd", width=3), marker=dict(size=8),
        customdata=daily["Amostra"],
        hovertemplate="Data: %{x|%d/%m/%Y}<br>Déficit médio: %{y:.2f}<br>Previsões: %{customdata}<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_dark", height=270, margin=dict(l=15, r=18, t=20, b=25),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(13,28,43,0.75)",
        showlegend=False,
    )
    fig.update_yaxes(title_text="Déficit médio", rangemode="tozero", gridcolor="rgba(157,189,208,.12)")
    fig.update_xaxes(title_text="", tickformat="%d/%m")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    st.markdown("#### Últimas projeções conferidas")
    table = confirmed.sort_values(["date", "game_id"], ascending=False).head(60)
    cols = {
        "date": "Data", "player": "Jogador", "projection": "Proj.",
        "actual": "Real", "target_minimum": "Meta mínima",
        "status": "Situação", "shortfall": "Déficit",
        "season_gp": "Jogos na época", "line": "Linha", "line_source": "Casa",
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

"""Audit that GitHub Actions really persisted what the V4 monitor reported.

Run before and after the capture/settlement steps. The audit fails for
missing/duplicated rows or reported counts inconsistent with saved files.
It does not falsely fail a successful no-op when all eligible games were
already registered or when a schedule had no games.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from model_tracking import SNAPSHOTS, RESULTS, load_jsonl

BASELINE = Path(".monitor_before.json")


def audit_counts() -> dict:
    forecasts = load_jsonl(SNAPSHOTS)
    results = load_jsonl(RESULTS)
    return {
        "snapshots": len(forecasts),
        "results": len(results),
        "games": len({str(row.get("game_id", "")) for row in forecasts}),
        "game_ids": sorted({str(row.get("game_id", "")) for row in forecasts}),
        "snapshot_keys": [str(row.get("key", "")) for row in forecasts],
        "result_keys": [str(row.get("key", "")) for row in results],
    }


def _reported_count(path: Path, mode: str) -> int | None:
    if not path.exists():
        return None
    matches = re.findall(
        rf"^\s*{re.escape(mode)}:\s*(\d+)\s+new rows\s*$",
        path.read_text(encoding="utf-8", errors="replace"),
        flags=re.MULTILINE,
    )
    return int(matches[-1]) if matches else None


def assess(before: dict, after: dict,
           expected_snapshots: int | None = None,
           expected_results: int | None = None) -> dict:
    """Compare persisted data against before-run baseline and CLI counts."""
    old_snapshots = before["snapshots"]
    old_results = before["results"]
    fresh = after["snapshots"] - old_snapshots
    settled = after["results"] - old_results
    added_games = sorted(set(after["game_ids"]) - set(before["game_ids"]))

    if fresh < 0 or settled < 0:
        raise ValueError("Previously recorded predictions/results disappeared")
    for label, keys in (
        ("snapshots", after["snapshot_keys"]),
        ("results", after["result_keys"]),
    ):
        if not all(keys) or len(keys) != len(set(keys)):
            raise ValueError(f"Invalid or duplicated keys in {label}")

    if not set(after["result_keys"]).issubset(set(after["snapshot_keys"])):
        raise ValueError("There are results without original pregame snapshots")
    if expected_snapshots is not None and expected_snapshots != fresh:
        raise ValueError(
            f"Capture claimed {expected_snapshots} new rows but saved {fresh}"
        )
    if expected_results is not None and expected_results != settled:
        raise ValueError(
            f"Settlement claimed {expected_results} new rows but saved {settled}"
        )

    return {
        "new_snapshots": fresh,
        "new_games": len(added_games),
        "new_game_ids": added_games,
        "new_results": settled,
        "snapshots_total": after["snapshots"],
        "games_total": after["games"],
        "results_total": after["results"],
    }


def _summary(report: dict, snapshot_checked: bool, settlement_checked: bool) -> str:
    date_label = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    lines = [
        "## Monitor V4 — conferência da execução",
        "",
        f"Registro gerado em: {date_label}",
        "",
        "| Indicador | Resultado |",
        "|---|---:|",
        f"| Novas projeções salvas | **{report['new_snapshots']}** |",
        f"| Novos confrontos salvos | **{report['new_games']}** |",
        f"| Total de projeções no GitHub | {report['snapshots_total']} |",
        f"| Total de confrontos registrados | {report['games_total']} |",
        f"| Novos resultados conferidos | **{report['new_results']}** |",
        f"| Total de resultados | {report['results_total']} |",
    ]
    if report["new_game_ids"]:
        lines += [
            "",
            "Confrontos novos (IDs ESPN): " + ", ".join(report["new_game_ids"]),
        ]
    if snapshot_checked and report["new_snapshots"] == 0:
        lines += [
            "",
            "⚠️ **Captura sem novos registros.** Pode não haver jogos elegíveis "
            "na janela de 20 minutos a 6 horas, ou todos já podem estar salvos. "
            "Examine os logs para confirmar a cobertura.",
        ]
    if settlement_checked and report["new_results"] == 0:
        lines += [
            "",
            "ℹ️ Nenhum resultado adicional foi encontrado nesta execução.",
        ]
    lines += [
        "",
        "A conferência compara arquivos reais antes/depois; um job verde "
        "**não garante** que todas as partidas da rodada tenham sido capturadas.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["before", "after"])
    opts = parser.parse_args()
    if opts.stage == "before":
        BASELINE.write_text(
            json.dumps(audit_counts(), ensure_ascii=False), encoding="utf-8"
        )
        print("Monitor baseline saved:", BASELINE.as_posix())
        return

    if not BASELINE.exists():
        raise RuntimeError("No baseline: cannot audit this monitor execution")
    before = json.loads(BASELINE.read_text(encoding="utf-8"))
    expected_snapshots = _reported_count(Path("capture.log"), "snapshot")
    expected_results = _reported_count(Path("settle.log"), "settle")
    if Path("capture.log").exists() and expected_snapshots is None:
        raise ValueError("Capture log does not declare its recorded row count")
    if Path("settle.log").exists() and expected_results is None:
        raise ValueError("Settlement log does not declare its recorded row count")

    report = assess(before, audit_counts(), expected_snapshots, expected_results)
    output = _summary(
        report, expected_snapshots is not None, expected_results is not None
    )
    print(output)
    if expected_snapshots is not None and report["new_snapshots"] == 0:
        print("::warning::Monitor V4 capture ran, but saved zero new projections")
    if path := os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(output)


if __name__ == "__main__":
    main()

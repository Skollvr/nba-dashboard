"""Non-destructive monthly archive/export of immutable NBA JSONL files.

Usage: python archive_model_data.py --output exports/model-history
Creates time-partitioned copies, verifies counts and SHA-256 of individual
source records, and NEVER deletes/overwrites source JSONL. Output directory
must not be the production data directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from model_tracking import SNAPSHOTS, RESULTS, load_jsonl


def _month(row, fallback="undated"):
    for field in ("game_date_brt", "settled_utc", "tipoff_utc"):
        raw = str(row.get(field) or "")
        if len(raw) >= 7 and raw[4:5] == "-" and raw[7:8] in {"-", "T", ""}:
            return raw[:7]
        if len(raw) >= 10 and raw[2:3] == "/" and raw[5:6] == "/":
            return raw[6:10] + "-" + raw[3:5]
    return fallback


def export_partitioned(output: Path, snaps=SNAPSHOTS, results=RESULTS) -> dict:
    output = output.resolve()
    if output == snaps.parent.resolve() or output == results.parent.resolve():
        raise ValueError("Archive directory must differ from live data directory")
    source = {"snapshots": load_jsonl(snaps), "results": load_jsonl(results)}
    index = {"format": "monthly-jsonl-export-v1", "files": {}, "counts": {}}
    output.mkdir(parents=True, exist_ok=True)
    for kind, rows in source.items():
        partitions = defaultdict(list)
        for row in rows:
            partitions[_month(row)].append(row)
        totals = 0
        for month, entries in sorted(partitions.items()):
            dest = output / month / f"{kind}.jsonl"
            dest.parent.mkdir(parents=True, exist_ok=True)
            payload = "".join(json.dumps(r,ensure_ascii=False,sort_keys=True,allow_nan=False)+"\n" for r in entries)
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            dest.write_text(payload,encoding="utf-8")
            # Verify a lossless record count and independent content digest.
            check=dest.read_bytes()
            if len(check.splitlines()) != len(entries) or hashlib.sha256(check).hexdigest() != digest:
                raise ValueError(f"Archive verification failed: {dest}")
            index["files"][str(dest.relative_to(output))] = {
                "records": len(entries), "sha256": digest,
            }
            totals += len(entries)
        if totals != len(rows):
            raise ValueError(f"Archive count mismatch for {kind}")
        index["counts"][kind] = totals
    (output / "manifest.json").write_text(json.dumps(index,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return index


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    opts=parser.parse_args()
    result=export_partitioned(opts.output)
    print(json.dumps(result["counts"], ensure_ascii=False))

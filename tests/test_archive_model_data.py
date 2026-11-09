import json
import pytest

from archive_model_data import export_partitioned


def test_archiving_is_lossless_and_keeps_sources_untouched(tmp_path):
    src=tmp_path/"data"
    src.mkdir()
    s=src/"model_snapshots.jsonl"
    r=src/"model_results.jsonl"
    s.write_text(json.dumps({"key":"g:1:AST","game_date_brt":"2026-11-09"})+"\n",encoding="utf-8")
    r.write_text(json.dumps({"key":"g:1:AST","settled_utc":"2026-11-10T12:00:00+00:00"})+"\n",encoding="utf-8")
    original=(s.read_bytes(),r.read_bytes())
    out=tmp_path/"export"
    manifest=export_partitioned(out,s,r)
    assert manifest["counts"]=={"snapshots":1,"results":1}
    assert (out/"2026-11"/"snapshots.jsonl").exists()
    assert (out/"2026-11"/"results.jsonl").exists()
    assert original==(s.read_bytes(),r.read_bytes())
    with pytest.raises(ValueError):
        export_partitioned(src,s,r)

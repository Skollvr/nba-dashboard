from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from pdf_reader import (
    _injury_links_from_html,
    _official_pdf_recent_candidates,
    fetch_latest_injury_report_pdf_url,
    parse_report_dt_from_url,
)


def test_extract_pdf_links_with_both_quote_styles_and_query_parameters():
    html = (
        "<a href='https://ak-static.cms.nba.com/referee/injury/"
        "Injury-Report_2026-10-22_01_15PM.pdf?ref=1'>"
        "<a data-href=\"/referee/injury/Injury-Report_2026-10-22_01_00PM.pdf\">"
    )
    links = _injury_links_from_html(
        html, "https://official.nba.com/nba-injury-report-2026-27-season/"
    )
    assert len(links) == 2
    assert "01_15PM.pdf" in links[0]
    assert parse_report_dt_from_url(links[0]).hour == 13
    assert parse_report_dt_from_url(links[1]).minute == 0


def test_cdn_candidates_follow_official_quarter_hour_file_names():
    now = datetime(2026, 10, 22, 13, 27, tzinfo=ZoneInfo("America/New_York"))
    candidates = _official_pdf_recent_candidates(now)
    assert len(candidates) == 9
    assert candidates[0].endswith("Injury-Report_2026-10-22_01_15PM.pdf")
    assert candidates[1].endswith("Injury-Report_2026-10-22_01_00PM.pdf")
    assert candidates[2].endswith("Injury-Report_2026-10-22_12_45PM.pdf")


def test_cdn_fallback_works_when_official_webpage_cannot_be_loaded():
    fetch_latest_injury_report_pdf_url.clear()
    now = datetime(2026, 10, 22, 13, 27, tzinfo=ZoneInfo("America/New_York"))
    with (
        patch("pdf_reader.datetime") as mock_date,
        patch("pdf_reader.requests.get", side_effect=RuntimeError("blocked")),
        patch("pdf_reader._pdf_header_ok") as valid_pdf,
    ):
        mock_date.now.return_value = now
        mock_date.strptime.side_effect = datetime.strptime
        valid_pdf.side_effect = lambda url: url.endswith(
            "Injury-Report_2026-10-22_01_15PM.pdf"
        )
        result = fetch_latest_injury_report_pdf_url()
    assert result.endswith("Injury-Report_2026-10-22_01_15PM.pdf")


def test_non_pdf_header_is_rejected():
    from pdf_reader import _pdf_header_ok
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            yield b"<html>not a pdf</html>"

    with patch("pdf_reader.requests.get", return_value=FakeResponse()):
        assert not _pdf_header_ok("https://example.com/fake.pdf")

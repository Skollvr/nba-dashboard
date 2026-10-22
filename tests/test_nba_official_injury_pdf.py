from datetime import date
from unittest.mock import patch

import pandas as pd

from config import TEAM_LOOKUP
from pdf_reader import (
    parse_official_injury_report_lines,
    extract_pdf_text_lines,
    _page_text_is_reversed,
    parse_injury_report_timestamp_from_url,
)
from injury_sources import get_preferred_injury_report
from processamento import merge_injury_report


PDF_URL = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-22_11_30AM.pdf"
PHI = next(tid for tid, data in TEAM_LOOKUP.items()
           if data.get("abbreviation") == "PHI")
CLE = next(tid for tid, data in TEAM_LOOKUP.items()
           if data.get("abbreviation") == "CLE")
CHI = next(tid for tid, data in TEAM_LOOKUP.items()
           if data.get("abbreviation") == "CHI")


def _pdf_rows():
    # Content from NBA's official 22 Oct 2026, 11:30 AM ET report.
    return [
        "Injury Report: 10/22/26 11:30 AM",
        "Page 1 of 3",
        "Game Date Game Time Matchup Team Player Name Current Status Reason",
        "10/22/2026 07:00 (ET) CLE@PHI Cleveland Cavaliers Allen, Jarrett Questionable",
        "Injury/Illness - Right Patellar",
        "Tendinopathy",
        "Diop, Khalifa Out Injury/Illness - Left Knee Injury Management",
        "Philadelphia 76ers Barlow, Dominick Out",
        "Injury/Illness - Low Back Stress",
        "Reaction",
        "Fall, Tacko Out Injury/Illness - Left Quadriceps Strain",
        "Hukporti, Ariel Out",
        "Injury/Illness - Right Achilles Tendon",
        "Rupture / Tear",
        "Wade, Dean Questionable Injury/Illness - Left Ankle Sprain",
        "10/23/2026 04:00 (ET) CHI@MIL Chicago Bulls NOT YET SUBMITTED",
        "Milwaukee Bucks NOT YET SUBMITTED",
    ]


def test_official_pdf_keeps_team_matchup_and_real_injuries():
    parsed = parse_official_injury_report_lines(_pdf_rows(), PDF_URL)
    phi = parsed[parsed["TEAM_ID_IR"].eq(PHI)]
    assert len(phi) == 4
    by_name = phi.set_index("PLAYER_NAME_IR")
    assert by_name.loc["Barlow, Dominick", "INJ_STATUS"] == "Out"
    assert by_name.loc["Fall, Tacko", "INJ_STATUS"] == "Out"
    assert by_name.loc["Hukporti, Ariel", "INJ_STATUS"] == "Out"
    assert by_name.loc["Wade, Dean", "INJ_STATUS"] == "Questionable"
    assert all(phi["MATCHUP"] == "CLE@PHI")
    assert all(phi["QUERY_OK"])
    assert "Low Back Stress Reaction" in by_name.loc[
        "Barlow, Dominick", "INJ_REASON"
    ]


def test_pdf_team_filter_and_names_match_current_roster():
    parsed = parse_official_injury_report_lines(_pdf_rows(), PDF_URL)
    with patch("injury_sources.fetch_latest_injury_report_df", return_value=parsed):
        result = get_preferred_injury_report(
            (CLE, PHI), "CLE@PHI", target_date=date(2026, 10, 22),
            today=date(2026, 10, 22)
        )
    assert len(result[result["TEAM_ID_IR"].eq(PHI)]) == 4
    team_df = pd.DataFrame([
        {"PLAYER": "Dominick Barlow", "PLAYER_ID": 1},
        {"PLAYER": "Ariel Hukporti", "PLAYER_ID": 2},
        {"PLAYER": "Dean Wade", "PLAYER_ID": 3},
        {"PLAYER": "Tyrese Maxey", "PLAYER_ID": 4},
    ])
    merged = merge_injury_report(
        team_df, result, "Philadelphia 76ers", PHI, game_matchup="CLE@PHI",
    ).set_index("PLAYER")
    assert merged.loc["Dominick Barlow", "INJ_STATUS"] == "Out"
    assert merged.loc["Ariel Hukporti", "IS_UNAVAILABLE"]
    assert merged.loc["Dean Wade", "INJ_STATUS"] == "Questionable"
    assert merged.loc["Tyrese Maxey", "INJ_STATUS"] == "Available"
    assert merged.loc["Tyrese Maxey", "INJ_SOURCE"] == "NBA Official PDF"
    assert merged.loc["Tyrese Maxey", "INJ_REPORT_URL"] == PDF_URL


def test_not_yet_submitted_does_not_become_available():
    parsed = parse_official_injury_report_lines(_pdf_rows(), PDF_URL)
    chi = parsed[parsed["TEAM_ID_IR"].eq(CHI)]
    assert len(chi) == 1
    assert chi.iloc[0]["TEAM_REPORT_STATUS"] == "NOT YET SUBMITTED"
    assert not chi.iloc[0]["QUERY_OK"]
    with patch("injury_sources.fetch_latest_injury_report_df", return_value=parsed):
        got = get_preferred_injury_report(
            (CHI,), "CHI@MIL", target_date=date(2026, 10, 23),
            today=date(2026, 10, 22)
        )
    merged = merge_injury_report(
        pd.DataFrame([{"PLAYER": "Sample Player", "PLAYER_ID": 99}]),
        got, "Chicago Bulls", CHI, game_matchup="CHI@MIL",
    )
    assert merged.iloc[0]["INJ_STATUS"] == "—"
    assert merged.iloc[0]["TEAM_REPORT_STATUS"] == "NOT YET SUBMITTED"
    assert not merged.iloc[0]["INJ_MATCHUP_FOUND"]


def test_pdf_url_exposes_brasilia_report_time():
    values = parse_injury_report_timestamp_from_url(PDF_URL)
    assert "22/10" in values["report_label_brt"]
    assert "11:30" in values["report_label_et"]



def test_detects_reversed_nba_pdf_row_sequence_by_game_chronology():
    # pypdf(layout) sometimes reads the official table from bottom to top.
    descending = [
        "10/23/2026 04:00 (ET) CHI@MIL Chicago Bulls NOT YET SUBMITTED",
        "09:30 (ET) DEN@OKC Oklahoma City Thunder Topic, Nikola Out",
        "07:00 (ET) CLE@PHI Philadelphia 76ers Barlow, Dominick Out",
        "10/22/2026 07:00 (ET) CLE@PHI Cleveland Cavaliers Allen, Jarrett Questionable",
        "Game Date Game Time Matchup Team Player Name Current Status Reason",
    ]
    assert _page_text_is_reversed(descending) is True
    assert _page_text_is_reversed(list(reversed(descending))) is False


def test_extraction_reverses_each_pdf_page_not_full_document():
    page_one = [
        "Game Date Game Time Matchup Team Player Name Current Status Reason",
        "10/22/2026 07:00 (ET) CLE@PHI Cleveland Cavaliers Allen, Jarrett Questionable",
        "Philadelphia 76ers Barlow, Dominick Out",
        "Fall, Tacko Out",
        "Hukporti, Ariel Out",
        "Wade, Dean Questionable",
        "09:30 (ET) DEN@OKC Denver Nuggets Jones, Spencer Questionable",
        "Oklahoma City Thunder Topic, Nikola Out",
        "10/23/2026 04:00 (ET) CHI@MIL Chicago Bulls NOT YET SUBMITTED",
    ]
    page_two = [
        "Milwaukee Bucks NOT YET SUBMITTED",
        "07:00 (ET) ATL@CHA Atlanta Hawks NOT YET SUBMITTED",
        "Charlotte Hornets NOT YET SUBMITTED",
        "10:00 (ET) LAC@LAL LA Clippers NOT YET SUBMITTED",
    ]

    class MockPage:
        def __init__(self, rows):
            self.rows = rows

        def extract_text(self, extraction_mode=None):
            return "\\n".join(reversed(self.rows))

    class MockReader:
        def __init__(self, contents):
            self.pages = [MockPage(page_one), MockPage(page_two)]

    with patch("pdf_reader.PdfReader", MockReader):
        extracted = extract_pdf_text_lines(b"%PDF fixture")
    parsed = parse_official_injury_report_lines(extracted, PDF_URL)

    phi = parsed[parsed["TEAM_ID_IR"].eq(PHI)].set_index("PLAYER_NAME_IR")
    assert len(phi) == 4
    assert set(phi.index) == {
        "Barlow, Dominick", "Fall, Tacko", "Hukporti, Ariel", "Wade, Dean",
    }
    assert all(phi["MATCHUP"] == "CLE@PHI")
    assert parsed.loc[
        parsed["TEAM_NAME_IR"].eq("Oklahoma City Thunder"), "MATCHUP"
    ].eq("DEN@OKC").all()


def test_parser_discards_explicit_cross_team_rows_instead_of_mislabeling():
    wrong = [
        "10/22/2026 09:30 (ET) DEN@OKC Denver Nuggets Jones, Spencer Questionable",
        "Philadelphia 76ers Barlow, Dominick Out",
        "Fall, Tacko Out",
        "10/22/2026 07:00 (ET) CLE@PHI Philadelphia 76ers Wade, Dean Questionable",
    ]
    parsed = parse_official_injury_report_lines(wrong, PDF_URL)
    assert "Barlow, Dominick" not in parsed.get(
        "PLAYER_NAME_IR", pd.Series(dtype=str)
    ).tolist()
    assert "Fall, Tacko" not in parsed.get(
        "PLAYER_NAME_IR", pd.Series(dtype=str)
    ).tolist()
    assert "Wade, Dean" in parsed["PLAYER_NAME_IR"].tolist()

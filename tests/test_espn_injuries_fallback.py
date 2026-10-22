import pandas as pd
from unittest.mock import patch

from espn_context import get_espn_injuries_standard
from processamento import merge_injury_report


PHI_ID = 1610612755


def _roster():
    return pd.DataFrame([
        {"PLAYER": "Dominick Barlow", "PLAYER_ID": 1001},
        {"PLAYER": "Tacko Fall", "PLAYER_ID": 1002},
        {"PLAYER": "Ariel Hukporti", "PLAYER_ID": 1003},
        {"PLAYER": "Dean Wade", "PLAYER_ID": 1004},
        {"PLAYER": "Joel Embiid", "PLAYER_ID": 1005},
    ])


def _injury(pid, name, status):
    return {
        "athlete": {"id": str(pid), "displayName": name},
        "team": {"id": "20", "abbreviation": "PHI"},
        "status": status,
        "injury": {"location": "lower body", "type": "test"},
    }


def test_empty_successful_team_response_does_not_mean_everyone_available():
    get_espn_injuries_standard.clear()
    with (
        patch("espn_context.get_espn_team_directory", return_value=pd.DataFrame([
            {"ESPN_TEAM_ID": 20, "TEAM_ID": PHI_ID}
        ])),
        patch("espn_context._espn_team_id_for_official", return_value=20),
        patch("espn_context._request_json", return_value={"injuries": []}) as request,
    ):
        injuries = get_espn_injuries_standard((PHI_ID,))

    result = merge_injury_report(
        _roster(), injuries, "Philadelphia 76ers", PHI_ID
    )
    assert result["INJ_STATUS"].eq("—").all()
    assert not result["INJ_MATCHUP_FOUND"].any()
    assert request.call_count == 2  # per-team + league fallback


def test_empty_team_response_can_be_recovered_from_espn_league_feed():
    get_espn_injuries_standard.clear()

    def response(url):
        if str(url).endswith("/teams/20/injuries"):
            return {"injuries": []}
        return {"injuries": [
            _injury(1001, "Dominick Barlow", "Out"),
            _injury(1004, "Dean Wade", "Day-To-Day"),
        ]}

    with (
        patch("espn_context.get_espn_team_directory", return_value=pd.DataFrame([
            {"ESPN_TEAM_ID": 20, "TEAM_ID": PHI_ID}
        ])),
        patch("espn_context._espn_team_id_for_official", return_value=20),
        patch("espn_context._request_json", side_effect=response),
    ):
        injuries = get_espn_injuries_standard((PHI_ID,))

    report = merge_injury_report(
        _roster(), injuries, "Philadelphia 76ers", PHI_ID
    ).set_index("PLAYER")
    assert report.loc["Dominick Barlow", "INJ_STATUS"] == "Out"
    assert report.loc["Dean Wade", "INJ_STATUS"] == "Questionable"
    assert report.loc["Joel Embiid", "INJ_STATUS"] == "Available"
    assert bool(report.loc["Dominick Barlow", "IS_UNAVAILABLE"])


def test_valid_team_feed_injury_status_overrides_depth_chart():
    get_espn_injuries_standard.clear()
    with (
        patch("espn_context.get_espn_team_directory", return_value=pd.DataFrame([
            {"ESPN_TEAM_ID": 20, "TEAM_ID": PHI_ID}
        ])),
        patch("espn_context._espn_team_id_for_official", return_value=20),
        patch("espn_context._request_json", return_value={"injuries": [
            _injury(1003, "Ariel Hukporti", "Out for Season"),
            _injury(1002, "Tacko Fall", "Out"),
        ]}),
    ):
        injuries = get_espn_injuries_standard((PHI_ID,))

    report = merge_injury_report(
        _roster(), injuries, "Philadelphia 76ers", PHI_ID
    ).set_index("PLAYER")
    assert report.loc["Ariel Hukporti", "INJ_STATUS"] == "Out"
    assert report.loc["Tacko Fall", "INJ_STATUS"] == "Out"
    assert bool(report.loc["Ariel Hukporti", "IS_UNAVAILABLE"])
    assert report.loc["Joel Embiid", "INJ_STATUS"] == "Available"

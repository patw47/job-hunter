"""
Unit tests for weekly_stats.py — no live API calls, gspread fully mocked.
"""
from __future__ import annotations

import datetime
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import weekly_stats

WEEK_MON = "2026-01-05"
WEEK_SUN = "2026-01-11"

_HEADER = [
    "job_id", "date_scanned", "title", "company", "location",
    "remote", "url", "source", "match_rate", "skills_found",
    "status", "cv_drive_link", "letter_drive_link", "applied_at", "notes",
]


def _row(
    date: str = WEEK_MON,
    title: str = "Ingénieur",
    company: str = "ACME",
    source: str = "indeed",
    match_rate: str = "75.0",
    status: str = "Notifié",
) -> list[str]:
    row = [""] * 15
    row[1] = date
    row[2] = title
    row[3] = company
    row[4] = "Geneva, CH"
    row[7] = source
    row[8] = match_rate
    row[10] = status
    return row


def _make_sheet(rows: list[list[str]]) -> MagicMock:
    sheet = MagicMock()
    sheet.get_all_values.return_value = [_HEADER, *rows]
    return sheet


def _week_bounds() -> tuple[datetime.date, datetime.date]:
    return datetime.date(2026, 1, 5), datetime.date(2026, 1, 11)


# ── compute_week_bounds ───────────────────────────────────────────────────────


class TestWeekBoundaryComputation(unittest.TestCase):
    def test_week_end_is_six_days_after_start(self) -> None:
        start, end = weekly_stats.compute_week_bounds(WEEK_MON)
        assert start == datetime.date(2026, 1, 5)
        assert end == datetime.date(2026, 1, 11)

    def test_explicit_monday_accepted(self) -> None:
        start, _ = weekly_stats.compute_week_bounds(WEEK_MON)
        assert start.weekday() == 0

    def test_non_monday_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            weekly_stats.compute_week_bounds("2026-01-06")

    def test_default_week_is_current_iso_week_monday(self) -> None:
        fixed_tuesday = datetime.date(2026, 6, 10)
        with patch("weekly_stats.datetime") as mock_dt:
            mock_dt.date.today.return_value = fixed_tuesday
            mock_dt.date.fromisoformat.side_effect = datetime.date.fromisoformat
            mock_dt.timedelta = datetime.timedelta
            monday = weekly_stats._current_week_monday()
        assert monday == datetime.date(2026, 6, 8)
        assert monday.weekday() == 0


# ── row filtering ─────────────────────────────────────────────────────────────


class TestRowFiltering(unittest.TestCase):
    def test_rows_outside_week_excluded(self) -> None:
        sheet = _make_sheet([_row(date="2026-01-04")])
        result = weekly_stats.compute_stats(sheet, *_week_bounds())
        assert result["stats"]["total_scanned"] == 0

    def test_rows_on_monday_boundary_included(self) -> None:
        sheet = _make_sheet([_row(date=WEEK_MON)])
        result = weekly_stats.compute_stats(sheet, *_week_bounds())
        assert result["stats"]["total_scanned"] == 1

    def test_rows_on_sunday_boundary_included(self) -> None:
        sheet = _make_sheet([_row(date=WEEK_SUN)])
        result = weekly_stats.compute_stats(sheet, *_week_bounds())
        assert result["stats"]["total_scanned"] == 1

    def test_malformed_date_row_skipped_without_crash(self) -> None:
        sheet = _make_sheet([_row(date="n/a"), _row(date=WEEK_MON)])
        result = weekly_stats.compute_stats(sheet, *_week_bounds())
        assert result["stats"]["total_scanned"] == 1


# ── stat counts ───────────────────────────────────────────────────────────────


class TestStatCounts(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            _row(match_rate="80.0", status="Notifié"),
            _row(match_rate="65.0", status="Généré"),
            _row(match_rate="60.0", status="Envoyé"),
            _row(match_rate="55.0", status="Ignoré"),
            _row(match_rate="40.0", status="Notifié"),
        ]
        self.sheet = _make_sheet(self.rows)

    def test_total_scanned(self) -> None:
        result = weekly_stats.compute_stats(self.sheet, *_week_bounds())
        assert result["stats"]["total_scanned"] == 5

    def test_total_qualified(self) -> None:
        result = weekly_stats.compute_stats(self.sheet, *_week_bounds())
        assert result["stats"]["total_qualified"] == 3

    def test_match_rate_exactly_60_counts_as_qualified(self) -> None:
        sheet = _make_sheet([_row(match_rate="60.0")])
        result = weekly_stats.compute_stats(sheet, *_week_bounds())
        assert result["stats"]["total_qualified"] == 1

    def test_match_rate_59_not_qualified(self) -> None:
        sheet = _make_sheet([_row(match_rate="59.9")])
        result = weekly_stats.compute_stats(sheet, *_week_bounds())
        assert result["stats"]["total_qualified"] == 0

    def test_notifications_sent_excludes_ignores(self) -> None:
        result = weekly_stats.compute_stats(self.sheet, *_week_bounds())
        assert result["stats"]["notifications_sent"] == 4

    def test_notifications_sent_excludes_empty_status(self) -> None:
        sheet = _make_sheet([_row(status=""), _row(status="Notifié")])
        result = weekly_stats.compute_stats(sheet, *_week_bounds())
        assert result["stats"]["notifications_sent"] == 1

    def test_cvs_generated_statuses(self) -> None:
        rows = [
            _row(status="Généré"),
            _row(status="Envoyé"),
            _row(status="Réponse+"),
            _row(status="Réponse-"),
            _row(status="Entretien"),
            _row(status="Notifié"),
            _row(status="Ignoré"),
        ]
        result = weekly_stats.compute_stats(_make_sheet(rows), *_week_bounds())
        assert result["stats"]["cvs_generated"] == 5

    def test_letters_generated_equals_cvs_generated(self) -> None:
        result = weekly_stats.compute_stats(self.sheet, *_week_bounds())
        assert result["stats"]["letters_generated"] == result["stats"]["cvs_generated"]

    def test_applications_sent_statuses(self) -> None:
        rows = [
            _row(status="Envoyé"),
            _row(status="Réponse+"),
            _row(status="Réponse-"),
            _row(status="Entretien"),
            _row(status="Généré"),
        ]
        result = weekly_stats.compute_stats(_make_sheet(rows), *_week_bounds())
        assert result["stats"]["applications_sent"] == 4

    def test_avg_match_rate_correct(self) -> None:
        rows = [_row(match_rate="80.0"), _row(match_rate="70.0"), _row(match_rate="60.0")]
        result = weekly_stats.compute_stats(_make_sheet(rows), *_week_bounds())
        assert abs(result["stats"]["avg_match_rate"] - 70.0) < 0.01

    def test_avg_match_rate_single_row(self) -> None:
        result = weekly_stats.compute_stats(_make_sheet([_row(match_rate="75.0")]), *_week_bounds())
        assert result["stats"]["avg_match_rate"] == 75.0

    def test_avg_match_rate_empty_week(self) -> None:
        result = weekly_stats.compute_stats(_make_sheet([]), *_week_bounds())
        assert result["stats"]["avg_match_rate"] == 0.0


# ── top matches ───────────────────────────────────────────────────────────────


class TestTopMatches(unittest.TestCase):
    def test_top_matches_returns_at_most_3(self) -> None:
        rows = [
            _row(match_rate="90.0", status="Notifié"),
            _row(match_rate="80.0", status="Notifié"),
            _row(match_rate="70.0", status="Notifié"),
            _row(match_rate="60.0", status="Notifié"),
            _row(match_rate="50.0", status="Notifié"),
        ]
        result = weekly_stats.compute_stats(_make_sheet(rows), *_week_bounds())
        assert len(result["stats"]["top_matches"]) == 3

    def test_top_matches_sorted_by_match_rate_descending(self) -> None:
        rows = [
            _row(match_rate="50.0", status="Notifié"),
            _row(match_rate="90.0", status="Notifié"),
            _row(match_rate="70.0", status="Notifié"),
            _row(match_rate="80.0", status="Notifié"),
            _row(match_rate="60.0", status="Notifié"),
        ]
        result = weekly_stats.compute_stats(_make_sheet(rows), *_week_bounds())
        rates = [m["match_rate"] for m in result["stats"]["top_matches"]]
        assert rates == [90.0, 80.0, 70.0]

    def test_top_matches_excludes_ignores(self) -> None:
        rows = [
            _row(match_rate="99.0", status="Ignoré"),
            _row(match_rate="75.0", status="Notifié"),
        ]
        result = weekly_stats.compute_stats(_make_sheet(rows), *_week_bounds())
        assert all(m["match_rate"] != 99.0 for m in result["stats"]["top_matches"])

    def test_top_matches_excludes_applied(self) -> None:
        rows = [
            _row(match_rate="99.0", status="Envoyé"),
            _row(match_rate="75.0", status="Notifié"),
        ]
        result = weekly_stats.compute_stats(_make_sheet(rows), *_week_bounds())
        assert all(m["match_rate"] != 99.0 for m in result["stats"]["top_matches"])

    def test_top_matches_fields(self) -> None:
        result = weekly_stats.compute_stats(_make_sheet([_row()]), *_week_bounds())
        for match in result["stats"]["top_matches"]:
            assert set(match.keys()) == {"title", "company", "match_rate", "status"}

    def test_top_matches_fewer_than_3_eligible(self) -> None:
        rows = [_row(match_rate="80.0"), _row(match_rate="70.0")]
        result = weekly_stats.compute_stats(_make_sheet(rows), *_week_bounds())
        assert len(result["stats"]["top_matches"]) == 2

    def test_top_matches_no_eligible_rows(self) -> None:
        rows = [_row(status="Ignoré"), _row(status="Envoyé")]
        result = weekly_stats.compute_stats(_make_sheet(rows), *_week_bounds())
        assert result["stats"]["top_matches"] == []


# ── source breakdown ──────────────────────────────────────────────────────────


class TestSourceBreakdown(unittest.TestCase):
    def test_source_breakdown_both_sources(self) -> None:
        rows = [
            _row(source="indeed"),
            _row(source="indeed"),
            _row(source="indeed"),
            _row(source="linkedin"),
            _row(source="linkedin"),
        ]
        result = weekly_stats.compute_stats(_make_sheet(rows), *_week_bounds())
        sb = result["stats"]["source_breakdown"]
        assert abs(sb["indeed"] - 60.0) < 0.01
        assert abs(sb["linkedin"] - 40.0) < 0.01

    def test_source_breakdown_all_indeed(self) -> None:
        rows = [_row(source="indeed")] * 4
        result = weekly_stats.compute_stats(_make_sheet(rows), *_week_bounds())
        sb = result["stats"]["source_breakdown"]
        assert sb["indeed"] == 100.0
        assert sb["linkedin"] == 0.0

    def test_source_breakdown_empty_week(self) -> None:
        result = weekly_stats.compute_stats(_make_sheet([]), *_week_bounds())
        sb = result["stats"]["source_breakdown"]
        assert sb["indeed"] == 0.0
        assert sb["linkedin"] == 0.0


# ── output format ─────────────────────────────────────────────────────────────


class TestOutputFormat(unittest.TestCase):
    def _result(self) -> dict:
        return weekly_stats.compute_stats(_make_sheet([_row()]), *_week_bounds())

    def test_output_has_required_top_level_keys(self) -> None:
        result = self._result()
        assert {"week_start", "week_end", "stats"} <= result.keys()

    def test_week_start_end_are_iso_strings(self) -> None:
        result = self._result()
        assert result["week_start"] == WEEK_MON
        assert result["week_end"] == WEEK_SUN

    def test_stats_has_all_required_keys(self) -> None:
        required = {
            "total_scanned", "total_qualified", "notifications_sent",
            "cvs_generated", "letters_generated", "applications_sent",
            "responses_received", "avg_match_rate", "source_breakdown", "top_matches",
        }
        assert required <= self._result()["stats"].keys()

    def test_output_is_json_serializable(self) -> None:
        result = self._result()
        json.dumps(result)


# ── CLI ───────────────────────────────────────────────────────────────────────


class TestCLI(unittest.TestCase):
    def test_cli_week_arg_respected(self) -> None:
        sheet = _make_sheet([_row()])
        with patch("matches_store.open_matches", return_value=sheet):
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                weekly_stats.main(["--week", WEEK_MON])
        out = json.loads(captured.getvalue())
        assert out["week_start"] == WEEK_MON

    def test_cli_invalid_week_exits_nonzero(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            weekly_stats.main(["--week", "not-a-date"])
        assert ctx.exception.code != 0

    def test_cli_non_monday_exits_nonzero(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            weekly_stats.main(["--week", "2026-01-06"])
        assert ctx.exception.code != 0

    def test_cli_output_is_valid_json(self) -> None:
        sheet = _make_sheet([_row()])
        with patch("matches_store.open_matches", return_value=sheet):
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                weekly_stats.main(["--week", WEEK_MON])
        json.loads(captured.getvalue())

    def test_open_matches_called_once(self) -> None:
        sheet = _make_sheet([])
        with patch("matches_store.open_matches", return_value=sheet) as mock_open:
            weekly_stats.main(["--week", WEEK_MON])
        mock_open.assert_called_once()


if __name__ == "__main__":
    unittest.main()

"""
Unit tests for matches_store.py — no live API calls, gspread fully mocked.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import matches_store

VALID_JOB_ID = "a" * 64
OTHER_JOB_ID = "b" * 64
NONEXISTENT_JOB_ID = "c" * 64

_HEADER = "job_id"


def _make_sheet(*job_ids: str, status: str = "Notifié", note: str = "") -> MagicMock:
    """Return a mock Worksheet with col_values(1) returning header + job_ids."""
    sheet = MagicMock()
    sheet.col_values.return_value = [_HEADER, *job_ids]

    def _cell(row: int, col: int) -> MagicMock:
        c = MagicMock()
        if col == matches_store.COL_STATUS:
            c.value = status
        elif col == matches_store.COL_NOTES:
            c.value = note
        else:
            c.value = ""
        return c

    sheet.cell.side_effect = _cell
    return sheet


# ── find_row ──────────────────────────────────────────────────────────────────


class TestFindRow(unittest.TestCase):
    def test_finds_first_data_row(self) -> None:
        sheet = _make_sheet(VALID_JOB_ID)
        assert matches_store.find_row(sheet, VALID_JOB_ID) == 2

    def test_finds_second_data_row(self) -> None:
        sheet = _make_sheet(OTHER_JOB_ID, VALID_JOB_ID)
        assert matches_store.find_row(sheet, VALID_JOB_ID) == 3

    def test_not_found_returns_none(self) -> None:
        sheet = _make_sheet(VALID_JOB_ID)
        assert matches_store.find_row(sheet, NONEXISTENT_JOB_ID) is None

    def test_header_not_matched(self) -> None:
        # Ensure "job_id" header itself is never returned as a match
        sheet = _make_sheet()
        assert matches_store.find_row(sheet, _HEADER) is None

    def test_empty_sheet_returns_none(self) -> None:
        sheet = MagicMock()
        sheet.col_values.return_value = [_HEADER]
        assert matches_store.find_row(sheet, VALID_JOB_ID) is None


# ── get_status ────────────────────────────────────────────────────────────────


class TestGetStatus(unittest.TestCase):
    def test_returns_status(self) -> None:
        sheet = _make_sheet(VALID_JOB_ID, status="Envoyé")
        assert matches_store.get_status(sheet, VALID_JOB_ID) == "Envoyé"

    def test_reads_col_11(self) -> None:
        sheet = _make_sheet(VALID_JOB_ID, status="Notifié")
        matches_store.get_status(sheet, VALID_JOB_ID)
        sheet.cell.assert_called_once_with(2, matches_store.COL_STATUS)

    def test_not_found_raises_key_error(self) -> None:
        sheet = _make_sheet(VALID_JOB_ID)
        with self.assertRaises(KeyError):
            matches_store.get_status(sheet, NONEXISTENT_JOB_ID)

    def test_empty_cell_returns_empty_string(self) -> None:
        sheet = _make_sheet(VALID_JOB_ID, status="")
        assert matches_store.get_status(sheet, VALID_JOB_ID) == ""


# ── set_status ────────────────────────────────────────────────────────────────


class TestSetStatus(unittest.TestCase):
    def test_calls_update_cell_with_col_11(self) -> None:
        sheet = _make_sheet(VALID_JOB_ID)
        matches_store.set_status(sheet, VALID_JOB_ID, "Envoyé")
        sheet.update_cell.assert_called_once_with(2, matches_store.COL_STATUS, "Envoyé")

    def test_all_valid_statuses_accepted(self) -> None:
        for status in matches_store.VALID_STATUSES:
            with self.subTest(status=status):
                sheet = _make_sheet(VALID_JOB_ID)
                matches_store.set_status(sheet, VALID_JOB_ID, status)

    def test_invalid_status_raises_value_error(self) -> None:
        sheet = _make_sheet(VALID_JOB_ID)
        with self.assertRaises(ValueError):
            matches_store.set_status(sheet, VALID_JOB_ID, "Approuvé")

    def test_invalid_status_never_calls_update_cell(self) -> None:
        sheet = _make_sheet(VALID_JOB_ID)
        with self.assertRaises(ValueError):
            matches_store.set_status(sheet, VALID_JOB_ID, "Approuvé")
        sheet.update_cell.assert_not_called()

    def test_empty_string_status_raises_value_error(self) -> None:
        sheet = _make_sheet(VALID_JOB_ID)
        with self.assertRaises(ValueError):
            matches_store.set_status(sheet, VALID_JOB_ID, "")

    def test_not_found_raises_key_error(self) -> None:
        sheet = _make_sheet(VALID_JOB_ID)
        with self.assertRaises(KeyError):
            matches_store.set_status(sheet, NONEXISTENT_JOB_ID, "Envoyé")


# ── set_note ──────────────────────────────────────────────────────────────────


class TestSetNote(unittest.TestCase):
    def test_calls_update_cell_with_col_15(self) -> None:
        sheet = _make_sheet(VALID_JOB_ID)
        matches_store.set_note(sheet, VALID_JOB_ID, "Bonne opportunité")
        sheet.update_cell.assert_called_once_with(2, matches_store.COL_NOTES, "Bonne opportunité")

    def test_not_found_raises_key_error(self) -> None:
        sheet = _make_sheet(VALID_JOB_ID)
        with self.assertRaises(KeyError):
            matches_store.set_note(sheet, NONEXISTENT_JOB_ID, "texte")

    def test_empty_note_accepted(self) -> None:
        sheet = _make_sheet(VALID_JOB_ID)
        matches_store.set_note(sheet, VALID_JOB_ID, "")
        sheet.update_cell.assert_called_once_with(2, matches_store.COL_NOTES, "")

    def test_note_with_special_chars(self) -> None:
        note = "Candidature envoyée — Réf: #42 (suivi réponse+)"
        sheet = _make_sheet(VALID_JOB_ID)
        matches_store.set_note(sheet, VALID_JOB_ID, note)
        sheet.update_cell.assert_called_once_with(2, matches_store.COL_NOTES, note)


# ── open_matches ──────────────────────────────────────────────────────────────


class TestOpenMatches(unittest.TestCase):
    def test_calls_service_account_with_creds_path(self) -> None:
        with patch("gspread.service_account") as mock_sa:
            mock_sa.return_value = MagicMock()
            mock_sa.return_value.open.return_value.worksheet.return_value = MagicMock()
            matches_store.open_matches("/fake/creds.json")
            mock_sa.assert_called_once_with(filename="/fake/creds.json")

    def test_opens_correct_spreadsheet(self) -> None:
        with patch("gspread.service_account") as mock_sa:
            mock_gc = MagicMock()
            mock_sa.return_value = mock_gc
            mock_gc.open.return_value.worksheet.return_value = MagicMock()
            matches_store.open_matches("/fake/creds.json")
            mock_gc.open.assert_called_once_with("job-hunter-tracker")

    def test_opens_matches_tab(self) -> None:
        with patch("gspread.service_account") as mock_sa:
            mock_gc = MagicMock()
            mock_sa.return_value = mock_gc
            mock_spreadsheet = MagicMock()
            mock_gc.open.return_value = mock_spreadsheet
            matches_store.open_matches("/fake/creds.json")
            mock_spreadsheet.worksheet.assert_called_once_with("MATCHES")


if __name__ == "__main__":
    unittest.main()

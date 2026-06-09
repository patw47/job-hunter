"""
Unit tests for deduplication.py — no live API calls, no gspread.
All sheet interactions are mocked.
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import deduplication as dedup


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_sheet(existing_hashes: list[str] | None = None) -> MagicMock:
    """Return a mock gspread Worksheet with col_values and append_rows stubbed."""
    sheet = MagicMock()
    sheet.col_values.return_value = existing_hashes or []
    return sheet


def _sha256(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


# ── normalize_url ─────────────────────────────────────────────────────────────


class TestNormalizeUrl(unittest.TestCase):
    def test_strips_query_string(self) -> None:
        result = dedup.normalize_url("https://fr.indeed.com/viewjob?jk=abc123&from=serp")
        assert result == "https://fr.indeed.com/viewjob"

    def test_strips_fragment(self) -> None:
        result = dedup.normalize_url("https://example.com/job#apply")
        assert result == "https://example.com/job"

    def test_strips_both(self) -> None:
        result = dedup.normalize_url("https://example.com/job?q=python#top")
        assert result == "https://example.com/job"

    def test_clean_url_unchanged(self) -> None:
        url = "https://example.com/job/123"
        assert dedup.normalize_url(url) == url

    def test_empty_string_no_raise(self) -> None:
        assert dedup.normalize_url("") == ""


# ── compute_hash ──────────────────────────────────────────────────────────────


class TestComputeHash(unittest.TestCase):
    def test_known_hash(self) -> None:
        url = "https://example.com/job/123"
        expected = _sha256(url)
        assert dedup.compute_hash(url) == expected

    def test_stability(self) -> None:
        url = "https://example.com/job/123"
        assert dedup.compute_hash(url) == dedup.compute_hash(url)

    def test_hashes_normalized_form(self) -> None:
        # Same URL with and without query → same hash (normalized before hashing)
        assert dedup.compute_hash("https://x.com/j?q=1") == dedup.compute_hash("https://x.com/j")

    def test_empty_string_returns_valid_hex(self) -> None:
        result = dedup.compute_hash("")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


# ── is_duplicate ──────────────────────────────────────────────────────────────


class TestIsDuplicate(unittest.TestCase):
    def test_returns_true_when_hash_present(self) -> None:
        sheet = _make_sheet(["sha256", "aabbccdd", "eeffgghh"])
        assert dedup.is_duplicate("aabbccdd", sheet) is True

    def test_returns_false_when_hash_absent(self) -> None:
        sheet = _make_sheet(["sha256", "aabbccdd"])
        assert dedup.is_duplicate("deadbeef", sheet) is False

    def test_empty_sheet_returns_false(self) -> None:
        sheet = _make_sheet([])
        assert dedup.is_duplicate("anything", sheet) is False

    def test_header_row_only_returns_false(self) -> None:
        # Fresh sheet with only the header — no data rows
        sheet = _make_sheet(["sha256"])
        assert dedup.is_duplicate("sha256", sheet) is True  # "sha256" IS in the list
        # but a real 64-char hash would not match
        real_hash = _sha256("https://example.com/job/1")
        sheet2 = _make_sheet(["sha256"])
        assert dedup.is_duplicate(real_hash, sheet2) is False

    def test_rescan_same_offers_returns_true(self) -> None:
        # Simulate: offer was written after first scan, second scan should detect duplicate
        url = "https://example.com/job/1"
        stored_hash = dedup.compute_hash(url)
        sheet = _make_sheet(["sha256", stored_hash])
        # Second scan: same URL → same hash → duplicate
        assert dedup.is_duplicate(dedup.compute_hash(url), sheet) is True


# ── log_hashes ────────────────────────────────────────────────────────────────


class TestLogHashes(unittest.TestCase):
    def _sample_hash(self, n: int = 1) -> list[dict]:
        return [
            {
                "url_hash": f"hash{i:064d}",
                "scan_date": "09.06.2026",
                "url": f"https://example.com/job/{i}",
                "title": f"Job {i}",
                "company": f"Corp {i}",
                "source": "indeed",
            }
            for i in range(n)
        ]

    def test_batch_write_called_once(self) -> None:
        sheet = _make_sheet()
        dedup.log_hashes(self._sample_hash(3), sheet)
        assert sheet.append_rows.call_count == 1

    def test_all_hashes_written(self) -> None:
        sheet = _make_sheet()
        hashes = self._sample_hash(3)
        dedup.log_hashes(hashes, sheet)
        rows = sheet.append_rows.call_args[0][0]
        assert len(rows) == 3

    def test_empty_list_no_write(self) -> None:
        sheet = _make_sheet()
        dedup.log_hashes([], sheet)
        sheet.append_rows.assert_not_called()

    def test_scan_date_preserved(self) -> None:
        sheet = _make_sheet()
        h = self._sample_hash(1)
        h[0]["scan_date"] = "09.06.2026"
        dedup.log_hashes(h, sheet)
        row = sheet.append_rows.call_args[0][0][0]
        assert row[1] == "09.06.2026"

    def test_row_column_order(self) -> None:
        # Column order must match SCANNED_HASHES header:
        # sha256 | date_scanned | url | title | company | source
        sheet = _make_sheet()
        h = {
            "url_hash": "abc123",
            "scan_date": "09.06.2026",
            "url": "https://example.com/job/1",
            "title": "AI Engineer",
            "company": "Acme",
            "source": "indeed",
        }
        dedup.log_hashes([h], sheet)
        row = sheet.append_rows.call_args[0][0][0]
        assert row == ["abc123", "09.06.2026", "https://example.com/job/1", "AI Engineer", "Acme", "indeed"]

    def test_new_offers_written_to_scanned_hashes(self) -> None:
        # AC: nouvelles offres → hash écrit dans SCANNED_HASHES
        sheet = _make_sheet()
        hashes = self._sample_hash(2)
        dedup.log_hashes(hashes, sheet)
        rows = sheet.append_rows.call_args[0][0]
        written_hashes = [r[0] for r in rows]
        assert f"hash{0:064d}" in written_hashes
        assert f"hash{1:064d}" in written_hashes


if __name__ == "__main__":
    unittest.main()

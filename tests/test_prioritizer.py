"""Unit tests for prioritizer.py — pure logic, all I/O mocked."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from prioritizer import (
    DAILY_CAP,
    clear_promoted_rows,
    compute_slots,
    merge_offers,
    read_pending_offers,
    run_prioritizer,
    split_by_cap,
    update_matches_tab,
    write_pending_tab,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _pending(**overrides) -> dict:
    base: dict = {
        "job_id": "j1", "title": "AI Eng", "company": "Acme",
        "location": "Remote", "url": "https://example.com/1",
        "match_rate": 80.0, "skills_found": "python", "source": "indeed",
        "date_scanned": "2026-06-10", "rank": 1,
    }
    base.update(overrides)
    return base


def _new_match(**overrides) -> dict:
    base: dict = {
        "job_id": "j99", "title": "ML Eng", "company": "Corp",
        "location": "Remote", "url": "https://example.com/99",
        "match_rate": 70.0, "skills_found": "llm", "source": "linkedin",
    }
    base.update(overrides)
    return base


def _make_pending_sheet(records: list[dict] | None = None) -> MagicMock:
    sheet = MagicMock()
    sheet.get_all_records.return_value = records or []
    return sheet


def _make_matches_sheet() -> MagicMock:
    return MagicMock()


# ── TestReadPendingOffers ─────────────────────────────────────────────────────


class TestReadPendingOffers(unittest.TestCase):
    def test_returns_list(self) -> None:
        sheet = _make_pending_sheet()
        assert isinstance(read_pending_offers(sheet), list)

    def test_empty_sheet_returns_empty_list(self) -> None:
        sheet = _make_pending_sheet([])
        assert read_pending_offers(sheet) == []

    def test_returns_all_rows(self) -> None:
        rows = [_pending(job_id=f"j{i}") for i in range(3)]
        sheet = _make_pending_sheet(rows)
        assert len(read_pending_offers(sheet)) == 3

    def test_each_row_is_dict(self) -> None:
        rows = [_pending(job_id="j1"), _pending(job_id="j2")]
        sheet = _make_pending_sheet(rows)
        for row in read_pending_offers(sheet):
            assert isinstance(row, dict)

    def test_calls_get_all_records_once(self) -> None:
        sheet = _make_pending_sheet()
        read_pending_offers(sheet)
        sheet.get_all_records.assert_called_once()


# ── TestComputeSlots ──────────────────────────────────────────────────────────


class TestComputeSlots(unittest.TestCase):
    def test_zero_pending_gives_cap(self) -> None:
        assert compute_slots([]) == DAILY_CAP

    def test_10_pending_gives_15(self) -> None:
        assert compute_slots([_pending() for _ in range(10)]) == 15

    def test_25_pending_gives_zero(self) -> None:
        assert compute_slots([_pending() for _ in range(25)]) == 0

    def test_overflow_pending_clamps_to_zero(self) -> None:
        assert compute_slots([_pending() for _ in range(30)]) == 0

    def test_custom_cap(self) -> None:
        assert compute_slots([_pending() for _ in range(3)], cap=10) == 7


# ── TestMergeOffers ───────────────────────────────────────────────────────────


class TestMergeOffers(unittest.TestCase):
    def test_pending_appears_before_new(self) -> None:
        p = [_pending(job_id="p1", match_rate=50.0)]
        n = [_new_match(job_id="n1", match_rate=99.0)]
        merged = merge_offers(p, n)
        assert merged[0]["job_id"] == "p1"
        assert merged[1]["job_id"] == "n1"

    def test_new_matches_sorted_by_match_rate_desc(self) -> None:
        n = [
            _new_match(job_id="n1", match_rate=60.0),
            _new_match(job_id="n2", match_rate=90.0),
            _new_match(job_id="n3", match_rate=75.0),
        ]
        merged = merge_offers([], n)
        rates = [float(o["match_rate"]) for o in merged]
        assert rates == sorted(rates, reverse=True)

    def test_pending_order_preserved_among_themselves(self) -> None:
        p = [_pending(job_id=f"p{i}", match_rate=float(i)) for i in range(5)]
        merged = merge_offers(p, [])
        assert [o["job_id"] for o in merged] == [f"p{i}" for i in range(5)]

    def test_empty_pending_returns_sorted_new(self) -> None:
        n = [_new_match(job_id="n1", match_rate=60.0), _new_match(job_id="n2", match_rate=90.0)]
        merged = merge_offers([], n)
        assert merged[0]["job_id"] == "n2"

    def test_empty_new_returns_pending(self) -> None:
        p = [_pending(job_id="p1")]
        assert merge_offers(p, []) == p

    def test_both_empty_returns_empty(self) -> None:
        assert merge_offers([], []) == []

    def test_return_length_equals_sum(self) -> None:
        p = [_pending(job_id=f"p{i}") for i in range(3)]
        n = [_new_match(job_id=f"n{i}") for i in range(4)]
        assert len(merge_offers(p, n)) == 7

    def test_match_rate_string_coerced_for_sort(self) -> None:
        n = [
            _new_match(job_id="n1", match_rate="60.0"),
            _new_match(job_id="n2", match_rate="85.0"),
        ]
        merged = merge_offers([], n)
        assert merged[0]["job_id"] == "n2"


# ── TestSplitByCap ────────────────────────────────────────────────────────────


class TestSplitByCap(unittest.TestCase):
    def test_ac1_10_pending_20_new_25_sent_5_parked(self) -> None:
        merged = [_pending(job_id=f"p{i}") for i in range(10)] + \
                 [_new_match(job_id=f"n{i}") for i in range(20)]
        to_notify, to_park = split_by_cap(merged, 25)
        assert len(to_notify) == 25
        assert len(to_park) == 5

    def test_to_notify_is_first_n(self) -> None:
        merged = [_pending(job_id=f"j{i}") for i in range(10)]
        to_notify, _ = split_by_cap(merged, 7)
        assert [o["job_id"] for o in to_notify] == [f"j{i}" for i in range(7)]

    def test_to_park_is_remainder(self) -> None:
        merged = [_pending(job_id=f"j{i}") for i in range(10)]
        _, to_park = split_by_cap(merged, 7)
        assert [o["job_id"] for o in to_park] == [f"j{i}" for i in range(7, 10)]

    def test_zero_slots_all_parked(self) -> None:
        merged = [_pending(job_id="j1"), _pending(job_id="j2")]
        to_notify, to_park = split_by_cap(merged, 0)
        assert to_notify == []
        assert len(to_park) == 2

    def test_slots_larger_than_list_no_park(self) -> None:
        merged = [_pending(job_id="j1")]
        to_notify, to_park = split_by_cap(merged, 100)
        assert len(to_notify) == 1
        assert to_park == []

    def test_returns_tuple_of_two_lists(self) -> None:
        result = split_by_cap([], 0)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], list)
        assert isinstance(result[1], list)


# ── TestUpdateMatchesTab ──────────────────────────────────────────────────────


class TestUpdateMatchesTab(unittest.TestCase):
    def test_ac4_status_set_to_notifie_via_update_cell(self) -> None:
        sheet = _make_matches_sheet()
        cell = MagicMock()
        cell.row = 3
        sheet.find.return_value = cell
        update_matches_tab([_pending(job_id="j1")], sheet)
        sheet.update_cell.assert_any_call(3, 11, "Notifié")

    def test_telegram_sent_false_via_update_cell(self) -> None:
        sheet = _make_matches_sheet()
        cell = MagicMock()
        cell.row = 3
        sheet.find.return_value = cell
        update_matches_tab([_pending(job_id="j1")], sheet)
        sheet.update_cell.assert_any_call(3, 12, "FALSE")

    def test_empty_list_no_write(self) -> None:
        sheet = _make_matches_sheet()
        update_matches_tab([], sheet)
        sheet.find.assert_not_called()
        sheet.append_rows.assert_not_called()

    def test_each_offer_processed(self) -> None:
        sheet = _make_matches_sheet()
        cell = MagicMock()
        cell.row = 2
        sheet.find.return_value = cell
        offers = [_pending(job_id=f"j{i}") for i in range(3)]
        update_matches_tab(offers, sheet)
        assert sheet.find.call_count == 3

    def test_not_found_triggers_append(self) -> None:
        sheet = _make_matches_sheet()
        sheet.find.side_effect = Exception("CellNotFound")
        update_matches_tab([_new_match(job_id="n1")], sheet)
        sheet.append_rows.assert_called_once()

    def test_appended_row_has_notifie_status(self) -> None:
        sheet = _make_matches_sheet()
        sheet.find.side_effect = Exception("CellNotFound")
        update_matches_tab([_new_match(job_id="n1")], sheet)
        rows = sheet.append_rows.call_args[0][0]
        assert len(rows) == 1
        row = rows[0]
        assert row[10] == "Notifié"

    def test_appended_row_has_false_telegram_sent(self) -> None:
        sheet = _make_matches_sheet()
        sheet.find.side_effect = Exception("CellNotFound")
        update_matches_tab([_new_match(job_id="n1")], sheet)
        row = sheet.append_rows.call_args[0][0][0]
        assert row[11] == "FALSE"


# ── TestWritePendingTab ───────────────────────────────────────────────────────


class TestWritePendingTab(unittest.TestCase):
    def test_rows_appended_to_pending_tab(self) -> None:
        sheet = _make_pending_sheet([])
        write_pending_tab([_new_match(job_id="n1")], sheet)
        sheet.append_rows.assert_called_once()

    def test_empty_to_park_no_write(self) -> None:
        sheet = _make_pending_sheet([])
        write_pending_tab([], sheet)
        sheet.append_rows.assert_not_called()

    def test_rank_assigned_sequentially(self) -> None:
        sheet = _make_pending_sheet([])
        offers = [_new_match(job_id=f"n{i}") for i in range(3)]
        write_pending_tab(offers, sheet)
        rows = sheet.append_rows.call_args[0][0]
        ranks = [row[9] for row in rows]
        assert ranks == ["1", "2", "3"]

    def test_batch_write_single_call(self) -> None:
        sheet = _make_pending_sheet([])
        offers = [_new_match(job_id=f"n{i}") for i in range(5)]
        write_pending_tab(offers, sheet)
        assert sheet.append_rows.call_count == 1

    def test_already_present_skipped(self) -> None:
        existing = [{"job_id": "n1"}]
        sheet = _make_pending_sheet(existing)
        write_pending_tab([_new_match(job_id="n1")], sheet)
        sheet.append_rows.assert_not_called()

    def test_only_novel_offers_written(self) -> None:
        existing = [{"job_id": "n1"}]
        sheet = _make_pending_sheet(existing)
        offers = [_new_match(job_id="n1"), _new_match(job_id="n2")]
        write_pending_tab(offers, sheet)
        rows = sheet.append_rows.call_args[0][0]
        written_ids = [row[0] for row in rows]
        assert written_ids == ["n2"]


# ── TestClearPromotedRows ─────────────────────────────────────────────────────


class TestClearPromotedRows(unittest.TestCase):
    def test_ac3_promoted_rows_deleted(self) -> None:
        sheet = _make_pending_sheet()
        cell = MagicMock()
        cell.row = 2
        sheet.find.return_value = cell
        clear_promoted_rows([_pending(job_id="j1")], sheet)
        sheet.delete_rows.assert_called_once_with(2)

    def test_empty_promoted_no_delete(self) -> None:
        sheet = _make_pending_sheet()
        clear_promoted_rows([], sheet)
        sheet.delete_rows.assert_not_called()

    def test_only_found_rows_deleted(self) -> None:
        sheet = _make_pending_sheet()
        found_cell = MagicMock()
        found_cell.row = 3
        sheet.find.side_effect = [found_cell, Exception("not found")]
        clear_promoted_rows(
            [_pending(job_id="j1"), _new_match(job_id="n99")], sheet
        )
        assert sheet.delete_rows.call_count == 1

    def test_match_by_job_id(self) -> None:
        sheet = _make_pending_sheet()
        cell = MagicMock()
        cell.row = 5
        sheet.find.return_value = cell
        clear_promoted_rows([_pending(job_id="target-id")], sheet)
        sheet.find.assert_called_once_with("target-id")

    def test_delete_in_reverse_order(self) -> None:
        sheet = _make_pending_sheet()
        cell_a = MagicMock()
        cell_a.row = 2
        cell_b = MagicMock()
        cell_b.row = 5
        sheet.find.side_effect = [cell_a, cell_b]
        clear_promoted_rows(
            [_pending(job_id="j1"), _pending(job_id="j2")], sheet
        )
        calls = [c.args[0] for c in sheet.delete_rows.call_args_list]
        assert calls == [5, 2]


# ── TestRunPrioritizer ────────────────────────────────────────────────────────


class TestRunPrioritizer(unittest.TestCase):
    def _make_sheets(self, pending_records: list[dict] | None = None):
        pending_sheet = _make_pending_sheet(pending_records)
        # find raises → clear_promoted_rows skips deletions (not testing deletion here)
        pending_sheet.find.side_effect = Exception("not found")
        matches_sheet = _make_matches_sheet()
        matches_sheet.find.side_effect = Exception("CellNotFound")
        return pending_sheet, matches_sheet

    def test_ac1_10_pending_20_new_25_notified_5_parked(self) -> None:
        pending_records = [_pending(job_id=f"p{i}") for i in range(10)]
        pending_sheet, matches_sheet = self._make_sheets(pending_records)
        new_matches = [_new_match(job_id=f"n{i}", match_rate=float(50 + i)) for i in range(20)]

        result = run_prioritizer(pending_sheet, matches_sheet, new_matches)

        assert result["notified"] == 25
        assert result["parked"] == 5

    def test_ac2_pending_in_head_of_notified(self) -> None:
        pending_records = [_pending(job_id=f"p{i}", match_rate=1.0) for i in range(5)]
        pending_sheet = _make_pending_sheet(pending_records)
        pending_sheet.find.side_effect = Exception("not found")
        matches_sheet = _make_matches_sheet()
        matches_sheet.find.side_effect = Exception("CellNotFound")

        notified_ids: list[str] = []
        def capture_append(rows, **_kw):
            notified_ids.append(rows[0][0])
        matches_sheet.append_rows.side_effect = capture_append

        new_matches = [_new_match(job_id=f"n{i}", match_rate=99.0) for i in range(5)]
        run_prioritizer(pending_sheet, matches_sheet, new_matches)
        # First 5 appended must be the pending offers
        assert notified_ids[:5] == [f"p{i}" for i in range(5)]

    def test_ac3_clear_called_on_notified(self) -> None:
        pending_records = [_pending(job_id=f"p{i}") for i in range(3)]
        pending_sheet = _make_pending_sheet(pending_records)
        matches_sheet = _make_matches_sheet()
        matches_sheet.find.side_effect = Exception("CellNotFound")
        cell = MagicMock()
        cell.row = 2
        pending_sheet.find.return_value = cell

        run_prioritizer(pending_sheet, matches_sheet, [])
        # Each pending offer should have been searched for deletion
        assert pending_sheet.find.call_count >= 3

    def test_returns_dict_with_counts(self) -> None:
        pending_sheet, matches_sheet = self._make_sheets([])
        result = run_prioritizer(pending_sheet, matches_sheet, [])
        assert isinstance(result, dict)
        assert "notified" in result
        assert "parked" in result

    def test_zero_new_matches_all_pending_sent(self) -> None:
        pending_records = [_pending(job_id=f"p{i}") for i in range(5)]
        pending_sheet, matches_sheet = self._make_sheets(pending_records)
        result = run_prioritizer(pending_sheet, matches_sheet, [])
        assert result["notified"] == 5
        assert result["parked"] == 0

    def test_zero_pending_zero_new_no_notified(self) -> None:
        pending_sheet, matches_sheet = self._make_sheets([])
        result = run_prioritizer(pending_sheet, matches_sheet, [])
        assert result["notified"] == 0
        assert result["parked"] == 0

    def test_full_pending_cap_new_go_to_park(self) -> None:
        pending_records = [_pending(job_id=f"p{i}") for i in range(25)]
        pending_sheet = _make_pending_sheet(pending_records)
        pending_sheet.find.return_value = MagicMock(row=2)
        matches_sheet = _make_matches_sheet()
        cell = MagicMock()
        cell.row = 2
        matches_sheet.find.return_value = cell

        new_matches = [_new_match(job_id=f"n{i}") for i in range(5)]
        result = run_prioritizer(pending_sheet, matches_sheet, new_matches)
        assert result["notified"] == 25
        assert result["parked"] == 5

    def test_overflow_pending_capped_at_daily_cap(self) -> None:
        pending_records = [_pending(job_id=f"p{i}") for i in range(30)]
        pending_sheet = _make_pending_sheet(pending_records)
        pending_sheet.find.return_value = MagicMock(row=2)
        matches_sheet = _make_matches_sheet()
        cell = MagicMock()
        cell.row = 2
        matches_sheet.find.return_value = cell

        result = run_prioritizer(pending_sheet, matches_sheet, [])
        # Even with 30 pending, should only notify 25
        assert result["notified"] == DAILY_CAP


if __name__ == "__main__":
    unittest.main()

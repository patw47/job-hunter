"""
Tests for csv_parser.py, offer_merger.py, layer2_scoring.py, deduplication.py,
and prioritizer.py — offline, no Drive, no n8n, no gspread network calls.
"""
from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import csv_parser
from csv_parser import load_indeed_csv, parse_indeed_csv
from offer_merger import merge_offers
from deduplication import compute_hash, is_duplicate, log_hashes
from layer2_scoring import write_match_if_qualified, MATCH_THRESHOLD
from prioritizer import run_prioritizer, DAILY_CAP
from telegram_notifier import (
    _build_indeed_card_text,
    _build_keyboard,
    _is_indeed_complete,
    send_indeed_card,
)
import notification_sender
from notification_sender import send_notifications

SCAN_DATE = "2026-06-11"

CANONICAL_COLUMNS = csv_parser.EXPECTED_COLUMNS

VALID_CSV = (
    "job_id,date_scanned,title,company,location,remote,url,source,"
    "match_rate,skills_found,status,cv_drive_link,letter_drive_link,applied_at\n"
    "abc0000000000001,2026-06-11,AI Engineer,Acme,Paris,true,"
    "https://fr.indeed.com/job/1,indeed,78.5,\"python,llm\",new,,,\n"
    "abc0000000000002,2026-06-11,ML Engineer,Globex,Remote,true,"
    "https://fr.indeed.com/job/2,indeed,65.0,pytorch,new,,,\n"
)

HEADERS_ONLY_CSV = (
    "job_id,date_scanned,title,company,location,remote,url,source,"
    "match_rate,skills_found,status,cv_drive_link,letter_drive_link,applied_at\n"
)

MISSING_URL_CSV = (
    "job_id,date_scanned,title,company,location,remote,url,source,"
    "match_rate,skills_found,status,cv_drive_link,letter_drive_link,applied_at\n"
    "abc0000000000001,2026-06-11,AI Engineer,Acme,Paris,true,"
    "https://fr.indeed.com/job/1,indeed,78.5,python,new,,,\n"
    "bad_row_no_url,2026-06-11,Bad Row,DropMe,Paris,true,,indeed,50.0,,new,,,\n"
    "abc0000000000003,2026-06-11,Data Eng,TechCo,Lyon,false,"
    "https://fr.indeed.com/job/3,indeed,70.0,sql,new,,,\n"
)

MISSING_JOB_ID_CSV = (
    "job_id,date_scanned,title,company,location,remote,url,source,"
    "match_rate,skills_found,status,cv_drive_link,letter_drive_link,applied_at\n"
    ",2026-06-11,No ID Row,Acme,Paris,true,https://fr.indeed.com/job/5,indeed,50.0,,new,,,\n"
    "abc0000000000001,2026-06-11,Good Row,Acme,Paris,true,"
    "https://fr.indeed.com/job/1,indeed,78.5,python,new,,,\n"
)

WRONG_ORDER_CSV = (
    "url,title,job_id,match_rate,date_scanned,company,location,remote,"
    "source,skills_found,status,cv_drive_link,letter_drive_link,applied_at\n"
    "https://fr.indeed.com/job/1,AI Engineer,abc0000000000001,78.5,"
    "2026-06-11,Acme,Paris,true,indeed,python,new,,,\n"
)

BOM_CSV = (
    "﻿job_id,date_scanned,title,company,location,remote,url,source,"
    "match_rate,skills_found,status,cv_drive_link,letter_drive_link,applied_at\n"
    "abc0000000000001,2026-06-11,AI Engineer,Acme,Paris,true,"
    "https://fr.indeed.com/job/1,indeed,78.5,python,new,,,\n"
)

EXTRA_COLUMNS_CSV = (
    "job_id,date_scanned,title,company,location,remote,url,source,"
    "match_rate,skills_found,status,cv_drive_link,letter_drive_link,applied_at,notes,extra_field\n"
    "abc0000000000001,2026-06-11,AI Engineer,Acme,Paris,true,"
    "https://fr.indeed.com/job/1,indeed,78.5,python,new,,,,,bonus\n"
)


class TestParseIndeedCsvHappyPath(unittest.TestCase):
    def setUp(self) -> None:
        self.offers = parse_indeed_csv(VALID_CSV)

    def test_returns_correct_count(self) -> None:
        self.assertEqual(len(self.offers), 2)

    def test_offer_has_all_canonical_fields(self) -> None:
        for offer in self.offers:
            for col in CANONICAL_COLUMNS:
                self.assertIn(col, offer, f"Missing field: {col}")

    def test_url_field_value(self) -> None:
        self.assertEqual(self.offers[0]["url"], "https://fr.indeed.com/job/1")

    def test_match_rate_preserved(self) -> None:
        self.assertEqual(self.offers[0]["match_rate"], "78.5")

    def test_source_preserved(self) -> None:
        self.assertEqual(self.offers[0]["source"], "indeed")

    def test_no_extra_fields(self) -> None:
        for offer in self.offers:
            self.assertEqual(set(offer.keys()), set(CANONICAL_COLUMNS))


class TestParseIndeedCsvEmpty(unittest.TestCase):
    def test_headers_only_returns_empty_list(self) -> None:
        self.assertEqual(parse_indeed_csv(HEADERS_ONLY_CSV), [])

    def test_empty_string_returns_empty_list(self) -> None:
        self.assertEqual(parse_indeed_csv(""), [])

    def test_whitespace_only_returns_empty_list(self) -> None:
        self.assertEqual(parse_indeed_csv("   \n  "), [])

    def test_headers_only_emits_zero_offers_log(self) -> None:
        with self.assertLogs("csv_parser", level="INFO") as cm:
            parse_indeed_csv(HEADERS_ONLY_CSV)
        self.assertTrue(
            any("indeed scan returned 0 offers" in line for line in cm.output),
            f"Expected '0 offers' log, got: {cm.output}",
        )


class TestParseIndeedCsvMissingUrl(unittest.TestCase):
    def setUp(self) -> None:
        self.offers = parse_indeed_csv(MISSING_URL_CSV)

    def test_row_missing_url_dropped(self) -> None:
        self.assertEqual(len(self.offers), 2)

    def test_valid_rows_kept(self) -> None:
        ids = {o["job_id"] for o in self.offers}
        self.assertIn("abc0000000000001", ids)
        self.assertIn("abc0000000000003", ids)
        self.assertNotIn("bad_row_no_url", ids)

    def test_drop_emits_warning(self) -> None:
        with self.assertLogs("csv_parser", level="WARNING") as cm:
            parse_indeed_csv(MISSING_URL_CSV)
        self.assertTrue(
            any("Dropping row" in line for line in cm.output),
            f"Expected drop warning, got: {cm.output}",
        )


class TestParseIndeedCsvMissingJobId(unittest.TestCase):
    def test_row_missing_job_id_dropped(self) -> None:
        offers = parse_indeed_csv(MISSING_JOB_ID_CSV)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["job_id"], "abc0000000000001")


class TestParseIndeedCsvWrongColumnOrder(unittest.TestCase):
    def test_wrong_order_returns_correct_count(self) -> None:
        offers = parse_indeed_csv(WRONG_ORDER_CSV)
        self.assertEqual(len(offers), 1)

    def test_wrong_order_fields_normalised(self) -> None:
        offers = parse_indeed_csv(WRONG_ORDER_CSV)
        self.assertEqual(offers[0]["url"], "https://fr.indeed.com/job/1")
        self.assertEqual(offers[0]["title"], "AI Engineer")
        self.assertEqual(offers[0]["job_id"], "abc0000000000001")


class TestParseIndeedCsvBom(unittest.TestCase):
    def test_bom_prefix_stripped(self) -> None:
        offers = parse_indeed_csv(BOM_CSV)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["job_id"], "abc0000000000001")


class TestParseIndeedCsvExtraColumns(unittest.TestCase):
    def test_extra_columns_ignored(self) -> None:
        offers = parse_indeed_csv(EXTRA_COLUMNS_CSV)
        self.assertEqual(len(offers), 1)
        self.assertEqual(set(offers[0].keys()), set(CANONICAL_COLUMNS))


class TestLoadIndeedCsvNotFound(unittest.TestCase):
    def test_nonexistent_path_returns_empty_list(self) -> None:
        result = load_indeed_csv(Path("/nonexistent/indeed-matches-2099-01-01.csv"))
        self.assertEqual(result, [])

    def test_nonexistent_path_logs_not_found_message(self) -> None:
        with self.assertLogs("csv_parser", level="INFO") as cm:
            load_indeed_csv(Path("/nonexistent/indeed-matches-2099-01-01.csv"))
        self.assertTrue(
            any("indeed CSV not found" in line for line in cm.output),
            f"Expected not-found log, got: {cm.output}",
        )

    def test_nonexistent_path_no_exception(self) -> None:
        try:
            load_indeed_csv(Path("/nonexistent/indeed-matches-2099-01-01.csv"))
        except Exception as exc:
            self.fail(f"load_indeed_csv raised unexpectedly: {exc}")


class TestLoadIndeedCsvFromFile(unittest.TestCase):
    def test_reads_valid_fixture_file(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".csv",
            delete=False,
            prefix="indeed-matches-2026-06-11",
            encoding="utf-8",
        ) as fh:
            fh.write(VALID_CSV)
            tmp_path = Path(fh.name)

        try:
            offers = load_indeed_csv(tmp_path)
            self.assertEqual(len(offers), 2)
        finally:
            tmp_path.unlink(missing_ok=True)


def _indeed_offer(**overrides: object) -> dict:
    base: dict = {
        "url": "https://fr.indeed.com/job/1",
        "title": "AI Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "Build AI systems.",
        "job_type": "remote",
        "date_posted": "2026-06-11",
        "source": "indeed",
        "match_rate": "78.5",
        "skills_found": "python,llm",
    }
    base.update(overrides)
    return base


def _linkedin_offer(**overrides: object) -> dict:
    base: dict = {
        "url": "https://www.linkedin.com/jobs/view/1",
        "title": "ML Engineer",
        "company": "Globex",
        "location": "Remote",
        "description": "ML role.",
        "job_type": "full_time",
        "date_posted": "2026-06-01",
        "source": "linkedin",
    }
    base.update(overrides)
    return base


class TestMergeIndeedEmptyLinkedIn30(unittest.TestCase):
    """AC1: Indeed CSV vide + LinkedIn 30 offres → 30 offres source linkedin."""

    def setUp(self) -> None:
        linkedin = [
            _linkedin_offer(url=f"https://www.linkedin.com/jobs/view/{i}")
            for i in range(30)
        ]
        self.result = merge_offers([], linkedin)

    def test_count(self) -> None:
        self.assertEqual(len(self.result), 30)

    def test_source_is_linkedin(self) -> None:
        for offer in self.result:
            self.assertEqual(offer["source"], "linkedin")

    def test_match_rate_null(self) -> None:
        for offer in self.result:
            self.assertIsNone(offer["match_rate"])

    def test_skills_found_null(self) -> None:
        for offer in self.result:
            self.assertIsNone(offer["skills_found"])


class TestMergeIndeed15LinkedInEmpty(unittest.TestCase):
    """AC2: Indeed 15 offres + LinkedIn vide (CAPTCHA) → 15 offres source indeed."""

    def setUp(self) -> None:
        indeed = [
            _indeed_offer(url=f"https://fr.indeed.com/job/{i}")
            for i in range(15)
        ]
        self.result = merge_offers(indeed, [])

    def test_count(self) -> None:
        self.assertEqual(len(self.result), 15)

    def test_source_is_indeed(self) -> None:
        for offer in self.result:
            self.assertEqual(offer["source"], "indeed")

    def test_match_rate_preserved(self) -> None:
        for offer in self.result:
            self.assertEqual(offer["match_rate"], "78.5")


class TestMergeDedupIntraSource(unittest.TestCase):
    """AC3: Indeed 5 + LinkedIn 5, 2 URLs identiques → 8 offres uniques."""

    def setUp(self) -> None:
        shared = [
            "https://fr.indeed.com/job/shared-1",
            "https://fr.indeed.com/job/shared-2",
        ]
        indeed = [
            _indeed_offer(url=shared[0]),
            _indeed_offer(url=shared[1]),
            _indeed_offer(url="https://fr.indeed.com/job/unique-1"),
            _indeed_offer(url="https://fr.indeed.com/job/unique-2"),
            _indeed_offer(url="https://fr.indeed.com/job/unique-3"),
        ]
        linkedin = [
            _linkedin_offer(url=shared[0]),
            _linkedin_offer(url=shared[1]),
            _linkedin_offer(url="https://www.linkedin.com/jobs/view/unique-a"),
            _linkedin_offer(url="https://www.linkedin.com/jobs/view/unique-b"),
            _linkedin_offer(url="https://www.linkedin.com/jobs/view/unique-c"),
        ]
        self.result = merge_offers(indeed, linkedin)

    def test_count_is_8(self) -> None:
        self.assertEqual(len(self.result), 8)

    def test_no_duplicate_urls(self) -> None:
        urls = [o["url"] for o in self.result]
        self.assertEqual(len(urls), len(set(urls)))

    def test_duplicate_url_kept_as_indeed(self) -> None:
        shared_url = "https://fr.indeed.com/job/shared-1"
        matches = [o for o in self.result if o["url"] == shared_url]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["source"], "indeed")


class TestMergeSourcePreservation(unittest.TestCase):
    """AC4: Le champ source est conservé fidèlement pour chaque offre."""

    def test_indeed_source_preserved(self) -> None:
        result = merge_offers([_indeed_offer()], [])
        self.assertEqual(result[0]["source"], "indeed")

    def test_linkedin_source_preserved(self) -> None:
        result = merge_offers([], [_linkedin_offer()])
        self.assertEqual(result[0]["source"], "linkedin")

    def test_mixed_sources_preserved(self) -> None:
        result = merge_offers(
            [_indeed_offer(url="https://fr.indeed.com/job/1")],
            [_linkedin_offer(url="https://www.linkedin.com/jobs/view/1")],
        )
        sources = [o["source"] for o in result]
        self.assertIn("indeed", sources)
        self.assertIn("linkedin", sources)

    def test_match_rate_null_for_linkedin(self) -> None:
        result = merge_offers([], [_linkedin_offer()])
        self.assertIsNone(result[0]["match_rate"])

    def test_skills_found_null_for_linkedin(self) -> None:
        result = merge_offers([], [_linkedin_offer()])
        self.assertIsNone(result[0]["skills_found"])

    def test_match_rate_preserved_for_indeed(self) -> None:
        result = merge_offers([_indeed_offer(match_rate="78.5")], [])
        self.assertEqual(result[0]["match_rate"], "78.5")

    def test_indeed_before_linkedin_in_output(self) -> None:
        result = merge_offers(
            [_indeed_offer(url="https://fr.indeed.com/job/1")],
            [_linkedin_offer(url="https://www.linkedin.com/jobs/view/1")],
        )
        self.assertEqual(result[0]["source"], "indeed")
        self.assertEqual(result[1]["source"], "linkedin")


def _qualified_offer(idx: int = 0, source: str = "linkedin") -> dict:
    """Offer with all fields required by run_prioritizer / _build_matches_row."""
    if source == "linkedin":
        url = f"https://www.linkedin.com/jobs/view/{idx}"
    else:
        url = f"https://fr.indeed.com/job/{idx}"
    return {
        "job_id": f"{idx:016d}",
        "date_scanned": SCAN_DATE,
        "url": url,
        "title": "AI Engineer",
        "company": "Acme",
        "location": "Remote",
        "remote": "yes",
        "source": source,
        "match_rate": "75.0",
        "skills_found": "python,llm",
        "status": "new",
        "cv_drive_link": "",
        "letter_drive_link": "",
        "applied_at": "",
        "job_type": "remote",
        "description": "AI role.",
    }


# ---------------------------------------------------------------------------
# AC1 — Indeed match_rate preserved verbatim (not recalculated)
# ---------------------------------------------------------------------------

class TestAC1IndeedMatchRatePreserved(unittest.TestCase):
    """AC1: Offre Indeed match_rate=75 → écrite avec le score CSV, non recalculée."""

    def setUp(self) -> None:
        self.ws = MagicMock()
        self.offer = _indeed_offer()

    def test_returns_true_when_above_threshold(self) -> None:
        result = write_match_if_qualified(self.offer, 75.0, ["python", "llm"], self.ws, SCAN_DATE)
        self.assertTrue(result)

    def test_append_row_called(self) -> None:
        write_match_if_qualified(self.offer, 75.0, ["python", "llm"], self.ws, SCAN_DATE)
        self.ws.append_row.assert_called_once()

    def test_match_rate_written_verbatim(self) -> None:
        write_match_if_qualified(self.offer, 75.0, ["python", "llm"], self.ws, SCAN_DATE)
        row = self.ws.append_row.call_args[0][0]
        self.assertEqual(row[8], "75.0")

    def test_status_is_new(self) -> None:
        write_match_if_qualified(self.offer, 75.0, ["python", "llm"], self.ws, SCAN_DATE)
        row = self.ws.append_row.call_args[0][0]
        self.assertEqual(row[10], "new")

    def test_column_order_14_cols(self) -> None:
        write_match_if_qualified(self.offer, 75.0, ["python", "llm"], self.ws, SCAN_DATE)
        row = self.ws.append_row.call_args[0][0]
        # MATCHES header: job_id, date_scanned, title, company, location, remote,
        #                 url, source, match_rate, skills_found, status,
        #                 cv_drive_link, letter_drive_link, applied_at
        self.assertEqual(len(row), 14)
        self.assertEqual(row[1], SCAN_DATE)
        self.assertEqual(row[7], "indeed")
        self.assertEqual(row[9], "python,llm")

    def test_skills_found_list_joined_comma(self) -> None:
        write_match_if_qualified(self.offer, 75.0, ["python", "llm", "ml"], self.ws, SCAN_DATE)
        row = self.ws.append_row.call_args[0][0]
        self.assertEqual(row[9], "python,llm,ml")


# ---------------------------------------------------------------------------
# AC2 — LinkedIn match_rate calculé ≥ 60% → écrit dans MATCHES
# ---------------------------------------------------------------------------

class TestAC2LinkedInMatchRateWritten(unittest.TestCase):
    """AC2: Offre LinkedIn avec match_rate ≥ 60% → écrite dans MATCHES."""

    def setUp(self) -> None:
        self.ws = MagicMock()
        self.offer = _linkedin_offer()

    def test_60pct_written(self) -> None:
        result = write_match_if_qualified(self.offer, 60.0, ["python"], self.ws, SCAN_DATE)
        self.assertTrue(result)
        self.ws.append_row.assert_called_once()

    def test_above_threshold_written(self) -> None:
        result = write_match_if_qualified(self.offer, 85.0, ["python", "ml"], self.ws, SCAN_DATE)
        self.assertTrue(result)

    def test_source_linkedin_in_row(self) -> None:
        write_match_if_qualified(self.offer, 65.0, ["python", "ml"], self.ws, SCAN_DATE)
        row = self.ws.append_row.call_args[0][0]
        self.assertEqual(row[7], "linkedin")

    def test_match_rate_string_format(self) -> None:
        write_match_if_qualified(self.offer, 65.0, ["python"], self.ws, SCAN_DATE)
        row = self.ws.append_row.call_args[0][0]
        self.assertEqual(row[8], "65.0")

    def test_status_is_new(self) -> None:
        write_match_if_qualified(self.offer, 65.0, ["python"], self.ws, SCAN_DATE)
        row = self.ws.append_row.call_args[0][0]
        self.assertEqual(row[10], "new")


# ---------------------------------------------------------------------------
# AC3 — match_rate < 60% → absent de MATCHES, présent uniquement dans SCANNED_HASHES
# ---------------------------------------------------------------------------

class TestAC3BelowThresholdNotWritten(unittest.TestCase):
    """AC3: match_rate < 60% → absent de MATCHES."""

    def test_59pct_not_written(self) -> None:
        ws = MagicMock()
        result = write_match_if_qualified(_indeed_offer(), 59.9, [], ws, SCAN_DATE)
        self.assertFalse(result)
        ws.append_row.assert_not_called()

    def test_0pct_not_written(self) -> None:
        ws = MagicMock()
        result = write_match_if_qualified(_linkedin_offer(), 0.0, [], ws, SCAN_DATE)
        self.assertFalse(result)
        ws.append_row.assert_not_called()

    def test_boundary_exactly_60_written(self) -> None:
        ws = MagicMock()
        result = write_match_if_qualified(_indeed_offer(), 60.0, [], ws, SCAN_DATE)
        self.assertTrue(result)

    def test_linkedin_59pct_not_written(self) -> None:
        ws = MagicMock()
        result = write_match_if_qualified(_linkedin_offer(), 59.9, [], ws, SCAN_DATE)
        self.assertFalse(result)
        ws.append_row.assert_not_called()

    def test_log_hashes_still_called_for_below_threshold(self) -> None:
        ws = MagicMock()
        hashes = [{
            "url_hash": compute_hash("https://fr.indeed.com/job/99"),
            "title": "Low Match",
            "company": "Corp",
            "url": "https://fr.indeed.com/job/99",
            "source": "indeed",
            "scan_date": SCAN_DATE,
        }]
        log_hashes(hashes, ws)
        ws.append_rows.assert_called_once()


# ---------------------------------------------------------------------------
# AC4 — Déduplication : offre déjà dans SCANNED_HASHES → filtrée
# ---------------------------------------------------------------------------

class TestAC4DeduplicationFilters(unittest.TestCase):
    """AC4: Offre déjà dans SCANNED_HASHES → non réécrite dans MATCHES."""

    def test_known_hash_is_duplicate(self) -> None:
        url = "https://fr.indeed.com/job/1"
        ws = MagicMock()
        ws.col_values.return_value = [compute_hash(url)]
        self.assertTrue(is_duplicate(compute_hash(url), ws))

    def test_unknown_hash_not_duplicate(self) -> None:
        ws = MagicMock()
        ws.col_values.return_value = []
        self.assertFalse(is_duplicate(compute_hash("https://example.com"), ws))

    def test_col_values_queried_on_column_1(self) -> None:
        ws = MagicMock()
        ws.col_values.return_value = []
        is_duplicate("anyhash", ws)
        ws.col_values.assert_called_with(1)

    def test_different_url_not_duplicate(self) -> None:
        url_a = "https://fr.indeed.com/job/1"
        url_b = "https://fr.indeed.com/job/2"
        ws = MagicMock()
        ws.col_values.return_value = [compute_hash(url_a)]
        self.assertFalse(is_duplicate(compute_hash(url_b), ws))

    def test_log_hashes_batch_write(self) -> None:
        ws = MagicMock()
        hashes = [
            {"url_hash": "h1", "title": "T1", "company": "C1",
             "url": "https://a.com", "source": "indeed", "scan_date": SCAN_DATE},
            {"url_hash": "h2", "title": "T2", "company": "C2",
             "url": "https://b.com", "source": "linkedin", "scan_date": SCAN_DATE},
        ]
        log_hashes(hashes, ws)
        ws.append_rows.assert_called_once()
        rows_written = ws.append_rows.call_args[0][0]
        self.assertEqual(len(rows_written), 2)

    def test_log_hashes_empty_no_write(self) -> None:
        ws = MagicMock()
        log_hashes([], ws)
        ws.append_rows.assert_not_called()

    def test_log_hashes_row_order_matches_header(self) -> None:
        # SCANNED_HASHES header: sha256 | date_scanned | url | title | company | source
        ws = MagicMock()
        h = {"url_hash": "deadbeef", "title": "Engineer", "company": "Acme",
             "url": "https://x.com/job/1", "source": "indeed", "scan_date": SCAN_DATE}
        log_hashes([h], ws)
        row = ws.append_rows.call_args[0][0][0]
        self.assertEqual(row[0], "deadbeef")
        self.assertEqual(row[1], SCAN_DATE)
        self.assertEqual(row[2], "https://x.com/job/1")
        self.assertEqual(row[5], "indeed")


# ---------------------------------------------------------------------------
# AC5 — Cap 25 : 30 offres qualifiées → 25 dans MATCHES, 5 dans PENDING_MATCHES
# ---------------------------------------------------------------------------

class TestAC5Cap25PendingMatches(unittest.TestCase):
    """AC5: 30 offres qualifiées le même jour → 25 MATCHES, 5 PENDING_MATCHES."""

    def _make_sheets(self) -> tuple:
        pending = MagicMock()
        matches = MagicMock()
        pending.get_all_records.return_value = []
        matches.find.side_effect = Exception("not found")
        pending.find.side_effect = Exception("not found")
        return pending, matches

    def test_30_qualified_notified_25_parked_5(self) -> None:
        pending, matches = self._make_sheets()
        new_matches = [_qualified_offer(i) for i in range(30)]
        result = run_prioritizer(pending, matches, new_matches)
        self.assertEqual(result["notified"], 25)
        self.assertEqual(result["parked"], 5)

    def test_matches_sheet_written_25_times(self) -> None:
        pending, matches = self._make_sheets()
        new_matches = [_qualified_offer(i) for i in range(30)]
        run_prioritizer(pending, matches, new_matches)
        self.assertEqual(matches.append_rows.call_count, 25)

    def test_pending_sheet_written_once_with_5_rows(self) -> None:
        pending, matches = self._make_sheets()
        new_matches = [_qualified_offer(i) for i in range(30)]
        run_prioritizer(pending, matches, new_matches)
        # write_pending_tab calls append_rows once for all overflow rows
        self.assertEqual(pending.append_rows.call_count, 1)
        rows = pending.append_rows.call_args[0][0]
        self.assertEqual(len(rows), 5)

    def test_zero_qualified_nothing_written(self) -> None:
        pending, matches = self._make_sheets()
        result = run_prioritizer(pending, matches, [])
        self.assertEqual(result["notified"], 0)
        self.assertEqual(result["parked"], 0)
        matches.append_rows.assert_not_called()

    def test_mixed_sources_same_cap(self) -> None:
        pending, matches = self._make_sheets()
        indeed_offers = [_qualified_offer(i, source="indeed") for i in range(15)]
        linkedin_offers = [_qualified_offer(i + 100, source="linkedin") for i in range(15)]
        result = run_prioritizer(pending, matches, indeed_offers + linkedin_offers)
        self.assertEqual(result["notified"], 25)
        self.assertEqual(result["parked"], 5)

    def test_daily_cap_constant_is_25(self) -> None:
        self.assertEqual(DAILY_CAP, 25)


def _indeed_offer_complete(**overrides: object) -> dict:
    """Indeed offer with all fields required by _is_indeed_complete."""
    base: dict = {
        "url": "https://fr.indeed.com/job/42",
        "title": "AI Engineer",
        "company": "Acme",
        "location": "Paris",
        "remote": "true",
        "source": "indeed",
        "match_rate": "82.0",
        "skills_found": "python,llm",
        "job_id": "abc0000000000042",
        "date_scanned": SCAN_DATE,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# S3-AC1 — Indeed complet → carte directe, pas d'appel Haiku
# ---------------------------------------------------------------------------

class TestS3AC1IndeedCompleteNoHaiku(unittest.TestCase):
    """S3-AC1: Indeed all fields → card built directly, _call_analyze NOT called."""

    def test_is_complete_all_fields_present(self) -> None:
        self.assertTrue(_is_indeed_complete(_indeed_offer_complete()))

    def test_is_incomplete_company_empty(self) -> None:
        self.assertFalse(_is_indeed_complete(_indeed_offer_complete(company="")))

    def test_is_incomplete_skills_empty(self) -> None:
        self.assertFalse(_is_indeed_complete(_indeed_offer_complete(skills_found="")))

    def test_is_incomplete_location_whitespace_only(self) -> None:
        self.assertFalse(_is_indeed_complete(_indeed_offer_complete(location="   ")))

    def test_build_card_contains_title(self) -> None:
        self.assertIn("AI Engineer", _build_indeed_card_text(_indeed_offer_complete()))

    def test_build_card_contains_match_rate_pct(self) -> None:
        self.assertIn("82%", _build_indeed_card_text(_indeed_offer_complete()))

    def test_build_card_contains_skills(self) -> None:
        self.assertIn("python,llm", _build_indeed_card_text(_indeed_offer_complete()))

    def test_build_card_contains_source_indeed(self) -> None:
        self.assertIn("Indeed", _build_indeed_card_text(_indeed_offer_complete()))

    def test_build_card_contains_url(self) -> None:
        self.assertIn("https://fr.indeed.com/job/42", _build_indeed_card_text(_indeed_offer_complete()))

    def test_send_notifications_complete_indeed_skips_analyze(self) -> None:
        with patch("notification_sender._call_analyze") as mock_analyze, \
             patch("notification_sender.telegram_notifier.send_indeed_card") as mock_card:
            mock_card.return_value = None
            send_notifications(
                [_indeed_offer_complete(match_rate="82.0")],
                "tok", "cid", "http://127.0.0.1:18798",
            )
        mock_card.assert_called_once()
        mock_analyze.assert_not_called()


# ---------------------------------------------------------------------------
# S3-AC2 — Indeed incomplet → fallback POST /analyze
# ---------------------------------------------------------------------------

class TestS3AC2IndeedIncompleteFallback(unittest.TestCase):
    """S3-AC2: Indeed company="" → fallback POST /analyze."""

    def test_incomplete_indeed_calls_analyze(self) -> None:
        offer = _indeed_offer_complete(company="", match_rate="82.0")
        with patch("notification_sender._call_analyze", return_value="card text") as mock_analyze, \
             patch("notification_sender.telegram_notifier.send_card_from_text", return_value=None):
            send_notifications([offer], "tok", "cid", "http://127.0.0.1:18798")
        mock_analyze.assert_called_once()

    def test_incomplete_indeed_no_direct_card(self) -> None:
        offer = _indeed_offer_complete(company="", match_rate="82.0")
        with patch("notification_sender._call_analyze", return_value="card text"), \
             patch("notification_sender.telegram_notifier.send_card_from_text", return_value=None), \
             patch("notification_sender.telegram_notifier.send_indeed_card") as mock_direct:
            send_notifications([offer], "tok", "cid", "http://127.0.0.1:18798")
        mock_direct.assert_not_called()

    def test_linkedin_complete_sends_direct_card_without_analyze(self) -> None:
        # Since LinkedIn offers carry match_rate + skills_found, complete data
        # short-circuits Haiku regardless of source (Patricia: "je veux juste la carte").
        offer = _qualified_offer(99, source="linkedin")
        offer["match_rate"] = "85.0"
        with patch("notification_sender._call_analyze", return_value="card text") as mock_analyze, \
             patch("notification_sender.telegram_notifier.send_card_from_text", return_value=None), \
             patch("notification_sender.telegram_notifier.send_indeed_card") as mock_direct:
            send_notifications([offer], "tok", "cid", "http://127.0.0.1:18798")
        mock_direct.assert_called_once()
        mock_analyze.assert_not_called()

    def test_linkedin_incomplete_falls_back_to_analyze(self) -> None:
        offer = _qualified_offer(99, source="linkedin")
        offer["match_rate"] = "85.0"
        offer["skills_found"] = ""
        with patch("notification_sender._call_analyze", return_value="card text") as mock_analyze, \
             patch("notification_sender.telegram_notifier.send_card_from_text", return_value=None), \
             patch("notification_sender.telegram_notifier.send_indeed_card") as mock_direct:
            send_notifications([offer], "tok", "cid", "http://127.0.0.1:18798")
        mock_analyze.assert_called_once()
        mock_direct.assert_not_called()


# ---------------------------------------------------------------------------
# S3-AC3 — Digest trié : Indeed 78% avant LinkedIn 75%
# ---------------------------------------------------------------------------

class TestS3AC3DigestSortedMixed(unittest.TestCase):
    """S3-AC3: LinkedIn 75% + Indeed 78% → digest sorted desc, Indeed first."""

    def test_digest_indeed_before_linkedin_when_higher_rate(self) -> None:
        linkedin = _qualified_offer(1, source="linkedin")  # match_rate="75.0"
        indeed = _indeed_offer_complete(match_rate="78.0", url="https://fr.indeed.com/job/99")
        captured: list = []
        with patch("notification_sender.telegram_notifier.send_digest",
                   side_effect=lambda o, t, c: captured.extend(o)):
            send_notifications([linkedin, indeed], "tok", "cid", "http://127.0.0.1:18798")
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0]["source"], "indeed")

    def test_digest_sorted_descending_three_offers(self) -> None:
        offers = [
            _indeed_offer_complete(match_rate="61.0", url="https://fr.indeed.com/job/1"),
            _indeed_offer_complete(match_rate="75.0", url="https://fr.indeed.com/job/2"),
            _indeed_offer_complete(match_rate="68.0", url="https://fr.indeed.com/job/3"),
        ]
        captured: list = []
        with patch("notification_sender.telegram_notifier.send_digest",
                   side_effect=lambda o, t, c: captured.extend(o)):
            send_notifications(offers, "tok", "cid", "http://127.0.0.1:18798")
        rates = [float(o["match_rate"]) for o in captured]
        self.assertEqual(rates, sorted(rates, reverse=True))

    def test_below_60_not_in_digest(self) -> None:
        offer = _indeed_offer_complete(match_rate="59.9", url="https://fr.indeed.com/job/5")
        captured: list = []
        with patch("notification_sender.telegram_notifier.send_digest",
                   side_effect=lambda o, t, c: captured.extend(o)):
            send_notifications([offer], "tok", "cid", "http://127.0.0.1:18798")
        self.assertEqual(len(captured), 0)


# ---------------------------------------------------------------------------
# S3-AC4 — Cap 25 toutes sources confondues
# ---------------------------------------------------------------------------

class TestS3AC4Cap25NotifRespected(unittest.TestCase):
    """S3-AC4: 25 offers all sent (cap enforced upstream by run_prioritizer)."""

    def test_25_high_offers_all_sent_individually(self) -> None:
        offers = [
            _indeed_offer_complete(
                match_rate="82.0",
                url=f"https://fr.indeed.com/job/{i}",
            )
            for i in range(25)
        ]
        sent: list = []
        with patch("notification_sender.telegram_notifier.send_indeed_card",
                   side_effect=lambda o, t, c: sent.append(o)):
            result = send_notifications(offers, "tok", "cid", "http://127.0.0.1:18798")
        self.assertEqual(len(sent), 25)
        self.assertEqual(result["sent_individual"], 25)
        self.assertEqual(result["sent_digest"], 0)

    def test_empty_new_matches_sends_nothing(self) -> None:
        with patch("notification_sender.telegram_notifier.send_indeed_card") as mock_c, \
             patch("notification_sender.telegram_notifier.send_digest") as mock_d:
            result = send_notifications([], "tok", "cid", "http://127.0.0.1:18798")
        mock_c.assert_not_called()
        mock_d.assert_not_called()
        self.assertEqual(result["sent_individual"], 0)
        self.assertEqual(result["sent_digest"], 0)

    def test_mixed_sources_25_total(self) -> None:
        indeed = [
            _indeed_offer_complete(match_rate="82.0", url=f"https://fr.indeed.com/job/{i}")
            for i in range(13)
        ]
        linkedin = [_qualified_offer(i + 100, source="linkedin") for i in range(12)]
        for o in linkedin:
            o["match_rate"] = "81.0"
        with patch("notification_sender.telegram_notifier.send_indeed_card",
                   side_effect=lambda o, t, c: None), \
             patch("notification_sender._call_analyze", return_value="card"), \
             patch("notification_sender.telegram_notifier.send_card_from_text",
                   side_effect=lambda t, o, tok, c: None):
            result = send_notifications(indeed + linkedin, "tok", "cid", "http://127.0.0.1:18798")
        self.assertEqual(result["sent_individual"], 25)


# ---------------------------------------------------------------------------
# S3-AC5 — Boutons inline [✅ Générer CV] [❌ Ignorer] sur carte Indeed
# ---------------------------------------------------------------------------

class TestS3AC5InlineKeyboardIndeed(unittest.TestCase):
    """S3-AC5: Boutons Générer CV et Ignorer présents sur carte Indeed."""

    def _all_button_texts(self) -> list[str]:
        kb = _build_keyboard("deadbeef", "https://fr.indeed.com/job/1")
        return [btn["text"] for row in kb["inline_keyboard"] for btn in row]

    def test_generate_cv_button_present(self) -> None:
        self.assertTrue(any("Générer CV" in t for t in self._all_button_texts()))

    def test_ignore_button_present(self) -> None:
        self.assertTrue(any("Ignorer" in t for t in self._all_button_texts()))

    def test_send_indeed_card_uses_markdown_parse_mode(self) -> None:
        captured: list = []
        with patch("telegram_notifier._telegram_post",
                   side_effect=lambda t, m, p: captured.append(p) or {"ok": True}):
            send_indeed_card(_indeed_offer_complete(), "tok", "cid")
        self.assertEqual(captured[0]["parse_mode"], "Markdown")

    def test_send_indeed_card_has_inline_keyboard(self) -> None:
        captured: list = []
        with patch("telegram_notifier._telegram_post",
                   side_effect=lambda t, m, p: captured.append(p) or {"ok": True}):
            send_indeed_card(_indeed_offer_complete(), "tok", "cid")
        self.assertIn("inline_keyboard", captured[0]["reply_markup"])

    def test_send_indeed_card_text_contains_indeed_badge(self) -> None:
        captured: list = []
        with patch("telegram_notifier._telegram_post",
                   side_effect=lambda t, m, p: captured.append(p) or {"ok": True}):
            send_indeed_card(_indeed_offer_complete(), "tok", "cid")
        self.assertIn("Indeed", captured[0]["text"])


if __name__ == "__main__":
    unittest.main()

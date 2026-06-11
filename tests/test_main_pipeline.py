"""
Tests for csv_parser.py — offline, fixture CSV strings only. No Drive, no n8n.
"""
from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import csv_parser
from csv_parser import load_indeed_csv, parse_indeed_csv

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


if __name__ == "__main__":
    unittest.main()

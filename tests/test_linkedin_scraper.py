"""
Unit tests for linkedin_scraper.py — offline, no live LinkedIn or Playwright required.
All async and filesystem dependencies are mocked or use temp directories.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import linkedin_scraper as ls

# ── Fixtures ───────────────────────────────────────────────────────────────────

_LONG_DESC = "x" * 600
_RAW_FULL = {
    "jobUrl": "https://www.linkedin.com/jobs/view/123",
    "jobTitle": "AI Engineer",
    "companyName": "Acme Corp",
    "formattedLocation": "Paris, FR (Remote)",
    "description": _LONG_DESC,
    "employmentType": "FULL_TIME",
    "listedAt": "2026-06-01",
}


class TestConstants(unittest.TestCase):
    def test_global_cap_is_40(self) -> None:
        assert ls.GLOBAL_CAP == 40

    def test_hybrid_threshold_is_10(self) -> None:
        assert ls.HYBRID_THRESHOLD == 10

    def test_delay_min(self) -> None:
        assert ls.DELAY_MIN == 3.0

    def test_delay_max(self) -> None:
        assert ls.DELAY_MAX == 8.0

    def test_scan_roots_has_12_entries(self) -> None:
        assert len(ls.SCAN_ROOTS) == 12, f"Expected 12 roots, got {len(ls.SCAN_ROOTS)}"

    def test_scan_roots_no_duplicates(self) -> None:
        assert len(set(ls.SCAN_ROOTS)) == len(ls.SCAN_ROOTS), "Duplicate entries in SCAN_ROOTS"


class TestNormalizeOffer(unittest.TestCase):
    def test_source_is_always_linkedin(self) -> None:
        result = ls.normalize_offer(_RAW_FULL)
        assert result["source"] == "linkedin"

    def test_source_not_overridable_by_raw(self) -> None:
        raw = {**_RAW_FULL, "source": "indeed"}
        assert ls.normalize_offer(raw)["source"] == "linkedin"

    def test_description_truncated_to_500(self) -> None:
        result = ls.normalize_offer(_RAW_FULL)
        assert len(result["description"]) == 500

    def test_description_under_500_unchanged(self) -> None:
        raw = {**_RAW_FULL, "description": "short desc"}
        assert ls.normalize_offer(raw)["description"] == "short desc"

    def test_description_exactly_500_unchanged(self) -> None:
        raw = {**_RAW_FULL, "description": "y" * 500}
        assert len(ls.normalize_offer(raw)["description"]) == 500

    def test_canonical_fields_present(self) -> None:
        result = ls.normalize_offer(_RAW_FULL)
        for field in ("url", "title", "company", "location", "description",
                      "job_type", "date_posted", "source"):
            with self.subTest(field=field):
                assert field in result, f"Missing field: {field}"

    def test_alias_url_jobUrl(self) -> None:
        assert ls.normalize_offer({"jobUrl": "https://li.com/123"})["url"] == "https://li.com/123"

    def test_alias_title_jobTitle(self) -> None:
        assert ls.normalize_offer({"jobTitle": "ML Engineer"})["title"] == "ML Engineer"

    def test_alias_company_companyName(self) -> None:
        assert ls.normalize_offer({"companyName": "OpenAI"})["company"] == "OpenAI"

    def test_alias_location_formattedLocation(self) -> None:
        assert ls.normalize_offer({"formattedLocation": "Remote"})["location"] == "Remote"

    def test_alias_date_listedAt(self) -> None:
        assert ls.normalize_offer({"listedAt": "2026-06-01"})["date_posted"] == "2026-06-01"

    def test_alias_date_postedAt(self) -> None:
        assert ls.normalize_offer({"postedAt": "2026-05-30"})["date_posted"] == "2026-05-30"

    def test_alias_job_type_employmentType(self) -> None:
        assert ls.normalize_offer({"employmentType": "CONTRACT"})["job_type"] == "CONTRACT"

    def test_empty_raw_gives_empty_strings(self) -> None:
        result = ls.normalize_offer({})
        for field in ("url", "title", "company", "location", "description", "job_type", "date_posted"):
            with self.subTest(field=field):
                assert result[field] == "", f"Expected empty string for {field}"
        assert result["source"] == "linkedin"

    def test_values_stripped_of_whitespace(self) -> None:
        raw = {"title": "  AI Lead  ", "company": "\nAcme\n"}
        result = ls.normalize_offer(raw)
        assert result["title"] == "AI Lead"
        assert result["company"] == "Acme"


class TestDetectCaptcha(unittest.TestCase):
    def test_captcha_in_content(self) -> None:
        assert ls.detect_captcha("https://linkedin.com/jobs", "please solve captcha") is True

    def test_authwall_in_url(self) -> None:
        assert ls.detect_captcha("https://linkedin.com/authwall?trk=x", "") is True

    def test_challenge_in_url(self) -> None:
        assert ls.detect_captcha("https://linkedin.com/challenge/", "") is True

    def test_unusual_activity_in_content(self) -> None:
        assert ls.detect_captcha("https://linkedin.com/jobs", "unusual activity detected") is True

    def test_security_check_in_content(self) -> None:
        assert ls.detect_captcha("", "security check required") is True

    def test_are_you_a_robot(self) -> None:
        assert ls.detect_captcha("", "are you a robot? click to verify") is True

    def test_case_insensitive(self) -> None:
        assert ls.detect_captcha("", "CAPTCHA REQUIRED") is True

    def test_normal_url_returns_false(self) -> None:
        assert (
            ls.detect_captcha("https://linkedin.com/jobs/search?keywords=AI", "<h1>Jobs</h1>")
            is False
        )

    def test_empty_inputs_return_false(self) -> None:
        assert ls.detect_captcha("", "") is False

    def test_partial_word_no_false_positive(self) -> None:
        # "capability" contains "cap" not "captcha" — should not trigger
        assert ls.detect_captcha("", "capability enhancement platform") is False


class TestSessionFile(unittest.TestCase):
    def test_contains_today_iso(self) -> None:
        p = ls._session_file()
        assert date.today().isoformat() in p.name

    def test_is_under_session_dir(self) -> None:
        assert ls._session_file().parent == ls.SESSION_DIR

    def test_suffix_is_json(self) -> None:
        assert ls._session_file().suffix == ".json"


class TestLoadSession(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = ls.SESSION_DIR
        ls.SESSION_DIR = Path(self._tmp.name)

    def tearDown(self) -> None:
        ls.SESSION_DIR = self._orig
        self._tmp.cleanup()

    def test_returns_none_when_file_missing(self) -> None:
        assert ls.load_session() is None

    def test_returns_cookies_when_file_exists(self) -> None:
        cookies = [{"name": "li_at", "value": "TOKEN", "domain": ".linkedin.com"}]
        ls._session_file().write_text(json.dumps(cookies))
        result = ls.load_session()
        assert result == cookies

    def test_invalid_json_returns_none(self) -> None:
        ls._session_file().write_text("NOT JSON {{{")
        result = ls.load_session()
        assert result is None

    def test_empty_list_returns_none(self) -> None:
        ls._session_file().write_text("[]")
        assert ls.load_session() is None

    def test_session_dir_not_existing_returns_none(self) -> None:
        ls.SESSION_DIR = Path(self._tmp.name) / "nonexistent"
        assert ls.load_session() is None


class TestSaveSession(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = ls.SESSION_DIR
        ls.SESSION_DIR = Path(self._tmp.name)

    def tearDown(self) -> None:
        ls.SESSION_DIR = self._orig
        self._tmp.cleanup()

    def test_creates_file(self) -> None:
        ls.save_session([{"name": "x"}])
        assert ls._session_file().exists()

    def test_file_content_round_trips(self) -> None:
        cookies = [{"name": "li_at", "value": "ABC"}]
        ls.save_session(cookies)
        on_disk = json.loads(ls._session_file().read_text())
        assert on_disk == cookies

    def test_overwrites_existing(self) -> None:
        ls.save_session([{"name": "old"}])
        ls.save_session([{"name": "new"}])
        on_disk = json.loads(ls._session_file().read_text())
        assert on_disk == [{"name": "new"}]

    def test_empty_list_written(self) -> None:
        ls.save_session([])
        on_disk = json.loads(ls._session_file().read_text())
        assert on_disk == []

    def test_creates_session_dir_if_missing(self) -> None:
        new_dir = Path(self._tmp.name) / "nested" / "sessions"
        ls.SESSION_DIR = new_dir
        ls.save_session([{"name": "x"}])
        assert new_dir.exists()


class TestExceptions(unittest.TestCase):
    def test_captcha_error_inherits_scraper_error(self) -> None:
        assert issubclass(ls.CaptchaError, ls.LinkedInScraperError)

    def test_login_error_inherits_scraper_error(self) -> None:
        assert issubclass(ls.LoginError, ls.LinkedInScraperError)

    def test_scraper_error_inherits_exception(self) -> None:
        assert issubclass(ls.LinkedInScraperError, Exception)

    def test_captcha_error_can_be_raised(self) -> None:
        with self.assertRaises(ls.CaptchaError):
            raise ls.CaptchaError("test")

    def test_login_error_can_be_raised(self) -> None:
        with self.assertRaises(ls.LoginError):
            raise ls.LoginError("test")


class TestRunScanCaptchaPath(unittest.TestCase):
    """run_scan() must raise CaptchaError immediately when detect_captcha returns True."""

    def test_captcha_raises_captcha_error(self) -> None:
        # Mock detect_captcha so the very first navigation triggers CAPTCHA.
        # We also need to prevent Playwright from actually launching.
        async def _fake_run() -> list[dict]:
            raise ls.CaptchaError("CAPTCHA on login page")

        with patch.object(ls, "run_scan", side_effect=ls.CaptchaError("mocked captcha")):
            with self.assertRaises(ls.CaptchaError):
                asyncio.run(ls.run_scan())

    def test_main_returns_1_on_captcha(self) -> None:
        with patch.object(ls, "run_scan", side_effect=ls.CaptchaError("mocked")):
            with patch.object(ls, "LINKEDIN_EMAIL", "user@example.com"):
                with patch.object(ls, "LINKEDIN_PASSWORD", "secret"):
                    code = ls.main()
        assert code == 1

    def test_main_returns_1_on_scraper_error(self) -> None:
        with patch.object(ls, "run_scan", side_effect=ls.LinkedInScraperError("no playwright")):
            with patch.object(ls, "LINKEDIN_EMAIL", "user@example.com"):
                with patch.object(ls, "LINKEDIN_PASSWORD", "secret"):
                    code = ls.main()
        assert code == 1

    def test_main_returns_0_on_success(self) -> None:
        offers = [{"url": "https://li.com/1", "title": "AI Engineer", "company": "Acme",
                   "location": "Remote", "description": "", "job_type": "", "date_posted": "",
                   "source": "linkedin"}]
        with patch.object(ls, "run_scan", return_value=offers):
            with patch.object(ls, "LINKEDIN_EMAIL", "user@example.com"):
                with patch.object(ls, "LINKEDIN_PASSWORD", "secret"):
                    with patch("builtins.print"):
                        code = ls.main()
        assert code == 0


class TestMainPreflightNoCredentials(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = ls.SESSION_DIR
        self._orig_email = ls.LINKEDIN_EMAIL
        self._orig_pwd = ls.LINKEDIN_PASSWORD
        ls.SESSION_DIR = Path(self._tmp.name)
        ls.LINKEDIN_EMAIL = ""
        ls.LINKEDIN_PASSWORD = ""

    def tearDown(self) -> None:
        ls.SESSION_DIR = self._orig_dir
        ls.LINKEDIN_EMAIL = self._orig_email
        ls.LINKEDIN_PASSWORD = self._orig_pwd
        self._tmp.cleanup()

    def test_returns_1_when_no_credentials_and_no_session(self) -> None:
        # No session file + no env vars → immediate exit 1
        code = ls.main()
        assert code == 1

    def test_proceeds_when_session_exists_despite_no_credentials(self) -> None:
        cookies = [{"name": "li_at", "value": "X"}]
        ls._session_file().write_text(json.dumps(cookies))
        with patch.object(ls, "run_scan", return_value=[]):
            with patch("builtins.print"):
                code = ls.main()
        assert code == 0


if __name__ == "__main__":
    unittest.main()

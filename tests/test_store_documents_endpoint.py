"""
Unit tests for POST /store-documents endpoint in hunter_bridge.py.

DriveUploader is fully mocked — no real Drive or gspread calls.
prepend_yaml_header and build_telegram_notification are pure Python, run as-is.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import hunter_bridge as hb
import drive_uploader as du

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI_TEST = True
except ImportError:
    _HAS_FASTAPI_TEST = False


@unittest.skipUnless(_HAS_FASTAPI_TEST, "fastapi[testclient] not installed")
class TestStoreDocumentsEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi.testclient import TestClient
        hb._sd_rate_limiter.reset()
        self.client = TestClient(hb.app)
        self.mock_uploader = MagicMock()
        self.mock_uploader.upload_document.side_effect = [
            "https://drive.google.com/cv_url",
            "https://drive.google.com/lm_url",
        ]
        self.mock_uploader.update_matches.return_value = True
        self._patcher = patch("hunter_bridge.drive_uploader.DriveUploader", return_value=self.mock_uploader)
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def _valid_payload(self, **overrides) -> dict:
        base = {
            "job_id": "job_abc123",
            "company": "AcmeCorp",
            "position": "Senior AI Engineer",
            "offer_url": "https://linkedin.com/jobs/123",
            "detection_date": "2026-06-10",
            "match_rate": 0.87,
            "language": "en",
            "cv_markdown": "# Patricia Wintrebert\n\nBuilt LLM pipelines.",
            "lm_markdown": "Dear Hiring Manager,\n\nI built LLM agents.",
            "application_type": "form",
        }
        base.update(overrides)
        return base

    def _reset_upload_side_effect(self) -> None:
        self.mock_uploader.upload_document.side_effect = [
            "https://drive.google.com/cv_url",
            "https://drive.google.com/lm_url",
        ]

    # ── Happy path ────────────────────────────────────────────────────────────

    def test_happy_path_form(self) -> None:
        r = self.client.post("/store-documents", json=self._valid_payload(application_type="form"))
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_happy_path_easy_apply(self) -> None:
        r = self.client.post("/store-documents", json=self._valid_payload(application_type="easy_apply"))
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_response_shape(self) -> None:
        r = self.client.post("/store-documents", json=self._valid_payload())
        body = r.json()
        for key in ("ok", "cv_drive_url", "lm_drive_url", "telegram_message"):
            assert key in body, f"Missing key: {key}"

    def test_telegram_message_shape(self) -> None:
        r = self.client.post("/store-documents", json=self._valid_payload())
        msg = r.json()["telegram_message"]
        assert "text" in msg
        assert "reply_markup" in msg

    def test_cv_drive_url_in_response(self) -> None:
        r = self.client.post("/store-documents", json=self._valid_payload())
        assert r.json()["cv_drive_url"] == "https://drive.google.com/cv_url"

    def test_lm_drive_url_in_response(self) -> None:
        r = self.client.post("/store-documents", json=self._valid_payload())
        assert r.json()["lm_drive_url"] == "https://drive.google.com/lm_url"

    # ── Telegram message content ──────────────────────────────────────────────

    def test_telegram_text_contains_company(self) -> None:
        r = self.client.post("/store-documents", json=self._valid_payload(company="MegaCorp"))
        assert "MegaCorp" in r.json()["telegram_message"]["text"]

    def test_telegram_text_contains_position(self) -> None:
        r = self.client.post("/store-documents", json=self._valid_payload(position="ML Lead"))
        assert "ML Lead" in r.json()["telegram_message"]["text"]

    def test_telegram_text_contains_cv_url(self) -> None:
        r = self.client.post("/store-documents", json=self._valid_payload())
        assert "https://drive.google.com/cv_url" in r.json()["telegram_message"]["text"]

    def test_telegram_text_contains_lm_url(self) -> None:
        r = self.client.post("/store-documents", json=self._valid_payload())
        assert "https://drive.google.com/lm_url" in r.json()["telegram_message"]["text"]

    def test_easy_apply_form_questions_count_shown(self) -> None:
        r = self.client.post(
            "/store-documents",
            json=self._valid_payload(application_type="form", form_questions_count=3),
        )
        assert "3" in r.json()["telegram_message"]["text"]

    def test_zero_form_questions_not_shown_for_easy_apply(self) -> None:
        r = self.client.post(
            "/store-documents",
            json=self._valid_payload(application_type="easy_apply", form_questions_count=0),
        )
        assert "0 question" not in r.json()["telegram_message"]["text"]

    # ── Drive upload calls ────────────────────────────────────────────────────

    def test_upload_called_twice(self) -> None:
        self.client.post("/store-documents", json=self._valid_payload())
        assert self.mock_uploader.upload_document.call_count == 2

    def test_update_matches_called(self) -> None:
        self.client.post("/store-documents", json=self._valid_payload(job_id="job_xyz"))
        self.mock_uploader.update_matches.assert_called_once()
        call_args = self.mock_uploader.update_matches.call_args[0]
        assert call_args[0] == "job_xyz"

    def test_update_matches_receives_drive_urls(self) -> None:
        self.client.post("/store-documents", json=self._valid_payload())
        call_args = self.mock_uploader.update_matches.call_args[0]
        assert call_args[1] == "https://drive.google.com/cv_url"
        assert call_args[2] == "https://drive.google.com/lm_url"

    def test_year_month_derived_from_detection_date(self) -> None:
        self.client.post(
            "/store-documents",
            json=self._valid_payload(detection_date="2026-09-15"),
        )
        calls = self.mock_uploader.upload_document.call_args_list
        for c in calls:
            assert c[0][2] == "2026-09"

    def test_update_matches_failure_does_not_fail_request(self) -> None:
        self.mock_uploader.update_matches.return_value = False
        r = self.client.post("/store-documents", json=self._valid_payload())
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_upload_exception_returns_error(self) -> None:
        self.mock_uploader.upload_document.side_effect = Exception("Drive error")
        r = self.client.post("/store-documents", json=self._valid_payload())
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "Drive error" in body["error"]

    # ── YAML header injection ─────────────────────────────────────────────────

    def test_yaml_header_prepended_to_cv(self) -> None:
        payload = self._valid_payload(company="TestCorp", position="Dev")
        self.client.post("/store-documents", json=payload)
        cv_call = self.mock_uploader.upload_document.call_args_list[0]
        cv_content = cv_call[0][0]
        assert "---" in cv_content
        assert "TestCorp" in cv_content

    def test_yaml_header_prepended_to_lm(self) -> None:
        payload = self._valid_payload(company="TestCorp")
        self.client.post("/store-documents", json=payload)
        lm_call = self.mock_uploader.upload_document.call_args_list[1]
        lm_content = lm_call[0][0]
        assert "---" in lm_content

    # ── Validation errors ─────────────────────────────────────────────────────

    def test_missing_job_id_422(self) -> None:
        payload = self._valid_payload()
        del payload["job_id"]
        r = self.client.post("/store-documents", json=payload)
        assert r.status_code == 422

    def test_missing_cv_markdown_422(self) -> None:
        payload = self._valid_payload()
        del payload["cv_markdown"]
        r = self.client.post("/store-documents", json=payload)
        assert r.status_code == 422

    def test_missing_lm_markdown_422(self) -> None:
        payload = self._valid_payload()
        del payload["lm_markdown"]
        r = self.client.post("/store-documents", json=payload)
        assert r.status_code == 422

    def test_missing_company_422(self) -> None:
        payload = self._valid_payload()
        del payload["company"]
        r = self.client.post("/store-documents", json=payload)
        assert r.status_code == 422

    def test_missing_position_422(self) -> None:
        payload = self._valid_payload()
        del payload["position"]
        r = self.client.post("/store-documents", json=payload)
        assert r.status_code == 422

    def test_form_questions_count_defaults_to_zero(self) -> None:
        payload = self._valid_payload()
        payload.pop("form_questions_count", None)
        r = self.client.post("/store-documents", json=payload)
        assert r.status_code == 200

    # ── Rate limiting ─────────────────────────────────────────────────────────

    def test_rate_limit_429(self) -> None:
        for _ in range(hb._SD_RATE_LIMIT_MAX):
            self._reset_upload_side_effect()
            self.client.post("/store-documents", json=self._valid_payload())
        r = self.client.post("/store-documents", json=self._valid_payload())
        assert r.status_code == 429


if __name__ == "__main__":
    unittest.main()

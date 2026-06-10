"""
Unit tests for hunter_server.py — no live API calls, no subprocess.
All external calls are mocked.
"""
from __future__ import annotations

import importlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the repo root is importable regardless of working directory.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import hunter_server as hs


# ── extract_inner ─────────────────────────────────────────────────────────────


class TestExtractInner(unittest.TestCase):
    def test_final_assistant_visible_text(self) -> None:
        payload = json.dumps({"result": {"finalAssistantVisibleText": "hello"}})
        assert hs.extract_inner(payload) == "hello"

    def test_payloads_path(self) -> None:
        payload = json.dumps(
            {"result": {"payloads": [{"text": "part1"}, {"text": "part2"}]}}
        )
        assert hs.extract_inner(payload) == "part1\npart2"

    def test_fallback_keys(self) -> None:
        for key in ("response", "content", "message", "text", "output"):
            payload = json.dumps({key: "value"})
            assert hs.extract_inner(payload) == "value", f"failed for key={key}"

    def test_invalid_json_returns_raw(self) -> None:
        raw = "not-json"
        assert hs.extract_inner(raw) == raw

    def test_empty_payloads_falls_through(self) -> None:
        payload = json.dumps({"result": {"payloads": []}})
        # Falls through to raw stdout since no other key matches.
        result = hs.extract_inner(payload)
        assert result == payload


# ── build_message ─────────────────────────────────────────────────────────────


class TestBuildMessage(unittest.TestCase):
    def test_includes_skill_and_brief(self) -> None:
        msg = hs.build_message("offer-analysis", {"title": "AI Eng"})
        assert "OFFER-ANALYSIS SKILL" in msg
        assert "AI Eng" in msg

    def test_brief_is_json_encoded(self) -> None:
        brief = {"a": 1, "b": "two"}
        msg = hs.build_message("cv-rewriter", brief)
        # The JSON dump of the brief should be present verbatim.
        assert json.dumps(brief, ensure_ascii=False, indent=2) in msg


# ── HTTP routing ──────────────────────────────────────────────────────────────


def _make_request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Invoke Handler via a fake HTTP socket and return (status_code, response_body)."""
    body_bytes = json.dumps(body or {}).encode() if body is not None else b""
    env = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(body_bytes)),
        "CONTENT_TYPE": "application/json",
    }
    # Build a minimal fake request file-like object.
    request_file = io.BytesIO(body_bytes)
    response_chunks: list[bytes] = []

    class FakeSocket:
        def makefile(self, *args, **kwargs):
            return io.BytesIO(
                f"{method} {path} HTTP/1.1\r\nContent-Length: {len(body_bytes)}\r\n\r\n".encode()
                + body_bytes
            )

        def sendall(self, data: bytes) -> None:
            response_chunks.append(data)

    # We patch call_hunter so no subprocess is launched.
    fake_response = json.dumps({"result": {"finalAssistantVisibleText": "ok-response"}})
    with patch.object(hs, "call_hunter", return_value=fake_response):
        handler = hs.Handler.__new__(hs.Handler)
        handler.rfile = request_file
        handler.headers = {"Content-Length": str(len(body_bytes))}
        handler.path = path

        captured: dict = {}

        def fake_send(code: int, obj: dict) -> None:
            captured["code"] = code
            captured["body"] = obj

        handler._send = fake_send

        if method == "GET":
            handler.do_GET()
        elif method == "POST":
            handler.do_POST()

    return captured.get("code", 0), captured.get("body", {})


class TestGetHealth(unittest.TestCase):
    def test_health_returns_200(self) -> None:
        code, body = _make_request("GET", "/health")
        assert code == 200
        assert body["status"] == "ok"
        assert body["service"] == "hunter-bridge"
        assert body["agent"] == "the-hunter"

    def test_unknown_get_returns_404(self) -> None:
        code, body = _make_request("GET", "/unknown")
        assert code == 404
        assert "error" in body


class TestPostRouting(unittest.TestCase):
    def test_analyze_route(self) -> None:
        code, body = _make_request("POST", "/analyze", {"title": "AI Eng"})
        assert code == 200
        assert body["ok"] is True
        assert body["skill"] == "offer-analysis"

    def test_rewrite_cv_route(self) -> None:
        code, body = _make_request("POST", "/rewrite-cv", {})
        assert code == 200
        assert body["skill"] == "cv-rewriter"

    def test_cover_letter_route(self) -> None:
        code, body = _make_request("POST", "/cover-letter", {})
        assert code == 200
        assert body["skill"] == "cover-letter-writer"

    def test_form_answers_route(self) -> None:
        code, body = _make_request("POST", "/form-answers", {})
        assert code == 200
        assert body["skill"] == "form-answerer"

    def test_report_route(self) -> None:
        code, body = _make_request("POST", "/report", {})
        assert code == 200
        assert body["skill"] == "weekly-reporter"

    def test_unknown_post_returns_404(self) -> None:
        code, body = _make_request("POST", "/not-a-route", {})
        assert code == 404

    def test_exception_returns_200_with_ok_false(self) -> None:
        """do_POST must never return 5xx — it wraps exceptions in ok:false."""
        with patch.object(hs, "call_hunter", side_effect=RuntimeError("boom")):
            handler = hs.Handler.__new__(hs.Handler)
            handler.rfile = io.BytesIO(b"{}")
            handler.headers = {"Content-Length": "2"}
            handler.path = "/analyze"
            captured: dict = {}

            def fake_send(code: int, obj: dict) -> None:
                captured["code"] = code
                captured["body"] = obj

            handler._send = fake_send
            handler.do_POST()

        assert captured["code"] == 200
        assert captured["body"]["ok"] is False
        assert "boom" in captured["body"]["error"]


# ── SKILL.md frontmatter validation ──────────────────────────────────────────


SKILLS_DIR = REPO_ROOT / "workspace" / "skills"
EXPECTED_SKILLS = [
    "offer-analysis",
    "cv-rewriter",
    "cover-letter-writer",
    "form-answerer",
    "weekly-reporter",
]


class TestSkillMdFrontmatter(unittest.TestCase):
    """Verify all SKILL.md files exist and have required 'description:' frontmatter.
    OpenClaw silently drops skills without this field.
    """

    def _skill_path(self, skill: str) -> Path:
        return SKILLS_DIR / skill / "SKILL.md"

    def test_all_skill_files_exist(self) -> None:
        for skill in EXPECTED_SKILLS:
            with self.subTest(skill=skill):
                path = self._skill_path(skill)
                assert path.exists(), f"Missing: {path}"

    def test_all_skills_have_description_frontmatter(self) -> None:
        for skill in EXPECTED_SKILLS:
            path = self._skill_path(skill)
            if not path.exists():
                self.skipTest(f"{path} missing — covered by test_all_skill_files_exist")
            content = path.read_text()
            with self.subTest(skill=skill):
                assert content.startswith("---"), f"{skill}/SKILL.md: must start with YAML frontmatter '---'"
                assert "description:" in content.split("---")[1], (
                    f"{skill}/SKILL.md: missing 'description:' in frontmatter "
                    f"(OpenClaw drops skills without it)"
                )

    def test_all_skills_have_name_frontmatter(self) -> None:
        for skill in EXPECTED_SKILLS:
            path = self._skill_path(skill)
            if not path.exists():
                self.skipTest(f"{path} missing")
            content = path.read_text()
            with self.subTest(skill=skill):
                assert "name:" in content.split("---")[1], (
                    f"{skill}/SKILL.md: missing 'name:' in frontmatter"
                )


# ── _handle_read_pending_matches ──────────────────────────────────────────────


class TestHandleReadPendingMatches(unittest.TestCase):
    def _mock_gc(self, rows):
        mock_gc = MagicMock()
        mock_ss = mock_gc.open.return_value
        mock_sheet = mock_ss.worksheet.return_value
        mock_sheet.get_all_values.return_value = rows
        return mock_gc

    @patch("gspread.service_account")
    def test_happy_path_two_offers(self, mock_sa):
        rows = [
            ["job1", "10.06.2026", "Dev Python", "Corp A", "Paris", "https://ex.com/1", "80.0", "python, llm", "indeed", "1"],
            ["job2", "10.06.2026", "Data Eng", "Corp B", "Lyon", "https://ex.com/2", "65.0", "sql", "linkedin", "2"],
        ]
        mock_sa.return_value = self._mock_gc(rows)
        result = hs._handle_read_pending_matches()
        assert result["ok"] is True
        assert result["count"] == 2
        assert len(result["offers"]) == 2
        assert result["snoozed_count"] == 0

    @patch("gspread.service_account")
    def test_snooze_future_excluded(self, mock_sa):
        rows = [
            ["job1", "10.06.2026", "Dev Python", "Corp A", "Paris", "https://ex.com/1", "80.0", "python", "indeed", "1", "01.01.2099"],
        ]
        mock_sa.return_value = self._mock_gc(rows)
        result = hs._handle_read_pending_matches()
        assert result["count"] == 0
        assert result["snoozed_count"] == 1

    @patch("gspread.service_account")
    def test_snooze_past_included(self, mock_sa):
        rows = [
            ["job1", "10.06.2026", "Dev Python", "Corp A", "Paris", "https://ex.com/1", "80.0", "python", "indeed", "1", "01.01.2020"],
        ]
        mock_sa.return_value = self._mock_gc(rows)
        result = hs._handle_read_pending_matches()
        assert result["count"] == 1
        assert result["snoozed_count"] == 0

    @patch("gspread.service_account")
    def test_empty_sheet(self, mock_sa):
        mock_sa.return_value = self._mock_gc([])
        result = hs._handle_read_pending_matches()
        assert result["ok"] is True
        assert result["count"] == 0
        assert result["offers"] == []

    @patch("gspread.service_account")
    def test_header_row_skipped(self, mock_sa):
        rows = [
            ["job_id", "date_scanned", "title", "company", "location", "url", "match_rate", "skills_found", "source", "rank"],
            ["job1", "10.06.2026", "Dev Python", "Corp A", "Paris", "https://ex.com/1", "80.0", "python", "indeed", "1"],
        ]
        mock_sa.return_value = self._mock_gc(rows)
        result = hs._handle_read_pending_matches()
        assert result["count"] == 1

    @patch("gspread.service_account")
    def test_route_get(self, mock_sa):
        mock_sa.return_value = self._mock_gc([])
        code, body = _make_request("GET", "/sheets/pending-matches")
        assert code == 200
        assert body["ok"] is True


# ── _handle_update_status ─────────────────────────────────────────────────────


class TestHandleUpdateStatus(unittest.TestCase):
    def _mock_gc_with_cell(self, found_row=None):
        mock_gc = MagicMock()
        mock_ss = mock_gc.open.return_value
        mock_sheet = mock_ss.worksheet.return_value
        if found_row is not None:
            mock_cell = MagicMock()
            mock_cell.row = found_row
            mock_sheet.find.return_value = mock_cell
        else:
            mock_sheet.find.return_value = None
        return mock_gc, mock_sheet

    @patch("gspread.service_account")
    def test_happy_path(self, mock_sa):
        mock_gc, mock_sheet = self._mock_gc_with_cell(found_row=3)
        mock_sa.return_value = mock_gc
        result = hs._handle_update_status({"job_id": "abc123", "status": "ignored"})
        assert result["ok"] is True
        assert result["updated"] is True
        mock_sheet.update_cell.assert_called_once_with(3, 11, "ignored")

    @patch("gspread.service_account")
    def test_job_id_not_found(self, mock_sa):
        mock_gc, _ = self._mock_gc_with_cell(found_row=None)
        mock_sa.return_value = mock_gc
        result = hs._handle_update_status({"job_id": "notexist", "status": "ignored"})
        assert result["ok"] is True
        assert result["updated"] is False

    def test_missing_job_id(self):
        result = hs._handle_update_status({"status": "ignored"})
        assert result["ok"] is False

    def test_missing_status(self):
        result = hs._handle_update_status({"job_id": "abc123"})
        assert result["ok"] is False

    @patch("gspread.service_account")
    def test_route_post(self, mock_sa):
        mock_gc, _ = self._mock_gc_with_cell(found_row=3)
        mock_sa.return_value = mock_gc
        code, body = _make_request("POST", "/sheets/update-status", {"job_id": "abc", "status": "ignored"})
        assert code == 200
        assert body["ok"] is True


# ── _handle_snooze ────────────────────────────────────────────────────────────


class TestHandleSnooze(unittest.TestCase):
    def _mock_gc_with_cell(self, found_row=None):
        mock_gc = MagicMock()
        mock_ss = mock_gc.open.return_value
        mock_sheet = mock_ss.worksheet.return_value
        if found_row is not None:
            mock_cell = MagicMock()
            mock_cell.row = found_row
            mock_sheet.find.return_value = mock_cell
        else:
            mock_sheet.find.return_value = None
        return mock_gc, mock_sheet

    @patch("gspread.service_account")
    def test_explicit_snooze_until(self, mock_sa):
        mock_gc, mock_sheet = self._mock_gc_with_cell(found_row=5)
        mock_sa.return_value = mock_gc
        result = hs._handle_snooze({"job_id": "abc123", "snooze_until": "20.06.2026"})
        assert result["ok"] is True
        assert result["snoozed_until"] == "20.06.2026"
        mock_sheet.update_cell.assert_called_once_with(5, 11, "20.06.2026")

    @patch("gspread.service_account")
    def test_default_snooze_is_tomorrow(self, mock_sa):
        from datetime import datetime as _dt, timedelta
        mock_gc, _ = self._mock_gc_with_cell(found_row=5)
        mock_sa.return_value = mock_gc
        result = hs._handle_snooze({"job_id": "abc123"})
        assert result["ok"] is True
        expected = (_dt.now() + timedelta(days=1)).strftime("%d.%m.%Y")
        assert result["snoozed_until"] == expected

    @patch("gspread.service_account")
    def test_job_id_not_found(self, mock_sa):
        mock_gc, _ = self._mock_gc_with_cell(found_row=None)
        mock_sa.return_value = mock_gc
        result = hs._handle_snooze({"job_id": "notexist"})
        assert result["ok"] is False

    def test_missing_job_id(self):
        result = hs._handle_snooze({})
        assert result["ok"] is False

    @patch("gspread.service_account")
    def test_route_post(self, mock_sa):
        mock_gc, _ = self._mock_gc_with_cell(found_row=5)
        mock_sa.return_value = mock_gc
        code, body = _make_request("POST", "/sheets/snooze", {"job_id": "abc", "snooze_until": "20.06.2026"})
        assert code == 200
        assert body["ok"] is True


# ── _handle_generate ──────────────────────────────────────────────────────────


class TestHandleGenerate(unittest.TestCase):
    _MATCH_ROW = [
        "jobA", "10.06.2026", "ML Eng", "Corp X", "Paris", "yes",
        "https://ex.com/1", "linkedin", "80.0", "python, ml", "pending", "", "",
    ]

    def _mock_gc(self, found_row=None, row_values=None):
        mock_gc = MagicMock()
        mock_ss = mock_gc.open.return_value
        mock_sheet = mock_ss.worksheet.return_value
        if found_row is not None:
            mock_cell = MagicMock()
            mock_cell.row = found_row
            mock_sheet.find.return_value = mock_cell
            mock_sheet.row_values.return_value = row_values or []
        else:
            mock_sheet.find.return_value = None
        return mock_gc, mock_sheet

    @patch("pathlib.Path")
    @patch("gspread.service_account")
    @patch.object(hs, "call_hunter", return_value=json.dumps({"result": {"finalAssistantVisibleText": "Generated text"}}))
    def test_happy_path(self, mock_call, mock_sa, MockPath):
        mock_gc, mock_sheet = self._mock_gc(found_row=2, row_values=self._MATCH_ROW)
        mock_sa.return_value = mock_gc
        result = hs._handle_generate({"job_id": "jobA"})
        assert result["ok"] is True
        assert result["job_id"] == "jobA"
        assert "cv_path" in result
        assert "letter_path" in result
        assert mock_call.call_count == 2
        MockPath.return_value.mkdir.assert_called_once_with(parents=True, exist_ok=True)

    @patch("gspread.service_account")
    def test_job_id_not_found(self, mock_sa):
        mock_gc, _ = self._mock_gc(found_row=None)
        mock_sa.return_value = mock_gc
        result = hs._handle_generate({"job_id": "notexist"})
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_missing_job_id(self):
        result = hs._handle_generate({})
        assert result["ok"] is False

    @patch("pathlib.Path")
    @patch("gspread.service_account")
    @patch.object(hs, "call_hunter", side_effect=RuntimeError("LLM timeout"))
    def test_generate_failure_propagated(self, mock_call, mock_sa, MockPath):
        mock_gc, _ = self._mock_gc(found_row=2, row_values=self._MATCH_ROW)
        mock_sa.return_value = mock_gc
        result = hs._handle_generate({"job_id": "jobA"})
        assert result["ok"] is False

    @patch("pathlib.Path")
    @patch("gspread.service_account")
    @patch.object(hs, "call_hunter", return_value=json.dumps({"result": {"finalAssistantVisibleText": "Generated"}}))
    def test_route_post(self, mock_call, mock_sa, MockPath):
        mock_gc, _ = self._mock_gc(found_row=2, row_values=self._MATCH_ROW)
        mock_sa.return_value = mock_gc
        code, body = _make_request("POST", "/generate", {"job_id": "jobA"})
        assert code == 200
        assert body["ok"] is True


if __name__ == "__main__":
    unittest.main()

"""
Sprint 2 tests for hunter_server.py — /health endpoint, 429 retry, timeout retry.
No live API calls, no subprocess.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import hunter_server as hs

# Re-use the _make_request helper from test_hunter_server.py pattern
import io


def _make_request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Invoke Handler via fake socket, patching call_hunter and health_check."""
    body_bytes = json.dumps(body or {}).encode() if body is not None else b""
    request_file = io.BytesIO(body_bytes)
    fake_response = json.dumps({"result": {"finalAssistantVisibleText": "ok-response"}})

    _ALL_OK = [
        {"service": "n8n", "ok": True},
        {"service": "openclaw", "ok": True},
        {"service": "google_sheets", "ok": True},
        {"service": "hunter_bridge", "ok": True},
    ]
    mock_hc = MagicMock()
    mock_hc.check_n8n.return_value = {"service": "n8n", "ok": True}
    mock_hc.check_openclaw.return_value = {"service": "openclaw", "ok": True}
    mock_hc.check_google_sheets.return_value = {"service": "google_sheets", "ok": True}

    with patch.object(hs, "call_hunter", return_value=fake_response), \
         patch.dict("sys.modules", {"health_check": mock_hc}):
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


# ── /health endpoint ──────────────────────────────────────────────────────────


class TestHealthEndpointS2(unittest.TestCase):
    def _make_health_request(self, n8n_ok=True, openclaw_ok=True, sheets_ok=True):
        mock_hc = MagicMock()
        mock_hc.check_n8n.return_value = {"service": "n8n", "ok": n8n_ok}
        mock_hc.check_openclaw.return_value = {"service": "openclaw", "ok": openclaw_ok}
        mock_hc.check_google_sheets.return_value = {"service": "google_sheets", "ok": sheets_ok}

        with patch.dict("sys.modules", {"health_check": mock_hc}):
            handler = hs.Handler.__new__(hs.Handler)
            handler.path = "/health"
            captured: dict = {}

            def fake_send(code: int, obj: dict) -> None:
                captured["code"] = code
                captured["body"] = obj

            handler._send = fake_send
            handler.do_GET()

        return captured.get("code", 0), captured.get("body", {})

    def test_health_endpoint_all_services_ok(self) -> None:
        code, body = self._make_health_request(n8n_ok=True, openclaw_ok=True, sheets_ok=True)
        assert code == 200
        assert body["status"] == "ok"
        assert body["service"] == "hunter-bridge"
        assert "services" in body
        assert len(body["services"]) >= 3
        assert body["ok"] is True

    def test_health_endpoint_one_service_down(self) -> None:
        code, body = self._make_health_request(n8n_ok=False, openclaw_ok=True, sheets_ok=True)
        assert code == 200
        assert body["ok"] is False
        assert "services" in body

    def test_health_check_all_called_once(self) -> None:
        mock_hc = MagicMock()
        mock_hc.check_n8n.return_value = {"service": "n8n", "ok": True}
        mock_hc.check_openclaw.return_value = {"service": "openclaw", "ok": True}
        mock_hc.check_google_sheets.return_value = {"service": "google_sheets", "ok": True}

        with patch.dict("sys.modules", {"health_check": mock_hc}):
            handler = hs.Handler.__new__(hs.Handler)
            handler.path = "/health"
            handler._send = lambda code, obj: None
            handler.do_GET()

        mock_hc.check_n8n.assert_called_once()
        mock_hc.check_openclaw.assert_called_once()
        mock_hc.check_google_sheets.assert_called_once()

    def test_health_response_has_services_key(self) -> None:
        """New response must include services key (absent from Sprint 1 static response)."""
        code, body = self._make_health_request()
        assert "services" in body

    def test_health_hunter_bridge_always_ok_in_services(self) -> None:
        """hunter_bridge is always ok since we are responding."""
        code, body = self._make_health_request()
        bridge_checks = [s for s in body["services"] if s["service"] == "hunter_bridge"]
        assert len(bridge_checks) == 1
        assert bridge_checks[0]["ok"] is True


# ── 429 retry backoff ─────────────────────────────────────────────────────────


def _make_429_error():
    import gspread.exceptions
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    return gspread.exceptions.APIError(mock_resp)


def _make_api_error(status: int):
    import gspread.exceptions
    mock_resp = MagicMock()
    mock_resp.status_code = status
    return gspread.exceptions.APIError(mock_resp)


class TestSheetsRetry(unittest.TestCase):
    def _call_write(self, append_side_effect, sleep_mock=None):
        mock_sheet = MagicMock()
        mock_sheet.append_rows.side_effect = append_side_effect
        mock_ss = MagicMock()
        mock_ss.worksheet.return_value = mock_sheet

        body = {"offers": [{"url": "http://x.com/job/1", "title": "Dev", "company": "Co", "location": "Paris", "job_type": "full-time", "source": "indeed", "skills_found": ["python"], "match_rate": 0.8}]}

        import gspread as _gs
        with patch("builtins.__import__", wraps=__import__) as mock_import, \
             patch("time.sleep", sleep_mock or MagicMock()):
            with patch.dict("sys.modules"):
                mock_gc = MagicMock()
                mock_gc.open.return_value = mock_ss
                with patch("gspread.service_account", return_value=mock_gc), \
                     patch("deduplication.CREDS_PATH", "/fake/creds.json"), \
                     patch("deduplication.SPREADSHEET_NAME", "Sheet"), \
                     patch("deduplication.compute_hash", return_value="abc123"):
                    try:
                        result = hs._handle_write_scan_results(body)
                    except Exception as exc:
                        result = {"ok": False, "error": str(exc)}
        return result, mock_sheet

    def test_write_succeeds_after_one_429(self) -> None:
        err_429 = _make_429_error()
        side_effect = [err_429, None]
        with patch("gspread.service_account") as mock_sa, \
             patch("time.sleep"):
            mock_gc = MagicMock()
            mock_ss = MagicMock()
            mock_sheet = MagicMock()
            mock_sheet.append_rows.side_effect = side_effect + [None]
            mock_ss.worksheet.return_value = mock_sheet
            mock_gc.open.return_value = mock_ss
            mock_sa.return_value = mock_gc
            with patch("deduplication.CREDS_PATH", "/fake"), \
                 patch("deduplication.SPREADSHEET_NAME", "Sheet"), \
                 patch("deduplication.compute_hash", return_value="abc"):
                result = hs._handle_write_scan_results({"offers": [{"url": "u", "title": "t", "company": "c", "location": "l", "source": "s", "skills_found": [], "match_rate": 0.8}]})
        assert result["ok"] is True

    def test_write_fails_after_max_retries_returns_ok_false(self) -> None:
        err_429 = _make_429_error()
        with patch("gspread.service_account") as mock_sa, \
             patch("time.sleep"):
            mock_gc = MagicMock()
            mock_ss = MagicMock()
            mock_sheet = MagicMock()
            mock_sheet.append_rows.side_effect = err_429
            mock_ss.worksheet.return_value = mock_sheet
            mock_gc.open.return_value = mock_ss
            mock_sa.return_value = mock_gc
            with patch("deduplication.CREDS_PATH", "/fake"), \
                 patch("deduplication.SPREADSHEET_NAME", "Sheet"), \
                 patch("deduplication.compute_hash", return_value="abc"):
                try:
                    result = hs._handle_write_scan_results({"offers": [{"url": "u", "title": "t", "company": "c", "location": "l", "source": "s", "skills_found": [], "match_rate": 0.8}]})
                except Exception:
                    result = {"ok": False}
        assert result["ok"] is False

    def test_write_429_sleep_increases(self) -> None:
        err_429 = _make_429_error()
        sleep_calls: list[float] = []
        success_count = [0]

        def fake_append(*args, **kwargs):
            if success_count[0] < 2:
                success_count[0] += 1
                raise err_429

        with patch("gspread.service_account") as mock_sa, \
             patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            mock_gc = MagicMock()
            mock_ss = MagicMock()
            mock_sheet = MagicMock()
            mock_sheet.append_rows.side_effect = fake_append
            mock_ss.worksheet.return_value = mock_sheet
            mock_gc.open.return_value = mock_ss
            mock_sa.return_value = mock_gc
            with patch("deduplication.CREDS_PATH", "/fake"), \
                 patch("deduplication.SPREADSHEET_NAME", "Sheet"), \
                 patch("deduplication.compute_hash", return_value="abc"):
                hs._handle_write_scan_results({"offers": [{"url": "u", "title": "t", "company": "c", "location": "l", "source": "s", "skills_found": [], "match_rate": 0.8}]})
        # At least 2 retries happened — each delay must be >= previous
        assert len(sleep_calls) >= 2
        for i in range(1, len(sleep_calls)):
            assert sleep_calls[i] >= sleep_calls[i - 1]

    def test_non_429_api_error_not_retried(self) -> None:
        err_500 = _make_api_error(500)
        call_count = [0]

        def fake_append(*args, **kwargs):
            call_count[0] += 1
            raise err_500

        with patch("gspread.service_account") as mock_sa, \
             patch("time.sleep") as mock_sleep:
            mock_gc = MagicMock()
            mock_ss = MagicMock()
            mock_sheet = MagicMock()
            mock_sheet.append_rows.side_effect = fake_append
            mock_ss.worksheet.return_value = mock_sheet
            mock_gc.open.return_value = mock_ss
            mock_sa.return_value = mock_gc
            with patch("deduplication.CREDS_PATH", "/fake"), \
                 patch("deduplication.SPREADSHEET_NAME", "Sheet"), \
                 patch("deduplication.compute_hash", return_value="abc"):
                try:
                    hs._handle_write_scan_results({"offers": [{"url": "u", "title": "t", "company": "c", "location": "l", "source": "s", "skills_found": [], "match_rate": 0.8}]})
                except Exception:
                    pass
        # append_rows called exactly once — no retry on non-429
        assert call_count[0] == 1
        mock_sleep.assert_not_called()

    def test_write_zero_offers_no_sheets_call(self) -> None:
        with patch("gspread.service_account") as mock_sa:
            result = hs._handle_write_scan_results({"offers": []})
        mock_sa.assert_not_called()
        assert result["ok"] is True
        assert result["written_count"] == 0


# ── Hunter Bridge timeout retry ───────────────────────────────────────────────


class TestCallHunterRetry(unittest.TestCase):
    def test_succeeds_after_one_timeout(self) -> None:
        import subprocess
        call_count = [0]

        def fake_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 600))
            r = MagicMock()
            r.stdout = '{"result": {"finalAssistantVisibleText": "ok"}}'
            return r

        with patch("subprocess.run", side_effect=fake_run):
            result = hs.call_hunter("test message", "test-tag", timeout=10)
        assert call_count[0] == 2
        assert "ok" in result

    def test_fails_after_two_timeouts(self) -> None:
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["openclaw"], 10)):
            with self.assertRaises(subprocess.TimeoutExpired):
                hs.call_hunter("test message", "test-tag", timeout=10)

    def test_non_timeout_exception_not_retried(self) -> None:
        call_count = [0]

        def fake_run(*args, **kwargs):
            call_count[0] += 1
            raise FileNotFoundError("openclaw not found")

        with patch("subprocess.run", side_effect=fake_run):
            with self.assertRaises(FileNotFoundError):
                hs.call_hunter("test message", "test-tag", timeout=10)
        # Must not retry on non-timeout errors
        assert call_count[0] == 1

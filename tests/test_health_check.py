"""
Unit tests for health_check.py — no live network calls, no filesystem access.
All external calls are mocked.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import health_check as hc


class TestCheckN8n(unittest.TestCase):
    def test_n8n_reachable_returns_ok_true(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("health_check.requests.get", return_value=mock_resp) as mock_get:
            result = hc.check_n8n()
        assert result["service"] == "n8n"
        assert result["ok"] is True
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        assert "timeout" in kwargs

    def test_n8n_connection_error_returns_ok_false(self) -> None:
        with patch("health_check.requests.get", side_effect=ConnectionError("refused")):
            result = hc.check_n8n()
        assert result["service"] == "n8n"
        assert result["ok"] is False
        assert "error" in result

    def test_n8n_non_200_returns_ok_false(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("health_check.requests.get", return_value=mock_resp):
            result = hc.check_n8n()
        assert result["ok"] is False
        assert result["status_code"] == 503


class TestCheckHunterBridge(unittest.TestCase):
    def test_hunter_bridge_reachable_returns_ok_true(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("health_check.requests.get", return_value=mock_resp) as mock_get:
            result = hc.check_hunter_bridge()
        assert result["service"] == "hunter_bridge"
        assert result["ok"] is True
        _, kwargs = mock_get.call_args
        assert "timeout" in kwargs

    def test_hunter_bridge_timeout_returns_ok_false(self) -> None:
        import requests as _req
        with patch("health_check.requests.get", side_effect=_req.exceptions.Timeout):
            result = hc.check_hunter_bridge()
        assert result["service"] == "hunter_bridge"
        assert result["ok"] is False
        assert "error" in result


class TestCheckOpenclaw(unittest.TestCase):
    def test_openclaw_reachable_returns_ok_true(self) -> None:
        with patch("health_check.shutil.which", return_value="/usr/bin/openclaw"), \
             patch("health_check.Path.exists", return_value=True):
            result = hc.check_openclaw()
        assert result["service"] == "openclaw"
        assert result["ok"] is True

    def test_openclaw_binary_missing_returns_ok_false(self) -> None:
        with patch("health_check.shutil.which", return_value=None):
            result = hc.check_openclaw()
        assert result["service"] == "openclaw"
        assert result["ok"] is False
        assert "binary not found" in result["error"]

    def test_openclaw_config_missing_returns_ok_false(self) -> None:
        with patch("health_check.shutil.which", return_value="/usr/bin/openclaw"), \
             patch("health_check.Path.exists", return_value=False):
            result = hc.check_openclaw()
        assert result["service"] == "openclaw"
        assert result["ok"] is False
        assert "config not found" in result["error"]


class TestCheckGoogleSheets(unittest.TestCase):
    def _mock_dedup(self):
        mock_module = MagicMock()
        mock_module.CREDS_PATH = "/fake/creds.json"
        mock_module.SPREADSHEET_NAME = "TestSheet"
        return mock_module

    def test_google_sheets_ok(self) -> None:
        mock_gc = MagicMock()
        with patch.dict("sys.modules", {"deduplication": self._mock_dedup()}), \
             patch("health_check.gspread.service_account", return_value=mock_gc):
            result = hc.check_google_sheets()
        assert result["service"] == "google_sheets"
        assert result["ok"] is True
        mock_gc.open.assert_called_once_with("TestSheet")

    def test_google_sheets_api_error_returns_ok_false(self) -> None:
        import gspread
        api_err = gspread.exceptions.APIError(MagicMock(status_code=403))
        with patch.dict("sys.modules", {"deduplication": self._mock_dedup()}), \
             patch("health_check.gspread.service_account", side_effect=api_err):
            result = hc.check_google_sheets()
        assert result["service"] == "google_sheets"
        assert result["ok"] is False
        assert "error" in result

    def test_google_sheets_missing_creds_file_returns_ok_false(self) -> None:
        with patch.dict("sys.modules", {"deduplication": self._mock_dedup()}), \
             patch("health_check.gspread.service_account", side_effect=FileNotFoundError("no file")):
            result = hc.check_google_sheets()
        assert result["service"] == "google_sheets"
        assert result["ok"] is False
        assert "credentials file missing" in result["error"]


class TestCheckAll(unittest.TestCase):
    def test_check_all_returns_four_entries(self) -> None:
        with patch.object(hc, "check_n8n", return_value={"service": "n8n", "ok": True}), \
             patch.object(hc, "check_hunter_bridge", return_value={"service": "hunter_bridge", "ok": True}), \
             patch.object(hc, "check_openclaw", return_value={"service": "openclaw", "ok": True}), \
             patch.object(hc, "check_google_sheets", return_value={"service": "google_sheets", "ok": True}):
            results = hc.check_all()
        assert len(results) == 4

    def test_check_all_overall_ok_when_all_pass(self) -> None:
        with patch.object(hc, "check_n8n", return_value={"service": "n8n", "ok": True}), \
             patch.object(hc, "check_hunter_bridge", return_value={"service": "hunter_bridge", "ok": True}), \
             patch.object(hc, "check_openclaw", return_value={"service": "openclaw", "ok": True}), \
             patch.object(hc, "check_google_sheets", return_value={"service": "google_sheets", "ok": True}):
            results = hc.check_all()
        assert all(r["ok"] for r in results)

    def test_check_all_one_fails(self) -> None:
        with patch.object(hc, "check_n8n", return_value={"service": "n8n", "ok": False, "error": "down"}), \
             patch.object(hc, "check_hunter_bridge", return_value={"service": "hunter_bridge", "ok": True}), \
             patch.object(hc, "check_openclaw", return_value={"service": "openclaw", "ok": True}), \
             patch.object(hc, "check_google_sheets", return_value={"service": "google_sheets", "ok": True}):
            results = hc.check_all()
        assert not all(r["ok"] for r in results)
        assert any(r["service"] == "n8n" and not r["ok"] for r in results)

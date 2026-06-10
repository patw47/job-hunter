from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import requests
import gspread

logger = logging.getLogger(__name__)

N8N_HEALTH_URL = os.environ.get("N8N_HEALTH_URL", "http://127.0.0.1:5678/healthz")
HUNTER_BRIDGE_PORT = os.environ.get("HUNTER_BRIDGE_PORT", "18798")
HUNTER_BRIDGE_URL = f"http://127.0.0.1:{HUNTER_BRIDGE_PORT}/health"
OPENCLAW_CONFIG = os.environ.get("OPENCLAW_CONFIG_PATH", "/home/thehunter/.openclaw/openclaw.json")
HEALTH_TIMEOUT = 5


def check_n8n() -> dict:
    """Check if n8n is reachable via its HTTP health endpoint."""
    try:
        resp = requests.get(N8N_HEALTH_URL, timeout=HEALTH_TIMEOUT)
        return {"service": "n8n", "ok": resp.status_code == 200, "status_code": resp.status_code}
    except Exception as exc:
        return {"service": "n8n", "ok": False, "error": str(exc)}


def check_hunter_bridge() -> dict:
    """Check if Hunter Bridge HTTP server is reachable."""
    try:
        resp = requests.get(HUNTER_BRIDGE_URL, timeout=HEALTH_TIMEOUT)
        return {"service": "hunter_bridge", "ok": resp.status_code == 200, "status_code": resp.status_code}
    except Exception as exc:
        return {"service": "hunter_bridge", "ok": False, "error": str(exc)}


def check_openclaw() -> dict:
    """Check if openclaw binary and config file are present."""
    binary = shutil.which("openclaw")
    if not binary:
        return {"service": "openclaw", "ok": False, "error": "openclaw binary not found in PATH"}
    if not Path(OPENCLAW_CONFIG).exists():
        return {"service": "openclaw", "ok": False, "error": f"config not found: {OPENCLAW_CONFIG}"}
    return {"service": "openclaw", "ok": True}


def check_google_sheets() -> dict:
    """Check Google Sheets connectivity by opening the spreadsheet."""
    try:
        from deduplication import CREDS_PATH, SPREADSHEET_NAME
        gc = gspread.service_account(filename=CREDS_PATH)
        gc.open(SPREADSHEET_NAME)
        return {"service": "google_sheets", "ok": True}
    except FileNotFoundError as exc:
        return {"service": "google_sheets", "ok": False, "error": f"credentials file missing: {exc}"}
    except gspread.exceptions.APIError as exc:
        return {"service": "google_sheets", "ok": False, "error": f"API error: {exc}"}
    except Exception as exc:
        return {"service": "google_sheets", "ok": False, "error": str(exc)}


def check_all() -> list[dict]:
    """Run all four service health checks."""
    return [check_n8n(), check_hunter_bridge(), check_openclaw(), check_google_sheets()]

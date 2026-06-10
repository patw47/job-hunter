#!/home/thehunter/venv/bin/python3
"""
Smoke test — Google Sheets connectivity and tab structure for job-hunter-tracker.

Verifies credentials, auth, spreadsheet access, and read/write on all 3 tabs.
Creates tabs and headers if missing.

Run as thehunter:
    sudo -u thehunter /home/thehunter/venv/bin/python3 /opt/apps/job-hunter/test_sheets.py

Exits 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Final

import gspread
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound

CREDS_PATH: Final[Path] = Path("/opt/apps/job-hunter/credentials.json")
SPREADSHEET_NAME: Final[str] = "job-hunter-tracker"
SENTINEL_PREFIX: Final[str] = "__TEST_SMOKE__"

HEADERS: Final[dict[str, list[str]]] = {
    "MATCHES": [
        "job_id", "date_scanned", "title", "company", "location", "remote",
        "url", "source", "match_rate", "skills_found", "status",
        "cv_drive_link", "letter_drive_link", "applied_at", "snooze_count",
    ],
    "SCANNED_HASHES": [
        "sha256", "date_scanned", "url", "title", "company", "source",
    ],
    "PENDING_MATCHES": [
        "job_id", "date_scanned", "title", "company", "location", "url",
        "match_rate", "skills_found", "source", "rank",
    ],
}

TEST_ROWS: Final[dict[str, list[str]]] = {
    "MATCHES": [
        SENTINEL_PREFIX, "2026-01-01", "Test Job", "Test Corp", "Remote", "yes",
        "https://example.com/job/1", "indeed", "75.0", "python,llm",
        "new", "", "", "", "0",
    ],
    "SCANNED_HASHES": [
        SENTINEL_PREFIX, "2026-01-01", "https://example.com/job/1",
        "Test Job", "Test Corp", "indeed",
    ],
    "PENDING_MATCHES": [
        SENTINEL_PREFIX, "2026-01-01", "Test Job", "Test Corp", "Remote",
        "https://example.com/job/1", "75.0", "python,llm", "indeed", "1",
    ],
}


class Results:
    """Accumulate pass/fail counts and print per-step status."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def ok(self, msg: str) -> None:
        self.passed += 1
        print(f"[PASS] {msg}")

    def fail(self, msg: str) -> None:
        self.failed += 1
        print(f"[FAIL] {msg}")

    @property
    def total(self) -> int:
        return self.passed + self.failed

    def summary(self) -> str:
        return f"RESULT: {self.passed}/{self.total} steps passed"


def api_call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call a gspread function with up to 2 retries on HTTP 429 quota errors."""
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except APIError as exc:
            if exc.response.status_code == 429 and attempt < 2:
                print(f"  [WARN] API quota — waiting 2s (attempt {attempt + 1}/2)")
                time.sleep(2)
                continue
            raise


def phase0_preflight(r: Results) -> dict[str, Any] | None:
    """Return parsed credentials dict or None on failure."""
    if not CREDS_PATH.exists():
        r.fail(f"Phase 0 — credentials.json missing at {CREDS_PATH}")
        return None
    r.ok(f"Phase 0 — credentials.json exists")

    try:
        creds = json.loads(CREDS_PATH.read_text())
    except json.JSONDecodeError as exc:
        r.fail(f"Phase 0 — credentials.json invalid JSON: {exc}")
        return None

    if creds.get("type") != "service_account":
        r.fail(f"Phase 0 — wrong credential type: {creds.get('type')!r} (expected 'service_account')")
        return None
    r.ok("Phase 0 — credential type is service_account")

    missing = [f for f in ("client_email", "private_key", "project_id") if not creds.get(f)]
    if missing:
        r.fail(f"Phase 0 — missing fields in credentials.json: {missing}")
        return None
    r.ok("Phase 0 — required fields present (client_email, private_key, project_id)")

    return creds


def phase1_auth(r: Results, creds_path: Path) -> gspread.Client | None:
    """Return authenticated gspread client or None on failure."""
    try:
        gc = gspread.service_account(filename=str(creds_path))
        r.ok("Phase 1 — service account auth")
        return gc
    except Exception as exc:
        r.fail(f"Phase 1 — auth failed: {exc}")
        return None


def phase2_open(r: Results, gc: gspread.Client, creds: dict[str, Any]) -> gspread.Spreadsheet | None:
    """Return opened spreadsheet or None on failure."""
    try:
        spreadsheet = api_call(gc.open, SPREADSHEET_NAME)
        r.ok(f"Phase 2 — opened spreadsheet '{SPREADSHEET_NAME}'")
        return spreadsheet
    except SpreadsheetNotFound:
        email = creds.get("client_email", "<unknown>")
        r.fail(
            f"Phase 2 — spreadsheet '{SPREADSHEET_NAME}' not found. "
            f"Share it with {email} (Editor role)."
        )
        return None
    except APIError as exc:
        r.fail(f"Phase 2 — API error {exc.response.status_code}: {exc}")
        return None


def _check_tab(r: Results, spreadsheet: gspread.Spreadsheet, tab_name: str) -> None:
    """Run phases 3–7 for one tab: existence, headers, write, read-back, cleanup."""
    expected = HEADERS[tab_name]
    print(f"\n--- {tab_name} ---")

    # Phase 3: tab existence
    try:
        ws = api_call(spreadsheet.worksheet, tab_name)
        r.ok(f"Phase 3 — tab '{tab_name}' exists")
    except WorksheetNotFound:
        try:
            ws = api_call(
                spreadsheet.add_worksheet,
                title=tab_name,
                rows=1000,
                cols=len(expected),
            )
            r.ok(f"Phase 3 — tab '{tab_name}' created")
        except Exception as exc:
            r.fail(f"Phase 3 — cannot create tab '{tab_name}': {exc}")
            return

    # Phase 4: header verification
    try:
        raw = api_call(ws.row_values, 1)
        # row_values strips trailing empty cells; pad to expected length for comparison
        headers = (raw + [""] * len(expected))[:len(expected)]
        if not any(headers):
            api_call(ws.insert_row, expected, index=1, value_input_option="RAW")
            r.ok(f"Phase 4 — headers written to '{tab_name}'")
        elif headers == expected:
            r.ok(f"Phase 4 — headers match for '{tab_name}'")
        else:
            r.fail(
                f"Phase 4 — header mismatch in '{tab_name}'.\n"
                f"  expected: {expected}\n"
                f"  got:      {headers}"
            )
            return
    except Exception as exc:
        r.fail(f"Phase 4 — header check failed for '{tab_name}': {exc}")
        return

    # Phase 5: write test row
    sentinel = f"{SENTINEL_PREFIX}_{int(time.time())}"
    test_row = [sentinel] + TEST_ROWS[tab_name][1:]
    try:
        api_call(ws.append_row, test_row, value_input_option="RAW")
        r.ok(f"Phase 5 — test row appended to '{tab_name}'")
    except APIError as exc:
        if exc.response.status_code == 403:
            r.fail(f"Phase 5 — write denied (403) on '{tab_name}': service account needs Editor role")
        else:
            r.fail(f"Phase 5 — write failed on '{tab_name}': {exc}")
        return

    # Phase 6: read back
    row_index: int | None = None
    try:
        cells = api_call(ws.findall, sentinel)
        if not cells:
            r.fail(f"Phase 6 — test row not found in '{tab_name}' after write")
            return
        row_index = cells[0].row
        row_data = api_call(ws.row_values, row_index)
        if not row_data or row_data[0] != sentinel:
            r.fail(f"Phase 6 — sentinel mismatch in '{tab_name}': got {row_data[0] if row_data else '(empty)'!r}")
            return
        r.ok(f"Phase 6 — test row read back from '{tab_name}' (row {row_index})")
    except Exception as exc:
        r.fail(f"Phase 6 — read-back failed for '{tab_name}': {exc}")
        return

    # Phase 7: cleanup
    try:
        api_call(ws.delete_rows, row_index)
        verify = api_call(ws.row_values, row_index)
        if verify and verify[0] == sentinel:
            r.fail(f"Phase 7 — cleanup failed for '{tab_name}': sentinel still present at row {row_index}")
        else:
            r.ok(f"Phase 7 — test row cleaned up from '{tab_name}'")
    except Exception as exc:
        r.fail(
            f"Phase 7 — cleanup failed for '{tab_name}' at row {row_index}: {exc}. "
            "Delete manually."
        )


def main() -> int:
    r = Results()

    creds = phase0_preflight(r)
    if creds is None:
        print(f"\n{r.summary()}")
        return 1

    gc = phase1_auth(r, CREDS_PATH)
    if gc is None:
        print(f"\n{r.summary()}")
        return 1

    spreadsheet = phase2_open(r, gc, creds)
    if spreadsheet is None:
        print(f"\n{r.summary()}")
        return 1

    for tab in ("MATCHES", "SCANNED_HASHES", "PENDING_MATCHES"):
        _check_tab(r, spreadsheet, tab)

    print(f"\n{r.summary()}")
    return 1 if r.failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

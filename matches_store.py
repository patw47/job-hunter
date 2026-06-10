from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from gspread import Worksheet

logger = logging.getLogger(__name__)

CREDS_PATH: Final[str] = os.environ.get(
    "GOOGLE_CREDS_PATH", "/opt/apps/job-hunter/credentials.json"
)
SPREADSHEET_NAME: Final[str] = "job-hunter-tracker"
MATCHES_TAB: Final[str] = "MATCHES"

COL_JOB_ID: Final[int] = 1
COL_STATUS: Final[int] = 11
COL_NOTES: Final[int] = 15

VALID_STATUSES: Final[frozenset[str]] = frozenset(
    {"Notifié", "Généré", "Envoyé", "Réponse+", "Réponse-", "Entretien", "Ignoré"}
)


def open_matches(creds_path: str | None = None) -> Worksheet:
    """Open and return the MATCHES worksheet using gspread service account."""
    import gspread

    path = creds_path or CREDS_PATH
    gc = gspread.service_account(filename=path)
    return gc.open(SPREADSHEET_NAME).worksheet(MATCHES_TAB)


def find_row(sheet: Worksheet, job_id: str) -> int | None:
    """Return 1-based row number for job_id, or None if not found."""
    all_ids: list[str] = sheet.col_values(COL_JOB_ID)
    for i, val in enumerate(all_ids):
        if i == 0:
            continue  # skip header row
        if val == job_id:
            return i + 1
    return None


def get_status(sheet: Worksheet, job_id: str) -> str:
    """Return current status for job_id.

    Raises KeyError if job_id not found in MATCHES.
    """
    row = find_row(sheet, job_id)
    if row is None:
        raise KeyError(job_id)
    return sheet.cell(row, COL_STATUS).value or ""


def set_status(sheet: Worksheet, job_id: str, status: str) -> None:
    """Update status column for job_id.

    Raises ValueError for unknown status values.
    Raises KeyError if job_id not found in MATCHES.
    """
    if status not in VALID_STATUSES:
        raise ValueError(status)
    row = find_row(sheet, job_id)
    if row is None:
        raise KeyError(job_id)
    sheet.update_cell(row, COL_STATUS, status)
    logger.info("MATCHES status updated: job_id=%s status=%s row=%d", job_id[:12], status, row)


def set_note(sheet: Worksheet, job_id: str, note: str) -> None:
    """Write or replace the notes column for job_id.

    Raises KeyError if job_id not found in MATCHES.
    """
    row = find_row(sheet, job_id)
    if row is None:
        raise KeyError(job_id)
    sheet.update_cell(row, COL_NOTES, note)
    logger.info("MATCHES note updated: job_id=%s row=%d", job_id[:12], row)

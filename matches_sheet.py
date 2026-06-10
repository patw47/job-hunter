from __future__ import annotations

import hashlib
import logging
import os
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from gspread import Worksheet

logger = logging.getLogger(__name__)

CREDS_PATH: str = os.environ.get("GOOGLE_CREDS_PATH", "/opt/apps/job-hunter/credentials.json")
SPREADSHEET_NAME: str = "job-hunter-tracker"
MATCHES_TAB: str = "MATCHES"

# MATCHES columns (1-based) — Sprint 2 adds snooze_count as col 15
COL_TITLE: int = 3
COL_COMPANY: int = 4
COL_LOCATION: int = 5
COL_REMOTE: int = 6
COL_URL: int = 7
COL_MATCH_RATE: int = 9
COL_SKILLS_FOUND: int = 10
COL_STATUS: int = 11
COL_SNOOZE_COUNT: int = 15

STATUS_IGNORED: str = "Ignoré"
STATUS_SNOOZED: str = "Snoozé"
STATUS_SENT: str = "Envoyé"
SNOOZE_MAX: int = 2


def _col_to_letter(col: int) -> str:
    """Convert 1-based column number to A1 column letter (supports cols 1–26)."""
    return chr(ord("A") + col - 1)


def _url_hash_16(url: str) -> str:
    """16-char SHA-256 prefix of normalized URL — identical to telegram_notifier._url_hash."""
    parsed = urlparse(url)
    normalized = parsed._replace(query="", fragment="").geturl()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def open_matches_sheet(creds_path: str | None = None) -> Worksheet:
    """Open and return the MATCHES worksheet using gspread service account."""
    import gspread

    path = creds_path or CREDS_PATH
    gc = gspread.service_account(filename=path)
    return gc.open(SPREADSHEET_NAME).worksheet(MATCHES_TAB)


def find_row_by_url_hash(
    url_hash: str, sheet: Worksheet
) -> tuple[int | None, list[str] | None]:
    """Find the first MATCHES row whose URL hash matches url_hash (16-char hex).

    Returns (1-based row index, row values) or (None, None) if not found.
    """
    all_rows: list[list[str]] = sheet.get_all_values()
    for i, row in enumerate(all_rows):
        if i == 0:
            continue  # skip header
        if len(row) < COL_URL:
            continue
        if _url_hash_16(row[COL_URL - 1]) == url_hash:
            return i + 1, row
    return None, None


def set_status(url_hash: str, status: str, sheet: Worksheet) -> bool:
    """Update the status column for the row matching url_hash.

    Returns True if the row was found and updated, False otherwise.
    """
    row_idx, _ = find_row_by_url_hash(url_hash, sheet)
    if row_idx is None:
        logger.warning("set_status: no MATCHES row found for hash=%s", url_hash)
        return False
    sheet.update_cell(row_idx, COL_STATUS, status)
    logger.info("MATCHES row %d status → %s (hash=%s)", row_idx, status, url_hash)
    return True


def increment_snooze(url_hash: str, sheet: Worksheet) -> tuple[bool, int]:
    """Increment snooze_count; auto-ignore when count reaches SNOOZE_MAX.

    Uses batch_update for atomicity (snooze_count + status in one API call).
    Returns (auto_ignored, new_count).
    """
    row_idx, row = find_row_by_url_hash(url_hash, sheet)
    if row_idx is None:
        logger.warning("increment_snooze: no MATCHES row found for hash=%s", url_hash)
        return False, 0

    snooze_raw = row[COL_SNOOZE_COUNT - 1] if len(row) >= COL_SNOOZE_COUNT else ""
    try:
        current = int(snooze_raw) if snooze_raw else 0
    except ValueError:
        current = 0

    new_count = current + 1
    new_status = STATUS_IGNORED if new_count >= SNOOZE_MAX else STATUS_SNOOZED

    sheet.batch_update(
        [
            {
                "range": f"{_col_to_letter(COL_SNOOZE_COUNT)}{row_idx}",
                "values": [[str(new_count)]],
            },
            {
                "range": f"{_col_to_letter(COL_STATUS)}{row_idx}",
                "values": [[new_status]],
            },
        ]
    )

    auto_ignored = new_count >= SNOOZE_MAX
    if auto_ignored:
        logger.info(
            "MATCHES row %d auto-ignored after %d snoozes (hash=%s)",
            row_idx,
            new_count,
            url_hash,
        )
    else:
        logger.info(
            "MATCHES row %d snoozed (%d/%d) (hash=%s)",
            row_idx,
            new_count,
            SNOOZE_MAX,
            url_hash,
        )
    return auto_ignored, new_count


def get_snoozed_offers(sheet: Worksheet) -> list[dict]:
    """Return MATCHES rows with status='Snoozé' and snooze_count < SNOOZE_MAX."""
    all_rows: list[list[str]] = sheet.get_all_values()
    result: list[dict] = []
    for i, row in enumerate(all_rows):
        if i == 0:
            continue
        if len(row) < COL_STATUS:
            continue
        if row[COL_STATUS - 1] != STATUS_SNOOZED:
            continue
        snooze_raw = row[COL_SNOOZE_COUNT - 1] if len(row) >= COL_SNOOZE_COUNT else ""
        try:
            snooze_count = int(snooze_raw) if snooze_raw else 0
        except ValueError:
            snooze_count = 0
        if snooze_count >= SNOOZE_MAX:
            continue
        result.append(
            {
                "url": row[COL_URL - 1] if len(row) >= COL_URL else "",
                "title": row[COL_TITLE - 1] if len(row) >= COL_TITLE else "",
                "company": row[COL_COMPANY - 1] if len(row) >= COL_COMPANY else "",
                "pays": row[COL_LOCATION - 1] if len(row) >= COL_LOCATION else "",
                "remote_type": row[COL_REMOTE - 1] if len(row) >= COL_REMOTE else "Remote",
                "match_rate": row[COL_MATCH_RATE - 1] if len(row) >= COL_MATCH_RATE else "",
                "keywords_matched": row[COL_SKILLS_FOUND - 1] if len(row) >= COL_SKILLS_FOUND else "",
                "keywords_missing": [],
            }
        )
    return result

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
SCANNED_HASHES_TAB: str = "SCANNED_HASHES"


def normalize_url(url: str) -> str:
    """Strip query string and fragment from a URL for stable hashing."""
    if not url:
        return url
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="").geturl()


def compute_hash(url: str) -> str:
    """Return the SHA-256 hex digest of the normalized URL."""
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()


def compute_stable_hash(title: str, company: str) -> str:
    """Return SHA-256 of normalized title+company — stable across URL changes."""
    key = (title.lower().strip() + "|" + company.lower().strip()).encode()
    return hashlib.sha256(key).hexdigest()


def is_duplicate(url_hash: str, sheet: Worksheet) -> bool:
    """Return True if url_hash already appears in column 1 (sha256) of SCANNED_HASHES."""
    existing: list[str] = sheet.col_values(1)
    return url_hash in existing


def log_hashes(hashes: list[dict], sheet: Worksheet) -> None:
    """Batch-write hash records to SCANNED_HASHES in a single API call.

    Each dict must have: url_hash, title, company, url, source, scan_date.
    Column order matches SCANNED_HASHES header: sha256 | date_scanned | url | title | company | source.
    """
    if not hashes:
        return
    rows: list[list[str]] = [
        [h["url_hash"], h["scan_date"], h["url"], h["title"], h["company"], h["source"]]
        for h in hashes
    ]
    sheet.append_rows(rows, value_input_option="RAW")


def open_scanned_hashes(creds_path: str | None = None) -> Worksheet:
    """Open and return the SCANNED_HASHES worksheet using gspread service account."""
    import gspread

    path = creds_path or CREDS_PATH
    gc = gspread.service_account(filename=path)
    return gc.open(SPREADSHEET_NAME).worksheet(SCANNED_HASHES_TAB)

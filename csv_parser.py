"""
CSV Parser — Indeed CSV reader and normalizer.

Public API:
  parse_indeed_csv(csv_text: str) -> list[dict]
  load_indeed_csv(path: Path) -> list[dict]
  download_and_parse_drive_csv(folder_id, date_str, creds_path) -> list[dict]

CLI: python3 csv_parser.py [--folder-id ID] [--date YYYY-MM-DD] [--creds PATH]
Outputs JSON array to stdout; always exits 0 (non-blocking).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import sys
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

EXPECTED_COLUMNS: Final[list[str]] = [
    "job_id",
    "date_scanned",
    "title",
    "company",
    "location",
    "remote",
    "url",
    "source",
    "match_rate",
    "skills_found",
    "status",
    "cv_drive_link",
    "letter_drive_link",
    "applied_at",
]

_DRIVE_SCOPES: Final[list[str]] = ["https://www.googleapis.com/auth/drive"]


def parse_indeed_csv(csv_text: str) -> list[dict]:
    """Parse CSV text → list of normalized offer dicts.

    Drops rows missing url or job_id with a warning.
    Returns [] for empty input or headers-only CSV.
    """
    if not csv_text or not csv_text.strip():
        return []

    text = csv_text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))

    offers: list[dict] = []
    row_count = 0

    for row in reader:
        row_count += 1
        cleaned = {k.strip(): (v.strip() if v is not None else "") for k, v in row.items()}

        job_id = cleaned.get("job_id", "")
        url = cleaned.get("url", "")

        if not job_id or not url:
            missing = [field for field, val in [("job_id", job_id), ("url", url)] if not val]
            logger.warning(
                "Dropping row missing %s: job_id=%r url=%r",
                ", ".join(missing),
                job_id,
                url,
            )
            continue

        offer = {col: cleaned.get(col, "") for col in EXPECTED_COLUMNS}
        offers.append(offer)

    if row_count == 0:
        logger.info("indeed scan returned 0 offers")

    return offers


def load_indeed_csv(path: Path) -> list[dict]:
    """Read CSV from local path. Returns [] with log if file not found."""
    if not path.exists():
        date_part = (
            path.stem.replace("indeed-matches-", "")
            if "indeed-matches-" in path.stem
            else path.stem
        )
        logger.info("indeed CSV not found for %s — skipping", date_part)
        return []

    text = path.read_text(encoding="utf-8-sig")
    return parse_indeed_csv(text)


def _build_drive_service(creds_path: str):
    """Build Google Drive v3 service from service account credentials file."""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_file(creds_path, scopes=_DRIVE_SCOPES)
    return build("drive", "v3", credentials=creds)


def download_and_parse_drive_csv(
    folder_id: str,
    date_str: str,
    creds_path: str,
) -> list[dict]:
    """Search Drive for indeed-matches-{date_str}.csv, download and parse.

    Returns [] if file not found or Drive call fails (non-blocking).
    """
    filename = f"indeed-matches-{date_str}.csv"

    try:
        service = _build_drive_service(creds_path)
        query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
        result = service.files().list(q=query, fields="files(id, name)").execute()
        files = result.get("files", [])

        if not files:
            logger.info("indeed CSV not found for %s — skipping", date_str)
            return []

        file_id = files[0]["id"]
        content = service.files().get_media(fileId=file_id).execute()
        csv_text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
        return parse_indeed_csv(csv_text)

    except Exception as exc:
        logger.warning("Drive lookup failed for %s: %s — skipping", filename, exc)
        return []


if __name__ == "__main__":
    import argparse
    from datetime import date

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    ap = argparse.ArgumentParser(description="Read Indeed CSV from Google Drive")
    ap.add_argument(
        "--folder-id",
        default=os.environ.get("GOOGLE_DRIVE_FOLDER_ID", ""),
    )
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument(
        "--creds",
        default=os.environ.get(
            "GOOGLE_CREDS_PATH", "/opt/apps/job-hunter/credentials.json"
        ),
    )
    args = ap.parse_args()

    if not args.folder_id:
        logger.warning("GOOGLE_DRIVE_FOLDER_ID not set — returning empty result")
        print(json.dumps([]))
        sys.exit(0)

    offers = download_and_parse_drive_csv(args.folder_id, args.date, args.creds)
    print(json.dumps(offers))

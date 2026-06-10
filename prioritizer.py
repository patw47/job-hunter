from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from gspread import Worksheet

logger = logging.getLogger(__name__)

DAILY_CAP: Final[int] = 25
CREDS_PATH: str = os.environ.get("GOOGLE_CREDS_PATH", "/opt/apps/job-hunter/credentials.json")
SPREADSHEET_NAME: str = "job-hunter-tracker"
MATCHES_TAB: str = "MATCHES"
PENDING_MATCHES_TAB: str = "PENDING_MATCHES"

MATCHES_STATUS_COL: Final[int] = 11
MATCHES_TELEGRAM_SENT_COL: Final[int] = 12

MATCHES_HEADER: Final[list[str]] = [
    "job_id", "date_scanned", "title", "company", "location", "remote",
    "url", "source", "match_rate", "skills_found", "status", "telegram_sent",
    "cv_drive_link", "letter_drive_link", "applied_at",
]

PENDING_HEADER: Final[list[str]] = [
    "job_id", "date_scanned", "title", "company", "location", "url",
    "match_rate", "skills_found", "source", "rank",
]


def read_pending_offers(pending_sheet: "Worksheet") -> list[dict]:
    """Returns all rows from PENDING_MATCHES as a list of dicts."""
    return pending_sheet.get_all_records()


def compute_slots(pending_offers: list[dict], cap: int = DAILY_CAP) -> int:
    """Available slots for new matches today: cap minus pending count, floor 0."""
    return max(0, cap - len(pending_offers))


def merge_offers(pending_offers: list[dict], new_matches: list[dict]) -> list[dict]:
    """Pending offers first (priority, original order preserved), then new matches sorted desc by match_rate."""
    sorted_new = sorted(
        new_matches,
        key=lambda o: float(o.get("match_rate") or 0),
        reverse=True,
    )
    return list(pending_offers) + sorted_new


def split_by_cap(merged: list[dict], slots: int) -> tuple[list[dict], list[dict]]:
    """Split merged: first `slots` items → to_notify, remainder → to_park."""
    n = max(0, slots)
    return merged[:n], merged[n:]


def update_matches_tab(to_notify: list[dict], matches_sheet: "Worksheet") -> None:
    """Upsert each notified offer in MATCHES: update status/telegram_sent if found, append if not."""
    if not to_notify:
        return
    for offer in to_notify:
        job_id = str(offer.get("job_id") or "")
        try:
            cell = matches_sheet.find(job_id)
            row = cell.row
            matches_sheet.update_cell(row, MATCHES_STATUS_COL, "Notifié")
            matches_sheet.update_cell(row, MATCHES_TELEGRAM_SENT_COL, "FALSE")
        except Exception:
            matches_sheet.append_rows(
                [_build_matches_row(offer)], value_input_option="RAW"
            )


def write_pending_tab(to_park: list[dict], pending_sheet: "Worksheet") -> None:
    """Append overflow offers to PENDING_MATCHES, skipping any already present by job_id."""
    if not to_park:
        return
    existing_ids: set[str] = {
        str(r.get("job_id") or "") for r in pending_sheet.get_all_records()
    }
    novel = [o for o in to_park if str(o.get("job_id") or "") not in existing_ids]
    if not novel:
        return
    rows = [_build_pending_row(o, rank=i + 1) for i, o in enumerate(novel)]
    pending_sheet.append_rows(rows, value_input_option="RAW")


def clear_promoted_rows(promoted: list[dict], pending_sheet: "Worksheet") -> None:
    """Delete promoted offers from PENDING_MATCHES (reverse order to keep row indices valid)."""
    if not promoted:
        return
    rows_to_delete: list[int] = []
    for offer in promoted:
        job_id = str(offer.get("job_id") or "")
        if not job_id:
            continue
        try:
            cell = pending_sheet.find(job_id)
            rows_to_delete.append(cell.row)
        except Exception:
            pass
    for row in sorted(rows_to_delete, reverse=True):
        pending_sheet.delete_rows(row)


def run_prioritizer(
    pending_sheet: "Worksheet",
    matches_sheet: "Worksheet",
    new_matches: list[dict],
) -> dict[str, int]:
    """Full prioritization cycle. Returns notified/parked counts."""
    pending = read_pending_offers(pending_sheet)
    slots = compute_slots(pending)
    merged = merge_offers(pending, new_matches)
    total_to_notify = min(DAILY_CAP, len(pending) + slots)
    to_notify, to_park = split_by_cap(merged, total_to_notify)

    update_matches_tab(to_notify, matches_sheet)
    clear_promoted_rows(to_notify, pending_sheet)
    write_pending_tab(to_park, pending_sheet)

    logger.info(
        "Prioritizer: notified=%d parked=%d (pending_in=%d new_in=%d slots=%d)",
        len(to_notify), len(to_park), len(pending), len(new_matches), slots,
    )
    return {"notified": len(to_notify), "parked": len(to_park)}


def open_sheets(creds_path: str | None = None) -> tuple["Worksheet", "Worksheet"]:
    """Open and return (pending_sheet, matches_sheet) via gspread service account."""
    import gspread

    path = creds_path or CREDS_PATH
    gc = gspread.service_account(filename=path)
    spreadsheet = gc.open(SPREADSHEET_NAME)
    return (
        spreadsheet.worksheet(PENDING_MATCHES_TAB),
        spreadsheet.worksheet(MATCHES_TAB),
    )


def _build_matches_row(
    offer: dict,
    status: str = "Notifié",
    telegram_sent: str = "FALSE",
) -> list[str]:
    """Build a MATCHES row in MATCHES_HEADER column order."""
    return [
        str(offer.get("job_id") or ""),
        str(offer.get("date_scanned") or ""),
        str(offer.get("title") or ""),
        str(offer.get("company") or ""),
        str(offer.get("location") or ""),
        str(offer.get("remote") or ""),
        str(offer.get("url") or ""),
        str(offer.get("source") or ""),
        str(offer.get("match_rate") or ""),
        str(offer.get("skills_found") or ""),
        status,
        telegram_sent,
        str(offer.get("cv_drive_link") or ""),
        str(offer.get("letter_drive_link") or ""),
        str(offer.get("applied_at") or ""),
    ]


def _build_pending_row(offer: dict, rank: int = 0) -> list[str]:
    """Build a PENDING_MATCHES row in PENDING_HEADER column order."""
    return [
        str(offer.get("job_id") or ""),
        str(offer.get("date_scanned") or ""),
        str(offer.get("title") or ""),
        str(offer.get("company") or ""),
        str(offer.get("location") or ""),
        str(offer.get("url") or ""),
        str(offer.get("match_rate") or ""),
        str(offer.get("skills_found") or ""),
        str(offer.get("source") or ""),
        str(rank),
    ]

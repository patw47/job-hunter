#!/usr/bin/env python3
"""
Compute weekly job application statistics from MATCHES Google Sheets.

CLI: python weekly_stats.py [--week YYYY-MM-DD]
  --week: Monday of the target week (ISO format). Defaults to current week's Monday.
Output: JSON to stdout matching weekly-reporter SKILL.md input format.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gspread import Worksheet

import matches_store

logger = logging.getLogger(__name__)

# Column indices (0-based) in get_all_values() rows
_COL_DATE = 1
_COL_TITLE = 2
_COL_COMPANY = 3
_COL_SOURCE = 7
_COL_MATCH_RATE = 8
_COL_STATUS = 10

_MIN_ROW_LEN = _COL_STATUS + 1

_STATUSES_CV_GENERATED: frozenset[str] = frozenset(
    {"Généré", "Envoyé", "Réponse+", "Réponse-", "Entretien"}
)
_STATUSES_APPLIED: frozenset[str] = frozenset(
    {"Envoyé", "Réponse+", "Réponse-", "Entretien"}
)
_STATUSES_RESPONSE: frozenset[str] = frozenset({"Réponse+", "Réponse-", "Entretien"})


def compute_week_bounds(week_start_iso: str) -> tuple[datetime.date, datetime.date]:
    """Return (Monday, Sunday) for the week whose Monday is week_start_iso.

    Raises ValueError if the date is not a Monday.
    """
    start = datetime.date.fromisoformat(week_start_iso)
    if start.weekday() != 0:
        raise ValueError(
            f"week_start must be a Monday, got {start} (weekday={start.weekday()})"
        )
    return start, start + datetime.timedelta(days=6)


def _current_week_monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def compute_stats(
    sheet: Worksheet, week_start: datetime.date, week_end: datetime.date
) -> dict:
    """Compute weekly stats from sheet for [week_start, week_end] inclusive.

    Returns a dict matching the weekly-reporter SKILL.md input format.
    """
    all_rows = sheet.get_all_values()
    data_rows = all_rows[1:] if all_rows else []

    week_rows = []
    for row in data_rows:
        if len(row) < _MIN_ROW_LEN:
            continue
        try:
            row_date = datetime.date.fromisoformat(row[_COL_DATE].strip())
        except ValueError:
            continue
        if week_start <= row_date <= week_end:
            week_rows.append(row)

    total_scanned = len(week_rows)
    total_qualified = 0
    notifications_sent = 0
    cvs_generated = 0
    applications_sent = 0
    responses_received = 0
    match_rate_sum = 0.0
    match_rate_count = 0
    source_counts: dict[str, int] = {"indeed": 0, "linkedin": 0}
    top_candidates: list[dict] = []

    for row in week_rows:
        status = row[_COL_STATUS].strip()

        raw_rate = row[_COL_MATCH_RATE].strip()
        try:
            match_rate = float(raw_rate) if raw_rate else 0.0
        except ValueError:
            match_rate = 0.0

        if match_rate >= 60.0:
            total_qualified += 1

        if status and status != "Ignoré":
            notifications_sent += 1

        if status in _STATUSES_CV_GENERATED:
            cvs_generated += 1

        if status in _STATUSES_APPLIED:
            applications_sent += 1

        if status in _STATUSES_RESPONSE:
            responses_received += 1

        if raw_rate:
            try:
                match_rate_sum += float(raw_rate)
                match_rate_count += 1
            except ValueError:
                pass

        source = row[_COL_SOURCE].strip().lower()
        if source in source_counts:
            source_counts[source] += 1

        if status not in _STATUSES_APPLIED and status != "Ignoré":
            top_candidates.append(
                {
                    "title": row[_COL_TITLE].strip(),
                    "company": row[_COL_COMPANY].strip(),
                    "match_rate": round(match_rate, 1),
                    "status": status,
                }
            )

    avg_match_rate = (
        round(match_rate_sum / match_rate_count, 1) if match_rate_count > 0 else 0.0
    )

    top_candidates.sort(key=lambda x: x["match_rate"], reverse=True)

    source_total = sum(source_counts.values())
    source_breakdown = {
        k: round(v / source_total * 100.0, 1) if source_total > 0 else 0.0
        for k, v in source_counts.items()
    }

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "stats": {
            "total_scanned": total_scanned,
            "total_qualified": total_qualified,
            "notifications_sent": notifications_sent,
            "cvs_generated": cvs_generated,
            "letters_generated": cvs_generated,
            "applications_sent": applications_sent,
            "responses_received": responses_received,
            "avg_match_rate": avg_match_rate,
            "source_breakdown": source_breakdown,
            "top_matches": top_candidates[:3],
        },
    }


def main(argv: list[str] | None = None) -> None:
    """CLI entry point — compute weekly stats and print JSON to stdout."""
    parser = argparse.ArgumentParser(
        description="Compute weekly job application statistics from MATCHES"
    )
    parser.add_argument(
        "--week",
        metavar="YYYY-MM-DD",
        help="Monday of the target week. Defaults to current week's Monday.",
    )
    args = parser.parse_args(argv)

    if args.week:
        try:
            week_start, week_end = compute_week_bounds(args.week)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        week_start = _current_week_monday()
        week_end = week_start + datetime.timedelta(days=6)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    sheet = matches_store.open_matches()
    result = compute_stats(sheet, week_start, week_end)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

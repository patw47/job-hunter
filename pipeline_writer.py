"""CLI wrapper around prioritizer.run_prioritizer for n8n pipeline execution.

Reads JSON from stdin: {"new_matches": [...]}
Persists per-offer details (description included) to the offers store, then
calls run_prioritizer(pending_sheet, matches_sheet, new_matches).
Outputs {"ok": true, "notified": N, "parked": M} to stdout.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logger = logging.getLogger(__name__)

_DETAIL_FIELDS = (
    "job_id", "title", "company", "location", "url",
    "source", "match_rate", "skills_found", "description",
)


def _offers_store_dir() -> Path:
    return Path(os.environ.get("OFFERS_STORE_DIR", "/opt/apps/job-hunter/offers"))


def persist_offer_details(new_matches: list[dict]) -> int:
    """Write one JSON file per offer to the offers store; returns count saved.

    MATCHES has no description column, so this store is what /generate reads
    to build CV/cover-letter briefs that stick to the offer text.
    """
    saved = 0
    store = _offers_store_dir()
    for offer in new_matches:
        job_id = offer.get("job_id", "")
        if not job_id:
            continue
        try:
            store.mkdir(parents=True, exist_ok=True)
            detail = {k: offer.get(k, "") for k in _DETAIL_FIELDS}
            (store / f"{job_id}.json").write_text(
                json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            saved += 1
        except Exception as exc:
            logger.warning("Could not persist offer %s: %s", job_id, exc)
    return saved


def main() -> None:
    """Entry point — reads JSON from stdin, writes result JSON to stdout."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"invalid JSON: {exc}"}))
        sys.exit(1)

    new_matches: list[dict] = data.get("new_matches", [])
    logger.info("pipeline_writer: received %d new_matches", len(new_matches))

    saved = persist_offer_details(new_matches)
    logger.info("pipeline_writer: persisted %d offer detail file(s)", saved)

    from prioritizer import open_sheets, run_prioritizer

    pending_sheet, matches_sheet = open_sheets()
    result = run_prioritizer(pending_sheet, matches_sheet, new_matches)

    print(json.dumps({"ok": True, **result}))


if __name__ == "__main__":
    main()

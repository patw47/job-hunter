"""CLI wrapper around prioritizer.run_prioritizer for n8n pipeline execution.

Reads JSON from stdin: {"new_matches": [...]}
Calls run_prioritizer(pending_sheet, matches_sheet, new_matches).
Outputs {"ok": true, "notified": N, "parked": M} to stdout.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logger = logging.getLogger(__name__)


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

    from prioritizer import open_sheets, run_prioritizer

    pending_sheet, matches_sheet = open_sheets()
    result = run_prioritizer(pending_sheet, matches_sheet, new_matches)

    print(json.dumps({"ok": True, **result}))


if __name__ == "__main__":
    main()

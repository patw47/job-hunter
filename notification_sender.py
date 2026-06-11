from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import telegram_notifier

logger = logging.getLogger(__name__)


def _normalize_rate(rate: object) -> float:
    """Normalize match_rate to float percentage (0-100)."""
    try:
        v = float(str(rate))
    except (TypeError, ValueError):
        return 0.0
    return v * 100.0 if v <= 1.0 else v


def _call_analyze(offer: dict, bridge_url: str) -> str | None:
    """Call POST /analyze and return card text, or None on error."""
    data = json.dumps(offer, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{bridge_url}/analyze",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
            if parsed.get("ok"):
                return str(parsed.get("result", "")).strip() or None
            logger.error("POST /analyze error: %s", parsed.get("error", "unknown"))
            return None
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        logger.error("POST /analyze failed for %r: %s", offer.get("title"), exc)
        return None


def send_notifications(
    new_matches: list[dict],
    bot_token: str,
    chat_id: str,
    bridge_url: str,
) -> dict:
    """Send Telegram notifications for qualified offers.

    ≥80%: individual card (Indeed complete → direct, else POST /analyze).
    60-79%: single digest message, sorted descending.
    Returns counters dict.
    """
    sorted_offers = sorted(
        new_matches,
        key=lambda o: _normalize_rate(o.get("match_rate", 0)),
        reverse=True,
    )

    high = [o for o in sorted_offers if _normalize_rate(o.get("match_rate", 0)) >= 80]
    medium = [o for o in sorted_offers if 60 <= _normalize_rate(o.get("match_rate", 0)) < 80]

    sent_individual = 0
    errors = 0

    for offer in high:
        try:
            if offer.get("source") == "indeed" and telegram_notifier._is_indeed_complete(offer):
                telegram_notifier.send_indeed_card(offer, bot_token, chat_id)
            else:
                card_text = _call_analyze(offer, bridge_url)
                if card_text:
                    telegram_notifier.send_card_from_text(card_text, offer, bot_token, chat_id)
                else:
                    telegram_notifier.send_match_card(offer, bot_token, chat_id)
            sent_individual += 1
        except Exception as exc:
            logger.error("Card send failed for %r: %s", offer.get("title"), exc)
            errors += 1

    sent_digest = 0
    if medium:
        try:
            telegram_notifier.send_digest(medium, bot_token, chat_id)
            sent_digest = len(medium)
        except Exception as exc:
            logger.error("Digest send failed: %s", exc)
            errors += 1

    return {
        "ok": errors == 0,
        "sent_individual": sent_individual,
        "sent_digest": sent_digest,
        "errors": errors,
    }


def main() -> int:
    """Read {new_matches: [...]} from stdin, send Telegram notifications, print JSON result."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON input: %s", exc)
        print(json.dumps({"ok": False, "error": "invalid_json"}))
        return 1

    new_matches = payload.get("new_matches", [])
    bot_token = os.environ.get("TELEGRAM_HUNTER_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_HUNTER_CHAT_ID", "")
    bridge_port = os.environ.get("HUNTER_BRIDGE_PORT", "18798")
    bridge_url = os.environ.get("HUNTER_BRIDGE_URL", f"http://127.0.0.1:{bridge_port}")

    if not bot_token or not chat_id:
        logger.error("TELEGRAM_HUNTER_BOT_TOKEN and TELEGRAM_HUNTER_CHAT_ID required")
        print(json.dumps({"ok": False, "error": "missing_env"}))
        return 1

    result = send_notifications(new_matches, bot_token, chat_id, bridge_url)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

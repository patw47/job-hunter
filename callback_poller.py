"""Callback poller — long-polls the cards bot and relays button clicks.

n8n is loopback-only so Telegram webhooks (public HTTPS required) are not an
option; this poller is the delivery path for inline-button callbacks:

    Telegram getUpdates (callback_query) → POST {bridge}/callback

The bridge answers the callback_query, edits the card and updates MATCHES.

Env:
  TELEGRAM_HUNTER_BOT_TOKEN  cards bot token (required)
  HUNTER_BRIDGE_URL          default http://127.0.0.1:$HUNTER_BRIDGE_PORT
  HUNTER_BRIDGE_PORT         default 18798
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

POLL_TIMEOUT = 30  # Telegram long-poll seconds
ERROR_BACKOFF = 5


def _bridge_url() -> str:
    port = os.environ.get("HUNTER_BRIDGE_PORT", "18798")
    return os.environ.get("HUNTER_BRIDGE_URL", f"http://127.0.0.1:{port}")


def _post(url: str, payload: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_callback_payload(callback_query: dict) -> dict:
    """Map a Telegram callback_query object to the bridge /callback body."""
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    return {
        "callback_query_id": callback_query.get("id", ""),
        "callback_data": callback_query.get("data", ""),
        "chat_id": str(chat.get("id", "")),
        "message_id": message.get("message_id"),
    }


def relay_callback(callback_query: dict) -> dict:
    payload = build_callback_payload(callback_query)
    logger.info(
        "Relaying callback %r (chat=%s)", payload["callback_data"], payload["chat_id"]
    )
    return _post(f"{_bridge_url()}/callback", payload, timeout=90)


def poll_loop(token: str) -> None:
    api = f"https://api.telegram.org/bot{token}"
    offset = 0
    logger.info("Callback poller started (bridge=%s)", _bridge_url())
    while True:
        try:
            updates = _post(
                f"{api}/getUpdates",
                {
                    "timeout": POLL_TIMEOUT,
                    "offset": offset,
                    "allowed_updates": ["callback_query"],
                },
                timeout=POLL_TIMEOUT + 10,
            )
            for update in updates.get("result", []):
                offset = update["update_id"] + 1
                cq = update.get("callback_query")
                if not cq:
                    continue
                try:
                    result = relay_callback(cq)
                    logger.info("Bridge result: %s", json.dumps(result)[:200])
                except Exception as exc:
                    logger.error("Relay failed for %r: %s", cq.get("data"), exc)
        except Exception as exc:
            logger.warning("Poll error: %s — retry in %ds", exc, ERROR_BACKOFF)
            time.sleep(ERROR_BACKOFF)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = os.environ.get("TELEGRAM_HUNTER_BOT_TOKEN", "")
    if not token:
        logger.error("TELEGRAM_HUNTER_BOT_TOKEN required")
        return 1
    poll_loop(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())

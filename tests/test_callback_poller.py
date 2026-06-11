"""Tests for callback_poller — mapping callback_query → bridge payload."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import callback_poller as cp


def _cq(data: str = "ignore:abc123", chat_id: int = 1543956122, message_id: int = 42) -> dict:
    return {
        "id": "cbq-001",
        "data": data,
        "message": {"message_id": message_id, "chat": {"id": chat_id, "type": "private"}},
    }


class TestBuildCallbackPayload(unittest.TestCase):
    def test_full_mapping(self) -> None:
        payload = cp.build_callback_payload(_cq())
        assert payload == {
            "callback_query_id": "cbq-001",
            "callback_data": "ignore:abc123",
            "chat_id": "1543956122",
            "message_id": 42,
        }

    def test_chat_id_is_string(self) -> None:
        payload = cp.build_callback_payload(_cq(chat_id=99))
        assert payload["chat_id"] == "99"

    def test_missing_message_tolerated(self) -> None:
        payload = cp.build_callback_payload({"id": "x", "data": "snooze:h"})
        assert payload["callback_data"] == "snooze:h"
        assert payload["chat_id"] == ""
        assert payload["message_id"] is None

    def test_all_actions_pass_through(self) -> None:
        for action in ("ignore", "snooze", "generate", "apply", "skip", "sent", "detail"):
            payload = cp.build_callback_payload(_cq(data=f"{action}:deadbeef"))
            assert payload["callback_data"] == f"{action}:deadbeef"


class TestRelayCallback(unittest.TestCase):
    def test_relay_posts_to_bridge(self) -> None:
        captured = {}

        def fake_post(url, payload, timeout):
            captured["url"] = url
            captured["payload"] = payload
            return {"ok": True, "action": "ignore"}

        with patch.object(cp, "_post", side_effect=fake_post):
            result = cp.relay_callback(_cq())
        assert result["ok"] is True
        assert captured["url"].endswith("/callback")
        assert captured["payload"]["callback_data"] == "ignore:abc123"

    def test_bridge_url_from_env(self) -> None:
        with patch.dict("os.environ", {"HUNTER_BRIDGE_URL": "http://127.0.0.1:9999"}):
            assert cp._bridge_url() == "http://127.0.0.1:9999"

    def test_bridge_url_default_port(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("HUNTER_BRIDGE_URL", None)
            os.environ.pop("HUNTER_BRIDGE_PORT", None)
            assert cp._bridge_url() == "http://127.0.0.1:18798"


if __name__ == "__main__":
    unittest.main()

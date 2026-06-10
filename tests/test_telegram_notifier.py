"""Unit tests for telegram_notifier.py — no network calls, matches_store fully mocked."""
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import telegram_notifier
from telegram_notifier import (
    _build_card_text,
    _build_keyboard,
    _escape_html,
    _flag_emoji,
    _url_hash,
    send_digest,
    send_match_card,
)

VALID_JOB_ID = "a" * 64
OTHER_JOB_ID = "b" * 64
SHORT_JOB_ID = "abc123"
UPPERCASE_JOB_ID = "A" * 64


def _run(argv: list[str]) -> dict:
    """Call main(argv) and return parsed JSON output."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        telegram_notifier.main(argv)
    return json.loads(buf.getvalue().strip())


def _mock_sheet() -> MagicMock:
    return MagicMock()


def _offer(**overrides) -> dict:
    base: dict = {
        "url": "https://example.com/jobs/ai-engineer-12345",
        "title": "AI Engineer",
        "company": "Acme Corp",
        "pays": "FR",
        "remote_type": "Remote",
        "match_rate": 85,
        "keywords_matched": ["Python", "FastAPI"],
        "keywords_missing": ["Kubernetes"],
    }
    base.update(overrides)
    return base


# ── /status ───────────────────────────────────────────────────────────────────


class TestStatusCommand(unittest.TestCase):
    def test_found_returns_status(self) -> None:
        sheet = _mock_sheet()
        with patch("matches_store.open_matches", return_value=sheet), \
             patch("matches_store.get_status", return_value="Notifié"):
            result = _run(["status", VALID_JOB_ID])
        assert result["ok"] is True
        assert "Notifié" in result["message"]

    def test_not_found_returns_error(self) -> None:
        sheet = _mock_sheet()
        with patch("matches_store.open_matches", return_value=sheet), \
             patch("matches_store.get_status", side_effect=KeyError(VALID_JOB_ID)):
            result = _run(["status", VALID_JOB_ID])
        assert result["ok"] is False

    def test_invalid_job_id_not_hex(self) -> None:
        result = _run(["status", SHORT_JOB_ID])
        assert result["ok"] is False

    def test_uppercase_job_id_rejected(self) -> None:
        result = _run(["status", UPPERCASE_JOB_ID])
        assert result["ok"] is False

    def test_output_is_valid_json(self) -> None:
        sheet = _mock_sheet()
        with patch("matches_store.open_matches", return_value=sheet), \
             patch("matches_store.get_status", return_value="Envoyé"):
            result = _run(["status", VALID_JOB_ID])
        assert "ok" in result
        assert "message" in result

    def test_empty_status_cell(self) -> None:
        sheet = _mock_sheet()
        with patch("matches_store.open_matches", return_value=sheet), \
             patch("matches_store.get_status", return_value=""):
            result = _run(["status", VALID_JOB_ID])
        assert result["ok"] is True


# ── /update ───────────────────────────────────────────────────────────────────


class TestUpdateCommand(unittest.TestCase):
    def test_valid_status_ok(self) -> None:
        sheet = _mock_sheet()
        with patch("matches_store.open_matches", return_value=sheet), \
             patch("matches_store.set_status"):
            result = _run(["update", VALID_JOB_ID, "Envoyé"])
        assert result["ok"] is True

    def test_invalid_status_error(self) -> None:
        sheet = _mock_sheet()
        with patch("matches_store.open_matches", return_value=sheet), \
             patch("matches_store.set_status", side_effect=ValueError("Approuvé")):
            result = _run(["update", VALID_JOB_ID, "Approuvé"])
        assert result["ok"] is False
        assert "Approuvé" in result["message"] or "invalide" in result["message"].lower()

    def test_not_found_error(self) -> None:
        sheet = _mock_sheet()
        with patch("matches_store.open_matches", return_value=sheet), \
             patch("matches_store.set_status", side_effect=KeyError(VALID_JOB_ID)):
            result = _run(["update", VALID_JOB_ID, "Envoyé"])
        assert result["ok"] is False

    def test_all_valid_statuses_accepted(self) -> None:
        import matches_store as ms
        for status in ms.VALID_STATUSES:
            with self.subTest(status=status):
                sheet = _mock_sheet()
                with patch("matches_store.open_matches", return_value=sheet), \
                     patch("matches_store.set_status"):
                    result = _run(["update", VALID_JOB_ID, status])
                assert result["ok"] is True

    def test_invalid_job_id_rejected(self) -> None:
        result = _run(["update", SHORT_JOB_ID, "Envoyé"])
        assert result["ok"] is False

    def test_missing_status_arg_error(self) -> None:
        result = _run(["update", VALID_JOB_ID])
        assert result["ok"] is False


# ── /note ─────────────────────────────────────────────────────────────────────


class TestNoteCommand(unittest.TestCase):
    def test_note_written(self) -> None:
        sheet = _mock_sheet()
        with patch("matches_store.open_matches", return_value=sheet), \
             patch("matches_store.set_note") as mock_note:
            result = _run(["note", VALID_JOB_ID, "Great company"])
        assert result["ok"] is True
        mock_note.assert_called_once_with(sheet, VALID_JOB_ID, "Great company")

    def test_not_found_error(self) -> None:
        sheet = _mock_sheet()
        with patch("matches_store.open_matches", return_value=sheet), \
             patch("matches_store.set_note", side_effect=KeyError(VALID_JOB_ID)):
            result = _run(["note", VALID_JOB_ID, "texte"])
        assert result["ok"] is False

    def test_multi_word_text_joined(self) -> None:
        sheet = _mock_sheet()
        with patch("matches_store.open_matches", return_value=sheet), \
             patch("matches_store.set_note") as mock_note:
            _run(["note", VALID_JOB_ID, "follow", "up", "next", "week"])
        mock_note.assert_called_once_with(sheet, VALID_JOB_ID, "follow up next week")

    def test_missing_text_arg_error(self) -> None:
        result = _run(["note", VALID_JOB_ID])
        assert result["ok"] is False

    def test_invalid_job_id_rejected(self) -> None:
        result = _run(["note", SHORT_JOB_ID, "texte"])
        assert result["ok"] is False


# ── mark_sent ─────────────────────────────────────────────────────────────────


class TestMarkSentCommand(unittest.TestCase):
    def test_calls_set_status_envoye(self) -> None:
        sheet = _mock_sheet()
        with patch("matches_store.open_matches", return_value=sheet), \
             patch("matches_store.set_status") as mock_set:
            result = _run(["mark_sent", VALID_JOB_ID])
        assert result["ok"] is True
        mock_set.assert_called_once_with(sheet, VALID_JOB_ID, "Envoyé")

    def test_not_found_error(self) -> None:
        sheet = _mock_sheet()
        with patch("matches_store.open_matches", return_value=sheet), \
             patch("matches_store.set_status", side_effect=KeyError(VALID_JOB_ID)):
            result = _run(["mark_sent", VALID_JOB_ID])
        assert result["ok"] is False

    def test_invalid_job_id_rejected(self) -> None:
        result = _run(["mark_sent", SHORT_JOB_ID])
        assert result["ok"] is False


# ── /feedback ─────────────────────────────────────────────────────────────────


class TestFeedbackCommand(unittest.TestCase):
    def test_add_calls_add_feedback(self) -> None:
        with patch("voice_profile_manager.add_feedback") as mock_add, \
             patch("voice_profile_manager.get_default_soul_path", return_value=Path("/tmp/soul.md")):
            result = _run(["feedback", "add", "n'utilise", "plus", "résultats-driven"])
        assert result["ok"] is True
        mock_add.assert_called_once()
        call_text = mock_add.call_args[0][1]
        assert "résultats-driven" in call_text

    def test_add_multi_word_text_joined(self) -> None:
        with patch("voice_profile_manager.add_feedback") as mock_add, \
             patch("voice_profile_manager.get_default_soul_path", return_value=Path("/tmp/soul.md")):
            _run(["feedback", "add", "word1", "word2", "word3"])
        assert mock_add.call_args[0][1] == "word1 word2 word3"

    def test_add_missing_text_returns_error(self) -> None:
        with patch("voice_profile_manager.get_default_soul_path", return_value=Path("/tmp/soul.md")):
            result = _run(["feedback", "add"])
        assert result["ok"] is False

    def test_remove_calls_remove_feedback(self) -> None:
        with patch("voice_profile_manager.remove_feedback") as mock_rm, \
             patch("voice_profile_manager.get_default_soul_path", return_value=Path("/tmp/soul.md")):
            result = _run(["feedback", "remove", "old", "entry"])
        assert result["ok"] is True
        mock_rm.assert_called_once()
        assert mock_rm.call_args[0][1] == "old entry"

    def test_remove_missing_text_returns_error(self) -> None:
        with patch("voice_profile_manager.get_default_soul_path", return_value=Path("/tmp/soul.md")):
            result = _run(["feedback", "remove"])
        assert result["ok"] is False

    def test_list_with_entries_returns_content(self) -> None:
        with patch("voice_profile_manager.list_feedback", return_value="- [2025-01-01] ADD: \"foo\""), \
             patch("voice_profile_manager.get_default_soul_path", return_value=Path("/tmp/soul.md")):
            result = _run(["feedback", "list"])
        assert result["ok"] is True
        assert "foo" in result["message"]

    def test_list_empty_returns_ok(self) -> None:
        with patch("voice_profile_manager.list_feedback", return_value=""), \
             patch("voice_profile_manager.get_default_soul_path", return_value=Path("/tmp/soul.md")):
            result = _run(["feedback", "list"])
        assert result["ok"] is True
        assert "vide" in result["message"]

    def test_unknown_subcmd_returns_error(self) -> None:
        with patch("voice_profile_manager.get_default_soul_path", return_value=Path("/tmp/soul.md")):
            result = _run(["feedback", "unknown"])
        assert result["ok"] is False

    def test_missing_subcmd_returns_error(self) -> None:
        result = _run(["feedback"])
        assert result["ok"] is False


# ── unknown / edge cases ──────────────────────────────────────────────────────


class TestUnknownCommand(unittest.TestCase):
    def test_unknown_command_returns_error(self) -> None:
        result = _run(["badcmd", VALID_JOB_ID])
        assert result["ok"] is False

    def test_no_args_returns_error(self) -> None:
        result = _run([])
        assert result["ok"] is False


# ── _flag_emoji ───────────────────────────────────────────────────────────────


class TestFlagEmoji(unittest.TestCase):
    def test_known_codes(self) -> None:
        assert _flag_emoji("CH") == "🇨🇭"
        assert _flag_emoji("FR") == "🇫🇷"
        assert _flag_emoji("DE") == "🇩🇪"
        assert _flag_emoji("GB") == "🇬🇧"
        assert _flag_emoji("US") == "🇺🇸"

    def test_case_insensitive(self) -> None:
        assert _flag_emoji("fr") == "🇫🇷"
        assert _flag_emoji("De") == "🇩🇪"

    def test_unknown_returns_globe(self) -> None:
        assert _flag_emoji("XX") == "🌍"
        assert _flag_emoji("") == "🌍"
        assert _flag_emoji("ZZZ") == "🌍"


# ── _url_hash ─────────────────────────────────────────────────────────────────


class TestUrlHash(unittest.TestCase):
    def test_returns_16_hex_chars(self) -> None:
        h = _url_hash("https://example.com/job/1")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_strips_query_and_fragment(self) -> None:
        base = "https://example.com/job/1"
        assert _url_hash(base) == _url_hash(base + "?ref=linkedin#apply")

    def test_same_url_stable(self) -> None:
        url = "https://jobs.example.com/engineer-42"
        assert _url_hash(url) == _url_hash(url)

    def test_different_urls_differ(self) -> None:
        assert _url_hash("https://a.com/1") != _url_hash("https://a.com/2")


# ── _escape_html ──────────────────────────────────────────────────────────────


class TestEscapeHtml(unittest.TestCase):
    def test_ampersand(self) -> None:
        assert _escape_html("R&D") == "R&amp;D"

    def test_angle_brackets(self) -> None:
        assert _escape_html("a<b>c") == "a&lt;b&gt;c"

    def test_no_change(self) -> None:
        assert _escape_html("Python FastAPI LangChain") == "Python FastAPI LangChain"


# ── _build_card_text ──────────────────────────────────────────────────────────


class TestBuildCardText(unittest.TestCase):
    def test_contains_match_rate(self) -> None:
        text = _build_card_text(_offer(match_rate=85))
        assert "85%" in text

    def test_contains_title_and_company(self) -> None:
        text = _build_card_text(_offer(title="ML Engineer", company="Acme"))
        assert "ML Engineer" in text
        assert "Acme" in text

    def test_flag_emoji_in_text(self) -> None:
        text = _build_card_text(_offer(pays="DE"))
        assert "🇩🇪" in text

    def test_keywords_matched_list(self) -> None:
        text = _build_card_text(_offer(keywords_matched=["Python", "FastAPI"]))
        assert "Python" in text
        assert "✅" in text

    def test_keywords_missing_list(self) -> None:
        text = _build_card_text(_offer(keywords_missing=["Docker"]))
        assert "Docker" in text
        assert "❌" in text

    def test_keywords_matched_string(self) -> None:
        text = _build_card_text(_offer(keywords_matched="Python, FastAPI"))
        assert "Python" in text

    def test_no_keywords_no_lines(self) -> None:
        text = _build_card_text(_offer(keywords_matched=[], keywords_missing=[]))
        assert "✅" not in text
        assert "❌" not in text

    def test_float_match_rate_normalized(self) -> None:
        text = _build_card_text(_offer(match_rate=0.85))
        assert "85%" in text

    def test_html_escaping_in_company(self) -> None:
        text = _build_card_text(_offer(company="R&D Corp"))
        assert "&amp;" in text
        assert "&" not in text.replace("&amp;", "")

    def test_fallback_skills_found_field(self) -> None:
        offer = _offer()
        del offer["keywords_matched"]
        offer["skills_found"] = ["Python"]
        text = _build_card_text(offer)
        assert "Python" in text


# ── _build_keyboard ───────────────────────────────────────────────────────────


class TestBuildKeyboard(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = _build_keyboard("abc123def456789a", "https://example.com/job")

    def test_has_inline_keyboard_key(self) -> None:
        assert "inline_keyboard" in self.kb

    def test_two_rows(self) -> None:
        assert len(self.kb["inline_keyboard"]) == 2

    def test_two_buttons_per_row(self) -> None:
        for row in self.kb["inline_keyboard"]:
            assert len(row) == 2

    def test_open_button_has_url(self) -> None:
        open_btn = self.kb["inline_keyboard"][0][0]
        assert open_btn["text"] == "🌐 Ouvrir"
        assert open_btn["url"] == "https://example.com/job"
        assert "callback_data" not in open_btn

    def test_generate_button_callback(self) -> None:
        btn = self.kb["inline_keyboard"][0][1]
        assert btn["text"] == "✅ Générer CV+Lettre"
        assert btn["callback_data"] == "generate:abc123def456789a"

    def test_ignore_button_callback(self) -> None:
        btn = self.kb["inline_keyboard"][1][0]
        assert btn["text"] == "❌ Ignorer"
        assert btn["callback_data"] == "ignore:abc123def456789a"

    def test_snooze_button_callback(self) -> None:
        btn = self.kb["inline_keyboard"][1][1]
        assert btn["text"] == "⏰ Plus tard"
        assert btn["callback_data"] == "snooze:abc123def456789a"

    def test_callback_data_fits_telegram_limit(self) -> None:
        for row in self.kb["inline_keyboard"]:
            for btn in row:
                if "callback_data" in btn:
                    assert len(btn["callback_data"].encode("utf-8")) <= 64


# ── send_match_card ───────────────────────────────────────────────────────────


class TestSendMatchCard(unittest.TestCase):
    def _make_ok_response(self) -> bytes:
        return json.dumps({"ok": True, "result": {"message_id": 42}}).encode("utf-8")

    def test_calls_telegram_api_sendmessage(self) -> None:
        import json as _json

        mock_resp = MagicMock()
        mock_resp.read.return_value = self._make_ok_response()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            send_match_card(_offer(), "fake-token", "12345")

        assert mock_open.called
        req = mock_open.call_args[0][0]
        assert "sendMessage" in req.full_url
        body = _json.loads(req.data.decode("utf-8"))
        assert body["chat_id"] == "12345"
        assert body["parse_mode"] == "HTML"
        assert "inline_keyboard" in body["reply_markup"]

    def test_raises_on_telegram_error(self) -> None:
        import json as _json

        error_body = _json.dumps({"ok": False, "description": "Bad Request"}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = error_body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with self.assertRaises(RuntimeError, msg="Bad Request"):
                send_match_card(_offer(), "fake-token", "12345")

    def test_callback_data_uses_url_hash(self) -> None:
        import json as _json

        captured: dict = {}
        mock_resp = MagicMock()
        mock_resp.read.return_value = self._make_ok_response()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        def capture(req, timeout=None):
            captured["body"] = _json.loads(req.data.decode("utf-8"))
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=capture):
            send_match_card(_offer(), "tok", "cid")

        kb = captured["body"]["reply_markup"]["inline_keyboard"]
        expected_hash = _url_hash(_offer()["url"])
        all_callbacks = [
            btn["callback_data"]
            for row in kb
            for btn in row
            if "callback_data" in btn
        ]
        for cb in all_callbacks:
            assert cb.endswith(expected_hash), f"{cb!r} does not end with {expected_hash!r}"


# ── send_digest ───────────────────────────────────────────────────────────────


def _digest_offer(match_rate: int | float, url_suffix: str = "") -> dict:
    return {
        "url": f"https://example.com/job/{url_suffix or match_rate}",
        "title": f"Job {match_rate}",
        "company": "Corp",
        "pays": "FR",
        "remote_type": "Remote",
        "match_rate": match_rate,
        "keywords_matched": ["Python"],
        "keywords_missing": [],
    }


def _ok_response() -> bytes:
    return json.dumps({"ok": True, "result": {"message_id": 1}}).encode("utf-8")


def _mock_urlopen(resp_bytes: bytes) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_bytes
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestSendDigest(unittest.TestCase):
    def test_empty_list_no_http_call(self) -> None:
        with patch("urllib.request.urlopen") as mock_open:
            result = send_digest([], "tok", "cid")
        assert result is None
        mock_open.assert_not_called()

    def test_all_above_80_no_http_call(self) -> None:
        offers = [_digest_offer(80), _digest_offer(92)]
        with patch("urllib.request.urlopen") as mock_open:
            result = send_digest(offers, "tok", "cid")
        assert result is None
        mock_open.assert_not_called()

    def test_ge80_offers_absent_from_digest(self) -> None:
        offers = [_digest_offer(85, "a"), _digest_offer(75, "b")]
        captured: dict = {}

        def capture(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _mock_urlopen(_ok_response())

        with patch("urllib.request.urlopen", side_effect=capture):
            send_digest(offers, "tok", "cid")

        text = captured["body"]["text"]
        assert "Job 85" not in text
        assert "Job 75" in text

    def test_offers_sorted_descending(self) -> None:
        offers = [_digest_offer(62, "a"), _digest_offer(78, "b"), _digest_offer(71, "c")]
        captured: dict = {}

        def capture(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _mock_urlopen(_ok_response())

        with patch("urllib.request.urlopen", side_effect=capture):
            send_digest(offers, "tok", "cid")

        text = captured["body"]["text"]
        pos_78 = text.index("78%")
        pos_71 = text.index("71%")
        pos_62 = text.index("62%")
        assert pos_78 < pos_71 < pos_62

    def test_header_format(self) -> None:
        offers = [_digest_offer(75, "a"), _digest_offer(68, "b")]
        captured: dict = {}

        def capture(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _mock_urlopen(_ok_response())

        with patch("urllib.request.urlopen", side_effect=capture):
            send_digest(offers, "tok", "cid")

        text = captured["body"]["text"]
        assert "🎯 THE HUNTER" in text
        assert "2 offres aujourd'hui" in text

    def test_numbered_list_and_inline_buttons(self) -> None:
        offers = [_digest_offer(75, "a"), _digest_offer(68, "b")]
        captured: dict = {}

        def capture(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _mock_urlopen(_ok_response())

        with patch("urllib.request.urlopen", side_effect=capture):
            send_digest(offers, "tok", "cid")

        text = captured["body"]["text"]
        assert "1." in text
        assert "2." in text

        kb = captured["body"]["reply_markup"]["inline_keyboard"]
        assert len(kb) == 2
        all_callbacks = [row[0]["callback_data"] for row in kb]
        for cb in all_callbacks:
            assert cb.startswith("detail:")
            assert len(cb.split(":")[1]) == 16

    def test_callback_data_uses_url_hash(self) -> None:
        offer = _digest_offer(70, "xyz")
        captured: dict = {}

        def capture(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _mock_urlopen(_ok_response())

        with patch("urllib.request.urlopen", side_effect=capture):
            send_digest([offer], "tok", "cid")

        expected_hash = _url_hash(offer["url"])
        kb = captured["body"]["reply_markup"]["inline_keyboard"]
        assert kb[0][0]["callback_data"] == f"detail:{expected_hash}"

    def test_callback_data_fits_telegram_limit(self) -> None:
        offer = _digest_offer(70, "some-very-long-job-url-slug-to-test-limit")
        captured: dict = {}

        def capture(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _mock_urlopen(_ok_response())

        with patch("urllib.request.urlopen", side_effect=capture):
            send_digest([offer], "tok", "cid")

        kb = captured["body"]["reply_markup"]["inline_keyboard"]
        for row in kb:
            for btn in row:
                if "callback_data" in btn:
                    assert len(btn["callback_data"].encode("utf-8")) <= 64

    def test_float_match_rate_normalized(self) -> None:
        offers = [_digest_offer(0.75), _digest_offer(0.82)]
        captured: dict = {}

        def capture(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _mock_urlopen(_ok_response())

        with patch("urllib.request.urlopen", side_effect=capture):
            send_digest(offers, "tok", "cid")

        text = captured["body"]["text"]
        assert "75%" in text
        assert "82%" not in text

    def test_exactly_one_http_call(self) -> None:
        offers = [_digest_offer(65, "a"), _digest_offer(72, "b")]
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(_ok_response())) as mock_open:
            send_digest(offers, "tok", "cid")
        assert mock_open.call_count == 1

    def test_raises_on_telegram_error(self) -> None:
        error_body = json.dumps({"ok": False, "description": "Forbidden"}).encode()
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(error_body)):
            with self.assertRaises(RuntimeError, msg="Forbidden"):
                send_digest([_digest_offer(70)], "tok", "cid")


if __name__ == "__main__":
    unittest.main()

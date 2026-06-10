"""Tests for Sprint 2 — matches_sheet.py and telegram_notifier callback functions."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from telegram_notifier import (
    _url_hash,
    answer_callback_query,
    edit_message_text,
    send_snooze_renotifications,
)
from matches_sheet import (
    COL_COMPANY,
    COL_LOCATION,
    COL_MATCH_RATE,
    COL_REMOTE,
    COL_SKILLS_FOUND,
    COL_SNOOZE_COUNT,
    COL_STATUS,
    COL_TITLE,
    COL_URL,
    STATUS_IGNORED,
    STATUS_SNOOZED,
    _url_hash_16,
    find_row_by_url_hash,
    get_snoozed_offers,
    increment_snooze,
    set_status,
)

KNOWN_URL = "https://example.com/jobs/engineer-99"
KNOWN_HASH = _url_hash(KNOWN_URL)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_row(
    url: str = "",
    title: str = "",
    company: str = "",
    location: str = "",
    remote: str = "Remote",
    match_rate: str = "",
    skills_found: str = "",
    status: str = "",
    snooze_count: str = "",
) -> list[str]:
    row = [""] * 15
    row[COL_TITLE - 1] = title
    row[COL_COMPANY - 1] = company
    row[COL_LOCATION - 1] = location
    row[COL_REMOTE - 1] = remote
    row[COL_URL - 1] = url
    row[COL_MATCH_RATE - 1] = match_rate
    row[COL_SKILLS_FOUND - 1] = skills_found
    row[COL_STATUS - 1] = status
    row[COL_SNOOZE_COUNT - 1] = snooze_count
    return row


def _make_sheet(*rows: list[str]) -> MagicMock:
    """Mock gspread Worksheet with a blank header row followed by data rows."""
    mock = MagicMock()
    mock.get_all_values.return_value = [[""] * 15] + list(rows)
    return mock


def _ok_response() -> bytes:
    return json.dumps({"ok": True, "result": {}}).encode()


def _error_response(desc: str) -> bytes:
    return json.dumps({"ok": False, "description": desc}).encode()


def _mock_urlopen(resp_bytes: bytes) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_bytes
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ── _url_hash_16 ──────────────────────────────────────────────────────────────


class TestUrlHash16(unittest.TestCase):
    def test_returns_16_chars(self) -> None:
        assert len(_url_hash_16(KNOWN_URL)) == 16

    def test_all_hex_chars(self) -> None:
        h = _url_hash_16(KNOWN_URL)
        assert all(c in "0123456789abcdef" for c in h)

    def test_matches_telegram_notifier_hash(self) -> None:
        # Critical cross-module contract: must be identical to _url_hash
        assert _url_hash_16(KNOWN_URL) == _url_hash(KNOWN_URL)

    def test_stable_deterministic(self) -> None:
        assert _url_hash_16(KNOWN_URL) == _url_hash_16(KNOWN_URL)

    def test_strips_query_and_fragment(self) -> None:
        assert _url_hash_16(KNOWN_URL) == _url_hash_16(KNOWN_URL + "?ref=x#top")


# ── find_row_by_url_hash ──────────────────────────────────────────────────────


class TestFindRowByUrlHash(unittest.TestCase):
    def test_finds_matching_row(self) -> None:
        row = _make_row(url=KNOWN_URL, title="Engineer", status="new")
        sheet = _make_sheet(row)
        idx, found = find_row_by_url_hash(KNOWN_HASH, sheet)
        assert idx == 2
        assert found is not None
        assert found[COL_URL - 1] == KNOWN_URL

    def test_returns_none_when_not_found(self) -> None:
        row = _make_row(url="https://other.example.com/job/42")
        sheet = _make_sheet(row)
        idx, found = find_row_by_url_hash(KNOWN_HASH, sheet)
        assert idx is None
        assert found is None

    def test_empty_sheet_returns_none(self) -> None:
        sheet = _make_sheet()
        idx, found = find_row_by_url_hash(KNOWN_HASH, sheet)
        assert idx is None
        assert found is None

    def test_skips_header_row(self) -> None:
        # Header has "url" in col 7 — its hash must not match KNOWN_HASH
        header = [""] * 15
        header[COL_URL - 1] = "url"
        data = _make_row(url=KNOWN_URL)
        mock = MagicMock()
        mock.get_all_values.return_value = [header, data]
        idx, _ = find_row_by_url_hash(KNOWN_HASH, mock)
        assert idx == 2  # data row, not header

    def test_row_shorter_than_url_col_is_skipped(self) -> None:
        short_row = ["col1", "col2"]  # only 2 columns, url is col 7
        sheet = _make_sheet(short_row)
        idx, _ = find_row_by_url_hash(KNOWN_HASH, sheet)
        assert idx is None


# ── set_status ────────────────────────────────────────────────────────────────


class TestSetStatus(unittest.TestCase):
    def test_calls_update_cell_with_correct_args(self) -> None:
        row = _make_row(url=KNOWN_URL, status="new")
        sheet = _make_sheet(row)
        result = set_status(KNOWN_HASH, STATUS_IGNORED, sheet)
        assert result is True
        sheet.update_cell.assert_called_once_with(2, COL_STATUS, STATUS_IGNORED)

    def test_hash_not_found_no_update(self) -> None:
        sheet = _make_sheet()
        result = set_status(KNOWN_HASH, STATUS_IGNORED, sheet)
        assert result is False
        sheet.update_cell.assert_not_called()

    def test_status_written_verbatim(self) -> None:
        row = _make_row(url=KNOWN_URL)
        sheet = _make_sheet(row)
        set_status(KNOWN_HASH, STATUS_SNOOZED, sheet)
        sheet.update_cell.assert_called_once_with(2, COL_STATUS, STATUS_SNOOZED)


# ── increment_snooze ──────────────────────────────────────────────────────────


class TestIncrementSnooze(unittest.TestCase):
    def _batch_args(self, row_idx: int, count: int, status: str) -> list[dict]:
        return [
            {"range": f"O{row_idx}", "values": [[str(count)]]},
            {"range": f"K{row_idx}", "values": [[status]]},
        ]

    def test_increments_from_empty_to_1(self) -> None:
        row = _make_row(url=KNOWN_URL, snooze_count="", status="new")
        sheet = _make_sheet(row)
        auto_ignored, count = increment_snooze(KNOWN_HASH, sheet)
        assert auto_ignored is False
        assert count == 1
        sheet.batch_update.assert_called_once_with(self._batch_args(2, 1, STATUS_SNOOZED))

    def test_increments_from_0_to_1(self) -> None:
        row = _make_row(url=KNOWN_URL, snooze_count="0")
        sheet = _make_sheet(row)
        auto_ignored, count = increment_snooze(KNOWN_HASH, sheet)
        assert auto_ignored is False
        assert count == 1

    def test_increments_from_1_to_2_auto_ignores(self) -> None:
        row = _make_row(url=KNOWN_URL, snooze_count="1")
        sheet = _make_sheet(row)
        auto_ignored, count = increment_snooze(KNOWN_HASH, sheet)
        assert auto_ignored is True
        assert count == 2
        sheet.batch_update.assert_called_once_with(self._batch_args(2, 2, STATUS_IGNORED))

    def test_already_at_2_still_auto_ignores(self) -> None:
        row = _make_row(url=KNOWN_URL, snooze_count="2")
        sheet = _make_sheet(row)
        auto_ignored, count = increment_snooze(KNOWN_HASH, sheet)
        assert auto_ignored is True
        assert count == 3
        sheet.batch_update.assert_called_once_with(self._batch_args(2, 3, STATUS_IGNORED))

    def test_hash_not_found_no_side_effects(self) -> None:
        sheet = _make_sheet()
        auto_ignored, count = increment_snooze(KNOWN_HASH, sheet)
        assert auto_ignored is False
        assert count == 0
        sheet.batch_update.assert_not_called()


# ── get_snoozed_offers ────────────────────────────────────────────────────────


class TestGetSnoozedOffers(unittest.TestCase):
    def test_returns_snoozed_rows_with_count_lt_2(self) -> None:
        row = _make_row(url=KNOWN_URL, title="AI Eng", company="Acme",
                        status=STATUS_SNOOZED, snooze_count="1")
        sheet = _make_sheet(row)
        offers = get_snoozed_offers(sheet)
        assert len(offers) == 1
        assert offers[0]["url"] == KNOWN_URL
        assert offers[0]["title"] == "AI Eng"

    def test_excludes_snooze_count_ge_2(self) -> None:
        row = _make_row(url=KNOWN_URL, status=STATUS_SNOOZED, snooze_count="2")
        sheet = _make_sheet(row)
        assert get_snoozed_offers(sheet) == []

    def test_excludes_other_statuses(self) -> None:
        row_ignored = _make_row(url=KNOWN_URL, status=STATUS_IGNORED, snooze_count="0")
        row_generated = _make_row(url="https://other.com/job", status="Généré", snooze_count="0")
        sheet = _make_sheet(row_ignored, row_generated)
        assert get_snoozed_offers(sheet) == []

    def test_empty_sheet_returns_empty_list(self) -> None:
        sheet = _make_sheet()
        assert get_snoozed_offers(sheet) == []

    def test_returned_dict_keys(self) -> None:
        row = _make_row(url=KNOWN_URL, title="T", company="C",
                        location="FR", remote="Remote", match_rate="75",
                        skills_found="Python", status=STATUS_SNOOZED, snooze_count="1")
        sheet = _make_sheet(row)
        offer = get_snoozed_offers(sheet)[0]
        assert offer["url"] == KNOWN_URL
        assert offer["title"] == "T"
        assert offer["company"] == "C"
        assert offer["pays"] == "FR"
        assert offer["remote_type"] == "Remote"
        assert offer["match_rate"] == "75"
        assert offer["keywords_matched"] == "Python"
        assert offer["keywords_missing"] == []


# ── answer_callback_query ─────────────────────────────────────────────────────


class TestAnswerCallbackQuery(unittest.TestCase):
    def test_calls_answer_callback_query_endpoint(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(_ok_response())) as mock_open:
            answer_callback_query("tok", "cqid-123", "✅ Done")
        req = mock_open.call_args[0][0]
        assert "answerCallbackQuery" in req.full_url

    def test_payload_contains_required_fields(self) -> None:
        captured: dict = {}

        def capture(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _mock_urlopen(_ok_response())

        with patch("urllib.request.urlopen", side_effect=capture):
            answer_callback_query("tok", "cqid-abc", "✅ Done")

        assert captured["body"]["callback_query_id"] == "cqid-abc"
        assert captured["body"]["text"] == "✅ Done"

    def test_raises_on_ok_false(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(_error_response("Query too old"))):
            with self.assertRaises(RuntimeError):
                answer_callback_query("tok", "cqid", "")

    def test_ok_true_no_exception(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(_ok_response())):
            result = answer_callback_query("tok", "cqid", "")
        assert result.get("ok") is True


# ── edit_message_text ─────────────────────────────────────────────────────────


class TestEditMessageText(unittest.TestCase):
    def test_calls_edit_message_text_endpoint(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(_ok_response())) as mock_open:
            edit_message_text("tok", "chat1", 42, "✅ Ignoré : Acme")
        req = mock_open.call_args[0][0]
        assert "editMessageText" in req.full_url

    def test_payload_contains_required_fields(self) -> None:
        captured: dict = {}

        def capture(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _mock_urlopen(_ok_response())

        with patch("urllib.request.urlopen", side_effect=capture):
            edit_message_text("tok", "chat1", 42, "hello")

        body = captured["body"]
        assert body["chat_id"] == "chat1"
        assert body["message_id"] == 42
        assert body["text"] == "hello"

    def test_reply_markup_included_when_provided(self) -> None:
        captured: dict = {}

        def capture(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _mock_urlopen(_ok_response())

        kb = {"inline_keyboard": []}
        with patch("urllib.request.urlopen", side_effect=capture):
            edit_message_text("tok", "chat1", 42, "text", reply_markup=kb)

        assert captured["body"]["reply_markup"] == kb

    def test_reply_markup_absent_when_none(self) -> None:
        captured: dict = {}

        def capture(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _mock_urlopen(_ok_response())

        with patch("urllib.request.urlopen", side_effect=capture):
            edit_message_text("tok", "chat1", 42, "text", reply_markup=None)

        assert "reply_markup" not in captured["body"]

    def test_raises_on_ok_false(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(_error_response("Message not modified"))):
            with self.assertRaises(RuntimeError):
                edit_message_text("tok", "chat1", 42, "same text")


# ── send_snooze_renotifications ───────────────────────────────────────────────


class TestSendSnoozeRenotifications(unittest.TestCase):
    def _snoozed_offers(self) -> list[dict]:
        return [
            {"url": "https://a.com/1", "title": "Job A", "company": "Corp A",
             "pays": "FR", "remote_type": "Remote", "match_rate": "75",
             "keywords_matched": "Python", "keywords_missing": []},
            {"url": "https://b.com/2", "title": "Job B", "company": "Corp B",
             "pays": "DE", "remote_type": "Remote", "match_rate": "68",
             "keywords_matched": "FastAPI", "keywords_missing": []},
        ]

    def test_calls_send_match_card_per_snoozed_offer(self) -> None:
        mock_sheet = MagicMock()
        with patch("matches_sheet.open_matches_sheet", return_value=mock_sheet):
            with patch("matches_sheet.get_snoozed_offers", return_value=self._snoozed_offers()):
                with patch("telegram_notifier.send_match_card") as mock_send:
                    count = send_snooze_renotifications("tok", "cid")
        assert count == 2
        assert mock_send.call_count == 2

    def test_no_calls_when_no_snoozed_offers(self) -> None:
        mock_sheet = MagicMock()
        with patch("matches_sheet.open_matches_sheet", return_value=mock_sheet):
            with patch("matches_sheet.get_snoozed_offers", return_value=[]):
                with patch("telegram_notifier.send_match_card") as mock_send:
                    count = send_snooze_renotifications("tok", "cid")
        assert count == 0
        mock_send.assert_not_called()

    def test_passes_correct_token_and_chat_id(self) -> None:
        mock_sheet = MagicMock()
        offers = self._snoozed_offers()[:1]
        with patch("matches_sheet.open_matches_sheet", return_value=mock_sheet):
            with patch("matches_sheet.get_snoozed_offers", return_value=offers):
                with patch("telegram_notifier.send_match_card") as mock_send:
                    send_snooze_renotifications("my-token", "my-chat")
        _, args, _ = mock_send.mock_calls[0]
        assert args[1] == "my-token"
        assert args[2] == "my-chat"

    def test_url_and_title_from_correct_columns(self) -> None:
        mock_sheet = MagicMock()
        offers = self._snoozed_offers()[:1]
        with patch("matches_sheet.open_matches_sheet", return_value=mock_sheet):
            with patch("matches_sheet.get_snoozed_offers", return_value=offers):
                with patch("telegram_notifier.send_match_card") as mock_send:
                    send_snooze_renotifications("tok", "cid")
        sent_offer = mock_send.call_args[0][0]
        assert sent_offer["url"] == "https://a.com/1"
        assert sent_offer["title"] == "Job A"


if __name__ == "__main__":
    unittest.main()

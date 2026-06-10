"""
Unit tests for telegram_notifier.py — matches_store fully mocked, no gspread calls.
"""
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


if __name__ == "__main__":
    unittest.main()

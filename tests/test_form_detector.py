"""
Unit tests for form_detector.py — no real Playwright, no live network.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import form_detector as fd


# ── _QUESTION_RE (pure regex, no async) ──────────────────────────────────────


class TestQuestionRegex(unittest.TestCase):
    def _match(self, label: str) -> bool:
        return bool(fd._QUESTION_RE.search(label))

    def test_question_mark_kept(self) -> None:
        assert self._match("Years of experience?")

    def test_how_prefix_kept(self) -> None:
        assert self._match("How many years of experience do you have")

    def test_why_prefix_kept(self) -> None:
        assert self._match("Why do you want to work here")

    def test_what_prefix_kept(self) -> None:
        assert self._match("What is your salary expectation")

    def test_describe_prefix_kept(self) -> None:
        assert self._match("Describe a project where you used LLMs")

    def test_tell_prefix_kept(self) -> None:
        assert self._match("Tell us about yourself")

    def test_explain_prefix_kept(self) -> None:
        assert self._match("Explain your experience with Python")

    def test_do_you_prefix_kept(self) -> None:
        assert self._match("Do you have the right to work in Switzerland")

    def test_plain_label_filtered(self) -> None:
        assert not self._match("First name")

    def test_cover_letter_label_filtered(self) -> None:
        assert not self._match("Cover letter")

    def test_lowercase_how_kept(self) -> None:
        assert self._match("how many years experience")

    def test_how_in_middle_no_question_mark_filtered(self) -> None:
        assert not self._match("Technical know how")

    def test_label_with_inline_question_mark_kept(self) -> None:
        assert self._match("Start date?")


# ── detect_form_questions (sync wrapper) ──────────────────────────────────────


class TestDetectFormQuestionsWrapper(unittest.TestCase):
    def test_returns_list_on_success(self) -> None:
        expected = [{"id": "q1", "label": "Why apply?", "type": "textarea"}]
        with patch.object(fd, "_detect_async", new_callable=AsyncMock) as mock:
            mock.return_value = expected
            result = fd.detect_form_questions("https://example.com/apply")
        assert result == expected

    def test_returns_empty_list_on_exception(self) -> None:
        with patch.object(fd, "_detect_async", new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("Network error")
            result = fd.detect_form_questions("https://example.com/apply")
        assert result == []

    def test_import_error_returns_empty(self) -> None:
        with patch.object(fd, "_detect_async", new_callable=AsyncMock) as mock:
            mock.side_effect = ImportError("No module named 'playwright'")
            result = fd.detect_form_questions("https://example.com/apply")
        assert result == []

    def test_url_passed_to_detect_async(self) -> None:
        target = "https://jobs.example.com/apply/123"
        with patch.object(fd, "_detect_async", new_callable=AsyncMock) as mock:
            mock.return_value = []
            fd.detect_form_questions(target)
        mock.assert_called_once_with(target)

    def test_empty_page_returns_empty_list(self) -> None:
        with patch.object(fd, "_detect_async", new_callable=AsyncMock) as mock:
            mock.return_value = []
            result = fd.detect_form_questions("https://example.com")
        assert result == []

    def test_multiple_questions_returned(self) -> None:
        questions = [
            {"id": "q1", "label": "Why this role?", "type": "textarea"},
            {"id": "q2", "label": "What is your salary expectation?", "type": "text"},
        ]
        with patch.object(fd, "_detect_async", new_callable=AsyncMock) as mock:
            mock.return_value = questions
            result = fd.detect_form_questions("https://example.com")
        assert len(result) == 2
        assert result[0]["type"] == "textarea"
        assert result[1]["type"] == "text"

    def test_result_dicts_have_id_label_type(self) -> None:
        questions = [{"id": "q1", "label": "Describe a project?", "type": "textarea"}]
        with patch.object(fd, "_detect_async", new_callable=AsyncMock) as mock:
            mock.return_value = questions
            result = fd.detect_form_questions("https://example.com")
        assert "id" in result[0]
        assert "label" in result[0]
        assert "type" in result[0]


if __name__ == "__main__":
    unittest.main()

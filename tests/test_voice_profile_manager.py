"""
Unit tests for voice_profile_manager.py — pure file I/O, zero LLM calls.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import voice_profile_manager as vpm

# ── fixtures ──────────────────────────────────────────────────────────────────

SOUL_WITH_SECTION = """\
## Ton positionnement

Senior AI / Fullstack developer.

**Feedback log :**
- [2025-06-15] ADD: "n'utilise plus résultats-driven"
- [2025-06-18] ADD: "j'aime la formule Here's what I'd bring"

## Autres notes

Rien pour l'instant.
"""

SOUL_WITHOUT_SECTION = """\
## Ton positionnement

Senior AI / Fullstack developer.

## Autres notes

Rien pour l'instant.
"""

SOUL_EMPTY = ""

DATE = "2026-06-10"


def _write_tmp(content: str) -> Path:
    fd, name = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return Path(name)


# ── TestAddFeedback ───────────────────────────────────────────────────────────


class TestAddFeedback(unittest.TestCase):
    def setUp(self) -> None:
        self._paths: list[Path] = []

    def tearDown(self) -> None:
        for p in self._paths:
            p.unlink(missing_ok=True)

    def _tmp(self, content: str) -> Path:
        p = _write_tmp(content)
        self._paths.append(p)
        return p

    def test_entry_appears_in_file_after_add(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        vpm.add_feedback(p, "new feedback", DATE)
        assert f'- [{DATE}] ADD: "new feedback"' in p.read_text(encoding="utf-8")

    def test_entry_format_is_correct(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        vpm.add_feedback(p, "my text", DATE)
        assert f'- [{DATE}] ADD: "my text"' in p.read_text(encoding="utf-8")

    def test_entry_appended_not_prepended(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        vpm.add_feedback(p, "new entry", DATE)
        content = p.read_text(encoding="utf-8")
        existing_idx = content.index("résultats-driven")
        new_idx = content.index("new entry")
        assert new_idx > existing_idx

    def test_multiple_adds_accumulate(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        vpm.add_feedback(p, "first", DATE)
        vpm.add_feedback(p, "second", DATE)
        content = p.read_text(encoding="utf-8")
        assert '"first"' in content
        assert '"second"' in content

    def test_other_sections_not_touched(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        before = p.read_text(encoding="utf-8")
        vpm.add_feedback(p, "x", DATE)
        after = p.read_text(encoding="utf-8")
        assert "Senior AI / Fullstack developer." in after
        assert "Rien pour l'instant." in after
        # Other sections unchanged
        assert before.split("**Feedback log :**")[0] == after.split("**Feedback log :**")[0]

    def test_section_created_when_absent(self) -> None:
        p = self._tmp(SOUL_WITHOUT_SECTION)
        vpm.add_feedback(p, "new entry", DATE)
        content = p.read_text(encoding="utf-8")
        assert "**Feedback log :**" in content
        assert '"new entry"' in content

    def test_add_to_empty_file(self) -> None:
        p = self._tmp(SOUL_EMPTY)
        vpm.add_feedback(p, "solo entry", DATE)
        content = p.read_text(encoding="utf-8")
        assert "**Feedback log :**" in content
        assert '"solo entry"' in content

    def test_returns_none(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        result = vpm.add_feedback(p, "x", DATE)
        assert result is None

    def test_special_chars_in_text(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        special = 'Available immediately → remplacé par "Disponible immédiatement"'
        vpm.add_feedback(p, special, DATE)
        assert special in p.read_text(encoding="utf-8")

    def test_duplicate_text_adds_two_lines(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        vpm.add_feedback(p, "same text", DATE)
        vpm.add_feedback(p, "same text", DATE)
        content = p.read_text(encoding="utf-8")
        assert content.count('"same text"') == 2

    def test_file_not_overwritten_existing_content_preserved(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        vpm.add_feedback(p, "extra", DATE)
        content = p.read_text(encoding="utf-8")
        assert "résultats-driven" in content
        assert "Here's what I'd bring" in content

    def test_encoding_utf8(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        vpm.add_feedback(p, "été à París ñoño", DATE)
        content = p.read_text(encoding="utf-8")
        assert "été à París ñoño" in content


# ── TestRemoveFeedback ────────────────────────────────────────────────────────


class TestRemoveFeedback(unittest.TestCase):
    def setUp(self) -> None:
        self._paths: list[Path] = []

    def tearDown(self) -> None:
        for p in self._paths:
            p.unlink(missing_ok=True)

    def _tmp(self, content: str) -> Path:
        p = _write_tmp(content)
        self._paths.append(p)
        return p

    def test_matching_entry_removed(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        vpm.remove_feedback(p, "résultats-driven")
        assert "résultats-driven" not in p.read_text(encoding="utf-8")

    def test_non_matching_entries_preserved(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        vpm.remove_feedback(p, "résultats-driven")
        assert "Here's what I'd bring" in p.read_text(encoding="utf-8")

    def test_other_sections_not_touched(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        before = p.read_text(encoding="utf-8")
        vpm.remove_feedback(p, "résultats-driven")
        after = p.read_text(encoding="utf-8")
        assert "Senior AI / Fullstack developer." in after
        assert "Rien pour l'instant." in after

    def test_returns_none(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        result = vpm.remove_feedback(p, "résultats-driven")
        assert result is None

    def test_remove_nonexistent_text_no_crash(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        vpm.remove_feedback(p, "this does not exist")

    def test_remove_nonexistent_text_file_unchanged(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        before = p.read_text(encoding="utf-8")
        vpm.remove_feedback(p, "this does not exist")
        assert p.read_text(encoding="utf-8") == before

    def test_remove_only_first_match(self) -> None:
        content = (
            "**Feedback log :**\n"
            '- [2025-01-01] ADD: "duplicate"\n'
            '- [2025-01-02] ADD: "duplicate"\n'
        )
        p = self._tmp(content)
        vpm.remove_feedback(p, "duplicate")
        remaining = p.read_text(encoding="utf-8")
        assert remaining.count('"duplicate"') == 1

    def test_remove_from_absent_section_no_crash(self) -> None:
        p = self._tmp(SOUL_WITHOUT_SECTION)
        vpm.remove_feedback(p, "anything")

    def test_text_match_is_substring(self) -> None:
        # Partial substring match triggers removal (documented behaviour)
        p = self._tmp(SOUL_WITH_SECTION)
        vpm.remove_feedback(p, "résultats")
        assert "résultats-driven" not in p.read_text(encoding="utf-8")

    def test_section_header_remains_after_full_removal(self) -> None:
        content = "**Feedback log :**\n- [2025-01-01] ADD: \"only entry\"\n"
        p = self._tmp(content)
        vpm.remove_feedback(p, "only entry")
        assert "**Feedback log :**" in p.read_text(encoding="utf-8")


# ── TestListFeedback ──────────────────────────────────────────────────────────


class TestListFeedback(unittest.TestCase):
    def setUp(self) -> None:
        self._paths: list[Path] = []

    def tearDown(self) -> None:
        for p in self._paths:
            p.unlink(missing_ok=True)

    def _tmp(self, content: str) -> Path:
        p = _write_tmp(content)
        self._paths.append(p)
        return p

    def test_returns_string(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        assert isinstance(vpm.list_feedback(p), str)

    def test_returns_section_entries(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        result = vpm.list_feedback(p)
        assert "résultats-driven" in result
        assert "Here's what I'd bring" in result

    def test_does_not_include_other_sections(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        result = vpm.list_feedback(p)
        assert "Senior AI" not in result
        assert "Rien pour l'instant" not in result

    def test_returns_empty_when_section_absent(self) -> None:
        p = self._tmp(SOUL_WITHOUT_SECTION)
        assert vpm.list_feedback(p) == ""

    def test_returns_empty_on_empty_file(self) -> None:
        p = self._tmp(SOUL_EMPTY)
        assert vpm.list_feedback(p) == ""

    def test_content_matches_after_add(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        vpm.add_feedback(p, "round trip", DATE)
        result = vpm.list_feedback(p)
        assert "round trip" in result

    def test_returns_empty_when_no_entries(self) -> None:
        content = "**Feedback log :**\n"
        p = self._tmp(content)
        assert vpm.list_feedback(p) == ""

    def test_list_after_remove_reflects_change(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        vpm.remove_feedback(p, "résultats-driven")
        result = vpm.list_feedback(p)
        assert "résultats-driven" not in result
        assert "Here's what I'd bring" in result


# ── TestFileIntegrity ─────────────────────────────────────────────────────────


class TestFileIntegrity(unittest.TestCase):
    def setUp(self) -> None:
        self._paths: list[Path] = []

    def tearDown(self) -> None:
        for p in self._paths:
            p.unlink(missing_ok=True)

    def _tmp(self, content: str) -> Path:
        p = _write_tmp(content)
        self._paths.append(p)
        return p

    def test_add_does_not_truncate_file(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        before_len = len(p.read_bytes())
        vpm.add_feedback(p, "new entry", DATE)
        assert len(p.read_bytes()) > before_len

    def test_remove_does_not_truncate_other_sections(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        before = p.read_text(encoding="utf-8")
        before_prefix = before.split("**Feedback log :**")[0]
        vpm.remove_feedback(p, "résultats-driven")
        after = p.read_text(encoding="utf-8")
        after_prefix = after.split("**Feedback log :**")[0]
        assert before_prefix == after_prefix

    def test_add_remove_roundtrip(self) -> None:
        p = self._tmp(SOUL_WITH_SECTION)
        before_entries = vpm.list_feedback(p)
        vpm.add_feedback(p, "temporary entry", DATE)
        vpm.remove_feedback(p, "temporary entry")
        after_entries = vpm.list_feedback(p)
        assert before_entries == after_entries

    def test_no_llm_import(self) -> None:
        llm_libs = {"openai", "anthropic", "langchain", "cohere", "mistralai"}
        import importlib
        for lib in llm_libs:
            assert lib not in sys.modules, f"{lib} must not be imported by voice_profile_manager"


if __name__ == "__main__":
    unittest.main()

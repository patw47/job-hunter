"""
Unit tests for layer2_scoring.py — no live API calls, no gspread.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import layer2_scoring as l2


# ── Fixtures ──────────────────────────────────────────────────────────────────


SKILLS_FIXTURE: set[str] = {"Python", "LLM", "FastAPI", "RAG", "Docker", "GenAI"}

ALIASES_FIXTURE: dict[str, list[str]] = {
    "Gen AI": ["GenAI"],
    "gen_ai": ["GenAI"],
    "Vector DB": ["Qdrant", "Pinecone"],
}


def _offer(**overrides: str) -> dict:
    base: dict = {
        "url": "https://example.com/job/1",
        "title": "AI Engineer",
        "company": "Acme",
        "location": "Remote",
        "job_type": "remote",
        "source": "indeed",
    }
    base.update(overrides)
    return base


def _mock_sheet() -> MagicMock:
    sheet = MagicMock()
    return sheet


# ── Return shape ──────────────────────────────────────────────────────────────


class TestReturnShape(unittest.TestCase):
    def test_returns_tuple_of_three(self) -> None:
        result = l2.compute_match_rate("Python developer", SKILLS_FIXTURE, {})
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_rate_is_float(self) -> None:
        rate, _, _ = l2.compute_match_rate("Python developer", SKILLS_FIXTURE, {})
        assert isinstance(rate, float)

    def test_matched_is_list(self) -> None:
        _, matched, _ = l2.compute_match_rate("Python developer", SKILLS_FIXTURE, {})
        assert isinstance(matched, list)

    def test_missing_is_list(self) -> None:
        _, _, missing = l2.compute_match_rate("Python developer", SKILLS_FIXTURE, {})
        assert isinstance(missing, list)


# ── Acceptance criteria ────────────────────────────────────────────────────────


class TestAcceptanceCriteria(unittest.TestCase):
    """3 offer tests and all explicit ACs from the sprint spec."""

    # AC: match rate calculé correctement sur 3 offres tests

    def test_ac1_offer_all_skills_100_percent(self) -> None:
        rate, matched, missing = l2.compute_match_rate(
            "Python LLM FastAPI RAG Docker",
            {"Python", "LLM", "FastAPI", "RAG", "Docker"},
            {},
        )
        assert rate == 100.0
        assert set(matched) == {"Python", "LLM", "FastAPI", "RAG", "Docker"}
        assert missing == []

    def test_ac1_offer_partial_match_at_60_percent(self) -> None:
        rate, matched, missing = l2.compute_match_rate(
            "Python LLM FastAPI Java Cobol",
            {"Python", "LLM", "FastAPI"},
            {},
        )
        assert rate == 60.0
        assert set(matched) == {"Python", "LLM", "FastAPI"}
        assert len(missing) == 2

    def test_ac1_offer_mostly_unmatched_20_percent(self) -> None:
        rate, matched, missing = l2.compute_match_rate(
            "Python Java Cobol Ruby Golang",
            {"Python"},
            {},
        )
        assert rate == 20.0
        assert matched == ["Python"]
        assert len(missing) == 4

    # AC: alias résolu correctement ("Gen AI" compte comme match si "GenAI" est dans SKILLS_MASTER)

    def test_ac2_gen_ai_matches_when_genai_in_master(self) -> None:
        rate, matched, _ = l2.compute_match_rate(
            "Gen AI Python",
            {"GenAI", "Python"},
            {"Gen AI": ["GenAI"]},
        )
        assert rate == 100.0
        assert "GenAI" in matched

    def test_ac2_alias_chain_gen_ai_to_llm(self) -> None:
        # Transitive chain: "Gen AI" → "GenAI" → "LLM" (in skills_master)
        rate, matched, _ = l2.compute_match_rate(
            "Gen AI Python",
            {"LLM", "Python"},
            {"Gen AI": ["GenAI"], "GenAI": ["LLM"]},
        )
        assert "LLM" in matched
        assert rate > 0.0

    # AC: offre ≥ 60% écrite dans MATCHES avec toutes les colonnes

    def test_ac3_above_threshold_writes_to_matches(self) -> None:
        sheet = _mock_sheet()
        result = l2.write_match_if_qualified(
            _offer(), 75.0, ["Python", "LLM", "FastAPI"], sheet, "09.06.2026"
        )
        assert result is True
        sheet.append_row.assert_called_once()

    def test_ac3_written_row_has_all_columns(self) -> None:
        sheet = _mock_sheet()
        l2.write_match_if_qualified(
            _offer(), 75.0, ["Python", "LLM"], sheet, "09.06.2026"
        )
        row = sheet.append_row.call_args[0][0]
        assert len(row) == 14
        assert row[2] == "AI Engineer"   # title
        assert row[3] == "Acme"          # company
        assert row[7] == "indeed"        # source
        assert row[8] == "75.0"          # match_rate
        assert row[9] == "Python,LLM"    # skills_found
        assert row[10] == "new"          # status

    # AC: offre < 60% NON écrite dans MATCHES

    def test_ac4_below_threshold_not_written(self) -> None:
        sheet = _mock_sheet()
        result = l2.write_match_if_qualified(
            _offer(), 40.0, [], sheet, "09.06.2026"
        )
        assert result is False
        sheet.append_row.assert_not_called()

    def test_ac4_exactly_at_threshold_qualifies(self) -> None:
        sheet = _mock_sheet()
        result = l2.write_match_if_qualified(
            _offer(), 60.0, ["Python", "LLM", "FastAPI"], sheet, "09.06.2026"
        )
        assert result is True

    # AC: rescanner une offre < 60% → is_duplicate() = True

    def test_ac5_below_threshold_hash_still_in_scanned_hashes(self) -> None:
        from deduplication import compute_hash, is_duplicate

        url = "https://example.com/job/below-threshold"
        h = compute_hash(url)
        mock_scanned = MagicMock()
        mock_scanned.col_values.return_value = ["sha256", h]
        # Hash was written by dedup step regardless of match rate
        assert is_duplicate(h, mock_scanned) is True


# ── Rate formula ──────────────────────────────────────────────────────────────


class TestRateFormula(unittest.TestCase):
    def test_all_tokens_matched_100(self) -> None:
        rate, _, missing = l2.compute_match_rate(
            "Python FastAPI Docker", {"Python", "FastAPI", "Docker"}, {}
        )
        assert rate == 100.0
        assert missing == []

    def test_no_tokens_matched_0(self) -> None:
        rate, matched, _ = l2.compute_match_rate(
            "Java Ruby Cobol", {"Python", "LLM"}, {}
        )
        assert rate == 0.0
        assert matched == []

    def test_half_matched_50(self) -> None:
        rate, _, _ = l2.compute_match_rate("Python Java", {"Python"}, {})
        assert rate == 50.0

    def test_rate_bounded_0_to_100(self) -> None:
        rate, _, _ = l2.compute_match_rate("Python Java LLM", {"Python", "LLM"}, {})
        assert 0.0 <= rate <= 100.0

    def test_duplicate_token_counts_twice(self) -> None:
        # "Python" appears twice → 2 matched, denominator = 2 → 100%
        rate, matched, missing = l2.compute_match_rate(
            "Python Python", {"Python"}, {}
        )
        assert rate == 100.0
        assert len(matched) == 2
        assert missing == []


# ── Tokenisation ──────────────────────────────────────────────────────────────


class TestTokenisation(unittest.TestCase):
    def test_case_insensitive_skill_lookup(self) -> None:
        _, matched, _ = l2.compute_match_rate(
            "PYTHON fastapi", {"Python", "FastAPI"}, {}
        )
        assert set(matched) == {"Python", "FastAPI"}

    def test_punctuation_split(self) -> None:
        _, matched, _ = l2.compute_match_rate(
            "Python, FastAPI.", {"Python", "FastAPI"}, {}
        )
        assert "Python" in matched
        assert "FastAPI" in matched

    def test_whitespace_variants(self) -> None:
        _, matched, _ = l2.compute_match_rate(
            "Python\nLLM\tFastAPI", {"Python", "LLM", "FastAPI"}, {}
        )
        assert set(matched) == {"Python", "LLM", "FastAPI"}

    def test_empty_text_returns_zero(self) -> None:
        rate, matched, missing = l2.compute_match_rate("", {"Python"}, {})
        assert rate == 0.0
        assert matched == []
        assert missing == []

    def test_whitespace_only_returns_zero(self) -> None:
        rate, matched, missing = l2.compute_match_rate("   ", {"Python"}, {})
        assert rate == 0.0
        assert matched == []
        assert missing == []

    def test_punctuation_only_returns_zero(self) -> None:
        rate, matched, missing = l2.compute_match_rate("!!! ...", {"Python"}, {})
        assert rate == 0.0
        assert matched == []


# ── Alias resolution ──────────────────────────────────────────────────────────


class TestAliasResolution(unittest.TestCase):
    def test_single_token_alias_resolved(self) -> None:
        _, matched, _ = l2.compute_match_rate(
            "GenAI Python", {"LLM", "Python"}, {"GenAI": ["LLM"]}
        )
        assert "LLM" in matched

    def test_multi_word_alias_bigram(self) -> None:
        _, matched, _ = l2.compute_match_rate(
            "Gen AI Python", {"GenAI", "Python"}, {"Gen AI": ["GenAI"]}
        )
        assert "GenAI" in matched

    def test_alias_synonym_not_in_master_goes_to_missing(self) -> None:
        _, matched, missing = l2.compute_match_rate(
            "UnknownAlias Python",
            {"Python"},
            {"UnknownAlias": ["NotInMaster"]},
        )
        assert "Python" in matched
        assert "unknownalias" in missing

    def test_alias_cycle_no_infinite_loop(self) -> None:
        # A → B, B → A — must terminate without error
        rate, _, _ = l2.compute_match_rate(
            "SkillA SkillB",
            set(),
            {"SkillA": ["SkillB"], "SkillB": ["SkillA"]},
        )
        assert rate == 0.0

    def test_empty_alias_table_no_crash(self) -> None:
        rate, matched, _ = l2.compute_match_rate("Python", {"Python"}, {})
        assert matched == ["Python"]


# ── matched / missing content ─────────────────────────────────────────────────


class TestMatchedMissingContent(unittest.TestCase):
    def test_partition_is_complete(self) -> None:
        # matched + missing must account for every offer keyword
        _, matched, missing = l2.compute_match_rate("Python Java", {"Python"}, {})
        assert len(matched) + len(missing) == 2

    def test_matched_returns_canonical_casing(self) -> None:
        _, matched, _ = l2.compute_match_rate("python", {"Python"}, {})
        assert matched == ["Python"]

    def test_missing_contains_unrecognized_word(self) -> None:
        _, _, missing = l2.compute_match_rate("python cobol", {"Python"}, {})
        assert "cobol" in missing


# ── Edge cases ────────────────────────────────────────────────────────────────


class TestEdgeCases(unittest.TestCase):
    def test_empty_skills_master(self) -> None:
        rate, matched, missing = l2.compute_match_rate("Python LLM", set(), {})
        assert rate == 0.0
        assert matched == []
        assert len(missing) == 2

    def test_empty_alias_table(self) -> None:
        rate, matched, _ = l2.compute_match_rate("Python", {"Python"}, {})
        assert matched == ["Python"]

    def test_offer_with_only_unrecognized_words(self) -> None:
        rate, matched, _ = l2.compute_match_rate("Java Ruby Cobol", {"Python"}, {})
        assert rate == 0.0
        assert matched == []


# ── write_match_if_qualified ──────────────────────────────────────────────────


class TestWriteMatchIfQualified(unittest.TestCase):
    def _base_offer(self) -> dict:
        return {
            "url": "https://example.com/job/1",
            "title": "AI Engineer",
            "company": "Acme",
            "location": "Remote",
            "job_type": "remote",
            "source": "indeed",
        }

    def test_above_threshold_returns_true(self) -> None:
        sheet = _mock_sheet()
        assert l2.write_match_if_qualified(
            self._base_offer(), 75.0, ["Python"], sheet, "09.06.2026"
        ) is True

    def test_above_threshold_calls_append_row(self) -> None:
        sheet = _mock_sheet()
        l2.write_match_if_qualified(
            self._base_offer(), 75.0, ["Python"], sheet, "09.06.2026"
        )
        sheet.append_row.assert_called_once()

    def test_below_threshold_returns_false(self) -> None:
        sheet = _mock_sheet()
        assert l2.write_match_if_qualified(
            self._base_offer(), 59.9, [], sheet, "09.06.2026"
        ) is False

    def test_below_threshold_no_write(self) -> None:
        sheet = _mock_sheet()
        l2.write_match_if_qualified(
            self._base_offer(), 40.0, [], sheet, "09.06.2026"
        )
        sheet.append_row.assert_not_called()

    def test_exactly_at_threshold_qualifies(self) -> None:
        sheet = _mock_sheet()
        assert l2.write_match_if_qualified(
            self._base_offer(), 60.0, ["Python", "LLM", "FastAPI"], sheet, "09.06.2026"
        ) is True

    def test_row_has_14_columns(self) -> None:
        sheet = _mock_sheet()
        l2.write_match_if_qualified(
            self._base_offer(), 75.0, ["Python", "LLM"], sheet, "09.06.2026"
        )
        row = sheet.append_row.call_args[0][0]
        assert len(row) == 14

    def test_skills_found_joined_with_comma(self) -> None:
        sheet = _mock_sheet()
        l2.write_match_if_qualified(
            self._base_offer(), 75.0, ["Python", "LLM", "FastAPI"], sheet, "09.06.2026"
        )
        row = sheet.append_row.call_args[0][0]
        assert row[9] == "Python,LLM,FastAPI"

    def test_match_rate_formatted_as_string(self) -> None:
        sheet = _mock_sheet()
        l2.write_match_if_qualified(
            self._base_offer(), 83.3, ["Python"], sheet, "09.06.2026"
        )
        row = sheet.append_row.call_args[0][0]
        assert row[8] == "83.3"

    def test_status_is_new(self) -> None:
        sheet = _mock_sheet()
        l2.write_match_if_qualified(
            self._base_offer(), 75.0, ["Python"], sheet, "09.06.2026"
        )
        row = sheet.append_row.call_args[0][0]
        assert row[10] == "new"

    def test_remote_job_type_mapped_to_yes(self) -> None:
        sheet = _mock_sheet()
        l2.write_match_if_qualified(
            self._base_offer(), 75.0, ["Python"], sheet, "09.06.2026"
        )
        row = sheet.append_row.call_args[0][0]
        assert row[5] == "yes"

    def test_hybrid_job_type_mapped(self) -> None:
        sheet = _mock_sheet()
        offer = self._base_offer()
        offer["job_type"] = "hybrid"
        l2.write_match_if_qualified(offer, 75.0, ["Python"], sheet, "09.06.2026")
        row = sheet.append_row.call_args[0][0]
        assert row[5] == "hybrid"

    def test_threshold_constant_is_60(self) -> None:
        assert l2.MATCH_THRESHOLD == 60.0


if __name__ == "__main__":
    unittest.main()

"""
Unit tests for skills_loader.py — no live file system except tmp files.
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

import skills_loader as sl


_MINIMAL_MD = """\
# Skills Master — Match Rate Keywords

## Catégories de compétences

### AI / LLM / Agents

- LLM, Large Language Model
- RAG, Retrieval Augmented Generation
- Vector database, Embeddings

### Python & Backend

- Python, FastAPI

## Alias table

| Terme offre | Synonymes reconnus |
|-------------|-------------------|
| Vector DB | Qdrant, Pinecone, pgvector |
| GenAI | LLM, Large Language Model |
"""

_CATEGORIES_ONLY_MD = """\
## Catégories de compétences

### AI / LLM / Agents

- LLM, Large Language Model
"""


def _write_tmp(content: str) -> Path:
    """Écrit content dans un fichier temporaire et retourne son chemin."""
    fd, name = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return Path(name)


# ── load_keywords ─────────────────────────────────────────────────────────────


class TestLoadKeywords(unittest.TestCase):
    def setUp(self) -> None:
        self._path = _write_tmp(_MINIMAL_MD)

    def tearDown(self) -> None:
        self._path.unlink(missing_ok=True)

    def test_returns_set(self) -> None:
        result = sl.load_keywords(self._path)
        assert isinstance(result, set)

    def test_all_bullet_tokens_present(self) -> None:
        result = sl.load_keywords(self._path)
        expected = {
            "LLM", "Large Language Model",
            "RAG", "Retrieval Augmented Generation",
            "Vector database", "Embeddings",
            "Python", "FastAPI",
        }
        assert expected.issubset(result)

    def test_tokens_stripped_of_whitespace(self) -> None:
        result = sl.load_keywords(self._path)
        for token in result:
            assert token == token.strip()

    def test_section_headers_not_in_keywords(self) -> None:
        result = sl.load_keywords(self._path)
        assert "AI / LLM / Agents" not in result
        assert "Python & Backend" not in result

    def test_alias_table_rows_not_in_keywords(self) -> None:
        result = sl.load_keywords(self._path)
        assert "Vector DB" not in result
        assert "GenAI" not in result

    def test_no_alias_section_still_loads(self) -> None:
        path = _write_tmp(_CATEGORIES_ONLY_MD)
        try:
            result = sl.load_keywords(path)
            assert len(result) > 0
        finally:
            path.unlink(missing_ok=True)


# ── load_aliases ──────────────────────────────────────────────────────────────


class TestLoadAliases(unittest.TestCase):
    def setUp(self) -> None:
        self._path = _write_tmp(_MINIMAL_MD)

    def tearDown(self) -> None:
        self._path.unlink(missing_ok=True)

    def test_returns_dict(self) -> None:
        result = sl.load_aliases(self._path)
        assert isinstance(result, dict)

    def test_known_alias_key_present(self) -> None:
        result = sl.load_aliases(self._path)
        assert "Vector DB" in result

    def test_alias_synonyms_are_list(self) -> None:
        result = sl.load_aliases(self._path)
        for value in result.values():
            assert isinstance(value, list)

    def test_vector_db_synonyms(self) -> None:
        result = sl.load_aliases(self._path)
        assert result["Vector DB"] == ["Qdrant", "Pinecone", "pgvector"]

    def test_genai_synonyms(self) -> None:
        result = sl.load_aliases(self._path)
        assert result["GenAI"] == ["LLM", "Large Language Model"]

    def test_synonym_count_matches_fixture(self) -> None:
        result = sl.load_aliases(self._path)
        assert len(result) == 2

    def test_separator_row_excluded(self) -> None:
        result = sl.load_aliases(self._path)
        assert not any(k.startswith("-") for k in result)

    def test_no_alias_section_returns_empty_dict(self) -> None:
        path = _write_tmp(_CATEGORIES_ONLY_MD)
        try:
            result = sl.load_aliases(path)
            assert result == {}
        finally:
            path.unlink(missing_ok=True)


# ── file errors ───────────────────────────────────────────────────────────────


class TestFileErrors(unittest.TestCase):
    def test_load_keywords_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            sl.load_keywords(Path("/nonexistent/path/SKILLS_MASTER.md"))

    def test_load_aliases_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            sl.load_aliases(Path("/nonexistent/path/SKILLS_MASTER.md"))

    def test_load_keywords_empty_file(self) -> None:
        path = _write_tmp("")
        try:
            result = sl.load_keywords(path)
            assert result == set()
        finally:
            path.unlink(missing_ok=True)

    def test_load_aliases_empty_file(self) -> None:
        path = _write_tmp("")
        try:
            result = sl.load_aliases(path)
            assert result == {}
        finally:
            path.unlink(missing_ok=True)


# ── resolve_alias ─────────────────────────────────────────────────────────────


class TestResolveAlias(unittest.TestCase):
    _ALIAS_DICT: dict[str, list[str]] = {
        "Vector DB": ["Qdrant", "Pinecone", "pgvector"],
        "GenAI": ["LLM", "Large Language Model"],
    }

    def test_known_term_returns_synonyms(self) -> None:
        result = sl.resolve_alias("Vector DB", self._ALIAS_DICT)
        assert result == ["Qdrant", "Pinecone", "pgvector"]

    def test_unknown_term_returns_empty_list(self) -> None:
        result = sl.resolve_alias("Unknown Term", self._ALIAS_DICT)
        assert result == []

    def test_case_sensitive_miss(self) -> None:
        result = sl.resolve_alias("vector db", self._ALIAS_DICT)
        assert result == []

    def test_returns_list_not_none(self) -> None:
        result = sl.resolve_alias("AnythingAtAll", self._ALIAS_DICT)
        assert result is not None
        assert isinstance(result, list)

    def test_empty_alias_dict(self) -> None:
        result = sl.resolve_alias("Vector DB", {})
        assert result == []


if __name__ == "__main__":
    unittest.main()

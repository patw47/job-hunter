"""Tests for pipeline_writer.persist_offer_details (offers store for /generate)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipeline_writer as pw


class TestPersistOfferDetails(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["OFFERS_STORE_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        os.environ.pop("OFFERS_STORE_DIR", None)
        self._tmp.cleanup()

    def _offer(self, **overrides) -> dict:
        base = {
            "job_id": "abc123def456",
            "title": "AI Engineer",
            "company": "Acme",
            "location": "Remote",
            "url": "https://example.com/job/1",
            "source": "linkedin",
            "match_rate": "70.0",
            "skills_found": "LLM,RAG",
            "description": "Build RAG pipelines with Python and Claude.",
            "status": "new",
        }
        base.update(overrides)
        return base

    def test_writes_one_file_per_offer(self) -> None:
        saved = pw.persist_offer_details([self._offer(), self._offer(job_id="zzz999")])
        assert saved == 2
        assert (Path(self._tmp.name) / "abc123def456.json").exists()
        assert (Path(self._tmp.name) / "zzz999.json").exists()

    def test_description_round_trip(self) -> None:
        pw.persist_offer_details([self._offer()])
        detail = json.loads((Path(self._tmp.name) / "abc123def456.json").read_text())
        assert detail["description"] == "Build RAG pipelines with Python and Claude."
        assert detail["title"] == "AI Engineer"

    def test_status_field_not_persisted(self) -> None:
        pw.persist_offer_details([self._offer()])
        detail = json.loads((Path(self._tmp.name) / "abc123def456.json").read_text())
        assert "status" not in detail

    def test_offer_without_job_id_skipped(self) -> None:
        saved = pw.persist_offer_details([self._offer(job_id="")])
        assert saved == 0
        assert list(Path(self._tmp.name).iterdir()) == []

    def test_empty_list(self) -> None:
        assert pw.persist_offer_details([]) == 0

    def test_missing_description_defaults_empty(self) -> None:
        offer = self._offer()
        del offer["description"]
        pw.persist_offer_details([offer])
        detail = json.loads((Path(self._tmp.name) / "abc123def456.json").read_text())
        assert detail["description"] == ""


class TestScoreOfferTarget(unittest.TestCase):
    """match_rate = distinct skills found / 10, capped at 1.0."""

    def setUp(self) -> None:
        import hunter_server as hs
        self.hs = hs
        self.keywords = {f"Skill{i}" for i in range(20)}

    def _offer_with(self, n: int) -> dict:
        return {"title": "", "description": " ".join(f"skill{i}" for i in range(n))}

    def test_six_skills_is_sixty_percent(self) -> None:
        rate, found = self.hs._score_offer(self._offer_with(6), self.keywords, {})
        assert rate == 0.6
        assert len(found) == 6

    def test_ten_skills_is_full_match(self) -> None:
        rate, _ = self.hs._score_offer(self._offer_with(10), self.keywords, {})
        assert rate == 1.0

    def test_capped_at_one(self) -> None:
        rate, found = self.hs._score_offer(self._offer_with(15), self.keywords, {})
        assert rate == 1.0
        assert len(found) == 15

    def test_zero_skills(self) -> None:
        rate, found = self.hs._score_offer({"title": "Accountant", "description": "ledger"}, self.keywords, {})
        assert rate == 0.0
        assert found == []

    def test_description_drives_score(self) -> None:
        offer = {"title": "Engineer", "description": "skill1 skill2 skill3 skill4 skill5 skill6"}
        rate, _ = self.hs._score_offer(offer, self.keywords, {})
        assert rate == 0.6


if __name__ == "__main__":
    unittest.main()

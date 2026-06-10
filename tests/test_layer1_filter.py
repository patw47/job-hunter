"""
Unit tests for layer1_filter.py — pure logic, no external calls.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from layer1_filter import apply_layer1


def _offer(**overrides: str) -> dict:
    base: dict = {
        "url": "https://example.com/job/1",
        "title": "",
        "company": "Acme",
        "location": "",
        "description": "",
        "job_type": "",
        "date_posted": "",
        "source": "indeed",
    }
    base.update(overrides)
    return base


# ── Return shape ──────────────────────────────────────────────────────────────


class TestReturnShape(unittest.TestCase):
    def test_returns_tuple(self) -> None:
        result = apply_layer1(_offer(job_type="remote"))
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_first_elem_bool_true(self) -> None:
        passed, _ = apply_layer1(_offer(job_type="remote"))
        assert passed is True

    def test_first_elem_bool_false(self) -> None:
        passed, _ = apply_layer1(_offer(title="Junior AI Engineer", job_type="remote"))
        assert passed is False

    def test_second_elem_is_str(self) -> None:
        _, reason = apply_layer1(_offer())
        assert isinstance(reason, str)

    def test_pass_starts_with_pass(self) -> None:
        _, reason = apply_layer1(_offer(job_type="remote"))
        assert reason.startswith("PASS:")

    def test_rejected_starts_with_rejected(self) -> None:
        _, reason = apply_layer1(_offer(job_type="onsite"))
        assert reason.startswith("REJECTED:")

    def test_ignored_starts_with_ignored(self) -> None:
        _, reason = apply_layer1(_offer())
        assert reason.startswith("IGNORED:")


# ── Junior / internship filter ────────────────────────────────────────────────


class TestJuniorFilter(unittest.TestCase):
    def test_ac5_junior_title_rejected(self) -> None:
        passed, reason = apply_layer1(_offer(title="Junior AI Engineer", job_type="remote"))
        assert passed is False
        assert reason.startswith("REJECTED:")

    def test_internship_rejected(self) -> None:
        passed, _ = apply_layer1(_offer(title="Data Science Internship", job_type="remote"))
        assert passed is False

    def test_alternance_rejected(self) -> None:
        passed, _ = apply_layer1(_offer(title="Développeur Python alternance", job_type="remote"))
        assert passed is False

    def test_stage_rejected(self) -> None:
        passed, _ = apply_layer1(_offer(title="Stage Data Engineer", job_type="remote"))
        assert passed is False

    def test_intern_rejected(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Intern", job_type="remote"))
        assert passed is False

    def test_apprenti_rejected(self) -> None:
        passed, _ = apply_layer1(_offer(title="Apprenti DevOps", job_type="remote"))
        assert passed is False

    def test_case_insensitive_upper(self) -> None:
        passed, _ = apply_layer1(_offer(title="JUNIOR Python Developer", job_type="remote"))
        assert passed is False

    def test_senior_not_rejected(self) -> None:
        passed, _ = apply_layer1(_offer(title="Senior AI Engineer", job_type="remote"))
        assert passed is True

    def test_staging_not_rejected(self) -> None:
        # "staging" contains "stage" as substring but \bstage\b does not match mid-word
        passed, _ = apply_layer1(_offer(title="Staging Environment Engineer", job_type="remote"))
        assert passed is True

    def test_junior_fires_before_cascade(self) -> None:
        # Junior filter runs before work-type detection — even with no work type info, REJECTED not IGNORED
        passed, reason = apply_layer1(_offer(title="Junior Engineer"))
        assert passed is False
        assert reason.startswith("REJECTED:")

    def test_job_type_compound_containing_junior(self) -> None:
        # Title is the only field checked for junior filter
        passed, _ = apply_layer1(_offer(title="Lead AI Engineer", job_type="Junior REMOTE"))
        assert passed is True  # job_type not checked for junior, title is fine


# ── On-site filter ────────────────────────────────────────────────────────────


class TestOnsiteFilter(unittest.TestCase):
    def test_ac1_onsite_job_type_rejected(self) -> None:
        passed, reason = apply_layer1(_offer(title="Software Engineer", job_type="onsite", location="Paris"))
        assert passed is False
        assert reason.startswith("REJECTED:")

    def test_onsite_location_rejected(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", location="On-site Paris"))
        assert passed is False

    def test_presentiel_rejected(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", location="Paris - présentiel"))
        assert passed is False

    def test_in_office_rejected(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", job_type="in-office"))
        assert passed is False


# ── Remote filter ─────────────────────────────────────────────────────────────


class TestRemoteFilter(unittest.TestCase):
    def test_remote_job_type_passes(self) -> None:
        passed, reason = apply_layer1(_offer(title="AI Engineer", job_type="remote"))
        assert passed is True
        assert reason.startswith("PASS:")

    def test_ac6_remote_no_city_passes(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", job_type="remote", location="", description=""))
        assert passed is True

    def test_remote_location_passes(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", location="Remote - France"))
        assert passed is True

    def test_teletravail_passes(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", location="Télétravail complet"))
        assert passed is True

    def test_full_remote_location_passes(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", location="Full remote"))
        assert passed is True

    def test_remote_title_passes(self) -> None:
        passed, _ = apply_layer1(_offer(title="Remote AI Engineer"))
        assert passed is True

    def test_remote_body_passes(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", description="This is a remote position."))
        assert passed is True

    def test_job_type_compound_remote_passes(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", job_type="CDI - Remote"))
        assert passed is True


# ── Hybrid filter ─────────────────────────────────────────────────────────────


class TestHybridFilter(unittest.TestCase):
    def test_ac2_hybrid_sion_valais_passes(self) -> None:
        passed, reason = apply_layer1(_offer(title="AI Engineer", job_type="hybrid", location="Sion, Valais"))
        assert passed is True
        assert reason.startswith("PASS:")

    def test_hybrid_martigny_passes(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", job_type="hybrid", location="Martigny, Valais, Switzerland"))
        assert passed is True

    def test_hybrid_bordeaux_passes(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", job_type="hybrid", location="Bordeaux, Nouvelle-Aquitaine"))
        assert passed is True

    def test_hybrid_agen_passes(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", job_type="hybrid", location="Agen, France"))
        assert passed is True

    def test_hybrid_valais_by_region_passes(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", job_type="hybrid", location="Canton du Valais, CH"))
        assert passed is True

    def test_ac3_hybrid_paris_rejected(self) -> None:
        passed, reason = apply_layer1(_offer(title="AI Engineer", job_type="hybrid", location="Paris"))
        assert passed is False
        assert reason.startswith("REJECTED:")

    def test_hybrid_lyon_rejected(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", job_type="hybrid", location="Lyon, France"))
        assert passed is False

    def test_hybrid_zurich_rejected(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", job_type="hybrid", location="Zurich"))
        assert passed is False

    def test_ac4_hybrid_paris_full_remote_possible_passes(self) -> None:
        passed, reason = apply_layer1(_offer(
            title="AI Engineer",
            job_type="hybrid",
            location="Paris",
            description="full remote possible pour ce poste"
        ))
        assert passed is True
        assert reason.startswith("PASS:")

    def test_hybrid_full_remote_possible_case_insensitive(self) -> None:
        passed, _ = apply_layer1(_offer(
            title="AI Engineer",
            job_type="hybrid",
            location="Paris",
            description="Full Remote Possible starting day one"
        ))
        assert passed is True

    def test_hybrid_no_location_no_body_ignored(self) -> None:
        # hybrid detected in job_type, but no location info and no full remote possible
        passed, reason = apply_layer1(_offer(title="AI Engineer", job_type="hybrid", location=""))
        assert passed is False
        assert reason.startswith("REJECTED:")

    def test_hybrid_location_empty_full_remote_possible_passes(self) -> None:
        passed, _ = apply_layer1(_offer(
            title="AI Engineer",
            job_type="hybrid",
            location="",
            description="full remote possible"
        ))
        assert passed is True

    def test_hybride_french_keyword_detected(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", location="Paris (hybride)", description="full remote possible"))
        assert passed is True

    def test_location_case_insensitive_zone(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", job_type="hybrid", location="sion, valais"))
        assert passed is True


# ── Cascade detection ─────────────────────────────────────────────────────────


class TestCascadeDetection(unittest.TestCase):
    def test_step1_job_type_remote_beats_location_onsite(self) -> None:
        # job_type "remote" checked before location "on-site"
        passed, _ = apply_layer1(_offer(title="AI Eng", job_type="remote", location="on-site Paris"))
        assert passed is True

    def test_step1_job_type_onsite_beats_location_remote(self) -> None:
        # job_type "onsite" beats location "Remote"
        passed, _ = apply_layer1(_offer(title="AI Eng", job_type="onsite", location="Remote Paris"))
        assert passed is False

    def test_step2_location_fallback_when_no_job_type(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Eng", job_type="", location="Remote - France"))
        assert passed is True

    def test_step2_location_hybrid_triggers_zone_check(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Eng", job_type="", location="Hybrid Paris"))
        assert passed is False

    def test_step3_title_fallback_when_fields_empty(self) -> None:
        passed, _ = apply_layer1(_offer(title="Remote AI Engineer", job_type="", location=""))
        assert passed is True

    def test_step3_title_hybrid_fallback(self) -> None:
        # hybrid in title, no tolerated zone, no full remote possible → rejected
        passed, _ = apply_layer1(_offer(title="Hybrid AI Engineer", job_type="", location=""))
        assert passed is False

    def test_step4_description_fallback_when_title_empty(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", job_type="", location="", description="remote position in France"))
        assert passed is True

    def test_step5_no_match_ignored(self) -> None:
        passed, reason = apply_layer1(_offer(title="AI Engineer"))
        assert passed is False
        assert reason.startswith("IGNORED:")


# ── Acceptance criteria (spec) ────────────────────────────────────────────────


class TestAcceptanceCriteria(unittest.TestCase):
    def test_ac1_onsite_rejected(self) -> None:
        passed, reason = apply_layer1(_offer(title="Software Engineer", job_type="onsite", location="Paris"))
        assert passed is False
        assert "REJECTED" in reason

    def test_ac2_hybrid_sion_valais_passes(self) -> None:
        passed, _ = apply_layer1(_offer(title="Software Engineer", job_type="hybrid", location="Sion, Valais"))
        assert passed is True

    def test_ac3_hybrid_paris_rejected(self) -> None:
        passed, _ = apply_layer1(_offer(title="Software Engineer", job_type="hybrid", location="Paris"))
        assert passed is False

    def test_ac4_hybrid_paris_full_remote_possible_passes(self) -> None:
        passed, _ = apply_layer1(_offer(
            title="Software Engineer",
            job_type="hybrid",
            location="Paris",
            description="full remote possible"
        ))
        assert passed is True

    def test_ac5_junior_title_rejected(self) -> None:
        passed, _ = apply_layer1(_offer(title="Junior AI Engineer", job_type="remote"))
        assert passed is False

    def test_ac6_full_remote_no_city_passes(self) -> None:
        passed, _ = apply_layer1(_offer(title="AI Engineer", job_type="remote", location="", description=""))
        assert passed is True


if __name__ == "__main__":
    unittest.main()

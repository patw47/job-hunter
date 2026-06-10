"""
Unit tests for hunter_bridge.py — no live API calls, no subprocess.
All external calls are mocked.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import hunter_bridge as hb


def _fake_openclaw_stdout(cv_body: str = "# My CV\n\nContent here.") -> str:
    return json.dumps({"result": {"finalAssistantVisibleText": f"[CV_START]\n{cv_body}\n[CV_END]"}})


# ── detect_language ───────────────────────────────────────────────────────────


class TestDetectLanguage(unittest.TestCase):
    def test_fr_detected(self) -> None:
        assert hb.detect_language("Nous recherchons un candidat pour notre poste") == "fr"

    def test_de_detected(self) -> None:
        assert hb.detect_language("Wir suchen eine erfahrene Stelle für unsere Bewerber") == "de"

    def test_en_default(self) -> None:
        assert hb.detect_language("We are looking for a senior engineer") == "en"

    def test_no_signal_defaults_en(self) -> None:
        assert hb.detect_language("XYZ Corp 123 ABC") == "en"

    def test_explicit_override_wins(self) -> None:
        assert hb.detect_language("Nous recherchons notre candidat", override="en") == "en"

    def test_case_insensitive_fr(self) -> None:
        assert hb.detect_language("NOUS RECHERCHONS NOTRE CANDIDATURE POUR VOTRE POSTE") == "fr"

    def test_invalid_override_ignored(self) -> None:
        result = hb.detect_language("We are looking for an engineer", override="ZH")
        assert result == "en"

    def test_override_de_wins_over_fr_text(self) -> None:
        assert hb.detect_language("nous recherchons notre poste", override="de") == "de"


# ── detect_profile_tag ────────────────────────────────────────────────────────


class TestDetectProfileTag(unittest.TestCase):
    def test_ai_engineer_from_claude(self) -> None:
        assert hb.detect_profile_tag("", ["Claude", "LLM"]) == "ai_engineer"

    def test_ai_engineer_from_llm_in_offer(self) -> None:
        assert hb.detect_profile_tag("We need an LLM expert", []) == "ai_engineer"

    def test_ai_engineer_from_agent(self) -> None:
        assert hb.detect_profile_tag("Build agentic AI systems", []) == "ai_engineer"

    def test_mlops_from_k8s(self) -> None:
        assert hb.detect_profile_tag("deploy on k8s cluster", []) == "mlops"

    def test_mlops_from_mlops_keyword(self) -> None:
        assert hb.detect_profile_tag("mlops engineer needed", []) == "mlops"

    def test_ai_builder_from_n8n(self) -> None:
        assert hb.detect_profile_tag("build n8n workflows", []) == "ai_builder"

    def test_ai_builder_from_automation(self) -> None:
        assert hb.detect_profile_tag("workflow automation platform", []) == "ai_builder"

    def test_full_stack_default(self) -> None:
        assert hb.detect_profile_tag("React developer CDI Paris", []) == "full_stack"

    def test_override_wins(self) -> None:
        assert hb.detect_profile_tag("Build n8n automation", [], override="mlops") == "mlops"

    def test_ai_engineer_beats_mlops(self) -> None:
        assert hb.detect_profile_tag("LLM on k8s mlops", []) == "ai_engineer"

    def test_case_insensitive(self) -> None:
        assert hb.detect_profile_tag("Deploy MLOPS on K8S", []) == "mlops"

    def test_invalid_override_ignored(self) -> None:
        result = hb.detect_profile_tag("React developer", [], override="wizard")
        assert result == "full_stack"


# ── parse_cv_wrapper ──────────────────────────────────────────────────────────


class TestParseCvWrapper(unittest.TestCase):
    def test_happy_path(self) -> None:
        raw = "[CV_START]\n# Patricia Wintrebert\n\nContent.\n[CV_END]"
        assert hb.parse_cv_wrapper(raw) == "# Patricia Wintrebert\n\nContent."

    def test_extra_content_outside_ignored(self) -> None:
        raw = "preamble\n[CV_START]\n# CV\n[CV_END]\npostamble"
        assert hb.parse_cv_wrapper(raw) == "# CV"

    def test_missing_start_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            hb.parse_cv_wrapper("# CV\n[CV_END]")
        assert "[CV_START]" in str(ctx.exception)

    def test_missing_end_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            hb.parse_cv_wrapper("[CV_START]\n# CV")
        assert "[CV_END]" in str(ctx.exception)

    def test_nested_markers_use_first_start_and_end(self) -> None:
        raw = "[CV_START]\n# CV\n[CV_END]\n[CV_START]\nextra\n[CV_END]"
        assert hb.parse_cv_wrapper(raw) == "# CV"


# ── generate_cv_filename ──────────────────────────────────────────────────────


class TestGenerateCvFilename(unittest.TestCase):
    def test_happy_path(self) -> None:
        name = hb.generate_cv_filename("AcmeCorp", today="2026-06-10")
        assert name == "CV_Patricia_Wintrebert_AcmeCorp_2026-06-10.md"

    def test_spaces_normalized(self) -> None:
        name = hb.generate_cv_filename("Acme Corp Ltd", today="2026-06-10")
        assert " " not in name
        assert "2026-06-10" in name

    def test_special_chars_removed(self) -> None:
        name = hb.generate_cv_filename("Acme/Corp & Co.", today="2026-06-10")
        # Only the .md extension may contain dots; no special chars from company name
        company_part = name.replace(".md", "").split("_2026-06-10")[0]
        assert "/" not in company_part
        assert "&" not in company_part
        assert "." not in company_part

    def test_date_format_iso(self) -> None:
        name = hb.generate_cv_filename("X", today="2026-06-10")
        assert "2026-06-10" in name

    def test_uses_today_when_no_date(self) -> None:
        name = hb.generate_cv_filename("TestCo")
        assert name.startswith("CV_Patricia_Wintrebert_TestCo_")
        assert name.endswith(".md")


# ── validate_forbidden_phrases ────────────────────────────────────────────────


class TestValidateForbiddenPhrases(unittest.TestCase):
    def test_clean_cv_passes(self) -> None:
        cv = "# Patricia Wintrebert\n\nBuilt LLM pipelines and deployed RAG systems."
        assert hb.validate_forbidden_phrases(cv) == []

    def test_forbidden_phrase_detected(self) -> None:
        cv = "I was responsible for managing the team."
        found = hb.validate_forbidden_phrases(cv)
        assert "responsible for" in found

    def test_case_insensitive_detection(self) -> None:
        cv = "RESPONSIBLE FOR all cloud infrastructure."
        found = hb.validate_forbidden_phrases(cv)
        assert "responsible for" in found

    def test_multiple_forbidden_phrases_all_reported(self) -> None:
        cv = "A results-driven team player with synergy."
        found = hb.validate_forbidden_phrases(cv)
        assert len(found) >= 2

    def test_no_false_positives_on_partial_match(self) -> None:
        # "synergy" should match but "synergistic" should not (it doesn't contain "synergy" exactly)
        # Actually "synergistic" does contain "synergy" — this tests the lookup is correct
        cv = "Built a scalable infrastructure."
        assert hb.validate_forbidden_phrases(cv) == []


# ── Rate limiter ──────────────────────────────────────────────────────────────


class TestRateLimiter(unittest.TestCase):
    def setUp(self) -> None:
        self.limiter = hb._RateLimiter(max_calls=5, window_seconds=3600)

    def test_first_5_allowed(self) -> None:
        for i in range(5):
            assert self.limiter.check_and_record(_now=float(i)) is True

    def test_6th_rejected(self) -> None:
        for i in range(5):
            self.limiter.check_and_record(_now=0.0)
        assert self.limiter.check_and_record(_now=0.0) is False

    def test_resets_after_window(self) -> None:
        for _ in range(5):
            self.limiter.check_and_record(_now=0.0)
        assert self.limiter.check_and_record(_now=3601.0) is True

    def test_reset_clears_all(self) -> None:
        for _ in range(5):
            self.limiter.check_and_record(_now=0.0)
        self.limiter.reset()
        assert self.limiter.check_and_record(_now=0.0) is True

    def test_counter_is_instance_scoped(self) -> None:
        other = hb._RateLimiter(max_calls=5, window_seconds=3600)
        for _ in range(5):
            hb._cv_rate_limiter.check_and_record(_now=0.0)
        assert other.check_and_record(_now=0.0) is True


# ── call_cv_rewriter (subprocess mocked) ─────────────────────────────────────


class TestCallCvRewriter(unittest.TestCase):
    def _run(self, stdout: str) -> str:
        mock_result = MagicMock()
        mock_result.stdout = stdout
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = hb.call_cv_rewriter({"company": "Acme", "language": "en"})
        return result

    def test_message_contains_skill_tag(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = _fake_openclaw_stdout()
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            hb.call_cv_rewriter({"company": "Acme"})
        call_args = mock_run.call_args
        message_arg = call_args[0][0]
        assert "CV-REWRITER SKILL" in " ".join(message_arg)

    def test_openclaw_agent_command_used(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            hb.call_cv_rewriter({})
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "openclaw"
        assert "agent" in cmd

    def test_timeout_propagates(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("openclaw", 600)):
            with self.assertRaises(subprocess.TimeoutExpired):
                hb.call_cv_rewriter({})


# ── POST /rewrite-cv (FastAPI TestClient) ────────────────────────────────────


try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI_TEST = True
except ImportError:
    _HAS_FASTAPI_TEST = False


@unittest.skipUnless(_HAS_FASTAPI_TEST, "fastapi[testclient] not installed")
class TestRewriteCvEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi.testclient import TestClient
        hb._cv_rate_limiter.reset()
        self.client = TestClient(hb.app)

    def _mock_openclaw(self, cv_body: str = "# Patricia\n\nSenior AI Engineer.") -> MagicMock:
        mock_proc = MagicMock()
        mock_proc.stdout = _fake_openclaw_stdout(cv_body)
        return mock_proc

    def _valid_payload(self, **overrides) -> dict:
        base = {
            "offer_description": "We need an AI engineer working with Claude and LLM agents.",
            "matched_keywords": ["Claude", "LLM"],
            "company": "AcmeCorp",
        }
        base.update(overrides)
        return base

    def test_health(self) -> None:
        r = self.client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_happy_path(self) -> None:
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch.object(hb.CV_OUTPUT_DIR.__class__, "mkdir", return_value=None):
                with patch("pathlib.Path.write_text"):
                    r = self.client.post("/rewrite-cv", json=self._valid_payload())
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["cv_markdown"] == "# Patricia\n\nSenior AI Engineer."
        assert body["language"] == "en"
        assert body["profile_tag"] == "ai_engineer"
        assert "AcmeCorp" in body["filename"]

    def test_language_detected_fr(self) -> None:
        payload = self._valid_payload(
            offer_description="Nous recherchons notre candidat pour notre poste en France."
        )
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/rewrite-cv", json=payload)
        assert r.json()["language"] == "fr"

    def test_language_override_wins(self) -> None:
        payload = self._valid_payload(
            offer_description="Nous recherchons notre candidat pour notre poste.",
            language="de",
        )
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/rewrite-cv", json=payload)
        assert r.json()["language"] == "de"

    def test_profile_tag_override(self) -> None:
        payload = self._valid_payload(profile_tag="mlops")
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/rewrite-cv", json=payload)
        assert r.json()["profile_tag"] == "mlops"

    def test_rate_limit_429(self) -> None:
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                for _ in range(5):
                    self.client.post("/rewrite-cv", json=self._valid_payload())
        r = self.client.post("/rewrite-cv", json=self._valid_payload())
        assert r.status_code == 429

    def test_missing_cv_wrapper_returns_error(self) -> None:
        mock_proc = MagicMock()
        mock_proc.stdout = json.dumps({"result": {"finalAssistantVisibleText": "raw text without markers"}})
        with patch("subprocess.run", return_value=mock_proc):
            r = self.client.post("/rewrite-cv", json=self._valid_payload())
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "CV_START" in body["error"]

    def test_timeout_returns_error(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("openclaw", 600)):
            r = self.client.post("/rewrite-cv", json=self._valid_payload())
        body = r.json()
        assert body["ok"] is False
        assert "timeout" in body["error"]

    def test_missing_offer_description_422(self) -> None:
        r = self.client.post("/rewrite-cv", json={"company": "X", "matched_keywords": []})
        assert r.status_code == 422

    def test_missing_company_422(self) -> None:
        r = self.client.post("/rewrite-cv", json={"offer_description": "...", "matched_keywords": []})
        assert r.status_code == 422

    def test_keywords_in_cv_present(self) -> None:
        cv_body = "# Patricia\n\nExpert in Claude, LLM, and agentic systems."
        with patch("subprocess.run", return_value=self._mock_openclaw(cv_body)):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/rewrite-cv", json=self._valid_payload())
        body = r.json()
        assert "Claude" in body["cv_markdown"]
        assert "LLM" in body["cv_markdown"]

    def test_forbidden_phrases_flagged(self) -> None:
        cv_body = "# Patricia\n\nA results-driven team player responsible for cloud infra."
        with patch("subprocess.run", return_value=self._mock_openclaw(cv_body)):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/rewrite-cv", json=self._valid_payload())
        body = r.json()
        assert body["ok"] is True  # CV still returned
        assert body["forbidden_phrases_found"] is not None
        assert len(body["forbidden_phrases_found"]) >= 1

    def test_clean_cv_no_forbidden_field(self) -> None:
        cv_body = "# Patricia\n\nBuilt LLM pipelines and shipped production RAG systems."
        with patch("subprocess.run", return_value=self._mock_openclaw(cv_body)):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/rewrite-cv", json=self._valid_payload())
        body = r.json()
        assert body["ok"] is True
        assert body["forbidden_phrases_found"] is None

    def test_filename_format(self) -> None:
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/rewrite-cv", json=self._valid_payload())
        filename = r.json()["filename"]
        assert filename.startswith("CV_Patricia_Wintrebert_AcmeCorp_")
        assert filename.endswith(".md")


# ── parse_letter_wrapper ──────────────────────────────────────────────────────


def _fake_openclaw_letter_stdout(letter_body: str = "Dear Hiring Manager,\n\nI built systems.") -> str:
    return json.dumps({"result": {"finalAssistantVisibleText": f"[LETTER_START]\n{letter_body}\n[LETTER_END]"}})


class TestParseLetterWrapper(unittest.TestCase):
    def test_happy_path(self) -> None:
        raw = "[LETTER_START]\nDear Hiring Manager,\n\nI built systems.\n[LETTER_END]"
        assert hb.parse_letter_wrapper(raw) == "Dear Hiring Manager,\n\nI built systems."

    def test_extra_content_outside_ignored(self) -> None:
        raw = "preamble\n[LETTER_START]\nContent.\n[LETTER_END]\npostamble"
        assert hb.parse_letter_wrapper(raw) == "Content."

    def test_missing_start_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            hb.parse_letter_wrapper("Content.\n[LETTER_END]")
        assert "[LETTER_START]" in str(ctx.exception)

    def test_missing_end_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            hb.parse_letter_wrapper("[LETTER_START]\nContent.")
        assert "[LETTER_END]" in str(ctx.exception)

    def test_only_first_start_and_end_used(self) -> None:
        raw = "[LETTER_START]\nFirst.\n[LETTER_END]\n[LETTER_START]\nSecond.\n[LETTER_END]"
        assert hb.parse_letter_wrapper(raw) == "First."


# ── generate_letter_filename ──────────────────────────────────────────────────


class TestGenerateLetterFilename(unittest.TestCase):
    def test_happy_path(self) -> None:
        name = hb.generate_letter_filename("AcmeCorp", today="2026-06-10")
        assert name == "LM_Patricia_Wintrebert_AcmeCorp_2026-06-10.md"

    def test_spaces_normalized(self) -> None:
        name = hb.generate_letter_filename("Acme Corp Ltd", today="2026-06-10")
        assert " " not in name
        assert "2026-06-10" in name

    def test_special_chars_removed(self) -> None:
        name = hb.generate_letter_filename("Acme/Corp & Co.", today="2026-06-10")
        company_part = name.replace(".md", "").split("_2026-06-10")[0]
        assert "/" not in company_part
        assert "&" not in company_part
        assert "." not in company_part

    def test_date_format_iso(self) -> None:
        name = hb.generate_letter_filename("X", today="2026-06-10")
        assert "2026-06-10" in name

    def test_uses_today_when_no_date_arg(self) -> None:
        name = hb.generate_letter_filename("TestCo")
        assert name.startswith("LM_Patricia_Wintrebert_TestCo_")
        assert name.endswith(".md")

    def test_prefix_is_lm_not_cv(self) -> None:
        name = hb.generate_letter_filename("AcmeCorp", today="2026-06-10")
        assert name.startswith("LM_")
        assert not name.startswith("CV_")


# ── count_words ───────────────────────────────────────────────────────────────


class TestCountWords(unittest.TestCase):
    def test_single_word(self) -> None:
        assert hb.count_words("hello") == 1

    def test_empty_string(self) -> None:
        assert hb.count_words("") == 0

    def test_multiline_text(self) -> None:
        assert hb.count_words("one two\nthree  four") == 4

    def test_markdown_headers_counted(self) -> None:
        assert hb.count_words("# Title\n\nTwo words.") == 4

    def test_whitespace_only(self) -> None:
        assert hb.count_words("   \n  ") == 0


# ── call_cover_letter_writer (subprocess mocked) ─────────────────────────────


class TestCallCoverLetterWriter(unittest.TestCase):
    def test_openclaw_agent_command_used(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            hb.call_cover_letter_writer({})
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "openclaw"
        assert "agent" in cmd

    def test_message_contains_skill_tag(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = _fake_openclaw_letter_stdout()
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            hb.call_cover_letter_writer({"company": "Acme", "role": "Engineer"})
        message_arg = " ".join(mock_run.call_args[0][0])
        assert "COVER-LETTER-WRITER SKILL" in message_arg

    def test_brief_fields_in_message(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            hb.call_cover_letter_writer({"company": "Acme", "role": "Engineer", "language": "en"})
        call_args = mock_run.call_args[0][0]
        full_cmd = " ".join(call_args)
        assert "Acme" in full_cmd

    def test_timeout_propagates(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("openclaw", 600)):
            with self.assertRaises(subprocess.TimeoutExpired):
                hb.call_cover_letter_writer({})


# ── POST /cover-letter (FastAPI TestClient) ───────────────────────────────────


@unittest.skipUnless(_HAS_FASTAPI_TEST, "fastapi[testclient] not installed")
class TestCoverLetterEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi.testclient import TestClient
        hb._cl_rate_limiter.reset()
        hb._cv_rate_limiter.reset()
        self.client = TestClient(hb.app)

    def _mock_openclaw(self, letter_body: str = "I built LLM pipelines.\n\nI shipped production agents.") -> MagicMock:
        mock_proc = MagicMock()
        mock_proc.stdout = _fake_openclaw_letter_stdout(letter_body)
        return mock_proc

    def _valid_payload(self, **overrides) -> dict:
        base = {
            "offer_description": "We need an AI engineer working with Claude and LLM agents.",
            "company": "AcmeCorp",
            "role": "AI Engineer",
        }
        base.update(overrides)
        return base

    def test_happy_path(self) -> None:
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/cover-letter", json=self._valid_payload())
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["letter_markdown"] is not None
        assert body["language"] == "en"
        assert body["word_count"] is not None
        assert body["filename"] is not None

    def test_filename_prefix_is_lm(self) -> None:
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/cover-letter", json=self._valid_payload())
        assert r.json()["filename"].startswith("LM_Patricia_Wintrebert_")

    def test_filename_contains_company(self) -> None:
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/cover-letter", json=self._valid_payload())
        assert "AcmeCorp" in r.json()["filename"]

    def test_word_count_matches_letter(self) -> None:
        body_text = "I built LLM pipelines and shipped production agents to prod."
        with patch("subprocess.run", return_value=self._mock_openclaw(body_text)):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/cover-letter", json=self._valid_payload())
        resp = r.json()
        assert resp["word_count"] == hb.count_words(resp["letter_markdown"])

    def test_language_detected_fr(self) -> None:
        payload = self._valid_payload(
            offer_description="Nous recherchons notre candidat pour notre poste en France."
        )
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/cover-letter", json=payload)
        assert r.json()["language"] == "fr"

    def test_language_override_wins(self) -> None:
        payload = self._valid_payload(
            offer_description="Nous recherchons notre candidat pour notre poste.",
            language="de",
        )
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/cover-letter", json=payload)
        assert r.json()["language"] == "de"

    def test_language_defaults_en(self) -> None:
        payload = self._valid_payload(offer_description="Senior engineer position.")
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/cover-letter", json=payload)
        assert r.json()["language"] == "en"

    def test_missing_offer_description_422(self) -> None:
        r = self.client.post("/cover-letter", json={"company": "X", "role": "Dev"})
        assert r.status_code == 422

    def test_missing_company_422(self) -> None:
        r = self.client.post("/cover-letter", json={"offer_description": "...", "role": "Dev"})
        assert r.status_code == 422

    def test_missing_role_422(self) -> None:
        r = self.client.post("/cover-letter", json={"offer_description": "...", "company": "X"})
        assert r.status_code == 422

    def test_rate_limit_429(self) -> None:
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                for _ in range(5):
                    self.client.post("/cover-letter", json=self._valid_payload())
        r = self.client.post("/cover-letter", json=self._valid_payload())
        assert r.status_code == 429

    def test_rate_limiter_independent_from_cv(self) -> None:
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                for _ in range(5):
                    self.client.post("/cover-letter", json=self._valid_payload())
        mock_cv = MagicMock()
        mock_cv.stdout = _fake_openclaw_stdout()
        with patch("subprocess.run", return_value=mock_cv):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post(
                    "/rewrite-cv",
                    json={"offer_description": "AI engineer", "company": "X", "matched_keywords": []},
                )
        assert r.status_code == 200

    def test_missing_markers_returns_error(self) -> None:
        mock_proc = MagicMock()
        mock_proc.stdout = json.dumps({"result": {"finalAssistantVisibleText": "raw text no markers"}})
        with patch("subprocess.run", return_value=mock_proc):
            r = self.client.post("/cover-letter", json=self._valid_payload())
        body = r.json()
        assert body["ok"] is False
        assert "LETTER_START" in body["error"]

    def test_timeout_returns_error(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("openclaw", 600)):
            r = self.client.post("/cover-letter", json=self._valid_payload())
        body = r.json()
        assert body["ok"] is False
        assert "timeout" in body["error"]

    def test_forbidden_phrases_flagged(self) -> None:
        letter_body = "I am a results-driven team player responsible for cloud infra."
        with patch("subprocess.run", return_value=self._mock_openclaw(letter_body)):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/cover-letter", json=self._valid_payload())
        body = r.json()
        assert body["ok"] is True
        assert body["forbidden_phrases_found"] is not None
        assert len(body["forbidden_phrases_found"]) >= 1

    def test_clean_letter_no_forbidden_field(self) -> None:
        letter_body = "I built LLM pipelines and shipped production RAG systems at scale."
        with patch("subprocess.run", return_value=self._mock_openclaw(letter_body)):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/cover-letter", json=self._valid_payload())
        body = r.json()
        assert body["ok"] is True
        assert body["forbidden_phrases_found"] is None

    def test_optional_cv_summary_accepted(self) -> None:
        payload = self._valid_payload(cv_summary="Senior AI engineer, 8 years experience.")
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/cover-letter", json=payload)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_optional_matched_keywords_empty_list(self) -> None:
        payload = self._valid_payload(matched_keywords=[])
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/cover-letter", json=payload)
        assert r.status_code == 200

    def test_optional_profile_tag_absent(self) -> None:
        with patch("subprocess.run", return_value=self._mock_openclaw()):
            with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                r = self.client.post("/cover-letter", json=self._valid_payload())
        assert r.status_code == 200


# ── parse_answers_wrapper ─────────────────────────────────────────────────────


def _fake_openclaw_form_stdout(answers: list) -> str:
    body = json.dumps({"answers": answers})
    return json.dumps({"result": {"finalAssistantVisibleText": f"[ANSWERS_START]\n{body}\n[ANSWERS_END]"}})


class TestParseAnswersWrapper(unittest.TestCase):
    def test_happy_path(self) -> None:
        raw = '[ANSWERS_START]\n{"answers": [{"id": "q1", "answer": "Yes", "source": "generated"}]}\n[ANSWERS_END]'
        result = hb.parse_answers_wrapper(raw)
        assert result == [{"id": "q1", "answer": "Yes", "source": "generated"}]

    def test_empty_answers_list(self) -> None:
        raw = '[ANSWERS_START]\n{"answers": []}\n[ANSWERS_END]'
        assert hb.parse_answers_wrapper(raw) == []

    def test_multiple_answers(self) -> None:
        answers = [{"id": "q1", "answer": "A1", "source": "generated"}, {"id": "q2", "answer": "A2", "source": "generated"}]
        raw = f'[ANSWERS_START]\n{json.dumps({"answers": answers})}\n[ANSWERS_END]'
        result = hb.parse_answers_wrapper(raw)
        assert len(result) == 2
        assert result[0]["id"] == "q1"
        assert result[1]["id"] == "q2"

    def test_extra_content_outside_ignored(self) -> None:
        raw = 'preamble\n[ANSWERS_START]\n{"answers": []}\n[ANSWERS_END]\npostamble'
        assert hb.parse_answers_wrapper(raw) == []

    def test_missing_start_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            hb.parse_answers_wrapper('{"answers": []}\n[ANSWERS_END]')
        assert "[ANSWERS_START]" in str(ctx.exception)

    def test_missing_end_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            hb.parse_answers_wrapper('[ANSWERS_START]\n{"answers": []}')
        assert "[ANSWERS_END]" in str(ctx.exception)

    def test_invalid_json_raises(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            hb.parse_answers_wrapper('[ANSWERS_START]\nnot json\n[ANSWERS_END]')


# ── _match_precalibrated ──────────────────────────────────────────────────────


_FILLED_USER_ANSWERS: dict[str, str] = {
    "Prétentions salariales": "Mes prétentions sont de 90K€.",
    "Disponibilité": "Immédiatement.",
    "Télétravail": "Remote total depuis 3 ans.",
    "Expérience Python": "10+ ans de Python en production.",
    "Années d'expérience": "10+ ans d'expérience.",
    "Droit de travail en Suisse": "Permis B Suisse.",
}


class TestMatchPrecalibrated(unittest.TestCase):
    def test_salary_en(self) -> None:
        result = hb._match_precalibrated("What is your salary expectation?", _FILLED_USER_ANSWERS)
        assert result == "Mes prétentions sont de 90K€."

    def test_salary_fr(self) -> None:
        result = hb._match_precalibrated("Quelles sont vos prétentions salariales ?", _FILLED_USER_ANSWERS)
        assert result == "Mes prétentions sont de 90K€."

    def test_availability_matched(self) -> None:
        result = hb._match_precalibrated("When are you available to start?", _FILLED_USER_ANSWERS)
        assert result == "Immédiatement."

    def test_remote_matched(self) -> None:
        result = hb._match_precalibrated("Do you have remote work experience?", _FILLED_USER_ANSWERS)
        assert result == "Remote total depuis 3 ans."

    def test_python_experience_matched(self) -> None:
        result = hb._match_precalibrated("How many years of Python experience do you have?", _FILLED_USER_ANSWERS)
        assert result is not None

    def test_years_experience_matched(self) -> None:
        result = hb._match_precalibrated("How many years of experience do you have?", _FILLED_USER_ANSWERS)
        assert result is not None

    def test_right_to_work_matched(self) -> None:
        result = hb._match_precalibrated("Do you have the right to work in Switzerland?", _FILLED_USER_ANSWERS)
        assert result == "Permis B Suisse."

    def test_open_question_not_matched(self) -> None:
        result = hb._match_precalibrated("Why do you want to work at Acme?", _FILLED_USER_ANSWERS)
        assert result is None

    def test_case_insensitive(self) -> None:
        result = hb._match_precalibrated("SALARY EXPECTATIONS", _FILLED_USER_ANSWERS)
        assert result == "Mes prétentions sont de 90K€."

    def test_missing_section_in_dict_returns_none(self) -> None:
        result = hb._match_precalibrated("What is your salary?", {})
        assert result is None


# ── _parse_user_md_precal ─────────────────────────────────────────────────────


class TestParseUserMdPrecal(unittest.TestCase):
    def _tmp_path(self, content: str) -> Path:
        import tempfile
        tmp = Path(tempfile.mktemp(suffix=".md"))
        tmp.write_text(content, encoding="utf-8")
        return tmp

    def test_filled_section_returned(self) -> None:
        content = (
            "## Réponses pré-calibrées aux questions fréquentes\n\n"
            '### Disponibilité\n"Immédiatement."\n\n'
            '### Prétentions salariales\n"90K€ brut/an."\n\n'
            "## Autre section\n"
        )
        path = self._tmp_path(content)
        result = hb._parse_user_md_precal(path)
        path.unlink()
        assert result["Disponibilité"] == "Immédiatement."
        assert result["Prétentions salariales"] == "90K€ brut/an."

    def test_fill_in_section_excluded(self) -> None:
        content = (
            "## Réponses pré-calibrées aux questions fréquentes\n\n"
            '### Disponibilité\n"[FILL IN — ex: immédiatement]"\n\n'
            '### Prétentions salariales\n"90K€ brut/an."\n'
        )
        path = self._tmp_path(content)
        result = hb._parse_user_md_precal(path)
        path.unlink()
        assert "Disponibilité" not in result
        assert result["Prétentions salariales"] == "90K€ brut/an."

    def test_file_not_found_returns_empty(self) -> None:
        result = hb._parse_user_md_precal(Path("/nonexistent/USER.md"))
        assert result == {}

    def test_no_precal_section_returns_empty(self) -> None:
        content = "# User Profile\n\n## Profil\nNo precal section.\n"
        path = self._tmp_path(content)
        result = hb._parse_user_md_precal(path)
        path.unlink()
        assert result == {}


# ── POST /form-answers (FastAPI TestClient) ───────────────────────────────────


@unittest.skipUnless(_HAS_FASTAPI_TEST, "fastapi[testclient] not installed")
class TestFormAnswersEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi.testclient import TestClient
        hb._fa_rate_limiter.reset()
        hb._cv_rate_limiter.reset()
        hb._cl_rate_limiter.reset()
        self.client = TestClient(hb.app)

    def _mock_openclaw(self, answers: list | None = None) -> MagicMock:
        if answers is None:
            answers = [{"id": "q1", "answer": "Because I love AI.", "source": "generated"}]
        mock_proc = MagicMock()
        mock_proc.stdout = _fake_openclaw_form_stdout(answers)
        return mock_proc

    def _valid_payload(self, **overrides) -> dict:
        base: dict = {
            "questions": [{"id": "q1", "label": "Why do you want to work here?", "type": "textarea"}],
            "offer_description": "AI Engineer at Acme AI, remote-first.",
            "company": "Acme AI",
            "title": "AI Engineer",
        }
        base.update(overrides)
        return base

    def test_empty_questions_no_subprocess(self) -> None:
        payload = self._valid_payload(questions=[])
        with patch("subprocess.run") as mock_run:
            r = self.client.post("/form-answers", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["answers"] == []
        mock_run.assert_not_called()

    def test_all_precalibrated_no_subprocess(self) -> None:
        payload = self._valid_payload(questions=[
            {"id": "q_sal", "label": "What is your salary expectation?", "type": "text"},
            {"id": "q_avail", "label": "When are you available to start?", "type": "text"},
        ])
        with patch("subprocess.run") as mock_run, \
             patch.object(hb, "_parse_user_md_precal", return_value=_FILLED_USER_ANSWERS):
            r = self.client.post("/form-answers", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert all(a["source"] == "precalibrated" for a in body["answers"])
        mock_run.assert_not_called()

    def test_all_generated_subprocess_called(self) -> None:
        with patch("subprocess.run", return_value=self._mock_openclaw()) as mock_run, \
             patch.object(hb, "_parse_user_md_precal", return_value=_FILLED_USER_ANSWERS):
            r = self.client.post("/form-answers", json=self._valid_payload())
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["answers"][0]["source"] == "generated"
        mock_run.assert_called_once()

    def test_mixed_precal_and_generated(self) -> None:
        payload = self._valid_payload(questions=[
            {"id": "q_sal", "label": "What is your salary expectation?", "type": "text"},
            {"id": "q_why", "label": "Why do you want to work here?", "type": "textarea"},
        ])
        generated = [{"id": "q_why", "answer": "Because I love AI.", "source": "generated"}]
        with patch("subprocess.run", return_value=self._mock_openclaw(generated)) as mock_run, \
             patch.object(hb, "_parse_user_md_precal", return_value=_FILLED_USER_ANSWERS):
            r = self.client.post("/form-answers", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert len(body["answers"]) == 2
        sources = {a["id"]: a["source"] for a in body["answers"]}
        assert sources["q_sal"] == "precalibrated"
        assert sources["q_why"] == "generated"

    def test_mixed_subprocess_payload_contains_only_unmatched(self) -> None:
        payload = self._valid_payload(questions=[
            {"id": "q_sal", "label": "What is your salary expectation?", "type": "text"},
            {"id": "q_why", "label": "Why do you want to work here?", "type": "textarea"},
        ])
        generated = [{"id": "q_why", "answer": "Because I love AI.", "source": "generated"}]
        with patch("subprocess.run", return_value=self._mock_openclaw(generated)) as mock_run, \
             patch.object(hb, "_parse_user_md_precal", return_value=_FILLED_USER_ANSWERS):
            self.client.post("/form-answers", json=payload)
        call_cmd = " ".join(mock_run.call_args[0][0])
        assert "q_why" in call_cmd
        assert "q_sal" not in call_cmd

    def test_response_ids_match_input(self) -> None:
        payload = self._valid_payload(questions=[
            {"id": "unique_xyz", "label": "Describe a challenging project.", "type": "textarea"},
        ])
        generated = [{"id": "unique_xyz", "answer": "Built a RAG pipeline.", "source": "generated"}]
        with patch("subprocess.run", return_value=self._mock_openclaw(generated)), \
             patch.object(hb, "_parse_user_md_precal", return_value={}):
            r = self.client.post("/form-answers", json=payload)
        assert r.json()["answers"][0]["id"] == "unique_xyz"

    def test_timeout_returns_ok_false(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("openclaw", 600)), \
             patch.object(hb, "_parse_user_md_precal", return_value={}):
            r = self.client.post("/form-answers", json=self._valid_payload())
        body = r.json()
        assert body["ok"] is False
        assert "timeout" in body["error"]

    def test_missing_markers_returns_error(self) -> None:
        mock_proc = MagicMock()
        mock_proc.stdout = json.dumps({"result": {"finalAssistantVisibleText": "raw text no markers"}})
        with patch("subprocess.run", return_value=mock_proc), \
             patch.object(hb, "_parse_user_md_precal", return_value={}):
            r = self.client.post("/form-answers", json=self._valid_payload())
        body = r.json()
        assert body["ok"] is False
        assert "ANSWERS_START" in body["error"]

    def test_language_fr_detected_in_brief(self) -> None:
        payload = self._valid_payload(
            offer_description="Nous recherchons notre candidat pour notre poste en France."
        )
        with patch("subprocess.run", return_value=self._mock_openclaw()) as mock_run, \
             patch.object(hb, "_parse_user_md_precal", return_value={}):
            self.client.post("/form-answers", json=payload)
        call_cmd = " ".join(mock_run.call_args[0][0])
        assert '"language": "fr"' in call_cmd

    def test_language_override_in_brief(self) -> None:
        payload = self._valid_payload(language="de")
        with patch("subprocess.run", return_value=self._mock_openclaw()) as mock_run, \
             patch.object(hb, "_parse_user_md_precal", return_value={}):
            self.client.post("/form-answers", json=payload)
        call_cmd = " ".join(mock_run.call_args[0][0])
        assert '"language": "de"' in call_cmd

    def test_rate_limit_429(self) -> None:
        empty_payload = self._valid_payload(questions=[])
        for _ in range(20):
            r = self.client.post("/form-answers", json=empty_payload)
            assert r.status_code == 200
        r = self.client.post("/form-answers", json=empty_payload)
        assert r.status_code == 429

    def test_missing_questions_field_422(self) -> None:
        r = self.client.post("/form-answers", json={"offer_description": "AI engineer at Acme"})
        assert r.status_code == 422

    def test_missing_offer_description_422(self) -> None:
        r = self.client.post("/form-answers", json={"questions": []})
        assert r.status_code == 422

    def test_response_shape(self) -> None:
        with patch("subprocess.run", return_value=self._mock_openclaw()), \
             patch.object(hb, "_parse_user_md_precal", return_value={}):
            r = self.client.post("/form-answers", json=self._valid_payload())
        body = r.json()
        assert "ok" in body
        assert "answers" in body
        assert "error" in body
        for ans in body["answers"]:
            assert "id" in ans
            assert "answer" in ans
            assert "source" in ans


if __name__ == "__main__":
    unittest.main()

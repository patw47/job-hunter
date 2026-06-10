#!/usr/bin/env python3
"""
Hunter Bridge — FastAPI server for CV generation and cover letters.

Routes:
  GET  /health             -- healthcheck
  POST /rewrite-cv         -- cv-rewriter skill (Sonnet), rate-limited to 5/hour
  POST /cover-letter       -- cover-letter-writer skill (Sonnet), rate-limited to 5/hour
  POST /form-answers       -- form-answerer skill (Haiku), pre-calibrated lookup + LLM fallback, rate-limited to 20/hour
  POST /store-documents    -- Drive upload + MATCHES update + Telegram notification payload, rate-limited to 10/hour
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from collections import deque
from datetime import date
from pathlib import Path
from typing import Final

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import drive_uploader

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

PORT: Final[int] = int(os.environ.get("HUNTER_BRIDGE_PORT", "18799"))
OPENCLAW_CONFIG: Final[str] = os.environ.get(
    "OPENCLAW_CONFIG_PATH", "/home/thehunter/.openclaw/openclaw.json"
)
WORKSPACE_DIR: Final[Path] = Path(
    os.environ.get("OPENCLAW_WORKSPACE", "/home/thehunter/.openclaw/workspace")
)
CV_OUTPUT_DIR: Final[Path] = Path(
    os.environ.get("CV_OUTPUT_DIR", "/home/thehunter/generated/cv")
)
LM_OUTPUT_DIR: Final[Path] = Path(
    os.environ.get("LM_OUTPUT_DIR", "/home/thehunter/generated/cover-letters")
)
AGENT_ID: Final[str] = "the-hunter"
SKILL_NAME: Final[str] = "cv-rewriter"
COVER_LETTER_SKILL_NAME: Final[str] = "cover-letter-writer"
FORM_ANSWERER_SKILL_NAME: Final[str] = "form-answerer"
OPENCLAW_TIMEOUT: Final[int] = 600

RATE_LIMIT_MAX: Final[int] = 5
RATE_LIMIT_WINDOW: Final[int] = 3600  # seconds

FORBIDDEN_PHRASES: Final[list[str]] = [
    "responsible for",
    "team player",
    "hard worker",
    "dynamic professional",
    "synergy",
    "results-driven",
    "results driven",
    "passionate about",
    "detail-oriented",
    "detail oriented",
    "think outside the box",
    "go-getter",
    "strong communication skills",
    "excellent communication skills",
]

ALLOWED_LANGUAGES: Final[frozenset[str]] = frozenset({"en", "fr", "de"})
ALLOWED_PROFILE_TAGS: Final[frozenset[str]] = frozenset(
    {"ai_engineer", "ai_builder", "full_stack", "mlops"}
)

# ── Language detection ─────────────────────────────────────────────────────────

_LANG_FR_PATTERNS: Final[tuple[str, ...]] = (
    r"\bnous\b", r"\bvous\b", r"\bposte\b", r"\bfrançais\b",
    r"\bexpérience\b", r"\bcandidature\b", r"\brecherchons\b",
    r"\bnotre\b", r"\bvotre\b",
)
_LANG_DE_PATTERNS: Final[tuple[str, ...]] = (
    r"\bwir\b", r"\bstelle\b", r"\bdeutsch\b",
    r"\berfahrung\b", r"\bbewerber\b", r"\bgesucht\b",
    r"\bunsere\b", r"\bihre\b",
)
_LANG_THRESHOLD: Final[int] = 2


def detect_language(offer_text: str, override: str | None = None) -> str:
    """Detect offer language — returns 'en', 'fr', or 'de'. Default 'en'."""
    if override and override.lower() in ALLOWED_LANGUAGES:
        return override.lower()
    text = offer_text.lower()
    fr_count = sum(1 for p in _LANG_FR_PATTERNS if re.search(p, text))
    de_count = sum(1 for p in _LANG_DE_PATTERNS if re.search(p, text))
    if de_count >= _LANG_THRESHOLD:
        return "de"
    if fr_count >= _LANG_THRESHOLD:
        return "fr"
    return "en"


# ── Profile tag detection ─────────────────────────────────────────────────────

_AI_ENGINEER_KW: Final[tuple[str, ...]] = (
    "claude", "llm", "agent", "rag", "langchain", "openai", "gpt", "anthropic",
    "embedding", "vector", "mcp", "large language", "foundation model", "agentic",
)
_MLOPS_KW: Final[tuple[str, ...]] = (
    "mlops", "mlflow", "kubeflow", "model deployment", "model serving",
    "kubernetes", "k8s",
)
_AI_BUILDER_KW: Final[tuple[str, ...]] = (
    "n8n", "make ", "automation", "workflow", "no-code", "low-code",
    "zapier", "ai product", "airflow",
)


def detect_profile_tag(
    offer_text: str,
    matched_keywords: list[str],
    override: str | None = None,
) -> str:
    """Keyword-based profile detection. Priority: ai_engineer > mlops > ai_builder > full_stack."""
    if override and override.lower() in ALLOWED_PROFILE_TAGS:
        return override.lower()
    combined = (offer_text + " " + " ".join(matched_keywords)).lower()
    if any(k in combined for k in _AI_ENGINEER_KW):
        return "ai_engineer"
    if any(k in combined for k in _MLOPS_KW):
        return "mlops"
    if any(k in combined for k in _AI_BUILDER_KW):
        return "ai_builder"
    return "full_stack"


# ── Rate limiter ──────────────────────────────────────────────────────────────

class _RateLimiter:
    """Sliding-window rate limiter. Patchable _now callable for tests."""

    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._calls: deque[float] = deque()

    def check_and_record(self, _now: float | None = None) -> bool:
        """Return True if allowed; False if limit exceeded (does not record on False)."""
        now = _now if _now is not None else time.time()
        while self._calls and now - self._calls[0] > self.window:
            self._calls.popleft()
        if len(self._calls) >= self.max_calls:
            return False
        self._calls.append(now)
        return True

    def reset(self) -> None:
        self._calls.clear()


_cv_rate_limiter = _RateLimiter(RATE_LIMIT_MAX, RATE_LIMIT_WINDOW)
_cl_rate_limiter = _RateLimiter(RATE_LIMIT_MAX, RATE_LIMIT_WINDOW)
_FA_RATE_LIMIT_MAX: Final[int] = 20
_fa_rate_limiter = _RateLimiter(_FA_RATE_LIMIT_MAX, RATE_LIMIT_WINDOW)
_SD_RATE_LIMIT_MAX: Final[int] = 10
_sd_rate_limiter = _RateLimiter(_SD_RATE_LIMIT_MAX, RATE_LIMIT_WINDOW)

# ── CV parsing ────────────────────────────────────────────────────────────────

_CV_START: Final[str] = "[CV_START]"
_CV_END: Final[str] = "[CV_END]"
_LETTER_START: Final[str] = "[LETTER_START]"
_LETTER_END: Final[str] = "[LETTER_END]"
_ANSWERS_START: Final[str] = "[ANSWERS_START]"
_ANSWERS_END: Final[str] = "[ANSWERS_END]"


def parse_cv_wrapper(raw: str) -> str:
    """Extract CV markdown from [CV_START]...[CV_END] markers."""
    start_idx = raw.find(_CV_START)
    if start_idx == -1:
        raise ValueError(f"Missing {_CV_START} marker in OpenClaw response")
    end_idx = raw.find(_CV_END, start_idx)
    if end_idx == -1:
        raise ValueError(f"Missing {_CV_END} marker in OpenClaw response")
    return raw[start_idx + len(_CV_START):end_idx].strip()


def parse_letter_wrapper(raw: str) -> str:
    """Extract letter markdown from [LETTER_START]...[LETTER_END] markers."""
    start_idx = raw.find(_LETTER_START)
    if start_idx == -1:
        raise ValueError(f"Missing {_LETTER_START} marker in OpenClaw response")
    end_idx = raw.find(_LETTER_END, start_idx)
    if end_idx == -1:
        raise ValueError(f"Missing {_LETTER_END} marker in OpenClaw response")
    return raw[start_idx + len(_LETTER_START):end_idx].strip()


def parse_answers_wrapper(raw: str) -> list[dict]:
    """Extract JSON answers list from [ANSWERS_START]...[ANSWERS_END] markers."""
    start_idx = raw.find(_ANSWERS_START)
    if start_idx == -1:
        raise ValueError(f"Missing {_ANSWERS_START} marker in form-answerer response")
    end_idx = raw.find(_ANSWERS_END, start_idx)
    if end_idx == -1:
        raise ValueError(f"Missing {_ANSWERS_END} marker in form-answerer response")
    json_str = raw[start_idx + len(_ANSWERS_START):end_idx].strip()
    data = json.loads(json_str)
    return data.get("answers", [])


# ── Filename generation ───────────────────────────────────────────────────────

_UNSAFE_CHARS: Final[re.Pattern[str]] = re.compile(r"[^\w]")


def generate_cv_filename(company: str, today: str | None = None) -> str:
    """Return CV_Patricia_Wintrebert_{Company}_{YYYY-MM-DD}.md"""
    if today is None:
        today = date.today().isoformat()
    clean = _UNSAFE_CHARS.sub("_", company)
    clean = re.sub(r"_+", "_", clean).strip("_")
    return f"CV_Patricia_Wintrebert_{clean}_{today}.md"


def generate_letter_filename(company: str, today: str | None = None) -> str:
    """Return LM_Patricia_Wintrebert_{Company}_{YYYY-MM-DD}.md"""
    if today is None:
        today = date.today().isoformat()
    clean = _UNSAFE_CHARS.sub("_", company)
    clean = re.sub(r"_+", "_", clean).strip("_")
    return f"LM_Patricia_Wintrebert_{clean}_{today}.md"


# ── Word count ────────────────────────────────────────────────────────────────


def count_words(text: str) -> int:
    """Count whitespace-separated tokens in text."""
    return len(text.split())


# ── Forbidden phrases validation ──────────────────────────────────────────────

def validate_forbidden_phrases(cv_text: str) -> list[str]:
    """Return list of forbidden phrases found in the CV (case-insensitive)."""
    text_lower = cv_text.lower()
    return [phrase for phrase in FORBIDDEN_PHRASES if phrase in text_lower]


# ── Precalibrated answers (zero-token lookup from USER.md) ────────────────────

_USER_MD_PATH: Final[Path] = WORKSPACE_DIR / "the-hunter" / "USER.md"
_FILL_IN_RE: Final[re.Pattern[str]] = re.compile(r"\[FILL IN", re.IGNORECASE)

# (pattern, USER.md section header) in priority order
_PRECAL_PATTERNS: Final[list[tuple[re.Pattern[str], str]]] = [
    (re.compile(r"salary|salaire|pr[eé]tention|compensation|r[eé]mun[eé]ration|wage", re.IGNORECASE), "Prétentions salariales"),
    (re.compile(r"availab|disponib|start\s*date|notice\s*period|when\s+can\s+you\s+start", re.IGNORECASE), "Disponibilité"),
    (re.compile(r"remote|t[eé]l[eé]travail|work\s+from\s+home|wfh", re.IGNORECASE), "Télétravail"),
    (re.compile(r"python.*experience|experience.*python|years.*python|python.*years", re.IGNORECASE), "Expérience Python"),
    (re.compile(r"years?\s+of\s+experience|how\s+many\s+years|ans\s+d.exp[eé]rience", re.IGNORECASE), "Années d'expérience"),
    (re.compile(r"right\s+to\s+work|work\s+permit|autorisation\s+de\s+travail|visa.*work|work.*authoriz|eligible\s+to\s+work", re.IGNORECASE), "Droit de travail en Suisse"),
]


def _parse_user_md_precal(path: Path = _USER_MD_PATH) -> dict[str, str]:
    """Load pre-calibrated answer sections from USER.md.

    Returns {section_name: answer_text}. Sections with [FILL IN] are excluded.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("USER.md not found at %s — pre-calibration disabled", path)
        return {}

    result: dict[str, str] = {}

    precal_start = text.find("## Réponses pré-calibrées")
    if precal_start == -1:
        return result

    next_h2 = re.search(r"\n## ", text[precal_start + 1:])
    precal_block = (
        text[precal_start: precal_start + 1 + next_h2.start()]
        if next_h2
        else text[precal_start:]
    )

    for m in re.finditer(r"### (.+?)\n(.*?)(?=\n### |\Z)", precal_block, re.DOTALL):
        section_name = m.group(1).strip()
        section_content = m.group(2).strip()
        quoted = re.search(r'"(.+?)"', section_content, re.DOTALL)
        answer = quoted.group(1).strip() if quoted else section_content
        if answer and not _FILL_IN_RE.search(answer):
            result[section_name] = answer

    return result


def _match_precalibrated(label: str, user_answers: dict[str, str]) -> str | None:
    """Return pre-calibrated answer for label if matched, else None."""
    for pattern, section_name in _PRECAL_PATTERNS:
        if pattern.search(label) and section_name in user_answers:
            return user_answers[section_name]
    return None


# ── OpenClaw call ─────────────────────────────────────────────────────────────

def _extract_inner(stdout: str) -> str:
    try:
        outer = json.loads(stdout)
        result_obj = outer.get("result", {})
        fat = result_obj.get("finalAssistantVisibleText")
        if fat:
            return fat
        payloads = result_obj.get("payloads", [])
        if payloads and isinstance(payloads, list):
            all_text = "\n".join(p.get("text", "") for p in payloads if p.get("text"))
            if all_text:
                return all_text
        for key in ("response", "content", "message", "text", "output"):
            if key in outer and isinstance(outer[key], str):
                return outer[key]
        return stdout
    except Exception:
        return stdout


def call_cv_rewriter(brief: dict) -> str:
    """Dispatch cv-rewriter skill via openclaw agent CLI. Returns raw output text."""
    session_id = f"bridge-cv-{int(time.time())}"
    message = (
        f"[{SKILL_NAME.upper()} SKILL]\n"
        "Brief reçu depuis n8n :\n"
        + json.dumps(brief, ensure_ascii=False, indent=2)
        + f"\n\nApplique la skill {SKILL_NAME} selon SKILL.md."
    )
    env = os.environ.copy()
    env["OPENCLAW_CONFIG_PATH"] = OPENCLAW_CONFIG
    env.setdefault("HOME", "/home/thehunter")
    result = subprocess.run(
        [
            "openclaw", "agent",
            "--agent", AGENT_ID,
            "--session-id", session_id,
            "--message", message,
            "--json",
            "--timeout", str(OPENCLAW_TIMEOUT),
        ],
        capture_output=True,
        text=True,
        timeout=OPENCLAW_TIMEOUT + 30,
        stdin=subprocess.DEVNULL,
        env=env,
    )
    return _extract_inner(result.stdout.strip())


def call_cover_letter_writer(brief: dict) -> str:
    """Dispatch cover-letter-writer skill via openclaw agent CLI. Returns raw output text."""
    session_id = f"bridge-cl-{int(time.time())}"
    message = (
        f"[{COVER_LETTER_SKILL_NAME.upper()} SKILL]\n"
        "Brief reçu depuis n8n :\n"
        + json.dumps(brief, ensure_ascii=False, indent=2)
        + f"\n\nApplique la skill {COVER_LETTER_SKILL_NAME} selon SKILL.md."
    )
    env = os.environ.copy()
    env["OPENCLAW_CONFIG_PATH"] = OPENCLAW_CONFIG
    env.setdefault("HOME", "/home/thehunter")
    result = subprocess.run(
        [
            "openclaw", "agent",
            "--agent", AGENT_ID,
            "--session-id", session_id,
            "--message", message,
            "--json",
            "--timeout", str(OPENCLAW_TIMEOUT),
        ],
        capture_output=True,
        text=True,
        timeout=OPENCLAW_TIMEOUT + 30,
        stdin=subprocess.DEVNULL,
        env=env,
    )
    return _extract_inner(result.stdout.strip())


def call_form_answerer(brief: dict) -> str:
    """Dispatch form-answerer skill via openclaw agent CLI. Returns raw output text."""
    session_id = f"bridge-fa-{int(time.time())}"
    message = (
        f"[{FORM_ANSWERER_SKILL_NAME.upper()} SKILL]\n"
        "Brief reçu depuis n8n :\n"
        + json.dumps(brief, ensure_ascii=False, indent=2)
        + f"\n\nApplique la skill {FORM_ANSWERER_SKILL_NAME} selon SKILL.md."
    )
    env = os.environ.copy()
    env["OPENCLAW_CONFIG_PATH"] = OPENCLAW_CONFIG
    env.setdefault("HOME", "/home/thehunter")
    result = subprocess.run(
        [
            "openclaw", "agent",
            "--agent", AGENT_ID,
            "--session-id", session_id,
            "--message", message,
            "--json",
            "--timeout", str(OPENCLAW_TIMEOUT),
        ],
        capture_output=True,
        text=True,
        timeout=OPENCLAW_TIMEOUT + 30,
        stdin=subprocess.DEVNULL,
        env=env,
    )
    return _extract_inner(result.stdout.strip())


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Hunter Bridge", version="0.1.0")


class RewriteCvRequest(BaseModel):
    offer_description: str = Field(..., min_length=1)
    matched_keywords: list[str] = Field(default_factory=list)
    company: str = Field(..., min_length=1)
    profile_tag: str | None = None
    language: str | None = None


class RewriteCvResponse(BaseModel):
    ok: bool
    filename: str | None = None
    cv_markdown: str | None = None
    language: str | None = None
    profile_tag: str | None = None
    error: str | None = None
    forbidden_phrases_found: list[str] | None = None


class CoverLetterRequest(BaseModel):
    offer_description: str = Field(..., min_length=1)
    company: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    language: str | None = None
    cv_summary: str | None = None
    matched_keywords: list[str] = Field(default_factory=list)
    profile_tag: str | None = None


class CoverLetterResponse(BaseModel):
    ok: bool
    filename: str | None = None
    letter_markdown: str | None = None
    language: str | None = None
    word_count: int | None = None
    error: str | None = None
    forbidden_phrases_found: list[str] | None = None


class FormAnswerQuestion(BaseModel):
    id: str
    label: str
    type: str | None = None
    options: list[str] | None = None


class FormAnswersRequest(BaseModel):
    questions: list[FormAnswerQuestion]
    offer_description: str = Field(..., min_length=1)
    company: str | None = None
    title: str | None = None
    url: str | None = None
    language: str | None = None


class FormAnswerItem(BaseModel):
    id: str
    answer: str
    source: str  # "precalibrated" | "generated"


class FormAnswersResponse(BaseModel):
    ok: bool
    answers: list[FormAnswerItem] | None = None
    error: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "hunter-bridge", "agent": AGENT_ID, "port": PORT}


@app.post("/rewrite-cv", response_model=RewriteCvResponse)
def rewrite_cv(req: RewriteCvRequest) -> RewriteCvResponse:
    """Generate ATS-optimised CV via cv-rewriter skill. Rate-limited to 5/hour."""
    if not _cv_rate_limiter.check_and_record():
        raise HTTPException(
            status_code=429,
            detail={"ok": False, "error": "rate_limit: max 5 CV generations per hour"},
        )

    language = detect_language(req.offer_description, req.language)
    profile_tag = detect_profile_tag(
        req.offer_description, req.matched_keywords, req.profile_tag
    )
    brief = {
        "company": req.company,
        "description": req.offer_description,
        "language": language,
        "skills_found": req.matched_keywords,
        "profile_tag": profile_tag,
    }
    filename = generate_cv_filename(req.company)

    try:
        raw_output = call_cv_rewriter(brief)
    except subprocess.TimeoutExpired:
        return RewriteCvResponse(
            ok=False, error="timeout: OpenClaw cv-rewriter exceeded 600s"
        )
    except Exception as exc:
        return RewriteCvResponse(ok=False, error=str(exc))

    try:
        cv_markdown = parse_cv_wrapper(raw_output)
    except ValueError as exc:
        return RewriteCvResponse(ok=False, error=str(exc))

    forbidden = validate_forbidden_phrases(cv_markdown)
    if forbidden:
        logger.warning("Forbidden phrases in generated CV: %s", forbidden)

    try:
        CV_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (CV_OUTPUT_DIR / filename).write_text(cv_markdown, encoding="utf-8")
        logger.info("CV saved: %s", CV_OUTPUT_DIR / filename)
    except Exception as exc:
        logger.error("Failed to save CV: %s", exc)

    return RewriteCvResponse(
        ok=True,
        filename=filename,
        cv_markdown=cv_markdown,
        language=language,
        profile_tag=profile_tag,
        forbidden_phrases_found=forbidden if forbidden else None,
    )


@app.post("/cover-letter", response_model=CoverLetterResponse)
def cover_letter(req: CoverLetterRequest) -> CoverLetterResponse:
    """Generate cover letter via cover-letter-writer skill. Rate-limited to 5/hour."""
    if not _cl_rate_limiter.check_and_record():
        raise HTTPException(
            status_code=429,
            detail={"ok": False, "error": "rate_limit: max 5 cover letters per hour"},
        )

    language = detect_language(req.offer_description, req.language)
    profile_tag = detect_profile_tag(
        req.offer_description, req.matched_keywords, req.profile_tag
    )
    brief = {
        "company": req.company,
        "role": req.role,
        "description": req.offer_description,
        "language": language,
        "skills_found": req.matched_keywords,
        "profile_tag": profile_tag,
        "cv_summary": req.cv_summary,
    }
    filename = generate_letter_filename(req.company)

    try:
        raw_output = call_cover_letter_writer(brief)
    except subprocess.TimeoutExpired:
        return CoverLetterResponse(
            ok=False, error="timeout: OpenClaw cover-letter-writer exceeded 600s"
        )
    except Exception as exc:
        return CoverLetterResponse(ok=False, error=str(exc))

    try:
        letter_markdown = parse_letter_wrapper(raw_output)
    except ValueError as exc:
        return CoverLetterResponse(ok=False, error=str(exc))

    forbidden = validate_forbidden_phrases(letter_markdown)
    if forbidden:
        logger.warning("Forbidden phrases in generated cover letter: %s", forbidden)

    wc = count_words(letter_markdown)
    logger.info("Cover letter word count: %d (target 250-350)", wc)

    try:
        LM_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (LM_OUTPUT_DIR / filename).write_text(letter_markdown, encoding="utf-8")
        logger.info("Cover letter saved: %s", LM_OUTPUT_DIR / filename)
    except Exception as exc:
        logger.error("Failed to save cover letter: %s", exc)

    return CoverLetterResponse(
        ok=True,
        filename=filename,
        letter_markdown=letter_markdown,
        language=language,
        word_count=wc,
        forbidden_phrases_found=forbidden if forbidden else None,
    )


@app.post("/form-answers", response_model=FormAnswersResponse)
def form_answers(req: FormAnswersRequest) -> FormAnswersResponse:
    """Generate form answers: pre-calibrated from USER.md + Haiku for open questions. Rate-limited to 20/hour."""
    if not _fa_rate_limiter.check_and_record():
        raise HTTPException(
            status_code=429,
            detail={"ok": False, "error": "rate_limit: max 20 form answer requests per hour"},
        )

    if not req.questions:
        return FormAnswersResponse(ok=True, answers=[])

    language = detect_language(req.offer_description, req.language)
    user_answers = _parse_user_md_precal()

    precalibrated: list[FormAnswerItem] = []
    open_questions: list[dict] = []

    for q in req.questions:
        answer = _match_precalibrated(q.label, user_answers)
        if answer is not None:
            precalibrated.append(FormAnswerItem(id=q.id, answer=answer, source="precalibrated"))
        else:
            open_questions.append({
                "id": q.id,
                "label": q.label,
                "type": q.type,
                "options": q.options,
            })

    if not open_questions:
        return FormAnswersResponse(ok=True, answers=precalibrated)

    brief = {
        "title": req.title,
        "company": req.company,
        "url": req.url,
        "language": language,
        "questions": open_questions,
    }

    try:
        raw_output = call_form_answerer(brief)
    except subprocess.TimeoutExpired:
        return FormAnswersResponse(
            ok=False, error="timeout: OpenClaw form-answerer exceeded 600s"
        )
    except Exception as exc:
        return FormAnswersResponse(ok=False, error=str(exc))

    try:
        generated = parse_answers_wrapper(raw_output)
    except (ValueError, json.JSONDecodeError) as exc:
        return FormAnswersResponse(ok=False, error=str(exc))

    all_answers: list[FormAnswerItem] = precalibrated + [
        FormAnswerItem(id=a["id"], answer=a.get("answer", ""), source="generated")
        for a in generated
        if isinstance(a, dict) and "id" in a
    ]

    return FormAnswersResponse(ok=True, answers=all_answers)


class StoreDocumentsRequest(BaseModel):
    job_id: str = Field(..., min_length=1)
    company: str = Field(..., min_length=1)
    position: str = Field(..., min_length=1)
    offer_url: str = Field(..., min_length=1)
    detection_date: str = Field(..., min_length=1)
    match_rate: float | None = None
    language: str | None = None
    cv_markdown: str = Field(..., min_length=1)
    lm_markdown: str = Field(..., min_length=1)
    application_type: str = Field(..., min_length=1)
    form_questions_count: int = 0


class StoreDocumentsResponse(BaseModel):
    ok: bool
    cv_drive_url: str | None = None
    lm_drive_url: str | None = None
    telegram_message: dict | None = None
    error: str | None = None


@app.post("/store-documents", response_model=StoreDocumentsResponse)
def store_documents(req: StoreDocumentsRequest) -> StoreDocumentsResponse:
    """Upload CV+letter to Drive, update MATCHES, return Telegram notification payload. Rate-limited to 10/hour."""
    if not _sd_rate_limiter.check_and_record():
        raise HTTPException(
            status_code=429,
            detail={"ok": False, "error": "rate_limit: max 10 store-documents requests per hour"},
        )

    year_month = req.detection_date[:7]

    metadata = {
        "Company": req.company,
        "Position": req.position,
        "Offer URL": req.offer_url,
        "Detection date": req.detection_date,
        "Match Rate": req.match_rate if req.match_rate is not None else "",
        "Language": req.language or "",
        "Status": drive_uploader.STATUS_GENERATED,
    }

    cv_with_header = drive_uploader.prepend_yaml_header(req.cv_markdown, metadata)
    lm_with_header = drive_uploader.prepend_yaml_header(req.lm_markdown, metadata)

    cv_filename = generate_cv_filename(req.company, req.detection_date)
    lm_filename = generate_letter_filename(req.company, req.detection_date)

    uploader = drive_uploader.DriveUploader()

    try:
        cv_url = uploader.upload_document(cv_with_header, cv_filename, year_month)
        lm_url = uploader.upload_document(lm_with_header, lm_filename, year_month)
    except Exception as exc:
        logger.error("Drive upload failed: %s", exc)
        return StoreDocumentsResponse(ok=False, error=str(exc))

    uploader.update_matches(req.job_id, cv_url, lm_url)

    notification = drive_uploader.build_telegram_notification(
        company=req.company,
        position=req.position,
        cv_url=cv_url,
        lm_url=lm_url,
        offer_url=req.offer_url,
        job_id=req.job_id,
        application_type=req.application_type,
        form_questions_count=req.form_questions_count,
    )

    return StoreDocumentsResponse(
        ok=True,
        cv_drive_url=cv_url,
        lm_drive_url=lm_url,
        telegram_message=notification,
    )


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run("hunter_bridge:app", host="127.0.0.1", port=PORT, reload=False)

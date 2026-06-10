#!/usr/bin/env python3
"""
Hunter Bridge — FastAPI server for CV generation.

Routes:
  GET  /health      -- healthcheck
  POST /rewrite-cv  -- cv-rewriter skill (Sonnet), rate-limited to 5/hour
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
AGENT_ID: Final[str] = "the-hunter"
SKILL_NAME: Final[str] = "cv-rewriter"
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

# ── CV parsing ────────────────────────────────────────────────────────────────

_CV_START: Final[str] = "[CV_START]"
_CV_END: Final[str] = "[CV_END]"


def parse_cv_wrapper(raw: str) -> str:
    """Extract CV markdown from [CV_START]...[CV_END] markers."""
    start_idx = raw.find(_CV_START)
    if start_idx == -1:
        raise ValueError(f"Missing {_CV_START} marker in OpenClaw response")
    end_idx = raw.find(_CV_END, start_idx)
    if end_idx == -1:
        raise ValueError(f"Missing {_CV_END} marker in OpenClaw response")
    return raw[start_idx + len(_CV_START):end_idx].strip()


# ── Filename generation ───────────────────────────────────────────────────────

_UNSAFE_CHARS: Final[re.Pattern[str]] = re.compile(r"[^\w]")


def generate_cv_filename(company: str, today: str | None = None) -> str:
    """Return CV_Patricia_Wintrebert_{Company}_{YYYY-MM-DD}.md"""
    if today is None:
        today = date.today().isoformat()
    clean = _UNSAFE_CHARS.sub("_", company)
    clean = re.sub(r"_+", "_", clean).strip("_")
    return f"CV_Patricia_Wintrebert_{clean}_{today}.md"


# ── Forbidden phrases validation ──────────────────────────────────────────────

def validate_forbidden_phrases(cv_text: str) -> list[str]:
    """Return list of forbidden phrases found in the CV (case-insensitive)."""
    text_lower = cv_text.lower()
    return [phrase for phrase in FORBIDDEN_PHRASES if phrase in text_lower]


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


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run("hunter_bridge:app", host="127.0.0.1", port=PORT, reload=False)

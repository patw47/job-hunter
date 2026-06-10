"""Layer 2 : moteur de calcul du match rate offre vs SKILLS_MASTER."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from gspread import Worksheet

logger = logging.getLogger(__name__)

MATCH_THRESHOLD: Final[float] = 60.0

_WORD_SEP: Final[re.Pattern] = re.compile(r"[^\w]+")


def _resolve_token(
    token: str,
    skills_lower: dict[str, str],
    alias_lower: dict[str, list[str]],
    visited: set[str],
) -> str | None:
    """Resolve lowercased token to canonical skill via direct match or alias chain.

    Handles transitive chains (e.g. "gen ai" → "genai" → "llm") and prevents
    infinite loops on cyclic alias definitions.
    """
    if token in visited:
        return None
    visited.add(token)
    if token in skills_lower:
        return skills_lower[token]
    for synonym in alias_lower.get(token, []):
        resolved = _resolve_token(synonym.lower(), skills_lower, alias_lower, visited)
        if resolved is not None:
            return resolved
    return None


def _extract_offer_keywords(
    text_lower: str,
    all_terms: list[str],
) -> list[str]:
    """Scan lowercased offer text for recognized phrases, then tokenise gaps.

    Recognized phrases (skills_master + alias keys, longest first) are found via
    word-boundary regex. Remaining text is split into individual words.
    """
    if not all_terms:
        return [t for t in _WORD_SEP.split(text_lower) if t]

    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(t) for t in all_terms) + r")(?!\w)",
    )

    result: list[str] = []
    last_end = 0
    for m in pattern.finditer(text_lower):
        gap = text_lower[last_end : m.start()]
        result.extend(t for t in _WORD_SEP.split(gap) if t)
        result.append(m.group())
        last_end = m.end()
    gap = text_lower[last_end:]
    result.extend(t for t in _WORD_SEP.split(gap) if t)
    return result


def compute_match_rate(
    offer_text: str,
    skills_master: set[str],
    alias_table: dict[str, list[str]],
) -> tuple[float, list[str], list[str]]:
    """Compute match rate between offer text and skills_master.

    Scans offer_text for recognized skill phrases (skills_master + alias keys) using
    longest-match-first regex, then tokenises remaining text. Applies recursive alias
    resolution (with cycle detection) for each token.

    Returns:
        (rate, matched, missing)
        - rate: 0–100 float — len(matched) / len(offer_keywords) * 100
        - matched: canonical skill names from skills_master found in the offer
        - missing: offer tokens that could not be resolved to any skill
    """
    if not offer_text.strip():
        return 0.0, [], []

    skills_lower: dict[str, str] = {s.lower(): s for s in skills_master}
    alias_lower: dict[str, list[str]] = {k.lower(): v for k, v in alias_table.items()}

    all_terms: list[str] = sorted(
        set(skills_lower.keys()) | set(alias_lower.keys()),
        key=len,
        reverse=True,
    )

    offer_keywords = _extract_offer_keywords(offer_text.lower(), all_terms)
    if not offer_keywords:
        return 0.0, [], []

    matched: list[str] = []
    missing: list[str] = []

    for kw in offer_keywords:
        resolved = _resolve_token(kw, skills_lower, alias_lower, set())
        if resolved is not None:
            matched.append(resolved)
        else:
            missing.append(kw)

    rate = len(matched) / len(offer_keywords) * 100
    logger.debug(
        "Match rate: %.1f%% (%d/%d keywords)", rate, len(matched), len(offer_keywords)
    )
    return rate, matched, missing


def _remote_flag(job_type: str) -> str:
    """Derive MATCHES 'remote' column value from offer job_type."""
    jt = job_type.lower()
    if "remote" in jt:
        return "yes"
    if "hybrid" in jt:
        return "hybrid"
    return "no"


def write_match_if_qualified(
    offer: dict,
    rate: float,
    matched: list[str],
    worksheet: Worksheet,
    scan_date: str,
) -> bool:
    """Append offer to MATCHES worksheet if rate >= MATCH_THRESHOLD.

    Column order follows MATCHES header:
    job_id | date_scanned | title | company | location | remote | url | source |
    match_rate | skills_found | status | cv_drive_link | letter_drive_link | applied_at

    Returns True if written, False if rate is below threshold.
    """
    if rate < MATCH_THRESHOLD:
        logger.debug(
            "Below threshold (%.1f%% < %.1f%%): %s",
            rate,
            MATCH_THRESHOLD,
            offer.get("url", ""),
        )
        return False

    from deduplication import compute_hash

    row: list[str] = [
        compute_hash(offer["url"]),
        scan_date,
        offer.get("title", ""),
        offer.get("company", ""),
        offer.get("location", ""),
        _remote_flag(offer.get("job_type", "")),
        offer.get("url", ""),
        offer.get("source", ""),
        f"{rate:.1f}",
        ",".join(matched),
        "new",
        "",
        "",
        "",
    ]
    worksheet.append_row(row, value_input_option="RAW")
    logger.info("Match written: %s (%.1f%%)", offer.get("title", ""), rate)
    return True

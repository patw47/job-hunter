from __future__ import annotations

import logging
import re
from typing import Final

logger = logging.getLogger(__name__)

# ── Tolerated hybrid zones (section 4.1) ────────────────────────────────────

VALAIS_CITIES: Final[tuple[str, ...]] = (
    "crans-montana",
    "sierre",
    "sion",
    "martigny",
    "visp",
    "brig",
    "monthey",
    "valais",
)

NOUVELLE_AQUITAINE_CITIES: Final[tuple[str, ...]] = (
    "biscarrosse",
    "bordeaux",
    "mérignac",
    "merignac",
    "mont-de-marsan",
    "dax",
    "marmande",
    "agen",
    "villeneuve-sur-lot",
    "nouvelle-aquitaine",
)

_TOLERATED_ZONE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in VALAIS_CITIES + NOUVELLE_AQUITAINE_CITIES) + r")\b",
    re.IGNORECASE,
)

# ── Work-type patterns ────────────────────────────────────────────────────────
# job_type field values: update once indeed_mcp_findings.md is validated with confirmed MCP values.

_REMOTE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(remote|full[\s\-]remote|t[eé]l[eé]travail|work[\s\-]from[\s\-]home|wfh|fully[\s\-]remote|telecommut\w*)\b",
    re.IGNORECASE,
)
_HYBRID_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(hybrid|hybride)\b",
    re.IGNORECASE,
)
_ONSITE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(on-?site|in[\s\-]office|pr[eé]sentiel|office[\s\-]only|sur[\s\-]site)\b",
    re.IGNORECASE,
)
_FULL_REMOTE_POSSIBLE_RE: Final[re.Pattern[str]] = re.compile(
    r"full[\s\-]+remote[\s\-]+possible",
    re.IGNORECASE,
)
# LinkedIn lists located jobs as "City, Region, Country" but country-wide
# remote jobs as the bare country name — a strong structural remote signal.
_COUNTRY_ONLY_LOCATIONS: Final[frozenset[str]] = frozenset({
    "suisse", "switzerland", "schweiz", "svizzera",
    "france", "royaume-uni", "united kingdom", "uk", "angleterre", "england",
    "union européenne", "european union", "europe",
    "allemagne", "germany", "deutschland", "pays-bas", "netherlands",
    "irlande", "ireland", "espagne", "spain", "portugal", "italie", "italy",
    "belgique", "belgium", "autriche", "austria", "luxembourg",
    "suède", "sweden", "norvège", "norway", "danemark", "denmark",
    "finlande", "finland", "pologne", "poland", "estonie", "estonia",
    "lettonie", "latvia", "lituanie", "lithuania", "tchéquie", "czechia",
})

# Prose mentions "remote" loosely ("remote-friendly culture", "no remote") —
# only explicit phrasing qualifies a description as remote.
_STRONG_REMOTE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(fully[\s\-]remote|100\s*%\s*remote|full[\s\-]remote|remote[\s\-]first"
    r"|work[\s\-]from[\s\-]anywhere|t[eé]l[eé]travail\s+(?:complet|total|int[eé]gral)"
    r"|enti[eè]rement\s+[aà]\s+distance)\b",
    re.IGNORECASE,
)

# ── Junior/internship filter ─────────────────────────────────────────────────

_JUNIOR_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(junior|internship|alternance|stage|intern|apprenti)\b",
    re.IGNORECASE,
)


def _detect_work_type(offer: dict) -> str:
    """Cascade: job_type → location → title → description. Returns remote/hybrid/onsite/unknown.

    Short structured fields trust any marker. In the description, explicit
    onsite/hybrid wording wins over a loose 'remote' mention ('Expected to
    work onsite Tuesday–Thursday' must reject even if 'remote' appears
    elsewhere in the text), and only strong remote phrasing qualifies.
    """
    for field in ("job_type", "location", "title"):
        text = offer.get(field) or ""
        if _REMOTE_RE.search(text):
            return "remote"
        if _HYBRID_RE.search(text):
            return "hybrid"
        if _ONSITE_RE.search(text):
            return "onsite"
    body = offer.get("description") or ""
    if _ONSITE_RE.search(body):
        return "onsite"
    if (offer.get("location") or "").strip().lower() in _COUNTRY_ONLY_LOCATIONS:
        return "remote"
    if _HYBRID_RE.search(body):
        return "hybrid"
    if _STRONG_REMOTE_RE.search(body):
        return "remote"
    return "unknown"


def _is_in_tolerated_zone(offer: dict) -> bool:
    """Return True if offer location matches a tolerated hybrid zone (Valais or Nouvelle-Aquitaine)."""
    return bool(_TOLERATED_ZONE_RE.search(offer.get("location") or ""))


def apply_layer1(offer: dict) -> tuple[bool, str]:
    """Apply layer 1 disqualifying filters. Returns (pass, reason). pass=False blocks layer 2."""
    title = offer.get("title") or ""
    if _JUNIOR_RE.search(title):
        reason = "REJECTED: junior/internship level"
        logger.debug("Layer 1 — %s | title=%s", reason, title)
        return (False, reason)

    work_type = _detect_work_type(offer)

    if work_type == "remote":
        return (True, "PASS: remote")

    if work_type == "onsite":
        reason = "REJECTED: on-site"
        logger.debug("Layer 1 — %s | url=%s", reason, offer.get("url"))
        return (False, reason)

    if work_type == "hybrid":
        if _is_in_tolerated_zone(offer):
            return (True, "PASS: hybrid in tolerated zone")
        body = offer.get("description") or ""
        if _FULL_REMOTE_POSSIBLE_RE.search(body):
            return (True, "PASS: hybrid with full remote possible")
        reason = "REJECTED: hybrid outside tolerated zones"
        logger.debug("Layer 1 — %s | location=%s", reason, offer.get("location"))
        return (False, reason)

    reason = "IGNORED: no work type information"
    logger.debug("Layer 1 — %s | url=%s", reason, offer.get("url"))
    return (False, reason)

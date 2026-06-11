"""Merge Indeed + LinkedIn offer arrays with URL-based intra-dedup."""
from __future__ import annotations

import logging

from deduplication import normalize_url

logger = logging.getLogger(__name__)

INDEED_CAP: int = 60
LINKEDIN_CAP: int = 40
GLOBAL_CAP: int = 100


def merge_offers(
    indeed_offers: list[dict],
    linkedin_offers: list[dict],
) -> list[dict]:
    """Merge Indeed + LinkedIn offers, dedup by URL, cap at GLOBAL_CAP.

    Indeed offers first (already scored), LinkedIn appended after.
    match_rate and skills_found are null for LinkedIn (scored later by Layer 2).
    """
    indeed_capped = indeed_offers[:INDEED_CAP]
    linkedin_capped = linkedin_offers[:LINKEDIN_CAP]

    seen: set[str] = set()
    merged: list[dict] = []

    for offer in indeed_capped:
        url = offer.get("url", "")
        key = normalize_url(url)
        if key and key not in seen:
            seen.add(key)
            merged.append(
                {
                    "url": url,
                    "title": offer.get("title", ""),
                    "company": offer.get("company", ""),
                    "location": offer.get("location", ""),
                    "description": offer.get("description", ""),
                    "job_type": offer.get("job_type", ""),
                    "date_posted": offer.get("date_posted") or offer.get("date_scanned", ""),
                    "source": "indeed",
                    "match_rate": offer.get("match_rate") or None,
                    "skills_found": offer.get("skills_found") or None,
                }
            )

    for offer in linkedin_capped:
        url = offer.get("url", "")
        key = normalize_url(url)
        if key and key not in seen:
            seen.add(key)
            merged.append(
                {
                    "url": url,
                    "title": offer.get("title", ""),
                    "company": offer.get("company", ""),
                    "location": offer.get("location", ""),
                    "description": offer.get("description", ""),
                    "job_type": offer.get("job_type", ""),
                    "date_posted": offer.get("date_posted", ""),
                    "source": "linkedin",
                    "match_rate": None,
                    "skills_found": None,
                }
            )

    result = merged[:GLOBAL_CAP]
    logger.info(
        "Merge: indeed=%d linkedin=%d → merged=%d",
        len(indeed_capped),
        len(linkedin_capped),
        len(result),
    )
    return result

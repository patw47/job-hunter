#!/home/thehunter/venv/bin/python3
"""
LinkedIn Playwright scraper.

Two-pass remote/hybrid job scanner for LinkedIn.
Pass 1: f_WT=3 (remote). Pass 2: f_WT=2,3 (remote+hybrid) if pass 1 < 10 unique URLs.
Cap: 40 offers globally. Delays: 3-8s between requests.

Usage:
    LINKEDIN_EMAIL=<e> LINKEDIN_PASSWORD=<p> python3 /opt/apps/job-hunter/linkedin_scraper.py

Stdout: JSON array of normalized offers.
Stderr: progress/info logs.
Exit 0: success. Exit 1: CAPTCHA detected or fatal error.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

LINKEDIN_EMAIL: str = os.environ.get("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD: str = os.environ.get("LINKEDIN_PASSWORD", "")
SESSION_DIR: Path = Path(
    os.environ.get("LINKEDIN_SESSION_DIR", "/opt/apps/job-hunter/sessions")
)

SCAN_ROOTS: list[str] = [
    "AI", "agent", "agentic", "GenAI", "automation",
    "LLM", "RAG", "ML", "full stack", "n8n", "Python", "developer",
]

# Geographic targets from USER.md (priority order): Switzerland, UK, European
# Union. Without geoId the guest endpoint serves US-centric recommendations.
GEO_IDS: list[tuple[str, str]] = [
    ("106693272", "Switzerland"),
    ("101165590", "United Kingdom"),
    ("91000000", "European Union"),
]
_geo_env = os.environ.get("LINKEDIN_GEO_IDS", "")
if _geo_env.strip():
    GEO_IDS = [(g.strip(), g.strip()) for g in _geo_env.split(",") if g.strip()]
HYBRID_THRESHOLD: int = 10
GLOBAL_CAP: int = 40
DELAY_MIN: float = 3.0
DELAY_MAX: float = 8.0

# Guest jobs API: returns parseable base-card HTML without authentication.
# The authenticated /jobs/search/ page is unusable from a datacenter IP
# (authwall when logged out, redirect loop with a bare li_at cookie).
_SEARCH_BASE: str = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_JOB_POSTING_BASE: str = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/"

# Layer 2 scores title + description, and CV generation needs the offer text:
# guest search cards carry no description, so each offer is enriched from the
# guest jobPosting endpoint after the scan passes.
DESCRIPTION_MAX: int = 3000
_JOB_ID_RE: re.Pattern[str] = re.compile(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})")
_DESCRIPTION_SELECTORS: tuple[str, ...] = (
    "div.show-more-less-html__markup",
    "div.description__text",
    "section.show-more-less-html",
)
_LOGIN_URL: str = "https://www.linkedin.com/login"

_CAPTCHA_MARKERS: tuple[str, ...] = (
    "captcha",
    "challenge",
    "security check",
    "verify you're a human",
    "are you a robot",
    "unusual activity",
    "authwall",
)

_USER_AGENT: str = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Selectors for job cards — multiple fallbacks for DOM resilience
_JOB_CARD_SELECTORS: tuple[str, ...] = (
    "div.job-search-card",
    "li.jobs-search-results__list-item",
    "[data-occludable-job-id]",
    "div.base-card",
)
_TITLE_SELECTORS: tuple[str, ...] = (
    "a.base-card__full-link",
    "a.job-card-list__title",
    "h3.base-search-card__title a",
    "a[data-tracking-control-name*='job_card_title']",
)
_COMPANY_SELECTORS: tuple[str, ...] = (
    "a.hidden-nested-link",
    "h4.base-search-card__subtitle a",
    ".job-card-container__company-name",
    ".base-search-card__subtitle",
)
_LOCATION_SELECTORS: tuple[str, ...] = (
    "span.job-search-card__location",
    ".base-search-card__metadata",
    ".job-card-container__metadata-item",
)
_DATE_SELECTORS: tuple[str, ...] = (
    "time.job-search-card__listdate",
    "time[datetime]",
)


# ── Exceptions ─────────────────────────────────────────────────────────────────


class LinkedInScraperError(Exception):
    """Base error for LinkedIn scraper."""


class CaptchaError(LinkedInScraperError):
    """CAPTCHA or security challenge detected — abort immediately."""


class LoginError(LinkedInScraperError):
    """LinkedIn login failed."""


# ── Session management ─────────────────────────────────────────────────────────


def _session_file() -> Path:
    """Path for today's session cookie file."""
    return SESSION_DIR / f"linkedin_session_{date.today().isoformat()}.json"


def load_session() -> list[dict] | None:
    """Load today's cookies if available. Returns None when missing or invalid."""
    path = _session_file()
    if not path.exists():
        return None
    try:
        cookies = json.loads(path.read_text())
        if isinstance(cookies, list) and cookies:
            logger.info("Loaded session from %s (%d cookies)", path, len(cookies))
            return cookies
        return None
    except Exception as exc:
        logger.warning("Failed to load session file %s: %s", path, exc)
        return None


def save_session(cookies: list[dict]) -> None:
    """Persist today's cookies to SESSION_DIR."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path = _session_file()
    path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2))
    logger.info("Session saved to %s (%d cookies)", path, len(cookies))


# ── CAPTCHA detection ──────────────────────────────────────────────────────────


def detect_captcha(url: str, content: str) -> bool:
    """Return True if CAPTCHA or security challenge is present in url or content."""
    check = (url + content).lower()
    return any(marker in check for marker in _CAPTCHA_MARKERS)


# ── Offer normalization ────────────────────────────────────────────────────────


def normalize_offer(raw: dict) -> dict:
    """Normalize a raw scraped job to the 7-field canonical format + source."""
    def _get(*keys: str) -> str:
        for k in keys:
            v = raw.get(k)
            if v is not None:
                return str(v).strip()
        return ""

    return {
        "url": _get("url", "link", "jobUrl"),
        "title": _get("title", "jobTitle", "name"),
        "company": _get("company", "companyName", "employer"),
        "location": _get("location", "formattedLocation", "city"),
        "description": _get("description", "snippet", "summary")[:500],
        "job_type": _get("job_type", "jobType", "employmentType", "workType"),
        "date_posted": _get("date_posted", "datePosted", "listedAt", "postedAt"),
        "source": "linkedin",
    }


# ── Playwright helpers ─────────────────────────────────────────────────────────


async def _text_of(element: Any, selectors: tuple[str, ...]) -> str:
    """Extract text content from first matching child selector."""
    for sel in selectors:
        try:
            el = await element.query_selector(sel)
            if el:
                text = (await el.text_content() or "").strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


async def _href_of(element: Any, selectors: tuple[str, ...]) -> str:
    """Extract href from first matching child selector, stripping query params."""
    for sel in selectors:
        try:
            el = await element.query_selector(sel)
            if el:
                href = await el.get_attribute("href") or ""
                if href:
                    return href.split("?")[0].strip()
        except Exception:
            continue
    return ""


async def _date_of(element: Any) -> str:
    """Extract datetime attribute or text from time element."""
    for sel in _DATE_SELECTORS:
        try:
            el = await element.query_selector(sel)
            if el:
                dt = await el.get_attribute("datetime") or ""
                if dt:
                    return dt
                text = (await el.text_content() or "").strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


async def _scrape_jobs_from_page(page: Any) -> list[dict]:
    """Parse job cards from current search results page."""
    for sel in _JOB_CARD_SELECTORS:
        try:
            await page.wait_for_selector(sel, timeout=10000)
            cards = await page.query_selector_all(sel)
            if not cards:
                continue
            logger.debug("Found %d cards with selector: %s", len(cards), sel)
            raw_offers: list[dict] = []
            for card in cards:
                title = await _text_of(card, _TITLE_SELECTORS)
                url = await _href_of(card, _TITLE_SELECTORS)
                company = await _text_of(card, _COMPANY_SELECTORS)
                location = await _text_of(card, _LOCATION_SELECTORS)
                date_posted = await _date_of(card)
                raw_offers.append({
                    "url": url,
                    "title": title,
                    "company": company,
                    "location": location,
                    "description": "",
                    "job_type": "",
                    "date_posted": date_posted,
                })
            return raw_offers
        except Exception:
            continue
    logger.warning("No job cards found — all selectors failed")
    return []


async def _try_login(page: Any) -> None:
    """Attempt LinkedIn login using LINKEDIN_EMAIL / LINKEDIN_PASSWORD env vars."""
    if not LINKEDIN_EMAIL or not LINKEDIN_PASSWORD:
        raise LoginError("LINKEDIN_EMAIL and LINKEDIN_PASSWORD must be set")

    logger.info("Navigating to login page")
    await page.goto(_LOGIN_URL, wait_until="networkidle", timeout=30000)
    if detect_captcha(page.url, await page.content()):
        raise CaptchaError("CAPTCHA on login page")

    await page.fill("#username", LINKEDIN_EMAIL)
    await page.fill("#password", LINKEDIN_PASSWORD)
    await page.click("button[type='submit']")
    await page.wait_for_load_state("networkidle", timeout=30000)

    url = page.url
    content = await page.content()
    if detect_captcha(url, content):
        raise CaptchaError("CAPTCHA after login attempt")
    if "checkpoint" in url or "challenge" in url:
        raise CaptchaError(f"Security challenge after login: {url}")
    if "login" in url:
        raise LoginError(f"Still on login page after submit — credentials wrong? url={url}")

    logger.info("Login successful")


async def _search_root(
    page: Any,
    query: str,
    f_wt: str,
    seen_urls: set[str],
    remaining: int,
    geo_id: str = "",
) -> list[dict]:
    """Navigate to a LinkedIn job search URL and collect up to `remaining` new unique offers."""
    encoded_query = query.replace(" ", "%20")
    geo_param = f"&geoId={geo_id}" if geo_id else ""
    url = f"{_SEARCH_BASE}?keywords={encoded_query}&f_WT={f_wt}{geo_param}&position=1&pageNum=0"
    logger.info("GET %s", url)
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception as exc:
        logger.warning("Navigation failed for %s: %s", url, exc)
        return []

    if detect_captcha(page.url, await page.content()):
        raise CaptchaError(f"CAPTCHA on search page (query={query!r}, f_WT={f_wt})")

    raw_cards = await _scrape_jobs_from_page(page)
    new_offers: list[dict] = []
    for raw in raw_cards:
        norm = normalize_offer(raw)
        offer_url = norm["url"]
        if offer_url and offer_url not in seen_urls:
            seen_urls.add(offer_url)
            new_offers.append(norm)
            if len(new_offers) >= remaining:
                break
    return new_offers


def extract_job_id(url: str) -> str:
    """Extract the numeric LinkedIn job id from a /jobs/view/ URL ('' if absent)."""
    m = _JOB_ID_RE.search(url or "")
    return m.group(1) if m else ""


async def _fetch_description(page: Any, offer_url: str) -> str:
    """Fetch the offer description from the guest jobPosting endpoint.

    Best-effort: any failure (no job id, navigation error, authwall redirect,
    unknown DOM) returns '' without aborting the scan.
    """
    job_id = extract_job_id(offer_url)
    if not job_id:
        return ""
    try:
        await page.goto(
            f"{_JOB_POSTING_BASE}{job_id}", wait_until="domcontentloaded", timeout=20000
        )
    except Exception as exc:
        logger.warning("Description fetch failed for job %s: %s", job_id, exc)
        return ""
    # URL check only: description bodies legitimately contain words like
    # "challenge", so content-based CAPTCHA markers would false-positive here.
    if "authwall" in page.url or "/login" in page.url:
        logger.warning("Authwall on job posting %s — description skipped", job_id)
        return ""
    for sel in _DESCRIPTION_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text()).strip()
                if text:
                    return text[:DESCRIPTION_MAX]
        except Exception:
            continue
    return ""


async def _enrich_descriptions(page: Any, offers: list[dict]) -> None:
    """Fill offer['description'] in place for every offer, with anti-bot delays."""
    if not offers:
        return
    logger.info("=== Enriching %d offers with descriptions ===", len(offers))
    for i, offer in enumerate(offers, start=1):
        desc = await _fetch_description(page, offer.get("url", ""))
        offer["description"] = desc
        logger.info("  [%d/%d] %d chars — %s", i, len(offers), len(desc), offer.get("title", "")[:60])
        await _random_delay()


async def _random_delay() -> None:
    """Sleep a random duration in [DELAY_MIN, DELAY_MAX] seconds."""
    delay = random.uniform(DELAY_MIN, DELAY_MAX)
    logger.info("Delay %.1fs", delay)
    await asyncio.sleep(delay)


# ── Main scan logic ────────────────────────────────────────────────────────────


async def run_scan() -> list[dict]:
    """
    Execute two-pass LinkedIn scan.

    Pass 1: Remote (f_WT=3) over all SCAN_ROOTS.
    Pass 2: Remote+Hybrid (f_WT=2,3) if remote URLs < HYBRID_THRESHOLD.
    Returns up to GLOBAL_CAP normalized offers, deduplicated by URL.
    Raises CaptchaError immediately if CAPTCHA is detected.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise LinkedInScraperError(
            "playwright not installed — run: "
            "pip install playwright --break-system-packages && playwright install chromium"
        ) from exc

    all_offers: list[dict] = []
    seen_urls: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        ctx = await browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="fr-FR",
            extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"},
        )
        # Mask webdriver property to reduce bot detection likelihood
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await ctx.new_page()

        # Guest endpoint needs no auth; injecting session cookies on it
        # triggers redirect loops, so the scan always runs cookie-less.
        logger.info("Guest endpoint mode — no login required")

        # ── Pass 1: Remote (f_WT=3) ───────────────────────────────────────────
        logger.info(
            "=== Pass 1: Remote (f_WT=3) — %d roots × %d geos ===",
            len(SCAN_ROOTS), len(GEO_IDS),
        )
        for geo_id, geo_label in GEO_IDS:
            if len(seen_urls) >= GLOBAL_CAP:
                break
            for root in SCAN_ROOTS:
                remaining = GLOBAL_CAP - len(seen_urls)
                if remaining <= 0:
                    logger.info("Cap %d reached — stopping pass 1", GLOBAL_CAP)
                    break
                logger.info("[%s] remote — %s ...", root, geo_label)
                try:
                    new = await _search_root(page, f"{root} remote", "3", seen_urls, remaining, geo_id)
                    all_offers.extend(new)
                    logger.info("  +%d new (total=%d unique)", len(new), len(seen_urls))
                except CaptchaError:
                    await browser.close()
                    raise
                except Exception as exc:
                    logger.warning("  Error on root=%r: %s", root, exc)
                await _random_delay()

        remote_count = len(seen_urls)
        logger.info("Pass 1 done: %d unique remote URLs", remote_count)

        # ── Pass 2: Remote+Hybrid (f_WT=2,3) if needed ───────────────────────
        if remote_count < HYBRID_THRESHOLD:
            logger.info(
                "=== Pass 2: Remote+Hybrid (f_WT=2,3) — remote=%d < threshold=%d ===",
                remote_count, HYBRID_THRESHOLD,
            )
            for geo_id, geo_label in GEO_IDS:
                if len(seen_urls) >= GLOBAL_CAP:
                    break
                for root in SCAN_ROOTS:
                    remaining = GLOBAL_CAP - len(seen_urls)
                    if remaining <= 0:
                        logger.info("Cap %d reached — stopping pass 2", GLOBAL_CAP)
                        break
                    logger.info("[%s] hybrid — %s ...", root, geo_label)
                    try:
                        # f_WT=2,3 → URL-encoded as 2%2C3
                        new = await _search_root(page, f"{root} remote", "2%2C3", seen_urls, remaining, geo_id)
                        all_offers.extend(new)
                        logger.info("  +%d new (total=%d unique)", len(new), len(seen_urls))
                    except CaptchaError:
                        await browser.close()
                        raise
                    except Exception as exc:
                        logger.warning("  Error on root=%r: %s", root, exc)
                    await _random_delay()
        else:
            logger.info(
                "Pass 2 skipped: remote=%d >= threshold=%d", remote_count, HYBRID_THRESHOLD
            )

        offers = all_offers[:GLOBAL_CAP]
        await _enrich_descriptions(page, offers)
        await browser.close()

    return offers


# ── Entry point ────────────────────────────────────────────────────────────────


def main() -> int:
    """Entry point: run scan, print JSON to stdout, return exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        offers = asyncio.run(run_scan())
    except CaptchaError as exc:
        logger.error("CAPTCHA detected — aborting: %s", exc)
        return 1
    except LinkedInScraperError as exc:
        logger.error("Scraper error: %s", exc)
        return 1
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        return 1

    print(json.dumps(offers, ensure_ascii=False, indent=2))
    logger.info("Done: %d offers output (cap=%d)", len(offers), GLOBAL_CAP)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Form question detector — Playwright-based extraction of application form questions.

Usage as library:
    from form_detector import detect_form_questions
    questions = detect_form_questions("https://example.com/apply")

Usage as CLI:
    python form_detector.py <url>
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from typing import Final

logger = logging.getLogger(__name__)

_QUESTION_RE: Final[re.Pattern[str]] = re.compile(
    r"\?|^(?:How|Why|What|Describe|Tell|Explain|Do you)\b",
    re.IGNORECASE,
)

_WAIT_SELECTOR: Final[str] = "textarea, input[type='text']"
_GOTO_TIMEOUT: Final[int] = 30000  # ms


async def _detect_async(url: str) -> list[dict]:
    """Navigate to URL and extract question-like form field labels."""
    from playwright.async_api import async_playwright

    results: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=_GOTO_TIMEOUT)
            await page.wait_for_selector(_WAIT_SELECTOR, timeout=10000)

            elements = await page.query_selector_all(_WAIT_SELECTOR)

            for idx, el in enumerate(elements):
                tag: str = await el.evaluate("el => el.tagName.toLowerCase()")
                raw_id: str | None = await el.get_attribute("id")
                raw_name: str | None = await el.get_attribute("name")
                field_id = raw_id or raw_name or f"field_{idx}"
                field_type = "textarea" if tag == "textarea" else "text"

                label_text: str | None = None

                # 1. aria-label attribute
                label_text = await el.get_attribute("aria-label")

                # 2. <label for="..."> element
                if not label_text and raw_id:
                    label_el = await page.query_selector(f"label[for='{raw_id}']")
                    if label_el:
                        label_text = (await label_el.inner_text()).strip() or None

                # 3. placeholder fallback
                if not label_text:
                    label_text = await el.get_attribute("placeholder")

                if not label_text:
                    continue

                label_text = label_text.strip()
                if _QUESTION_RE.search(label_text):
                    results.append({"id": field_id, "label": label_text, "type": field_type})

        finally:
            await browser.close()

    return results


def detect_form_questions(url: str) -> list[dict]:
    """Return question-like form fields detected on the given URL.

    Returns list of {id: str, label: str, type: str}. Returns [] on any failure.
    """
    try:
        return asyncio.run(_detect_async(url))
    except Exception as exc:
        logger.warning("Form detection failed for %s: %s", url, str(exc)[:200])
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) != 2:
        print("Usage: python form_detector.py <url>", file=sys.stderr)
        sys.exit(1)
    target_url = sys.argv[1]
    detected = detect_form_questions(target_url)
    print(json.dumps(detected, ensure_ascii=False, indent=2))

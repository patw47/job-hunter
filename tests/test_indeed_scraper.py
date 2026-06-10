"""
Unit tests for indeed_scraper.py — 100% offline.

No network, no Playwright browser launch. Card parsing runs against mocked HTML
via BeautifulSoup; the browser layer is never imported (lazy import inside
_run_with_browser). main() is exercised by patching _run_with_browser.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import indeed_scraper as scraper


# ── normalize_offer ────────────────────────────────────────────────────────────


class TestNormalizeOffer(unittest.TestCase):
    def test_all_canonical_keys(self) -> None:
        raw = {
            "url": "https://x/1",
            "title": "AI Eng",
            "company": "Acme",
            "location": "Paris",
            "description": "desc",
            "job_type": "CDI",
            "datePosted": "2026-06-01",
        }
        out = scraper.normalize_offer(raw)
        assert out["url"] == "https://x/1"
        assert out["title"] == "AI Eng"
        assert out["company"] == "Acme"
        assert out["location"] == "Paris"
        assert out["description"] == "desc"
        assert out["job_type"] == "CDI"
        assert out["date_posted"] == "2026-06-01"
        assert out["source"] == "indeed"

    def test_url_alias_order(self) -> None:
        assert scraper.normalize_offer({"link": "L"})["url"] == "L"
        assert scraper.normalize_offer({"jobUrl": "J"})["url"] == "J"
        # url wins over link when both present
        assert scraper.normalize_offer({"url": "U", "link": "L"})["url"] == "U"

    def test_title_company_aliases(self) -> None:
        assert scraper.normalize_offer({"jobTitle": "T"})["title"] == "T"
        assert scraper.normalize_offer({"companyName": "C"})["company"] == "C"
        assert scraper.normalize_offer({"employer": "E"})["company"] == "E"

    def test_date_posted_aliases(self) -> None:
        assert scraper.normalize_offer({"postedAt": "p"})["date_posted"] == "p"
        assert scraper.normalize_offer({"formattedRelativeTime": "2 days ago"})["date_posted"] == "2 days ago"

    def test_description_truncated_to_500(self) -> None:
        out = scraper.normalize_offer({"description": "x" * 600})
        assert len(out["description"]) == 500

    def test_empty_raw_all_blank(self) -> None:
        out = scraper.normalize_offer({})
        for k in ("url", "title", "company", "location", "description", "job_type", "date_posted"):
            assert out[k] == "", f"{k} should be empty"
        assert out["source"] == "indeed"

    def test_numeric_coercion(self) -> None:
        assert scraper.normalize_offer({"url": 12345})["url"] == "12345"


# ── detect_captcha ───────────────────────────────────────────────────────────────


class TestDetectCaptcha(unittest.TestCase):
    POSITIVE = [
        "<title>Just a moment...</title>",
        '<div id="cf-browser-verification"></div>',
        "<p>Checking your browser before accessing</p>",
        "<p>Verifying you are human</p>",
        '<script src="https://hcaptcha.com/1/api.js"></script>',
        '<div class="h-captcha"></div>',
        "window._cf_chl_opt = {};",
        "<h1>Additional Verification Required</h1>",
    ]

    def test_positive_markers(self) -> None:
        for html in self.POSITIVE:
            with self.subTest(html=html[:30]):
                assert scraper.detect_captcha(html) is True

    def test_case_insensitive(self) -> None:
        assert scraper.detect_captcha("<TITLE>JUST A MOMENT...</TITLE>") is True

    def test_normal_page_false(self) -> None:
        html = '<html><body><div class="job_seen_beacon"><h2 class="jobTitle"><a>Dev</a></h2></div></body></html>'
        assert scraper.detect_captcha(html) is False

    def test_empty_html_is_captcha(self) -> None:
        assert scraper.detect_captcha("") is True


# ── parse_cards ──────────────────────────────────────────────────────────────────


FULL_CARD = """
<div class="job_seen_beacon">
  <h2 class="jobTitle"><a href="/rc/clk?jk=abc123">Senior Python Engineer</a></h2>
  <span data-testid="company-name">Acme Corp</span>
  <div data-testid="text-location">Paris, France</div>
  <time datetime="2026-06-01">il y a 2 jours</time>
  <div class="job-snippet">Build agentic pipelines with Python and LLMs.</div>
</div>
"""

TWO_CARDS = f"""
<html><body>
{FULL_CARD}
<div class="job_seen_beacon">
  <h2 class="jobTitle"><a href="/rc/clk?jk=def456">ML Engineer</a></h2>
  <span data-testid="company-name">Globex</span>
</div>
</body></html>
"""


class TestParseCards(unittest.TestCase):
    def test_full_card(self) -> None:
        offers = scraper.parse_cards(FULL_CARD)
        assert len(offers) == 1
        o = offers[0]
        assert o["title"] == "Senior Python Engineer"
        assert o["url"] == "https://fr.indeed.com/rc/clk?jk=abc123"
        assert o["company"] == "Acme Corp"
        assert o["location"] == "Paris, France"
        assert o["date_posted"] == "2026-06-01"
        assert "agentic" in o["description"]
        assert o["source"] == "indeed"

    def test_multiple_cards(self) -> None:
        offers = scraper.parse_cards(TWO_CARDS)
        assert len(offers) == 2
        assert offers[1]["title"] == "ML Engineer"
        assert offers[1]["location"] == ""  # missing selector → blank

    def test_relative_url_made_absolute(self) -> None:
        offers = scraper.parse_cards(FULL_CARD)
        assert offers[0]["url"].startswith("https://fr.indeed.com/")

    def test_time_text_fallback_when_no_datetime(self) -> None:
        html = """
        <div class="job_seen_beacon">
          <h2 class="jobTitle"><a href="/j/1">X</a></h2>
          <time>3 days ago</time>
        </div>
        """
        # time without datetime attr is not matched by time[datetime] → blank
        assert scraper.parse_cards(html)[0]["date_posted"] == ""

    def test_empty_beacon_skipped(self) -> None:
        assert scraper.parse_cards('<div class="job_seen_beacon"></div>') == []

    def test_no_beacon_returns_empty(self) -> None:
        assert scraper.parse_cards("<html><body><p>nothing</p></body></html>") == []


# ── build_search_url ─────────────────────────────────────────────────────────────


class TestBuildSearchUrl(unittest.TestCase):
    def test_remote_filter_present(self) -> None:
        url = scraper.build_search_url("AI", 0, remote=True)
        assert "q=AI" in url
        assert "sc=0kf%3Aattr(DSQF7)%3B" in url
        assert "start=" not in url  # start=0 omitted

    def test_hybrid_pass_no_remote_filter(self) -> None:
        url = scraper.build_search_url("full stack", 20, remote=False)
        assert "q=full+stack" in url
        assert "sc=" not in url
        assert "start=20" in url


# ── main / run_scan ──────────────────────────────────────────────────────────────


class TestMain(unittest.TestCase):
    def test_scan_happy_path_emits_json_exit_0(self) -> None:
        offers = [
            {**scraper.normalize_offer({"url": "https://x/1", "title": "A"}), "_pass": 1},
            {**scraper.normalize_offer({"url": "https://x/2", "title": "B"}), "_pass": 2},
        ]
        buf = io.StringIO()
        with patch.object(scraper, "_run_with_browser", return_value=offers):
            with redirect_stdout(buf):
                rc = scraper.main([])
        assert rc == 0
        data = json.loads(buf.getvalue())
        assert len(data) == 2
        assert all(d["source"] == "indeed" for d in data)
        assert all("_pass" not in d for d in data)  # internal marker stripped

    def test_scan_captcha_exit_1(self) -> None:
        with patch.object(scraper, "_run_with_browser", side_effect=scraper.CaptchaError("blocked")):
            rc = scraper.main([])
        assert rc == 1

    def test_scan_no_offers_exit_1(self) -> None:
        with patch.object(scraper, "_run_with_browser", return_value=[]):
            rc = scraper.main([])
        assert rc == 1

    def test_scan_fatal_error_exit_1(self) -> None:
        with patch.object(scraper, "_run_with_browser", side_effect=RuntimeError("boom")):
            rc = scraper.main([])
        assert rc == 1

    def test_spike_route_invoked(self) -> None:
        with patch.object(scraper, "run_spike", return_value=0) as spy:
            rc = scraper.main(["--spike", "https://fr.indeed.com/jobs?q=AI"])
        assert rc == 0
        spy.assert_called_once_with("https://fr.indeed.com/jobs?q=AI")


# ── two-pass orchestration (mocked Page) ─────────────────────────────────────────


def _beacon(href: str, title: str = "X") -> str:
    return (
        f'<div class="job_seen_beacon"><h2 class="jobTitle">'
        f'<a href="{href}">{title}</a></h2></div>'
    )


class TestTwoPass(unittest.TestCase):
    """Exercise run_two_pass_scan with a fake fetch_html (no browser, no sleep)."""

    def _fake_fetch(self, _page: object, url: str) -> str:
        if "start=" in url:           # pages > 0 vides → racine épuisée
            return "<html></html>"
        if "sc=" in url:              # passe 1 remote : MÊME url pour toutes les racines
            return _beacon("https://x/remote-dup", "Remote")
        q = url.split("q=")[1].split("&")[0]   # passe 2 : url unique par racine
        return _beacon(f"https://x/hybrid-{q}", f"Hybrid {q}")

    def test_pass2_triggered_and_cross_root_dedup(self) -> None:
        with patch.object(scraper, "_sleep_jitter", lambda: None), \
             patch.object(scraper, "fetch_html", self._fake_fetch):
            offers = scraper.run_two_pass_scan(object())

        urls = [o["url"] for o in offers]
        # remote dup (12 racines même URL) compté une seule fois
        assert urls.count("https://x/remote-dup") == 1
        # passe 2 déclenchée car remote (1) < HYBRID_THRESHOLD (10)
        p1 = [o for o in offers if o["_pass"] == 1]
        p2 = [o for o in offers if o["_pass"] == 2]
        assert len(p1) == 1
        assert len(p2) == len(scraper.SCAN_ROOTS)   # 1 url unique par racine en passe 2
        # zéro doublon global
        assert len(urls) == len(set(urls))

    def test_pass2_skipped_when_remote_above_threshold(self) -> None:
        # Chaque racine remote renvoie une URL unique → ≥ seuil → pas de passe 2.
        def fetch(_page: object, url: str) -> str:
            if "start=" in url:
                return "<html></html>"
            q = url.split("q=")[1].split("&")[0]
            return _beacon(f"https://x/remote-{q}", q)

        with patch.object(scraper, "_sleep_jitter", lambda: None), \
             patch.object(scraper, "fetch_html", fetch):
            offers = scraper.run_two_pass_scan(object())

        assert all(o["_pass"] == 1 for o in offers)
        assert len(offers) == len(scraper.SCAN_ROOTS)

    def test_captcha_propagates(self) -> None:
        def fetch(_page: object, _url: str) -> str:
            raise scraper.CaptchaError("blocked")

        with patch.object(scraper, "_sleep_jitter", lambda: None), \
             patch.object(scraper, "fetch_html", fetch):
            with self.assertRaises(scraper.CaptchaError):
                scraper.run_two_pass_scan(object())


if __name__ == "__main__":
    unittest.main()

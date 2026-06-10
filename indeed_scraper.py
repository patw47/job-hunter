#!/home/thehunter/venv/bin/python3
"""
Scraper Indeed public (Playwright + BeautifulSoup).

Remplace l'ancien accès Indeed MCP (déprécié : pas de mode API-key headless,
seul chemin restant = scraping). Calqué sur la logique de test_indeed.py :
mêmes racines de scan, même seuil/cap, même normalisation 7 champs, même
logique deux passes (remote → fallback élargi), mêmes codes de sortie 0/1.

Recherche publique fr.indeed.com/jobs (aucun login). Le HTML est récupéré via
chromium headless (Playwright, importé paresseusement) puis parsé hors-ligne
par BeautifulSoup — ce qui rend le parsing testable sans navigateur ni réseau.

Sortie : JSON des offres normalisées sur stdout (consommable par n8n Execute
Command). Toute la progression va sur stderr pour garder stdout propre.

Codes de sortie :
    0 = scan OK (JSON émis sur stdout)
    1 = échec (CAPTCHA / Cloudflare détecté, ou aucune offre, ou erreur fatale)

Usage :
    /home/thehunter/venv/bin/python3 indeed_scraper.py
    /home/thehunter/venv/bin/python3 indeed_scraper.py --spike "<url>"   # spike Cloudflare
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

if TYPE_CHECKING:  # évite d'importer playwright au chargement du module (tests offline)
    from playwright.sync_api import Page

# ─── Configuration (alignée sur test_indeed.py) ──────────────────────────────────

SCAN_ROOTS: Final[list[str]] = [
    "AI", "agent", "agentic", "GenAI", "automation",
    "LLM", "RAG", "ML", "full stack", "n8n", "Python", "developer",
]

HYBRID_THRESHOLD: Final[int] = 10   # passe 2 déclenchée si remote < seuil
GLOBAL_CAP: Final[int] = 60         # cap global toutes passes confondues
PER_ROOT_LIMIT: Final[int] = 30     # max résultats par racine par passe

BASE_URL: Final[str] = "https://fr.indeed.com/jobs"
PAGE_STEP: Final[int] = 10          # pagination Indeed : start=0,10,20,...

# Filtre « remote » natif Indeed (sc=0kf:attr(DSQF7);). Indeed n'expose AUCUN
# filtre natif « hybride » → passe 2 = recherche élargie sans filtre remote ;
# le tri hybride/présentiel est dérivé en aval par le Layer 1 (hors-scope ici).
REMOTE_SC: Final[str] = "0kf:attr(DSQF7);"

# Délais anti-bot (secondes) relevés entre navigations, jitter aléatoire.
DELAY_MIN: Final[float] = 1.5
DELAY_MAX: Final[float] = 3.5
NAV_TIMEOUT_MS: Final[int] = 30_000

# User-agent réaliste : un chromium headless par défaut s'annonce « HeadlessChrome »,
# marqueur trivial pour Cloudflare. On force un UA desktop standard.
USER_AGENT: Final[str] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Marqueurs CAPTCHA / Cloudflare (comparaison en minuscules).
CAPTCHA_MARKERS: Final[tuple[str, ...]] = (
    "just a moment...",
    "cf-browser-verification",
    "checking your browser",
    "verifying you are human",
    "hcaptcha.com/1/api.js",
    "h-captcha",
    "_cf_chl_opt",                 # script de challenge Cloudflare
    "additional verification required",
)


# ─── Exceptions ──────────────────────────────────────────────────────────────────


class ScraperError(Exception):
    """Erreur de base du scraper Indeed."""


class CaptchaError(ScraperError):
    """Cloudflare / CAPTCHA détecté — le scraping ne peut pas continuer."""


# ─── Helpers (purs, testables hors-ligne) ────────────────────────────────────────


def _log(msg: str) -> None:
    """Progression sur stderr (stdout réservé au JSON final)."""
    print(msg, file=sys.stderr, flush=True)


def detect_captcha(html: str) -> bool:
    """Vrai si le HTML est une interstitielle Cloudflare / un CAPTCHA Indeed."""
    if not html:
        return True
    haystack = html.lower()
    return any(marker in haystack for marker in CAPTCHA_MARKERS)


def build_search_url(query: str, start: int, remote: bool) -> str:
    """Construit l'URL de recherche publique Indeed (pagination par pas de 10)."""
    params = [f"q={query.replace(' ', '+')}", "l="]
    if start:
        params.append(f"start={start}")
    if remote:
        # Encode `:` et `;` mais garde les parenthèses (forme attendue par Indeed).
        params.append(f"sc={quote(REMOTE_SC, safe='()')}")
    return f"{BASE_URL}?{'&'.join(params)}"


def normalize_offer(raw: dict) -> dict:
    """Normalise une offre brute vers le format canonique 7 champs du projet."""
    def _get(*keys: str, default: str = "") -> str:
        for k in keys:
            v = raw.get(k)
            if v is not None:
                return str(v)
        return default

    return {
        "url": _get("url", "link", "jobUrl", "applyUrl", "detailsPageUrl", "externalUrl"),
        "title": _get("title", "jobTitle", "name", "position"),
        "company": _get("company", "companyName", "employer", "employerName", "hiringOrganization"),
        "location": _get("location", "formattedLocation", "city", "jobLocation"),
        "description": _get("description", "snippet", "summary", "jobDescription", "body")[:500],
        "job_type": _get("jobType", "job_type", "employmentType", "workType", "contractType"),
        "date_posted": _get("datePosted", "date_posted", "postedAt", "formattedRelativeTime", "publishedAt"),
        "source": "indeed",
    }


def _card_to_raw(card: Any) -> dict:
    """Extrait les champs bruts d'une card Indeed (élément BeautifulSoup)."""
    raw: dict[str, str] = {}

    title_a = card.select_one("h2.jobTitle a")
    if title_a is not None:
        raw["title"] = title_a.get_text(strip=True)
        href = title_a.get("href")
        if href:
            raw["url"] = urljoin(BASE_URL, href)

    company = card.select_one("[data-testid='company-name']")
    if company is not None:
        raw["company"] = company.get_text(strip=True)

    location = card.select_one("[data-testid='text-location']")
    if location is not None:
        raw["location"] = location.get_text(strip=True)

    posted = card.select_one("time[datetime]")
    if posted is not None:
        raw["date_posted"] = posted.get("datetime") or posted.get_text(strip=True)

    # description : non listée dans les sélecteurs requis, best-effort sur le snippet.
    snippet = card.select_one("[data-testid='jobsnippet_footer'], .job-snippet")
    if snippet is not None:
        raw["description"] = snippet.get_text(" ", strip=True)

    return raw


def parse_cards(html: str) -> list[dict]:
    """Parse un HTML de page de résultats → liste d'offres normalisées."""
    soup = BeautifulSoup(html, "html.parser")
    offers: list[dict] = []
    for card in soup.select(".job_seen_beacon"):
        raw = _card_to_raw(card)
        if not raw.get("url") and not raw.get("title"):
            continue  # card vide / placeholder
        offers.append(normalize_offer(raw))
    return offers


# ─── Récupération réseau (Playwright, importé paresseusement) ────────────────────


def _sleep_jitter() -> None:
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))


def fetch_html(page: Page, url: str) -> str:
    """Navigue vers `url`, retourne le HTML. Lève CaptchaError si interstitielle."""
    page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    html = page.content()
    if detect_captcha(html):
        raise CaptchaError(f"Cloudflare/CAPTCHA détecté sur {url}")
    return html


def scrape_pass(
    page: Page,
    *,
    remote: bool,
    pass_no: int,
    seen_urls: set[str],
    all_offers: list[dict],
) -> None:
    """Une passe complète sur toutes les racines, dédup par URL, cap global."""
    for root in SCAN_ROOTS:
        remaining = GLOBAL_CAP - len(seen_urls)
        if remaining <= 0:
            _log(f"  → cap {GLOBAL_CAP} atteint, arrêt passe {pass_no}")
            return
        collected_for_root = 0
        start = 0
        while collected_for_root < PER_ROOT_LIMIT and len(seen_urls) < GLOBAL_CAP:
            url = build_search_url(root, start, remote)
            try:
                html = fetch_html(page, url)
            except CaptchaError:
                raise
            except Exception as exc:  # noqa: BLE001 — page suivante malgré l'échec
                _log(f"  [{root}] start={start} ERREUR: {exc}")
                break
            cards = parse_cards(html)
            if not cards:
                break  # plus de résultats pour cette racine
            new_count = 0
            for offer in cards:
                offer_url = offer["url"]
                if offer_url and offer_url not in seen_urls:
                    seen_urls.add(offer_url)
                    all_offers.append({**offer, "_pass": pass_no})
                    new_count += 1
                    collected_for_root += 1
            _log(f"  [{root}] start={start}: {new_count} nouvelles ({len(seen_urls)} total)")
            if new_count == 0:
                break  # page entièrement dupliquée → racine épuisée
            start += PAGE_STEP
            _sleep_jitter()


def run_two_pass_scan(page: Page) -> list[dict]:
    """Passe 1 (remote) → si URLs uniques < seuil, passe 2 (recherche élargie)."""
    seen_urls: set[str] = set()
    all_offers: list[dict] = []

    _log(f"--- Passe 1 : Remote ({len(SCAN_ROOTS)} racines) ---")
    scrape_pass(page, remote=True, pass_no=1, seen_urls=seen_urls, all_offers=all_offers)
    remote_count = len(seen_urls)
    _log(f"Passe 1 terminée : {remote_count} URLs uniques")

    if remote_count < HYBRID_THRESHOLD:
        _log(f"--- Passe 2 : élargie (remote={remote_count} < seuil={HYBRID_THRESHOLD}) ---")
        scrape_pass(page, remote=False, pass_no=2, seen_urls=seen_urls, all_offers=all_offers)
    else:
        _log(f"Passe 2 ignorée : remote={remote_count} ≥ seuil={HYBRID_THRESHOLD}")

    return all_offers[:GLOBAL_CAP]


# ─── Contexte navigateur ─────────────────────────────────────────────────────────


def _run_with_browser(fn: Any) -> Any:
    """Lance chromium headless, exécute `fn(page)`, ferme proprement."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=USER_AGENT, locale="fr-FR")
            page = context.new_page()
            return fn(page)
        finally:
            browser.close()


# ─── Modes CLI ───────────────────────────────────────────────────────────────────


def run_spike(url: str) -> int:
    """Spike Cloudflare : 1 URL, vérifie ≥1 card réelle (pas « Just a moment… »)."""
    def _spike(page: Page) -> int:
        page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        html = page.content()
        if detect_captcha(html):
            _log("✗ SPIKE BLOQUÉ : Cloudflare/CAPTCHA détecté (pas de card réelle).")
            _log("  → mitigation requise (voir indeed-spike.md) avant de câbler le scan.")
            return 1
        cards = parse_cards(html)
        _log(f"✓ SPIKE OK : {len(cards)} card(s) réelle(s) détectée(s).")
        if cards:
            _log(f"  exemple : {json.dumps(cards[0], ensure_ascii=False)[:200]}")
        return 0 if cards else 1

    try:
        return _run_with_browser(_spike)
    except Exception as exc:  # noqa: BLE001
        _log(f"✗ SPIKE ERREUR fatale : {exc}")
        return 1


def run_scan() -> int:
    """Scan complet deux passes, JSON sur stdout, exit 0/1."""
    try:
        offers = _run_with_browser(run_two_pass_scan)
    except CaptchaError as exc:
        _log(f"✗ {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        _log(f"✗ Erreur fatale : {exc}")
        return 1

    if not offers:
        _log("✗ Aucune offre collectée")
        return 1

    clean = [{k: v for k, v in o.items() if not k.startswith("_")} for o in offers]
    print(json.dumps(clean, ensure_ascii=False))
    p1 = sum(1 for o in offers if o.get("_pass") == 1)
    p2 = sum(1 for o in offers if o.get("_pass") == 2)
    _log(f"✓ {len(offers)} offres (remote={p1}, élargie={p2}, cap={GLOBAL_CAP})")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(description="Scraper Indeed public (Playwright).")
    parser.add_argument(
        "--spike",
        metavar="URL",
        help="Mode spike : teste une seule URL Indeed pour valider l'accès Cloudflare.",
    )
    args = parser.parse_args(argv)

    if args.spike:
        return run_spike(args.spike)
    return run_scan()


if __name__ == "__main__":
    sys.exit(main())

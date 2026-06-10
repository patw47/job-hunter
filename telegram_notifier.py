from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_FLAG_MAP: dict[str, str] = {
    "AD": "🇦🇩", "AE": "🇦🇪", "AT": "🇦🇹", "AU": "🇦🇺",
    "BE": "🇧🇪", "BR": "🇧🇷", "CA": "🇨🇦", "CH": "🇨🇭",
    "CZ": "🇨🇿", "DE": "🇩🇪", "DK": "🇩🇰", "ES": "🇪🇸",
    "FI": "🇫🇮", "FR": "🇫🇷", "GB": "🇬🇧", "HU": "🇭🇺",
    "IE": "🇮🇪", "IL": "🇮🇱", "IN": "🇮🇳", "IT": "🇮🇹",
    "JP": "🇯🇵", "LU": "🇱🇺", "MX": "🇲🇽", "NL": "🇳🇱",
    "NO": "🇳🇴", "PL": "🇵🇱", "PT": "🇵🇹", "RO": "🇷🇴",
    "SE": "🇸🇪", "SG": "🇸🇬", "UA": "🇺🇦", "US": "🇺🇸",
    "ZA": "🇿🇦",
}


def _flag_emoji(pays: str) -> str:
    """Return flag emoji for ISO-3166-1 alpha-2 country code, or 🌍 if unknown."""
    return _FLAG_MAP.get(pays.upper().strip(), "🌍") if pays else "🌍"


def _url_hash(url: str) -> str:
    """Return first 16 hex chars of SHA-256 of the normalized URL."""
    parsed = urlparse(url)
    normalized = parsed._replace(query="", fragment="").geturl()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _keywords_line(value: list | set | str | None) -> str:
    """Normalize keywords field (list, set, or comma-string) to display string."""
    if not value:
        return ""
    if isinstance(value, (list, set)):
        return " • ".join(str(k).strip() for k in value if k)
    return str(value).strip()


def _build_card_text(offer: dict) -> str:
    """Build Telegram card message in HTML parse mode."""
    match_rate = offer.get("match_rate", 0)
    if isinstance(match_rate, float) and match_rate <= 1.0:
        match_rate = int(match_rate * 100)
    else:
        match_rate = int(match_rate)

    title = _escape_html(str(offer.get("title", "?")))
    company = _escape_html(str(offer.get("company", "?")))
    pays = str(offer.get("pays", ""))
    remote_type = str(offer.get("remote_type", offer.get("job_type", "Remote"))).capitalize()
    flag = _flag_emoji(pays)

    matched = _keywords_line(offer.get("keywords_matched", offer.get("skills_found")))
    missing = _keywords_line(offer.get("keywords_missing"))

    lines = [
        f"🎯 <b>{match_rate}%</b> — {title} @ {company} {flag}",
        "",
        f"📍 {_escape_html(remote_type)}",
    ]
    if matched:
        lines.append(f"✅ {_escape_html(matched)}")
    if missing:
        lines.append(f"❌ {_escape_html(missing)}")

    return "\n".join(lines)


def _build_keyboard(url_hash: str, url: str) -> dict:
    """Build inline keyboard: 🌐 Ouvrir (url) + 3 callback buttons."""
    return {
        "inline_keyboard": [
            [
                {"text": "🌐 Ouvrir", "url": url},
                {"text": "✅ Générer CV+Lettre", "callback_data": f"generate:{url_hash}"},
            ],
            [
                {"text": "❌ Ignorer", "callback_data": f"ignore:{url_hash}"},
                {"text": "⏰ Plus tard", "callback_data": f"snooze:{url_hash}"},
            ],
        ]
    }


def _telegram_post(token: str, method: str, payload: dict) -> dict:
    """POST a JSON payload to the Telegram Bot API."""
    api_url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        logger.error("Telegram API HTTP %s: %s", exc.code, body[:200])
        raise


def send_match_card(offer: dict, bot_token: str, chat_id: str) -> None:
    """Send an individual match card for an offer with ≥80% match rate.

    Callbacks are emitted with structured callback_data (action:url_hash) but
    are not processed here — callback handling belongs to Sprint 2.
    """
    url = str(offer.get("url", ""))
    h = _url_hash(url)
    text = _build_card_text(offer)
    keyboard = _build_keyboard(h, url)

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": keyboard,
        "disable_web_page_preview": True,
    }

    result = _telegram_post(bot_token, "sendMessage", payload)

    if result.get("ok"):
        logger.info("Telegram card sent: %s (hash=%s)", offer.get("title", "?"), h)
    else:
        description = result.get("description", str(result))
        logger.error("Telegram sendMessage failed: %s", description)
        raise RuntimeError(f"Telegram API error: {description}")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print("Usage: TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python telegram_notifier.py")
        sys.exit(1)

    test_offer: dict = {
        "url": "https://example.com/jobs/ai-engineer-acme-12345",
        "title": "AI Engineer",
        "company": "Acme Corp",
        "pays": "FR",
        "remote_type": "Remote",
        "match_rate": 85,
        "keywords_matched": ["Python", "FastAPI", "LangChain", "RAG"],
        "keywords_missing": ["Kubernetes", "Docker"],
    }

    print(f"Sending test card (85%) to chat_id={chat_id} ...")
    send_match_card(test_offer, token, chat_id)
    print("Done — check your Telegram.")

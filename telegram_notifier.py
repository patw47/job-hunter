from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

import matches_store
import voice_profile_manager

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

_JOB_ID_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{64}$")


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


def _remote_label(offer: dict) -> str:
    """Derive Remote/On-site label from offer fields."""
    raw = str(offer.get("remote", offer.get("job_type", ""))).strip().lower()
    return "Remote" if raw in ("true", "yes", "remote", "1") else "On-site"


def _is_indeed_complete(offer: dict) -> bool:
    """Return True if all required Indeed card fields are non-empty."""
    required = ("title", "company", "location", "match_rate", "skills_found", "url")
    return all(str(offer.get(f, "")).strip() for f in required)


def _build_indeed_card_text(offer: dict) -> str:
    """Build Telegram Markdown card for a complete Indeed offer (no LLM call)."""
    rate = offer.get("match_rate", 0)
    if isinstance(rate, float) and rate <= 1.0:
        rate_pct = int(rate * 100)
    else:
        rate_pct = int(float(str(rate)))

    title = str(offer.get("title", "?"))
    company = str(offer.get("company", "?"))
    location = str(offer.get("location", "?"))
    remote = _remote_label(offer)
    url = str(offer.get("url", ""))
    skills = str(offer.get("skills_found", ""))

    return (
        f"🎯 *{title}* — {company}\n"
        f"📍 {location} | {remote}\n"
        f"🔗 {url}\n"
        f"\n"
        f"*Match* : {rate_pct}% | Skills : {skills}\n"
        f"\n"
        f"⚡ Source : Indeed"
    )


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


def send_digest(offers: list[dict], bot_token: str, chat_id: str) -> None:
    """Send daily digest for 60-79% match offers, sorted by match rate descending.

    Skips silently if no offers fall in the 60-79% range.
    """

    def _rate(offer: dict) -> int:
        r = offer.get("match_rate", 0)
        if isinstance(r, float) and r <= 1.0:
            return int(r * 100)
        return int(r)

    filtered = sorted(
        [o for o in offers if 60 <= _rate(o) < 80],
        key=_rate,
        reverse=True,
    )

    if not filtered:
        logger.info("Digest skipped — no 60-79%% offers today")
        return

    n = len(filtered)
    text_lines = [f"🎯 THE HUNTER — {n} offre{'s' if n > 1 else ''} aujourd'hui", ""]
    keyboard_rows: list[list[dict]] = []

    for i, offer in enumerate(filtered, 1):
        rate = _rate(offer)
        title_raw = str(offer.get("title", "?"))
        company_raw = str(offer.get("company", "?"))
        remote = str(offer.get("remote_type", offer.get("job_type", "Remote"))).capitalize()
        flag = _flag_emoji(str(offer.get("pays", "")))
        url = str(offer.get("url", ""))
        h = _url_hash(url)

        text_lines.append(
            f"{i}. {rate}% — {_escape_html(title_raw)} @ {_escape_html(company_raw)} {flag} {_escape_html(remote)}"
        )
        keyboard_rows.append([
            {"text": f"{i}. {rate}% — {title_raw} @ {company_raw} {flag}", "callback_data": f"detail:{h}"}
        ])

    payload = {
        "chat_id": chat_id,
        "text": "\n".join(text_lines),
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": keyboard_rows},
        "disable_web_page_preview": True,
    }

    result = _telegram_post(bot_token, "sendMessage", payload)
    if result.get("ok"):
        logger.info("Telegram digest sent: %d offer(s) (60-79%%)", n)
    else:
        description = result.get("description", str(result))
        logger.error("Telegram digest failed: %s", description)
        raise RuntimeError(f"Telegram API error: {description}")


def send_match_card(offer: dict, bot_token: str, chat_id: str) -> None:
    """Send an individual match card for an offer with ≥80% match rate."""
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


def _send_message_resilient(bot_token: str, payload: dict) -> dict:
    """sendMessage with plain-text fallback when Telegram rejects the markup.

    LLM-generated card text can contain unbalanced Markdown entities; a 400
    'can't parse entities' must degrade to plain text, not fail the pipeline.
    """
    result = _telegram_post(bot_token, "sendMessage", payload)
    if not result.get("ok") and "parse entities" in str(result.get("description", "")):
        logger.warning("Telegram rejected markup — resending as plain text")
        plain = {k: v for k, v in payload.items() if k != "parse_mode"}
        result = _telegram_post(bot_token, "sendMessage", plain)
    return result


def send_indeed_card(offer: dict, bot_token: str, chat_id: str) -> None:
    """Send Telegram card for a complete Indeed offer (no LLM call)."""
    url = str(offer.get("url", ""))
    h = _url_hash(url)
    text = _build_indeed_card_text(offer)
    keyboard = _build_keyboard(h, url)

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": keyboard,
        "disable_web_page_preview": True,
    }

    result = _send_message_resilient(bot_token, payload)
    if result.get("ok"):
        logger.info("Telegram Indeed card sent: %s (hash=%s)", offer.get("title", "?"), h)
    else:
        description = result.get("description", str(result))
        logger.error("Telegram sendMessage failed: %s", description)
        raise RuntimeError(f"Telegram API error: {description}")


def send_card_from_text(text: str, offer: dict, bot_token: str, chat_id: str) -> None:
    """Send Telegram card using pre-built Markdown text (from /analyze Haiku)."""
    url = str(offer.get("url", ""))
    h = _url_hash(url)
    keyboard = _build_keyboard(h, url)

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": keyboard,
        "disable_web_page_preview": True,
    }

    result = _send_message_resilient(bot_token, payload)
    if result.get("ok"):
        logger.info("Telegram card (from text) sent: %s (hash=%s)", offer.get("title", "?"), h)
    else:
        description = result.get("description", str(result))
        logger.error("Telegram sendMessage failed: %s", description)
        raise RuntimeError(f"Telegram API error: {description}")


def answer_callback_query(token: str, callback_query_id: str, text: str = "") -> dict:
    """Respond to a Telegram callback_query to dismiss the loading spinner."""
    result = _telegram_post(
        token,
        "answerCallbackQuery",
        {"callback_query_id": callback_query_id, "text": text},
    )
    if not result.get("ok"):
        description = result.get("description", str(result))
        logger.error("answerCallbackQuery failed: %s", description)
        raise RuntimeError(f"Telegram API error: {description}")
    return result


def edit_message_text(
    token: str,
    chat_id: str,
    message_id: int,
    text: str,
    reply_markup: dict | None = None,
) -> dict:
    """Edit an existing message text (and optionally its reply markup)."""
    payload: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    result = _telegram_post(token, "editMessageText", payload)
    if not result.get("ok"):
        description = result.get("description", str(result))
        logger.error("editMessageText failed: %s", description)
        raise RuntimeError(f"Telegram API error: {description}")
    return result


def send_snooze_renotifications(
    bot_token: str, chat_id: str, creds_path: str | None = None
) -> int:
    """Re-send snoozed offers as individual cards.

    Returns number of cards sent.
    """
    from matches_sheet import get_snoozed_offers, open_matches_sheet

    sheet = open_matches_sheet(creds_path)
    snoozed = get_snoozed_offers(sheet)
    sent = 0
    for offer in snoozed:
        try:
            send_match_card(offer, bot_token, chat_id)
            sent += 1
        except Exception as exc:
            logger.warning(
                "Snooze re-notification failed for %r: %s", offer.get("title"), exc
            )
    logger.info("Snooze re-notifications: %d sent", sent)
    return sent


# --- Command dispatcher (Epic 6) ---

def _result(ok: bool, message: str) -> None:
    print(json.dumps({"ok": ok, "message": message}, ensure_ascii=False))


def _valid_job_id(job_id: str) -> bool:
    return bool(_JOB_ID_RE.match(job_id))


def main(argv: list[str]) -> int:
    """Dispatch command from argv, print JSON result. Always returns 0."""
    if not argv:
        _result(False, "❌ Usage : telegram_notifier.py <commande> <args…>")
        return 0

    command = argv[0]

    if command == "status":
        if len(argv) != 2:
            _result(False, "❌ Usage : status <job_id>")
            return 0
        job_id = argv[1]
        if not _valid_job_id(job_id):
            _result(False, f"❌ ID d'offre invalide : {job_id[:20]}…")
            return 0
        try:
            sheet = matches_store.open_matches()
            status = matches_store.get_status(sheet, job_id)
            _result(True, f"📋 Statut actuel : {status or '(non défini)'}")
        except KeyError:
            _result(False, f"❌ Offre introuvable (ID : {job_id[:12]}…)")

    elif command == "update":
        if len(argv) != 3:
            _result(False, "❌ Usage : update <job_id> <statut>")
            return 0
        job_id, statut = argv[1], argv[2]
        if not _valid_job_id(job_id):
            _result(False, f"❌ ID d'offre invalide : {job_id[:20]}…")
            return 0
        try:
            sheet = matches_store.open_matches()
            matches_store.set_status(sheet, job_id, statut)
            _result(True, f"✅ Statut mis à jour → {statut}")
        except ValueError:
            valid = " | ".join(sorted(matches_store.VALID_STATUSES))
            _result(False, f"❌ Statut invalide : {statut!r}\nValeurs acceptées : {valid}")
        except KeyError:
            _result(False, f"❌ Offre introuvable (ID : {job_id[:12]}…)")

    elif command == "note":
        if len(argv) < 3:
            _result(False, "❌ Usage : note <job_id> <texte>")
            return 0
        job_id = argv[1]
        texte = " ".join(argv[2:])
        if not _valid_job_id(job_id):
            _result(False, f"❌ ID d'offre invalide : {job_id[:20]}…")
            return 0
        try:
            sheet = matches_store.open_matches()
            matches_store.set_note(sheet, job_id, texte)
            _result(True, "📝 Note enregistrée.")
        except KeyError:
            _result(False, f"❌ Offre introuvable (ID : {job_id[:12]}…)")

    elif command == "mark_sent":
        if len(argv) != 2:
            _result(False, "❌ Usage : mark_sent <job_id>")
            return 0
        job_id = argv[1]
        if not _valid_job_id(job_id):
            _result(False, f"❌ ID d'offre invalide : {job_id[:20]}…")
            return 0
        try:
            sheet = matches_store.open_matches()
            matches_store.set_status(sheet, job_id, "Envoyé")
            _result(True, "✅ Candidature marquée Envoyé.")
        except KeyError:
            _result(False, f"❌ Offre introuvable (ID : {job_id[:12]}…)")

    elif command == "feedback":
        if len(argv) < 2:
            _result(False, "❌ Usage : feedback <add|remove|list> [texte]")
            return 0
        subcmd = argv[1]
        soul_path = voice_profile_manager.get_default_soul_path()

        if subcmd == "add":
            if len(argv) < 3:
                _result(False, "❌ Usage : feedback add <texte>")
                return 0
            texte = " ".join(argv[2:])
            today = datetime.date.today().isoformat()
            voice_profile_manager.add_feedback(soul_path, texte, today)
            _result(True, f'✅ Feedback ajouté : "{texte}"')

        elif subcmd == "remove":
            if len(argv) < 3:
                _result(False, "❌ Usage : feedback remove <texte>")
                return 0
            texte = " ".join(argv[2:])
            voice_profile_manager.remove_feedback(soul_path, texte)
            _result(True, f'✅ Feedback retiré : "{texte}"')

        elif subcmd == "list":
            contenu = voice_profile_manager.list_feedback(soul_path)
            if contenu:
                _result(True, f"📝 Feedback log :\n{contenu}")
            else:
                _result(True, "📝 Feedback log vide.")

        else:
            _result(
                False,
                f"❌ Sous-commande inconnue : {subcmd!r}\nUsage : feedback <add|remove|list>",
            )

    else:
        _result(False, f"❌ Commande inconnue : {command!r}\nCommandes : status | update | note | mark_sent | feedback")

    return 0


if __name__ == "__main__":
    _DISPATCH_COMMANDS = {"status", "update", "note", "mark_sent", "feedback"}
    if len(sys.argv) > 1 and sys.argv[1] in _DISPATCH_COMMANDS:
        sys.exit(main(sys.argv[1:]))

    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Telegram notifier CLI")
    parser.add_argument("--digest", action="store_true", help="Send daily digest (60-79%% offers)")
    parser.add_argument(
        "--offers-file",
        default=os.environ.get("SCORED_OFFERS_FILE", "/opt/apps/job-hunter/workspace/today_scored.json"),
        help="Path to scored offers JSON (for --digest mode)",
    )
    cli_args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print("Usage: TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python telegram_notifier.py [--digest]")
        sys.exit(1)

    if cli_args.digest:
        try:
            with open(cli_args.offers_file) as f:
                offers_data = json.load(f)
        except FileNotFoundError:
            logger.warning("Scored offers file not found: %s — digest skipped", cli_args.offers_file)
            sys.exit(0)
        send_digest(offers_data, token, chat_id)
        print("Done — digest sent (or skipped if no 60-79%% offers).")
    else:
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

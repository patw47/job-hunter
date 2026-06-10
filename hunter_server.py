#!/usr/bin/env python3
"""
The Hunter HTTP bridge for n8n.

Pont entre n8n et l'agent OpenClaw `the-hunter` via la CLI `openclaw agent`.

Routes:
  GET  /health         -- healthcheck
  POST /analyze        -- offer-analysis skill (Haiku)
  POST /rewrite-cv     -- cv-rewriter skill (Sonnet)
  POST /cover-letter   -- cover-letter-writer skill (Sonnet)
  POST /form-answers   -- form-answerer skill (Haiku)
  POST /report         -- weekly-reporter skill (Haiku)

Tourne en tant qu'utilisateur `thehunter`.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import logging
import subprocess, json, os, time
from datetime import datetime

logger = logging.getLogger(__name__)

PORT = int(os.environ.get("HUNTER_BRIDGE_PORT", "18798"))
OPENCLAW_CONFIG = os.environ.get(
    "OPENCLAW_CONFIG_PATH", "/home/thehunter/.openclaw/openclaw.json"
)
AGENT_ID = "the-hunter"

SKILL_MAP = {
    "/analyze":       "offer-analysis",
    "/rewrite-cv":    "cv-rewriter",
    "/cover-letter":  "cover-letter-writer",
    "/form-answers":  "form-answerer",
    "/report":        "weekly-reporter",
}


def call_hunter(message, tag, timeout=600):
    session_id = f"n8n-{tag}-{int(time.time())}"
    env = os.environ.copy()
    env["OPENCLAW_CONFIG_PATH"] = OPENCLAW_CONFIG
    env.setdefault("HOME", "/home/thehunter")
    r = subprocess.run(
        ["openclaw", "agent", "--agent", AGENT_ID,
         "--session-id", session_id, "--message", message,
         "--json", "--timeout", str(timeout)],
        capture_output=True, text=True, timeout=timeout + 20, env=env,
    )
    return r.stdout.strip()


def extract_inner(stdout):
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


def _today_ddmmyyyy() -> str:
    return datetime.now().strftime("%d.%m.%Y")


def _handle_prioritize(body: dict) -> dict:
    """Run prioritizer: merge PENDING_MATCHES + new_matches, notify top 25, park overflow."""
    from prioritizer import open_sheets, run_prioritizer

    new_matches: list[dict] = body.get("new_matches", [])
    pending_sheet, matches_sheet = open_sheets()
    result = run_prioritizer(pending_sheet, matches_sheet, new_matches)
    return {"ok": True, **result}


def _handle_dedup(body: dict) -> dict:
    """Deduplicate offers against SCANNED_HASHES and write all new hashes in batch."""
    from deduplication import compute_hash, log_hashes, open_scanned_hashes

    offers: list[dict] = body.get("offers", [])
    scan_date: str = body.get("scan_date") or _today_ddmmyyyy()

    if not offers:
        return {"ok": True, "new_offers": [], "total_scanned": 0, "new_count": 0, "duplicate_count": 0}

    sheet = open_scanned_hashes()
    # Load existing hashes once — avoid one API call per offer
    existing_hashes: set[str] = set(sheet.col_values(1))

    new_offers: list[dict] = []
    new_hashes: list[dict] = []

    for offer in offers:
        url: str = offer.get("url", "")
        url_hash = compute_hash(url)
        if url_hash in existing_hashes:
            continue
        # Track in-session to avoid writing the same URL twice in one scan
        existing_hashes.add(url_hash)
        new_offers.append(offer)
        new_hashes.append({
            "url_hash": url_hash,
            "title": offer.get("title", ""),
            "company": offer.get("company", ""),
            "url": url,
            "source": offer.get("source", ""),
            "scan_date": scan_date,
        })

    log_hashes(new_hashes, sheet)

    return {
        "ok": True,
        "new_offers": new_offers,
        "total_scanned": len(offers),
        "new_count": len(new_offers),
        "duplicate_count": len(offers) - len(new_offers),
    }


def _handle_callback(body: dict) -> dict:
    """Route a Telegram callback_query by action prefix and update MATCHES."""
    from telegram_notifier import (
        answer_callback_query,
        edit_message_text,
        send_match_card,
    )
    from matches_sheet import (
        COL_COMPANY,
        COL_LOCATION,
        COL_MATCH_RATE,
        COL_REMOTE,
        COL_SKILLS_FOUND,
        COL_TITLE,
        COL_URL,
        STATUS_IGNORED,
        STATUS_SENT,
        find_row_by_url_hash,
        increment_snooze,
        open_matches_sheet,
        set_status,
    )

    callback_query_id: str = body.get("callback_query_id", "")
    callback_data: str = body.get("callback_data", "")
    chat_id: str = str(body.get("chat_id", ""))
    message_id: int | None = body.get("message_id")
    bot_token: str = os.environ.get("TELEGRAM_HUNTER_BOT_TOKEN", "")

    if not bot_token:
        return {"ok": False, "error": "TELEGRAM_HUNTER_BOT_TOKEN not set"}

    if ":" not in callback_data:
        return {"ok": False, "error": f"invalid callback_data: {callback_data!r}"}

    action, url_hash = callback_data.split(":", 1)

    answer_text = ""
    edit_text: str | None = None
    result: dict = {"ok": True, "action": action, "url_hash": url_hash}

    try:
        sheet = open_matches_sheet()
        _, row = find_row_by_url_hash(url_hash, sheet)

        def _cell(col: int) -> str:
            return row[col - 1] if row and len(row) >= col else "?"

        company = _cell(COL_COMPANY)

        if action == "ignore":
            set_status(url_hash, STATUS_IGNORED, sheet)
            answer_text = "❌ Ignoré"
            edit_text = f"❌ Ignoré : {company}"

        elif action == "snooze":
            auto_ignored, count = increment_snooze(url_hash, sheet)
            if auto_ignored:
                answer_text = "❌ 2 snoozes — offre ignorée"
                edit_text = f"❌ Ignoré (2 snoozes) : {company}"
            else:
                answer_text = f"⏰ Snoozé ({count}/2)"
                edit_text = f"⏰ Snoozé ({count}/2) : {company}"

        elif action == "generate":
            logger.info("GENERATE signal for hash=%s (Epic 5 queue)", url_hash)
            answer_text = "🚀 Génération lancée"
            edit_text = f"🚀 Génération CV+Lettre lancée : {company}"

        elif action == "apply":
            logger.info("APPLY signal for hash=%s (Epic 6 queue)", url_hash)
            answer_text = "📝 Easy Apply lancé"
            edit_text = f"📝 Easy Apply lancé : {company}"

        elif action == "skip":
            answer_text = "⏭️ Postule manuellement"
            edit_text = f"⏭️ Postule manuellement : {company}"

        elif action == "sent":
            set_status(url_hash, STATUS_SENT, sheet)
            answer_text = "📬 Marqué envoyé"
            edit_text = f"📬 Envoyé : {company}"

        elif action == "detail":
            if row:
                offer = {
                    "url": _cell(COL_URL),
                    "title": _cell(COL_TITLE),
                    "company": _cell(COL_COMPANY),
                    "pays": _cell(COL_LOCATION),
                    "remote_type": _cell(COL_REMOTE),
                    "match_rate": _cell(COL_MATCH_RATE),
                    "keywords_matched": _cell(COL_SKILLS_FOUND),
                    "keywords_missing": [],
                }
                send_match_card(offer, bot_token, chat_id)

        else:
            logger.warning("Unknown callback action=%r hash=%s", action, url_hash)
            answer_text = "?"

    except Exception as exc:
        logger.error("Callback routing failed: %s", exc)
        result = {"ok": False, "error": str(exc)}

    finally:
        if callback_query_id:
            try:
                answer_callback_query(bot_token, callback_query_id, answer_text)
            except Exception as exc:
                logger.error("answerCallbackQuery failed: %s", exc)

    if edit_text and message_id:
        try:
            edit_message_text(bot_token, chat_id, message_id, edit_text)
        except Exception as exc:
            logger.warning("editMessageText failed: %s", exc)

    return result


def _handle_snooze_renotify() -> dict:
    """Re-send snoozed MATCHES offers as individual Telegram cards."""
    from telegram_notifier import send_snooze_renotifications

    bot_token = os.environ.get("TELEGRAM_HUNTER_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_HUNTER_CHAT_ID", "")

    if not bot_token or not chat_id:
        return {
            "ok": False,
            "error": "TELEGRAM_HUNTER_BOT_TOKEN or TELEGRAM_HUNTER_CHAT_ID not set",
        }

    sent = send_snooze_renotifications(bot_token, chat_id)
    return {"ok": True, "sent": sent}


def build_message(skill, brief):
    return (
        f"[{skill.upper()} SKILL]\n"
        "Brief reçu depuis n8n :\n"
        + json.dumps(brief, ensure_ascii=False, indent=2)
        + f"\n\nApplique la skill {skill} selon SKILL.md."
    )


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok", "service": "hunter-bridge", "agent": AGENT_ID, "port": PORT})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/callback":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            try:
                self._send(200, _handle_callback(body))
            except Exception as e:
                self._send(200, {"ok": False, "error": str(e)})
            return

        if self.path == "/snooze-renotify":
            try:
                self._send(200, _handle_snooze_renotify())
            except Exception as e:
                self._send(200, {"ok": False, "error": str(e)})
            return

        if self.path == "/dedup":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            try:
                self._send(200, _handle_dedup(body))
            except Exception as e:
                self._send(200, {"ok": False, "error": str(e)})
            return

        skill = SKILL_MAP.get(self.path)
        if not skill:
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {}
        try:
            tag = self.path.lstrip("/")
            message = build_message(skill, body)
            stdout = call_hunter(message, tag)
            result = extract_inner(stdout)
            self._send(200, {"ok": True, "skill": skill, "result": result})
        except Exception as e:
            self._send(200, {"ok": False, "skill": skill, "error": str(e)})

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    print(f"Hunter HTTP server listening on 127.0.0.1:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

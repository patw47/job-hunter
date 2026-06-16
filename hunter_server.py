#!/usr/bin/env python3
"""
The Hunter HTTP bridge for n8n.

Pont entre n8n et l'agent OpenClaw `the-hunter` via la CLI `openclaw agent`.

Routes:
  GET  /health                       -- healthcheck
  POST /analyze                      -- offer-analysis skill (Haiku)
  POST /rewrite-cv                   -- cv-rewriter skill (Sonnet)
  POST /cover-letter                 -- cover-letter-writer skill (Sonnet)
  POST /form-answers                 -- form-answerer skill (Haiku)
  POST /report                       -- weekly-reporter skill (Haiku)
  POST /dedup                        -- dedup offers against SCANNED_HASHES
  POST /layer1                       -- layer 1 disqualifying filters
  POST /layer2                       -- layer 2 skills scoring
  POST /sheets/write-scan-results    -- write MATCHES + PENDING_MATCHES

Tourne en tant qu'utilisateur `thehunter`.
"""
from __future__ import annotations

import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
import logging
import subprocess, json, os, time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

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


def call_hunter(message, tag, timeout=600, agent=None):
    session_id = f"n8n-{tag}-{int(time.time())}"
    env = os.environ.copy()
    env["OPENCLAW_CONFIG_PATH"] = OPENCLAW_CONFIG
    env.setdefault("HOME", "/home/thehunter")
    cmd = ["openclaw", "agent", "--agent", agent or AGENT_ID,
           "--session-id", session_id, "--message", message,
           "--json", "--timeout", str(timeout)]
    for attempt in range(2):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 20, env=env)
            return r.stdout.strip()
        except subprocess.TimeoutExpired:
            if attempt == 0:
                logger.warning("call_hunter timeout (attempt 1), retrying once...")
                continue
            raise


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
    from deduplication import compute_hash, compute_stable_hash, log_hashes, open_scanned_hashes

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
        url_hash = compute_stable_hash(offer.get("title", ""), offer.get("company", ""))
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
        "scan_date": scan_date,
    }


# Distinct profile skills found in an offer for a 100% match rate. Dividing by
# the full SKILLS_MASTER size (~100 keywords) made 60% unreachable on real
# offers; 6+ skills found in title+description now qualifies (threshold 0.6).
_MATCH_RATE_TARGET: int = 10


def _term_in_text(term: str, text: str) -> bool:
    """Word-boundary match — substring matching credits 'R' or 'Make' on any text."""
    import re
    return re.search(r"(?<!\w)" + re.escape(term.lower()) + r"(?!\w)", text) is not None


def _score_offer(offer: dict, keywords: set[str], aliases: dict[str, list[str]]) -> tuple[float, list[str]]:
    """Score an offer against SKILLS_MASTER keywords. Returns (match_rate, skills_found).

    match_rate = distinct profile skills found / _MATCH_RATE_TARGET, capped at 1.0.
    The alias table maps offer-side terms to profile skills ("Vector DB" → Qdrant…),
    so it is inverted here: an offer mentioning the term credits each mapped skill.
    """
    if not keywords:
        return 0.0, []
    text = " ".join([offer.get("title") or "", offer.get("description") or ""]).lower()

    offer_terms_by_skill: dict[str, list[str]] = {}
    for term, skills in aliases.items():
        for skill in skills:
            offer_terms_by_skill.setdefault(skill, []).append(term)

    matched: list[str] = []
    for kw in keywords:
        if _term_in_text(kw, text):
            matched.append(kw)
            continue
        for term in offer_terms_by_skill.get(kw, []):
            if _term_in_text(term, text):
                matched.append(kw)
                break
    rate = min(len(matched) / _MATCH_RATE_TARGET, 1.0)
    return round(rate, 4), sorted(matched)


def _handle_layer1(body: dict) -> dict:
    """Apply layer 1 disqualifying filters to a list of offers."""
    from layer1_filter import apply_layer1

    offers: list[dict] = body.get("offers", [])
    scan_date: str = body.get("scan_date", "")
    if not offers:
        return {"ok": True, "passed": [], "rejected": [], "passed_count": 0, "rejected_count": 0, "scan_date": scan_date}

    passed: list[dict] = []
    rejected: list[dict] = []
    for offer in offers:
        ok, reason = apply_layer1(offer)
        (passed if ok else rejected).append({**offer, "layer1_reason": reason})

    return {
        "ok": True,
        "passed": passed,
        "rejected": rejected,
        "passed_count": len(passed),
        "rejected_count": len(rejected),
        "scan_date": scan_date,
    }


def _handle_layer2(body: dict) -> dict:
    """Score offers against SKILLS_MASTER.md keywords. Returns scored list + high_match (≥60%)."""
    from skills_loader import load_keywords, load_aliases, DEFAULT_SKILLS_MASTER_PATH

    offers: list[dict] = body.get("offers", [])
    scan_date: str = body.get("scan_date", "")
    threshold: float = float(body.get("threshold", 0.6))

    if not offers:
        return {
            "ok": True, "scored": [], "high_match": [], "high_match_count": 0,
            "total_scored": 0, "match_threshold": threshold, "scan_date": scan_date,
        }

    try:
        keywords = load_keywords(DEFAULT_SKILLS_MASTER_PATH)
        aliases = load_aliases(DEFAULT_SKILLS_MASTER_PATH)
    except FileNotFoundError:
        logger.warning("SKILLS_MASTER.md not found at %s — scoring with empty keywords", DEFAULT_SKILLS_MASTER_PATH)
        keywords, aliases = set(), {}

    scored: list[dict] = []
    for offer in offers:
        match_rate, skills_found = _score_offer(offer, keywords, aliases)
        scored.append({**offer, "match_rate": match_rate, "skills_found": skills_found})

    scored.sort(key=lambda x: x["match_rate"], reverse=True)
    high_match = [o for o in scored if o["match_rate"] >= threshold]

    return {
        "ok": True,
        "scored": scored,
        "high_match": high_match,
        "high_match_count": len(high_match),
        "total_scored": len(scored),
        "match_threshold": threshold,
        "scan_date": scan_date,
    }


def _sheets_append_with_retry(sheet, rows: list[list], max_retries: int = 3) -> None:
    """Append rows to a worksheet, retrying on 429 rate-limit errors with exponential backoff."""
    import gspread

    for attempt in range(max_retries + 1):
        try:
            sheet.append_rows(rows, value_input_option="RAW")
            return
        except gspread.exceptions.APIError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", 0)
            if status != 429 or attempt >= max_retries:
                raise
            delay = 2 ** (attempt + 1)
            logger.warning("Sheets 429 rate limit, retry %d/%d in %ds", attempt + 1, max_retries, delay)
            time.sleep(delay)


def _handle_write_scan_results(body: dict) -> dict:
    """Write high-match offers (from /layer2 high_match) to MATCHES and PENDING_MATCHES sheets."""
    import gspread
    from deduplication import compute_hash, CREDS_PATH, SPREADSHEET_NAME

    offers: list[dict] = body.get("offers", [])
    scan_date: str = body.get("scan_date") or _today_ddmmyyyy()

    if not offers:
        return {"ok": True, "written_count": 0, "scan_date": scan_date}

    gc = gspread.service_account(filename=CREDS_PATH)
    ss = gc.open(SPREADSHEET_NAME)
    matches_sheet = ss.worksheet("MATCHES")
    pending_sheet = ss.worksheet("PENDING_MATCHES")

    matches_rows: list[list] = []
    pending_rows: list[list] = []

    for rank, offer in enumerate(offers, start=1):
        job_id = compute_stable_hash(offer.get("title", ""), offer.get("company", ""))
        skills_str = ", ".join(offer.get("skills_found") or [])
        match_pct = round((offer.get("match_rate") or 0.0) * 100, 1)
        # MATCHES columns: job_id, date_scanned, title, company, location, remote, url, source, match_rate, skills_found, status, cv_drive_link, letter_drive_link, applied_at
        matches_rows.append([
            job_id, scan_date,
            offer.get("title", ""), offer.get("company", ""), offer.get("location", ""),
            offer.get("job_type", ""), offer.get("url", ""), offer.get("source", ""),
            match_pct, skills_str, "pending", "", "", "",
        ])
        # PENDING_MATCHES columns: job_id, date_scanned, title, company, location, url, match_rate, skills_found, source, rank
        pending_rows.append([
            job_id, scan_date,
            offer.get("title", ""), offer.get("company", ""), offer.get("location", ""),
            offer.get("url", ""), match_pct, skills_str, offer.get("source", ""), rank,
        ])

    _sheets_append_with_retry(matches_sheet, matches_rows)
    _sheets_append_with_retry(pending_sheet, pending_rows)

    return {"ok": True, "written_count": len(offers), "scan_date": scan_date}


def _handle_read_pending_matches() -> dict:
    """Read PENDING_MATCHES, filtering out offers snoozed until a future date."""
    import gspread
    from deduplication import CREDS_PATH, SPREADSHEET_NAME

    gc = gspread.service_account(filename=CREDS_PATH)
    ss = gc.open(SPREADSHEET_NAME)
    sheet = ss.worksheet("PENDING_MATCHES")
    all_rows = sheet.get_all_values()

    today_str = _today_ddmmyyyy()
    today_dt = datetime.strptime(today_str, "%d.%m.%Y")

    offers: list[dict] = []
    snoozed_count = 0

    for row in all_rows:
        if not row or not row[0] or row[0] == "job_id":
            continue
        snooze_until_str: str = row[10] if len(row) > 10 else ""
        if snooze_until_str:
            try:
                snooze_dt = datetime.strptime(snooze_until_str, "%d.%m.%Y")
                if snooze_dt > today_dt:
                    snoozed_count += 1
                    continue
            except ValueError:
                pass
        offers.append({
            "job_id": row[0],
            "date_scanned": row[1] if len(row) > 1 else "",
            "title": row[2] if len(row) > 2 else "",
            "company": row[3] if len(row) > 3 else "",
            "location": row[4] if len(row) > 4 else "",
            "url": row[5] if len(row) > 5 else "",
            "match_rate": row[6] if len(row) > 6 else "",
            "skills_found": row[7] if len(row) > 7 else "",
            "source": row[8] if len(row) > 8 else "",
            "rank": row[9] if len(row) > 9 else "",
        })

    return {"ok": True, "offers": offers, "count": len(offers), "snoozed_count": snoozed_count}


def _handle_update_status(body: dict) -> dict:
    """Update the status column in MATCHES for a given job_id."""
    import gspread
    from deduplication import CREDS_PATH, SPREADSHEET_NAME

    job_id: str = body.get("job_id", "")
    status: str = body.get("status", "")
    if not job_id or not status:
        return {"ok": False, "error": "job_id and status required", "updated": False}

    gc = gspread.service_account(filename=CREDS_PATH)
    ss = gc.open(SPREADSHEET_NAME)
    sheet = ss.worksheet("MATCHES")
    cell = sheet.find(job_id, in_column=1)
    if cell is None:
        return {"ok": True, "updated": False}
    sheet.update_cell(cell.row, 11, status)  # col 11 = status (1-indexed)
    return {"ok": True, "updated": True}


def _handle_snooze(body: dict) -> dict:
    """Mark an offer in PENDING_MATCHES as snoozed until the given date (default: tomorrow)."""
    import gspread
    from deduplication import CREDS_PATH, SPREADSHEET_NAME

    job_id: str = body.get("job_id", "")
    if not job_id:
        return {"ok": False, "error": "job_id required"}

    snooze_until: str = body.get("snooze_until") or (
        (datetime.now() + timedelta(days=1)).strftime("%d.%m.%Y")
    )

    gc = gspread.service_account(filename=CREDS_PATH)
    ss = gc.open(SPREADSHEET_NAME)
    sheet = ss.worksheet("PENDING_MATCHES")
    cell = sheet.find(job_id, in_column=1)
    if cell is None:
        return {"ok": False, "error": f"job_id {job_id} not found in PENDING_MATCHES"}
    sheet.update_cell(cell.row, 11, snooze_until)  # col 11 = snooze_until (new column, 1-indexed)
    return {"ok": True, "snoozed_until": snooze_until}


def _handle_generate(body: dict) -> dict:
    """Generate CV and cover letter for a job offer found in MATCHES by job_id."""
    import gspread
    from deduplication import CREDS_PATH, SPREADSHEET_NAME
    from pathlib import Path

    job_id: str = body.get("job_id", "")
    if not job_id:
        return {"ok": False, "error": "job_id required"}

    gc = gspread.service_account(filename=CREDS_PATH)
    ss = gc.open(SPREADSHEET_NAME)
    matches_sheet = ss.worksheet("MATCHES")
    cell = matches_sheet.find(job_id, in_column=1)
    if cell is None:
        return {"ok": False, "error": f"job_id {job_id} not found in MATCHES"}

    row = matches_sheet.row_values(cell.row)
    offer_data = {
        "job_id": job_id,
        "title": row[2] if len(row) > 2 else "",
        "company": row[3] if len(row) > 3 else "",
        "location": row[4] if len(row) > 4 else "",
        "url": row[6] if len(row) > 6 else "",
        "skills_found": row[9].split(", ") if len(row) > 9 and row[9] else [],
    }

    # MATCHES has no description column: the offer text lives in the offers
    # store written by pipeline_writer at scan time. CV and cover letter must
    # stick to the actual offer description, so inject it when available.
    offers_dir = Path(os.environ.get("OFFERS_STORE_DIR", "/opt/apps/job-hunter/offers"))
    detail_path = offers_dir / f"{job_id}.json"
    if detail_path.exists():
        try:
            detail = json.loads(detail_path.read_text(encoding="utf-8"))
            if detail.get("description"):
                offer_data["offer_description"] = detail["description"]
        except Exception as exc:
            logger.warning("generate: could not read offer detail %s: %s", detail_path, exc)

    # Epic 5: generation must run on Sonnet. The gateway forbids per-call
    # model overrides, so a dedicated agent (the-hunter-writer, same
    # workspace, Sonnet default) handles cv-rewriter and cover-letter-writer.
    gen_agent = os.environ.get("GENERATION_AGENT", "the-hunter-writer")
    try:
        cv_text = _extract_document(
            extract_inner(call_hunter(build_message("cv-rewriter", offer_data), "rewrite-cv", timeout=300, agent=gen_agent))
        )
        letter_text = _extract_document(
            extract_inner(call_hunter(build_message("cover-letter-writer", offer_data), "cover-letter", timeout=300, agent=gen_agent))
        )
        if not cv_text.strip() or not letter_text.strip():
            return {"ok": False, "error": "generation returned empty document", "job_id": job_id}
    except Exception as e:
        return {"ok": False, "error": f"generation failed: {e}", "job_id": job_id}

    output_dir = Path(f"/opt/apps/job-hunter/generated/{job_id}")
    output_dir.mkdir(parents=True, exist_ok=True)
    cv_path = output_dir / "cv.md"
    letter_path = output_dir / "cover_letter.md"
    cv_path.write_text(cv_text, encoding="utf-8")
    letter_path.write_text(letter_text, encoding="utf-8")

    matches_sheet.update_cell(cell.row, 11, "ready")
    matches_sheet.update_cell(cell.row, 12, str(cv_path))
    matches_sheet.update_cell(cell.row, 13, str(letter_path))

    # Epic 5 S4: YAML header + Drive upload (public link) + links in MATCHES.
    # Best-effort — a Drive failure must not lose the generated documents.
    cv_url = letter_url = None
    try:
        from datetime import date as _date

        from drive_uploader import DriveUploader, prepend_yaml_header

        today = _date.today()
        meta = {
            "Company": offer_data["company"],
            "Position": offer_data["title"],
            "Offer URL": offer_data["url"],
            "Detection date": row[1] if len(row) > 1 else "",
            "Match Rate": f"{row[8]}%" if len(row) > 8 and row[8] else "",
            "Language": "EN",
            "Status": "Generated",
        }
        slug = "".join(
            c for c in offer_data["company"] if c.isalnum() or c in " -_"
        ).strip().replace(" ", "_") or "Company"
        date_str = today.strftime("%Y-%m-%d")
        uploader = DriveUploader()
        cv_url = uploader.upload_document(
            prepend_yaml_header(cv_text, meta),
            f"CV_Patricia_Wintrebert_{slug}_{date_str}.md",
            today.strftime("%Y-%m"),
        )
        letter_url = uploader.upload_document(
            prepend_yaml_header(letter_text, meta),
            f"LM_Patricia_Wintrebert_{slug}_{date_str}.md",
            today.strftime("%Y-%m"),
        )
        uploader.update_matches(job_id, cv_url, letter_url)
    except Exception as exc:
        logger.warning("Drive upload failed for %s: %s", job_id, exc)

    return {
        "ok": True,
        "job_id": job_id,
        "cv_path": str(cv_path),
        "letter_path": str(letter_path),
        "cv_drive": cv_url,
        "letter_drive": letter_url,
        "offer": {"title": offer_data["title"], "company": offer_data["company"]},
    }


def _extract_document(raw: str) -> str:
    """Strip the agent's reasoning monologue from a generated document.

    The agent narrates ('Je vais lire la skill…') before the deliverable.
    Preference order: longest fenced markdown block, then content from the
    first YAML front-matter, then the raw text.
    """
    import re

    # Skill wrappers ([CV_START]...[CV_END], [LETTER_START]...) take priority
    wrapped = re.findall(r"\[(\w+)_START\]\s*(.*?)\s*\[\1_END\]", raw, re.S)
    if wrapped:
        return max((w[1] for w in wrapped), key=len).strip()
    blocks = re.findall(r"```(?:markdown|md)?\s*\n(.*?)```", raw, re.S)
    if blocks:
        return max(blocks, key=len).strip()
    m = re.search(r"^---\s*\n.*", raw, re.S | re.M)
    if m:
        return m.group(0).strip()
    # Last resort: drop narration lines before the first markdown heading
    # (the Kraken CV had 92 lines of prose before '## CV GÉNÉRÉ').
    m = re.search(r"^#{1,6} .*", raw, re.S | re.M)
    if m:
        return m.group(0).strip()
    return raw.strip()


def _spawn_generation(url_hash: str, chat_id: str, company: str, bot_token: str) -> None:
    """Run CV+letter generation in a background thread and confirm on Telegram.

    The callback must answer Telegram within seconds while generation takes
    minutes (two Sonnet calls); the n8n callbacks workflow used to own this
    orchestration, the local callback poller path needs it server-side.
    """
    import threading

    def _run() -> None:
        from telegram_notifier import _telegram_post

        try:
            result = _handle_generate({"job_id": url_hash})
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        if result.get("ok"):
            offer = result.get("offer", {})
            cv_link = result.get("cv_drive")
            letter_link = result.get("letter_drive")
            text = f"✅ Documents prêts : {offer.get('company', company)} — {offer.get('title', '')}"
            if cv_link and letter_link:
                text += f"\n📄 CV : {cv_link}\n📝 Lettre : {letter_link}"
            # Drive uploads are blocked for service accounts on personal
            # accounts — deliver the documents straight into the chat.
            if bot_token and chat_id:
                from datetime import date as _date
                from pathlib import Path

                from telegram_notifier import send_document

                from document_converter import convert_document

                slug = "".join(
                    c for c in offer.get("company", company) if c.isalnum() or c in " -_"
                ).strip().replace(" ", "_") or "Company"
                date_str = _date.today().strftime("%Y-%m-%d")
                for path, prefix, label in (
                    (result.get("cv_path"), "CV", "📄 CV"),
                    (result.get("letter_path"), "LM", "📝 Lettre de motivation"),
                ):
                    if not path:
                        continue
                    # Recruiters get DOCX + PDF; raw .md only if conversion fails.
                    try:
                        deliverables = convert_document(path)
                    except Exception as exc:
                        logger.error("conversion failed for %s: %s", path, exc)
                        deliverables = []
                    for out in deliverables or [Path(path)]:
                        try:
                            send_document(
                                bot_token, chat_id, str(out),
                                filename=f"{prefix}_Patricia_Wintrebert_{slug}_{date_str}{Path(out).suffix}",
                                caption=f"{label} ({Path(out).suffix.lstrip('.').upper()}) — {offer.get('company', company)}",
                            )
                        except Exception as exc:
                            logger.error("sendDocument failed for %s: %s", out, exc)
        else:
            text = f"❌ Génération échouée ({company}) : {result.get('error', '?')}"
        if bot_token and chat_id:
            try:
                _telegram_post(bot_token, "sendMessage", {"chat_id": chat_id, "text": text})
            except Exception as exc:
                logger.error("generation notify failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name=f"generate-{url_hash[:8]}").start()


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
            logger.info("GENERATE signal for hash=%s — spawning generation", url_hash)
            _spawn_generation(url_hash, chat_id, company, bot_token)
            answer_text = "🚀 Génération lancée"
            edit_text = f"⏳ Génération CV+Lettre en cours : {company}"

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
        + "\n\nIMPORTANT : réponds UNIQUEMENT avec le livrable final, dans un seul bloc"
        + " ```markdown — aucun commentaire, aucune narration avant ou après le bloc."
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
            try:
                import health_check as hc
                service_checks = [
                    hc.check_n8n(),
                    hc.check_openclaw(),
                    hc.check_google_sheets(),
                ]
            except Exception as exc:
                logger.warning("health_check import failed: %s", exc)
                service_checks = []
            services = service_checks + [{"service": "hunter_bridge", "ok": True}]
            all_ok = all(s["ok"] for s in services)
            self._send(200, {
                "status": "ok",
                "service": "hunter-bridge",
                "agent": AGENT_ID,
                "port": PORT,
                "ok": all_ok,
                "services": services,
            })
        elif self.path == "/sheets/pending-matches":
            try:
                self._send(200, _handle_read_pending_matches())
            except Exception as e:
                self._send(200, {"ok": False, "error": str(e)})
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

        _PIPELINE_HANDLERS = {
            "/dedup": _handle_dedup,
            "/layer1": _handle_layer1,
            "/layer2": _handle_layer2,
            "/sheets/write-scan-results": _handle_write_scan_results,
            "/sheets/update-status": _handle_update_status,
            "/sheets/snooze": _handle_snooze,
            "/generate": _handle_generate,
        }
        if self.path in _PIPELINE_HANDLERS:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            try:
                self._send(200, _PIPELINE_HANDLERS[self.path](body))
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
    import logging.handlers as _lh

    _log_dir = "/opt/apps/job-hunter/logs"
    os.makedirs(_log_dir, exist_ok=True)
    _log_file = f"{_log_dir}/hunter.log"
    _file_handler = _lh.TimedRotatingFileHandler(
        _log_file, when="midnight", backupCount=7, encoding="utf-8"
    )
    _fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    _file_handler.setFormatter(_fmt)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), _file_handler],
    )
    print(f"Hunter HTTP server listening on 127.0.0.1:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

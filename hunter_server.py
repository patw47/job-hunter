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
import subprocess, json, os, time
from datetime import datetime, timedelta

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
    cmd = ["openclaw", "agent", "--agent", AGENT_ID,
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
        "scan_date": scan_date,
    }


def _score_offer(offer: dict, keywords: set[str], aliases: dict[str, list[str]]) -> tuple[float, list[str]]:
    """Score an offer against SKILLS_MASTER keywords. Returns (match_rate, skills_found)."""
    if not keywords:
        return 0.0, []
    text = " ".join([offer.get("title") or "", offer.get("description") or ""]).lower()
    matched: list[str] = []
    for kw in keywords:
        if kw.lower() in text:
            matched.append(kw)
            continue
        for synonym in aliases.get(kw, []):
            if synonym.lower() in text:
                matched.append(kw)
                break
    return round(len(matched) / len(keywords), 4), sorted(matched)


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
        job_id = compute_hash(offer.get("url", ""))
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

    try:
        cv_text = extract_inner(call_hunter(build_message("cv-rewriter", offer_data), "rewrite-cv", timeout=300))
        letter_text = extract_inner(call_hunter(build_message("cover-letter-writer", offer_data), "cover-letter", timeout=300))
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

    return {
        "ok": True,
        "job_id": job_id,
        "cv_path": str(cv_path),
        "letter_path": str(letter_path),
        "offer": {"title": offer_data["title"], "company": offer_data["company"]},
    }


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

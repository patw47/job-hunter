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

    matches_sheet.append_rows(matches_rows, value_input_option="RAW")
    pending_sheet.append_rows(pending_rows, value_input_option="RAW")

    return {"ok": True, "written_count": len(offers), "scan_date": scan_date}


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
        _PIPELINE_HANDLERS = {
            "/dedup": _handle_dedup,
            "/layer1": _handle_layer1,
            "/layer2": _handle_layer2,
            "/sheets/write-scan-results": _handle_write_scan_results,
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
    print(f"Hunter HTTP server listening on 127.0.0.1:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

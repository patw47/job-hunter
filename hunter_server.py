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
import subprocess, json, os, time
from datetime import datetime

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
        if self.path == "/prioritize":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            try:
                self._send(200, _handle_prioritize(body))
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

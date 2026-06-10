#!/home/thehunter/venv/bin/python3
"""
Telegram command dispatcher for The Hunter.

Called by n8n when Telegram sends a command or inline-button callback.

Usage:
    telegram_notifier.py status <job_id>
    telegram_notifier.py update <job_id> <statut>
    telegram_notifier.py note <job_id> <texte libre>
    telegram_notifier.py mark_sent <job_id>

job_id must be a 64-character lowercase hex SHA-256 digest.
Stdout: single JSON line — {"ok": bool, "message": str}
Exit 0 always (caller reads "ok" field, not exit code).
"""
from __future__ import annotations

import json
import logging
import re
import sys

import matches_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_JOB_ID_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{64}$")


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

    else:
        _result(False, f"❌ Commande inconnue : {command!r}\nCommandes : status | update | note | mark_sent")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

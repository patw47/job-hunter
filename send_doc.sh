#!/bin/bash
# send_doc.sh <job_id> <cv|letter> [caption]
# Convertit le document source (.md) en DOCX + PDF et envoie les deux à
# Patricia via le bot cartes. C'est LE seul canal autorisé pour livrer un
# document — jamais de .md brut.
set -euo pipefail

JOB_ID="${1:?usage: send_doc.sh <job_id> <cv|letter> [caption]}"
KIND="${2:?usage: send_doc.sh <job_id> <cv|letter> [caption]}"
CAPTION="${3:-Document — $JOB_ID}"

cd /opt/apps/job-hunter
set -a; . ./.env; set +a

case "$KIND" in
  cv)     SRC="generated/$JOB_ID/cv.md";           PREFIX="CV" ;;
  letter) SRC="generated/$JOB_ID/cover_letter.md"; PREFIX="LM" ;;
  *) echo "type invalide: $KIND (cv|letter)"; exit 1 ;;
esac

[ -f "$SRC" ] || { echo "introuvable: $SRC"; exit 1; }

/home/thehunter/venv/bin/python3 - "$SRC" "$PREFIX" "$CAPTION" <<'EOF'
import sys
sys.path.insert(0, "/opt/apps/job-hunter")
import os
from datetime import date
from document_converter import convert_document
from telegram_notifier import send_document

src, prefix, caption = sys.argv[1], sys.argv[2], sys.argv[3]
tok = os.environ["TELEGRAM_HUNTER_BOT_TOKEN"]
chat = os.environ["TELEGRAM_HUNTER_CHAT_ID"]
outs = convert_document(src)
if not outs:
    raise SystemExit("conversion échouée — rien envoyé")
for o in outs:
    r = send_document(
        tok, chat, str(o),
        f"{prefix}_Patricia_Wintrebert_{date.today().isoformat()}{o.suffix}",
        f"{caption} ({o.suffix.lstrip('.').upper()})",
    )
    print(o.suffix, "ok" if r.get("ok") else f"ÉCHEC: {r}")
EOF

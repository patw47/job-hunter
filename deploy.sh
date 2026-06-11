#!/bin/bash
# deploy.sh — déploie main sur le VPS et relance les services.
#
# Usage (depuis le poste local) : ./deploy.sh
#
# Étapes : push vérifié → git pull sur le VPS → restart des services →
# health checks → notification Telegram de The Hunter (« réveillé »).
set -euo pipefail

VPS="hetzner-vps"
APP_DIR="/opt/apps/job-hunter"
SERVICES="hunter-server hunter-poller job-hunter"

echo "── Déploiement job-hunter ──────────────────────────"

# 1. Le local doit être propre et poussé
if [ -n "$(git status --porcelain)" ]; then
  echo "✗ Working tree local non propre — commit/stash d'abord."; exit 1
fi
git push origin main
LOCAL_SHA=$(git rev-parse --short HEAD)
echo "✓ main poussé ($LOCAL_SHA)"

# 2. Pull + restart sur le VPS
ssh "$VPS" "
set -e
sudo -n -u thehunter git -C $APP_DIR pull origin main
DEPLOYED=\$(sudo -n -u thehunter git -C $APP_DIR rev-parse --short HEAD)
echo \"✓ VPS à \$DEPLOYED\"
sudo -n systemctl restart $SERVICES
sleep 8
"

# 3. Health checks
ssh "$VPS" '
set -e
for s in hunter-server hunter-poller job-hunter; do
  st=$(systemctl is-active "$s")
  echo "  $s: $st"
  [ "$st" = "active" ] || exit 1
done
curl -sf http://127.0.0.1:18798/health > /dev/null && echo "  bridge /health: ok"
curl -sf http://127.0.0.1:5682/healthz > /dev/null && echo "  n8n /healthz: ok"
'

# 4. The Hunter annonce son réveil sur Telegram
ssh "$VPS" "
set -a; . $APP_DIR/.env; set +a
curl -s -X POST \"https://api.telegram.org/bot\$TELEGRAM_BOT_TOKEN/sendMessage\" \
  -H 'Content-Type: application/json' \
  -d \"{\\\"chat_id\\\": \\\"\$TELEGRAM_CHAT_ID\\\", \\\"text\\\": \\\"🎯 The Hunter réveillé — déploiement $LOCAL_SHA terminé.\\nServices: hunter-server ✓ hunter-poller ✓ n8n ✓\\nProchain scan: 13h00.\\\"}\" > /dev/null
" && echo "✓ Notification Telegram envoyée"

echo "── Déploiement $LOCAL_SHA OK ───────────────────────"

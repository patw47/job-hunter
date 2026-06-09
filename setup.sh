#!/usr/bin/env bash
# setup.sh — Deploy job-hunter workspace to the VPS
#
# Prerequisites on VPS:
#   - User thehunter + group apps exist
#   - openclaw installed: /home/thehunter/.npm-global/bin/openclaw
#   - Python venv at /home/thehunter/venv
#   - /opt/apps/job-hunter/.env exists with required vars:
#       ANTHROPIC_API_KEY, TELEGRAM_HUNTER_BOT_TOKEN, TELEGRAM_HUNTER_CHAT_ID
#
# Run as: sudo bash setup.sh

set -euo pipefail

APP_USER=thehunter
APP_GROUP=apps
HOME_DIR=/home/thehunter
OPENCLAW_DIR=$HOME_DIR/.openclaw
WORKSPACE_DIR=$OPENCLAW_DIR/workspace
APP_DIR=/opt/apps/job-hunter
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== job-hunter workspace setup ==="
echo "App dir   : $APP_DIR"
echo "Workspace : $WORKSPACE_DIR"

# ── 1. Directories ────────────────────────────────────────────────────────────

echo "[1/7] Creating directories..."
install -d -m 750 -o $APP_USER -g $APP_GROUP "$OPENCLAW_DIR"
install -d -m 750 -o $APP_USER -g $APP_GROUP "$WORKSPACE_DIR"
install -d -m 750 -o $APP_USER -g $APP_GROUP "$WORKSPACE_DIR/the-hunter"
for skill in offer-analysis cv-rewriter cover-letter-writer form-answerer weekly-reporter; do
    install -d -m 750 -o $APP_USER -g $APP_GROUP "$WORKSPACE_DIR/skills/$skill"
done
install -d -m 755 -o $APP_USER -g $APP_GROUP "$APP_DIR"

# ── 2. Context MD files ───────────────────────────────────────────────────────

echo "[2/7] Deploying context MD files..."
for f in SOUL.md HEARTBEAT.md TOOLS.md KEYWORDS.md ARCHITECTURE-VPS.md SKILLS_MASTER.md; do
    if [ -f "$SCRIPT_DIR/workspace/the-hunter/$f" ]; then
        install -m 640 -o $APP_USER -g $APP_GROUP \
            "$SCRIPT_DIR/workspace/the-hunter/$f" \
            "$WORKSPACE_DIR/the-hunter/$f"
        echo "  ✓ $f"
    else
        echo "  ⚠ MISSING: workspace/the-hunter/$f (skipped)"
    fi
done

# Personal data files — only deploy if they exist and are filled in
for f in CV_BASE.md USER.md GITHUB_REPOS.md; do
    if [ -f "$SCRIPT_DIR/workspace/the-hunter/$f" ]; then
        if grep -q "FILL IN" "$SCRIPT_DIR/workspace/the-hunter/$f"; then
            echo "  ⚠ STUB detected in $f — please fill in personal data before deploying"
        fi
        install -m 640 -o $APP_USER -g $APP_GROUP \
            "$SCRIPT_DIR/workspace/the-hunter/$f" \
            "$WORKSPACE_DIR/the-hunter/$f"
        echo "  ✓ $f (may contain stubs)"
    else
        echo "  ⚠ MISSING: workspace/the-hunter/$f — create before first run"
    fi
done

# ── 3. SKILL.md files ─────────────────────────────────────────────────────────

echo "[3/7] Deploying SKILL.md files..."
for skill in offer-analysis cv-rewriter cover-letter-writer form-answerer weekly-reporter; do
    src="$SCRIPT_DIR/workspace/skills/$skill/SKILL.md"
    dst="$WORKSPACE_DIR/skills/$skill/SKILL.md"
    if [ -f "$src" ]; then
        install -m 640 -o $APP_USER -g $APP_GROUP "$src" "$dst"
        # Verify required frontmatter
        if grep -q '^description:' "$dst"; then
            echo "  ✓ $skill/SKILL.md (description: OK)"
        else
            echo "  ✗ ERROR: $skill/SKILL.md missing 'description:' frontmatter — OpenClaw will drop this skill!"
            exit 1
        fi
    else
        echo "  ✗ MISSING: workspace/skills/$skill/SKILL.md"
        exit 1
    fi
done

# ── 4. openclaw.json ──────────────────────────────────────────────────────────

echo "[4/7] Generating openclaw.json from .env..."
if [ ! -f "$APP_DIR/.env" ]; then
    echo "  ✗ ERROR: $APP_DIR/.env not found. Cannot generate openclaw.json."
    exit 1
fi

# Source .env (only safe keys)
set +u
# shellcheck disable=SC1090
source <(grep -E '^(TELEGRAM_HUNTER_BOT_TOKEN|TELEGRAM_HUNTER_CHAT_ID)=' "$APP_DIR/.env")
set -u

if [ -z "${TELEGRAM_HUNTER_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_HUNTER_CHAT_ID:-}" ]; then
    echo "  ✗ ERROR: TELEGRAM_HUNTER_BOT_TOKEN and/or TELEGRAM_HUNTER_CHAT_ID missing from .env"
    exit 1
fi

OPENCLAW_JSON="$OPENCLAW_DIR/openclaw.json"
# Use Python for substitution — sed chokes on | and & in token values.
python3 - "$SCRIPT_DIR/workspace/openclaw.json.template" "$OPENCLAW_JSON" \
    "$TELEGRAM_HUNTER_BOT_TOKEN" "$TELEGRAM_HUNTER_CHAT_ID" <<'PYEOF'
import sys
template_path, out_path, bot_token, chat_id = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
content = open(template_path).read()
content = content.replace("TELEGRAM_HUNTER_BOT_TOKEN", bot_token)
content = content.replace("TELEGRAM_HUNTER_CHAT_ID", chat_id)
open(out_path, "w").write(content)
PYEOF
chown "$APP_USER:$APP_GROUP" "$OPENCLAW_JSON"
chmod 600 "$OPENCLAW_JSON"
echo "  ✓ openclaw.json written (mode 600)"

# ── 5. App files ──────────────────────────────────────────────────────────────

echo "[5/7] Deploying app files..."
install -m 640 -o $APP_USER -g $APP_GROUP \
    "$SCRIPT_DIR/hunter_server.py" "$APP_DIR/hunter_server.py"
echo "  ✓ hunter_server.py"

# ── 6. Systemd services ───────────────────────────────────────────────────────

echo "[6/7] Installing systemd services..."
install -m 644 -o root -g root \
    "$SCRIPT_DIR/openclaw-thehunter.service" /etc/systemd/system/openclaw-thehunter.service
install -m 644 -o root -g root \
    "$SCRIPT_DIR/hunter-server.service" /etc/systemd/system/hunter-server.service
systemctl daemon-reload
systemctl enable openclaw-thehunter.service hunter-server.service
echo "  ✓ Services enabled"

# ── 7. Start services ─────────────────────────────────────────────────────────

echo "[7/7] Starting services..."
systemctl restart openclaw-thehunter.service
# Wait for openclaw gateway to be ready before starting the bridge.
for i in 1 2 3 4 5; do
    sleep 1
    systemctl is-active --quiet openclaw-thehunter.service && break
    [ "$i" -eq 5 ] && { echo "  ✗ openclaw-thehunter failed to start"; systemctl status openclaw-thehunter --no-pager | tail -15; exit 1; }
done
systemctl restart hunter-server.service
sleep 2

# Validate
echo ""
echo "=== Validation ==="

# Service status
if systemctl is-active --quiet openclaw-thehunter.service; then
    echo "  ✓ openclaw-thehunter.service: active"
else
    echo "  ✗ openclaw-thehunter.service: FAILED"
    systemctl status openclaw-thehunter.service --no-pager | tail -10
fi

if systemctl is-active --quiet hunter-server.service; then
    echo "  ✓ hunter-server.service: active"
else
    echo "  ✗ hunter-server.service: FAILED"
    systemctl status hunter-server.service --no-pager | tail -10
fi

# Health check
sleep 1
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:18798/health 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✓ GET /health → 200"
    curl -s http://127.0.0.1:18798/health
    echo ""
else
    echo "  ✗ GET /health → $HTTP_CODE (expected 200)"
fi

# Skills check
echo ""
echo "  Skills loaded:"
sudo -u $APP_USER env HOME=$HOME_DIR openclaw skills list --agent the-hunter 2>/dev/null || \
    echo "  (openclaw skills list failed — check openclaw-thehunter.service logs)"

echo ""
echo "=== Setup complete ==="
echo "Next steps:"
echo "  1. Fill in personal data: workspace/the-hunter/{CV_BASE.md,USER.md,GITHUB_REPOS.md}"
echo "  2. Send a test Telegram message to validate the bot"
echo "  3. Run: curl -s -X POST http://127.0.0.1:18798/analyze -H 'Content-Type: application/json' -d '{\"title\":\"AI Engineer\",\"description\":\"Remote LLM Python role\"}'"

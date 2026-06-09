# Architecture VPS — The Hunter

## Stack de déploiement

```
VPS Ubuntu (systemd)
├── openclaw-thehunter.service  ← OpenClaw gateway (Telegram bot + CLI backend)
│     └── port: local socket / daemon
├── hunter-server.service       ← Python HTTP bridge (:18798, 127.0.0.1 only)
│     └── hunter_server.py
└── (n8n intégré à property-cm.service ou standalone)
```

## Utilisateur système

- **User** : `thehunter`
- **Group** : `apps`
- **Home** : `/home/thehunter`
- **Venv Python** : `/home/thehunter/venv`
- **OpenClaw** : `/home/thehunter/.npm-global/bin/openclaw`

## Répertoires clés

| Chemin | Contenu |
|--------|---------|
| `/home/thehunter/.openclaw/` | Config OpenClaw root |
| `/home/thehunter/.openclaw/openclaw.json` | Config gateway (tokens Telegram, workspace path) |
| `/home/thehunter/.openclaw/workspace/` | Workspace agent |
| `/home/thehunter/.openclaw/workspace/the-hunter/` | Context files agent |
| `/home/thehunter/.openclaw/workspace/skills/` | Skills SKILL.md |
| `/opt/apps/job-hunter/` | App directory |
| `/opt/apps/job-hunter/.env` | Secrets (API keys, tokens) — gitignored |
| `/opt/apps/job-hunter/credentials.json` | Service account Google — gitignored |
| `/opt/apps/job-hunter/hunter_server.py` | HTTP bridge |

## Variables d'environnement (.env)

```bash
ANTHROPIC_API_KEY=...          # Claude Haiku + Sonnet
TELEGRAM_HUNTER_BOT_TOKEN=...  # Bot Telegram The Hunter
TELEGRAM_HUNTER_CHAT_ID=...    # Chat ID Patricia
GOOGLE_SHEETS_SPREADSHEET_ID=... # job-hunter-tracker
```

## Ports

| Port | Service |
|------|---------|
| 18798 | hunter-server.service (HTTP bridge, 127.0.0.1 only) |

## Flux n8n → agent

```
n8n POST /analyze (payload offre)
  → hunter_server.py do_POST()
  → subprocess: openclaw agent --agent the-hunter --message [...] --json
  → OpenClaw → Haiku (offer-analysis skill)
  → JSON response → n8n
```

## Flux Telegram → agent

```
Message Telegram Patricia
  → openclaw-thehunter.service (getUpdates long-polling)
  → OpenClaw → route vers skill appropriée
  → Haiku / Sonnet selon skill
  → Réponse Telegram
```

## Commandes de debug

```bash
# Status services
sudo systemctl status openclaw-thehunter.service
sudo systemctl status hunter-server.service

# Logs
sudo journalctl -u openclaw-thehunter -n 50 -f
sudo journalctl -u hunter-server -n 50 -f

# Skills chargées
sudo -u thehunter env HOME=/home/thehunter openclaw skills list --agent the-hunter

# Health check
curl -s http://127.0.0.1:18798/health

# Test analyze
curl -s -X POST http://127.0.0.1:18798/analyze \
  -H "Content-Type: application/json" \
  -d '{"title":"AI Engineer","description":"Remote LLM Python role"}'
```

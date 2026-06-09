# Heartbeat — Schedule

## Daily scan — 12h00 Paris time (Europe/Paris)

Triggered by n8n scheduler.

### Séquence

| Heure | Action |
|-------|--------|
| 12h00 | Lire PENDING_MATCHES + SCANNED_HASHES depuis Google Sheets |
| 12h00 | Scanner Indeed (60 offres) via MCP |
| 12h00 | Scanner LinkedIn (40 offres) via Playwright |
| 12h00 | Layer 1 : filtres éliminatoires (Python) |
| 12h00 | Layer 2 : calcul match rate vs SKILLS_MASTER.md (Python) |
| 12h00 | Écrire SCANNED_HASHES (toutes offres vues) |
| 12h00 | Écrire MATCHES / PENDING_MATCHES (≥ 60%) |
| 12h30 | Notifications Telegram : ≥ 80% immédiat, 60-79% digest max 25 |

### Seuils

- `< 60%` → log silencieux dans SCANNED_HASHES uniquement
- `≥ 60%` → MATCHES + éligible notification
- `≥ 80%` → notification individuelle immédiate
- `≥ 80%` (Phase 2) → auto Easy Apply avec fenêtre veto 2h

### Fermeture annuelle

Aucune communication du 15 décembre au 20 janvier.

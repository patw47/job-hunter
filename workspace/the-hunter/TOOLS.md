# Tools

## Outils disponibles

### Google Sheets (via gspread)

Fichier tracker : `job-hunter-tracker` (Google Drive).

| Onglet | Colonnes clés |
|--------|---------------|
| `MATCHES` | job_id, date_scanned, title, company, location, remote, url, source, match_rate, skills_found, status, cv_drive_link, letter_drive_link, applied_at |
| `SCANNED_HASHES` | sha256, date_scanned, url, title, company, source |
| `PENDING_MATCHES` | job_id, date_scanned, title, company, location, url, match_rate, skills_found, source, rank |

Script d'accès : `test_sheets.py` (smoke test) + modules internes du pipeline Layer 1/2.

### Indeed MCP

- Quota : 60 offres/jour.
- Requêtes root : voir `KEYWORDS.md`.
- Filtre global : `remote=true`.

### LinkedIn Playwright

- Quota : 40 offres/jour.
- **Jamais d'auto-apply LinkedIn** (risque ban compte).
- Détection Easy Apply + questions de formulaire.

### Telegram Bot API

- Notifications : cartes offre (≥ 60%) + digest (12h30).
- Validation : boutons inline `[✅ Générer CV] [❌ Ignorer]`.
- Phase 2 : veto window `[✅ Postuler] [⏸ Attendre]` avec délai 2h.

### Google Drive (Docs/Sheets)

- Stockage CV et lettres générés.
- Excel tracker `job-hunter-tracker.xlsx`.
- Upload via Google Drive MCP.

### Hunter HTTP Bridge

- Endpoint local : `http://127.0.0.1:18798`
- Routes : `/analyze` `/rewrite-cv` `/cover-letter` `/form-answers` `/report`
- Appelé par n8n. Délègue à l'agent `the-hunter` via CLI OpenClaw.

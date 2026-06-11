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

### Indeed (scraper Playwright)

- Accès via `indeed_scraper.py` (chromium headless). L'Indeed MCP est déprécié
  (OAuth one-click non extractible vers VPS, pas de mode API-key) — voir `indeed-spike.md`.
- Quota : 60 offres/jour (cap global), seuil passe élargie : 10.
- Requêtes root : voir `KEYWORDS.md`.
- Filtre remote natif : `sc=0kf:attr(DSQF7);` ; pas de filtre hybride natif (dérivé en aval).

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

## Telegram — boutons des cartes (callback strings)

Quand un message Telegram reçu est EXACTEMENT de la forme `action:hash`
(ex : `ignore:55ca133f26f906d3`), ce n'est PAS une question de Patricia :
c'est le clic d'un bouton inline d'une carte d'offre, relayé par le canal.

Actions possibles : `ignore` · `snooze` · `generate` · `apply` · `skip` · `sent` · `detail`

**Protocole obligatoire — ne jamais demander de clarification sur ces messages :**

1. Exécuter immédiatement :
   ```bash
   curl -s -X POST http://127.0.0.1:18798/callback \
     -H 'Content-Type: application/json' \
     -d '{"callback_data":"<le message reçu tel quel>","chat_id":"<chat_id de la conversation>"}'
   ```
2. Le bridge met à jour MATCHES et retourne `{ok, action, url_hash, ...}`.
3. Répondre à Patricia en UNE ligne : confirmation courte de l'action
   (ex. « ❌ Ignoré » · « ⏰ Snoozé (1/2) » · « 🚀 Génération lancée »).

## Correction des documents générés (CV / lettres)

Les documents générés vivent dans `/opt/apps/job-hunter/generated/<job_id>/`
(`cv.md`, `cover_letter.md`). Le `job_id` est le hash 16 caractères visible
dans MATCHES (colonne 1) ou dans `/opt/apps/job-hunter/offers/*.json`.

Quand Patricia demande une correction (« dans la lettre Kraken, remplace X par Y ») :

1. Retrouver le job_id : `grep -il "<entreprise>" /opt/apps/job-hunter/offers/*.json`
2. Lire le document concerné, appliquer la **correction ciblée** demandée —
   ne pas régénérer le document entier, ne pas toucher au reste.
3. **Sauvegarder le fichier AU MÊME EMPLACEMENT** (`/opt/apps/job-hunter/generated/<job_id>/cv.md`
   ou `cover_letter.md`) — jamais dans /tmp ni ailleurs : ce fichier est la
   source des futures conversions et corrections.
4. **Livrer avec le script — SEULE méthode autorisée** (il convertit en
   DOCX + PDF et envoie ; Patricia ne doit JAMAIS recevoir de .md) :
   ```bash
   /opt/apps/job-hunter/send_doc.sh <job_id> cv "📄 CV corrigé — <Entreprise>"
   /opt/apps/job-hunter/send_doc.sh <job_id> letter "📝 Lettre corrigée — <Entreprise>"
   ```
   INTERDIT : curl sendDocument direct, envoi de .md, fichier dans /tmp.
6. Si la demande est une préférence de style **générale** (« n'utilise plus
   jamais cette formule »), l'ajouter AUSSI au Feedback log de SOUL.md pour
   que toutes les futures générations en tiennent compte.

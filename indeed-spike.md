# Spike Cloudflare — Indeed scraping (bloquant)

**Sprint 0 Revision — Indeed MCP Integration**
**Date :** 2026-06-10
**Statut :** consigné — exécution live requise sur le VPS (pas de réseau en CI/worktree)

---

## Objectif

Avant de câbler tout le pipeline, vérifier qu'un chromium headless peut charger
**une** page de résultats Indeed et y trouver **≥ 1 card réelle** (`.job_seen_beacon`)
plutôt qu'une interstitielle Cloudflare (« Just a moment… »). Si bloqué → documenter
la mitigation, ne pas câbler le reste.

## Pourquoi ce spike existe

Le Sprint 0 initial supposait un accès Indeed MCP headless via `INDEED_MCP_API_KEY`.
Faux :

- l'accès Indeed MCP = OAuth one-click claude.ai (token chez Anthropic, **non
  extractible** vers un VPS) ;
- pas de mode API-key serveur ;
- l'API Indeed officielle de recherche est **dépréciée depuis 2023** ;
- les API restantes sont côté employeur/ATS.

→ Seul chemin headless restant = **scraper Playwright**, comme `linkedin_scraper.py`.
Mais Indeed est protégé par Cloudflare : le spike valide que c'est franchissable.

## Comment lancer le spike (sur le VPS)

```bash
cd /opt/apps/job-hunter
/home/thehunter/venv/bin/python3 -m pip install -r requirements.txt
/home/thehunter/venv/bin/playwright install chromium

# 1 URL de résultats remote (racine « AI »)
/home/thehunter/venv/bin/python3 indeed_scraper.py \
  --spike "https://fr.indeed.com/jobs?q=AI&l=&sc=0kf%3Aattr(DSQF7)%3B"
```

### Lecture du résultat

| Sortie stderr | Signification | Exit |
|---|---|---|
| `✓ SPIKE OK : N card(s) réelle(s)…` | Cloudflare franchi, scraping viable | 0 |
| `✗ SPIKE BLOQUÉ : Cloudflare/CAPTCHA détecté` | Interstitielle servie, scraping bloqué | 1 |
| `✗ SPIKE ERREUR fatale …` | Navigateur absent / réseau / timeout | 1 |

`detect_captcha()` cherche (insensible à la casse) : `just a moment...`,
`cf-browser-verification`, `checking your browser`, `verifying you are human`,
`hcaptcha.com/1/api.js`, `h-captcha`, `_cf_chl_opt`, `additional verification required`.

## Mitigations si BLOQUÉ (par ordre de coût croissant)

1. **UA + locale réalistes** — déjà appliqués (`USER_AGENT` desktop Chrome, `locale=fr-FR`).
   Le `HeadlessChrome` par défaut est un marqueur trivial pour Cloudflare.
2. **`headless=False` sous Xvfb** — un headful virtualisé passe souvent là où le
   headless échoue. Coût : paquet `xvfb` + `xvfb-run` autour du process.
3. **`playwright-stealth`** — masque `navigator.webdriver` et consorts. Coût : 1 dép.
4. **Throttling agressif** — délais déjà jitterés (`DELAY_MIN`/`DELAY_MAX` = 1.5–3.5 s) ;
   les augmenter si rate-limit observé.
5. **Résidentiel / proxy** — dernier recours si l'IP du VPS est blacklistée. Coût : abo proxy.

Si aucune mitigation 1–4 ne débloque : **ne pas câbler le scan**, escalader (changer de
source ou revoir le volume Indeed). Le workflow n8n reste alors inactif (`active: false`).

## Contrainte CI / worktree

Le spike **ne peut pas** tourner en CI ni dans le worktree de dev : pas de réseau
sortant, pas de navigateur installé. Le parsing (`parse_cards`, `detect_captcha`,
`normalize_offer`) est en revanche testé hors-ligne contre du HTML mocké
(`tests/test_indeed_scraper.py`). Le spike live est une étape **manuelle VPS**.

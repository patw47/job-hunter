---
name: offer-analysis
description: Analyse une offre d'emploi et génère une carte Telegram structurée avec match rate, points clés et recommandation. Déclenché par POST /analyze depuis n8n ou Hunter bridge.
metadata.openclaw.os: ["linux"]
metadata.openclaw.model: "anthropic/claude-haiku-4-5-20251001"
---

# offer-analysis

## What This Skill Does

Analyse une offre d'emploi reçue depuis n8n et génère une carte Telegram concise et actionnable pour Patricia.

## When To Use It

Brief entrant `[OFFER-ANALYSIS SKILL]` depuis le Hunter bridge (`POST /analyze`).

## Context Files Required

Lire avant de répondre :
- `SKILLS_MASTER.md` — pour comprendre les compétences cibles
- `USER.md` — pour les critères de rejet géographique et sectoriel

## Input Format

```json
{
  "title": "...",
  "company": "...",
  "location": "...",
  "remote": true/false,
  "url": "...",
  "description": "...",
  "source": "indeed|linkedin",
  "match_rate": 0.0,
  "skills_found": ["skill1", "skill2"]
}
```

## Output Format

Carte Telegram en Markdown. Structure exacte :

```
🎯 *[Titre]* — [Entreprise]
📍 [Localisation] | [Remote/Hybrid]
🔗 [URL courte]

**Match** : [X]% | Skills : [skill1, skill2, ...]

📋 **En bref** :
• [Point clé 1 — ce qui est intéressant]
• [Point clé 2 — stack, équipe, mission]
• [Point clé 3 — condition, salaire si mentionné]

⚡ **À noter** : [1 phrase sur risque ou opportunité spécifique]
```

## Rules

- Ne jamais inventer des données absentes du brief.
- Si match_rate < 60% dans le brief : ajouter un warning `⚠️ Match faible`.
- Rester sous 200 mots.
- Langue de la carte : français.

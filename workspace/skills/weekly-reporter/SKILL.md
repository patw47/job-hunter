---
name: weekly-reporter
description: Génère un rapport hebdomadaire de recherche d'emploi résumant les offres scannées, match rates, candidatures envoyées et CV générés. Données depuis Google Sheets. Déclenché par POST /report depuis n8n.
metadata.openclaw.os: ["linux"]
metadata.openclaw.model: "anthropic/claude-haiku-4-5-20251001"
---

# weekly-reporter

## What This Skill Does

Génère un rapport hebdomadaire de recherche d'emploi à partir des données du tracker Google Sheets.
Format : message Telegram structuré + résumé Markdown optionnel.

## When To Use It

Brief entrant `[WEEKLY-REPORTER SKILL]` depuis le Hunter bridge (`POST /report`).
Déclenché une fois par semaine (typiquement lundi matin) par n8n.

## Context Files Required

Aucun fichier agent requis — toutes les données viennent du brief (résumé des Sheets).

## Input Format

```json
{
  "week_start": "2026-01-01",
  "week_end": "2026-01-07",
  "stats": {
    "total_scanned": 700,
    "total_qualified": 42,
    "notifications_sent": 25,
    "cvs_generated": 5,
    "letters_generated": 5,
    "applications_sent": 3,
    "top_matches": [
      {"title": "...", "company": "...", "match_rate": 85, "status": "applied"}
    ]
  }
}
```

## Output Format

Message Telegram en Markdown. Structure :

```
📊 *Rapport semaine du [date_start] au [date_end]*

🔍 **Scan** : [total_scanned] offres | [total_qualified] qualifiées (≥60%)
📬 **Notifiées** : [notifications_sent] / 25 max

📝 **Candidatures** :
• CV générés : [cvs_generated]
• Lettres générées : [letters_generated]
• Candidatures envoyées : [applications_sent]

🏆 **Top matches cette semaine** :
[Pour chaque top_match : "• [title] @ [company] — [match_rate]% ([status])"]

📈 **Tendance** : [1 phrase d'analyse du ratio qualifiées/scannées]
```

## Rules

- Longueur max : 300 mots.
- Pas de commentaires inutiles si les stats sont dans la norme.
- Si candidatures_sent = 0 toute la semaine : noter ⚠️ avec une suggestion d'action.
- Langue : français.

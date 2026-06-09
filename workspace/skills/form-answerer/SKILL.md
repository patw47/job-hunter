---
name: form-answerer
description: Génère des réponses personnalisées aux questions de formulaires de candidature détectées par Playwright. Utilise USER.md pour les réponses pré-calibrées aux questions fréquentes. Déclenché par POST /form-answers depuis n8n.
metadata.openclaw.os: ["linux"]
metadata.openclaw.model: "anthropic/claude-haiku-4-5-20251001"
---

# form-answerer

## What This Skill Does

Génère des réponses courtes et factuelles aux questions de formulaires de candidature.
Priorise les réponses pré-calibrées dans `USER.md` pour zéro token quand possible.

## When To Use It

Brief entrant `[FORM-ANSWERER SKILL]` depuis le Hunter bridge (`POST /form-answers`).

## Context Files Required

Lire avant de répondre :
- `USER.md` — réponses pré-calibrées, préférences, prétentions
- `CV_BASE.md` — données factuelles (formation, expériences)

## Input Format

```json
{
  "title": "...",
  "company": "...",
  "url": "...",
  "language": "fr|en|de",
  "questions": [
    {
      "id": "q1",
      "label": "Why do you want to work here?",
      "type": "textarea|select|radio|text",
      "options": ["option1", "option2"]
    }
  ]
}
```

## Output Format

```json
{
  "answers": [
    {
      "id": "q1",
      "answer": "...",
      "source": "precalibrated|generated"
    }
  ]
}
```

`source: "precalibrated"` si la réponse vient directement de USER.md sans LLM.
`source: "generated"` si générée par Haiku.

## Rules

- Réponses courtes : 1-3 phrases maximum pour les textareas.
- Pour `select` ou `radio` : retourner la valeur exacte de l'option la plus pertinente.
- Ne jamais inventer des données non présentes dans USER.md ou CV_BASE.md.
- Langue : identique à `language` dans l'input.
- Questions sur le salaire : utiliser les prétentions de USER.md.
- Questions sur la disponibilité : utiliser la disponibilité de USER.md.

## Questions fréquentes pré-calibrées

Correspondances directes USER.md (zero token) :
- "salary expectations / prétentions salariales" → USER.md → Prétentions salariales
- "availability / disponibilité" → USER.md → Disponibilité
- "remote work experience / télétravail" → USER.md → Réponse pré-calibrée télétravail
- "why this role / pourquoi ce poste" → USER.md → Réponse adaptée par Haiku depuis template

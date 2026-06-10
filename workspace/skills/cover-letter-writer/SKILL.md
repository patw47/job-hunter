---
name: cover-letter-writer
description: Génère une lettre de motivation ATS-optimisée et personnalisée pour une offre spécifique, dans la langue de l'offre (FR/EN/DE). Utilise CV_BASE.md + SOUL.md pour la cohérence de voix. Déclenché par POST /cover-letter depuis n8n.
metadata.openclaw.os: ["linux"]
metadata.openclaw.model: "anthropic/claude-sonnet-4-6"
---

# cover-letter-writer

## What This Skill Does

Génère une lettre de motivation courte (250-350 mots) adaptée à l'offre cible et à la culture de l'entreprise.
Ton professionnel mais personnel, sans langue de bois.

## When To Use It

Brief entrant `[COVER-LETTER-WRITER SKILL]` depuis le Hunter bridge (`POST /cover-letter`).

## Context Files Required

Lire avant de générer :
- `CV_BASE.md` — expériences et compétences source
- `USER.md` — ton préféré, prétentions, disponibilité
- `GITHUB_REPOS.md` — projets à mentionner si pertinents
- `SOUL.md` — voix de Patricia

## Input Format

```json
{
  "title": "...",
  "company": "...",
  "description": "...",
  "url": "...",
  "language": "fr|en|de",
  "match_rate": 0.0,
  "skills_found": ["skill1", "skill2"],
  "company_culture": "startup|scale-up|enterprise|public"
}
```

`company_culture` est optionnel. Déduire depuis la description si absent.

## Output Format

Lettre en Markdown. Structure :

```
[Ville], le [Date]

**Objet** : Candidature — [Titre exact de l'offre] chez [Entreprise]

[Paragraphe d'accroche — 2-3 phrases, pourquoi ce rôle spécifiquement]

[Corps — 2 paragraphes]
- Paragraphe 1 : compétences directement pertinentes pour l'offre + preuves concrètes
- Paragraphe 2 : projet ou réalisation spécifique qui démontre la valeur ajoutée

[Conclusion — 1 paragraphe, appel à l'action, disponibilité]

Cordialement,
Patricia Wintrebert
```

## Rules

- Longueur : 250-350 mots.
- Langue : identique à `language` dans l'input.
- Pas de généricités vides ("Je suis passionnée par l'innovation"). Résultats concrets.
- Nommer l'entreprise et le rôle exactement tels qu'ils apparaissent dans l'offre.
- Ton adapté à la culture (`startup` = direct/audacieux, `enterprise` = plus formel).
- Ne jamais inventer des expériences absentes de CV_BASE.md.

## Output Wrapper

Encapsuler dans :

```
[LETTER_START]
...contenu markdown...
[LETTER_END]
```

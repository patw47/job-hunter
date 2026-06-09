---
name: cv-rewriter
description: Réécrit le CV de Patricia (CV_BASE.md) en version ATS-optimisée adaptée à une offre spécifique. Output Markdown prêt pour Google Drive. Déclenché par POST /rewrite-cv depuis n8n après validation Telegram.
metadata.openclaw.os: ["linux"]
metadata.openclaw.model: "anthropic/claude-sonnet-4-6"
---

# cv-rewriter

## What This Skill Does

Génère un CV ATS-optimisé en adaptant `CV_BASE.md` aux exigences spécifiques de l'offre cible.
Sélectionne les sections pertinentes selon le profil détecté (ai_engineer / ai_builder / full_stack / mlops).

## When To Use It

Brief entrant `[CV-REWRITER SKILL]` depuis le Hunter bridge (`POST /rewrite-cv`), après validation Telegram de Patricia.

## Context Files Required

Lire avant de générer :
- `CV_BASE.md` — source de vérité du CV
- `SKILLS_MASTER.md` — liste des compétences et alias
- `GITHUB_REPOS.md` — projets GitHub à injecter
- `USER.md` — préférences, langue, prétentions salariales

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
  "profile_tag": "ai_engineer|ai_builder|full_stack|mlops"
}
```

`profile_tag` est optionnel. Si absent, le déduire depuis les skills_found et la description.

## Output Format

CV complet en Markdown. Structure :

1. En-tête (nom, titre adapté à l'offre, contact, liens)
2. Résumé professionnel (3-4 phrases, adapté à l'offre, mots-clés de l'offre insérés naturellement)
3. Compétences techniques (sections ordonnées par pertinence pour l'offre)
4. Expériences professionnelles (sections taguées profil_tag prioritaires)
5. Projets GitHub pertinents (depuis GITHUB_REPOS.md)
6. Formation

## ATS Rules

- Pas de tableaux, pas d'images, pas de colonnes.
- Longueur : 1-2 pages max (environ 600-900 mots).
- Mots-clés de l'offre intégrés naturellement (pas de keyword stuffing).
- Sections dans l'ordre : résumé → compétences → expériences → projets → formation.
- Titre du CV adapté au titre exact de l'offre (ex: "AI Engineer" → pas "Développeur IA").
- Langue : identique à `language` dans l'input.

## Output Wrapper

Encapsuler le CV dans :

```
[CV_START]
...contenu markdown...
[CV_END]
```

Pour parsing propre par n8n avant upload Google Drive.

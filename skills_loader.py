"""Module de chargement de SKILLS_MASTER.md : keywords et table d'alias."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_MASTER_PATH: Final[Path] = Path(
    os.environ.get(
        "SKILLS_MASTER_PATH",
        "/home/thehunter/.openclaw/workspace/the-hunter/SKILLS_MASTER.md",
    )
)


# Sections whose body is prose or metadata, never keywords.
_NON_KEYWORD_SECTIONS: Final[tuple[str, ...]] = ("usage", "alias", "cv match rate", "sources")


def load_keywords(path: Path) -> set[str]:
    """Parse les sections catégories de SKILLS_MASTER.md et retourne tous les tokens.

    Accepte les deux formats rencontrés :
    - repo : sous-sections ``### Catégorie`` avec lignes ``- kw, kw``
    - VPS  : sections ``## CATEGORIE`` avec lignes CSV nues
    """
    text = path.read_text(encoding="utf-8")
    keywords: set[str] = set()
    in_section = False
    in_code_block = False

    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip().lower()
            is_heading_2plus = stripped.startswith("##")
            in_section = is_heading_2plus and not any(
                title.startswith(s) or s in title for s in _NON_KEYWORD_SECTIONS
            )
            continue

        if not in_section or not stripped:
            continue
        if stripped.startswith((">", "|", "---", "*")):
            continue

        content = stripped[2:] if stripped.startswith("- ") else stripped
        for token in content.split(","):
            token = token.strip()
            if token:
                keywords.add(token)

    logger.debug("Loaded %d keywords from %s", len(keywords), path)
    return keywords


def load_aliases(path: Path) -> dict[str, list[str]]:
    """Parse la table d'alias de SKILLS_MASTER.md et retourne le dict terme → synonymes."""
    text = path.read_text(encoding="utf-8")
    aliases: dict[str, list[str]] = {}
    in_table = False
    table_started = False

    for line in text.splitlines():
        stripped = line.strip()

        # "## Alias table" (repo) ou "## ALIAS TABLE" (VPS)
        if stripped.startswith("##") and stripped.lstrip("#").strip().lower().startswith("alias"):
            in_table = True
            continue

        if not in_table:
            continue

        if not stripped.startswith("|"):
            # prose (citations, lignes vides) avant la table : on attend la 1re ligne '|' ;
            # après la table, toute ligne non vide met fin au parsing
            if stripped and table_started:
                break
            continue

        table_started = True
        parts = stripped.split("|")
        if len(parts) < 4:
            continue

        term = parts[1].strip()
        synonyms_raw = parts[2].strip()

        # ligne d'en-tête FR ou EN ("Terme offre" / "Term in offer")
        if not term or term.lower().startswith(("terme", "term ")) or term.startswith("-"):
            continue

        synonyms = [s.strip() for s in synonyms_raw.split(",") if s.strip()]
        if not synonyms:
            continue
        # "Vector DB / Vector Store / Vector database" = variantes d'un même terme
        for variant in term.split(" / "):
            variant = variant.strip()
            if variant:
                aliases[variant] = synonyms

    logger.debug("Loaded %d alias entries from %s", len(aliases), path)
    return aliases


def resolve_alias(term: str, alias_dict: dict[str, list[str]]) -> list[str]:
    """Retourne les synonymes du terme depuis alias_dict, ou [] si absent."""
    return alias_dict.get(term, [])

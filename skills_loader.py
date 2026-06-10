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


def load_keywords(path: Path) -> set[str]:
    """Parse les sections catégories de SKILLS_MASTER.md et retourne tous les tokens."""
    text = path.read_text(encoding="utf-8")
    keywords: set[str] = set()
    in_category = False

    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith("### "):
            in_category = True
            continue

        if stripped.startswith("## ") and not stripped.startswith("### "):
            in_category = False
            continue

        if in_category and stripped.startswith("- "):
            for token in stripped[2:].split(","):
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

    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith("## Alias table"):
            in_table = True
            continue

        if not in_table:
            continue

        if not stripped.startswith("|"):
            if stripped:
                break
            continue

        parts = stripped.split("|")
        if len(parts) < 4:
            continue

        term = parts[1].strip()
        synonyms_raw = parts[2].strip()

        if not term or term == "Terme offre" or term.startswith("-"):
            continue

        synonyms = [s.strip() for s in synonyms_raw.split(",") if s.strip()]
        if term and synonyms:
            aliases[term] = synonyms

    logger.debug("Loaded %d alias entries from %s", len(aliases), path)
    return aliases


def resolve_alias(term: str, alias_dict: dict[str, list[str]]) -> list[str]:
    """Retourne les synonymes du terme depuis alias_dict, ou [] si absent."""
    return alias_dict.get(term, [])

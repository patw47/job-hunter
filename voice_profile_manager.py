#!/home/thehunter/venv/bin/python3
"""
Voice profile feedback manager — Feedback log section of SOUL.md.

Zero LLM calls: pure Python file I/O.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_SOUL_PATH = Path("/home/thehunter/.openclaw/workspace/the-hunter/SOUL.md")
_SECTION_HEADER = "**Feedback log :**"


def get_default_soul_path() -> Path:
    """Return SOUL.md path from SOUL_PATH env var or the production default."""
    return Path(os.getenv("SOUL_PATH", str(_DEFAULT_SOUL_PATH)))


def _split_soul(text: str) -> tuple[str, list[str], str]:
    """Split SOUL.md text at the Feedback log section.

    Returns (prefix, entries, suffix):
    - prefix: text up to and including the header line (with trailing newline)
    - entries: non-empty lines from the section body
    - suffix: text from the next '## ' heading onward, or empty string

    When section is absent returns (original_text, [], "").
    """
    lines = text.splitlines(keepends=True)
    header_idx: int | None = None
    for i, line in enumerate(lines):
        if line.rstrip("\n").rstrip() == _SECTION_HEADER:
            header_idx = i
            break

    if header_idx is None:
        return text, [], ""

    prefix = "".join(lines[: header_idx + 1])

    suffix_start = len(lines)
    body_lines: list[str] = []
    for i in range(header_idx + 1, len(lines)):
        if lines[i].startswith("## "):
            suffix_start = i
            break
        body_lines.append(lines[i].rstrip("\n"))

    entries = [l for l in body_lines if l.strip()]
    suffix = "".join(lines[suffix_start:])
    return prefix, entries, suffix


def add_feedback(soul_path: Path, text: str, date: str) -> None:
    """Append an ADD entry to the Feedback log section of SOUL.md."""
    content = soul_path.read_text(encoding="utf-8")
    new_entry = f'- [{date}] ADD: "{text}"'
    prefix, entries, suffix = _split_soul(content)

    if _SECTION_HEADER not in content:
        sep = "\n" if content and not content.endswith("\n\n") else ""
        soul_path.write_text(
            content + sep + f"\n{_SECTION_HEADER}\n{new_entry}\n",
            encoding="utf-8",
        )
    else:
        entries.append(new_entry)
        body = "\n".join(entries) + "\n"
        soul_path.write_text(prefix + body + suffix, encoding="utf-8")

    logger.info("Feedback added: %s", text[:80])


def remove_feedback(soul_path: Path, text: str) -> None:
    """Remove first entry containing *text* from the Feedback log section."""
    content = soul_path.read_text(encoding="utf-8")
    prefix, entries, suffix = _split_soul(content)

    if not entries:
        return

    new_entries: list[str] = []
    removed = False
    for entry in entries:
        if not removed and text in entry:
            removed = True
        else:
            new_entries.append(entry)

    if not removed:
        return

    body = ("\n".join(new_entries) + "\n") if new_entries else ""
    soul_path.write_text(prefix + body + suffix, encoding="utf-8")
    logger.info("Feedback removed (matched %r)", text[:80])


def list_feedback(soul_path: Path) -> str:
    """Return the Feedback log entries as a newline-joined string, or '' if absent."""
    content = soul_path.read_text(encoding="utf-8")
    _, entries, _ = _split_soul(content)
    return "\n".join(entries)

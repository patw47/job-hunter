"""Markdown → DOCX / PDF conversion for generated documents.

Markdown stays the internal source format; recruiters get DOCX (editable)
and PDF (ready to send). Conversion is best-effort: any failure returns
False and the caller falls back to delivering the .md file.

Requires: pandoc (+ weasyprint as its PDF engine) — installed via apt.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_STYLE_PATH = Path(__file__).parent / "cv-style.css"
_TIMEOUT = 120


def _run_pandoc(args: list[str]) -> bool:
    try:
        result = subprocess.run(
            ["pandoc", *args], capture_output=True, text=True, timeout=_TIMEOUT
        )
    except FileNotFoundError:
        logger.error("pandoc not installed")
        return False
    except subprocess.TimeoutExpired:
        logger.error("pandoc timed out")
        return False
    if result.returncode != 0:
        logger.error("pandoc failed: %s", result.stderr[:300])
        return False
    return True


def md_to_docx(md_path: str | Path, out_path: str | Path) -> bool:
    """Convert a Markdown document to DOCX. Returns True on success."""
    return _run_pandoc([str(md_path), "-o", str(out_path), "--from", "markdown"])


def md_to_pdf(md_path: str | Path, out_path: str | Path) -> bool:
    """Convert a Markdown document to a styled A4 PDF via weasyprint."""
    args = [
        str(md_path), "-o", str(out_path),
        "--from", "markdown",
        "--pdf-engine=weasyprint",
        "--metadata", "title= ",
    ]
    if _STYLE_PATH.exists():
        args += ["--css", str(_STYLE_PATH)]
    return _run_pandoc(args)


def convert_document(md_path: str | Path) -> list[Path]:
    """Produce sibling .docx and .pdf next to md_path; returns created files."""
    md_path = Path(md_path)
    produced: list[Path] = []
    for suffix, fn in ((".docx", md_to_docx), (".pdf", md_to_pdf)):
        out = md_path.with_suffix(suffix)
        if fn(md_path, out) and out.exists():
            produced.append(out)
    return produced

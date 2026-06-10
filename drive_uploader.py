"""
Drive Uploader — Google Drive storage and MATCHES update for generated documents.

Public API:
  build_yaml_header(metadata) -> str
  prepend_yaml_header(content, metadata) -> str
  build_telegram_notification(...) -> dict
  DriveUploader.upload_document(content, filename, year_month) -> str
  DriveUploader.update_matches(job_id, cv_url, lm_url) -> bool
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

CREDS_PATH: Final[str] = os.environ.get("GOOGLE_CREDS_PATH", "/opt/apps/job-hunter/credentials.json")
SPREADSHEET_NAME: Final[str] = "job-hunter-tracker"
MATCHES_TAB: Final[str] = "MATCHES"
DRIVE_ROOT_FOLDER: Final[str] = "job-hunter — Applications"

# MATCHES column indices (1-based, matching test_sheets.py HEADERS order)
_COL_STATUS: Final[int] = 11   # "status"
_COL_CV_DRIVE: Final[int] = 12  # "cv_drive_link"
_COL_LM_DRIVE: Final[int] = 13  # "letter_drive_link"

STATUS_GENERATED: Final[str] = "Généré"
_FOLDER_MIME: Final[str] = "application/vnd.google-apps.folder"
_DRIVE_SCOPES: Final[list[str]] = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def _yaml_escape(value: str) -> str:
    """Quote YAML value if it contains characters that need escaping."""
    if not value:
        return value
    if ":" in value or '"' in value or value.startswith("#"):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def build_yaml_header(metadata: dict) -> str:
    """Build YAML frontmatter block from ordered metadata dict."""
    lines = ["---"]
    for key, value in metadata.items():
        v = _yaml_escape(str(value) if value is not None else "")
        lines.append(f"{key}: {v}")
    lines.append("---")
    return "\n".join(lines)


def prepend_yaml_header(content: str, metadata: dict) -> str:
    """Prepend YAML frontmatter to Markdown content."""
    header = build_yaml_header(metadata)
    return f"{header}\n\n{content}"


def build_telegram_notification(
    company: str,
    position: str,
    cv_url: str,
    lm_url: str,
    offer_url: str,
    job_id: str,
    application_type: str,
    form_questions_count: int = 0,
) -> dict:
    """Build Telegram notification dict {text, reply_markup} for post-generation."""
    text_lines = [
        f"✅ Documents prêts : {company} — {position}",
        f"📄 CV : {cv_url}",
        f"📝 Lettre : {lm_url}",
    ]
    if application_type == "easy_apply":
        text_lines.append("👉 Easy Apply détecté — je postule pour toi ?")
        keyboard = [[
            {"text": "✅ Oui, postule !", "callback_data": f"apply:yes:{job_id}"},
            {"text": "❌ Non, je le fais moi", "callback_data": f"apply:no:{job_id}"},
        ]]
    else:
        if form_questions_count > 0:
            text_lines.append(
                f"📝 {form_questions_count} questions détectées → [Voir réponses suggérées]"
            )
        keyboard = [[
            {"text": "🌐 Ouvrir l'offre", "url": offer_url},
            {"text": "📤 Marquer Envoyé", "callback_data": f"mark_sent:{job_id}"},
        ]]
    return {
        "text": "\n".join(text_lines),
        "reply_markup": {"inline_keyboard": keyboard},
    }


class DriveUploader:
    """Upload Markdown documents to Google Drive and update MATCHES spreadsheet."""

    def __init__(self, creds_path: str | Path = CREDS_PATH) -> None:
        self._creds_path = str(creds_path)
        self._drive = None
        self._gc = None

    def _get_drive(self):
        """Lazy-init Google Drive v3 service."""
        if self._drive is None:
            from google.oauth2.service_account import Credentials  # noqa: PLC0415
            from googleapiclient.discovery import build  # noqa: PLC0415
            creds = Credentials.from_service_account_file(self._creds_path, scopes=_DRIVE_SCOPES)
            self._drive = build("drive", "v3", credentials=creds)
        return self._drive

    def _get_gc(self):
        """Lazy-init gspread client."""
        if self._gc is None:
            import gspread  # noqa: PLC0415
            self._gc = gspread.service_account(filename=self._creds_path)
        return self._gc

    def _get_or_create_folder(self, parent_id: str | None, name: str) -> str:
        """Return id of existing folder or create it. parent_id=None means Drive root."""
        drive = self._get_drive()
        parent_clause = f"and '{parent_id}' in parents" if parent_id else "and 'root' in parents"
        q = f"name='{name}' and mimeType='{_FOLDER_MIME}' {parent_clause} and trashed=false"
        result = drive.files().list(q=q, fields="files(id,name)").execute()
        files = result.get("files", [])
        if files:
            return files[0]["id"]
        body: dict = {"name": name, "mimeType": _FOLDER_MIME}
        if parent_id:
            body["parents"] = [parent_id]
        created = drive.files().create(body=body, fields="id").execute()
        return created["id"]

    def _ensure_monthly_folder(self, year_month: str) -> str:
        """Get or create job-hunter — Applications/{year_month}/ and return folder id."""
        root_id = self._get_or_create_folder(None, DRIVE_ROOT_FOLDER)
        return self._get_or_create_folder(root_id, year_month)

    def upload_document(self, content: str, filename: str, year_month: str) -> str:
        """Upload Markdown content to Drive, make public readable, return webViewLink."""
        from googleapiclient.http import MediaInMemoryUpload  # noqa: PLC0415
        drive = self._get_drive()
        folder_id = self._ensure_monthly_folder(year_month)
        media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/markdown", resumable=False)
        file_meta: dict = {"name": filename, "parents": [folder_id]}
        file_obj = drive.files().create(body=file_meta, media_body=media, fields="id").execute()
        file_id = file_obj["id"]
        drive.permissions().create(
            fileId=file_id, body={"type": "anyone", "role": "reader"}
        ).execute()
        link_data = drive.files().get(fileId=file_id, fields="webViewLink").execute()
        url: str = link_data.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")
        logger.info("Uploaded %s to Drive: %s", filename, url)
        return url

    def update_matches(self, job_id: str, cv_url: str | None, lm_url: str | None) -> bool:
        """Update cv_drive_link, letter_drive_link, status in MATCHES for job_id."""
        try:
            gc = self._get_gc()
            ws = gc.open(SPREADSHEET_NAME).worksheet(MATCHES_TAB)
            cell = ws.find(job_id)
            row = cell.row
            if cv_url:
                ws.update_cell(row, _COL_CV_DRIVE, cv_url)
            if lm_url:
                ws.update_cell(row, _COL_LM_DRIVE, lm_url)
            ws.update_cell(row, _COL_STATUS, STATUS_GENERATED)
            logger.info("Updated MATCHES row %d for job_id=%s", row, job_id)
            return True
        except Exception as exc:
            logger.warning("Failed to update MATCHES for job_id=%s: %s", job_id, exc)
            return False

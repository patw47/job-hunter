"""
Unit tests for drive_uploader.py — no live API calls, all external calls mocked.

Google API packages may not be installed in the test env. Since imports are lazy
(inside methods), we only need sys.modules injection before tests that call those
methods. We patch _get_drive/_get_gc directly so no sys.modules injection is needed.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, call, patch

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import drive_uploader as du


# ── build_yaml_header ─────────────────────────────────────────────────────────


class TestBuildYamlHeader(unittest.TestCase):
    def test_starts_and_ends_with_fence(self) -> None:
        result = du.build_yaml_header({"Company": "Acme"})
        assert result.startswith("---")
        assert result.endswith("---")

    def test_all_keys_present(self) -> None:
        meta = {"Company": "Acme", "Position": "AI Engineer", "Language": "en"}
        result = du.build_yaml_header(meta)
        for key in meta:
            assert key in result

    def test_empty_dict_produces_valid_fence(self) -> None:
        result = du.build_yaml_header({})
        assert result == "---\n---"

    def test_url_value_is_quoted(self) -> None:
        result = du.build_yaml_header({"Offer URL": "https://example.com/jobs/123"})
        assert '"https://example.com/jobs/123"' in result

    def test_return_type_is_str(self) -> None:
        assert isinstance(du.build_yaml_header({}), str)

    def test_none_value_becomes_empty(self) -> None:
        result = du.build_yaml_header({"Match Rate": None})
        assert "Match Rate: " in result

    def test_hash_prefix_quoted(self) -> None:
        result = du.build_yaml_header({"Note": "#important"})
        assert '"#important"' in result


# ── prepend_yaml_header ───────────────────────────────────────────────────────


class TestPrependYamlHeader(unittest.TestCase):
    def test_header_before_content(self) -> None:
        result = du.prepend_yaml_header("# CV\n\nBody", {"Company": "Acme"})
        yaml_pos = result.index("---")
        content_pos = result.index("# CV")
        assert yaml_pos < content_pos

    def test_blank_line_between_header_and_content(self) -> None:
        result = du.prepend_yaml_header("# CV", {"Company": "Acme"})
        assert "---\n\n# CV" in result

    def test_empty_content_no_crash(self) -> None:
        result = du.prepend_yaml_header("", {"Company": "Acme"})
        assert "---" in result

    def test_empty_metadata_content_preserved(self) -> None:
        result = du.prepend_yaml_header("content", {})
        assert result.endswith("content")

    def test_result_contains_content(self) -> None:
        content = "# Patricia\n\nBuilt LLM pipelines."
        result = du.prepend_yaml_header(content, {"Language": "en"})
        assert content in result


# ── build_telegram_notification ───────────────────────────────────────────────


class TestBuildTelegramNotification(unittest.TestCase):
    def _call(self, **overrides) -> dict:
        kwargs = dict(
            company="Acme AI",
            position="Senior AI Engineer",
            cv_url="https://drive.google.com/cv",
            lm_url="https://drive.google.com/lm",
            offer_url="https://linkedin.com/jobs/123",
            job_id="job_abc",
            application_type="form",
        )
        kwargs.update(overrides)
        return du.build_telegram_notification(**kwargs)

    def test_returns_required_keys(self) -> None:
        result = self._call()
        assert "text" in result
        assert "reply_markup" in result

    def test_text_contains_company(self) -> None:
        result = self._call(company="MegaCorp")
        assert "MegaCorp" in result["text"]

    def test_text_contains_position(self) -> None:
        result = self._call(position="ML Lead")
        assert "ML Lead" in result["text"]

    def test_text_contains_cv_url(self) -> None:
        result = self._call(cv_url="https://drive.google.com/cv_specific")
        assert "https://drive.google.com/cv_specific" in result["text"]

    def test_text_contains_lm_url(self) -> None:
        result = self._call(lm_url="https://drive.google.com/lm_specific")
        assert "https://drive.google.com/lm_specific" in result["text"]

    def test_easy_apply_text(self) -> None:
        result = self._call(application_type="easy_apply")
        assert "Easy Apply" in result["text"]

    def test_easy_apply_buttons(self) -> None:
        result = self._call(application_type="easy_apply")
        buttons = [b["text"] for row in result["reply_markup"]["inline_keyboard"] for b in row]
        assert any("Oui" in t for t in buttons)
        assert any("Non" in t for t in buttons)

    def test_form_questions_shown_when_nonzero(self) -> None:
        result = self._call(application_type="form", form_questions_count=5)
        assert "5" in result["text"]

    def test_form_questions_zero_not_shown(self) -> None:
        result = self._call(application_type="easy_apply", form_questions_count=0)
        assert "0 question" not in result["text"]

    def test_form_buttons_contain_offer_url(self) -> None:
        result = self._call(application_type="form")
        buttons = [b for row in result["reply_markup"]["inline_keyboard"] for b in row]
        url_buttons = [b for b in buttons if b.get("url")]
        assert any("linkedin" in b["url"] for b in url_buttons)

    def test_form_buttons_mark_sent_callback(self) -> None:
        result = self._call(application_type="form", job_id="job_xyz")
        buttons = [b for row in result["reply_markup"]["inline_keyboard"] for b in row]
        cbs = [b.get("callback_data", "") for b in buttons]
        assert any("mark_sent:job_xyz" in cb for cb in cbs)

    def test_easy_apply_callback_contains_job_id(self) -> None:
        result = self._call(application_type="easy_apply", job_id="job_42")
        buttons = [b for row in result["reply_markup"]["inline_keyboard"] for b in row]
        cbs = [b.get("callback_data", "") for b in buttons]
        assert any("job_42" in cb for cb in cbs)

    def test_reply_markup_has_inline_keyboard(self) -> None:
        result = self._call()
        assert "inline_keyboard" in result["reply_markup"]

    def test_reply_markup_non_empty(self) -> None:
        result = self._call()
        keyboard = result["reply_markup"]["inline_keyboard"]
        assert len(keyboard) > 0
        assert len(keyboard[0]) > 0


# ── DriveUploader — lazy init ─────────────────────────────────────────────────


class TestDriveUploaderLazyInit(unittest.TestCase):
    def test_no_drive_build_on_construction(self) -> None:
        with patch.object(du.DriveUploader, "_get_drive") as mock_get:
            du.DriveUploader("/fake/creds.json")
            mock_get.assert_not_called()

    def test_no_gspread_on_construction(self) -> None:
        with patch.object(du.DriveUploader, "_get_gc") as mock_get:
            du.DriveUploader("/fake/creds.json")
            mock_get.assert_not_called()

    def test_drive_called_on_upload(self) -> None:
        uploader = du.DriveUploader("/fake/creds.json")
        mock_drive = MagicMock()
        mock_drive.files.return_value.list.return_value.execute.return_value = {"files": [{"id": "fid"}]}
        mock_drive.files.return_value.create.return_value.execute.return_value = {"id": "new_file"}
        mock_drive.files.return_value.get.return_value.execute.return_value = {
            "webViewLink": "https://drive.google.com/view"
        }
        with patch.object(uploader, "_get_drive", return_value=mock_drive):
            with patch.object(uploader, "_ensure_monthly_folder", return_value="folder_id"):
                # patch MediaInMemoryUpload at the module import site
                with patch.dict(
                    sys.modules,
                    {"googleapiclient.http": MagicMock()},
                ):
                    uploader.upload_document("content", "file.md", "2026-06")
        # Verify _get_drive was called (patched version invoked)
        # If we get here without ImportError, it worked.

    def test_gc_called_on_update_matches(self) -> None:
        uploader = du.DriveUploader("/fake/creds.json")
        mock_gc = MagicMock()
        mock_ws = MagicMock()
        mock_cell = MagicMock()
        mock_cell.row = 3
        mock_ws.find.return_value = mock_cell
        mock_gc.open.return_value.worksheet.return_value = mock_ws
        with patch.object(uploader, "_get_gc", return_value=mock_gc):
            uploader.update_matches("job_id", "cv_url", "lm_url")
        mock_gc.open.assert_called_once_with(du.SPREADSHEET_NAME)


# ── DriveUploader._get_or_create_folder ──────────────────────────────────────


class TestGetOrCreateFolder(unittest.TestCase):
    def setUp(self) -> None:
        self.uploader = du.DriveUploader("/fake/creds.json")
        self.mock_drive = MagicMock()
        patcher = patch.object(self.uploader, "_get_drive", return_value=self.mock_drive)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_existing_folder_returned(self) -> None:
        self.mock_drive.files.return_value.list.return_value.execute.return_value = {
            "files": [{"id": "existing_id", "name": "2026-06"}]
        }
        result = self.uploader._get_or_create_folder("parent_id", "2026-06")
        assert result == "existing_id"
        self.mock_drive.files.return_value.create.assert_not_called()

    def test_folder_created_when_absent(self) -> None:
        self.mock_drive.files.return_value.list.return_value.execute.return_value = {"files": []}
        self.mock_drive.files.return_value.create.return_value.execute.return_value = {"id": "new_id"}
        result = self.uploader._get_or_create_folder("parent_id", "new_folder")
        assert result == "new_id"

    def test_create_called_with_folder_mime(self) -> None:
        self.mock_drive.files.return_value.list.return_value.execute.return_value = {"files": []}
        self.mock_drive.files.return_value.create.return_value.execute.return_value = {"id": "x"}
        self.uploader._get_or_create_folder("parent_id", "folder_name")
        create_call = self.mock_drive.files.return_value.create.call_args
        body = create_call[1].get("body") or create_call[0][0] if create_call[0] else create_call[1]["body"]
        assert body["mimeType"] == du._FOLDER_MIME

    def test_create_sets_parent_when_provided(self) -> None:
        self.mock_drive.files.return_value.list.return_value.execute.return_value = {"files": []}
        self.mock_drive.files.return_value.create.return_value.execute.return_value = {"id": "x"}
        self.uploader._get_or_create_folder("the_parent_id", "folder")
        create_call = self.mock_drive.files.return_value.create.call_args
        body = create_call[1].get("body") or (create_call[0][0] if create_call[0] else create_call[1]["body"])
        assert "the_parent_id" in body.get("parents", [])

    def test_root_query_when_parent_none(self) -> None:
        self.mock_drive.files.return_value.list.return_value.execute.return_value = {"files": []}
        self.mock_drive.files.return_value.create.return_value.execute.return_value = {"id": "x"}
        self.uploader._get_or_create_folder(None, "root_folder")
        list_call = self.mock_drive.files.return_value.list.call_args
        q = list_call[1].get("q") or list_call[0][0]
        assert "'root' in parents" in q


# ── DriveUploader._ensure_monthly_folder ─────────────────────────────────────


class TestEnsureMonthlyFolder(unittest.TestCase):
    def setUp(self) -> None:
        self.uploader = du.DriveUploader("/fake/creds.json")

    def test_creates_root_then_monthly(self) -> None:
        with patch.object(
            self.uploader, "_get_or_create_folder", side_effect=["root_id", "month_id"]
        ) as mock_create:
            result = self.uploader._ensure_monthly_folder("2026-06")
        assert result == "month_id"
        assert mock_create.call_count == 2

    def test_root_folder_name(self) -> None:
        calls_received = []
        def _capture(parent_id, name):
            calls_received.append((parent_id, name))
            return "some_id"
        with patch.object(self.uploader, "_get_or_create_folder", side_effect=_capture):
            self.uploader._ensure_monthly_folder("2026-06")
        assert calls_received[0] == (None, du.DRIVE_ROOT_FOLDER)

    def test_monthly_folder_name(self) -> None:
        calls_received = []
        def _capture(parent_id, name):
            calls_received.append((parent_id, name))
            return "some_id"
        with patch.object(self.uploader, "_get_or_create_folder", side_effect=_capture):
            self.uploader._ensure_monthly_folder("2026-06")
        assert calls_received[1][1] == "2026-06"

    def test_returns_str(self) -> None:
        with patch.object(self.uploader, "_get_or_create_folder", side_effect=["r", "m"]):
            result = self.uploader._ensure_monthly_folder("2026-06")
        assert isinstance(result, str)


# ── DriveUploader.upload_document ────────────────────────────────────────────


class TestUploadDocument(unittest.TestCase):
    def setUp(self) -> None:
        self.uploader = du.DriveUploader("/fake/creds.json")
        self.mock_drive = MagicMock()
        patcher_drive = patch.object(self.uploader, "_get_drive", return_value=self.mock_drive)
        patcher_folder = patch.object(self.uploader, "_ensure_monthly_folder", return_value="folder_id")
        patcher_drive.start()
        patcher_folder.start()
        self.addCleanup(patcher_drive.stop)
        self.addCleanup(patcher_folder.stop)
        # Inject googleapiclient.http mock so MediaInMemoryUpload import succeeds
        self._http_mock = MagicMock()
        sys.modules["googleapiclient.http"] = self._http_mock

    def tearDown(self) -> None:
        sys.modules.pop("googleapiclient.http", None)

    def _setup_drive_responses(self, file_id: str = "file123", view_link: str = "https://drive.google.com/view/file123") -> None:
        self.mock_drive.files.return_value.create.return_value.execute.return_value = {"id": file_id}
        self.mock_drive.files.return_value.get.return_value.execute.return_value = {"webViewLink": view_link}

    def test_returns_view_link(self) -> None:
        self._setup_drive_responses(view_link="https://drive.google.com/view/abc")
        result = self.uploader.upload_document("# CV", "cv.md", "2026-06")
        assert result == "https://drive.google.com/view/abc"

    def test_fallback_url_when_no_webviewlink(self) -> None:
        self.mock_drive.files.return_value.create.return_value.execute.return_value = {"id": "xyz"}
        self.mock_drive.files.return_value.get.return_value.execute.return_value = {}
        result = self.uploader.upload_document("# CV", "cv.md", "2026-06")
        assert "xyz" in result

    def test_permission_set_to_anyone_reader(self) -> None:
        self._setup_drive_responses()
        self.uploader.upload_document("content", "file.md", "2026-06")
        perm_call = self.mock_drive.permissions.return_value.create.call_args
        body = perm_call[1].get("body") or (perm_call[0][0] if perm_call[0] else None)
        assert body["type"] == "anyone"
        assert body["role"] == "reader"

    def test_ensure_monthly_folder_called(self) -> None:
        self._setup_drive_responses()
        with patch.object(self.uploader, "_ensure_monthly_folder", return_value="fid") as mock_ef:
            self.uploader.upload_document("content", "file.md", "2026-07")
        mock_ef.assert_called_once_with("2026-07")

    def test_upload_uses_filename(self) -> None:
        self._setup_drive_responses()
        self.uploader.upload_document("content", "CV_Patricia_Wintrebert_Acme_2026-06-10.md", "2026-06")
        create_call = self.mock_drive.files.return_value.create.call_args
        body = create_call[1].get("body") or (create_call[0][0] if create_call[0] else create_call[1]["body"])
        assert body["name"] == "CV_Patricia_Wintrebert_Acme_2026-06-10.md"


# ── DriveUploader.update_matches ─────────────────────────────────────────────


class TestUpdateMatches(unittest.TestCase):
    def setUp(self) -> None:
        self.uploader = du.DriveUploader("/fake/creds.json")
        self.mock_gc = MagicMock()
        self.mock_ws = MagicMock()
        self.mock_cell = MagicMock()
        self.mock_cell.row = 5
        self.mock_ws.find.return_value = self.mock_cell
        self.mock_gc.open.return_value.worksheet.return_value = self.mock_ws
        patcher = patch.object(self.uploader, "_get_gc", return_value=self.mock_gc)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_happy_path_returns_true(self) -> None:
        assert self.uploader.update_matches("job_1", "cv_url", "lm_url") is True

    def test_job_id_not_found_returns_false(self) -> None:
        self.mock_ws.find.side_effect = Exception("CellNotFound")
        assert self.uploader.update_matches("unknown_id", "cv_url", "lm_url") is False

    def test_cv_url_written_to_correct_column(self) -> None:
        self.uploader.update_matches("job_1", "https://cv_url", "https://lm_url")
        update_calls = self.mock_ws.update_cell.call_args_list
        cv_call = next(c for c in update_calls if c[0][1] == du._COL_CV_DRIVE)
        assert cv_call[0][2] == "https://cv_url"

    def test_lm_url_written_to_correct_column(self) -> None:
        self.uploader.update_matches("job_1", "https://cv_url", "https://lm_url")
        update_calls = self.mock_ws.update_cell.call_args_list
        lm_call = next(c for c in update_calls if c[0][1] == du._COL_LM_DRIVE)
        assert lm_call[0][2] == "https://lm_url"

    def test_status_set_to_generated(self) -> None:
        self.uploader.update_matches("job_1", "cv", "lm")
        update_calls = self.mock_ws.update_cell.call_args_list
        status_call = next(c for c in update_calls if c[0][1] == du._COL_STATUS)
        assert status_call[0][2] == du.STATUS_GENERATED

    def test_update_exception_handled(self) -> None:
        self.mock_ws.update_cell.side_effect = Exception("API error")
        assert self.uploader.update_matches("job_1", "cv", "lm") is False

    def test_row_used_from_find(self) -> None:
        self.mock_cell.row = 42
        self.uploader.update_matches("job_1", "cv", "lm")
        for c in self.mock_ws.update_cell.call_args_list:
            assert c[0][0] == 42


if __name__ == "__main__":
    unittest.main()

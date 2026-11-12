"""Streamlit UI tests (streamlit.testing AppTest). Login and Google Sheets are faked."""
import sys
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = str(Path(__file__).resolve().parent)


def signed_in_app(root):
    """Runs streamlit_app.main() with a fake user and an in-memory sheet."""
    import sys

    if root not in sys.path:
        sys.path.insert(0, root)
    import streamlit_app as app

    store = getattr(app, "_test_store", None)
    if store is None:
        store = app._test_store = {"writes": 0, "records": [
            {"id": "d1", "kind": "docent", "title": "빛의 층위", "schedule": "2026. 9. 1. – 11. 30.", "space": "한강아트센터",
             "detail": "빛을 다룬 전시\n\n장소: 서울 중구\n누리집: https://example.com", "posterUrl": "",
             "sessions": [{"performedAt": "2026-10-03T14:00", "participants": 15, "adults": "", "teens": "", "children": "",
                           "questions": "", "notes": "", "impression": "예전 기록"}]},
            {"id": "d2", "kind": "docent", "title": "색의 정원", "schedule": "", "space": "", "detail": "", "posterUrl": "",
             "sessions": []},
            {"id": "v1", "kind": "visit", "title": "그림자 극장", "space": "시립미술관", "visitDate": "2026-09-20",
             "detail": "", "listened": True, "evaluation": "친절한 설명", "impression": "인상적", "posterUrl": ""},
        ]}

    class FakeSheets:
        def __init__(self, token):
            pass

        def find_or_create(self):
            return "SHEET"

        def read_records(self, sheet_id):
            records = store["records"]
            return ([dict(r) for r in records if r["kind"] == "docent"], [dict(r) for r in records if r["kind"] == "visit"])

        def write_records(self, sheet_id, records):
            store["records"] = records
            store["writes"] += 1

    app.JournalSheets = FakeSheets
    app.current_user = lambda: {"email": "tester@example.com", "token": "token"}
    app.search_client = lambda: None
    app.main()


def no_permission_app(root):
    import sys

    if root not in sys.path:
        sys.path.insert(0, root)
    import streamlit_app as app
    from google_sheets import PermissionMissing

    class NoDriveScope:
        def __init__(self, token):
            pass

        def find_or_create(self):
            raise PermissionMissing("Google 드라이브 파일 권한이 없습니다. 다시 로그인해 주세요.")

    app.JournalSheets = NoDriveScope
    app.current_user = lambda: {"email": "tester@example.com", "token": "token"}
    app.search_client = lambda: None
    app.main()


def signed_out_app(root, configured):
    import sys

    if root not in sys.path:
        sys.path.insert(0, root)
    import streamlit_app as app

    app.current_user = lambda: None
    app.auth_configured = lambda: configured
    app.main()


def markdown_text(at):
    return "\n".join(element.value for element in at.markdown)


class StreamlitAppTests(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("streamlit_app", None)  # fresh fake sheet per test

    def store(self):
        return sys.modules["streamlit_app"]._test_store

    def app(self):
        at = AppTest.from_function(signed_in_app, args=(ROOT,), default_timeout=20)
        return at.run()

    def test_signed_out_page(self):
        # Independent of whether this PC has .streamlit/secrets.toml.
        without = AppTest.from_function(signed_out_app, args=(ROOT, False), default_timeout=20).run()
        self.assertFalse(without.exception)
        self.assertIn("전시와 함께한 순간을", markdown_text(without))
        self.assertTrue(any("[auth]" in error.value for error in without.error))
        sys.modules.pop("streamlit_app", None)
        configured = AppTest.from_function(signed_out_app, args=(ROOT, True), default_timeout=20).run()
        self.assertFalse(configured.error)
        self.assertTrue(configured.button(key="login-main"))
        self.assertTrue(configured.button(key="login-side"))

    def test_missing_drive_permission_offers_login_again(self):
        at = AppTest.from_function(no_permission_app, args=(ROOT,), default_timeout=20).run()
        self.assertFalse(at.exception)
        self.assertTrue(any("드라이브 파일 권한" in warning.value for warning in at.warning))
        self.assertTrue(any(button.label == "로그아웃하고 다시 로그인" for button in at.button))

    def test_lists_records_and_shows_selected_detail(self):
        at = self.app()
        self.assertFalse(at.exception)
        text = markdown_text(at)
        self.assertIn("빛의 층위", text)
        self.assertIn("15명 (구분 없음)", text)      # old session without breakdown
        self.assertIn("https://example.com", text)    # "누리집: …" became a fact cell
        self.assertIn("도슨트 수행 전시", text)
        at.button(key="pick-d2").click().run()
        self.assertIn("아직 수행 회차가 없습니다", markdown_text(at))
        at.radio(key="tab").set_value("visit").run()
        self.assertIn("친절한 설명", markdown_text(at))

    def test_add_record_through_dialog(self):
        at = self.app()
        at.button(key="add-docent").click().run()
        at.text_input(key="rf_title").set_value("새 전시").run()
        at.text_input(key="rf_space").set_value("북서울미술관").run()
        at.button(key="rf_save").click().run()
        self.assertFalse(at.exception)
        titles = [r["title"] for r in self.store()["records"] if r["kind"] == "docent"]
        self.assertIn("새 전시", titles)
        self.assertIn("새 전시", markdown_text(at))     # newly saved record is selected

    def test_empty_title_is_rejected(self):
        at = self.app()
        at.button(key="add-docent").click().run()
        at.button(key="rf_save").click().run()
        self.assertTrue(any("전시 제목" in error.value for error in at.error))
        self.assertEqual(self.store()["writes"], 0)

    def test_add_session_with_audience_breakdown(self):
        at = self.app()
        at.button(key="add-session").click().run()
        at.number_input(key="sf_adults").set_value(12).run()
        at.number_input(key="sf_children").set_value(3).run()
        self.assertTrue(any("합계 15명" in caption.value for caption in at.caption))
        at.text_area(key="sf_questions").set_value("작가 의도는?").run()
        at.button(key="sf_save").click().run()
        self.assertFalse(at.exception)
        record = next(r for r in self.store()["records"] if r["id"] == "d1")
        self.assertEqual(len(record["sessions"]), 2)
        new = next(s for s in record["sessions"] if s["questions"] == "작가 의도는?")
        self.assertEqual((new["participants"], new["adults"], new["teens"], new["children"]), (15, 12, "", 3))

    def test_editing_old_session_without_breakdown_keeps_total(self):
        at = self.app()
        at.button(key="edit-session-0").click().run()
        at.text_area(key="sf_notes").set_value("메모").run()
        at.button(key="sf_save").click().run()
        session = next(r for r in self.store()["records"] if r["id"] == "d1")["sessions"][0]
        self.assertEqual((session["participants"], session["notes"]), (15, "메모"))

    def test_cancel_in_confirmation_keeps_everything(self):
        at = self.app()
        at.button(key="del-record").click().run()
        self.assertTrue(any("삭제할까요" in m.value for m in at.markdown))  # asks first
        self.assertEqual(self.store()["writes"], 0)                       # nothing deleted yet
        at.button(key="cf_cancel").click().run()
        at.button(key="del-session-0").click().run()
        self.assertTrue(any("1회차" in m.value and "삭제할까요" in m.value for m in at.markdown))
        at.button(key="cf_cancel").click().run()
        self.assertEqual(self.store()["writes"], 0)
        record = next(r for r in self.store()["records"] if r["id"] == "d1")
        self.assertEqual(len(record["sessions"]), 1)

    def test_delete_session_after_confirmation(self):
        at = self.app()
        at.button(key="del-session-0").click().run()
        at.button(key="cf_delete").click().run()
        record = next(r for r in self.store()["records"] if r["id"] == "d1")
        self.assertEqual(record["sessions"], [])

    def test_delete_record_after_confirmation(self):
        at = self.app()
        at.button(key="pick-d2").click().run()
        at.button(key="del-record").click().run()
        at.button(key="cf_delete").click().run()
        self.assertNotIn("d2", [r["id"] for r in self.store()["records"]])



class DeploymentRequirementsTests(unittest.TestCase):
    def test_login_dependencies_are_listed(self):
        # Streamlit Cloud installs only requirements.txt. st.login needs Authlib and
        # httpx (Streamlit's "auth" extra); without httpx the login button returns
        # "Internal server error".
        listed = {line.split("==")[0].strip().lower()
                  for line in (Path(ROOT) / "requirements.txt").read_text(encoding="utf-8").splitlines()
                  if line.strip() and not line.startswith("#")}
        self.assertTrue({"streamlit", "authlib", "httpx"} <= listed)


if __name__ == "__main__":
    unittest.main()

"""Session photos: image preparation, validation, Drive calls, web API and Streamlit UI."""
import io
import json
import sys
import unittest
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image
from streamlit.testing.v1 import AppTest

import google_sheets
import journal_core as core
import server

ROOT = str(Path(__file__).resolve().parent)
PHOTO_A, PHOTO_B = "1AbCdEfGhIjKlMn", "2OpQrStUvWxYz0"


def image_bytes(size=(3000, 2000), fmt="PNG", mode="RGBA", exif=None):
    out = io.BytesIO()
    image = Image.new(mode, size, (200, 120, 40, 255) if mode == "RGBA" else (200, 120, 40))
    if exif is not None:
        image.save(out, fmt, exif=exif)
    else:
        image.save(out, fmt)
    return out.getvalue()


class PreparePhotoTests(unittest.TestCase):
    def test_large_photo_is_shrunk_to_jpeg_without_exif(self):
        exif = Image.Exif()
        exif[0x010F] = "PhoneMaker"          # Make
        exif[0x8825] = {2: (37, 30, 0)}       # GPS info
        data = core.prepare_photo(image_bytes(fmt="JPEG", mode="RGB", exif=exif))
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.format, "JPEG")
            self.assertEqual(max(result.size), core.MAX_SIDE)
            self.assertFalse(result.getexif())  # location and camera data removed

    def test_rejects_non_images_and_oversized_files(self):
        for bad in (b"", b"not an image", b"\x00" * (core.MAX_UPLOAD_BYTES + 1)):
            with self.assertRaises(core.PhotoError):
                core.prepare_photo(bad)

    def test_png_with_transparency_becomes_jpeg(self):
        with Image.open(io.BytesIO(core.prepare_photo(image_bytes(size=(400, 300))))) as result:
            self.assertEqual((result.format, result.mode, result.size), ("JPEG", "RGB", (400, 300)))


class PhotoValidationTests(unittest.TestCase):
    def record(self, photos):
        return [{"id": "d1", "kind": "docent", "title": "전시", "sessions": [{"performedAt": "2026-11-12T10:00", "photos": photos}]}]

    def test_photo_ids_are_kept_and_deduplicated(self):
        clean = core.validate_records(self.record([PHOTO_A, PHOTO_B, PHOTO_A]))
        self.assertEqual(clean[0]["sessions"][0]["photos"], [PHOTO_A, PHOTO_B])

    def test_rejects_bad_ids_and_too_many_photos(self):
        for photos in (["../etc/passwd"], ["short"], "notalist", [f"{PHOTO_A}{i}" for i in range(core.MAX_PHOTOS + 1)]):
            with self.assertRaises(ValueError):
                core.validate_records(self.record(photos))

    def test_session_without_photos_gets_empty_list(self):
        clean = core.validate_records([{"id": "d1", "kind": "docent", "title": "전시", "sessions": [{"performedAt": ""}]}])
        self.assertEqual(clean[0]["sessions"][0]["photos"], [])


class DrivePhotoTests(unittest.TestCase):
    def test_upload_sends_multipart_with_folder_and_returns_id(self):
        client = google_sheets.JournalSheets("TOKEN")
        with patch.object(google_sheets, "_request", return_value=b'{"id": "NEWFILEID123"}') as sent:
            file_id = client.upload_photo("FOLDER", "a.jpg", b"JPEGDATA")
        self.assertEqual(file_id, "NEWFILEID123")
        url, method, token, body, headers, params = sent.call_args.args[:6]
        self.assertEqual((url, method, token), (google_sheets.UPLOAD_URL, "POST", "TOKEN"))
        self.assertIn(b'"parents": ["FOLDER"]', body)
        self.assertIn(b"JPEGDATA", body)
        self.assertTrue(headers["Content-Type"].startswith("multipart/related; boundary="))
        self.assertEqual(params["uploadType"], "multipart")

    def test_trash_moves_file_to_drive_trash(self):
        client = google_sheets.JournalSheets("TOKEN")
        with patch.object(client, "_api", return_value={}) as api:
            client.trash_photo(PHOTO_A)
        self.assertEqual(api.call_args.kwargs["method"], "PATCH")
        self.assertEqual(api.call_args.kwargs["data"], {"trashed": True})
        self.assertTrue(api.call_args.args[0].endswith(PHOTO_A))


class FakeDrive:
    uploads, trashed = [], []

    def __init__(self, token):
        pass

    def find_or_create_photo_folder(self):
        return "FOLDER"

    def upload_photo(self, folder, name, data):
        FakeDrive.uploads.append((folder, name, data))
        return "UPLOADEDID0001"

    def download_photo(self, file_id):
        return b"JPEG:" + file_id.encode()

    def trash_photo(self, file_id):
        FakeDrive.trashed.append(file_id)


class WebPhotoApiTests(unittest.TestCase):
    def setUp(self):
        FakeDrive.uploads, FakeDrive.trashed = [], []
        self.http = server.LocalhostServer(("127.0.0.1", 0), server.JournalHandler)
        self.thread = Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)

    def call(self, path, method="GET", body=None, signed_in=True, headers=None):
        session = {"google": {"email": "a@example.com", "access_token": "t", "expires_at": 9e12}}
        with patch.object(server, "_session_for", return_value=("sid", session if signed_in else None)), \
                patch.object(server, "JournalSheets", FakeDrive):
            request = Request(self.base + path, data=body, method=method, headers=headers or {})
            try:
                with urlopen(request) as response:
                    return response.status, response.read(), response.headers
            except HTTPError as error:
                payload = error.read()
                error.close()
                return error.code, payload, error.headers

    def test_upload_shrinks_and_stores_photo(self):
        status, body, _ = self.call("/api/photos", "POST", image_bytes(size=(2400, 1200)), headers={"Content-Type": "image/png"})
        self.assertEqual((status, json.loads(body)), (200, {"id": "UPLOADEDID0001"}))
        folder, name, data = FakeDrive.uploads[0]
        self.assertEqual(folder, "FOLDER")
        self.assertTrue(name.endswith(".jpg"))
        with Image.open(io.BytesIO(data)) as stored:
            self.assertEqual((stored.format, stored.size), ("JPEG", (1600, 800)))

    def test_upload_rejects_bad_files_and_anonymous_users(self):
        self.assertEqual(self.call("/api/photos", "POST", b"not an image")[0], 400)
        self.assertEqual(self.call("/api/photos", "POST", image_bytes(), signed_in=False)[0], 401)

    def test_download_is_private_and_validates_ids(self):
        status, body, headers = self.call(f"/api/photos/{PHOTO_A}")
        self.assertEqual((status, body), (200, b"JPEG:" + PHOTO_A.encode()))
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertIn("private", headers["Cache-Control"])
        self.assertEqual(self.call("/api/photos/..%2Fsecret")[0], 404)
        self.assertEqual(self.call(f"/api/photos/{PHOTO_A}", signed_in=False)[0], 401)

    def test_delete_moves_photo_to_trash(self):
        status, _, _ = self.call(f"/api/photos/{PHOTO_B}", "DELETE")
        self.assertEqual((status, FakeDrive.trashed), (200, [PHOTO_B]))
        self.assertEqual(self.call(f"/api/photos/{PHOTO_B}", "DELETE", headers={"Origin": "https://evil.example"})[0], 403)


def app_with_photos(root):
    import sys
    if root not in sys.path:
        sys.path.insert(0, root)
    import streamlit_app as app

    store = getattr(app, "_photo_store", None)
    if store is None:
        store = app._photo_store = {"trashed": [], "records": [
            {"id": "d1", "kind": "docent", "title": "빛의 층위", "schedule": "", "space": "", "detail": "", "posterUrl": "",
             "sessions": [{"performedAt": "2026-10-03T14:00", "participants": 3, "adults": 3, "teens": "", "children": "",
                           "questions": "", "notes": "", "impression": "", "photos": ["1AbCdEfGhIjKlMn", "2OpQrStUvWxYz0"]}]},
        ]}
    from PIL import Image
    import io as _io
    buffer = _io.BytesIO()
    Image.new("RGB", (40, 30), (90, 90, 90)).save(buffer, "JPEG")
    jpeg = buffer.getvalue()

    class FakeSheets:
        def __init__(self, token): pass
        def find_or_create(self): return "S"
        def read_records(self, sid):
            return [dict(r) for r in store["records"]], []
        def write_records(self, sid, records): store["records"] = records
        def download_photo(self, file_id): return jpeg
        def trash_photo(self, file_id): store["trashed"].append(file_id)

    app.JournalSheets = FakeSheets
    app.current_user = lambda: {"email": "tester@example.com", "token": "token"}
    app.search_client = lambda: None
    app.main()


def open_session(at, record_id="d1", index=0):
    """Sessions start collapsed; open one like a click on its summary."""
    at.session_state[f"session-open-{record_id}-{index}"] = True
    return at.run()


class StreamlitPhotoTests(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("streamlit_app", None)

    def store(self):
        return sys.modules["streamlit_app"]._photo_store

    def test_detail_shows_session_photos(self):
        at = open_session(AppTest.from_function(app_with_photos, args=(ROOT,), default_timeout=20).run())
        self.assertFalse(at.exception)
        self.assertIn("📷 2장", at.expander[0].label)        # photo count in the collapsed summary
        self.assertEqual(len(at.get("image")), 1)          # one gallery
        self.assertEqual(len(at.get("image")[0].proto.imgs), 2)

    def test_removing_photo_needs_confirmation_and_trashes_on_save(self):
        at = open_session(AppTest.from_function(app_with_photos, args=(ROOT,), default_timeout=20).run())
        at.button(key="edit-session-0").click().run()
        at.button(key=f"sf-photo-remove-{PHOTO_A}").click().run()
        self.assertTrue(any("이 사진을 뺄까요" in w.value for w in at.warning))
        at.button(key="sf-photo-keep").click().run()                 # cancel keeps the photo
        at.button(key=f"sf-photo-remove-{PHOTO_A}").click().run()
        at.button(key="sf-photo-confirm").click().run()
        self.assertEqual(self.store()["trashed"], [])               # nothing trashed before saving
        at.button(key="sf_save").click().run()
        self.assertFalse(at.exception)
        session = self.store()["records"][0]["sessions"][0]
        self.assertEqual(session["photos"], [PHOTO_B])
        self.assertEqual(self.store()["trashed"], [PHOTO_A])

    def test_deleting_session_trashes_its_photos(self):
        at = open_session(AppTest.from_function(app_with_photos, args=(ROOT,), default_timeout=20).run())
        at.button(key="del-session-0").click().run()
        self.assertTrue(any("사진 2장" in m.value for m in at.markdown))
        at.button(key="cf_delete").click().run()
        self.assertEqual(sorted(self.store()["trashed"]), sorted([PHOTO_A, PHOTO_B]))


if __name__ == "__main__":
    unittest.main()

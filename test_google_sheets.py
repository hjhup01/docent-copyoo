import json
import unittest
from unittest.mock import patch
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

import google_sheets
import server


class JournalSheetsTests(unittest.TestCase):
    def setUp(self):
        self.client = google_sheets.JournalSheets("TOKEN")

    def test_docent_and_visit_records_round_trip(self):
        records = [
            {
                "id": "d1", "kind": "docent", "title": "빛의 층위",
                "schedule": "2026. 9. - 11.", "space": "한강아트센터",
                "detail": "작품과 빛에 대한 전시", "posterUrl": "https://www.culture.go.kr/upload/p.jpg",
                "sessions": [
                    {"performedAt": "2026-11-12T10:30", "participants": 12,
                     "adults": 7, "teens": 3, "children": 2,
                     "questions": "작가의 의도는?", "notes": "질의응답 진행",
                     "impression": "즐거운 대화"},
                    # recorded before the breakdown existed: total only
                    {"performedAt": "2026-11-13T14:00", "participants": 8,
                     "adults": "", "teens": "", "children": "",
                     "questions": "", "notes": "", "impression": "다시 진행"},
                ],
            },
            {
                "id": "v1", "kind": "visit", "title": "색의 정원",
                "space": "시립미술관", "visitDate": "2026-11-10",
                "detail": "회화전", "listened": True,
                "evaluation": "작품 배경을 잘 설명해 주었어요.",
                "impression": "색감이 인상적이었어요.",
                "posterUrl": "",
            },
        ]
        writes = []

        def capture_write(spreadsheet_id, tables):
            writes.extend(tables)

        with patch.object(self.client, "_replace_sheets", side_effect=capture_write):
            self.client.write_records("S1", records)

        self.assertEqual([title for title, _, _ in writes], [google_sheets.DOCENT_SHEET, google_sheets.VISIT_SHEET])
        docent_rows = writes[0][2]
        visit_rows = writes[1][2]
        self.assertEqual(len(docent_rows), 2)
        self.assertEqual([row[5] for row in docent_rows], [1, 2])
        self.assertEqual(docent_rows[0][7], 12)
        self.assertEqual(docent_rows[0][11:14], [7, 3, 2])
        self.assertEqual(visit_rows[0][5], "예")
        self.assertEqual(
            google_sheets._decode_docent([list(google_sheets.DOCENT_HEADERS), *docent_rows]),
            [records[0]],
        )
        self.assertEqual(
            google_sheets._decode_visits([list(google_sheets.VISIT_HEADERS), *visit_rows]),
            [records[1]],
        )

    def test_docent_without_sessions_keeps_exhibition_record(self):
        row = ["d2", "전시", "", "공간", "상세", "", "", "", "", "", ""]
        self.assertEqual(
            google_sheets._decode_docent([list(google_sheets.DOCENT_HEADERS), row]),
            [{
                "id": "d2", "kind": "docent", "title": "전시", "schedule": "",
                "space": "공간", "detail": "상세", "posterUrl": "", "sessions": [],
            }],
        )

    def test_rows_from_sheet_before_breakdown_columns_still_decode(self):
        old_headers = list(google_sheets.DOCENT_HEADERS[:11])
        row = ["d4", "전시", "", "", "", 1, "2026-11-12T10:00", 15, "", "", "감상"]
        session = google_sheets._decode_docent([old_headers, row])[0]["sessions"][0]
        self.assertEqual(session["participants"], 15)
        self.assertEqual((session["adults"], session["teens"], session["children"]), ("", "", ""))

    def test_empty_sheet_is_initialized_with_headers(self):
        metadata = {"sheets": [
            {"properties": {"sheetId": 1, "title": google_sheets.DOCENT_SHEET}},
            {"properties": {"sheetId": 2, "title": google_sheets.VISIT_SHEET}},
        ]}
        with patch.object(self.client, "_api", return_value=metadata), \
                patch.object(self.client, "_read_values", side_effect=[[], []]) as read_values, \
                patch.object(self.client, "_write_rows") as write_rows:
            self.client._ensure_sheets("S1")
        self.assertEqual(read_values.call_count, 2)
        self.assertEqual(write_rows.call_count, 2)
        self.assertEqual(write_rows.call_args_list[0].args[2], google_sheets.DOCENT_HEADERS)
        self.assertEqual(write_rows.call_args_list[1].args[2], google_sheets.VISIT_HEADERS)

    def test_replace_sheets_writes_in_one_request_and_blanks_stale_rows(self):
        calls = []

        def fake_api(url, method="GET", data=None, params=None):
            calls.append((url, method, data, params))
            if url.endswith(":batchGet"):
                # Sheet previously held a header plus 3 rows; visit sheet was empty.
                return {"valueRanges": [{"values": [["h"], ["a"], ["b"], ["c"]]}, {}]}
            return {}

        with patch.object(self.client, "_api", side_effect=fake_api):
            self.client._replace_sheets("S1", (
                (google_sheets.DOCENT_SHEET, ("A", "B"), [["1", "x"]]),
                (google_sheets.VISIT_SHEET, ("A",), []),
            ))

        self.assertEqual(len(calls), 2)
        self.assertNotIn(":clear", "".join(url for url, *_ in calls))
        url, method, data, _ = calls[1]
        self.assertTrue(url.endswith(":batchUpdate"))
        self.assertEqual(method, "POST")
        self.assertEqual(data["valueInputOption"], "RAW")
        docent, visit = data["data"]
        self.assertEqual(docent["range"], f"'{google_sheets.DOCENT_SHEET}'!A1:B4")
        self.assertEqual(docent["values"], [["A", "B"], ["1", "x"], ["", ""], ["", ""]])
        self.assertEqual(visit["values"], [["A"]])

    def test_hand_edited_numeric_cells_are_read_as_text(self):
        row = ["d3", 2026, "", "", "", 1, "2026-11-12T10:00", 12.0, "", "", ""]
        record = google_sheets._decode_docent([list(google_sheets.DOCENT_HEADERS), row])[0]
        self.assertEqual(record["title"], "2026")
        self.assertEqual(record["sessions"][0]["participants"], 12)

    def test_oauth_url_requests_only_login_and_drive_file_scopes(self):
        url = google_sheets.make_auth_url("client-id", "http://localhost:8501", "signed-state")
        self.assertIn("openid+email+", url)
        self.assertIn("drive.file", url)
        self.assertNotIn("drive", url.replace("drive.file", ""))
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A8501", url)


class GoogleErrorMessageTests(unittest.TestCase):
    def _error(self, code, body):
        import io
        return HTTPError("https://sheets.googleapis.com", code, "err", {}, io.BytesIO(json.dumps(body).encode()))

    def test_missing_drive_scope_asks_to_log_in_again(self):
        body = {"error": {"code": 403, "status": "PERMISSION_DENIED",
                          "message": "Request had insufficient authentication scopes.",
                          "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}
        error = google_sheets._http_error(self._error(403, body))
        self.assertIsInstance(error, google_sheets.PermissionMissing)
        self.assertIn("다시 로그인", str(error))

    def test_disabled_api_is_named(self):
        body = {"error": {"code": 403, "status": "PERMISSION_DENIED",
                          "message": "Google Sheets API has not been used in project 123 before or it is disabled.",
                          "details": [{"reason": "SERVICE_DISABLED"}]}}
        self.assertIn("Google Sheets API", str(google_sheets._http_error(self._error(403, body))))

    def test_workspace_admin_block_is_explained(self):
        body = {"error": {"code": 403, "message": "The domain administrators have disabled Drive apps.",
                          "errors": [{"reason": "domainPolicy"}]}}
        self.assertIn("관리자 정책", str(google_sheets._http_error(self._error(403, body))))

    def test_expired_token_and_unknown_errors(self):
        self.assertIsInstance(google_sheets._http_error(self._error(401, {})), google_sheets.AuthExpired)
        unknown = google_sheets._http_error(self._error(500, {"error": {"status": "INTERNAL"}}))
        self.assertIn("HTTP 500 INTERNAL", str(unknown))
        self.assertNotIsInstance(unknown, google_sheets.PermissionMissing)


class RequestValidationTests(unittest.TestCase):
    def test_valid_records_are_normalized(self):
        records = [{
            "id": "d1", "kind": "docent", "title": "  전시  ",
            "sessions": [{"performedAt": "2026-11-12T10:00", "participants": 3}],
        }]
        normalized = server._validate_records(records)
        self.assertEqual(normalized[0]["title"], "전시")
        self.assertEqual(normalized[0]["sessions"][0]["participants"], 3)

    def test_total_is_derived_from_audience_breakdown(self):
        normalized = server._validate_records([{
            "id": "d1", "kind": "docent", "title": "전시",
            "sessions": [
                {"performedAt": "2026-11-12T10:00", "participants": 999, "adults": 4, "children": 2},
                {"performedAt": "2026-11-13T10:00", "participants": 15},
            ],
        }])
        first, legacy = normalized[0]["sessions"]
        self.assertEqual(first["participants"], 6)
        self.assertEqual((first["adults"], first["teens"], first["children"]), (4, "", 2))
        self.assertEqual(legacy["participants"], 15)

    def test_poster_url_must_be_http(self):
        ok = server._validate_records([{"id": "v1", "kind": "visit", "title": "전시",
                                        "posterUrl": " https://example.com/p.jpg "}])
        self.assertEqual(ok[0]["posterUrl"], "https://example.com/p.jpg")
        for bad in ("javascript:alert(1)", "data:image/png;base64,AAA", "//example.com/p.jpg", 5):
            with self.assertRaises(ValueError):
                server._validate_records([{"id": "v1", "kind": "visit", "title": "전시", "posterUrl": bad}])

    def test_rejects_invalid_audience_count(self):
        for bad in (-1, 1.5, "3", True):
            with self.assertRaises(ValueError):
                server._validate_records([{
                    "id": "d1", "kind": "docent", "title": "전시",
                    "sessions": [{"performedAt": "2026-11-12T10:00", "teens": bad}],
                }])

    def test_rejects_missing_title_and_invalid_participants(self):
        with self.assertRaises(ValueError):
            server._validate_records([{"id": "d1", "kind": "docent", "title": " "}])
        with self.assertRaises(ValueError):
            server._validate_records([{
                "id": "d1", "kind": "docent", "title": "전시",
                "sessions": [{"participants": -1}],
            }])

    def test_visit_without_docent_explanation_drops_evaluation(self):
        normalized = server._validate_records([{
            "id": "v1", "kind": "visit", "title": "전시", "listened": False, "evaluation": "남은 평가",
        }])
        self.assertEqual(normalized[0]["evaluation"], "")

    def test_rejects_record_without_kind(self):
        with self.assertRaises(ValueError):
            server._validate_records([{"id": "x", "title": "전시"}])

    def _start_server(self):
        http_server = server.LocalhostServer(("127.0.0.1", 0), server.JournalHandler)
        thread = Thread(target=http_server.serve_forever, daemon=True)
        thread.start()

        def stop():
            http_server.shutdown()
            http_server.server_close()
            thread.join(timeout=2)

        self.addCleanup(stop)
        return f"http://127.0.0.1:{http_server.server_port}"

    def test_unexpected_error_returns_json_500(self):
        base_url = self._start_server()
        with patch.object(server, "_session_for", side_effect=RuntimeError("boom")):
            with self.assertRaises(HTTPError) as raised:
                urlopen(f"{base_url}/api/session")
        self.assertEqual(raised.exception.code, 500)
        self.assertIn("error", json.loads(raised.exception.read()))
        raised.exception.close()

    def test_local_server_serves_app_and_gates_records_without_login(self):
        base_url = self._start_server()
        with urlopen(f"{base_url}/") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
            self.assertIn("나의 전시 기록".encode("utf-8"), response.read())
        for asset in ("styles.css", "app.js"):
            with urlopen(f"{base_url}/{asset}") as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read())
        with urlopen(f"{base_url}/api/session") as response:
            self.assertFalse(json.loads(response.read())["authenticated"])
        with self.assertRaises(HTTPError) as raised:
            urlopen(f"{base_url}/api/records")
        self.assertEqual(raised.exception.code, 401)
        raised.exception.close()


if __name__ == "__main__":
    unittest.main()

"""Google OAuth and per-user Google Sheets storage for the exhibition journal."""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
SHEETS_URL = "https://sheets.googleapis.com/v4/spreadsheets"
SPREADSHEET_TITLE = "나의 전시 기록"
DOCENT_SHEET = "Docent 수행기록"
VISIT_SHEET = "관람 기록"
DOCENT_HEADERS = (
    "기록 ID", "전시 제목", "전시 일정", "전시 공간", "전시 상세 정보", "회차",
    "수행 날짜와 시간", "참여 인원", "받은 질문", "특이 사항", "수행 감상",
    # Appended (not inserted) so sheets written by older versions keep their layout.
    # "참여 인원" above holds the total.
    "성인", "청소년", "어린이", "포스터", "사진",
)
# Per-audience participant counts: (session key, column index in DOCENT_HEADERS).
AUDIENCE_COLUMNS = (("adults", 11), ("teens", 12), ("children", 13))
DOCENT_POSTER_COLUMN = 14
DOCENT_PHOTO_COLUMN = 15  # comma-separated Google Drive file IDs of the session's photos
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
PHOTO_FOLDER_TITLE = "나의 전시 기록 사진"
FOLDER_MIME = "application/vnd.google-apps.folder"
UPLOAD_TIMEOUT_SECONDS = 30
VISIT_HEADERS = (
    "기록 ID", "전시 제목", "전시 공간", "방문일", "전시 상세 정보",
    "Docent 설명 여부", "Docent 설명 평가", "나의 감상", "포스터",
)
SCOPES = ("openid", "email", "https://www.googleapis.com/auth/drive.file")
TIMEOUT_SECONDS = 12


class GoogleError(Exception):
    """A safe, user-facing Google API error."""


class AuthExpired(GoogleError):
    """The Google access token is no longer valid."""


class PermissionMissing(GoogleError):
    """The token lacks the Drive scope (e.g. the user unticked it on the consent screen)."""


def _google_error_reason(payload):
    """(status, reasons, message) from a Google API JSON error body; no secrets inside."""
    try:
        error = json.loads(payload or b"{}").get("error") or {}
    except (ValueError, UnicodeDecodeError, AttributeError):
        return "", set(), ""
    if not isinstance(error, dict):
        return "", set(), ""
    reasons = {str(item.get("reason", "")) for item in error.get("errors") or [] if isinstance(item, dict)}
    for detail in error.get("details") or []:
        if isinstance(detail, dict) and detail.get("reason"):
            reasons.add(str(detail["reason"]))
    return str(error.get("status") or ""), reasons, str(error.get("message") or "")[:200]


def _http_error(error):
    """Turn a Google API HTTP error into a specific, user-facing GoogleError."""
    status, reasons, message = _google_error_reason(error.read())
    if error.code == 401:
        return AuthExpired("Google 로그인이 만료되었습니다. 다시 로그인해 주세요.")
    if reasons & {"ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientPermissions"} or "insufficient authentication scopes" in message.lower():
        return PermissionMissing(
            "Google 드라이브 파일 권한이 없습니다. 로그아웃한 뒤 다시 로그인하고, 구글 동의 화면에서 "
            "‘Google Drive 파일’ 항목을 체크해 허용해 주세요. 동의 화면에 그 항목이 보이지 않으면 "
            "Streamlit Secrets의 [auth.client_kwargs] scope에 drive.file이 들어 있는지 확인해 주세요.")
    if reasons & {"domainPolicy"} or "domain administrators" in message.lower():
        return GoogleError(
            "이 구글 계정은 회사·학교 관리자 정책으로 외부 앱의 드라이브 사용이 막혀 있습니다. "
            "개인 Gmail 계정으로 로그인하거나 관리자에게 허용을 요청해 주세요.")
    if reasons & {"SERVICE_DISABLED", "accessNotConfigured"} or "has not been used in project" in message:
        api = "Google Drive API" if "drive" in message.lower() else "Google Sheets API"
        return GoogleError(f"구글 클라우드 프로젝트에서 {api}가 사용 설정되지 않았습니다. 콘솔의 API 라이브러리에서 사용 설정해 주세요.")
    if error.code == 404:
        return GoogleError("Google 시트를 찾지 못했습니다. 페이지를 새로고침해 주세요.")
    if error.code == 429 or "RATE_LIMIT_EXCEEDED" in reasons:
        return GoogleError("Google 시트 요청이 많아 잠시 제한되었습니다. 잠시 후 다시 시도해 주세요.")
    detail = f" (HTTP {error.code}{' ' + status if status else ''})"
    return GoogleError("Google 시트에 접근하지 못했습니다. 권한과 시트 설정을 확인해 주세요." + detail)


def _request(url, method="GET", token=None, body=None, headers=None, params=None, timeout=TIMEOUT_SECONDS):
    """Send a request and return the raw response body (bytes)."""
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    headers = dict(headers or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as error:
        raise _http_error(error) from None
    except (URLError, TimeoutError, OSError):
        raise GoogleError("Google 서비스에 연결하지 못했습니다. 네트워크를 확인한 뒤 다시 시도해 주세요.") from None


def _request_json(url, method="GET", token=None, data=None, params=None):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    payload = _request(url, method, token, body, headers, params)
    try:
        return json.loads(payload) if payload else {}
    except ValueError:
        raise GoogleError("Google 서비스 응답을 해석하지 못했습니다.") from None


def make_auth_url(client_id, redirect_uri, state):
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "access_type": "online",
        "include_granted_scopes": "true",
    })


def exchange_code(code, client_id, client_secret, redirect_uri):
    body = urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    request = Request(TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            token_data = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        raise GoogleError("Google 로그인에 실패했습니다. 다시 시도해 주세요.") from None

    access_token = token_data.get("access_token")
    granted = set((token_data.get("scope") or "").split())
    if not access_token or (granted and SCOPES[2] not in granted):
        raise GoogleError("Google Drive 파일 접근 권한을 허용한 뒤 다시 로그인해 주세요.")
    user = _request_json(USERINFO_URL, token=access_token)
    email = user.get("email")
    if not email:
        raise GoogleError("Google 계정 이메일을 확인하지 못했습니다.")
    expires_in = max(60, int(token_data.get("expires_in", 3600)))
    return {"access_token": access_token, "email": email, "expires_at": time.time() + expires_in}


class JournalSheets:
    def __init__(self, access_token):
        self.access_token = access_token

    def _api(self, url, method="GET", data=None, params=None):
        return _request_json(url, method, self.access_token, data, params)

    def find_or_create(self):
        found = self._api(
            DRIVE_FILES_URL,
            params={
                "q": f"name = '{SPREADSHEET_TITLE}' and mimeType = "
                     "'application/vnd.google-apps.spreadsheet' and trashed = false",
                "spaces": "drive",
                "fields": "files(id,name)",
                "pageSize": 10,
            },
        )
        files = found.get("files") or []
        if files:
            spreadsheet_id = files[0]["id"]
            self._ensure_sheets(spreadsheet_id)
            return spreadsheet_id

        created = self._api(
            SHEETS_URL,
            method="POST",
            data={
                "properties": {"title": SPREADSHEET_TITLE},
                "sheets": [
                    {"properties": {"title": DOCENT_SHEET}},
                    {"properties": {"title": VISIT_SHEET}},
                ],
            },
        )
        spreadsheet_id = created.get("spreadsheetId")
        if not spreadsheet_id:
            raise GoogleError("Google 시트를 만들지 못했습니다.")
        self._write_rows(spreadsheet_id, DOCENT_SHEET, DOCENT_HEADERS, [])
        self._write_rows(spreadsheet_id, VISIT_SHEET, VISIT_HEADERS, [])
        return spreadsheet_id

    def _ensure_sheets(self, spreadsheet_id):
        metadata = self._api(
            f"{SHEETS_URL}/{quote(spreadsheet_id, safe='')}",
            params={"fields": "sheets.properties(sheetId,title)"},
        )
        existing = {
            sheet["properties"]["title"]: sheet["properties"]["sheetId"]
            for sheet in metadata.get("sheets", [])
        }
        missing = [title for title in (DOCENT_SHEET, VISIT_SHEET) if title not in existing]
        if missing:
            self._api(
                f"{SHEETS_URL}/{quote(spreadsheet_id, safe='')}:batchUpdate",
                method="POST",
                data={"requests": [{"addSheet": {"properties": {"title": title}}} for title in missing]},
            )
        for title, headers in ((DOCENT_SHEET, DOCENT_HEADERS), (VISIT_SHEET, VISIT_HEADERS)):
            if title in missing or not self._read_values(spreadsheet_id, title, len(headers)):
                self._write_rows(spreadsheet_id, title, headers, [])

    def read_records(self, spreadsheet_id):
        docent_values = self._read_values(spreadsheet_id, DOCENT_SHEET, len(DOCENT_HEADERS))
        visit_values = self._read_values(spreadsheet_id, VISIT_SHEET, len(VISIT_HEADERS))
        return _decode_docent(docent_values), _decode_visits(visit_values)

    def write_records(self, spreadsheet_id, records):
        docent_rows, visit_rows = build_rows(records)
        self._replace_sheets(spreadsheet_id, (
            (DOCENT_SHEET, DOCENT_HEADERS, docent_rows),
            (VISIT_SHEET, VISIT_HEADERS, visit_rows),
        ))

    def _replace_sheets(self, spreadsheet_id, tables):
        """Overwrite every sheet in a single values:batchUpdate call.

        Rows left over from a previous, longer write are overwritten with blanks
        in the same request instead of being cleared beforehand, so a failed
        request never leaves the journal half-erased.
        """
        base_url = f"{SHEETS_URL}/{quote(spreadsheet_id, safe='')}/values"
        current = self._api(
            f"{base_url}:batchGet",
            params=[("ranges", f"'{title}'!A:A") for title, _, _ in tables]
            + [("majorDimension", "ROWS")],
        )
        existing_counts = [len(item.get("values") or []) for item in current.get("valueRanges", [])]
        data = []
        for index, (title, headers, rows) in enumerate(tables):
            values = [list(headers), *rows]
            existing = existing_counts[index] if index < len(existing_counts) else 0
            values.extend([[""] * len(headers) for _ in range(max(0, existing - len(values)))])
            data.append({"range": f"'{title}'!A1:{_column_name(len(headers))}{len(values)}", "values": values})
        self._api(
            f"{base_url}:batchUpdate",
            method="POST",
            data={"valueInputOption": "RAW", "data": data},
        )

    def _read_values(self, spreadsheet_id, title, column_count):
        last_column = _column_name(column_count)
        sheet_range = quote(f"'{title}'!A1:{last_column}", safe="")
        result = self._api(
            f"{SHEETS_URL}/{quote(spreadsheet_id, safe='')}/values/{sheet_range}",
            params={"valueRenderOption": "UNFORMATTED_VALUE"},
        )
        return result.get("values") or []

    # ---------- session photos: '나의 전시 기록 사진' folder in the user's Drive ----------

    def find_or_create_photo_folder(self):
        found = self._api(DRIVE_FILES_URL, params={
            "q": f"name = '{PHOTO_FOLDER_TITLE}' and mimeType = '{FOLDER_MIME}' and trashed = false",
            "spaces": "drive", "fields": "files(id)", "pageSize": 10,
        })
        files = found.get("files") or []
        if files:
            return files[0]["id"]
        created = self._api(DRIVE_FILES_URL, method="POST", data={"name": PHOTO_FOLDER_TITLE, "mimeType": FOLDER_MIME},
                            params={"fields": "id"})
        if not created.get("id"):
            raise GoogleError("사진 폴더를 만들지 못했습니다.")
        return created["id"]

    def upload_photo(self, folder_id, name, data, mime="image/jpeg"):
        """Upload one photo into the folder (multipart upload) and return its file ID."""
        boundary = "exhibition-journal-photo-boundary"
        meta = json.dumps({"name": name, "parents": [folder_id], "mimeType": mime}).encode("utf-8")
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + meta
                + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode() + data
                + f"\r\n--{boundary}--".encode())
        payload = _request(UPLOAD_URL, "POST", self.access_token, body,
                           {"Content-Type": f"multipart/related; boundary={boundary}", "Accept": "application/json"},
                           {"uploadType": "multipart", "fields": "id"}, UPLOAD_TIMEOUT_SECONDS)
        try:
            file_id = json.loads(payload).get("id")
        except ValueError:
            file_id = None
        if not file_id:
            raise GoogleError("사진을 올리지 못했습니다. 잠시 후 다시 시도해 주세요.")
        return file_id

    def download_photo(self, file_id):
        return _request(f"{DRIVE_FILES_URL}/{quote(file_id, safe='')}", token=self.access_token,
                        params={"alt": "media"}, timeout=UPLOAD_TIMEOUT_SECONDS)

    def trash_photo(self, file_id):
        """Move a photo to the Drive trash (recoverable for 30 days)."""
        self._api(f"{DRIVE_FILES_URL}/{quote(file_id, safe='')}", method="PATCH", data={"trashed": True},
                  params={"fields": "id"})

    def _write_rows(self, spreadsheet_id, title, headers, rows):
        self._replace_sheets(spreadsheet_id, ((title, headers, rows),))


def build_rows(records):
    """Flatten journal records into (docent_rows, visit_rows) sheet rows."""
    docent_rows = []
    visit_rows = []
    for record in records:
        if record["kind"] == "docent":
            sessions = record.get("sessions") or [{}]
            for index, session in enumerate(sessions, 1):
                docent_rows.append([
                    record["id"], record.get("title", ""), record.get("schedule", ""),
                    record.get("space", ""), record.get("detail", ""),
                    index if record.get("sessions") else "",
                    session.get("performedAt", ""), session.get("participants", ""),
                    session.get("questions", ""), session.get("notes", ""),
                    session.get("impression", ""),
                    *(session.get(key, "") for key, _ in AUDIENCE_COLUMNS),
                    record.get("posterUrl", ""),
                    ",".join(session.get("photos") or []),
                ])
        else:
            visit_rows.append([
                record["id"], record.get("title", ""), record.get("space", ""),
                record.get("visitDate", ""), record.get("detail", ""),
                "예" if record.get("listened") else "아니요",
                record.get("evaluation", ""), record.get("impression", ""),
                record.get("posterUrl", ""),
            ])
    return docent_rows, visit_rows


def _column_name(number):
    name = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell(row, index):
    if index >= len(row) or row[index] is None:
        return ""
    value = row[index]
    # Cells edited by hand in Google Sheets may come back as numbers or booleans;
    # the app works with text, so normalize them (12.0 -> "12").
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _decode_docent(values):
    records = {}
    for row in values[1:]:
        if not row or not row[0]:
            continue
        record_id = str(row[0])
        record = records.setdefault(record_id, {
            "id": record_id,
            "kind": "docent",
            "title": _cell(row, 1),
            "schedule": _cell(row, 2),
            "space": _cell(row, 3),
            "detail": _cell(row, 4),
            "posterUrl": _cell(row, DOCENT_POSTER_COLUMN),
            "sessions": [],
        })
        if any(_cell(row, index) for index in range(6, len(DOCENT_HEADERS))):
            session = {
                "performedAt": _cell(row, 6),
                "participants": _count(_cell(row, 7)),
                "questions": _cell(row, 8),
                "notes": _cell(row, 9),
                "impression": _cell(row, 10),
            }
            session["photos"] = [p for p in _cell(row, DOCENT_PHOTO_COLUMN).split(",") if p.strip()]
            for key, index in AUDIENCE_COLUMNS:
                session[key] = _count(_cell(row, index))
            record["sessions"].append(session)
    return list(records.values())


def _count(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return ""


def _decode_visits(values):
    records = []
    for row in values[1:]:
        if not row or not row[0]:
            continue
        records.append({
            "id": str(row[0]),
            "kind": "visit",
            "title": _cell(row, 1),
            "space": _cell(row, 2),
            "visitDate": _cell(row, 3),
            "detail": _cell(row, 4),
            "listened": _cell(row, 5) == "예",
            "evaluation": _cell(row, 6),
            "impression": _cell(row, 7),
            "posterUrl": _cell(row, 8),
        })
    return records

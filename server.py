"""Local web server, Google sign-in callback, and journal API."""
import hashlib
import hmac
import http.cookies
import json
import os
from pathlib import Path
import secrets
import threading
import time
import traceback
from urllib.parse import parse_qs, urlencode, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from google_sheets import AuthExpired, GoogleError, JournalSheets, exchange_code, make_auth_url
from journal_core import validate_records as _validate_records
from public_data import ExhibitionSearch, InvalidSearch, PublicDataError


ROOT = Path(__file__).resolve().parent
COOKIE_NAME = "exhibition_journal_session"
SESSION_MAX_AGE = 3600
MAX_REQUEST_SIZE = 2_000_000
SESSIONS = {}
PENDING_STATES = {}
LOCK = threading.Lock()
EXPIRED_COOKIE = f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data: http: https:; "
    "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def load_env():
    values = {}
    path = ROOT / ".env"
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return {**values, **os.environ}


CONFIG = load_env()
CLIENT_ID = CONFIG.get("GOOGLE_CLIENT_ID", "").strip()
CLIENT_SECRET = CONFIG.get("GOOGLE_CLIENT_SECRET", "").strip()
PORT = int(CONFIG.get("APP_PORT", "8502"))
# CULTURE_API_KEY is the documented name; DATA_GO_KR_SERVICE_KEY is accepted as an alias.
EXHIBITION_SEARCH = ExhibitionSearch(CONFIG.get("CULTURE_API_KEY") or CONFIG.get("DATA_GO_KR_SERVICE_KEY", ""))
REDIRECT_URI = CONFIG.get("GOOGLE_REDIRECT_URI", f"http://localhost:{PORT}").strip()
if not CLIENT_ID or not CLIENT_SECRET:
    raise RuntimeError("MyPJT/.env에 GOOGLE_CLIENT_ID와 GOOGLE_CLIENT_SECRET을 설정해 주세요.")


def _sign_state(timestamp, nonce):
    message = f"{timestamp}.{nonce}".encode("utf-8")
    signature = hmac.new(CLIENT_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"{timestamp}.{nonce}.{signature}"


def _new_state():
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(24)
    state = _sign_state(timestamp, nonce)
    with LOCK:
        PENDING_STATES[state] = time.time()
        for old_state, created_at in list(PENDING_STATES.items()):
            if time.time() - created_at > 1800:
                PENDING_STATES.pop(old_state, None)
    return state


def _consume_state(state):
    try:
        timestamp, nonce, signature = state.split(".", 2)
        age = time.time() - int(timestamp)
    except (AttributeError, TypeError, ValueError):
        return False
    expected = _sign_state(timestamp, nonce).rsplit(".", 1)[1]
    with LOCK:
        issued_at = PENDING_STATES.pop(state, None)
    return bool(issued_at and hmac.compare_digest(signature, expected) and 0 <= age <= 1800)


def _session_for(handler):
    cookie = http.cookies.SimpleCookie()
    try:
        cookie.load(handler.headers.get("Cookie", ""))
        morsel = cookie.get(COOKIE_NAME)
    except http.cookies.CookieError:
        morsel = None
    if not morsel:
        return None, None
    session_id = morsel.value
    with LOCK:
        session = SESSIONS.get(session_id)
        if session and session.get("expires_at", 0) <= time.time():
            SESSIONS.pop(session_id, None)
            session = None
    if session and session.get("google", {}).get("expires_at", 0) <= time.time():
        with LOCK:
            SESSIONS.pop(session_id, None)
        return session_id, None
    return session_id, session


class JournalHandler(BaseHTTPRequestHandler):
    server_version = "ExhibitionJournal/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")

    def _send(self, status, body=b"", content_type="application/json; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "same-origin")
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if headers:
            for name, value in headers:
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, value, headers=None):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send(status, body, headers=headers)

    def _redirect(self, location, headers=None):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        if headers:
            for name, value in headers:
                self.send_header(name, value)
        self.end_headers()

    def _body_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("요청 크기를 확인할 수 없습니다.") from None
        if length <= 0 or length > MAX_REQUEST_SIZE:
            raise ValueError("요청 데이터 크기가 올바르지 않습니다.")
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("요청 데이터 형식이 올바르지 않습니다.") from None

    def _check_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        request_host = self.headers.get("Host", "")
        try:
            origin_host = urlsplit(origin).netloc.lower()
        except ValueError:
            return False
        return origin_host == request_host.lower()

    def _handle_safely(self, handler):
        """Turn unexpected errors into a JSON 500 instead of dropping the connection."""
        try:
            handler()
        except Exception:  # noqa: BLE001 - last-resort guard for the request thread
            traceback.print_exc()
            try:
                self._json(500, {"error": "서버에서 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."})
            except OSError:
                pass

    def _spreadsheet_client(self, session):
        client = JournalSheets(session["google"]["access_token"])
        spreadsheet_id = session.get("spreadsheet_id")
        if not spreadsheet_id:
            spreadsheet_id = client.find_or_create()
            with LOCK:
                session["spreadsheet_id"] = spreadsheet_id
        return client, spreadsheet_id

    def do_GET(self):
        self._handle_safely(self._do_get)

    def _do_get(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/auth/google":
            state = _new_state()
            self._redirect(make_auth_url(CLIENT_ID, REDIRECT_URI, state))
            return
        if parsed.path == "/auth/logout":
            session_id, _ = _session_for(self)
            if session_id:
                with LOCK:
                    SESSIONS.pop(session_id, None)
            self._redirect("/", headers=[("Set-Cookie", EXPIRED_COOKIE)])
            return
        if parsed.path == "/":
            query = parse_qs(parsed.query)
            if "code" in query or "error" in query:
                self._oauth_callback(query)
                return
            self._serve_index()
            return
        assets = {
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        if parsed.path in assets:
            filename, content_type = assets[parsed.path]
            try:
                body = (ROOT / filename).read_bytes()
            except OSError:
                self._send(500, b"Application asset is unavailable", "text/plain; charset=utf-8")
                return
            self._send(200, body, content_type)
            return
        if parsed.path == "/api/session":
            _, session = _session_for(self)
            self._json(200, {
                "authenticated": bool(session),
                "email": session["google"]["email"] if session else "",
                "exhibitionSearch": EXHIBITION_SEARCH.configured,
            })
            return
        if parsed.path == "/api/records":
            _, session = _session_for(self)
            if not session:
                self._json(401, {"error": "Google에 로그인해 주세요."})
                return
            try:
                client, spreadsheet_id = self._spreadsheet_client(session)
                docent, visits = client.read_records(spreadsheet_id)
                self._json(200, {"records": docent + visits})
            except AuthExpired as error:
                self._expire_session()
                self._json(401, {"error": str(error)})
            except GoogleError as error:
                self._json(502, {"error": str(error)})
            return
        if parsed.path in ("/api/exhibitions", "/api/exhibitions/detail"):
            self._exhibition_lookup(parsed)
            return
        if parsed.path == "/favicon.ico":
            self._send(204)
            return
        self._send(404, b"Not found", "text/plain; charset=utf-8")

    def _exhibition_lookup(self, parsed):
        _, session = _session_for(self)
        if not session:
            self._json(401, {"error": "Google에 로그인해 주세요."})
            return
        if not EXHIBITION_SEARCH.configured:
            self._json(200, {"configured": False, "items": [], "total": 0, "hasMore": False})
            return
        query = parse_qs(parsed.query)
        try:
            if parsed.path.endswith("/detail"):
                result = {"item": EXHIBITION_SEARCH.detail((query.get("seq") or [""])[0])}
            else:
                try:
                    page = int((query.get("page") or ["1"])[0])
                except ValueError:
                    page = 1
                result = EXHIBITION_SEARCH.search(
                    (query.get("q") or [""])[0], page,
                    status=(query.get("status") or ["running"])[0],
                    sort=(query.get("sort") or ["latest"])[0],
                )
        except InvalidSearch as error:
            self._json(400, {"configured": True, "error": str(error)})
            return
        except PublicDataError as error:
            self._json(502, {"configured": True, "error": str(error)})
            return
        self._json(200, {"configured": True, **result})

    def _oauth_callback(self, query):
        state = (query.get("state") or [""])[0]
        if not _consume_state(state):
            self._redirect("/?login_error=state")
            return
        error = (query.get("error") or [""])[0]
        if error:
            self._redirect("/?login_error=cancelled")
            return
        code = (query.get("code") or [""])[0]
        try:
            google = exchange_code(code, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)
        except GoogleError:
            self._redirect("/?login_error=google")
            return
        session_id = secrets.token_urlsafe(32)
        session = {"google": google, "expires_at": min(google["expires_at"], time.time() + SESSION_MAX_AGE)}
        with LOCK:
            SESSIONS[session_id] = session
        cookie = (
            f"{COOKIE_NAME}={session_id}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={SESSION_MAX_AGE}"
        )
        self._redirect("/", headers=[("Set-Cookie", cookie)])

    def _serve_index(self):
        try:
            body = (ROOT / "index.html").read_bytes()
        except OSError:
            self._send(500, b"Application page is unavailable", "text/plain; charset=utf-8")
            return
        self._send(200, body, "text/html; charset=utf-8")

    def do_POST(self):
        self._handle_safely(self._do_post)

    def _do_post(self):
        parsed = urlsplit(self.path)
        if not self._check_origin():
            self._json(403, {"error": "출처가 확인되지 않은 요청입니다."})
            return
        session_id, session = _session_for(self)
        if parsed.path == "/api/logout":
            if session_id:
                with LOCK:
                    SESSIONS.pop(session_id, None)
            self._json(200, {"ok": True}, headers=[("Set-Cookie", EXPIRED_COOKIE)])
            return
        if parsed.path != "/api/records":
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        if not session:
            self._json(401, {"error": "Google에 로그인해 주세요."})
            return
        try:
            payload = self._body_json()
            records = _validate_records(payload.get("records") if isinstance(payload, dict) else None)
        except ValueError as error:
            self._json(400, {"error": str(error)})
            return
        try:
            client, spreadsheet_id = self._spreadsheet_client(session)
            client.write_records(spreadsheet_id, records)
            self._json(200, {"ok": True})
        except AuthExpired as error:
            self._expire_session()
            self._json(401, {"error": str(error)})
        except GoogleError as error:
            self._json(502, {"error": str(error)})

    def _expire_session(self):
        session_id, _ = _session_for(self)
        if session_id:
            with LOCK:
                SESSIONS.pop(session_id, None)

    def do_PUT(self):
        self.do_POST()


class LocalhostServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    print(f"나의 전시 기록: http://localhost:{PORT}")
    LocalhostServer(("127.0.0.1", PORT), JournalHandler).serve_forever()

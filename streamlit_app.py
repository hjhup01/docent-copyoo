"""나의 전시 기록 — Streamlit 버전 (Streamlit Cloud: Main file path = streamlit_app.py).

The plain web version (server.py + index.html) keeps working; both share
google_sheets.py (storage), public_data.py (exhibition search) and journal_core.py (rules).

Login uses Streamlit's built-in st.login() like miniPJT: the [auth] section of
.streamlit/secrets.toml (or the Cloud "Secrets" box) holds the Google OAuth client,
and `expose_tokens = "access"` hands us the access token for Google Sheets.
"""
import html
import os
import uuid
from datetime import date, datetime, time
from pathlib import Path

import streamlit as st

import journal_core as core
from google_sheets import AuthExpired, GoogleError, JournalSheets, PermissionMissing
from public_data import ExhibitionSearch, PublicDataError

ROOT = Path(__file__).resolve().parent
PANE_HEIGHT = 720
KINDS = {"docent": "🎙️ Docent 수행기록", "visit": "📒 관람한 전시"}
LIST_TITLES = {"docent": "Docent 수행 기록", "visit": "관람한 전시 기록"}
STATUS_OPTIONS = {"running": "전시중", "upcoming": "전시예정", "past": "지난전시", "all": "전체"}
SORT_OPTIONS = {"latest": "최신순", "ending": "끝나는 날짜순", "title": "이름순"}
BADGES = {"running": "전시중", "upcoming": "전시예정", "past": "지난전시"}



# ---------- settings ----------

def _dotenv():
    values = {}
    path = ROOT / ".env"
    if path.exists():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    return values


def setting(name):
    """st.secrets (Cloud) first, then environment variables, then the local .env file."""
    try:
        value = st.secrets.get(name)
    except Exception:  # no secrets.toml
        value = None
    return str(value or os.environ.get(name) or _dotenv().get(name) or "").strip()


def auth_configured():
    try:
        auth = st.secrets.get("auth")
        return bool(auth and auth.get("client_id"))
    except Exception:
        return False


@st.cache_resource
def exhibition_search(key):
    # One instance per key so its 10-minute result cache is shared across sessions.
    return ExhibitionSearch(key)


def search_client():
    key = setting("CULTURE_API_KEY") or setting("DATA_GO_KR_SERVICE_KEY")
    return exhibition_search(key) if key else None


def current_user():
    """{'email', 'token'} when signed in with a usable access token, else None."""
    try:
        if not st.user.is_logged_in:
            return None
        tokens = st.user.tokens
        token = tokens.get("access") if hasattr(tokens, "get") else tokens["access"]
    except Exception:
        return None
    if not token:
        return None
    return {"email": st.user.get("email") or "", "token": token}


# ---------- styles (miniPJT "다크 갤러리") ----------

GOLD, GOLD_HOVER, GOLD_TEXT, ON_GOLD = "#c9a45c", "#dcb96f", "#d8b46a", "#16171b"
MUTED, LINE, SURFACE, BG, RAISED, INPUT = "#9a9890", "#2e2f36", "#1f2026", "#16171b", "#1b1c21", "#121317"

PAGE_CSS = f"""
<style>
.brand {{ font-size: 1.6rem !important; font-weight: 700; color: #ecebe6; margin: 0 !important; line-height: 1.3 !important; }}
.tagline {{ color: {MUTED}; font-size: .85rem !important; margin: .3rem 0 .6rem !important; }}
.side-card {{ background: {RAISED}; border: 1px solid #2a2b31; border-top: 3px solid {GOLD}; border-radius: 9px;
    padding: 14px 14px 12px; box-shadow: 0 6px 18px rgba(0,0,0,.35); }}
.side-card-title {{ font-size: .78rem; font-weight: 700; color: {GOLD_TEXT}; letter-spacing: .08em; margin-bottom: 10px; }}
.stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
.stat {{ background: {INPUT}; border: 1px solid #2a2b31; border-radius: 8px; padding: 8px 10px; }}
.stat b {{ display: block; font-size: 1.5rem; font-weight: 600; line-height: 1.2; }}
.stat span {{ color: {MUTED}; font-size: .74rem; white-space: nowrap; }}
.storage-note {{ color: {MUTED}; font-size: .78rem !important; line-height: 1.5 !important; margin: 10px 0 0 !important; }}
.avatar {{ width: 28px; height: 28px; border-radius: 50%; background: {GOLD}; color: {ON_GOLD}; display: inline-grid;
    place-items: center; font-weight: 700; font-size: .8rem; margin-right: 8px; vertical-align: middle; }}
.account-email, .account-email a {{ color: #cfcdc6 !important; font-size: .85rem; vertical-align: middle; text-decoration: none !important; pointer-events: none; }}
[data-testid="stSidebarUserContent"] {{ min-height: 100%; display: flex; flex-direction: column; }}
[data-testid="stSidebarUserContent"] > div:first-child {{ flex: 1; display: flex; flex-direction: column; }}
[data-testid="stSidebarUserContent"] > div:first-child > div[data-testid="stVerticalBlock"] {{ flex: 1; }}
[data-testid="stSidebarUserContent"] [data-testid="stLayoutWrapper"]:has(> .st-key-account-card) {{ margin-top: auto !important; }}
button[kind="primary"], button[kind="primary"] p {{ color: {ON_GOLD} !important; font-weight: 600; }}
/* top nav: radio as tabs (same as miniPJT st-key-top-nav) */
.st-key-top-nav {{ border-bottom: 1px solid {LINE}; padding-bottom: .4rem; }}
.st-key-top-nav [role="radiogroup"] {{ gap: .3rem !important; }}
.st-key-top-nav [data-testid="stRadioOption"] {{ padding: .35rem .9rem; border-radius: 8px; margin: 0 !important; cursor: pointer; }}
.st-key-top-nav [data-testid="stRadioOption"] > div > div:first-child {{ display: none !important; }}
.st-key-top-nav [data-testid="stRadioOption"]:hover {{ background: #24252b; }}
.st-key-top-nav [data-testid="stRadioOption"][data-selected="true"] {{ background: #2a2618; box-shadow: inset 0 -2px 0 {GOLD}; }}
.st-key-top-nav [data-testid="stRadioOption"][data-selected="true"] p {{ color: {GOLD_TEXT} !important; font-weight: 600; }}
.pane-title {{ font-size: 1.2rem; font-weight: 700; color: {GOLD_TEXT}; text-align: center; padding: .1rem 0 .35rem; }}
/* list cards */
.st-key-list-pane div[class*="st-key-card-"] {{ border: 1px solid {LINE}; border-radius: 10px; padding: 10px 12px; background: {SURFACE}; }}
.st-key-list-pane div[class*="st-key-card-sel-"] {{ border: 2px solid {GOLD}; }}
.st-key-list-pane div[class*="st-key-pick-"] button {{ background: transparent !important; border: 0 !important; padding: 0 !important;
    min-height: 0 !important; justify-content: flex-start !important; text-align: left !important; }}
.st-key-list-pane div[class*="st-key-pick-"] button p {{ text-align: left !important; line-height: 1.4 !important; font-weight: 600; font-size: 1rem; }}
.st-key-list-pane div[class*="st-key-pick-"] button:hover p {{ color: {GOLD} !important; }}
.st-key-list-pane img, .st-key-detail-pane img {{ border-radius: 6px; }}
.thumb, .thumb-empty {{ width: 64px; height: 86px; border-radius: 6px; object-fit: cover; display: block; }}
.thumb-empty {{ background: linear-gradient(160deg, #2c2a22, #1f2026); }}
.meta {{ color: {MUTED}; font-size: .85rem; line-height: 1.45; }}
.badge {{ display: inline-block; font-size: .74rem; border-radius: 999px; padding: 1px 9px; margin: 4px 4px 0 0; font-weight: 500;
    background: #2a2618; color: {GOLD_TEXT}; }}
.badge.running {{ color: #7fd19b; background: #1f3a2a; }} .badge.upcoming {{ color: #8fb4ff; background: #1f2a44; }}
.badge.past {{ color: #9a9890; background: #2a2b31; }}
/* detail */
.eyebrow {{ color: {GOLD_TEXT}; font-size: .74rem !important; font-weight: 700; letter-spacing: .12em; margin: 0 !important; }}
.hero {{ display: grid; grid-template-columns: minmax(0, 1fr); gap: 22px; padding-bottom: 18px; border-bottom: 1px solid {LINE}; }}
.hero.has-poster {{ grid-template-columns: 150px minmax(0, 1fr); align-items: start; }}
.hero .poster img {{ width: 100%; border-radius: 6px; box-shadow: 0 14px 34px rgba(0,0,0,.5); display: block; }}
.detail-title {{ font-size: 1.55rem; font-weight: 700; line-height: 1.4; margin: 0 0 12px; word-break: keep-all; }}
.meta-list {{ display: grid; gap: 8px; }}
.meta-list div {{ display: grid; grid-template-columns: 104px minmax(0, 1fr); gap: 10px; padding: 8px 12px; border: 1px solid {LINE};
    border-radius: 8px; background: {BG}; }}
.meta-list span {{ color: {MUTED}; font-size: .8rem; }} .meta-list b {{ font-weight: 500; overflow-wrap: anywhere; }}
.meta-list b.muted {{ color: #6f6e68; font-weight: 400; }}
.section-title {{ color: {GOLD_TEXT}; font-size: 1.05rem; font-weight: 600; margin: 18px 0 8px; }}
.section-title small {{ color: {MUTED}; font-weight: 400; margin-left: 6px; }}
.about p {{ color: #cfcdc6; line-height: 1.85; white-space: pre-line; margin: 0 0 10px; word-break: keep-all; }}
.info-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 8px 0 4px; }}
.info-grid div {{ background: {BG}; border: 1px solid {LINE}; border-radius: 8px; padding: 8px 10px; line-height: 1.45; overflow-wrap: anywhere; }}
.info-grid b {{ display: block; color: {MUTED}; font-weight: 500; font-size: .76rem; margin-bottom: 2px; }}
.empty-note {{ color: {MUTED}; border: 1px dashed {LINE}; border-radius: 8px; padding: 16px; text-align: center; font-size: .9rem; }}
.session-head {{ color: {GOLD_TEXT}; font-weight: 700; }} .session-head small {{ color: {MUTED}; font-weight: 400; margin-left: 10px; }}
.people {{ background: {BG}; border-radius: 8px; padding: 8px 12px; margin: 4px 0 6px; font-weight: 500; }}
.people span {{ color: {MUTED}; font-size: .8rem; margin-right: 10px; }}
.notes div {{ padding: 10px 2px 0; }} .notes div + div {{ border-top: 1px solid {LINE}; margin-top: 10px; }}
.notes span {{ display: block; color: {MUTED}; font-size: .8rem; margin-bottom: 2px; }}
.notes p {{ margin: 0; color: #cfcdc6; white-space: pre-wrap; line-height: 1.7; }}
.note-box {{ background: {BG}; border: 1px solid {LINE}; border-left: 3px solid {GOLD}; border-radius: 0 8px 8px 0; padding: 12px 14px;
    color: #cfcdc6; white-space: pre-wrap; line-height: 1.75; }}
.auth-card {{ max-width: 520px; margin: 8vh auto 0; text-align: center; background: {RAISED}; border: 1px solid #2a2b31;
    border-top: 3px solid {GOLD}; border-radius: 12px; padding: 40px 36px 28px; box-shadow: 0 6px 18px rgba(0,0,0,.35); }}
.auth-card h1 {{ font-size: 1.8rem; line-height: 1.35; padding: 8px 0 0; }}
.auth-card p {{ color: {MUTED}; }}
.st-key-detail-pane div[class*="st-key-edit-"] button, .st-key-detail-pane div[class*="st-key-del-"] button {{
    background: transparent !important; border: 0 !important; min-height: 0 !important; padding: 2px 6px !important; }}
.st-key-detail-pane div[class*="st-key-edit-"] button p, .st-key-detail-pane div[class*="st-key-del-"] button p {{ color: {MUTED} !important; font-size: .9rem; }}
.st-key-detail-pane div[class*="st-key-edit-"] button:hover p {{ color: {GOLD_TEXT} !important; }}
.st-key-detail-pane div[class*="st-key-del-"] button:hover p {{ color: #e58b7c !important; }}
.lookup-item-title {{ font-weight: 600; }}
@media (max-width: 640px) {{
    .st-key-list-pane {{ height: auto !important; max-height: 55vh !important; }}
    .st-key-detail-pane {{ height: auto !important; max-height: none !important; }}
    .info-grid {{ grid-template-columns: 1fr; }}
    .hero.has-poster {{ grid-template-columns: 110px minmax(0, 1fr); }}
    .meta-list div {{ grid-template-columns: 1fr; gap: 2px; }}
}}
</style>
"""

esc = html.escape


# ---------- journal state ----------

def journal():
    return st.session_state.setdefault("journal", {"docent": [], "visit": []})


def selected_id(kind):
    return st.session_state.setdefault("selected", {"docent": None, "visit": None}).get(kind)


def select(kind, record_id):
    st.session_state.setdefault("selected", {"docent": None, "visit": None})[kind] = record_id


def find_record(kind, record_id):
    return next((record for record in journal()[kind] if record["id"] == record_id), None)


def sheets(user):
    return JournalSheets(user["token"])


def load_journal(user, force=False):
    ss = st.session_state
    if not force and ss.get("journal_owner") == user["email"] and not ss.get("load_error"):
        return
    try:
        client = sheets(user)
        sheet_id = ss.get("sheet_id") if ss.get("journal_owner") == user["email"] else None
        sheet_id = sheet_id or client.find_or_create()
        docent, visits = client.read_records(sheet_id)
    except AuthExpired:
        ss["auth_expired"] = True
        return
    except PermissionMissing as error:
        ss["permission_missing"] = str(error)
        return
    except GoogleError as error:
        ss["load_error"] = str(error)
        return
    for record in docent:
        record["sessions"] = core.sort_sessions(record.get("sessions") or [])
    ss.update(journal={"docent": docent, "visit": visits}, journal_owner=user["email"], sheet_id=sheet_id,
              load_error="", auth_expired=False)
    ss.setdefault("selected", {"docent": None, "visit": None})
    for kind in ("docent", "visit"):
        if not find_record(kind, ss["selected"].get(kind)):
            ss["selected"][kind] = journal()[kind][0]["id"] if journal()[kind] else None


def save_journal(user, next_journal):
    """Validate and write the whole journal. Returns an error message or None."""
    try:
        records = core.validate_records([*next_journal["docent"], *next_journal["visit"]])
        sheets(user).write_records(st.session_state["sheet_id"], records)
    except ValueError as error:
        return str(error)
    except AuthExpired:
        st.session_state["auth_expired"] = True
        return "구글 로그인이 만료되었습니다. 다시 로그인해 주세요."
    except GoogleError as error:
        return str(error)
    st.session_state["journal"] = {
        "docent": [r for r in records if r["kind"] == "docent"],
        "visit": [r for r in records if r["kind"] == "visit"],
    }
    return None


# ---------- session photos (user's Google Drive) ----------

def photo_bytes(file_id):
    """Photo bytes for display, cached per browser session (photos are private, so never shared)."""
    cache = st.session_state.setdefault("photo_cache", {})
    if file_id not in cache:
        user = current_user()
        if not user:
            return None
        try:
            cache[file_id] = sheets(user).download_photo(file_id)
        except GoogleError:
            return None
    return cache[file_id]


def upload_photos(user, files):
    """Shrink and upload picked files; returns their Drive IDs. Raises PhotoError / GoogleError."""
    client = sheets(user)
    folder = st.session_state.get("photo_folder_id") or client.find_or_create_photo_folder()
    st.session_state["photo_folder_id"] = folder
    uploaded = []
    try:
        for number, file in enumerate(files, 1):
            data = core.prepare_photo(file.getvalue())
            name = datetime.now().strftime(f"docent-%Y%m%d-%H%M%S-{number}.jpg")
            file_id = client.upload_photo(folder, name, data)
            st.session_state.setdefault("photo_cache", {})[file_id] = data
            uploaded.append(file_id)
    except (core.PhotoError, GoogleError):
        trash_photos(user, uploaded)
        raise
    return uploaded


def trash_photos(user, ids):
    """Best effort: move removed photos to the Drive trash (recoverable for 30 days)."""
    client = sheets(user)
    for file_id in ids:
        try:
            client.trash_photo(file_id)
        except GoogleError:
            pass


def render_photo_gallery(photos, width=110):
    images = [data for data in map(photo_bytes, photos) if data]
    if images:
        st.image(images, width=width)
    if len(images) < len(photos):
        st.caption(f"사진 {len(photos) - len(images)}장을 불러오지 못했어요.")


def replace_record(kind, record):
    records = [record if item["id"] == record["id"] else item for item in journal()[kind]]
    if not any(item["id"] == record["id"] for item in journal()[kind]):
        records.insert(0, record)
    return {**journal(), kind: records}


# ---------- sidebar ----------

def render_sidebar(user):
    with st.sidebar:
        st.markdown('<p class="brand">🖼️ 나의 전시 기록</p>'
                    '<p class="tagline">도슨트로 나눈 이야기와 관람한 작품을 기록하세요</p>', unsafe_allow_html=True)
        if user:
            data = journal()
            st.markdown(f"""<div class="side-card"><div class="side-card-title">JOURNAL · 나의 기록</div>
                <div class="stats"><div class="stat"><b>{len(data['docent'])}</b><span>도슨트 수행 전시</span></div>
                <div class="stat"><b>{len(data['visit'])}</b><span>관람한 전시</span></div></div>
                <p class="storage-note">내 구글 드라이브의 ‘나의 전시 기록’ 시트에 저장돼요.</p></div>""", unsafe_allow_html=True)
        with st.container(key="account-card", border=True):
            if user:
                initial = esc((user["email"] or "?")[0].upper())
                st.markdown(f'<span class="avatar">{initial}</span><span class="account-email">{esc(user["email"]).replace("@", "&#64;")}</span>',
                            unsafe_allow_html=True)
                if st.button("로그아웃", key="logout", type="tertiary"):
                    st.session_state.clear()
                    st.logout()
            elif auth_configured():
                if st.button("🔑 구글로 로그인", key="login-side", type="primary", use_container_width=True):
                    st.login()
                st.caption("기록은 로그인한 계정의 구글 드라이브에만 저장됩니다.")


def render_signed_out():
    st.markdown("""<div class="auth-card"><div style="font-size:2rem">🖼️</div>
        <p class="eyebrow">YOUR PRIVATE EXHIBITION JOURNAL</p>
        <h1>전시와 함께한 순간을<br/>나만의 기록으로.</h1>
        <p>구글 계정으로 로그인하면 Docent 수행 기록과 관람 기록이 내 구글 드라이브의 스프레드시트에 저장됩니다.</p></div>""",
                unsafe_allow_html=True)
    if not auth_configured():
        st.error("구글 로그인 설정이 없습니다. `.streamlit/secrets.toml`(배포 시 앱 Settings → Secrets)에 [auth] 항목을 넣어 주세요. "
                 "예시는 `.streamlit/secrets.toml.example`에 있습니다.")
        return
    _, middle, _ = st.columns([1, 1, 1])
    with middle:
        if st.button("🔑 구글로 로그인하고 시작하기", key="login-main", type="primary", use_container_width=True):
            st.login()


# ---------- list pane ----------

def card_meta(kind, record):
    space = record.get("space") or "공간 미입력"
    if kind == "docent":
        return f"{esc(space)} · 수행 {len(record.get('sessions') or [])}회"
    return f"{esc(space)} · {esc(core.short_date(record.get('visitDate')) or '방문일 미입력')}"


def render_list(kind):
    head, add = st.columns([6, 1], vertical_alignment="center")
    head.markdown(f'<div class="pane-title">{LIST_TITLES[kind]}</div>', unsafe_allow_html=True)
    if add.button("＋", key=f"add-{kind}", help="새 기록 추가"):
        open_dialog("record", kind=kind, record_id=None)
    query = st.text_input("검색", key=f"q-{kind}", placeholder="🔍 전시 제목 또는 공간 검색", label_visibility="collapsed")
    needle = query.strip().casefold()
    records = [r for r in journal()[kind] if not needle or needle in f"{r.get('title', '')} {r.get('space', '')}".casefold()]
    st.caption(f"기록 {len(records)}개")
    # shorter than the detail pane by the search box, so both panes end on the same line
    with st.container(height=PANE_HEIGHT - 112, key="list-pane"):
        if not records:
            st.markdown('<div class="empty-note">' + ("검색 결과가 없습니다." if needle else "아직 기록이 없습니다. ＋ 버튼으로 첫 기록을 남겨 보세요.")
                        + "</div>", unsafe_allow_html=True)
        current = selected_id(kind)
        for record in records:
            rid = record["id"]
            with st.container(key=f"card-{'sel-' if rid == current else ''}{rid}"):
                thumb, text = st.columns([1, 4], vertical_alignment="center")
                poster = core.safe_url(record.get("posterUrl"))
                thumb.markdown(f'<img class="thumb" src="{esc(poster)}" referrerpolicy="no-referrer" alt=""/>' if poster
                               else '<div class="thumb-empty"></div>', unsafe_allow_html=True)
                with text:
                    st.button(record["title"], key=f"pick-{rid}", on_click=select, args=(kind, rid))
                    badge = '<br/><span class="badge">Docent 해설 관람</span>' if kind == "visit" and record.get("listened") else ""
                    st.markdown(f'<div class="meta">{card_meta(kind, record)}{badge}</div>', unsafe_allow_html=True)


# ---------- detail pane ----------

def hero_html(record, meta):
    poster = core.safe_url(record.get("posterUrl"))
    poster_html = (f'<a class="poster" href="{esc(poster)}" target="_blank" rel="noopener noreferrer">'
                   f'<img src="{esc(poster)}" alt="{esc(record["title"])} 포스터" referrerpolicy="no-referrer"/></a>') if poster else ""
    rows = "".join(f'<div><span>{icon} {esc(label)}</span><b class="{"muted" if muted else ""}">{esc(value)}</b></div>'
                   for icon, label, value, muted in meta)
    return (f'<div class="hero{" has-poster" if poster else ""}">{poster_html}<div>'
            f'<div class="detail-title">{esc(record["title"])}</div><div class="meta-list">{rows}</div></div></div>')


def about_html(text):
    paragraphs, facts = core.split_about(text)
    if not paragraphs and not facts:
        return '<div class="section-title">전시 소개</div><div class="empty-note">아직 입력된 소개가 없어요. 수정에서 전시 소개를 적어 보세요.</div>'
    body = "".join(f"<p>{esc(p)}</p>" for p in paragraphs)
    cells = "".join(
        f'<div><b>{esc(label)}</b>' + (f'<a href="{esc(value)}" target="_blank" rel="noopener noreferrer">{esc(value)}</a>'
                                       if core.safe_url(value) else esc(value)) + "</div>"
        for label, value in facts)
    return f'<div class="section-title">전시 소개</div><div class="about">{body}</div>' + (f'<div class="info-grid">{cells}</div>' if cells else "")


def notes_html(session):
    notes = [(label, session.get(key)) for key, label in (("questions", "받은 질문"), ("notes", "특이 사항"), ("impression", "이번 수행의 감상"))
             if session.get(key)]
    return '<div class="notes">' + "".join(f"<div><span>{label}</span><p>{esc(value)}</p></div>" for label, value in notes) + "</div>" if notes else ""


def detail_actions(kind, record, eyebrow):
    top, edit, delete = st.columns([6, 1, 1], vertical_alignment="center")
    top.markdown(f'<p class="eyebrow">{eyebrow}</p>', unsafe_allow_html=True)
    if edit.button("수정", key="edit-record"):
        open_dialog("record", kind=kind, record_id=record["id"])
    if delete.button("삭제", key="del-record"):
        open_dialog("confirm-record", kind=kind, record_id=record["id"])


def render_detail(kind):
    st.markdown('<div class="pane-title">기록 상세</div>', unsafe_allow_html=True)
    with st.container(height=PANE_HEIGHT, key="detail-pane", border=True):
        record = find_record(kind, selected_id(kind))
        if not record:
            st.markdown('<div class="empty-note">왼쪽 목록에서 전시를 선택하면 상세 기록을 볼 수 있습니다.</div>', unsafe_allow_html=True)
            return
        if kind == "docent":
            detail_actions(kind, record, "DOCENT PERFORMANCE")
            st.markdown(hero_html(record, [
                ("◷", "전시 일정", record.get("schedule") or "미입력", not record.get("schedule")),
                ("⌖", "전시 공간", record.get("space") or "미입력", not record.get("space")),
                ("◉", "도슨트 수행", core.docent_summary(record), not record.get("sessions")),
            ]), unsafe_allow_html=True)
            st.markdown(about_html(record.get("detail")), unsafe_allow_html=True)
            render_sessions(record)
        else:
            detail_actions(kind, record, "EXHIBITION VISIT")
            st.markdown(hero_html(record, [
                ("◷", "방문일", core.korean_date(record.get("visitDate")) or "미입력", not record.get("visitDate")),
                ("⌖", "전시 공간", record.get("space") or "미입력", not record.get("space")),
                ("◉", "Docent 해설", "들었어요" if record.get("listened") else "듣지 않았어요", not record.get("listened")),
            ]), unsafe_allow_html=True)
            st.markdown(about_html(record.get("detail")), unsafe_allow_html=True)
            if record.get("listened"):
                st.markdown('<div class="section-title">Docent 해설 평가</div>'
                            f'<div class="note-box">{esc(record.get("evaluation") or "아직 평가를 작성하지 않았어요.")}</div>',
                            unsafe_allow_html=True)
            st.markdown('<div class="section-title">나의 감상</div>'
                        f'<div class="note-box">{esc(record.get("impression") or "아직 감상을 기록하지 않았어요.")}</div>',
                        unsafe_allow_html=True)


def render_sessions(record):
    sessions = record.get("sessions") or []
    head, add = st.columns([5, 2], vertical_alignment="center")
    head.markdown(f'<div class="section-title">수행 회차 <small>총 {len(sessions)}회</small></div>', unsafe_allow_html=True)
    if add.button("＋ 회차 추가", key="add-session", use_container_width=True):
        open_dialog("session", kind="docent", record_id=record["id"], index=None)
    if not sessions:
        st.markdown('<div class="empty-note">아직 수행 회차가 없습니다. ＋ 회차 추가로 도슨트를 진행한 날의 기록을 남겨 보세요.</div>',
                    unsafe_allow_html=True)
    for index in reversed(range(len(sessions))):
        session = sessions[index]
        with st.container(border=True, key=f"session-box-{index}"):
            title, edit, delete = st.columns([6, 1, 1], vertical_alignment="center")
            when = core.korean_date(session.get("performedAt"), with_time=True) or "날짜 미입력"
            title.markdown(f'<div class="session-head">{index + 1}회차<small>{esc(when)}</small></div>', unsafe_allow_html=True)
            if edit.button("수정", key=f"edit-session-{index}"):
                open_dialog("session", kind="docent", record_id=record["id"], index=index)
            if delete.button("삭제", key=f"del-session-{index}"):
                open_dialog("confirm-session", kind="docent", record_id=record["id"], index=index)
            st.markdown(f'<div class="people"><span>참여 인원</span>{esc(core.participants_text(session))}</div>{notes_html(session)}',
                        unsafe_allow_html=True)
            if session.get("photos"):
                render_photo_gallery(session["photos"])


# ---------- dialogs ----------

FORM_FIELDS = ("title", "schedule", "space", "visitDate", "detail", "posterUrl", "evaluation", "impression", "listened")


def open_dialog(name, **params):
    ss = st.session_state
    ss["dialog"] = {"name": name, **params}
    kind, record_id = params.get("kind"), params.get("record_id")
    if name == "record":
        record = find_record(kind, record_id) or {}
        for field in FORM_FIELDS:
            ss[f"rf_{field}"] = record.get(field, False if field == "listened" else "")
        visit = record.get("visitDate")
        ss["rf_visitDate"] = datetime.strptime(visit, "%Y-%m-%d").date() if visit else None
        ss["rf_mode"] = "직접 입력" if record_id or not search_client() else "공공데이터에서 찾기"
        ss["lk_query"] = record.get("title", "")
        ss["lk_status"], ss["lk_sort"], ss["lk_page"] = "running", "latest", 1
        ss.pop("lk_result", None)
        ss.pop("lk_picked", None)
    elif name == "session":
        record = find_record(kind, record_id) or {}
        index = params.get("index")
        session = (record.get("sessions") or [])[index] if index is not None else {}
        performed = session.get("performedAt") or ""
        try:
            moment = datetime.strptime(performed, "%Y-%m-%dT%H:%M")
            ss["sf_date"], ss["sf_time"] = moment.date(), moment.time()
        except ValueError:
            ss["sf_date"], ss["sf_time"] = date.today(), time(14, 0)
        for key, _ in core.AUDIENCE_LABELS:
            ss[f"sf_{key}"] = session.get(key) if core.has_count(session.get(key)) else None
        for key in ("questions", "notes", "impression"):
            ss[f"sf_{key}"] = session.get(key, "")
        ss["sf_photos"] = list(session.get("photos") or [])
        ss["sf_photos_removed"] = []
        ss["sf_photo_confirm"] = None
        ss["sf_upload_key"] = ss.get("sf_upload_key", 0) + 1  # fresh file uploader each time


def close_dialog():
    st.session_state.pop("dialog", None)


def run_dialog(user):
    current = st.session_state.get("dialog")
    if not current:
        return
    name = current["name"]
    if name == "record":
        title = "전시 기록 수정" if current.get("record_id") else ("Docent 기록 추가" if current["kind"] == "docent" else "관람 기록 추가")
        st.dialog(title, width="large", on_dismiss=close_dialog)(record_dialog)(user, current["kind"], current.get("record_id"))
    elif name == "session":
        title = "수행 회차 추가" if current.get("index") is None else f"{current['index'] + 1}회차 기록 수정"
        st.dialog(title, width="medium", on_dismiss=close_dialog)(session_dialog)(user, current["record_id"], current.get("index"))
    elif name == "confirm-record":
        st.dialog("기록 삭제", on_dismiss=close_dialog)(confirm_dialog)(user, current)
    elif name == "confirm-session":
        st.dialog("회차 삭제", on_dismiss=close_dialog)(confirm_dialog)(user, current)


def pick_exhibition(item):
    """on_click: fill the form from a public-data result (runs before widgets are drawn)."""
    ss = st.session_state
    ss["rf_title"] = item["title"][:120]
    ss["rf_space"] = item.get("place", "")[:120]
    ss["rf_schedule"] = core.exhibition_period(item)[:120]
    ss["rf_posterUrl"] = item.get("thumbnail", "")
    client = search_client()
    if client:
        try:
            detail = client.detail(item["seq"])
            text = core.detail_text(detail)
            if text and not ss.get("rf_detail", "").strip():
                ss["rf_detail"] = text[:20000]
            if detail.get("imgUrl"):
                ss["rf_posterUrl"] = detail["imgUrl"]
        except PublicDataError:
            pass
    ss["lk_picked"] = item["title"]
    ss["rf_mode"] = "직접 입력"


def run_search(page=1):
    ss = st.session_state
    client = search_client()
    keyword = ss.get("lk_query", "").strip()
    if not client or not keyword:
        ss["lk_result"] = {"error": "검색어를 입력해 주세요."} if client else None
        return
    try:
        result = client.search(keyword, page=page, status=ss["lk_status"], sort=ss["lk_sort"])
        if page > 1 and ss.get("lk_result", {}).get("items"):
            result["items"] = ss["lk_result"]["items"] + result["items"]
        ss["lk_result"], ss["lk_page"] = result, page
    except PublicDataError as error:
        ss["lk_result"] = {"error": str(error)}


def render_lookup():
    ss = st.session_state
    with st.container(border=True, key="lookup-card"):
        query, go = st.columns([5, 1], vertical_alignment="bottom")
        query.text_input("공공데이터 전시 검색", key="lk_query", placeholder="전시 제목이나 미술관 이름으로 검색", on_change=run_search)
        if go.button("검색", key="lk_go", type="primary", use_container_width=True):
            run_search()
        filters, sort = st.columns([3, 1], vertical_alignment="bottom")
        counts = (ss.get("lk_result") or {}).get("counts") or {}
        filters.segmented_control("전시 상태", list(STATUS_OPTIONS), key="lk_status", required=True, on_change=run_search,
                                  format_func=lambda s: STATUS_OPTIONS[s] + (f" {counts[s]}" if s in counts else ""),
                                  label_visibility="collapsed")
        sort.selectbox("정렬", list(SORT_OPTIONS), key="lk_sort", format_func=SORT_OPTIONS.get, on_change=run_search,
                       label_visibility="collapsed")
        result = ss.get("lk_result")
        if not result:
            st.caption("한국문화정보원 공공데이터에서 전시를 찾아 선택하면 아래 정보가 채워져요.")
            return
        if result.get("error"):
            st.warning(result["error"] + " 찾는 전시가 없으면 ‘직접 입력’으로 바꿔 입력해 주세요.")
            return
        if not counts.get("all"):
            st.info("공공데이터에서 찾지 못했어요. 위에서 ‘직접 입력’을 골라 입력해 주세요.")
            return
        if not result["items"]:
            others = ", ".join(f"{STATUS_OPTIONS[s]} {counts[s]}건" for s in ("running", "upcoming", "past") if counts.get(s))
            st.info(f"{STATUS_OPTIONS[ss['lk_status']]}인 전시가 없어요. ({others})")
            return
        note = " · 결과가 많아 일부만 보여요. 검색어를 더 구체적으로 입력해 보세요." if result.get("truncated") else ""
        st.caption(f"{STATUS_OPTIONS[ss['lk_status']]} {result['total']}건 · 선택하면 아래 정보가 채워져요.{note}")
        with st.container(height=300):
            for item in result["items"]:
                thumb, text, pick = st.columns([0.6, 7, 1.1], vertical_alignment="center")
                poster = core.safe_url(item.get("thumbnail"))
                thumb.markdown(f'<img class="thumb" style="width:46px;height:62px" src="{esc(poster)}" referrerpolicy="no-referrer" alt=""/>'
                               if poster else '<div class="thumb-empty" style="width:46px;height:62px"></div>', unsafe_allow_html=True)
                status = item.get("status", "running")
                text.markdown(f'<span class="badge {status}">{BADGES.get(status, "")}</span> <span class="lookup-item-title">{esc(item["title"])}</span>'
                              f'<div class="meta">{esc(" · ".join(filter(None, (item.get("place"), item.get("area")))) or "장소 정보 없음")}'
                              f' · {esc(core.exhibition_period(item) or "기간 정보 없음")}</div>', unsafe_allow_html=True)
                pick.button("선택", key=f"lk-pick-{item['seq']}", on_click=pick_exhibition, args=(item,))
        if result.get("hasMore"):
            st.button("결과 더 보기", key="lk_more", on_click=run_search, args=(ss.get("lk_page", 1) + 1,), use_container_width=True)


def record_dialog(user, kind, record_id):
    ss = st.session_state
    if search_client():
        st.segmented_control("입력 방식", ["공공데이터에서 찾기", "직접 입력"], key="rf_mode", required=True, label_visibility="collapsed")
        if ss.get("rf_mode") == "공공데이터에서 찾기":
            render_lookup()
    else:
        st.caption("공공데이터 전시 검색이 설정되지 않아 직접 입력으로 기록합니다. (CULTURE_API_KEY)")
    if ss.get("lk_picked"):
        st.success(f"공공데이터에서 ‘{ss['lk_picked']}’ 정보를 채웠어요. 필요하면 아래에서 고칠 수 있어요.")

    st.markdown('<p class="eyebrow">전시 정보</p>' if kind == "docent" else '<p class="eyebrow">관람 정보</p>', unsafe_allow_html=True)
    left, right = st.columns(2)
    left.text_input("전시 제목 *", key="rf_title", max_chars=120)
    if kind == "docent":
        right.text_input("전시 일정", key="rf_schedule", max_chars=120, placeholder="예: 2026. 9. 1. – 11. 30.")
        st.text_input("전시 공간", key="rf_space", max_chars=120, placeholder="미술관 또는 전시장 이름")
    else:
        right.text_input("전시 공간", key="rf_space", max_chars=120, placeholder="미술관 또는 전시장 이름")
        left2, right2 = st.columns(2)
        left2.date_input("전시장 방문일", key="rf_visitDate", format="YYYY.MM.DD")
        right2.checkbox("Docent 설명을 들었어요", key="rf_listened")
    st.text_area("전시 상세 정보", key="rf_detail", height=110, placeholder="전시 소개, 참여 작가, 작품 정보 등을 적어 보세요.")
    poster_col, preview = st.columns([5, 1], vertical_alignment="center")
    poster_col.text_input("포스터 이미지 주소", key="rf_posterUrl", placeholder="공공데이터에서 고르면 자동으로 채워져요.")
    poster = core.safe_url(ss.get("rf_posterUrl"))
    preview.markdown(f'<img class="thumb" src="{esc(poster)}" referrerpolicy="no-referrer" alt=""/>' if poster
                     else '<div class="thumb-empty"></div>', unsafe_allow_html=True)
    if kind == "visit":
        st.text_area("Docent 설명에 대한 평가", key="rf_evaluation", height=90, disabled=not ss.get("rf_listened"))
        st.text_area("나의 감상", key="rf_impression", height=110)
    else:
        st.caption("수행 회차는 전시를 저장한 뒤, 목록에서 전시를 선택해 추가할 수 있어요.")

    cancel, save = st.columns([4, 1])
    if cancel.button("취소", key="rf_cancel"):
        close_dialog()
        st.rerun()
    if save.button("기록 저장", key="rf_save", type="primary", use_container_width=True):
        existing = find_record(kind, record_id) or {}
        record = {**existing, "id": record_id or str(uuid.uuid4()), "kind": kind,
                  "title": ss.get("rf_title", "").strip(), "space": ss.get("rf_space", "").strip(),
                  "detail": ss.get("rf_detail", "").strip(), "posterUrl": ss.get("rf_posterUrl", "").strip()}
        if kind == "docent":
            record["schedule"] = ss.get("rf_schedule", "").strip()
            record.setdefault("sessions", [])
        else:
            visit = ss.get("rf_visitDate")
            record["visitDate"] = visit.isoformat() if visit else ""
            record["listened"] = bool(ss.get("rf_listened"))
            record["evaluation"] = ss.get("rf_evaluation", "").strip() if record["listened"] else ""
            record["impression"] = ss.get("rf_impression", "").strip()
        error = save_journal(user, replace_record(kind, record))
        if error:
            st.error(error)
            return
        select(kind, record["id"])
        close_dialog()
        st.toast("기록을 저장했어요.", icon="✅")
        st.rerun()


def remove_draft_photo(file_id):
    """on_click after the confirmation: drop the photo from the draft (trashed only when saved)."""
    ss = st.session_state
    ss["sf_photos"] = [p for p in ss["sf_photos"] if p != file_id]
    ss["sf_photos_removed"] = [*ss.get("sf_photos_removed", []), file_id]
    ss["sf_photo_confirm"] = None


def render_photo_editor():
    """Photos of the session being edited: saved ones (removable after a confirmation) + new uploads."""
    ss = st.session_state
    st.markdown(f"**수행 사진** <small style='color:{MUTED}'>({len(ss['sf_photos'])}/{core.MAX_PHOTOS})</small>",
                unsafe_allow_html=True)
    if ss["sf_photos"]:
        columns = st.columns(core.MAX_PHOTOS)
        for column, file_id in zip(columns, list(ss["sf_photos"])):
            data = photo_bytes(file_id)
            if data:
                column.image(data, width="stretch")
            else:
                column.caption("불러오지 못함")
            column.button("빼기", key=f"sf-photo-remove-{file_id}", use_container_width=True,
                          on_click=ss.__setitem__, args=("sf_photo_confirm", file_id))
    pending = ss.get("sf_photo_confirm")
    if pending in ss["sf_photos"]:
        st.warning("이 사진을 뺄까요? ‘저장’을 누르면 드라이브 휴지통으로 옮겨져요(30일 동안 되살릴 수 있어요).")
        keep, remove = st.columns([3, 1])
        keep.button("취소", key="sf-photo-keep", on_click=ss.__setitem__, args=("sf_photo_confirm", None))
        remove.button("사진 빼기", key="sf-photo-confirm", type="primary", use_container_width=True,
                      on_click=remove_draft_photo, args=(pending,))
    room = core.MAX_PHOTOS - len(ss["sf_photos"])
    files = st.file_uploader(
        f"사진 추가 (JPG·PNG·WEBP, 한 장 10MB 이하, {max(room, 0)}장 더 올릴 수 있어요)",
        type=list(core.PHOTO_TYPES), accept_multiple_files=True, key=f"sf-upload-{ss.get('sf_upload_key', 0)}",
        disabled=room <= 0)
    st.caption("저장할 때 긴 변 1600px로 줄이고 위치 정보는 지워 내 구글 드라이브의 ‘나의 전시 기록 사진’ 폴더에 올려요.")
    return list(files or [])


def session_dialog(user, record_id, index):
    ss = st.session_state
    record = find_record("docent", record_id)
    if not record:
        st.error("회차를 추가할 전시를 찾을 수 없습니다.")
        return
    st.caption(record["title"])
    day, clock = st.columns(2)
    day.date_input("수행 날짜 *", key="sf_date", format="YYYY.MM.DD")
    clock.time_input("시간 *", key="sf_time", step=600)
    st.markdown("**참여 인원**")
    columns = st.columns(3)
    for column, (key, label) in zip(columns, core.AUDIENCE_LABELS):
        column.number_input(f"{label} (명)", key=f"sf_{key}", min_value=0, max_value=100000, step=1, value=None, placeholder="0")
    filled = [ss[f"sf_{key}"] for key, _ in core.AUDIENCE_LABELS if ss.get(f"sf_{key}") is not None]
    previous = (record.get("sessions") or [])[index] if index is not None else {}
    if filled:
        st.caption(f"합계 {sum(int(v) for v in filled)}명")
    elif core.has_count(previous.get("participants")):
        st.caption(f"이전에 구분 없이 총 {previous['participants']}명으로 기록된 회차입니다. 비워 두면 기존 인원이 유지됩니다.")
    st.text_area("받은 질문", key="sf_questions", height=80)
    st.text_area("특이 사항", key="sf_notes", height=80)
    st.text_area("이번 수행의 감상", key="sf_impression", height=100)
    new_files = render_photo_editor()
    cancel, save = st.columns([3, 1])
    if cancel.button("취소", key="sf_cancel"):
        close_dialog()
        st.rerun()
    if save.button("저장", key="sf_save", type="primary", use_container_width=True):
        if not ss.get("sf_date") or not ss.get("sf_time"):
            st.error("수행 날짜와 시간을 입력해 주세요.")
            return
        session = {
            "performedAt": datetime.combine(ss["sf_date"], ss["sf_time"]).strftime("%Y-%m-%dT%H:%M"),
            **{key: int(ss[f"sf_{key}"]) if ss.get(f"sf_{key}") is not None else "" for key, _ in core.AUDIENCE_LABELS},
            **{key: ss.get(f"sf_{key}", "").strip() for key in ("questions", "notes", "impression")},
        }
        session["participants"] = sum(int(v) for v in filled) if filled else previous.get("participants", "")
        if len(ss["sf_photos"]) + len(new_files) > core.MAX_PHOTOS:
            st.error(f"회차당 사진은 {core.MAX_PHOTOS}장까지 올릴 수 있어요.")
            return
        uploaded = []
        if new_files:
            try:
                with st.spinner("사진을 올리는 중…"):
                    uploaded = upload_photos(user, new_files)
            except (core.PhotoError, GoogleError) as error:
                st.error(str(error))
                return
        session["photos"] = [*ss["sf_photos"], *uploaded]
        sessions = list(record.get("sessions") or [])
        if index is None:
            sessions.append(session)
        else:
            sessions[index] = session
        error = save_journal(user, replace_record("docent", {**record, "sessions": core.sort_sessions(sessions)}))
        if error:
            trash_photos(user, uploaded)
            st.error(error)
            return
        trash_photos(user, ss.get("sf_photos_removed") or [])
        close_dialog()
        st.toast("회차를 저장했어요.", icon="✅")
        st.rerun()


def confirm_dialog(user, current):
    kind, record = current["kind"], find_record(current["kind"], current["record_id"])
    if not record:
        close_dialog()
        st.rerun()
    index = current.get("index")
    photos = core.session_photo_ids(record) if index is None else list(record["sessions"][index].get("photos") or [])
    photo_note = f" 사진 {len(photos)}장은 드라이브 휴지통으로 옮겨져요." if photos else ""
    if index is None:
        st.write(f"‘{record['title']}’ 기록을 삭제할까요? 삭제한 기록은 복구할 수 없습니다.{photo_note}")
    else:
        when = core.korean_date(record["sessions"][index].get("performedAt")) or "날짜 미입력"
        st.write(f"{index + 1}회차({when}) 기록을 삭제할까요? 삭제한 기록은 복구할 수 없습니다.{photo_note}")
    cancel, delete = st.columns([3, 1])
    if cancel.button("취소", key="cf_cancel"):
        close_dialog()
        st.rerun()
    if delete.button("삭제", key="cf_delete", type="primary", use_container_width=True):
        if index is None:
            next_journal = {**journal(), kind: [r for r in journal()[kind] if r["id"] != record["id"]]}
        else:
            sessions = [s for i, s in enumerate(record["sessions"]) if i != index]
            next_journal = replace_record(kind, {**record, "sessions": sessions})
        error = save_journal(user, next_journal)
        if error:
            st.error(error)
            return
        trash_photos(user, photos)
        if index is None:
            remaining = journal()[kind]
            select(kind, remaining[0]["id"] if remaining else None)
        close_dialog()
        st.rerun()


# ---------- page ----------

def main():
    st.set_page_config(page_title="나의 전시 기록", page_icon="🖼️", layout="wide")
    st.markdown(PAGE_CSS, unsafe_allow_html=True)
    user = current_user()
    if user:
        load_journal(user)
    render_sidebar(user)
    if not user:
        render_signed_out()
        return
    ss = st.session_state
    if ss.get("auth_expired"):
        st.warning("구글 로그인이 만료되었습니다. 다시 로그인해 주세요.")
        if st.button("다시 로그인", type="primary"):
            ss.clear()
            st.logout()
        return
    if ss.get("permission_missing"):
        st.warning(ss["permission_missing"])
        if st.button("로그아웃하고 다시 로그인", type="primary"):
            ss.clear()
            st.logout()
        return
    if ss.get("load_error"):
        st.error(ss["load_error"])
        if st.button("다시 불러오기"):
            ss["load_error"] = ""
            load_journal(user, force=True)
            st.rerun()
        return
    data = journal()
    with st.container(key="top-nav"):
        kind = st.radio("기록 종류", list(KINDS), key="tab", horizontal=True, label_visibility="collapsed",
                        format_func=lambda k: f"{KINDS[k]}  {len(data[k])}")
    left, right = st.columns([1, 1.2], gap="medium")
    with left:
        render_list(kind)
    with right:
        render_detail(kind)
    run_dialog(user)


if __name__ == "__main__":
    main()

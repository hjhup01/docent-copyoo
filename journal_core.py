"""Journal rules shared by the web server (server.py) and the Streamlit app.

- validate_records(): normalizes and checks records before they are written to Google Sheets.
- Display helpers (dates, participants, exhibition periods, "라벨: 값" facts) used by the Streamlit UI;
  they mirror the behaviour of app.js so both front ends show the same text.
"""
import io
import re
from datetime import date, datetime

from google_sheets import AUDIENCE_COLUMNS

AUDIENCE_KEYS = tuple(key for key, _ in AUDIENCE_COLUMNS)
AUDIENCE_LABELS = (("adults", "성인"), ("teens", "청소년"), ("children", "어린이"))
RECORD_TEXT_FIELDS = ("title", "schedule", "space", "detail", "visitDate", "evaluation", "impression", "posterUrl")
SESSION_TEXT_FIELDS = ("performedAt", "questions", "notes", "impression")
MAX_TEXT = 20000

# Session photos (same rules as miniPJT's visit photos)
MAX_PHOTOS = 5
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_SIDE = 1600
JPEG_QUALITY = 85
PHOTO_TYPES = ("jpg", "jpeg", "png", "webp")
PHOTO_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")


class PhotoError(ValueError):
    """User-facing message about an uploaded photo."""


def prepare_photo(data):
    """Uploaded bytes -> JPEG bytes: long side <= MAX_SIDE, rotated upright, EXIF (location etc.) dropped."""
    if not data:
        raise PhotoError("사진 파일을 읽지 못했습니다.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise PhotoError("사진 한 장은 10MB 이하로 올려 주세요.")
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            image.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise PhotoError("JPG·PNG·WEBP 사진만 올릴 수 있습니다.") from None


def valid_photo_id(value):
    return isinstance(value, str) and bool(PHOTO_ID.match(value))


def session_photo_ids(record):
    return [photo for session in record.get("sessions") or [] for photo in session.get("photos") or []]


# ---------- validation ----------

def _clean_count(value):
    if value == "":
        return ""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100_000:
        return value
    raise ValueError("참여 인원은 0 이상의 정수로 입력해 주세요.")


def validate_records(value):
    if not isinstance(value, list) or len(value) > 2000:
        raise ValueError("전시 기록 형식이 올바르지 않습니다.")
    records = []
    seen = set()
    for item in value:
        if not isinstance(item, dict) or item.get("kind") not in {"docent", "visit"}:
            raise ValueError("전시 기록 형식이 올바르지 않습니다.")
        record_id = str(item.get("id", ""))
        title = item.get("title")
        if not record_id or len(record_id) > 100 or record_id in seen:
            raise ValueError("전시 기록 ID가 올바르지 않습니다.")
        if not isinstance(title, str) or not title.strip() or len(title) > 120:
            raise ValueError("전시 제목을 입력해 주세요.")
        seen.add(record_id)
        clean = {"id": record_id, "kind": item["kind"]}
        for field in RECORD_TEXT_FIELDS:
            field_value = item.get(field, "")
            if not isinstance(field_value, str):
                raise ValueError("전시 기록의 입력값 형식이 올바르지 않습니다.")
            if len(field_value) > MAX_TEXT:
                raise ValueError("전시 기록의 입력 길이가 너무 깁니다.")
            clean[field] = field_value.strip() if field != "detail" else field_value
        poster = clean["posterUrl"]
        if poster and (len(poster) > 1000 or not poster.lower().startswith(("https://", "http://"))):
            raise ValueError("포스터 이미지 주소는 http:// 또는 https://로 시작해야 합니다.")
        if item["kind"] == "docent":
            sessions = item.get("sessions", [])
            if not isinstance(sessions, list) or len(sessions) > 500:
                raise ValueError("Docent 수행 회차 형식이 올바르지 않습니다.")
            clean["sessions"] = []
            for session in sessions:
                if not isinstance(session, dict):
                    raise ValueError("Docent 수행 회차 형식이 올바르지 않습니다.")
                clean_session = {
                    key: _clean_count(session.get(key, ""))
                    for key in ("participants", *AUDIENCE_KEYS)
                }
                audience = [clean_session[key] for key in AUDIENCE_KEYS if clean_session[key] != ""]
                if audience:
                    # The total is derived from the breakdown; a bare total is
                    # kept only for sessions recorded before the breakdown existed.
                    clean_session["participants"] = sum(audience)
                for field in SESSION_TEXT_FIELDS:
                    field_value = session.get(field, "")
                    if not isinstance(field_value, str) or len(field_value) > MAX_TEXT:
                        raise ValueError("Docent 수행 회차 입력값이 올바르지 않습니다.")
                    clean_session[field] = field_value
                photos = session.get("photos", [])
                if not isinstance(photos, list) or len(photos) > MAX_PHOTOS or not all(map(valid_photo_id, photos)):
                    raise ValueError(f"회차 사진은 {MAX_PHOTOS}장까지 올릴 수 있습니다.")
                clean_session["photos"] = list(dict.fromkeys(photos))
                clean["sessions"].append(clean_session)
        else:
            listened = item.get("listened", False)
            if not isinstance(listened, bool):
                raise ValueError("Docent 설명 여부를 확인할 수 없습니다.")
            clean["listened"] = listened
            if not listened:
                clean["evaluation"] = ""
        records.append(clean)
    return records


# ---------- display helpers (same wording as app.js) ----------

def has_count(value):
    return value != "" and value is not None


def sort_sessions(sessions):
    return sorted(sessions, key=lambda session: str(session.get("performedAt") or ""))


def session_participants(session):
    """Total for one session, or None when nothing was recorded."""
    counts = [session.get(key) for key in AUDIENCE_KEYS if has_count(session.get(key))]
    if counts:
        return sum(int(value) for value in counts)
    participants = session.get("participants")
    return int(participants) if has_count(participants) else None


def participants_text(session):
    """'총 20명 (성인 12명 · 청소년 5명 · 어린이 3명)'; old sessions only have a total."""
    parts = [f"{label} {session[key]}명" for key, label in AUDIENCE_LABELS if has_count(session.get(key))]
    if parts:
        return f"총 {session_participants(session)}명 ({' · '.join(parts)})"
    participants = session.get("participants")
    return f"{participants}명 (구분 없음)" if has_count(participants) else "미입력"


def docent_summary(record):
    sessions = record.get("sessions") or []
    if not sessions:
        return "아직 없음"
    counted = [value for value in map(session_participants, sessions) if value is not None]
    return f"{len(sessions)}회" + (f" · 누적 {sum(counted)}명" if counted else "")


def _parse(value):
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt), "T" in fmt
        except ValueError:
            continue
    return None, False


def korean_date(value, with_time=False):
    """'2026년 11월 7일' / '2026년 11월 7일 오전 11:00'. Unparseable text is returned as is."""
    parsed, has_time = _parse(value)
    if not parsed:
        return str(value or "")
    text = f"{parsed.year}년 {parsed.month}월 {parsed.day}일"
    if with_time and has_time:
        hour = parsed.hour % 12 or 12
        text += f" {'오전' if parsed.hour < 12 else '오후'} {hour:02d}:{parsed.minute:02d}"
    return text


def short_date(value):
    """'9월 20일' for list cards."""
    parsed, _ = _parse(value)
    return f"{parsed.month}월 {parsed.day}일" if parsed else str(value or "")


def compact_date(value):
    """YYYYMMDD -> '2026. 9. 1.'"""
    match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", str(value or "").strip())
    if not match:
        return str(value or "").strip()
    return f"{int(match[1])}. {int(match[2])}. {int(match[3])}."


def exhibition_period(item):
    start, end = compact_date(item.get("startDate")), compact_date(item.get("endDate"))
    if start and end:
        return start if start == end else f"{start} – {end}"
    return start or end


def detail_text(detail):
    """Text for '전시 상세 정보' from a public-data detail item."""
    blocks = []
    contents = detail.get("contents1") or ""
    if contents:
        blocks.append(contents if len(contents) <= 3000 else contents[:3000] + "…")
    place = detail.get("placeAddr") or " ".join(filter(None, (detail.get("area"), detail.get("sigungu"), detail.get("place"))))
    facts = [(label, value) for label, value in (
        ("장소", place), ("관람료", detail.get("price")), ("문의", detail.get("phone")), ("누리집", detail.get("url")),
    ) if value]
    if facts:
        blocks.append("\n".join(f"{label}: {value}" for label, value in facts))
    return "\n\n".join(blocks)


FACT_LINE = re.compile(r"^(장소|주소|관람료|입장료|문의|누리집|홈페이지|기간|관람\s?시간|작가|참여\s?작가)\s*[:：]\s*(.+)$")


def split_about(text):
    """Free text -> (paragraphs, facts). '라벨: 값' lines become facts."""
    facts, paragraphs = [], []
    for block in re.split(r"\n\s*\n", str(text or "").replace("\r", "")):
        kept = []
        for line in block.split("\n"):
            match = FACT_LINE.match(line.strip())
            if match:
                facts.append((match[1], match[2].strip()))
            else:
                kept.append(line)
        paragraph = "\n".join(kept).strip()
        if paragraph:
            paragraphs.append(paragraph)
    return paragraphs, facts


def safe_url(value):
    url = str(value or "").strip()
    return url if re.match(r"^https?://", url, re.I) else ""


def today_stamp(today=None):
    return (today or date.today()).strftime("%Y%m%d")

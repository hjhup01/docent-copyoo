"""Exhibition search backed by the Korea Culture Information Service public API.

Service: 한국문화정보원_한눈에보는문화정보조회서비스 (data.go.kr dataset 15138937)
  - realm2   : 분야별 문화정보목록조회 (realmCode=D000 -> 전시)
  - detail2  : 문화정보 상세정보조회 (seq)
Responses are XML. The service key is issued by data.go.kr after applying for use.
"""
import html
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://apis.data.go.kr/B553457/cultureinfo"
EXHIBITION_REALM = "D000"
TIMEOUT_SECONDS = 10
CACHE_SECONDS = 600
CACHE_MAX_ENTRIES = 300
RETRY_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 1.2
PAGE_SIZE = 10
SEARCH_FIELDS = ("keyword", "place")  # title match first, then venue match
STATUSES = ("running", "upcoming", "past", "all")
SORTS = ("latest", "ending", "title")
RESULT_PAGE_SIZE = 20
FETCH_PAGE_SIZE = 100
MAX_FETCH_PAGES = 3  # per search field -> at most 300 title + 300 venue matches
# The service holds exhibitions from 2013 on; start well before that so the
# "past" filter covers its whole history.
HISTORY_START = "20000101"
FUTURE_YEARS = 3
IMAGE_FIELDS = ("thumbnail", "imgUrl")
MAX_KEYWORD_LENGTH = 50
LIST_FIELDS = ("seq", "title", "startDate", "endDate", "place", "realmName", "area", "sigungu", "thumbnail")
DETAIL_FIELDS = LIST_FIELDS + ("price", "contents1", "url", "phone", "imgUrl", "placeAddr", "placeUrl")
KEY_ERRORS = {
    "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
    "SERVICE_ACCESS_DENIED_ERROR",
    "DEADLINE_HAS_EXPIRED_ERROR",
    "UNREGISTERED_IP_ERROR",
}


class PublicDataError(Exception):
    """A safe, user-facing public data API error."""


class InvalidSearch(PublicDataError):
    """The request itself is invalid (empty keyword, unknown filter...)."""


class ExhibitionSearch:
    def __init__(self, service_key):
        # data.go.kr issues both an URL-encoded and a decoded key; accept either.
        self.service_key = unquote(service_key.strip()) if service_key else ""
        self._cache = {}
        self._lock = threading.Lock()

    @property
    def configured(self):
        return bool(self.service_key)

    def search(self, keyword, page=1, status="running", sort="latest", today=None):
        """Search exhibitions by title or venue.

        status: running(전시중) / upcoming(전시예정) / past(지난전시) / all(전체)
        sort:   latest(최신순: 시작일 늦은 순) / ending(끝나는 날짜순) / title(이름순)
        """
        keyword = (keyword or "").strip()
        if not keyword:
            raise InvalidSearch("검색어를 입력해 주세요.")
        if len(keyword) > MAX_KEYWORD_LENGTH:
            raise InvalidSearch(f"검색어는 {MAX_KEYWORD_LENGTH}자 이내로 입력해 주세요.")
        if status not in STATUSES:
            raise InvalidSearch("전시 상태 필터가 올바르지 않습니다.")
        if sort not in SORTS:
            raise InvalidSearch("정렬 기준이 올바르지 않습니다.")
        page = max(1, min(int(page), 100))
        today = (today or date.today()).strftime("%Y%m%d")
        items, truncated = self._collect(keyword, today)
        counts = {name: 0 for name in STATUSES}
        for item in items:
            item["status"] = exhibition_status(item, today)
            counts[item["status"]] += 1
        counts["all"] = len(items)
        matched = [item for item in items if status == "all" or item["status"] == status]
        matched = sort_exhibitions(matched, sort, status)
        start = (page - 1) * RESULT_PAGE_SIZE
        return {
            "items": matched[start:start + RESULT_PAGE_SIZE],
            "page": page,
            "status": status,
            "sort": sort,
            "total": len(matched),
            "counts": counts,
            "hasMore": start + RESULT_PAGE_SIZE < len(matched),
            "truncated": truncated,
        }

    def _collect(self, keyword, today):
        """Every exhibition matching the keyword by title or venue, whole history.

        The API's `keyword` only matches titles and `place` matches venue names,
        so both are queried and merged (title matches first). Each is fetched in
        pages of 100, up to MAX_FETCH_PAGES, so filters and sorting can be applied
        to the full result instead of one API page at a time.
        """
        dates = (HISTORY_START, _years_from(date(int(today[:4]), int(today[4:6]), int(today[6:])), FUTURE_YEARS).strftime("%Y%m%d"))
        with ThreadPoolExecutor(max_workers=len(SEARCH_FIELDS)) as pool:
            futures = [pool.submit(self._fetch_all, field, keyword, dates) for field in SEARCH_FIELDS]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except PublicDataError as error:
                    outcomes.append(error)
        results = [outcome for outcome in outcomes if not isinstance(outcome, PublicDataError)]
        if not results:
            raise outcomes[0]
        items, seen = [], set()
        for found, _ in results:
            for item in found:
                if item["seq"] not in seen:
                    seen.add(item["seq"])
                    items.append(dict(item))
        return items, any(truncated for _, truncated in results)

    def _fetch_all(self, field, keyword, dates):
        items = []
        for page in range(1, MAX_FETCH_PAGES + 1):
            body = self._call("realm2", {
                "PageNo": page,
                "numOfrows": FETCH_PAGE_SIZE,
                "realmCode": EXHIBITION_REALM,
                field: keyword,
                "from": dates[0],
                "to": dates[1],
            })
            found = [_item(element, LIST_FIELDS) for element in body.iter("item")]
            items.extend(item for item in found if item["seq"] and item["title"])
            total = _to_int(body.findtext("totalCount"), len(found))
            if page * FETCH_PAGE_SIZE >= total or not found:
                return items, False
        return items, True

    def detail(self, seq):
        seq = str(seq or "").strip()
        if not seq.isdigit() or len(seq) > 20:
            raise InvalidSearch("전시 정보 번호가 올바르지 않습니다.")
        body = self._call("detail2", {"seq": seq})
        element = next(body.iter("item"), None)
        if element is None:
            raise PublicDataError("전시 상세 정보를 찾지 못했습니다.")
        item = _item(element, DETAIL_FIELDS)
        item["contents1"] = _plain_text(item["contents1"])
        return item

    def _call(self, operation, params):
        if not self.configured:
            raise PublicDataError("공공데이터 서비스 키가 설정되지 않았습니다.")
        # Repeated lookups (switching filters, reopening the dialog) are served
        # from a short-lived cache: the service allows only a few requests per
        # second, and every search costs two of them.
        cache_key = (operation, tuple(sorted((key, str(value)) for key, value in params.items())))
        cached = self._cache_get(cache_key)
        if cached is not None:
            return _parse_body(cached)
        query = urlencode({"serviceKey": self.service_key, **params})
        request = Request(f"{BASE_URL}/{operation}?{query}", headers={"Accept": "application/xml"})
        for attempt in range(RETRY_ATTEMPTS):
            try:
                with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    payload = response.read()
                break
            except HTTPError as error:
                payload = error.read()
                if error.code == 429 and attempt + 1 < RETRY_ATTEMPTS:
                    time.sleep(RETRY_DELAY_SECONDS)  # per-second limit: wait and retry once
                    continue
                _raise_gateway_error(payload)
                raise PublicDataError("공공데이터 서비스가 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.") from None
            except (URLError, TimeoutError, OSError):
                raise PublicDataError("공공데이터 서비스에 연결하지 못했습니다. 네트워크를 확인해 주세요.") from None
        body = _parse_body(payload)  # raises on API-level errors, which are not cached
        self._cache_put(cache_key, payload)
        return body

    def _cache_get(self, key):
        with self._lock:
            entry = self._cache.get(key)
            if entry and time.monotonic() - entry[0] < CACHE_SECONDS:
                return entry[1]
            self._cache.pop(key, None)
            return None

    def _cache_put(self, key, payload):
        with self._lock:
            if len(self._cache) >= CACHE_MAX_ENTRIES:
                oldest = min(self._cache, key=lambda item: self._cache[item][0])
                self._cache.pop(oldest, None)
            self._cache[key] = (time.monotonic(), payload)


def _parse_body(payload):
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        _raise_json_error(payload)
        raise PublicDataError("공공데이터 응답을 해석하지 못했습니다.") from None
    _raise_gateway_error(payload, root)
    code = (root.findtext("./header/resultCode") or root.findtext("./header/reseultCode") or "00").strip()
    if code not in {"00", "0", "000"}:
        message = (root.findtext("./header/resultMsg") or "").strip()
        raise PublicDataError(f"공공데이터 조회에 실패했습니다. ({code} {message})".replace(" )", ")"))
    body = root.find("body")
    return body if body is not None else root


def _raise_gateway_error(payload, root=None):
    """data.go.kr gateway errors use <OpenAPI_ServiceResponse><cmmMsgHeader>."""
    if root is None:
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            return
    header = root.find("cmmMsgHeader")
    if header is None:
        return
    error = (header.findtext("errMsg") or "").strip()
    reason = (header.findtext("returnAuthMsg") or "").strip()
    if error in KEY_ERRORS or reason in KEY_ERRORS:
        raise PublicDataError(
            "공공데이터 서비스 키가 유효하지 않습니다. data.go.kr 활용 신청 승인 여부와 .env의 키를 확인해 주세요. "
            "(승인 직후에는 반영까지 1시간 정도 걸릴 수 있습니다.)"
        )
    if "PER_SECOND" in error or "초당" in reason:
        raise PublicDataError("공공데이터에 요청이 몰려 잠시 제한되었어요. 몇 초 뒤에 다시 검색해 주세요.")
    if "LIMITED_NUMBER" in error or "LIMITED_NUMBER" in reason:
        raise PublicDataError("공공데이터 일일 조회 한도를 초과했습니다. 내일 다시 시도하거나 직접 입력해 주세요.")
    raise PublicDataError(f"공공데이터 조회에 실패했습니다. ({reason or error})")


def _raise_json_error(payload):
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return
    header = (data.get("response") or {}).get("header") or {}
    if header.get("resultCode") not in (None, "00"):
        raise PublicDataError(f"공공데이터 조회에 실패했습니다. ({header.get('resultMsg', '')})")


def _item(element, fields):
    # Titles come HTML-escaped inside the XML (e.g. "뮤지엄&times;만나다").
    item = {field: html.unescape(element.findtext(field) or "").strip() for field in fields}
    for field in IMAGE_FIELDS:
        if field in item:
            item[field] = _image_url(item[field])
    return item


def _image_url(url):
    """culture.go.kr serves posters over https and only redirects plain http."""
    if url.startswith("http://www.culture.go.kr/") or url.startswith("http://culture.go.kr/"):
        return "https://" + url[len("http://"):]
    return url if url.startswith(("https://", "http://")) else ""


def _years_from(day, years):
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def exhibition_status(item, today):
    """running / upcoming / past relative to today (YYYYMMDD)."""
    if item.get("endDate") and item["endDate"] < today:
        return "past"
    if item.get("startDate") and item["startDate"] > today:
        return "upcoming"
    return "running"


def sort_exhibitions(items, sort, status="all"):
    if sort == "title":
        return sorted(items, key=lambda item: (_title_key(item["title"]), item["startDate"]))
    if sort == "ending":
        if status == "past":
            # Already ended: the most recently ended first.
            return sorted(items, key=lambda item: (item["endDate"], item["startDate"]), reverse=True)
        # Soonest-ending first; exhibitions without an end date go last.
        return sorted(items, key=lambda item: (item["endDate"] or "99999999", item["startDate"]))
    # latest: most recently started first
    return sorted(items, key=lambda item: (item["startDate"] or "00000000", item["endDate"]), reverse=True)


def _title_key(title):
    # Ignore leading quotes/brackets such as 《 》 「 」 ' " so names sort by their text.
    return re.sub(r"^[\s《〈「『\[\(<'\"“‘]+", "", title).casefold()


def _to_int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _plain_text(value):
    """contents1 may contain HTML markup; keep readable text only."""
    if not value:
        return ""
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", value)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\r", "")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()

import io
import json
import unittest
from datetime import date
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import public_data
import server

LIST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
<body><totalCount>12</totalCount><PageNo>1</PageNo><numOfrows>10</numOfrows><items>
<item><seq>250002</seq><title>토비아스 레베르거: 나 자신 뿐이다</title><startDate>20180616</startDate>
<endDate>20251231</endDate><place>부산현대미술관</place><realmName>전시</realmName><area>부산</area>
<sigungu>사하구</sigungu><thumbnail>http://www.culture.go.kr/upload/a.jpg</thumbnail></item>
<item><seq>250003</seq><title>빛의 층위 「산수&amp;middot;격물」</title><startDate>20260901</startDate><endDate>20261130</endDate>
<place>한강아트센터</place><area>서울</area></item>
</items></body></response>""".encode("utf-8")

DETAIL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><header><resultCode>00</resultCode></header><body><items><item>
<seq>250002</seq><title>토비아스 레베르거</title><place>부산현대미술관</place>
<contents1>&lt;p&gt;첫 문단&amp;nbsp;입니다&lt;/p&gt;&lt;p&gt;둘째&lt;br&gt;줄&lt;/p&gt;</contents1>
<placeAddr>부산광역시 사하구 낙동남로 1191</placeAddr><price>무료</price></item></items></body></response>""".encode("utf-8")

EMPTY_XML = """<response><header><resultCode>00</resultCode></header>
<body><totalCount>0</totalCount><items></items></body></response>""".encode("utf-8")

KEY_ERROR_XML = """<OpenAPI_ServiceResponse><cmmMsgHeader>
<errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg><returnAuthMsg>등록되지 않은 서비스키</returnAuthMsg>
<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>""".encode("utf-8")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ExhibitionSearchTests(unittest.TestCase):
    def setUp(self):
        # data.go.kr's "Encoding" key is URL-encoded; it must be decoded once.
        self.client = public_data.ExhibitionSearch("abc%2Bdef%3D%3D")

    def _respond(self, by_field):
        """Fake urlopen answering per search field ("keyword" or "place")."""
        def fake(request, timeout=None):
            query = parse_qs(urlsplit(request.full_url).query)
            field = "keyword" if "keyword" in query else "place"
            payload = by_field[field]
            if isinstance(payload, Exception):
                raise payload
            return FakeResponse(payload)
        return fake

    TODAY = date(2026, 11, 12)

    def _xml(self, rows, total=None):
        items = "".join(
            f"<item><seq>{seq}</seq><title>{title}</title><startDate>{start}</startDate><endDate>{end}</endDate></item>"
            for seq, title, start, end in rows
        )
        count = len(rows) if total is None else total
        return f"""<response><header><resultCode>00</resultCode></header><body><totalCount>{count}</totalCount>
<items>{items}</items></body></response>""".encode("utf-8")

    def test_search_queries_title_and_venue_over_whole_history(self):
        with patch.object(public_data, "urlopen", side_effect=self._respond({"keyword": LIST_XML, "place": EMPTY_XML})) as opened:
            result = self.client.search("  미술관 ", status="all", today=self.TODAY)
        queries = [parse_qs(urlsplit(call.args[0].full_url).query) for call in opened.call_args_list]
        self.assertEqual(len(queries), 2)
        for call in opened.call_args_list:
            self.assertTrue(call.args[0].full_url.startswith("https://apis.data.go.kr/B553457/cultureinfo/realm2?"))
        self.assertEqual(sorted("keyword" if "keyword" in q else "place" for q in queries), ["keyword", "place"])
        for query in queries:
            self.assertEqual(query["serviceKey"], ["abc+def=="])
            self.assertEqual(query["realmCode"], ["D000"])
            self.assertEqual(query.get("keyword", query.get("place")), ["미술관"])
            self.assertEqual(query["numOfrows"], ["100"])
            self.assertEqual(query["from"], ["20000101"])
            self.assertEqual(query["to"], ["20291112"])
        self.assertEqual({item["title"] for item in result["items"]}, {"토비아스 레베르거: 나 자신 뿐이다", "빛의 층위 「산수·격물」"})
        by_seq = {item["seq"]: item for item in result["items"]}
        self.assertEqual(by_seq["250003"]["thumbnail"], "")
        # culture.go.kr posters are upgraded to https (plain http only redirects).
        self.assertEqual(by_seq["250002"]["thumbnail"], "https://www.culture.go.kr/upload/a.jpg")

    def test_venue_name_finds_exhibitions_and_duplicates_are_merged(self):
        with patch.object(public_data, "urlopen", side_effect=self._respond({"keyword": EMPTY_XML, "place": LIST_XML})):
            venue_only = self.client.search("북서울미술관", status="all", today=self.TODAY)
        self.assertEqual(len(venue_only["items"]), 2)
        with patch.object(public_data, "urlopen", side_effect=self._respond({"keyword": LIST_XML, "place": LIST_XML})):
            both = public_data.ExhibitionSearch("k").search("빛", status="all", today=self.TODAY)
        self.assertEqual(sorted(item["seq"] for item in both["items"]), ["250002", "250003"])
        self.assertEqual(both["total"], 2)

    STATUS_ROWS = [
        ("1", "지난 전시", "20240101", "20240301"),
        ("2", "오늘 끝나는 전시", "20260101", "20261112"),
        ("3", "다음 달 전시", "20261201", "20270301"),
        ("4", "상설전", "20200101", ""),
        ("5", "오늘 시작", "20261112", "20261231"),
    ]

    def _status_search(self, status, sort="latest"):
        client = public_data.ExhibitionSearch("k")
        xml = self._xml(self.STATUS_ROWS)
        with patch.object(public_data, "urlopen", side_effect=self._respond({"keyword": xml, "place": EMPTY_XML})):
            return client.search("전시", status=status, sort=sort, today=self.TODAY)

    def test_status_filter_and_counts(self):
        result = self._status_search("running")
        self.assertEqual(result["counts"], {"running": 3, "upcoming": 1, "past": 1, "all": 5})
        self.assertEqual({item["seq"] for item in result["items"]}, {"2", "4", "5"})
        self.assertEqual([item["seq"] for item in self._status_search("upcoming")["items"]], ["3"])
        self.assertEqual([item["seq"] for item in self._status_search("past")["items"]], ["1"])
        self.assertEqual(len(self._status_search("all")["items"]), 5)
        self.assertEqual(self._status_search("past")["items"][0]["status"], "past")

    def test_sort_orders(self):
        latest = [item["seq"] for item in self._status_search("all", "latest")["items"]]
        self.assertEqual(latest, ["3", "5", "2", "1", "4"])  # most recently started first
        ending = [item["seq"] for item in self._status_search("all", "ending")["items"]]
        self.assertEqual(ending, ["1", "2", "5", "3", "4"])  # soonest end first, no end date last
        past_rows = [("a", "2015년 종료", "20150101", "20150301"), ("b", "어제 종료", "20260101", "20261111"),
                     ("c", "2024년 종료", "20240101", "20240601")]
        xml = self._xml(past_rows)
        with patch.object(public_data, "urlopen", side_effect=self._respond({"keyword": xml, "place": EMPTY_XML})):
            past = public_data.ExhibitionSearch("k").search("전시", status="past", sort="ending", today=self.TODAY)
        self.assertEqual([item["seq"] for item in past["items"]], ["b", "c", "a"])  # past: most recently ended first
        client = public_data.ExhibitionSearch("k")
        xml = self._xml([("1", "하늘", "20260101", "20261231"), ("2", "《가나》", "20260101", "20261231"),
                         ("3", "나무", "20260101", "20261231")])
        with patch.object(public_data, "urlopen", side_effect=self._respond({"keyword": xml, "place": EMPTY_XML})):
            titled = client.search("전시", status="all", sort="title", today=self.TODAY)
        self.assertEqual([item["title"] for item in titled["items"]], ["《가나》", "나무", "하늘"])

    def test_results_are_paged_20_at_a_time(self):
        rows = [(str(i), f"전시 {i:02d}", "20260101", "20261231") for i in range(45)]
        client = public_data.ExhibitionSearch("k")
        xml = self._xml(rows)
        with patch.object(public_data, "urlopen", side_effect=self._respond({"keyword": xml, "place": EMPTY_XML})) as opened:
            first = client.search("전시", status="running", sort="title", today=self.TODAY)
            third = client.search("전시", page=3, status="running", sort="title", today=self.TODAY)
        self.assertEqual((len(first["items"]), first["total"], first["hasMore"]), (20, 45, True))
        self.assertEqual((len(third["items"]), third["hasMore"]), (5, False))
        self.assertEqual(third["items"][-1]["title"], "전시 44")
        self.assertEqual(opened.call_count, 2)  # later pages come from the cached API results

    def test_large_results_fetch_more_api_pages_and_flag_truncation(self):
        page_xml = self._xml([("1", "전시", "20260101", "20261231")], total=350)
        with patch.object(public_data, "urlopen", side_effect=self._respond({"keyword": page_xml, "place": EMPTY_XML})) as opened:
            result = public_data.ExhibitionSearch("k").search("서울", status="all", today=self.TODAY)
        pages = sorted(parse_qs(urlsplit(call.args[0].full_url).query)["PageNo"][0]
                       for call in opened.call_args_list if "keyword" in call.args[0].full_url)
        self.assertEqual(pages, ["1", "2", "3"])
        self.assertTrue(result["truncated"])
        small = self._xml([("1", "전시", "20260101", "20261231")], total=150)
        with patch.object(public_data, "urlopen", side_effect=self._respond({"keyword": small, "place": EMPTY_XML})):
            result = public_data.ExhibitionSearch("k").search("서울", status="all", today=self.TODAY)
        self.assertFalse(result["truncated"])

    def test_rejects_unknown_status_or_sort(self):
        with patch.object(public_data, "urlopen") as opened:
            for kwargs in ({"status": "forever"}, {"sort": "random"}):
                with self.assertRaises(public_data.PublicDataError):
                    self.client.search("미술관", **kwargs)
        opened.assert_not_called()

    def test_one_failing_search_still_returns_the_other(self):
        error = HTTPError("u", 500, "err", {}, io.BytesIO(b""))
        with patch.object(public_data, "urlopen", side_effect=self._respond({"keyword": error, "place": LIST_XML})):
            result = self.client.search("북서울", status="all", today=self.TODAY)
        self.assertEqual(len(result["items"]), 2)
        fresh = public_data.ExhibitionSearch("k")  # no cached results
        with patch.object(public_data, "urlopen", side_effect=self._respond({"keyword": error, "place": error})):
            with self.assertRaises(public_data.PublicDataError):
                fresh.search("북서울")

    def test_changing_status_or_sort_reuses_cached_results(self):
        with patch.object(public_data, "urlopen", side_effect=lambda *a, **k: FakeResponse(LIST_XML)) as opened:
            self.client.search("북서울", today=self.TODAY)
            self.assertEqual(opened.call_count, 2)  # title + venue
            self.client.search("북서울", status="past", sort="title", today=self.TODAY)
            self.client.search("북서울", status="all", sort="ending", today=self.TODAY)
            self.assertEqual(opened.call_count, 2)
            self.client.search("다른 검색어", today=self.TODAY)
            self.assertEqual(opened.call_count, 4)

    def test_per_second_limit_is_retried_once(self):
        limited = """<OpenAPI_ServiceResponse><cmmMsgHeader>
<errMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR</errMsg>
<returnAuthMsg>초당 서비스 요청제한 횟수 초과 에러</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>""".encode("utf-8")
        calls = []

        def flaky(request, timeout=None):
            calls.append(request)
            if len(calls) == 1:
                raise HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(limited))
            return FakeResponse(DETAIL_XML)

        with patch.object(public_data, "urlopen", side_effect=flaky), patch.object(public_data.time, "sleep") as slept:
            item = self.client.detail("250002")
        self.assertEqual(item["seq"], "250002")
        slept.assert_called_once()

        def always_limited(request, timeout=None):
            raise HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(limited))

        with patch.object(public_data, "urlopen", side_effect=always_limited), patch.object(public_data.time, "sleep"):
            with self.assertRaises(public_data.PublicDataError) as raised:
                public_data.ExhibitionSearch("k").detail("1")
        self.assertIn("잠시", str(raised.exception))
        self.assertNotIn("일일", str(raised.exception))

    def test_detail_strips_html_from_contents(self):
        with patch.object(public_data, "urlopen", return_value=FakeResponse(DETAIL_XML)) as opened:
            item = self.client.detail("250002")
        self.assertIn("/detail2?", opened.call_args.args[0].full_url)
        self.assertEqual(item["contents1"], "첫 문단 입니다\n둘째\n줄")
        self.assertEqual(item["placeAddr"], "부산광역시 사하구 낙동남로 1191")

    def test_invalid_key_gives_actionable_message(self):
        error = HTTPError("u", 403, "Forbidden", {}, io.BytesIO(KEY_ERROR_XML))
        with patch.object(public_data, "urlopen", side_effect=error):
            with self.assertRaises(public_data.PublicDataError) as raised:
                self.client.search("전시")
        self.assertIn("서비스 키", str(raised.exception))

    def test_rejects_bad_input_without_calling_api(self):
        with patch.object(public_data, "urlopen") as opened:
            for call in (lambda: self.client.search(" "), lambda: self.client.search("가" * 51),
                         lambda: self.client.detail("12;drop")):
                with self.assertRaises(public_data.PublicDataError):
                    call()
        opened.assert_not_called()

    def test_unconfigured_search_reports_not_configured(self):
        self.assertFalse(public_data.ExhibitionSearch("").configured)


class ExhibitionEndpointTests(unittest.TestCase):
    def _get(self, path):
        from threading import Thread
        from urllib.request import urlopen
        http_server = server.LocalhostServer(("127.0.0.1", 0), server.JournalHandler)
        thread = Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        try:
            try:
                with urlopen(f"http://127.0.0.1:{http_server.server_port}{path}") as response:
                    return response.status, json.loads(response.read())
            except HTTPError as error:
                body = json.loads(error.read())
                error.close()
                return error.code, body
        finally:
            http_server.shutdown()
            http_server.server_close()
            thread.join(timeout=2)

    def test_search_requires_login(self):
        status, _ = self._get("/api/exhibitions?q=x")
        self.assertEqual(status, 401)

    def test_search_without_key_reports_not_configured(self):
        session = {"google": {"email": "a@example.com", "access_token": "t", "expires_at": 9e12}}
        with patch.object(server, "_session_for", return_value=("sid", session)), \
                patch.object(server, "EXHIBITION_SEARCH", public_data.ExhibitionSearch("")):
            status, body = self._get("/api/exhibitions?q=x")
        self.assertEqual(status, 200)
        self.assertFalse(body["configured"])

    def test_search_with_key_proxies_results(self):
        session = {"google": {"email": "a@example.com", "access_token": "t", "expires_at": 9e12}}
        with patch.object(server, "_session_for", return_value=("sid", session)), \
                patch.object(server, "EXHIBITION_SEARCH", public_data.ExhibitionSearch("k")), \
                patch.object(public_data, "urlopen", side_effect=lambda *a, **k: FakeResponse(LIST_XML)):
            status, body = self._get("/api/exhibitions?q=%EB%B9%9B&page=1&status=all&sort=title")
            default_status, default_body = self._get("/api/exhibitions?q=%EB%B9%9B")
            bad_status, _ = self._get("/api/exhibitions?q=%EB%B9%9B&sort=random")
        self.assertEqual(status, 200)
        self.assertTrue(body["configured"])
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual((body["status"], body["sort"]), ("all", "title"))
        # defaults: 전시중 + 최신순
        self.assertEqual((default_status, default_body["status"], default_body["sort"]), (200, "running", "latest"))
        self.assertEqual(default_body["counts"]["all"], 2)
        self.assertEqual(bad_status, 400)


if __name__ == "__main__":
    unittest.main()

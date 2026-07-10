"""collector 테스트. 네트워크를 타지 않는다 (RSS는 고정 XML, 지표는 가짜 프레임)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from econ_insta.collector import (
    KST,
    Article,
    CollectError,
    Quote,
    clean_text,
    collect_articles,
    dedupe,
    parse_feed,
    parse_pubdate,
)


def rss(*items: str) -> bytes:
    body = "".join(items)
    return f"<?xml version='1.0' encoding='UTF-8'?><rss><channel>{body}</channel></rss>".encode()


def item(title="제목", link="https://example.com/1", pub="Fri, 10 Jul 2026 15:00:00 +0900", desc=""):
    return (
        f"<item><title>{title}</title><link>{link}</link>"
        f"<pubDate>{pub}</pubDate><description>{desc}</description></item>"
    )


class ParsePubdateTest(unittest.TestCase):
    def test_standard_offset(self):
        parsed = parse_pubdate("Fri, 10 Jul 2026 15:49:32 +0900")
        self.assertEqual(parsed.utcoffset(), timedelta(hours=9))
        self.assertEqual(parsed.hour, 15)

    def test_colon_offset_keeps_timezone(self):
        """매경의 '+09:00'. 표준 파서는 타임존을 조용히 버린다."""
        parsed = parse_pubdate("Fri, 10 Jul 2026 14:48:42 +09:00")
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.utcoffset(), timedelta(hours=9))
        self.assertEqual(parsed.hour, 14)

    def test_utc_converted_to_kst(self):
        parsed = parse_pubdate("Fri, 10 Jul 2026 06:00:00 +0000")
        self.assertEqual(parsed.hour, 15)

    def test_naive_assumed_kst(self):
        parsed = parse_pubdate("Fri, 10 Jul 2026 09:00:00")
        self.assertEqual(parsed.utcoffset(), timedelta(hours=9))

    def test_mixed_offsets_are_sortable(self):
        """aware/naive가 섞이면 정렬에서 TypeError가 난다. 그 회귀를 막는다."""
        stamps = [
            parse_pubdate("Fri, 10 Jul 2026 15:49:32 +0900"),
            parse_pubdate("Fri, 10 Jul 2026 14:48:42 +09:00"),
        ]
        self.assertEqual(sorted(stamps)[0].hour, 14)

    def test_empty_raises(self):
        with self.assertRaises(CollectError):
            parse_pubdate("")

    def test_garbage_raises(self):
        with self.assertRaises(CollectError):
            parse_pubdate("어제쯤")


class CleanTextTest(unittest.TestCase):
    def test_strips_tags_and_entities(self):
        self.assertEqual(clean_text("<p>코스피 &amp; 코스닥</p>"), "코스피 & 코스닥")

    def test_collapses_whitespace(self):
        self.assertEqual(clean_text("가\n\n  나\t다"), "가 나 다")

    def test_empty(self):
        self.assertEqual(clean_text(""), "")


class ParseFeedTest(unittest.TestCase):
    def test_parses_items(self):
        articles = parse_feed("연합뉴스", rss(item(title="코스피 상승", desc="3% 올랐다")))
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].source, "연합뉴스")
        self.assertEqual(articles[0].title, "코스피 상승")
        self.assertEqual(articles[0].summary, "3% 올랐다")

    def test_cdata_description(self):
        """연합·매경이 실제로 쓰는 형식."""
        articles = parse_feed("매일경제", rss(item(desc="<![CDATA[코스피가 3% 올랐다]]>")))
        self.assertEqual(articles[0].summary, "코스피가 3% 올랐다")

    def test_missing_description(self):
        """한국경제는 item에 description을 아예 넣지 않는다."""
        feed = rss(
            "<item><title>제목</title><link>https://a</link>"
            "<pubDate>Fri, 10 Jul 2026 15:00:00 +0900</pubDate></item>"
        )
        articles = parse_feed("한국경제", feed)
        self.assertEqual(articles[0].summary, "")

    def test_description_with_child_tags(self):
        """findtext()는 자식 엘리먼트가 끼면 앞부분만 돌려준다. _text()가 이를 막는다."""
        articles = parse_feed("연합뉴스", rss(item(desc="<b>3%</b> 올랐다")))
        self.assertEqual(articles[0].summary, "3% 올랐다")

    def test_escaped_html_description(self):
        articles = parse_feed("연합뉴스", rss(item(desc="&lt;b&gt;3%&lt;/b&gt; 올랐다")))
        self.assertEqual(articles[0].summary, "3% 올랐다")

    def test_skips_item_without_link(self):
        articles = parse_feed("한국경제", rss(item(link="")))
        self.assertEqual(articles, [])

    def test_skips_item_with_bad_pubdate(self):
        """항목 하나가 깨져도 나머지는 살아야 한다."""
        feed = rss(item(title="정상", link="https://a"), item(title="깨짐", link="https://b", pub="어제"))
        articles = parse_feed("매일경제", feed)
        self.assertEqual([a.title for a in articles], ["정상"])

    def test_summary_truncated(self):
        articles = parse_feed("연합뉴스", rss(item(desc="가" * 500)))
        self.assertEqual(len(articles[0].summary), 300)

    def test_invalid_xml_raises(self):
        with self.assertRaises(CollectError):
            parse_feed("연합뉴스", b"<rss><channel>")


def make_article(title, source="연합뉴스", minutes_ago=0):
    return Article(
        source=source,
        title=title,
        link=f"https://example.com/{abs(hash(title))}",
        published=datetime(2026, 7, 10, 15, 0, tzinfo=KST) - timedelta(minutes=minutes_ago),
    )


class DedupeTest(unittest.TestCase):
    def test_removes_exact_duplicate(self):
        articles = [make_article("코스피 상승"), make_article("코스피 상승", source="한국경제")]
        self.assertEqual(len(dedupe(articles)), 1)

    def test_ignores_bracket_prefix(self):
        """[특징주] 말머리만 다른 재게재 기사."""
        articles = [make_article("[특징주] 삼성전자 강세"), make_article("삼성전자 강세")]
        self.assertEqual(len(dedupe(articles)), 1)

    def test_keeps_first_occurrence(self):
        articles = [make_article("코스피 상승"), make_article("코스피 상승", source="매일경제")]
        self.assertEqual(dedupe(articles)[0].source, "연합뉴스")

    def test_keeps_distinct(self):
        articles = [make_article("코스피 상승"), make_article("코스닥 하락")]
        self.assertEqual(len(dedupe(articles)), 2)


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


class FakeSession:
    """지정된 URL에만 응답하고, 나머지는 requests 예외를 던진다."""

    def __init__(self, bodies: dict[str, bytes]):
        self.bodies = bodies

    def get(self, url, **_kwargs):
        if url not in self.bodies:
            import requests

            raise requests.RequestException("연결 실패")
        return FakeResponse(self.bodies[url])


class CollectArticlesTest(unittest.TestCase):
    def setUp(self):
        self._real_session = None

    def _patch(self, session):
        import econ_insta.collector as mod

        self._real_session = mod.requests.Session
        mod.requests.Session = lambda: session
        self.addCleanup(setattr, mod.requests, "Session", self._real_session)

    def test_merges_and_sorts_newest_first(self):
        feeds = {"연합뉴스": "https://a", "한국경제": "https://b"}
        self._patch(
            FakeSession(
                {
                    "https://a": rss(item(title="오래된", link="https://x", pub="Fri, 10 Jul 2026 10:00:00 +0900")),
                    "https://b": rss(item(title="최신", link="https://y", pub="Fri, 10 Jul 2026 14:00:00 +0900")),
                }
            )
        )
        # cutoff 계산이 실제 현재 시각을 쓰므로 max_age를 넉넉히 준다.
        articles = collect_articles(max_age_hours=10**6, feeds=feeds)
        self.assertEqual([a.title for a in articles], ["최신", "오래된"])

    def test_failed_feed_recorded_not_raised(self):
        feeds = {"연합뉴스": "https://a", "한국경제": "https://down"}
        self._patch(FakeSession({"https://a": rss(item(title="살아있음", link="https://x"))}))
        errors: list[str] = []
        articles = collect_articles(max_age_hours=10**6, feeds=feeds, errors=errors)
        self.assertEqual([a.title for a in articles], ["살아있음"])
        self.assertEqual(len(errors), 1)
        self.assertIn("한국경제", errors[0])

    def test_failed_feed_raises_without_error_sink(self):
        self._patch(FakeSession({}))
        with self.assertRaises(CollectError):
            collect_articles(feeds={"연합뉴스": "https://down"})

    def test_respects_limit(self):
        items = [item(title=f"기사{i}", link=f"https://x/{i}") for i in range(5)]
        self._patch(FakeSession({"https://a": rss(*items)}))
        articles = collect_articles(max_age_hours=10**6, limit=3, feeds={"연합뉴스": "https://a"})
        self.assertEqual(len(articles), 3)

    def test_drops_stale_articles(self):
        self._patch(FakeSession({"https://a": rss(item(pub="Mon, 01 Jan 2024 00:00:00 +0900"))}))
        articles = collect_articles(max_age_hours=24, feeds={"연합뉴스": "https://a"})
        self.assertEqual(articles, [])


class QuoteFormatTest(unittest.TestCase):
    def test_index_has_no_decimals(self):
        self.assertEqual(Quote("^KS11", "코스피", 7475.94, 2.52).price_text, "7,476")

    def test_fx_has_one_decimal(self):
        self.assertEqual(Quote("KRW=X", "원/달러", 1503.19, -0.01).price_text, "1,503.2")

    def test_change_sign_always_shown(self):
        self.assertEqual(Quote("^KS11", "코스피", 1.0, 2.5).change_text, "+2.50%")
        self.assertEqual(Quote("^KS11", "코스피", 1.0, -2.5).change_text, "-2.50%")


if __name__ == "__main__":
    unittest.main()

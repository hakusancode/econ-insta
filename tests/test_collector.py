"""collector 테스트. 네트워크를 타지 않는다 (RSS는 고정 XML, 지표는 가짜 프레임)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from econ_insta.collector import (
    KST,
    Article,
    CollectError,
    FeedSpec,
    Quote,
    apply_quota,
    clean_text,
    collect_articles,
    dedupe,
    gather_articles,
    is_boilerplate,
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


def make_article(title, source="매일경제", minutes_ago=0):
    return Article(
        source=source,
        title=title,
        link=f"https://example.com/{abs(hash(title))}",
        published=datetime(2026, 7, 10, 15, 0, tzinfo=KST) - timedelta(minutes=minutes_ago),
    )


class IsBoilerplateTest(unittest.TestCase):
    def test_rejects_personnel_tags(self):
        for title in ("[인사] 한국수출입은행", "[동정] 황종우 해수부 장관", "[프로필] 임기근 국무조정실장"):
            self.assertTrue(is_boilerplate(title), title)

    def test_rejects_fx_rate_table(self):
        self.assertTrue(is_boilerplate("외국환시세(7월10일·15:30 기준가)"))

    def test_keeps_stock_movers(self):
        """[특징주]는 시장 소재이므로 남긴다."""
        self.assertFalse(is_boilerplate("[특징주] SK하이닉스 약세마감"))

    def test_keeps_fx_news(self):
        self.assertFalse(is_boilerplate("[외환] 원/달러 환율 4.7원 내린 1,501.4원"))

    def test_keeps_plain_title(self):
        self.assertFalse(is_boilerplate("코스피 2.5% 상승해 7,400대"))

    def test_keeps_english_title(self):
        self.assertFalse(is_boilerplate("China may struggle to fund Xi Jinping's tech dreams"))


class DedupeTest(unittest.TestCase):
    def test_removes_exact_duplicate(self):
        articles = [make_article("코스피 상승"), make_article("코스피 상승", source="한국경제")]
        self.assertEqual(len(dedupe(articles)), 1)

    def test_ignores_bracket_prefix(self):
        """[특징주] 말머리만 다른 재게재 기사."""
        articles = [make_article("[특징주] 삼성전자 강세"), make_article("삼성전자 강세")]
        self.assertEqual(len(dedupe(articles)), 1)

    def test_keeps_first_occurrence(self):
        articles = [make_article("코스피 상승"), make_article("코스피 상승", source="한국경제")]
        self.assertEqual(dedupe(articles)[0].source, "매일경제")

    def test_keeps_distinct(self):
        articles = [make_article("코스피 상승"), make_article("코스닥 하락")]
        self.assertEqual(len(dedupe(articles)), 2)


class ApplyQuotaTest(unittest.TestCase):
    FEEDS = {
        "매일경제": FeedSpec("https://a", quota=2),
        "WSJ": FeedSpec("https://b", language="en", quota=1),
    }

    def test_caps_per_source(self):
        articles = [make_article(f"기사{i}") for i in range(5)]
        self.assertEqual(len(apply_quota(articles, self.FEEDS)), 2)

    def test_each_source_gets_its_own_quota(self):
        articles = [make_article(f"국내{i}") for i in range(4)]
        articles += [make_article(f"해외{i}", source="WSJ") for i in range(4)]
        kept = apply_quota(articles, self.FEEDS)
        self.assertEqual([a.source for a in kept], ["매일경제", "매일경제", "WSJ"])

    def test_keeps_earliest_in_input_order(self):
        """입력이 최신순이면 각 매체의 최신 기사가 남는다."""
        articles = [make_article("최신", minutes_ago=0), make_article("오래된", minutes_ago=99)]
        feeds = {"매일경제": FeedSpec("https://a", quota=1)}
        self.assertEqual(apply_quota(articles, feeds)[0].title, "최신")

    def test_unknown_source_dropped(self):
        self.assertEqual(apply_quota([make_article("x", source="어디선가")], self.FEEDS), [])


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

    # cutoff 계산이 실제 현재 시각을 쓰므로 창을 넉넉히 준다.
    FOREVER = 10**6

    def test_merges_and_sorts_newest_first(self):
        feeds = {
            "매일경제": FeedSpec("https://a", max_age_hours=self.FOREVER),
            "한국경제": FeedSpec("https://b", max_age_hours=self.FOREVER),
        }
        self._patch(
            FakeSession(
                {
                    "https://a": rss(item(title="오래된", link="https://x", pub="Fri, 10 Jul 2026 10:00:00 +0900")),
                    "https://b": rss(item(title="최신", link="https://y", pub="Fri, 10 Jul 2026 14:00:00 +0900")),
                }
            )
        )
        articles = collect_articles(feeds=feeds)
        self.assertEqual([a.title for a in articles], ["최신", "오래된"])

    def test_failed_feed_recorded_not_raised(self):
        feeds = {
            "매일경제": FeedSpec("https://a", max_age_hours=self.FOREVER),
            "한국경제": FeedSpec("https://down", max_age_hours=self.FOREVER),
        }
        self._patch(FakeSession({"https://a": rss(item(title="살아있음", link="https://x"))}))
        errors: list[str] = []
        articles = collect_articles(feeds=feeds, errors=errors)
        self.assertEqual([a.title for a in articles], ["살아있음"])
        self.assertEqual(len(errors), 1)
        self.assertIn("한국경제", errors[0])

    def test_failed_feed_raises_without_error_sink(self):
        self._patch(FakeSession({}))
        with self.assertRaises(CollectError):
            collect_articles(feeds={"매일경제": FeedSpec("https://down")})

    def test_respects_limit(self):
        items = [item(title=f"기사{i}", link=f"https://x/{i}") for i in range(5)]
        self._patch(FakeSession({"https://a": rss(*items)}))
        feeds = {"매일경제": FeedSpec("https://a", max_age_hours=self.FOREVER, quota=5)}
        self.assertEqual(len(collect_articles(limit=3, feeds=feeds)), 3)

    def test_quota_applied_per_source(self):
        items = [item(title=f"기사{i}", link=f"https://x/{i}") for i in range(5)]
        self._patch(FakeSession({"https://a": rss(*items)}))
        feeds = {"매일경제": FeedSpec("https://a", max_age_hours=self.FOREVER, quota=2)}
        self.assertEqual(len(collect_articles(feeds=feeds)), 2)

    def test_boilerplate_filtered_out(self):
        feed = rss(
            item(title="[인사] 한국수출입은행", link="https://x/1"),
            item(title="코스피 급등", link="https://x/2"),
        )
        self._patch(FakeSession({"https://a": feed}))
        feeds = {"매일경제": FeedSpec("https://a", max_age_hours=self.FOREVER)}
        self.assertEqual([a.title for a in collect_articles(feeds=feeds)], ["코스피 급등"])

    def test_language_tagged_from_spec(self):
        self._patch(FakeSession({"https://b": rss(item(title="Stocks rise", link="https://y"))}))
        feeds = {"WSJ": FeedSpec("https://b", language="en", max_age_hours=self.FOREVER)}
        self.assertEqual(collect_articles(feeds=feeds)[0].language, "en")

    def test_per_source_age_window(self):
        """주간지는 창이 넓어 살아남고, 일간지는 같은 기사가 잘려나간다."""
        from email.utils import format_datetime

        from econ_insta.collector import now_kst

        # 고정 날짜를 쓰면 시간이 지나며 테스트가 썩는다. 현재 시각 기준으로 만든다.
        two_days_ago = format_datetime(now_kst() - timedelta(hours=48))
        old = rss(item(title="주간 기사", link="https://z", pub=two_days_ago))
        self._patch(FakeSession({"https://a": old, "https://b": old}))

        weekly = {"The Economist": FeedSpec("https://a", max_age_hours=72)}
        daily = {"매일경제": FeedSpec("https://b", max_age_hours=24)}
        self.assertEqual(len(collect_articles(feeds=weekly)), 1)
        self.assertEqual(len(collect_articles(feeds=daily)), 0)

    def test_drops_stale_articles(self):
        self._patch(FakeSession({"https://a": rss(item(pub="Mon, 01 Jan 2024 00:00:00 +0900"))}))
        self.assertEqual(collect_articles(feeds={"매일경제": FeedSpec("https://a")}), [])

    def test_gather는_매체별_상한을_적용하지_않는다(self):
        """quota의 원래 목적(독식 방지)은 rank_issues의 크로스소스 점수가 대신한다.

        gather는 모으기만 한다 — 버리는 것은 랭킹 뒤에서 한다(스펙 §4.1).
        """
        items = [item(title=f"기사{i}", link=f"https://x/{i}") for i in range(5)]
        self._patch(FakeSession({"https://a": rss(*items)}))
        feeds = {"매일경제": FeedSpec("https://a", max_age_hours=self.FOREVER, quota=2)}
        self.assertEqual(len(gather_articles(feeds=feeds)), 5)

    def test_gather는_신선도와_정형기사_필터는_유지한다(self):
        """quota만 빠진다. 컷오프와 보일러플레이트 필터는 gather의 일이다."""
        from email.utils import format_datetime

        from econ_insta.collector import now_kst

        # item()의 고정 pub 기본값은 시간이 지나며 썩는다(line 312 참고) — 여기서는
        # 기본 max_age_hours=24 창을 검증해야 하므로 "지금"을 기준으로 신선하게 만든다.
        recent = format_datetime(now_kst() - timedelta(hours=1))
        feed = rss(
            item(title="[인사] 한국수출입은행", link="https://x/1", pub=recent),
            item(title="코스피 급등", link="https://x/2", pub=recent),
            item(title="작년 기사", link="https://x/3", pub="Mon, 01 Jan 2024 00:00:00 +0900"),
        )
        self._patch(FakeSession({"https://a": feed}))
        feeds = {"매일경제": FeedSpec("https://a", quota=99)}
        self.assertEqual([a.title for a in gather_articles(feeds=feeds)], ["코스피 급등"])


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

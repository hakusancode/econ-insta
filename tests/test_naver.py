"""naver.py — 인기도 신호 파싱·재정렬·저하 경로. 전부 가짜 세션, 네트워크 없음."""

from __future__ import annotations

import os
import unittest
from datetime import datetime
from unittest.mock import patch

import requests

from econ_insta import naver
from econ_insta.collector import Article
from econ_insta.issues import Issue

KEYS = {"NAVER_CLIENT_ID": "id", "NAVER_CLIENT_SECRET": "secret"}


def art(title: str, source: str = "연합뉴스") -> Article:
    return Article(
        source=source, title=title, link="https://example.com/a",
        published=datetime(2026, 7, 21), summary="",
    )


def issue(*titles: str) -> Issue:
    return Issue(articles=[art(t) for t in titles])


class FakeResponse:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._body


class FakeSession:
    """URL별 준비된 응답을 돌려준다. 예외 객체면 던진다."""

    def __init__(self, news=None, datalab=None):
        self.news, self.datalab = news, datalab
        self.news_queries: list[str] = []

    def get(self, url, params=None, **kwargs):
        self.news_queries.append(params["query"])
        if isinstance(self.news, Exception):
            raise self.news
        return self.news

    def post(self, url, json=None, **kwargs):
        if isinstance(self.datalab, Exception):
            raise self.datalab
        return self.datalab


def news_body(total, hosts):
    return {"total": total, "items": [{"originallink": f"https://{h}/x"} for h in hosts]}


def datalab_body(**ratios):
    return {"results": [
        {"title": kw, "data": [{"period": "2026-07-20", "ratio": r}]}
        for kw, r in ratios.items()
    ]}


class NewsSignalTest(unittest.TestCase):
    @patch.dict(os.environ, KEYS)
    def test_매체는_www를_벗긴_도메인으로_센다(self):
        session = FakeSession(news=FakeResponse(news_body(
            120, ["www.yna.co.kr", "yna.co.kr", "hankyung.com"])))
        signal = naver.news_signal("금리", session=session)
        self.assertEqual(signal.total, 120)
        self.assertEqual(signal.sources, 2)

    @patch.dict(os.environ, KEYS)
    def test_http_오류는_NaverError(self):
        session = FakeSession(news=FakeResponse({}, status=500))
        with self.assertRaises(naver.NaverError):
            naver.news_signal("금리", session=session)


class TrendTest(unittest.TestCase):
    @patch.dict(os.environ, KEYS)
    def test_응답에_없는_키워드는_0점(self):
        session = FakeSession(datalab=FakeResponse(datalab_body(하이닉스=80.0)))
        scores = naver.trend_scores(["하이닉스", "무명키워드"], session=session)
        self.assertEqual(scores["하이닉스"], 80.0)
        self.assertEqual(scores["무명키워드"], 0.0)


class RerankTest(unittest.TestCase):
    @patch.dict(os.environ, {"NAVER_CLIENT_ID": "", "NAVER_CLIENT_SECRET": ""})
    def test_키가_없으면_원본_순서_그대로(self):
        issues = [issue("A"), issue("B")]
        self.assertEqual(naver.rerank(issues, session=FakeSession()), issues)

    @patch.dict(os.environ, KEYS)
    def test_네이버_매체수가_큰_이슈가_앞으로_온다(self):
        first, second = issue("조용한 발표"), issue("반도체 폭락 사태")
        responses = iter([
            FakeResponse(news_body(10, ["a.com"])),                      # first
            FakeResponse(news_body(900, ["a.com", "b.com", "c.com"])),   # second
        ])
        session = FakeSession(datalab=FakeResponse(datalab_body()))
        session.get = lambda url, params=None, **k: next(responses)
        self.assertEqual(naver.rerank([first, second], session=session), [second, first])

    @patch.dict(os.environ, KEYS)
    def test_뉴스검색_실패면_기존_랭킹_유지(self):
        issues = [issue("A"), issue("B")]
        session = FakeSession(news=requests.ConnectionError("down"))
        self.assertEqual(naver.rerank(issues, session=session), issues)

    @patch.dict(os.environ, KEYS)
    def test_데이터랩만_실패하면_뉴스신호로_계속(self):
        first, second = issue("조용한 발표"), issue("반도체 폭락 사태")
        responses = iter([
            FakeResponse(news_body(10, ["a.com"])),
            FakeResponse(news_body(900, ["a.com", "b.com", "c.com"])),
        ])
        session = FakeSession(datalab=requests.ConnectionError("down"))
        session.get = lambda url, params=None, **k: next(responses)
        self.assertEqual(naver.rerank([first, second], session=session), [second, first])


class KeywordTest(unittest.TestCase):
    def test_클러스터_최빈_핵심어를_뽑는다(self):
        # keywords()는 한글·영문 런을 따로 토큰화한다("SK하이닉스" → sk + 하이닉스).
        # 동률(sk 2회, 하이닉스 2회)에선 긴 쪽이 검색어답다.
        one = issue("SK하이닉스 폭락 지속", "SK하이닉스 반등 실패", "반도체 급락")
        self.assertEqual(naver.issue_keyword(one), "하이닉스")


if __name__ == "__main__":
    unittest.main()

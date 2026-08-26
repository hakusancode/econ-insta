"""daily.briefing_meta / render_edition의 briefing.json 배선 테스트.

국내 RSS는 D-2면 기사가 증발한다 — 주간 릴스가 소재로 쓸 이슈 메타데이터를
데일리가 매일 남겨야 한다(스펙 §4). "가드가 있다 ≠ 작동한다" — briefing_meta가
있어도 render_edition이 실제로 파일을 쓰는지는 별도로 확인해야 한다.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from econ_insta import daily

WHEN = datetime(2026, 8, 26, 19, 0)
KST = timezone(timedelta(hours=9))


def _briefing(issue=None):
    card = SimpleNamespace(title="금리 동결", source="매일경제·연합뉴스")
    return SimpleNamespace(headline="한은, 숨 고르기", cards=[card], issue=issue)


def _issue():
    articles = [
        SimpleNamespace(
            title="한은 기준금리 동결", source="매일경제",
            link="https://mk.co.kr/1", published=datetime(2026, 8, 25, 9, 0, tzinfo=KST),
            images=["https://img.mk.co.kr/1.jpg"],
        ),
        SimpleNamespace(
            title="금통위 동결 배경", source="연합뉴스",
            link="https://yna.co.kr/2", published=datetime(2026, 8, 25, 10, 0, tzinfo=KST),
            images=[],
        ),
    ]
    return SimpleNamespace(articles=articles, sources={"매일경제", "연합뉴스"})


class BriefingMetaTest(unittest.TestCase):
    def test_이슈가_있으면_전부_실린다(self):
        meta = daily.briefing_meta(_briefing(_issue()), daily.EDITIONS["kr"], WHEN)
        self.assertEqual(meta["date"], "2026-08-26")
        self.assertEqual(meta["edition"], "kr")
        self.assertEqual(meta["headline"], "한은, 숨 고르기")
        self.assertEqual(meta["issue_title"], "한은 기준금리 동결")
        self.assertEqual(meta["sources"], ["매일경제", "연합뉴스"])
        self.assertEqual(meta["article_count"], 2)
        self.assertEqual(meta["cards"], [{"title": "금리 동결", "source": "매일경제·연합뉴스"}])

    def test_이슈의_기사가_이미지와_함께_실린다(self):
        """주간 릴스가 article-photo 체인을 돌리려면 briefing.json에 기사 원문 정보가
        필요하다(제목·출처·링크·발행일·이미지 URL). 이 키가 빠지면 weekly_reel이
        Article을 복원할 수 없어 issue=None으로 떨어져 기사 사진 체인이 죽는다."""
        meta = daily.briefing_meta(_briefing(_issue()), daily.EDITIONS["kr"], WHEN)
        self.assertEqual(meta["articles"], [
            {
                "title": "한은 기준금리 동결", "source": "매일경제",
                "link": "https://mk.co.kr/1", "published": "2026-08-25T09:00:00+09:00",
                "images": ["https://img.mk.co.kr/1.jpg"],
            },
            {
                "title": "금통위 동결 배경", "source": "연합뉴스",
                "link": "https://yna.co.kr/2", "published": "2026-08-25T10:00:00+09:00",
                "images": [],
            },
        ])

    def test_이슈가_None이어도_메타는_만들어진다(self):
        meta = daily.briefing_meta(_briefing(None), daily.EDITIONS["kr"], WHEN)
        self.assertIsNone(meta["issue_title"])
        self.assertEqual(meta["sources"], [])
        self.assertEqual(meta["article_count"], 0)
        self.assertEqual(meta["articles"], [])

    def test_json_직렬화_왕복(self):
        meta = daily.briefing_meta(_briefing(_issue()), daily.EDITIONS["kr"], WHEN)
        self.assertEqual(json.loads(json.dumps(meta, ensure_ascii=False)), meta)


class RenderEditionBriefingJsonTest(unittest.TestCase):
    """가드가 있다 ≠ 작동한다 — briefing_meta 함수가 있어도 render_edition이
    실제로 briefing.json을 디스크에 쓰는지는 별도 배선 테스트로 확인해야 한다."""

    def test_render_edition이_briefing_json을_저장한다(self):
        fake_brief = SimpleNamespace(articles=[1], quotes=[], errors=[], collected_at=WHEN)
        fake_briefing = _briefing(_issue())
        fake_briefing.bg_query = ""

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "2026-08-26-kr"
            out.mkdir(parents=True)

            with (
                patch.object(daily, "collect", lambda feeds=None: fake_brief),
                patch.object(daily, "naver_rerank", lambda issues: issues),
                patch.object(daily, "rank_issues", lambda articles: []),
                patch.object(daily, "summarize", lambda brief, issues=None: fake_briefing),
                patch.object(daily, "build_background", lambda *a, **k: None),
                patch.object(daily.renderer, "render", lambda *a, **k: None),
                patch.object(daily, "output_dir", lambda edition, when: out),
            ):
                daily.render_edition(daily.EDITIONS["kr"])

            briefing_json = out / "briefing.json"
            self.assertTrue(briefing_json.exists())
            saved = json.loads(briefing_json.read_text(encoding="utf-8"))
            self.assertIn("date", saved)
            self.assertEqual(saved["headline"], "한은, 숨 고르기")


if __name__ == "__main__":
    unittest.main()

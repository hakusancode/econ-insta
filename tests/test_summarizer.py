"""summarizer 테스트. 가짜 클라이언트를 쓰므로 네트워크·API 키가 필요 없다."""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from types import SimpleNamespace

from econ_insta.collector import KST, Article, DailyBrief, Quote
from econ_insta.summarizer import (
    CARD_BODY_MAX,
    HEADLINE_MAX,
    SummarizeError,
    build_prompt,
    render_article,
    summarize,
)


def article(title="코스피 급등", source="매일경제", summary="코스피가 2.5% 올랐다.", language="ko"):
    return Article(
        source=source,
        title=title,
        link="https://example.com/1",
        published=datetime(2026, 7, 10, 15, 0, tzinfo=KST),
        summary=summary,
        language=language,
    )


def brief(articles=None, quotes=None):
    return DailyBrief(
        collected_at=datetime(2026, 7, 10, 8, 0, tzinfo=KST),
        articles=articles if articles is not None else [article()],
        quotes=quotes if quotes is not None else [Quote("^KS11", "코스피", 7475.94, 2.52)],
    )


def payload(cards=3, headline="오늘의 경제", body="본문입니다."):
    return {
        "headline": headline,
        "indicator_note": "지수가 일제히 올랐다.",
        "cards": [
            {"title": f"카드{i}", "body": body, "source": "매일경제"} for i in range(cards)
        ],
    }


class FakeClient:
    """messages.create()가 지정된 JSON을 텍스트 블록으로 돌려준다."""

    def __init__(self, data, stop_reason="end_turn", text=None):
        body = text if text is not None else json.dumps(data, ensure_ascii=False)
        self.response = SimpleNamespace(
            stop_reason=stop_reason,
            content=[SimpleNamespace(type="text", text=body)],
            usage=SimpleNamespace(input_tokens=3000, output_tokens=800),
        )
        self.captured = {}
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.captured = kwargs
        return self.response


class RenderArticleTest(unittest.TestCase):
    def test_includes_body_when_present(self):
        text = render_article(article(), 1)
        self.assertIn("has_body: true", text)
        self.assertIn("본문요약: 코스피가 2.5% 올랐다.", text)

    def test_marks_missing_body(self):
        """한국경제는 RSS에 description이 없어 제목만 온다."""
        text = render_article(article(source="한국경제", summary=""), 1)
        self.assertIn("has_body: false", text)
        self.assertNotIn("본문요약", text)

    def test_labels_language(self):
        self.assertIn("언어: en", render_article(article(source="WSJ", language="en"), 1))


class BuildPromptTest(unittest.TestCase):
    def test_contains_articles_and_quotes(self):
        text = build_prompt(brief())
        self.assertIn("코스피 급등", text)
        self.assertIn("7,476", text)
        self.assertIn("+2.52%", text)

    def test_notes_missing_quotes(self):
        self.assertIn("지표 수집 실패", build_prompt(brief(quotes=[])))

    def test_empty_articles_raises(self):
        with self.assertRaises(SummarizeError):
            build_prompt(brief(articles=[]))


class SummarizeTest(unittest.TestCase):
    def test_parses_response(self):
        client = FakeClient(payload())
        result = summarize(brief(), client=client)
        self.assertEqual(result.headline, "오늘의 경제")
        self.assertEqual(len(result.cards), 3)
        self.assertEqual(result.cards[0].source, "매일경제")

    def test_reports_token_usage_and_cost(self):
        result = summarize(brief(), client=FakeClient(payload()))
        self.assertEqual(result.input_tokens, 3000)
        self.assertEqual(result.output_tokens, 800)
        # 3000/1e6*2 + 800/1e6*10 = 0.006 + 0.008
        self.assertAlmostEqual(result.cost_usd, 0.014, places=6)

    def test_carries_quotes_through(self):
        result = summarize(brief(), client=FakeClient(payload()))
        self.assertEqual(result.quotes[0].name, "코스피")

    def test_requests_structured_output(self):
        client = FakeClient(payload())
        summarize(brief(), client=client)
        fmt = client.captured["output_config"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertFalse(fmt["schema"]["additionalProperties"])

    def test_uses_adaptive_thinking(self):
        """Sonnet 5에서 budget_tokens는 400을 반환한다. adaptive여야 한다."""
        client = FakeClient(payload())
        summarize(brief(), client=client)
        self.assertEqual(client.captured["thinking"], {"type": "adaptive"})
        self.assertNotIn("temperature", client.captured)

    def test_too_few_cards_raises(self):
        with self.assertRaises(SummarizeError):
            summarize(brief(), client=FakeClient(payload(cards=2)))

    def test_too_many_cards_raises(self):
        with self.assertRaises(SummarizeError):
            summarize(brief(), client=FakeClient(payload(cards=6)))

    def test_long_headline_raises(self):
        with self.assertRaises(SummarizeError):
            summarize(brief(), client=FakeClient(payload(headline="가" * (HEADLINE_MAX + 1))))

    def test_long_body_raises(self):
        with self.assertRaises(SummarizeError):
            summarize(brief(), client=FakeClient(payload(body="가" * (CARD_BODY_MAX + 1))))

    def test_blank_source_raises(self):
        data = payload()
        data["cards"][0]["source"] = "  "
        with self.assertRaises(SummarizeError):
            summarize(brief(), client=FakeClient(data))

    def test_truncated_response_raises(self):
        client = FakeClient(payload(), stop_reason="max_tokens")
        with self.assertRaises(SummarizeError) as ctx:
            summarize(brief(), client=client)
        self.assertIn("max_tokens", str(ctx.exception))

    def test_refusal_raises(self):
        with self.assertRaises(SummarizeError):
            summarize(brief(), client=FakeClient(payload(), stop_reason="refusal"))

    def test_bad_json_raises(self):
        with self.assertRaises(SummarizeError):
            summarize(brief(), client=FakeClient(None, text="{not json"))


if __name__ == "__main__":
    unittest.main()

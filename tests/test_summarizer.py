"""summarizer 테스트. 가짜 클라이언트를 쓰므로 네트워크·API 키가 필요 없다."""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from types import SimpleNamespace

from econ_insta.collector import KST, Article, DailyBrief, Quote
from econ_insta.issues import rank_issues
from econ_insta.summarizer import (
    CARD_BODY_MAX,
    HEADLINE_MAX,
    SummarizeError,
    audit,
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


NAMES = "가나다라마바사"


def payload(cards=3, headline="오늘의 경제", body="본문입니다.", note="지수가 일제히 올랐다."):
    """검증을 통과하는 깨끗한 응답. 제목·본문에 숫자를 넣지 않는다."""
    return {
        "headline": headline,
        "indicator_note": note,
        "cards": [
            {"title": f"카드{NAMES[i]}", "body": body, "source": "매일경제"} for i in range(cards)
        ],
    }


class FakeClient:
    """messages.create()가 지정된 JSON을 텍스트 블록으로 돌려준다.

    data에 리스트를 주면 호출 순서대로 다른 응답을 낸다 (재시도 경로 검증용).
    """

    def __init__(self, data, stop_reason="end_turn", text=None):
        self.bodies = [
            text if text is not None else json.dumps(d, ensure_ascii=False)
            for d in (data if isinstance(data, list) else [data])
        ]
        self.stop_reason = stop_reason
        self.calls = 0
        self.captured = {}
        self.prompts: list[str] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.captured = kwargs
        self.prompts.append(kwargs["messages"][0]["content"])
        body = self.bodies[min(self.calls, len(self.bodies) - 1)]
        self.calls += 1
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=body)],
            usage=SimpleNamespace(input_tokens=3000, output_tokens=800),
        )


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
        b = brief()
        text = build_prompt(b, rank_issues(b.articles))
        self.assertIn("코스피 급등", text)
        self.assertIn("7,476", text)
        self.assertIn("+2.52%", text)

    def test_notes_missing_quotes(self):
        b = brief(quotes=[])
        self.assertIn("지표 수집 실패", build_prompt(b, rank_issues(b.articles)))

    def test_empty_articles_raises(self):
        with self.assertRaises(SummarizeError):
            build_prompt(brief(articles=[]), [])


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

    def test_clean_output_does_not_retry(self):
        client = FakeClient(payload())
        summarize(brief(), client=client)
        self.assertEqual(client.calls, 1)


class AuditTest(unittest.TestCase):
    def test_digits_in_headline_flagged(self):
        self.assertIn("headline", audit(payload(headline="코스피 7% 급등"), "코스피 2.52%"))

    def test_digits_in_indicator_note_flagged(self):
        self.assertIn("indicator_note", audit(payload(note="1,503원대"), "환율 1,503.6"))

    def test_unsupported_card_number_flagged(self):
        data = payload(body="국고채 40조 순매수")
        self.assertEqual(audit(data, "국고채 41조 순매수")["card:0"], ["40조"])

    def test_supported_card_number_clean(self):
        data = payload(body="국고채 41조 순매수")
        self.assertEqual(audit(data, "국고채 41조 순매수"), {})

    def test_clean_payload_has_no_problems(self):
        self.assertEqual(audit(payload(), "아무 자료"), {})

    def test_reversed_won_direction_in_note_flagged(self):
        quotes = [Quote(symbol="KRW=X", name="원/달러", price=1519.9, change_pct=-0.58)]
        problems = audit(payload(note="원화는 약세로 돌아섰고"), "아무 자료", quotes)
        self.assertIn("indicator_note", problems)

    def test_correct_won_direction_in_note_clean(self):
        quotes = [Quote(symbol="KRW=X", name="원/달러", price=1503.3, change_pct=-1.09)]
        self.assertEqual(audit(payload(note="원화는 강세로 방향을 튼 하루"), "아무 자료", quotes), {})

    def test_won_claim_in_card_body_is_not_flagged(self):
        # 카드 본문의 "원화 강세로 전환할 여지" 같은 전망 인용은 지표와 무관하다.
        quotes = [Quote(symbol="KRW=X", name="원/달러", price=1503.3, change_pct=0.62)]
        data = payload(body="총재는 원화 강세로 전환할 여지가 크다고 말했다")
        self.assertEqual(audit(data, data["cards"][0]["body"], quotes), {})

    def test_missing_fx_quote_does_not_flag(self):
        self.assertEqual(audit(payload(note="원화 강세가 뚜렷하다"), "아무 자료", []), {})


class RetryTest(unittest.TestCase):
    def test_retries_once_then_succeeds(self):
        bad = payload(headline="코스피 7% 급등")
        client = FakeClient([bad, payload()])
        result = summarize(brief(), client=client)
        self.assertEqual(client.calls, 2)
        self.assertEqual(result.headline, "오늘의 경제")

    def test_retry_prompt_names_the_problem(self):
        client = FakeClient([payload(headline="코스피 7% 급등"), payload()])
        summarize(brief(), client=client)
        self.assertIn("직전 시도의 문제", client.prompts[1])
        self.assertIn("headline", client.prompts[1])

    def test_tokens_accumulate_across_retry(self):
        client = FakeClient([payload(headline="7% 급등"), payload()])
        result = summarize(brief(), client=client)
        self.assertEqual(result.input_tokens, 6000)
        self.assertEqual(result.output_tokens, 1600)

    def test_persistent_headline_violation_raises(self):
        """표지 문구는 대체할 수 없으므로 발행하지 않는다."""
        bad = payload(headline="코스피 7% 급등")
        with self.assertRaises(SummarizeError) as ctx:
            summarize(brief(), client=FakeClient([bad, bad]))
        self.assertIn("headline", str(ctx.exception))

    def test_persistent_card_violation_dropped(self):
        bad = payload(cards=4)
        bad["cards"][1]["body"] = "국고채 40조 순매수"  # 자료에는 41조
        b = brief(articles=[article(summary="외국인 국고채 41조 순매수")])
        result = summarize(b, client=FakeClient([bad, bad]))
        self.assertEqual(len(result.cards), 3)
        self.assertEqual(result.dropped_cards, 1)
        self.assertNotIn("40조", " ".join(c.body for c in result.cards))

    def test_dropping_below_minimum_raises(self):
        bad = payload(cards=3)
        bad["cards"][0]["body"] = "국고채 40조"
        b = brief(articles=[article(summary="외국인 국고채 41조 순매수")])
        with self.assertRaises(SummarizeError) as ctx:
            summarize(b, client=FakeClient([bad, bad]))
        self.assertIn("최소", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

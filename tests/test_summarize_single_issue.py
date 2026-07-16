import json
import unittest
from datetime import datetime
from types import SimpleNamespace

from econ_insta.collector import KST, Article, DailyBrief, Quote
from econ_insta.issues import rank_issues
from econ_insta.summarizer import summarize, build_prompt, SCHEMA


def art(title, source, summary=""):
    return Article(
        source=source,
        title=title,
        link="http://x",
        published=datetime(2026, 7, 16, 8, 0, tzinfo=KST),
        summary=summary,
        language="ko",
    )


class FakeMessages:
    """caller.messages.create 를 흉내낸다. 넘어온 프롬프트를 기록하고 고정 JSON을 돌려준다."""
    def __init__(self, payload):
        self._payload = payload
        self.last_prompt = None

    def create(self, *, model, max_tokens, system, thinking, output_config, messages):
        self.last_prompt = messages[0]["content"]
        text = json.dumps(self._payload, ensure_ascii=False)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
        )


class FakeClient:
    def __init__(self, payload):
        self.messages = FakeMessages(payload)


PAYLOAD = {
    "headline": "삼성 반도체, 시장이 얼어붙었다",
    "indicator_note": "위험 회피 심리가 지표 전반에 번졌다",
    "cards": [
        {"title": "무슨 일", "body": "삼성전자가 어닝 쇼크를 냈다.", "source": "연합뉴스", "role": "무슨 일"},
        {"title": "왜", "body": "메모리 가격 급락이 원인으로 지목됐다.", "source": "매일경제", "role": "왜"},
        {"title": "앞으로", "body": "업계는 감산 여부를 주시하고 있다.", "source": "한국경제", "role": "앞으로"},
    ],
}


# [이슈 2] = 한은 기준금리(1매체). 트랩 테스트용 — 번호가 1이 아니어야 issues[0] 뮤테이션이 잡힌다.
RATE_PAYLOAD = {
    "headline": "금리 동결, 시장은 숨을 골랐다",
    "indicator_note": "관망세가 지표에 묻어났다",
    "issue_index": 2,
    "cards": [
        {"title": "무슨 일", "body": "한국은행이 기준금리를 동결했다.", "source": "한국경제", "role": "무슨 일"},
        {"title": "왜", "body": "물가 둔화와 경기 부진을 함께 고려했다.", "source": "한국경제", "role": "왜"},
        {"title": "앞으로", "body": "시장은 다음 회의의 신호를 기다린다.", "source": "한국경제", "role": "앞으로"},
    ],
}


def sample_brief():
    arts = [
        art("삼성전자 반도체 어닝 쇼크", "연합뉴스"),
        art("삼성전자 반도체 실적 급감", "매일경제"),
        art("한은 기준금리 동결", "한국경제"),
    ]
    quotes = [Quote(symbol="^KS11", name="코스피", price=2981.4, change_pct=-2.14)]
    return DailyBrief(articles=arts, quotes=quotes, collected_at=datetime(2026, 7, 16), errors=[])


class SummarizeSingleIssueTest(unittest.TestCase):
    def test_returns_cards_with_roles(self):
        client = FakeClient(PAYLOAD)
        briefing = summarize(sample_brief(), client=client)
        self.assertEqual(briefing.headline, PAYLOAD["headline"])
        self.assertEqual(len(briefing.cards), 3)
        self.assertEqual(briefing.cards[0].role, "무슨 일")

    def test_prompt_contains_single_issue_instruction(self):
        client = FakeClient(PAYLOAD)
        summarize(sample_brief(), client=client)
        prompt = client.messages.last_prompt
        self.assertIn("이슈", prompt)          # 이슈 후보가 프롬프트에 실렸다
        self.assertIn("삼성전자", prompt)       # 크로스소스 상위 이슈가 후보로 보인다

    def test_build_prompt_ranks_issues(self):
        brief = sample_brief()
        prompt = build_prompt(brief, rank_issues(brief.articles))
        # 삼성(2매체) 이슈가 금리(1매체)보다 먼저 온다
        self.assertLess(prompt.index("삼성전자"), prompt.index("기준금리"))

    def test_build_prompt_넘겨받은_이슈만_싣는다(self):
        """build_prompt는 안에서 다시 랭킹하지 않는다.

        지역 import한 rank_issues를 되살리는 뮤테이션이 여기서 FAIL한다 —
        안 넘긴 삼성 이슈가 프롬프트에 나타나기 때문.
        """
        brief = sample_brief()
        금리만 = [i for i in rank_issues(brief.articles) if "기준금리" in i.articles[0].title]
        self.assertEqual(len(금리만), 1)          # 픽스처 방어: 이 이슈가 실제로 갈라져 있다
        prompt = build_prompt(brief, 금리만)
        self.assertIn("기준금리", prompt)
        self.assertNotIn("삼성전자", prompt)


class IssueContractTest(unittest.TestCase):
    """모델이 고른 이슈가 Briefing에 실려 나오는가 (스펙 2026-07-17-issue-contract-design.md)."""

    def test_모델이_고른_이슈가_briefing에_실린다(self):
        """트랩: 모델이 2번을 고르면 2번이 나와야 한다.

        issues[0]으로 폴백하는 뮤테이션이 여기서 FAIL한다. issue_index=1로
        짜면 버그 코드로도 통과하므로 반드시 1이 아닌 번호를 쓴다.
        """
        brief = sample_brief()
        self.assertGreaterEqual(len(rank_issues(brief.articles)), 2)   # 픽스처 방어

        briefing = summarize(brief, client=FakeClient(RATE_PAYLOAD))

        self.assertIsNotNone(briefing.issue)
        # 객체 동일성이 아니라 내용으로 단언한다 — 테스트가 rank_issues를
        # 다시 부르면 summarize 안의 것과 다른 객체가 나온다.
        self.assertEqual(briefing.issue.articles[0].title, "한은 기준금리 동결")

    def test_범위밖_번호면_이슈없이_발행된다(self):
        """카드는 살아서 나간다 — 배경 조달의 문제이지 콘텐츠의 문제가 아니다."""
        for bad in (99, 0, -1):
            with self.subTest(issue_index=bad):
                briefing = summarize(sample_brief(), client=FakeClient({**PAYLOAD, "issue_index": bad}))
                self.assertIsNone(briefing.issue)
                self.assertEqual(len(briefing.cards), 3)

    def test_번호가_없거나_타입이_이상해도_발행된다(self):
        for bad in ({**PAYLOAD}, {**PAYLOAD, "issue_index": "2"}, {**PAYLOAD, "issue_index": None}):
            with self.subTest(issue_index=bad.get("issue_index", "(없음)")):
                briefing = summarize(sample_brief(), client=FakeClient(bad))
                self.assertIsNone(briefing.issue)
                self.assertEqual(len(briefing.cards), 3)

    def test_스키마에_issue_index가_필수다(self):
        self.assertEqual(SCHEMA["properties"]["issue_index"]["type"], "integer")
        self.assertIn("issue_index", SCHEMA["required"])


if __name__ == "__main__":
    unittest.main()

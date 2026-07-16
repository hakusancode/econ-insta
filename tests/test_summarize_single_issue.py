import json
import re
import unittest
from datetime import datetime
from types import SimpleNamespace

from econ_insta.collector import KST, Article, DailyBrief, Quote
from econ_insta.issues import rank_issues
from econ_insta.summarizer import summarize, build_prompt, SCHEMA, SYSTEM


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


def 이슈블록(prompt: str, n: int) -> str:
    """프롬프트에서 `[이슈 N]` 블록만 잘라낸다. 다음 `[이슈 n+1]` 블록이나

    안내문("가장 화제성이...") 직전까지가 그 이슈의 내용이다. 블록이 없으면(예:
    후보를 슬라이스해 [이슈 2]가 통째로 안 실린 경우) AssertionError로 바로 드러난다.
    """
    match = re.search(rf"\[이슈 {n}\].*?(?=\n\n\[이슈 \d+\]|\n\n가장 화제성이|\Z)", prompt, re.DOTALL)
    assert match is not None, f"프롬프트에 [이슈 {n}] 블록이 없다"
    return match.group(0)


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

    def test_프롬프트_번호와_chosen_issue_매핑이_일치한다(self):
        """이음매 테스트: 프롬프트에서 [이슈 N]으로 번호 붙은 블록의 이슈 ==

        `_chosen_issue`가 번호 N에 대해 돌려주는 이슈. `summarize()`는
        `build_prompt(brief, issues)`와 `_chosen_issue(payload, issues)`에 **같은
        issues 리스트를 변형 없이** 넘긴다는 사실에 기대어 번호가 맞아떨어진다. 그
        불변식을 지키는 게 이 테스트뿐이다 — 스펙 §7이 후속으로 못박은
        "프롬프트 후보 개수 조정"(`issues[:N]` 슬라이스)이나 정렬 순서를 바꾸는
        리팩터가 이 테스트 없이는 조용히 계약을 깬다.

        RATE_PAYLOAD의 issue_index=2는 sample_brief()의 두 번째 이슈(금리, 1매체)를
        가리킨다. 프롬프트의 [이슈 2] 블록 안에 그 이슈의 대표 기사 제목이 실제로
        있는지 확인한다 — issues 리스트와 프롬프트 번호가 어긋나면(뒤집히거나
        슬라이스되면) 이 단언이 깨진다.
        """
        client = FakeClient(RATE_PAYLOAD)
        briefing = summarize(sample_brief(), client=client)

        block = 이슈블록(client.messages.last_prompt, 2)
        self.assertIn(briefing.issue.articles[0].title, block)

    def test_범위밖_번호면_이슈없이_발행된다(self):
        """카드는 살아서 나간다 — 배경 조달의 문제이지 콘텐츠의 문제가 아니다."""
        for bad in (99, 0, -1):
            with self.subTest(issue_index=bad):
                briefing = summarize(sample_brief(), client=FakeClient({**PAYLOAD, "issue_index": bad}))
                self.assertIsNone(briefing.issue)
                self.assertEqual(len(briefing.cards), 3)

    def test_번호가_없거나_타입이_이상해도_발행된다(self):
        for bad in (
            {**PAYLOAD},
            {**PAYLOAD, "issue_index": "2"},
            {**PAYLOAD, "issue_index": None},
        ):
            label = bad.get("issue_index", "(없음)")
            with self.subTest(issue_index=repr(label)):
                briefing = summarize(sample_brief(), client=FakeClient(bad))
                self.assertIsNone(briefing.issue)
                self.assertEqual(len(briefing.cards), 3)

    def test_issue_index가_True면_issues0으로_새지_않는다(self):
        """bool은 int의 서브클래스라 isinstance(True, int)가 True다.

        isinstance만 쓰면 1 <= True <= len(issues)가 1 <= 1 <= n으로 성립해
        issues[0]을 반환한다 — _chosen_issue가 막으려는 바로 그 폴백이
        타입 검사를 뚫고 되살아난다. type(index) is not int로 bool을 배제해야 한다.
        """
        briefing = summarize(sample_brief(), client=FakeClient({**PAYLOAD, "issue_index": True}))
        self.assertIsNone(briefing.issue)
        self.assertEqual(len(briefing.cards), 3)

    def test_스키마에_issue_index가_필수다(self):
        self.assertEqual(SCHEMA["properties"]["issue_index"]["type"], "integer")
        self.assertIn("issue_index", SCHEMA["required"])

    def test_system이_issue_index를_채우라고_지시한다(self):
        """모델이 issue_index를 채우게 만드는 유일한 장치는 SYSTEM 프롬프트 지시뿐이다.

        스키마는 필드의 존재(타입)만 강제하고, 값을 제대로 채우라는 지시는
        SYSTEM 프롬프트에만 있다. 문구 전체를 단언하면 깨지기 쉬우므로
        issue_index가 언급되는지만 확인한다.
        """
        self.assertIn("issue_index", SYSTEM)


if __name__ == "__main__":
    unittest.main()

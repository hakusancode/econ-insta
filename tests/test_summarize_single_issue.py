import json
import re
import unittest
from datetime import datetime
from types import SimpleNamespace

from econ_insta.collector import KST, Article, DailyBrief, Quote
from econ_insta.issues import rank_issues
from econ_insta.summarizer import (
    SCHEMA, SYSTEM, PROMPT_ISSUES, SummarizeError,
    audit, build_prompt, render_issue, replace_hanja, summarize,
)


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
    "bg_query": "Bank of Korea building",
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


# 서로 핵심어가 겹치지 않는 12개 주제. rank_issues는 핵심어 2개 이상 겹칠 때만
# 합치므로(min_shared=2) 이 목록은 이슈 12개로 갈라진다.
_TOPICS = [
    "삼성전자 어닝쇼크",
    "한은 기준금리 동결",
    "코스피 급락",
    "원화 환율 상승",
    "비트코인 반등",
    "유가 배럴당 급등",
    "미국 고용 지표",
    "엔비디아 실적",
    "부동산 거래량 감소",
    "조선업 수주 확대",
    "항공사 여객 증가",
    "배터리 수출 부진",
]


def many_issue_brief():
    """이슈 12개짜리 브리프. 프롬프트 상위 N개 자르기를 검증한다."""
    arts = [art(title, "매일경제") for title in _TOPICS]
    quotes = [Quote(symbol="^KS11", name="코스피", price=2981.4, change_pct=-2.14)]
    return DailyBrief(articles=arts, quotes=quotes, collected_at=datetime(2026, 7, 16), errors=[])


def big_issue_brief():
    """기사 8건이 한 이슈로 묶이는 브리프. 이슈당 나열 수 자르기를 검증한다.

    제목의 핵심어(한은·기준금리·인상)가 전부 같아 rank_issues가 하나로 묶는다.
    끝의 숫자는 _WORD_RE가 잡지 않으므로 핵심어에 영향이 없다.
    """
    sources = ["연합뉴스", "매일경제", "한국경제"]
    arts = [art(f"한은 기준금리 인상 {i}", sources[i % 3]) for i in range(8)]
    quotes = [Quote(symbol="^KS11", name="코스피", price=2981.4, change_pct=-2.14)]
    return DailyBrief(articles=arts, quotes=quotes, collected_at=datetime(2026, 7, 16), errors=[])


HANJA_PAYLOAD = {
    "headline": "이란·美 충돌 격화",
    "indicator_note": "위험 회피가 中 증시까지 번졌다",
    "cards": [
        {"title": "美 증시 급락", "body": "中 반발이 이어졌다.", "source": "연합뉴스", "role": "무슨 일"},
        {"title": "왜", "body": "日 엔화가 급등했다.", "source": "매일경제", "role": "왜"},
        {"title": "앞으로", "body": "시장은 다음 신호를 기다린다.", "source": "한국경제", "role": "앞으로"},
    ],
}


class HanjaTest(unittest.TestCase):
    """번들 Pretendard에는 한자 글리프가 없다 — 훅의 "美"가 tofu로 발행된 실제 사고
    (2026-07-17, media_id=18087340157553909). 관용 국가 약칭은 한글로 풀어 내보낸다."""

    def test_국가_약칭_한자가_한글로_풀린다(self):
        briefing = summarize(sample_brief(), client=FakeClient(HANJA_PAYLOAD))
        self.assertEqual(briefing.headline, "이란·미국 충돌 격화")
        self.assertEqual(briefing.indicator_note, "위험 회피가 중국 증시까지 번졌다")
        self.assertEqual(briefing.cards[0].title, "미국 증시 급락")
        self.assertEqual(briefing.cards[0].body, "중국 반발이 이어졌다.")
        self.assertEqual(briefing.cards[1].body, "일본 엔화가 급등했다.")

    def test_system이_한자를_금지한다(self):
        """치환표는 아는 한자만 안다 — 모르는 한자가 새는 것은 프롬프트가 1차로 막아야 한다."""
        self.assertIn("한자", SYSTEM)

    def test_관용_접사_한자도_풀린다(self):
        """2026-07-18 아침 크론 실제 사고 — "뉴욕發"의 發이 tofu로 발행됨. SYSTEM 금지는
        모델이 안 지켰다(이 저장소 세 번째 재현: 프롬프트 규칙 하나로 믿지 말 것)."""
        self.assertEqual(replace_hanja("뉴욕發 매도 공포"), "뉴욕발 매도 공포")
        self.assertEqual(replace_hanja("對중국 수출"), "대중국 수출")

    def test_audit가_치환표_밖_한자를_잡는다(self):
        """표에 없는 한자는 재생성 루프로 보내야 한다 — 렌더까지 가면 무조건 tofu다."""
        payload = {
            "headline": "반도체 訥변 공포",   # 訥: 표에 없는 한자
            "indicator_note": "위험 회피가 짙어졌다",
            "cards": [{"title": "무슨 일", "body": "증시가 밀렸다.", "source": "연합뉴스"}],
        }
        problems = audit(payload, "자료 원문", quotes=[])
        self.assertIn("headline", problems)
        self.assertIn("訥", " ".join(problems["headline"]))

    def test_audit는_치환표가_아는_한자를_넘어간다(self):
        """美는 replace_hanja가 미국으로 풀므로 재생성까지 갈 필요 없다."""
        payload = {
            "headline": "美 증시 급락",
            "indicator_note": "위험 회피가 짙어졌다",
            "cards": [{"title": "무슨 일", "body": "中 증시가 밀렸다.", "source": "연합뉴스"}],
        }
        problems = audit(payload, "자료 원문", quotes=[])
        self.assertNotIn("headline", problems)
        self.assertNotIn("card:0", problems)

    def test_한자가_재생성에도_남으면_발행하지_않는다(self):
        """깨진 표지가 나가는 것보다 그날을 건너뛰는 게 낫다(FakeClient는 재시도에도
        같은 payload를 돌려주므로 재생성 실패 경로가 그대로 재현된다)."""
        payload = dict(HANJA_PAYLOAD, headline="뉴욕訥 매도 공포")
        with self.assertRaises(SummarizeError):
            summarize(sample_brief(), client=FakeClient(payload))


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

    def test_프롬프트에_상위_10개_이슈만_실린다(self):
        """brief.articles가 전량(수백 건)이 됐으므로 프롬프트에서 잘라야 한다(스펙 §4.3)."""
        brief = many_issue_brief()
        self.assertEqual(len(rank_issues(brief.articles)), 12)   # 픽스처 방어

        client = FakeClient(PAYLOAD)
        summarize(brief, client=client)
        prompt = client.messages.last_prompt

        self.assertIn("[이슈 10]", prompt)
        self.assertNotIn("[이슈 11]", prompt)
        self.assertIn("[후보 이슈 10개", prompt)


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

    def test_슬라이스_범위밖_번호는_이슈없이_저하한다(self):
        """이음매 테스트(위)로는 안 잡히는 뮤테이션을 직접 겨냥한다.

        앞에서 자르는 슬라이스(`issues[:PROMPT_ISSUES]`)는 순서를 보존하므로
        `issues_full[N-1] == issues_sliced[N-1]`이 1~PROMPT_ISSUES번에서는 그대로
        성립한다 — 그래서 유효 번호로 확인하는 위의 이음매 테스트는 "슬라이스를
        summarize() 밖(build_prompt 안)으로 옮기는" 뮤테이션을 못 잡는다
        (sample_brief는 이슈 2개뿐이라 애초에 잘리지도 않는다).

        진짜 깨지는 지점은 `_chosen_issue`의 범위 검사(`1 <= index <= len(issues)`)다.
        슬라이스가 summarize() 안에 있으면 이 len(issues)가 잘린 PROMPT_ISSUES를 보므로
        프롬프트에 없는 번호(PROMPT_ISSUES + 1)는 범위 밖으로 걸러져 None이 된다. 슬라이스가
        build_prompt 안으로 옮겨지면 len(issues)가 전체(12)를 보게 되어, 모델이 프롬프트에서
        본 적 없는 11번을 답해도 범위 검사를 통과해 issues[10]을 돌려준다 — `_chosen_issue`가
        막으려는 바로 그 사고(모델이 본 적 없는 이슈가 Briefing.issue에 실림)가 재현된다.
        """
        brief = many_issue_brief()
        self.assertGreater(len(rank_issues(brief.articles)), PROMPT_ISSUES)   # 픽스처 방어: 실제로 잘릴 만큼 많다

        out_of_range = PROMPT_ISSUES + 1
        briefing = summarize(brief, client=FakeClient({**PAYLOAD, "issue_index": out_of_range}))
        self.assertIsNone(briefing.issue)

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


class BgQueryTest(unittest.TestCase):
    """데일리도 스톡 사진 폴백에 도달할 수 있어야 한다.

    bg_query가 없으면 build_background가 `if not bg_query: return None`으로
    위키미디어·Unsplash를 통째로 건너뛴다. 데일리는 people도 없어서, 기사 사진이
    실패하면 표지가 무조건 그래픽이 된다 — 2026-07-16·17 이틀 연속 그렇게 나갔다.
    """

    def test_스키마에_bg_query가_필수다(self):
        self.assertEqual(SCHEMA["properties"]["bg_query"]["type"], "string")
        self.assertIn("bg_query", SCHEMA["required"])

    def test_모델의_bg_query가_briefing에_실린다(self):
        briefing = summarize(sample_brief(), client=FakeClient(RATE_PAYLOAD))
        self.assertEqual(briefing.bg_query, "Bank of Korea building")

    def test_bg_query가_없으면_빈문자열로_저하한다(self):
        """없어도 발행은 죽지 않는다 — 표지가 그래픽이 될 뿐이다."""
        briefing = summarize(sample_brief(), client=FakeClient(PAYLOAD))
        self.assertEqual(briefing.bg_query, "")
        self.assertEqual(len(briefing.cards), 3)

    def test_system이_bg_query에_추상어를_쓰지_말라고_지시한다(self):
        self.assertIn("bg_query", SYSTEM)
        self.assertIn("추상 개념은 쓰지 마십시오", SYSTEM)


class RenderIssueSliceTest(unittest.TestCase):
    """이슈당 기사 나열 수 (스펙 §4.4)."""

    def setUp(self):
        self.brief = big_issue_brief()
        self.issues = rank_issues(self.brief.articles)
        self.assertEqual(len(self.issues), 1)                 # 픽스처 방어
        self.assertEqual(len(self.issues[0].articles), 8)     # 픽스처 방어

    def test_기사를_5건까지만_나열한다(self):
        block = render_issue(self.issues[0], 1)
        self.assertEqual(block.count("  - ("), 5)

    def test_헤더는_전체_기사_수를_말한다(self):
        """5건만 보이지만 '8건짜리 이슈'라는 크기 신호는 모델이 봐야 한다."""
        block = render_issue(self.issues[0], 1)
        self.assertIn("기사 8건", block)

    def test_렌더가_issue_articles를_자르지_않는다(self):
        """photos.candidates(issue)가 이 리스트에서 표지 사진 후보를 뽑는다 —
        파괴적으로 자르면 4단계가 확보한 사진 도달률이 떨어진다."""
        build_prompt(self.brief, self.issues)
        self.assertEqual(len(self.issues[0].articles), 8)


if __name__ == "__main__":
    unittest.main()

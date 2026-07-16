# 이슈 계약 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `summarize()`가 모델이 고른 이슈를 `Briefing.issue`에 실어 내보내, 배경 조달 호출부가 `rank_issues()[0]`을 따로 부르다 모델의 선택과 갈리는 일을 없앤다.

**Architecture:** 스키마에 `issue_index` 정수 필드를 추가해 모델에게 자기가 고른 이슈 번호를 직접 물어본다. 프롬프트는 이미 `[이슈 N]`으로 번호를 매겨 보내므로 모델은 읽은 번호를 되돌려주기만 하면 된다. `build_prompt()`가 안에서 `rank_issues()`를 부르고 버리던 숨은 의존을 `summarize()`로 올려, 번호 → `Issue` 매핑에 쓸 리스트를 확보한다.

**Tech Stack:** Python 3.13, `anthropic` (구조화 출력 `output_config.format`), `unittest`, 모델 `claude-sonnet-5`.

**스펙:** `docs/superpowers/specs/2026-07-17-issue-contract-design.md` (커밋 `88111fd`)
**브랜치:** `card-redesign` (4단계 완료 `9a215d7` 위)

## Global Constraints

- **`issues[0]`으로 폴백하지 않는다.** 번호가 없거나 범위 밖이면 `None`으로 저하한다. `issues[0]` 폴백이 바로 이 필드가 생긴 이유다 — 되살리면 원래 결함이 그대로 복원된다.
- **배경 실패는 발행을 막지 않는다** (`backgrounds.py:219`의 기존 원칙). 이슈 번호가 틀린 건 배경 조달의 문제이지 콘텐츠의 문제가 아니다 — 카드가 검증을 통과했으면 발행한다.
- **트랩 테스트에 `issue_index=1`을 쓰지 않는다.** 버그 코드(`issues[0]`)로도 통과해 공허한 테스트가 된다. 원장에 공허한 테스트로 리뷰에서 잡힌 기록이 3건 있다.
- **주석·커밋 메시지·테스트 이름은 한국어.** 저장소 관례.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` (저장소 관례, 확인함).
- 시작 시점 전체 테스트 **299개 통과**. 각 태스크에서 회귀 0.

## 파일 구조

| 파일 | 책임 | 태스크 |
|---|---|---|
| `econ_insta/summarizer.py` | 요약 + 이슈 계약. `build_prompt` 시그니처, `SCHEMA`, `Briefing`, `_chosen_issue` | 1, 2 |
| `tests/test_summarizer.py` | `build_prompt` 호출 3곳 갱신 | 1 |
| `tests/test_summarize_single_issue.py` | 단일 이슈 계약 테스트 (트랩·저하·스키마) | 1, 2 |
| `docs/superpowers/specs/2026-07-16-image-sourcing-design.md` | 4단계 스펙 §4·§5 정정 | 3 |
| `.superpowers/sdd/progress.md` | durable 원장 | 3 |

새 모듈·새 테스트 파일은 만들지 않는다. 계약 테스트는 `test_summarize_single_issue.py`에 넣는다 — 픽스처(`sample_brief`, `FakeClient`)가 이미 거기 있고, 이 계약이 곧 3단계a의 단일 이슈 선택 계약이기 때문이다.

---

### Task 1: `build_prompt`의 숨은 `rank_issues` 의존을 드러낸다

순수 리팩터다. 동작 변화 없음. Task 2가 번호 → `Issue` 매핑에 쓸 `issues` 리스트를 확보하는 것이 목적.

**Files:**
- Modify: `econ_insta/summarizer.py:135-176` (`render_issue`, `build_prompt`), `econ_insta/summarizer.py:260-270` (`summarize`), import 블록 `econ_insta/summarizer.py:19`
- Test: `tests/test_summarize_single_issue.py`, `tests/test_summarizer.py:100-112`

**Interfaces:**
- Consumes: `issues.rank_issues(articles) -> list[Issue]`, `issues.Issue` (이미 존재, 변경 없음)
- Produces: `build_prompt(brief: DailyBrief, issues: list[Issue]) -> str` — Task 2가 이 시그니처 위에 얹는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_summarize_single_issue.py`의 import를 고친다:

```python
from econ_insta.issues import rank_issues
from econ_insta.summarizer import summarize, build_prompt
```

기존 `test_build_prompt_ranks_issues`(78-81행)를 새 시그니처로 고치고, 그 아래에 트랩 테스트를 추가한다:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_summarize_single_issue.py -v`
Expected: `test_build_prompt_ranks_issues`와 `test_build_prompt_넘겨받은_이슈만_싣는다` 둘 다 FAIL — `TypeError: build_prompt() takes 1 positional argument but 2 were given`

- [ ] **Step 3: 최소 구현**

`econ_insta/summarizer.py`의 import 블록(19행 근처)에 추가한다:

```python
from .collector import Article, DailyBrief, Quote, collect
from .config import _load_dotenv
from .factcheck import has_digits, unsupported_amounts, wrong_won_direction
from .issues import Issue, rank_issues
```

(`issues.py`는 `collector`만 import하므로 순환 없음 — 확인함.)

`render_issue`(147행)에 타입을 붙인다. `Issue`를 이제 import했으므로 애초에 빠져 있던 이유가 사라진다:

```python
def render_issue(issue: Issue, index: int) -> str:
```

`build_prompt`(158-176행)에서 지역 import와 `rank_issues` 호출을 걷어낸다:

```python
def build_prompt(brief: DailyBrief, issues: list[Issue]) -> str:
    if not brief.articles:
        raise SummarizeError("요약할 기사가 없습니다.")

    quotes = "\n".join(
        f"  {q.name}: {q.price_text} ({q.change_text})" for q in brief.quotes
    ) or "  (지표 수집 실패)"
    blocks = "\n\n".join(render_issue(iss, i) for i, iss in enumerate(issues, 1))

    return (
        f"오늘 날짜: {brief.collected_at:%Y년 %m월 %d일}\n\n"
        f"[시장지표]\n{quotes}\n\n"
        f"[후보 이슈 {len(issues)}개 — 화제성(매체 수) 내림차순]\n{blocks}\n\n"
        "가장 화제성이 큰 이슈 하나를 골라 단일 이슈 브리핑을 만드십시오."
    )
```

`summarize`(266-268행)에서 랭킹을 부른다:

```python
    _load_dotenv()
    caller = client or anthropic.Anthropic()
    issues = rank_issues(brief.articles)
    prompt = build_prompt(brief, issues)
```

- [ ] **Step 4: 나머지 호출부를 고친다**

`tests/test_summarizer.py`의 import(10-19행)에 추가한다:

```python
from econ_insta.issues import rank_issues
```

`BuildPromptTest`(100-112행)를 통째로 바꾼다:

```python
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
```

- [ ] **Step 5: 테스트 통과를 확인한다**

Run: `python -m pytest tests/ -q`
Expected: PASS. **300 passed** (299 + 신규 1건 `test_build_prompt_넘겨받은_이슈만_싣는다`). 실패 0.

숫자가 300이 아니면 멈추고 원인을 보고할 것 — 계획의 산술 오류일 수도, 진짜 회귀일 수도 있다(원장에 계획의 예상 테스트 수가 틀린 전례가 있다).

- [ ] **Step 6: 뮤테이션으로 트랩을 검증한다**

`build_prompt` 안에 `issues = rank_issues(brief.articles)`를 되살려 넣고 실행한다.

Run: `python -m pytest tests/test_summarize_single_issue.py -q`
Expected: **`test_build_prompt_넘겨받은_이슈만_싣는다` 1건만 FAIL** ("삼성전자"가 프롬프트에 나타남).

확인했으면 **되돌린다.** 이 단계는 테스트가 공허하지 않음을 증명하는 것이지 코드를 바꾸는 게 아니다.

- [ ] **Step 7: 커밋**

```bash
git add econ_insta/summarizer.py tests/test_summarizer.py tests/test_summarize_single_issue.py
git commit -F - <<'EOF'
refactor(summarizer): build_prompt의 숨은 rank_issues 의존을 드러낸다

build_prompt가 함수 안에서 rank_issues를 지역 import해 부르고 결과를
버렸다. 모델이 고른 이슈 번호를 Issue로 매핑하려면 그 리스트가 필요하다.
build_prompt(brief, issues)로 인자를 받게 하고 summarize가 랭킹을 부른다.

동작 변화 없음. 순수 리팩터. Issue를 import하게 된 김에 render_issue의
빠져 있던 타입도 붙였다.

트랩 테스트 1건 추가: 안 넘긴 이슈는 프롬프트에 실리지 않는다. 지역
import를 되살리는 뮤테이션으로 1건 FAIL을 확인하고 원상복구했다.

전체 스위트 299 -> 300, 실패 0.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 2: 이슈 계약 — 모델이 고른 번호를 `Briefing.issue`로

이 계획의 본체다.

**Files:**
- Modify: `econ_insta/summarizer.py` — `SYSTEM`(36-78행), `SCHEMA`(80-102행), `Briefing`(118-132행), `_chosen_issue`(신규), `summarize`의 반환(302-310행)
- Test: `tests/test_summarize_single_issue.py`

**Interfaces:**
- Consumes: `build_prompt(brief, issues) -> str` (Task 1), `rank_issues(articles) -> list[Issue]`
- Produces: `Briefing.issue: Issue | None` — 데일리 파이프라인 모듈이 `build_background(issue=briefing.issue)`로 쓴다(이번 범위 밖, 스펙 §4).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_summarize_single_issue.py`의 import에 `SCHEMA`를 추가한다:

```python
from econ_insta.summarizer import summarize, build_prompt, SCHEMA
```

`PAYLOAD`(42-50행) 아래에 금리 이슈용 payload를 추가한다. **카드 내용과 번호가 일치하게** 만든다 — 트랩의 초점은 "1이 아닌 번호"이지 "내용과 어긋난 번호"가 아니다:

```python
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
```

`SummarizeSingleIssueTest` 아래에 새 테스트 클래스를 추가한다:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_summarize_single_issue.py -v`
Expected: 4건 FAIL. `test_스키마에_issue_index가_필수다`는 `KeyError: 'issue_index'`, 나머지 3건은 `AttributeError: 'Briefing' object has no attribute 'issue'`.

- [ ] **Step 3: `Briefing`에 필드를 추가한다**

`econ_insta/summarizer.py:118-132`:

```python
@dataclass(frozen=True)
class Briefing:
    headline: str
    indicator_note: str
    cards: list[Card]
    quotes: list[Quote]
    issue: Issue | None = None
    """모델이 고른 이슈. None이면 표지 사진을 조달할 대상이 없어 그래픽으로 나간다."""
    input_tokens: int = 0
    output_tokens: int = 0
    dropped_cards: int = 0
    """수치 검증에 걸려 폐기된 카드 수. 0이 아니면 로그로 남겨야 한다."""

    @property
    def cost_usd(self) -> float:
        """Sonnet 5 도입가 기준 ($2/$10 per 1M). 사고 토큰도 출력으로 과금된다."""
        return self.input_tokens / 1e6 * 2 + self.output_tokens / 1e6 * 10
```

기본값 그룹 맨 앞이라 위치인자 순서가 바뀌지만, `Briefing(...)` 생성부는 `summarizer.py:302`와 `tests/test_renderer.py:67`(`Briefing(**{**defaults, **overrides})`) 둘뿐이고 **전부 키워드인자다**(확인함).

- [ ] **Step 4: 스키마와 프롬프트에 번호를 넣는다**

`SCHEMA["properties"]`에 추가하고 `required`를 고친다(`econ_insta/summarizer.py:80-102`):

```python
SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "표지 카드 제목"},
        "indicator_note": {"type": "string", "description": "지표 카드에 얹을 한 문장 코멘트"},
        "issue_index": {"type": "integer", "description": "당신이 고른 이슈의 번호(프롬프트의 [이슈 N])"},
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "source": {"type": "string", "description": "출처 매체명(복수면 대표 1곳 또는 'A·B')"},
                    "role": {"type": "string", "description": "서사 국면: 무슨 일 | 왜 | 반응 | 앞으로 (선택)"},
                },
                "required": ["title", "body", "source"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "indicator_note", "issue_index", "cards"],
    "additionalProperties": False,
}
```

`SYSTEM`의 "만드는 법" 목록에서 이슈를 고르라는 줄(43행) 바로 아래에 한 줄 넣는다:

```python
- **가장 화제성이 큰 이슈 하나**를 고르십시오(대개 첫 번째 후보). 그 이슈 하나만 다룹니다.
- 고른 이슈의 번호를 `issue_index`에 넣으십시오(프롬프트의 `[이슈 N]`의 N). 표지 사진을 그 이슈의 기사에서 찾기 때문에, 번호가 틀리면 표지에 엉뚱한 사진이 깔립니다.
```

- [ ] **Step 5: 매핑 헬퍼를 만든다**

`_describe`(222-229행)와 `_generate`(232행) 사이에 넣는다:

```python
def _chosen_issue(payload: dict, issues: list[Issue]) -> Issue | None:
    """모델이 고른 이슈. 번호가 없거나 범위 밖이면 None(표지는 그래픽으로 저하).

    issues[0]으로 폴백하지 않는다 — 모델의 선택과 갈리는 것이 바로 이 필드가 생긴 이유다.
    2026-07-16 실측: 모델은 코스피를 골랐는데 rank_issues()[0]은 광고성 리스티클이었다.

    payload["issue_index"]가 아니라 .get()인 것도 의도적이다. 스키마 required가
    보장하지만, 만에 하나 없을 때 KeyError로 발행을 죽이는 건 장식 하나 때문에
    게시물을 버리는 것이다. 없음·범위밖·타입이상을 전부 같은 저하 경로로 모은다.
    """
    index = payload.get("issue_index")
    if not isinstance(index, int) or not 1 <= index <= len(issues):
        return None
    return issues[index - 1]
```

- [ ] **Step 6: `summarize`가 이슈를 실어 내보내게 한다**

`econ_insta/summarizer.py:302-310`:

```python
    return Briefing(
        headline=payload["headline"],
        indicator_note=payload["indicator_note"],
        cards=cards,
        quotes=brief.quotes,
        issue=_chosen_issue(payload, issues),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        dropped_cards=len(dropped),
    )
```

재시도가 일어났다면 `payload`는 이미 재시도 결과다(280행에서 덮임) — 카드의 출처와 같은 payload에서 번호를 뽑는다.

- [ ] **Step 7: 테스트 통과를 확인한다**

Run: `python -m pytest tests/ -q`
Expected: PASS. **304 passed** (300 + 신규 4건). 실패 0.

- [ ] **Step 8: 뮤테이션 2종으로 트랩을 검증한다**

**뮤테이션 A —** `_chosen_issue`의 마지막 줄을 `return issues[0]`으로 바꾼다.
Run: `python -m pytest tests/test_summarize_single_issue.py -q`
Expected: **`test_모델이_고른_이슈가_briefing에_실린다` 1건 FAIL** ("삼성전자 반도체 어닝 쇼크" != "한은 기준금리 동결").

**뮤테이션 B —** 범위 검사를 지운다(`if not isinstance(index, int): return None`만 남긴다).
Run: `python -m pytest tests/test_summarize_single_issue.py -q`
Expected: **`test_범위밖_번호면_이슈없이_발행된다` 1건 FAIL**, subTest 3개가 각각 다른 이유로 깨진다:
- `99` → `issues[98]` → `IndexError`
- `0` → `issues[-1]` → **마지막 이슈가 조용히 잡힌다** (음수 인덱싱이 성공한다)
- `-1` → `issues[-2]` → 마찬가지로 조용히 엉뚱한 이슈

`0`과 `-1`이 예외가 아니라 **조용히 틀린 답**을 낸다는 게 범위 검사의 하한(`1 <=`)이 필요한 이유다.
상한만 있으면 파이썬의 음수 인덱싱이 결함을 되살린다.

둘 다 확인했으면 **되돌리고** 전체 스위트가 다시 304 통과인지 확인한다.

- [ ] **Step 9: 커밋**

```bash
git add econ_insta/summarizer.py tests/test_summarize_single_issue.py
git commit -F - <<'EOF'
feat(summarizer): 모델이 고른 이슈를 Briefing.issue로 실어 내보낸다

summarize()가 프롬프트에 이슈 후보를 싣고 모델이 하나를 고르는데 그 선택이
Briefing에 없었다. 배경 조달 호출부는 rank_issues()[0]을 따로 부를 수밖에
없었고 모델의 선택과 갈렸다. 2026-07-16 실측: 모델의 훅은 코스피였고
rank_issues()[0]은 광고성 리스티클이었다 — 사진이 있었다면 재무상담사
사진이 코스피 표지에 깔렸다.

스키마에 issue_index를 required로 추가해 모델에게 직접 번호를 물어보고,
_chosen_issue()가 Issue로 매핑해 Briefing.issue에 싣는다. 번호가 없거나
범위 밖이면 issues[0]으로 폴백하지 않고 None으로 저하한다 — issues[0]
폴백이 바로 이 필드가 생긴 이유다. 카드는 살아서 발행된다(배경 조달의
문제이지 콘텐츠의 문제가 아니다).

트랩 테스트는 모델이 2번을 고르는 시나리오다. 1번으로 짜면 버그 코드로도
통과해 공허해진다. 뮤테이션 2종(issues[0] 폴백 / 범위 검사 삭제) 각각
정확히 1건씩 FAIL을 확인하고 원상복구했다.

배선(build_background(issue=briefing.issue))은 이번 범위 밖 — 넘길
프로덕션 호출부가 없다(데일리 파이프라인 미모듈화). 계약은 스펙 §4.

전체 스위트 300 -> 304, 실패 0.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 3: 4단계 스펙과 원장을 정정한다

문서만. 안 고치면 4단계 스펙이 거짓말인 채로 남는다.

**Files:**
- Modify: `docs/superpowers/specs/2026-07-16-image-sourcing-design.md:51-61` (§4 다이어그램), `:100-101` (§5 머리)
- Modify: `.superpowers/sdd/progress.md` (끝에 추가)

**Interfaces:**
- Consumes: Task 2의 `Briefing.issue`
- Produces: 없음 (문서)

- [ ] **Step 1: §4 아키텍처 다이어그램을 고친다**

`docs/superpowers/specs/2026-07-16-image-sourcing-design.md:51-61`의 코드블록을 바꾼다. 지금 `issues.py → photos.py`로 바로 이어져 "누가 그 Issue를 아는가"가 비어 있고, 그 빈칸이 이번 결함의 발원지다:

```
collector.py    RSS/Atom → Article(+ images: list[str])      ← 신규 필드
     ↓
issues.py       Article[] → Issue[]  (크로스소스 랭킹 — 3단계a 완료, 수정 없음)
     ↓
summarizer.py   Issue[] → 프롬프트 → 모델이 하나 고름 → Briefing.issue   ← 이슈 계약(2026-07-17)
     ↓
photos.py       briefing.issue → 후보 수집 → 기계 필터 → Claude 비전 선택   ← 신규 모듈
     ↓
backgrounds.py  build_background(issue=briefing.issue): 기사 사진 → 인물 → 위키미디어 → Unsplash → None
     ↓
renderer.py     render(background=...) → render_cover(background=...)  ← render()에 통로 추가
```

그 아래에 문단을 넣는다:

```markdown
**`summarizer.py` 줄은 2026-07-17에 추가됐다.** 원래 이 스펙은 `issues.py → photos.py`로 바로 이었고,
`photos.pick(issue)`의 `issue`를 **누가 아는가**를 정하지 않았다. 호출부가 안다고 가정했으나 호출부는
알 수 없었다 — 모델이 프롬프트 안에서 이슈를 고르는데 그 선택이 `Briefing`에 실리지 않았기 때문이다.
그래서 호출부가 `rank_issues()[0]`을 따로 불러 **모델의 선택과 갈렸다.** 4단계 6개 태스크가 리뷰
클린으로 닫히고 299개 테스트가 통과한 뒤에도 실전에서 안 걸린 이유다.
설계·실측·수정: `docs/superpowers/specs/2026-07-17-issue-contract-design.md`.
```

- [ ] **Step 2: §5 머리에 계약을 명시한다**

같은 파일 `## 5. photos.py`(100행) 바로 아래, `### 5.1 후보 수집`(102행) 앞에 넣는다:

```markdown
**`pick(issue)`의 `issue`는 `briefing.issue`다 — `rank_issues()[0]`이 아니다.**
모델이 프롬프트에서 고른 그 이슈여야 한다. 다시 랭킹해서 1위를 집으면 모델의 선택과 갈린다
(2026-07-16 실측: 모델=코스피, `rank_issues()[0]`=광고성 리스티클).
계약은 `docs/superpowers/specs/2026-07-17-issue-contract-design.md` §4.
```

- [ ] **Step 3: 원장을 갱신한다**

`.superpowers/sdd/progress.md`는 **git 무시 대상이다**(`.superpowers/sdd/.gitignore`가 `*`). 커밋하지 말 것 —
`git add`가 "paths are ignored" 에러로 실패한다. 1~4단계 원장도 전부 커밋 안 된 로컬 스크래치이고 그게 설계대로다.
파일만 고치고 Step 5의 커밋 대상에서 뺀다.

파일 끝에 추가한다. 82-93행의 "**[치명적 설계 결함 — 4단계가 실전에서 안 걸림. 새 세션 최우선]**" 블록은 **지우지 않는다** — 원장은 durable 기록이고 결함의 발견 경위가 다음 사람에게 근거로 남아야 한다. 해소 사실을 뒤에 붙인다:

```markdown

# 이슈 계약 (스펙 docs/superpowers/specs/2026-07-17-issue-contract-design.md, 계획 .../plans/2026-07-17-issue-contract.md, 브랜치 card-redesign)
스펙 88111fd. 위 82-93행의 **치명적 설계 결함 해소** — "새 세션 최우선" 표시 내림.
사용자 결정: 범위는 계약만(데일리 파이프라인 모듈화 별도 단계), 프롬프트 후보 개수·본문요약 길이 안 건드림(비용 별도 판단),
  카드 출처 역추적 휴리스틱 안 씀(빈 계약을 또 추론으로 메우는 것이라 같은 병의 약한 버전).
Task 1: build_prompt(brief, issues)로 숨은 rank_issues 의존을 summarize로 올림. 순수 리팩터.
Task 2: SCHEMA에 issue_index(required) + Briefing.issue + _chosen_issue(). 범위 밖/누락/타입이상 → None 저하(issues[0] 폴백 금지).
Task 3: 4단계 스펙 §4 다이어그램·§5 정정(누가 Issue를 아는가가 비어 있던 칸), 원장 갱신.
**남은 계약 부채**: build_background(issue=briefing.issue) 배선은 안 함 — 넘길 프로덕션 호출부가 없음.
  ai_brief·blog_brief는 Issue 개념이 없는 다른 파이프라인. 데일리 파이프라인 모듈 만들 때 반드시 배선할 것(스펙 §4).
  이 수정은 결함을 막는 게 아니라 불필요하게 만든 것 — 호출부가 여전히 rank_issues()[0]을 부를 수는 있음.
Minor(후속): 유효하지만 거짓인 번호(3이라 답하고 1을 씀)는 교차검증 안 함 — 스키마가 출처를 "A·B" 복합
  문자열로 쓰라고 지시해 카드 출처 ↔ 이슈 매체 대조가 부정확. 억지로 하면 멀쩡한 사진 버리는 오탐.
남은 것: 3단계b(인기도 스크래핑·소스9곳·하루3건), 5단계(릴스 주간화), 데일리 파이프라인 모듈화+GH Actions cron,
  실물 확인 후속(얼굴 잘림 시 OpenCV), 광고성 리스티클이 랭킹 1위인 문제(3단계b).
```

- [ ] **Step 4: 회귀가 없는지 확인한다**

Run: `python -m pytest tests/ -q`
Expected: **304 passed**, 실패 0. (문서만 고쳤으므로 변화 없어야 한다.)

- [ ] **Step 5: 커밋**

원장은 git 무시 대상이라 커밋에 안 들어간다(Step 3). 스펙 파일만 add한다.

```bash
git add docs/superpowers/specs/2026-07-16-image-sourcing-design.md
git commit -F - <<'EOF'
docs: 4단계 스펙의 빈 칸(누가 Issue를 아는가)을 메우고 원장 갱신

4단계 스펙 §4 다이어그램이 issues.py -> photos.py로 바로 이어져
pick(issue)의 issue를 누가 아는지 정하지 않았다. 그 빈칸이 이슈 계약
결함의 발원지다. summarizer.py 줄을 넣고, §5에 issue는 briefing.issue이지
rank_issues()[0]이 아님을 명시했다.

원장의 치명적 설계 결함 블록은 지우지 않고 해소 사실을 뒤에 붙였다 —
발견 경위가 다음 사람에게 근거로 남아야 한다. 남은 계약 부채(배선)를
명시했다.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## 완료 기준

- 전체 스위트 **304 통과**, 실패 0 (시작 299 + Task 1의 1건 + Task 2의 4건)
- `Briefing.issue`가 모델이 고른 이슈를 들고 나온다. 뮤테이션 `issues[0]` 폴백이 정확히 1건 FAIL을 낸다.
- 4단계 스펙과 원장이 현실과 일치한다.
- 브랜치 `card-redesign` 미병합 유지 (병합은 사용자 결정).

## 리뷰 전략

4단계와 동일: **태스크별 리뷰 + 최종 전체 브랜치 리뷰**. 원장의 4단계 기록을 보면 태스크별 리뷰가 Important 2건(느슨한 정규식, 공허한 테스트)을 잡았고 최종 전체 리뷰가 태스크 경계 버그를 잡았다 — 두 층이 서로 다른 것을 잡는다.

리뷰어에게 특히 볼 것: **공허한 테스트.** 이 저장소에서 세 번 나왔다. 트랩 테스트가 진짜인지 뮤테이션으로 직접 재현할 것.

# 수집기 quota 재설계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 데일리 경로에서 매체별 quota를 폐기해 `rank_issues`가 그날 기사 전량을 보게 하고, 자르는 지점을 랭킹 뒤(상위 10개 이슈·이슈당 5건)로 옮긴다.

**Architecture:** `collect_articles`의 몸통에서 "모으기"를 `gather_articles`로 떼어낸다. `collect_articles`는 그 위에 `apply_quota` + `[:limit]`만 얹어 기존 계약을 그대로 유지하고(`ai_brief`·`blog_brief`용), `collect()`는 `gather_articles`를 직접 불러 전량을 `DailyBrief.articles`에 싣는다. `summarize()`가 `rank_issues(...)[:PROMPT_ISSUES]`로 한 번만 자르고, `render_issue`가 이슈당 `PROMPT_ARTICLES`건만 나열한다.

**Tech Stack:** Python 3.13, 표준 라이브러리 RSS 파싱, `requests`, 표준 `unittest`.

**스펙:** `docs/superpowers/specs/2026-07-17-collector-quota-design.md` (커밋 `a692beb`)

## Global Constraints

- **테스트 러너는 pytest가 아니라 표준 `unittest`다.** pytest는 설치돼 있지 않다. 전체 실행: `python -m unittest discover -s tests -q`
- **콘솔이 cp949라 한글 출력이 UnicodeEncodeError로 죽는다.** 파이썬 실행 시 `PYTHONIOENCODING=utf-8`을 반드시 붙인다.
- 시작 시점: 브랜치 `main`, 커밋 `c5213dc`(+ 스펙 커밋 `a692beb`), 전체 **311개** 통과.
- 태스크마다 전체 스위트를 돌려 회귀 0을 확인하고 커밋한다.
- **`apply_quota` 함수와 `FeedSpec.quota` 필드는 지우지 않는다.** `ai_brief`가 여전히 쓴다(스펙 §3.1).
- **공허한 테스트 금지.** 각 태스크의 "잡는 뮤테이션"을 **실제로 코드에 적용해 FAIL을 눈으로 확인**한 뒤 되돌린다. 이 저장소에서 공허한 테스트가 3번 나왔고 전부 "구현을 실제로 깨뜨려도 통과함"을 재현해서 잡았다. 주장이 아니라 재현이어야 한다.

---

## File Structure

| 파일 | 책임 | 변경 |
|---|---|---|
| `econ_insta/collector.py` | 피드 수집 · 선별 | `gather_articles` 신설, `collect_articles` 재구성, `collect()` quota 폐기 |
| `econ_insta/summarizer.py` | 프롬프트 조립 · 요약 | `PROMPT_ISSUES`·`PROMPT_ARTICLES` 상수, `summarize` 슬라이스, `render_issue` 슬라이스 |
| `tests/test_collector.py` | collector 테스트 | 신규 테스트 추가 |
| `tests/test_summarize_single_issue.py` | 단일 이슈 요약 테스트 | 신규 픽스처 + 테스트 추가 |

`econ_insta/issues.py`는 **건드리지 않는다.** `ai_brief.py`·`blog_brief.py`도 건드리지 않는다.

---

### Task 1: `gather_articles` 분리 (순수 리팩터, 동작 불변)

`collect_articles`에서 선별 두 줄(`apply_quota`, `[:limit]`)만 남기고 나머지를 `gather_articles`로 뗀다. **이 태스크에서 데일리 동작은 아직 안 바뀐다** — `collect()`는 여전히 `collect_articles`를 쓴다.

**Files:**
- Modify: `econ_insta/collector.py:369-399`
- Test: `tests/test_collector.py`

**Interfaces:**
- Consumes: 기존 `apply_quota(articles, feeds)`, `dedupe(articles)`, `fetch_feed(source, spec, session=)`, `now_kst()`, 모듈 전역 `FEEDS`
- Produces: `gather_articles(feeds: dict[str, FeedSpec] | None = None, errors: list[str] | None = None) -> list[Article]` — 신선도·topic 필터를 통과한 기사 전량, 최신순 정렬 + dedupe 완료, **quota·limit 미적용**. Task 2가 `collect()`에서 이것을 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_collector.py`의 `CollectArticlesTest` 클래스 안에 추가한다(이 클래스가 `self._patch`와 `self.FOREVER`를 갖고 있다):

```python
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
        feed = rss(
            item(title="[인사] 한국수출입은행", link="https://x/1"),
            item(title="코스피 급등", link="https://x/2"),
            item(title="작년 기사", link="https://x/3", pub="Mon, 01 Jan 2024 00:00:00 +0900"),
        )
        self._patch(FakeSession({"https://a": feed}))
        feeds = {"매일경제": FeedSpec("https://a", quota=99)}
        self.assertEqual([a.title for a in gather_articles(feeds=feeds)], ["코스피 급등"])
```

같은 파일 상단 import 블록(`tests/test_collector.py:8-21`)에 `gather_articles`를 추가한다. 알파벳 순서를 지킨다 — `dedupe` 다음, `is_boilerplate` 앞이다:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

```bash
PYTHONIOENCODING=utf-8 python -m unittest tests.test_collector -q
```

Expected: `ImportError: cannot import name 'gather_articles' from 'econ_insta.collector'`

- [ ] **Step 3: 최소 구현**

`econ_insta/collector.py:369-399`의 `collect_articles`를 통째로 아래로 교체한다:

```python
def gather_articles(
    feeds: dict[str, FeedSpec] | None = None,
    errors: list[str] | None = None,
) -> list[Article]:
    """모든 피드에서 신선한 기사를 **전량** 모아 최신순으로 돌려준다.

    매체별 상한도 전체 상한도 적용하지 않는다 — 버리는 것은 이슈 랭킹 뒤에서 한다
    (스펙 2026-07-17-collector-quota-design.md §4.1). 수집 기간은 매체별로 다르다
    (주간지는 창을 넓게 잡는다).

    최신순 정렬은 무엇을 버릴지 정하기 위한 것이 아니라, rank_issues의 탐욕적
    클러스터링이 시드를 여는 순서를 결정론적으로 고정하기 위한 것이다.
    """
    specs = feeds or FEEDS
    session = requests.Session()
    now = now_kst()

    gathered: list[Article] = []
    for source, spec in specs.items():
        try:
            fetched = fetch_feed(source, spec, session=session)
        except CollectError as exc:
            if errors is None:
                raise
            errors.append(str(exc))
            continue
        cutoff = now - timedelta(hours=spec.max_age_hours)
        fresh = [a for a in fetched if a.published >= cutoff]
        if spec.topic is not None:
            fresh = [a for a in fresh if spec.topic.search(f"{a.title} {a.summary}")]
        gathered.extend(fresh)

    gathered.sort(key=lambda a: a.published, reverse=True)
    return dedupe(gathered)


def collect_articles(
    limit: int = 20,
    feeds: dict[str, FeedSpec] | None = None,
    errors: list[str] | None = None,
) -> list[Article]:
    """최신순 + 매체별 쿼터 + 전체 상한.

    ai_brief·blog_brief 전용이다. **데일리 브리핑은 이 함수를 쓰지 않는다** —
    쿼터가 중요도를 못 보고 그날의 뉴스를 버리기 때문이다(스펙 §1). 데일리는
    gather_articles로 전량을 받아 rank_issues 뒤에서 자른다.
    """
    return apply_quota(gather_articles(feeds, errors), feeds or FEEDS)[:limit]
```

- [ ] **Step 4: 통과를 확인한다**

```bash
PYTHONIOENCODING=utf-8 python -m unittest tests.test_collector -q
```

Expected: OK. 신규 2개 포함.

기존 `collect_articles` 테스트 9개가 전부 통과해야 한다 — 이 태스크는 순수 리팩터라 **동작이 바뀌면 안 된다.** 특히 `test_quota_applied_per_source`·`test_respects_limit`·`test_merges_and_sorts_newest_first`가 통과하는지 눈으로 확인한다.

- [ ] **Step 5: 뮤테이션으로 테스트가 진짜인지 재현한다**

`gather_articles`의 마지막 줄을 `return apply_quota(dedupe(gathered), specs)`로 바꾼다.

```bash
PYTHONIOENCODING=utf-8 python -m unittest tests.test_collector -q
```

Expected: `test_gather는_매체별_상한을_적용하지_않는다` FAIL (5 != 2). 확인했으면 **되돌린다.**

- [ ] **Step 6: 전체 스위트**

```bash
PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q
```

Expected: OK, 313개(311 + 신규 2). 회귀 0.

- [ ] **Step 7: 커밋**

```bash
git add econ_insta/collector.py tests/test_collector.py
git commit -m "refactor(collector): gather_articles로 모으기와 선별을 분리한다"
```

---

### Task 2: `collect()`에서 quota를 폐기한다

데일리 경로가 실제로 바뀌는 태스크다.

**Files:**
- Modify: `econ_insta/collector.py:457-468`
- Test: `tests/test_collector.py`

**Interfaces:**
- Consumes: Task 1의 `gather_articles(feeds=None, errors=None) -> list[Article]`
- Produces: `collect() -> DailyBrief` — **`limit` 파라미터가 사라진다.** `DailyBrief.articles`가 quota·limit 없는 전량이 된다. Task 3이 이것을 `rank_issues`에 넣는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_collector.py`의 `CollectArticlesTest` 클래스 안에 추가한다:

```python
    def test_collect은_매체별_상한을_적용하지_않는다(self):
        """데일리 파이프라인의 입구. 여기서 quota가 그날의 뉴스를 버리고 있었다(스펙 §1.1).

        collect()는 feeds 인자가 없으므로 모듈 전역 FEEDS를 갈아끼운다.
        지표는 yfinance로 네트워크를 타므로 함께 막는다.
        """
        import econ_insta.collector as mod

        items = [item(title=f"기사{i}", link=f"https://x/{i}") for i in range(5)]
        self._patch(FakeSession({"https://a": rss(*items)}))

        self.addCleanup(setattr, mod, "FEEDS", mod.FEEDS)
        mod.FEEDS = {"매일경제": FeedSpec("https://a", max_age_hours=self.FOREVER, quota=2)}

        self.addCleanup(setattr, mod, "collect_quotes", mod.collect_quotes)
        mod.collect_quotes = lambda errors=None: []

        brief = mod.collect()
        self.assertEqual(len(brief.articles), 5)
```

`self.addCleanup(setattr, mod, "FEEDS", mod.FEEDS)`는 **덮어쓰기 전에** 호출해야 원본이 인자로 잡힌다. 순서를 바꾸면 복원이 망가진다.

- [ ] **Step 2: 실패를 확인한다**

```bash
PYTHONIOENCODING=utf-8 python -m unittest tests.test_collector -q
```

Expected: FAIL — `2 != 5` (`collect()`가 아직 `collect_articles`를 쓴다).

- [ ] **Step 3: 최소 구현**

`econ_insta/collector.py:457-460`을 교체한다:

```python
def collect() -> DailyBrief:
    """기사와 지표를 함께 모은다. 한쪽이 실패해도 brief.errors에 남기고 진행한다.

    기사는 **전량**이다(수백 건). 매체별 쿼터를 적용하지 않는다 — 쿼터는 중요도를
    못 보고 최신순으로 잘라 그날의 최대 뉴스를 버렸다(스펙 §1.1). 자르는 일은
    summarize()가 rank_issues 뒤에서 한다.
    """
    errors: list[str] = []
    articles = gather_articles(errors=errors)
```

이하(`try: quotes = collect_quotes(...)` 부터 `return DailyBrief(...)` 까지)는 그대로 둔다.

- [ ] **Step 4: 통과를 확인한다**

```bash
PYTHONIOENCODING=utf-8 python -m unittest tests.test_collector -q
```

Expected: OK.

- [ ] **Step 5: 뮤테이션 재현 — 두 방향**

**(a) 데일리 회귀**: `collect()`의 `articles = gather_articles(errors=errors)`를 `articles = collect_articles(errors=errors)`로 되돌린다.

Expected: `test_collect은_매체별_상한을_적용하지_않는다` FAIL. 되돌린다.

**(b) ai_brief 회귀 가드**: `collect_articles`에서 `apply_quota(...)`를 지워 `gather_articles(feeds, errors)[:limit]`로 만든다.

Expected: `test_quota_applied_per_source` FAIL. 이것이 스펙 §5 2행의 가드다 — quota를 전 파이프라인에서 지우는 실수를 막는다. 되돌린다.

- [ ] **Step 6: 전체 스위트**

```bash
PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q
```

Expected: OK, 314개. `collect()` 호출부 3곳(`collector.py:472`, `renderer.py:841`, `summarizer.py:366`)이 전부 무인자라 `limit` 제거로 깨지지 않는다.

- [ ] **Step 7: 커밋**

```bash
git add econ_insta/collector.py tests/test_collector.py
git commit -m "fix(collector): collect()가 매체별 쿼터 없이 기사 전량을 싣는다"
```

---

### Task 3: 프롬프트에 상위 `PROMPT_ISSUES`개 이슈만 싣는다

Task 2로 `brief.articles`가 수백 건이 됐다. 그대로 두면 `build_prompt`가 이슈 수백 개를 프롬프트에 실어 비용이 터진다.

**Files:**
- Modify: `econ_insta/summarizer.py:317` 및 상수 추가
- Test: `tests/test_summarize_single_issue.py`

**Interfaces:**
- Consumes: `rank_issues(articles) -> list[Issue]`, `build_prompt(brief, issues) -> str`, `_chosen_issue(payload, issues) -> Issue | None`
- Produces: 모듈 상수 `PROMPT_ISSUES: int = 10`. Task 4가 같은 자리에 `PROMPT_ARTICLES`를 더한다.

**⚠ 이 태스크에서 제일 조심할 것:** 슬라이스는 `summarize()` 안에서 **한 번만** 하고 `build_prompt`와 `_chosen_issue`에 **같은 리스트 객체**를 넘긴다. `build_prompt` 안에서 자르면 프롬프트의 `[이슈 N]` 번호와 `_chosen_issue`의 `issues[N-1]`이 어긋나 모델이 본 적 없는 이슈가 `Briefing.issue`에 실린다. 기존 이음매 테스트(`test_프롬프트_번호와_chosen_issue_매핑이_일치한다`)가 이것을 잡는다 — **지우거나 우회하지 말 것.**

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_summarize_single_issue.py`의 `sample_brief()` 정의(80-87행) 바로 아래에 픽스처를 추가한다:

```python
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
```

그리고 `SummarizeSingleIssueTest` 클래스 안에 테스트를 추가한다:

```python
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
```

`assertEqual(len(rank_issues(...)), 12)`는 픽스처 방어다 — `_TOPICS`가 실수로 합쳐지면 이 테스트가 아무것도 안 지키게 되므로 먼저 걸린다.

- [ ] **Step 2: 실패를 확인한다**

```bash
PYTHONIOENCODING=utf-8 python -m unittest tests.test_summarize_single_issue -q
```

Expected: FAIL — `'[이슈 11]' unexpectedly found in ...` (아직 12개가 전부 실린다).

만약 픽스처 방어 줄(`assertEqual(..., 12)`)에서 먼저 실패하면 `_TOPICS` 중 핵심어가 2개 겹치는 쌍이 있다는 뜻이다. `econ_insta/issues.py`의 `keywords()`로 직접 확인해 겹치는 주제를 바꾼다.

- [ ] **Step 3: 최소 구현**

`econ_insta/summarizer.py`에 상수를 추가한다. 기존 길이 상수(`CARD_BODY_MAX` 등)가 모여 있는 자리 옆에 둔다:

```python
PROMPT_ISSUES = 10
"""모델에 보일 이슈 수. collect()가 기사 전량을 싣게 되면서(스펙 §4.2) 자르는 지점이
여기로 옮겨왔다. 상위 10개면 매체 2곳 이상짜리 진짜 이슈는 확실히 들어온다."""
```

`summarize()`의 317행을 바꾼다:

```python
    issues = rank_issues(brief.articles)[:PROMPT_ISSUES]
```

**`build_prompt` 안에서 자르지 않는다.** `summarize()`가 이 한 리스트를 `build_prompt`와 `_chosen_issue` 양쪽에 그대로 넘기는 것이 계약이다.

- [ ] **Step 4: 통과를 확인한다**

```bash
PYTHONIOENCODING=utf-8 python -m unittest tests.test_summarize_single_issue -q
```

Expected: OK. 기존 이음매 테스트와 이슈 계약 테스트가 전부 통과해야 한다.

- [ ] **Step 5: 뮤테이션 재현 — 두 가지**

**(a) 슬라이스 누락**: `[:PROMPT_ISSUES]`를 지운다.

Expected: `test_프롬프트에_상위_10개_이슈만_실린다` FAIL. 되돌린다.

**(b) 이음매 파괴 (중요)**: 슬라이스를 `summarize()`에서 빼고 `build_prompt` 안(189행)으로 옮긴다:

```python
    blocks = "\n\n".join(render_issue(iss, i) for i, iss in enumerate(issues[:PROMPT_ISSUES], 1))
```

이 상태에서 `summarize()`는 `_chosen_issue`에 자르지 않은 리스트를 넘긴다. 모델이 11번을 고르면 프롬프트에 없는 이슈가 `Briefing.issue`에 실린다.

```bash
PYTHONIOENCODING=utf-8 python -m unittest tests.test_summarize_single_issue -q
```

**기존 이음매 테스트가 이것을 잡는지 확인한다.** 잡지 못하면 — 즉 (b) 뮤테이션이 전량 통과하면 — **멈추고 보고한다.** 이음매 가드가 이 변경을 못 막는다는 뜻이고, 스펙 §4.3의 전제가 틀린 것이다. 되돌린다.

- [ ] **Step 6: 전체 스위트**

```bash
PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q
```

Expected: OK, 315개.

- [ ] **Step 7: 커밋**

```bash
git add econ_insta/summarizer.py tests/test_summarize_single_issue.py
git commit -m "feat(summarizer): 프롬프트에 상위 10개 이슈만 싣는다"
```

---

### Task 4: 이슈당 `PROMPT_ARTICLES`건만 나열한다

**Files:**
- Modify: `econ_insta/summarizer.py:171-179` (`render_issue`) 및 상수 추가
- Test: `tests/test_summarize_single_issue.py`

**Interfaces:**
- Consumes: Task 3의 `PROMPT_ISSUES` 옆에 상수를 더한다. `render_issue(issue: Issue, index: int) -> str` (기존 시그니처 유지)
- Produces: 모듈 상수 `PROMPT_ARTICLES: int = 5`. 후속 태스크 없음.

**⚠ 두 가지를 반드시 지킨다:**
1. 헤더의 `기사 N건`은 **전체 기사 수**를 말한다(잘린 수가 아니다). 그 숫자가 이슈 크기 신호라 모델이 봐야 한다.
2. **`Issue.articles`를 파괴적으로 자르지 않는다.** `photos.candidates(issue)`가 표지 사진 후보를 그 리스트의 `images`에서 뽑는다 — 자르면 4단계가 확보한 사진 도달률이 떨어진다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_summarize_single_issue.py`의 `many_issue_brief()` 아래에 픽스처를 추가한다:

```python
def big_issue_brief():
    """기사 8건이 한 이슈로 묶이는 브리프. 이슈당 나열 수 자르기를 검증한다.

    제목의 핵심어(한은·기준금리·인상)가 전부 같아 rank_issues가 하나로 묶는다.
    끝의 숫자는 _WORD_RE가 잡지 않으므로 핵심어에 영향이 없다.
    """
    sources = ["연합뉴스", "매일경제", "한국경제"]
    arts = [art(f"한은 기준금리 인상 {i}", sources[i % 3]) for i in range(8)]
    quotes = [Quote(symbol="^KS11", name="코스피", price=2981.4, change_pct=-2.14)]
    return DailyBrief(articles=arts, quotes=quotes, collected_at=datetime(2026, 7, 16), errors=[])
```

그리고 새 테스트 클래스를 파일 끝에 추가한다:

```python
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
```

같은 파일 9행의 import에 `render_issue`를 추가한다. 현재 `SYSTEM`이 두 번 적혀 있으니 그 중복도 함께 정리한다:

```python
from econ_insta.summarizer import summarize, build_prompt, render_issue, SCHEMA, SYSTEM
```

- [ ] **Step 2: 실패를 확인한다**

```bash
PYTHONIOENCODING=utf-8 python -m unittest tests.test_summarize_single_issue -q
```

Expected: `test_기사를_5건까지만_나열한다` FAIL (`8 != 5`). 나머지 2개는 현재 구현에서도 통과한다 — 그것이 정상이다(회귀 가드이지 새 기능이 아니다).

- [ ] **Step 3: 최소 구현**

`econ_insta/summarizer.py`의 `PROMPT_ISSUES` 아래에 상수를 더한다:

```python
PROMPT_ARTICLES = 5
"""이슈당 모델에 보일 기사 수. 카드 4장 서사에 충분하다.

자르는 것은 프롬프트 표시분뿐이다 — Issue.articles는 온전히 남긴다.
photos.candidates(issue)가 표지 사진 후보를 그 전 기사에서 뽑기 때문이다(스펙 §4.4).
"""
```

`render_issue`의 174행 루프만 바꾼다:

```python
    for article in issue.articles[:PROMPT_ARTICLES]:
```

**173행 헤더는 건드리지 않는다** — `len(issue.articles)`가 전체 수를 말해야 한다.

- [ ] **Step 4: 통과를 확인한다**

```bash
PYTHONIOENCODING=utf-8 python -m unittest tests.test_summarize_single_issue -q
```

Expected: OK.

- [ ] **Step 5: 뮤테이션 재현 — 세 가지**

**(a)** `[:PROMPT_ARTICLES]` 제거 → `test_기사를_5건까지만_나열한다` FAIL. 되돌린다.

**(b)** 헤더를 `기사 {len(issue.articles[:PROMPT_ARTICLES])}건`으로 바꿈 → `test_헤더는_전체_기사_수를_말한다` FAIL. 되돌린다.

**(c)** `render_issue` 첫 줄에 `issue.articles = issue.articles[:PROMPT_ARTICLES]`를 넣어 파괴적으로 자름 → `test_렌더가_issue_articles를_자르지_않는다` FAIL. 되돌린다.

세 뮤테이션이 각각 **정확히 해당 테스트 1건만** 떨어뜨리는지 확인한다. 여러 개가 같이 떨어지면 테스트가 서로 중복된 것이다.

- [ ] **Step 6: 전체 스위트**

```bash
PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q
```

Expected: OK, 318개(315 + 신규 3).

- [ ] **Step 7: 커밋**

```bash
git add econ_insta/summarizer.py tests/test_summarize_single_issue.py
git commit -m "feat(summarizer): 이슈당 기사 5건까지만 프롬프트에 나열한다"
```

---

### Task 5: 실데이터 검증 (이 계획의 합격 기준)

**단위 테스트로는 원 결함을 못 잡았다** — 311개가 통과하는 상태에서 살아 있었다(스펙 §1.3). 이 태스크가 진짜 검증이다. **모델 호출도 발행도 하지 않는다** — 수집과 랭킹만 본다(비용 0).

**Files:**
- Create: 스크래치패드 일회성 스크립트 (저장소에 커밋하지 않는다)
- Modify: `.superpowers/sdd/progress.md` (진행 원장, git-ignored)

**Interfaces:**
- Consumes: `gather_articles()`, `collect_articles()`, `rank_issues()`

- [ ] **Step 1: 검증 스크립트를 쓴다**

경로: `C:\Users\user\AppData\Local\Temp\claude\C--Users-user\e0ffd954-8c93-43ef-8f26-2a1ea9dd99a7\scratchpad\verify_quota.py`

```python
"""수집기 quota 폐기 실데이터 검증. 모델 호출 없음, 발행 없음."""
from econ_insta.collector import collect_articles, gather_articles
from econ_insta.issues import rank_issues

errors: list[str] = []
full = gather_articles(errors=errors)
old = collect_articles(errors=[])

print(f"gather_articles(전량): {len(full)}건")
print(f"collect_articles(옛 경로, quota+limit): {len(old)}건")
print(f"수집 오류: {errors}\n")

by_source: dict[str, int] = {}
for a in full:
    by_source[a.source] = by_source.get(a.source, 0) + 1
print("매체별 수집량:", by_source, "\n")

issues = rank_issues(full)
print(f"이슈 총 {len(issues)}개. 상위 10개:\n")
for i, iss in enumerate(issues[:10], 1):
    print(f"[{i}] 매체 {len(iss.sources)}곳({', '.join(sorted(iss.sources))}), 기사 {len(iss.articles)}건")
    for a in iss.articles[:5]:
        print(f"      ({a.source}) {a.title}")
    print()

멀티 = sum(1 for iss in issues[:10] if len(iss.sources) >= 2)
print(f"상위 10개 중 매체 2곳 이상: {멀티}개")
```

- [ ] **Step 2: 실행한다**

```bash
cd /c/Users/user/econ-insta && PYTHONIOENCODING=utf-8 python "C:\Users\user\AppData\Local\Temp\claude\C--Users-user\e0ffd954-8c93-43ef-8f26-2a1ea9dd99a7\scratchpad\verify_quota.py"
```

네트워크를 탄다(RSS 5개 피드). 수십 초 걸릴 수 있다.

- [ ] **Step 3: 합격 기준을 눈으로 대조한다 (스펙 §6)**

| # | 확인할 것 | 기대 |
|---|---|---|
| 1 | `gather_articles` 건수 | 수백 건. **20건이면 실패** |
| 2 | 상위 10개에 그날의 진짜 뉴스가 있는가 | 사람이 판단 |
| 3 | 상위 10개 중 매체 2곳 이상인 이슈 수 | **1개 이상.** 0이면 설계가 틀렸다 |
| 4 | 광고성 영문 리스티클이 먹는 슬롯 수 | 세어서 기록 (스펙 §7 판단 근거) |

**대조 기준**: 2026-07-17 quota 우회 실측에서 한은 금리인상 클러스터가 매체 3곳으로 잡혔다. 같은 성격의 크로스소스 이슈가 상위에 못 올라오면 **멈추고 보고한다** — 코드가 아니라 설계가 틀린 것이다.

**주의**: 이 검증은 그날의 뉴스에 의존한다. 조용한 날이면 매체 2곳 이상 이슈가 원래 적을 수 있다. 3번이 0이면 실패로 단정하기 전에 매체별 수집량과 상위 이슈 제목을 함께 보고한다.

- [ ] **Step 4: 진행 원장에 결과를 기록한다**

`.superpowers/sdd/progress.md` 끝에 실측 수치를 붙인다. **git-ignored이지만 이 저장소의 durable 기록이다** — 단계별 커밋·리뷰 이력·Minor 목록이 전부 여기 있다. 커밋 대상이 아니므로 `git add`하지 않는다.

기록할 것: `gather_articles` 건수, 매체별 분포, 상위 10개 이슈의 매체 수/기사 수/제목, 매체 2곳 이상 이슈 개수, 리스티클 슬롯 수, 그리고 §7 노이즈 필터에 대한 판단 근거.

- [ ] **Step 5: 보고**

수치를 사용자에게 보고한다. **커밋할 코드가 없다** — 스크립트는 스크래치패드고 원장은 git-ignored다.

---

## Self-Review

**1. 스펙 커버리지**

| 스펙 | 태스크 |
|---|---|
| §3.1 데일리에서 quota 폐기, `apply_quota`·`FeedSpec.quota`는 남김 | Task 1·2 |
| §3.2 상위 10개 이슈 · 이슈당 5건 | Task 3·4 |
| §3.3 노이즈 필터 제외 | 태스크 없음 (의도적, §7) |
| §4.1 자르는 지점 이동 | Task 2·3 |
| §4.2 `gather_articles` 분리, `collect()` limit 제거 | Task 1·2 |
| §4.3 슬라이스 1회 + 이음매 유지 | Task 3 (Step 5b가 직접 재현) |
| §4.4 표시분만 자름, 헤더 진짜 수, `Issue.articles` 불변 | Task 4 (뮤테이션 3종) |
| §5 테스트 표 7행 | Task 1(2) + 2(1 + 기존 quota 테스트가 ai_brief 회귀 담당) + 3(1) + 4(3) + 기존 이음매 테스트 |
| §6 실데이터 검증 4항목 | Task 5 |

**2. 플레이스홀더 스캔**: 없음. 모든 코드 스텝에 실제 코드가 있다.

**3. 타입 일관성**: `gather_articles(feeds=None, errors=None) -> list[Article]`가 Task 1에서 정의되고 Task 2·5에서 같은 이름·시그니처로 쓰인다. `PROMPT_ISSUES`(Task 3)·`PROMPT_ARTICLES`(Task 4)는 같은 모듈의 인접 상수다. `render_issue(issue, index)`는 기존 시그니처를 유지한다.

**남은 리스크 (구현자가 알아야 할 것)**

- **Task 3 Step 1의 `_TOPICS` 12개가 실제로 이슈 12개로 갈라지는지는 계획 작성 시점에 `keywords()`를 손으로 대조한 것이지 실행으로 확인한 것이 아니다.** 픽스처 방어 줄이 이것을 즉시 드러낸다. 갈라지지 않으면 주제를 바꾼다.
- **Task 5의 3번 기준(매체 2곳 이상 1개 이상)은 그날의 뉴스에 의존한다.** 조용한 날엔 원래 적을 수 있다. 0이라고 즉시 실패로 단정하지 말고 수치와 함께 보고한다.
- Task 2가 `mod.FEEDS`·`mod.collect_quotes`를 갈아끼우는 것은 이 저장소의 기존 패턴(`_patch`가 `mod.requests.Session`을 갈아끼움)을 따른 것이다.

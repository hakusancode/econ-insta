# 수집기 quota — 자르는 지점을 중요도 뒤로 옮긴다

- 관련 스펙: `docs/superpowers/specs/2026-07-17-issue-contract-design.md` §7 (이 변경을 후속으로 예고함)
- 브랜치: `main` (`c5213dc` 위에 얹음)
- 날짜: 2026-07-17

## 1. 문제

`collect_articles`가 그날의 진짜 뉴스를 버린다. **이슈 계약·`bg_query`·표지 사진 소싱이 전부 정상 작동하는데도
브리핑이 계속 빈약했던 이유가 이것이다 — 코드는 되는데 입력이 잘못 들어가고 있었다.**

```python
gathered.sort(key=lambda a: a.published, reverse=True)      # collector.py:398
return apply_quota(dedupe(gathered), specs)[:limit]         # collector.py:399
```

발행시각 내림차순으로 정렬한 뒤 매체별 quota(연합 4·한경 5·매경 5·WSJ 3·Economist 2, 전체 `limit=20`)를
적용한다. `apply_quota`는 리스트 순서대로 각 매체의 앞 N건을 남기므로(collector.py:206-216),
**중요도를 전혀 보지 않고 매체별 "가장 최신 N건"만 남긴다.**

그리고 `summarize()`는 그렇게 잘린 20건 위에서 이슈를 랭크한다:

```python
issues = rank_issues(brief.articles)                        # summarizer.py:317
```

즉 이슈 랭킹은 574건이 아니라 **"최신 20건"** 만 보고 그날의 최대 이슈를 고르고 있었다.

### 1.1 실측 (2026-07-17 09시 수집, 추측 아님)

피드 원본 **574건** 중 파이프라인이 보는 것은 **20건**. 연합뉴스는 원본 113건 중 quota=4가 남긴 것이:

| 남은 것 (최신 4건) | 버려진 것 |
|---|---|
| 무신사 배틀그라운드 협업상품 | **07-16 16:26 코스피, 6%대 급락 6,800대 후퇴…하루 만에 7,000선 내줘(종합)** |
| 이마트24–콘진원 MOU | **07-16 16:51 기준금리 올렸지만 7~8월 연속 인상 우려 완화** |
| HS화성 서귀포항 공사 수주 | 07-16 16:05 반도체 또 출렁…삼전 8.8%·하닉 11.5% 급락 |
| 호르무즈 봉쇄 | 07-16 16:01 美금리 인상 기대 꺾이고 한은은 올리고 |

한은 기준금리 인상과 코스피 6% 급락(그날 최대 뉴스)을 통째로 버리고 아침 홍보성 단신을 남겼다.
매경에서도 09:50 한은 금리인상 2.75% 속보, 13:49 신현송 총재 발언이 같은 방식으로 잘렸다.

**이것이 그동안 증상 전부의 근원이다**: `rank_issues()[0]`이 "HS화성 항만공사"였던 이유, 이슈 상위가
전부 매체 1곳이었던 이유, 카드 4장이 전부 매일경제 단일 출처였던 이유.

### 1.2 반대 실측 — 코드는 이미 된다

quota를 우회해 `fetch_feed` 원본에서 금리 클러스터를 직접 골라 넣으니 **같은 파이프라인이**
매체 3곳 크로스소스 + 총재 얼굴 표지(4단계 이미지 소싱 첫 성공) + `role` 4단계 서사를 냈다.
발행: https://www.instagram.com/p/Da38QcCEuVG/

고칠 것은 파이프라인이 아니라 파이프라인에 들어가는 입력이다.

### 1.3 왜 테스트로 안 잡혔나

311개 테스트가 통과하는 상태에서 살아 있었다. `apply_quota`에는 전용 테스트가 4개 있고 전부 통과한다 —
**quota는 명세대로 정확히 동작하고 있다.** 명세가 틀렸다. "매체별 최신 N건을 남긴다"는 계약을 코드가
충실히 지켰고, 그 계약이 그날의 뉴스를 버리는 계약이었다. 단위 테스트로 잡을 수 있는 종류가 아니다.

발견 경로는 실데이터 실행이었다. 이 프로젝트에서 반복되는 패턴이다(4단계 이슈 계약 결함도 동일).

## 2. 진단 — quota는 잘못 놓였을 뿐 아니라 불필요하다

`FeedSpec.quota`의 docstring은 목적을 이렇게 적는다:

> 한 매체가 브리핑을 독식하지 않도록 매체별 상한을 둔다. — collector.py:39

그런데 **`rank_issues`가 이미 그 일을 하고 있고, 더 잘한다.**

```python
@property
def score(self) -> tuple[int, int]:
    """크로스소스 빈도: (서로 다른 매체 수, 기사 수). 큰 것이 큰 이슈."""
    return (len(self.sources), len(self.articles))          # issues.py:48-50
```

정렬 첫 키가 **매체 수**다. 한 매체가 자기 주제로 피드를 도배해도 매체 1곳짜리 이슈는 매체 3곳짜리를
절대 못 이긴다. 독식 방지는 이슈 단위에서, **그것도 중요도를 보면서** 이미 달성되고 있다.

quota는 같은 목적을 **중요도를 못 보는 채로 더 이른 단계에서** 흉내내다가 입력을 망가뜨린다.
게다가 3단계a에서 브리핑이 "3~5건 모음집"에서 "단일 이슈"로 바뀌면서 quota의 전제("브리핑을 여러
매체가 나눠 갖는다")는 이미 사라졌다. 단일 이슈 브리핑에서는 그 이슈를 다룬 기사가 많을수록 좋다.

## 3. 사용자 결정 (2026-07-17 세션)

1. **데일리 경로에서 quota를 폐기한다.** `apply_quota` 함수와 `FeedSpec.quota` 필드는 `ai_brief`가
   여전히 쓰므로 남긴다. 데일리만 안 쓴다.
2. **프롬프트에 상위 10개 이슈 · 이슈당 기사 5건까지 싣는다.** 최대 50건이 프롬프트에 들어간다
   (현재 20건의 2.5배, 비용 $0.06~0.12 추정). 이슈 계약 스펙 §7이 "비용 별도 판단"으로 남겨둔 항목의 결론이다.
3. **노이즈 필터는 이번 범위에서 제외한다.** 실측 후 판단(§7).

## 4. 설계

### 4.1 자르는 지점을 옮긴다

```
현재: fetch(574) → 신선도 → 최신순 정렬 → dedupe → quota → [:20] → rank_issues(20) → 프롬프트(전체 이슈)
                                                    ↑ 중요도를 못 본 채 여기서 그날의 뉴스가 죽는다

새로: fetch(574) → 신선도 → 최신순 정렬 → dedupe → rank_issues(574) → [:10] → 프롬프트(이슈당 5건)
                                                                        ↑ 중요도를 본 뒤 자른다
```

최신순 정렬은 **남기되 역할이 바뀐다.** 더 이상 "무엇을 버릴지"를 정하지 않고, 탐욕적 클러스터링의
시드 선택 순서를 결정론적으로 고정하는 역할만 한다(`rank_issues`는 리스트 순서대로 시드를 연다).

### 4.2 collector — 수집과 선별을 분리한다

`collect_articles`는 `ai_brief`(호출 `ai_brief.py:177`)와 `blog_brief`(호출 `blog_brief.py:396`)의
계약이므로 **동작을 그대로 둔다.** 그 몸통에서 "모으기"만 떼어낸다.

```python
def gather_articles(feeds=None, errors=None) -> list[Article]:
    """신선한 기사 전량. 매체별 상한도 전체 상한도 없다.

    최신순 정렬·dedupe는 한다 — 버리기 위해서가 아니라 클러스터링 시드 순서를 고정하기 위해서다.
    """

def collect_articles(limit=20, feeds=None, errors=None) -> list[Article]:
    """최신순 + 매체별 quota + 상한. ai_brief·blog_brief 전용 — 데일리는 쓰지 않는다."""
    return apply_quota(gather_articles(feeds, errors), feeds or FEEDS)[:limit]

def collect() -> DailyBrief:
    articles = gather_articles(errors=errors)      # 전량. quota 없음
```

`gather_articles`가 가져가는 것: `specs = feeds or FEEDS` 결정, 세션 생성, 피드별 `fetch_feed` +
`CollectError` 처리, 신선도 컷오프(`spec.max_age_hours`), `spec.topic` 정규식 필터, 최신순 정렬, `dedupe`.
남는 것: `apply_quota`와 `[:limit]` — 정확히 선별 두 줄이다.

`collect()`의 `limit` 파라미터는 의미를 잃으므로 제거한다. 호출부 3곳(`collector.py:472`,
`renderer.py:841`, `summarizer.py:366`)이 전부 무인자 호출이라 안전하다.

`DailyBrief.articles`가 20건에서 **수백 건 규모**가 된다. §1.1의 574건은 피드 원본 총량이고
`gather_articles`는 그 앞에 신선도 컷오프(collector.py:392-393)를 적용하므로 실제 수는 그보다 적다.
정확한 수는 관측 전엔 모른다 — §6.1에서 처음 측정한다. 이것을 소비하는 곳은 `summarize()`의
`rank_issues(brief.articles)`와 `build_prompt`의 빈 리스트 검사뿐이다.

### 4.3 summarizer — 프롬프트에서 자른다

```python
PROMPT_ISSUES = 10        # 모델에 보일 이슈 수
PROMPT_ARTICLES = 5       # 이슈당 보일 기사 수
```

```python
issues = rank_issues(brief.articles)[:PROMPT_ISSUES]   # 한 번만 계산
prompt = build_prompt(brief, issues)                    # 같은 객체
...
issue = _chosen_issue(payload, issues)                  # 같은 객체
```

**이 설계에서 제일 조심할 곳이다.** 슬라이스는 `summarize()` 안에서 **한 번만** 하고,
`build_prompt`와 `_chosen_issue`에 **같은 리스트 객체**가 가야 한다. `build_prompt` 안에서 자르면
프롬프트의 `[이슈 N]` 번호와 `_chosen_issue`의 `issues[N-1]`이 어긋나 **모델이 본 적 없는 이슈가
`Briefing.issue`에 실려 나간다.**

이슈 계약 스펙 §7이 이 변경을 후속으로 예고했고, 최종 리뷰가 **정확히 이 위험을 예측해**
이음매 테스트를 심어뒀다(`fab5cd5`, `test_프롬프트_번호와_chosen_issue_매핑이_일치한다`).
그 테스트가 이 스펙의 구현자를 잡는 가드다. 지우거나 우회하지 말 것.

### 4.4 render_issue — 표시분만 자른다

```python
for article in issue.articles[:PROMPT_ARTICLES]:
```

헤더의 `기사 {len(issue.articles)}건`은 **진짜 수를 유지한다.** 그 숫자가 이슈 크기 신호라
모델이 봐야 한다("기사 12건인데 5건만 보임" 상태가 정상이다).

**자르는 것은 프롬프트 표시분뿐이고 `Issue.articles`는 온전히 남긴다.**
`photos.candidates(issue)`가 표지 사진 후보를 `issue.articles`의 `images`에서 뽑기 때문에,
사진 후보는 5건이 아니라 그 이슈의 전 기사에서 나와야 한다. 4단계가 확보한 사진 도달률을
이 변경이 깎으면 안 된다.

## 5. 테스트

단위 테스트로는 원 결함을 못 잡았다(§1.3). 그러므로 단위 테스트는 **회귀 방지용**이고
검증의 본체는 실데이터다(§6).

| 테스트 | 잡는 뮤테이션 |
|---|---|
| `collect()`가 매체별 상한을 적용하지 않는다 | `gather_articles` 자리에 `collect_articles`를 되돌림 |
| `collect_articles()`는 여전히 quota를 적용한다 | ai_brief 회귀 — quota를 전 파이프라인에서 지움 |
| 슬라이스 뒤에도 프롬프트 번호 ↔ `_chosen_issue` 일치 | 기존 이음매 테스트(`fab5cd5`)가 이미 담당 |
| `build_prompt`가 상위 `PROMPT_ISSUES`개만 싣는다 | 슬라이스 누락 |
| `render_issue`가 `PROMPT_ARTICLES`건만 나열한다 | 슬라이스 누락 |
| `render_issue` 헤더는 전체 기사 수를 말한다 | `len(issue.articles[:5])`로 잘못 셈 |
| `Issue.articles`가 프롬프트 렌더 뒤에도 안 잘린다 | 슬라이스를 `Issue`에 파괴적으로 적용 → 사진 후보 소실 |

**공허한 테스트 주의**: 이 저장소에서 공허한 테스트가 3번 나왔고 전부 리뷰어가 **구현을 실제로
깨뜨려 테스트가 여전히 통과함을 재현**해서 잡았다. 위 표의 "잡는 뮤테이션" 칸은 주장이 아니라
구현자가 실제로 되돌려 FAIL을 확인해야 하는 항목이다.

## 6. 실데이터 검증 (이 스펙의 합격 기준)

구현 뒤 오늘 피드로 실행해 **눈으로 확인한다.**

1. `gather_articles()`가 몇 건을 돌려주는가 (기대: 수백 건, 20건이 아님)
2. `rank_issues()` 상위 10개를 출력해 **그날의 진짜 뉴스가 상위에 있는가**
3. 매체 수가 2곳 이상인 이슈가 상위에 실제로 나타나는가 (지금까지 상위 3개가 전부 매체 1곳이었음)
4. 노이즈(광고성 영문 리스티클)가 상위 10에서 **몇 슬롯을 먹는가** — §7 판단 근거

대조 기준이 있다: 2026-07-17 quota 우회 실측에서 한은 금리인상 클러스터가 매체 3곳으로 잡혔다.
같은 날 데이터로 `rank_issues`가 그것을 상위에 올리지 못하면 이 설계가 틀린 것이다.

## 7. 이 스펙이 하지 않는 것

- **노이즈 필터** — 영문 리스티클이 상위 슬롯을 먹는 문제. §6.4 실측 후 별도 판단.
  한 번에 두 가지를 바꾸면 어느 쪽이 효과를 냈는지 못 가린다.
- **`ai_brief` 변경** — quota 계약을 그대로 유지한다.
- **`render_card`의 `role` 미렌더** — 3단계a가 넣은 죽은 데이터. 다음 작업 후보.
- **데일리 파이프라인 모듈 + 호스팅 결정** — `.gitignore`의 `out/` 모순 포함.
- **`min_shared`·`_STOPWORDS` 등 클러스터링 튜닝** — 574건 규모에서 클러스터링 품질이
  실제로 나쁜지는 §6에서 처음 관측된다. 관측 전에 손대지 않는다.

# 이슈 계약 — `summarize()`가 고른 이슈를 `Briefing`에 싣는다

- 상위 스펙: `docs/superpowers/specs/2026-07-16-image-sourcing-design.md` (4단계) §4·§5
- 브랜치: `card-redesign` (4단계 완료 `9a215d7` 위에 얹음)
- 날짜: 2026-07-17

## 1. 문제

4단계(표지 이미지 소싱)는 6개 태스크·리뷰 클린·테스트 299개 통과로 닫혔지만 **실전에서 안 걸린다.**
2026-07-16 실데이터 렌더에서 드러났다.

`summarize()`는 내부에서 `rank_issues()`로 후보를 좁혀 프롬프트에 싣고 **모델이 그중 하나를 고른다.**
그런데 그 선택이 `Briefing`에 없다. `Briefing`은 `headline`·`indicator_note`·`cards`·`quotes`·토큰만 들고 나온다.

그래서 배경을 조달하는 호출부는 `photos.pick(issue)`에 넘길 `Issue`를 구할 데가 없어
`rank_issues()[0]`을 **따로** 부를 수밖에 없고, 그 값은 모델의 선택과 갈린다.

### 1.1 실측 (2026-07-16 실물 확인, 추측 아님)

| | 값 |
|---|---|
| 모델이 고른 이슈 (훅) | "AI 버블 경고 속 코스피 곤두박질" |
| `rank_issues()[0]` | "10 of the Best Financial Advisor Companies" (WSJ·Economist 광고성 리스티클, 매체 2곳·기사 4건) |

그날 그 리스티클 이슈에 쓸 사진이 없어 그래픽 폴백으로 빠졌고 **불일치가 가려졌다.** 사진이 있었다면
재무상담사 기사 사진이 코스피 표지에 깔렸다.

### 1.2 왜 테스트로 안 잡혔나

배선은 전부 맞다. 계약이 비어 있다. 스펙은 `photos.pick(issue)`라고 적었지만 **"누가 그 이슈를 아는가"를
아무도 정하지 않았다.** 계획은 호출부가 안다고 가정했으나 호출부는 알 수 없다. 299개 통과와 무관한 종류의
결함이고, 3단계a가 보장하기로 한 표지-본문 주제 일치가 4단계에서 깨진다.

### 1.3 현재 소비 측 상태

`build_background(..., issue=...)`를 넘기는 **프로덕션 호출부는 없다.** `build_background`를 부르는 곳은
`ai_brief.py:391`과 `blog_brief.py:406` 둘뿐이고 둘 다 Issue 개념이 없는 다른 파이프라인이라
`issue=` 없이 부른다(`backgrounds.py:220-223` 주석이 명시). 데일리 브리핑 파이프라인은 아직 모듈이 아니고,
2026-07-16 실물 확인은 스크래치 스크립트로 손수 조립한 것이었다.

즉 결함은 실재하지만 그 계약을 쓸 호출부가 저장소에 없다. 이 스펙은 **계약을 만드는 데까지**다(§7).

## 2. 사용자 결정 (2026-07-17 세션)

1. **범위는 계약만** — 데일리 파이프라인 모듈화는 별도 단계로 남긴다.
2. **프롬프트 후보 개수·본문요약 길이는 안 건드린다** — 비용은 별도 판단(§7).
3. **모델에게 직접 물어본다** — 스키마에 이슈 번호 필드를 추가한다. 카드 출처로 역추적하는 휴리스틱은
   쓰지 않는다: 진단이 "계약이 비어 있다"인데 그 빈 자리를 또 추론으로 메우는 것이라 같은 병의 약한 버전이다.

## 3. 설계

### 3.1 `Briefing`

```python
@dataclass(frozen=True)
class Briefing:
    headline: str
    indicator_note: str
    cards: list[Card]
    quotes: list[Quote]
    issue: Issue | None = None      # 신규: 모델이 고른 이슈. None이면 표지는 그래픽.
    input_tokens: int = 0
    output_tokens: int = 0
    dropped_cards: int = 0
```

기본값이 있어 기존 생성부는 그대로 돈다. `Briefing(...)` 호출부는 `summarizer.py:302`와
`tests/test_renderer.py:67`(`Briefing(**{**defaults, **overrides})`) 둘뿐이고 **전부 키워드인자라**
필드 위치가 순서를 깨지 않는다(확인함).

`Issue`는 `issues.py`에 이미 있다. `summarizer.py`가 `issues`를 import한다 — `issues.py`는 `collector`만
import하므로 순환이 없다(확인함).

### 3.2 스키마와 프롬프트

`SCHEMA["properties"]`에 추가하고 `required`에 넣는다:

```python
"issue_index": {"type": "integer", "description": "당신이 고른 이슈의 번호(프롬프트의 [이슈 N])"},
```

프롬프트는 이미 `render_issue()`가 `[이슈 1]`, `[이슈 2]` … 로 번호를 매겨 내보낸다
(`summarizer.py:149`, `enumerate(issues, 1)` — **1부터**). 모델은 자기가 읽은 번호를 되돌려주기만 하면 된다.
추가 출력 토큰은 정수 하나라 비용 영향이 사실상 없다.

`SYSTEM`에 지시 한 줄을 넣는다: 고른 이슈의 번호를 `issue_index`에 넣을 것.

JSON Schema의 `minimum`/`maximum`으로 범위를 강제하지 않는다 — 이 API의 구조화 출력이 `maxLength`를
지원하지 않아 코드로 검증하는 것이 이 파일의 기존 방침이다(`summarizer.py:30-31` 주석). 범위는 §3.4에서 본다.

### 3.3 배선 — 숨은 의존을 드러낸다

지금 `build_prompt()`가 함수 안에서 `rank_issues()`를 지역 import해 부르고 **결과를 버린다**
(`summarizer.py:162-164`). 번호를 `Issue`로 매핑하려면 그 리스트가 필요하다. 밖으로 올린다:

```python
def build_prompt(brief: DailyBrief, issues: list[Issue]) -> str: ...

# summarize() 안에서
issues = rank_issues(brief.articles)
prompt = build_prompt(brief, issues)
```

import는 모듈 상단으로 올린다(순환 없음). "기사 없음" 가드는 `brief.articles` 기준 그대로 둔다.

호출부는 테스트 4곳뿐이다: `test_summarizer.py:102,108,112`, `test_summarize_single_issue.py:79`.
`rank_issues(...)`를 넘기는 한 줄씩 수정.

### 3.4 매핑과 저하

**`_validate()`가 아니라 `summarize()`에서 매핑한다.** `_validate()`는 `_generate()` 안에서 불리고
`SummarizeError`를 던지면 발행이 죽는다. 그런데 이슈 번호가 틀린 건 **배경 조달의 문제이지 콘텐츠의 문제가
아니다** — 카드는 검증·감사를 통과한 상태다. `backgrounds.py:219`가 명시한 원칙이
"배경은 장식이므로 실패를 삼키고 발행을 막지 않는다"이다.

감사·재시도가 다 끝난 뒤 별도 헬퍼로:

```python
def _chosen_issue(payload: dict, issues: list[Issue]) -> Issue | None:
    """모델이 고른 이슈. 번호가 없거나 범위 밖이면 None(표지는 그래픽으로 저하).

    issues[0]으로 폴백하지 않는다 — 모델의 선택과 갈리는 것이 바로 이 필드가 생긴 이유다.
    """
    index = payload.get("issue_index")
    if not isinstance(index, int) or not 1 <= index <= len(issues):
        return None
    return issues[index - 1]
```

주석 둘째 줄이 이 함수에서 제일 중요하다. 다음 사람이 "None이 나오네, `issues[0]`으로 폴백하면 되겠다"고
고치는 순간 결함이 그대로 복원된다.

`.get()`은 저장소 관례(`payload["cards"]` 직접 접근)와 다르다. **의도적이다.** 스키마 `required`가
보장하지만, 만에 하나 없을 때 KeyError로 발행을 죽이는 건 장식 하나 때문에 게시물을 버리는 것이다.
없음·범위밖·타입이상을 **전부 같은 저하 경로**(None → 그래픽 표지)로 모은다.

재시도가 일어나면 최종 payload의 번호를 쓴다(재시도 payload가 카드의 출처이므로).

**믿을 것인가 검증할 것인가:** 범위만 본다. "유효하지만 거짓인 번호"(3이라 답하고 1을 씀)는 검증하지
않는다 — 카드 출처와 이슈 매체를 대조하는 방법이 유일한데, 스키마가 모델에게 출처를 `"A·B"` 복합
문자열로 쓰라고 지시하고 있어(`summarizer.py:92`) 정확한 대조가 안 된다. 억지로 하면 멀쩡한 사진을 버리는
오탐이 난다. §9 후속으로 남긴다.

## 4. 소비 측 계약

배경을 조달하는 호출부는 **`briefing.issue`를 써야 한다:**

```python
background = build_background(
    people=[], bg_query=...,
    issue=briefing.issue,          # rank_issues()[0]을 다시 부르지 말 것
    headline=briefing.headline,
    client=client, errors=errors,
)
```

**`rank_issues()[0]`을 다시 부르면 안 된다 — 모델의 선택과 갈린다**(§1.1 실측: 모델=코스피,
`rank_issues()[0]`=광고성 리스티클).

`briefing.issue`가 `None`이면 `build_background`가 사진 경로를 건너뛴다(기존 동작, `backgrounds.py:225`).

이번 범위에서 이 배선은 **하지 않는다** — 넘길 호출부가 없다(§1.3). 이 절은 데일리 파이프라인 모듈이
생길 때 지켜야 할 계약이다.

## 5. 정직한 한계

이 수정은 **결함을 막는 게 아니라 결함을 불필요하게 만든다.** `Briefing.issue`가 진실을 들고 나오므로
호출부가 `rank_issues()[0]`을 부를 *이유*가 사라지지만, 여전히 부를 *수는* 있다. 구조적으로 막는 것은
데일리 파이프라인 모듈이 생겨 호출부가 하나로 고정될 때다(§7). 그때까지는 §4가 계약이다.

## 6. 테스트

원장에 공허한 테스트로 리뷰에서 잡힌 기록이 3건 있다(4단계 Task 2 지적2, Task 4 지적, 최종리뷰 2건).
뮤테이션에 견디게 짠다.

1. **트랩 — 모델이 1이 아닌 번호를 고른다.** `issue_index=2` → `briefing.issue`가 **금리 이슈**다.
   `issues[0]`으로 되돌리는 뮤테이션이 FAIL을 낸다.
   **`issue_index=1`로 짜면 버그 코드로도 통과하므로 반드시 1이 아닌 번호를 쓴다.**
   재료는 있다: `test_summarize_single_issue.py`의 `sample_brief()`가 이슈 2개를 낸다
   (삼성 2매체 `(2,2)` > 금리 1매체 `(1,1)`).

   **단언은 내용으로 한다**, 객체 동일성이 아니라:
   ```python
   self.assertEqual(briefing.issue.articles[0].title, "한은 기준금리 동결")
   ```
   테스트가 `rank_issues()`를 다시 부르면 `summarize()` 안의 것과 **다른 객체**가 나오므로 `assertIs`는
   실패한다. `Issue`는 `frozen=False` 데이터클래스라 `==`는 필드 비교로 동작하긴 하지만, 제목 단언이
   읽는 사람에게 "2번은 금리 이슈"라는 의도를 그대로 보여주고 데이터클래스 eq 의미론에 기대지 않는다.
2. **픽스처 방어.** 트랩이 성립하려면 후보가 2개 이상이어야 하므로 테스트가 `len(rank_issues(...)) >= 2`를
   먼저 단언한다 — 픽스처가 바뀌어 이슈가 1개로 줄면 테스트가 조용히 무의미해지는 것을 막는다.
3. **저하 경로.** 범위 밖(`99`)·`0`·음수·누락·타입이상(문자열) → `briefing.issue is None`이고
   **카드는 살아서 발행된다**(`len(briefing.cards) == 3`).
4. **스키마.** `issue_index`가 `SCHEMA["properties"]`에 있고 `required`에 있다.
5. **배선.** `build_prompt(brief, issues)`가 넘겨받은 이슈로 프롬프트를 만든다.

기존 테스트 영향:
- `test_summarize_single_issue.py`의 `build_prompt` 호출 1곳, `test_summarizer.py` 3곳 → `issues` 인자 추가.
- `test_summarizer_schema.py`는 **손대지 않는다.** `_validate(payload)`를 직접 부르는데 `_validate`가
  `issue_index`를 안 건드리는 설계라 그대로 통과한다(확인함).
- 기존 `PAYLOAD`들은 `issue_index` 없이도 통과한다(`.get()` → `None` → `issue=None`). 트랩 테스트용
  payload만 번호를 싣는다.

## 7. 범위 — 안 하는 것

다음 세션이 헷갈리지 않도록 명시한다.

- **데일리 파이프라인 모듈화**(`collect → summarize → build_background → render → 발행`) — 별도 단계.
  §4 배선이 실제로 일어나는 곳.
- **프롬프트 후보 개수·본문요약 길이 조정** — 비용($0.06/회, 기존 $0.017~0.024) 별도 판단. 지금 후보를
  줄이면 모델이 광고성 1위를 피해 갈 탈출구가 좁아진다(§8).
- **광고성 리스티클이 랭킹 1위인 문제** — 3단계b(소스 확대). `collector`의 정형 기사 필터는 국문 패턴이라
  영문 리스티클을 못 거른다.
- **유효하지만 거짓인 번호의 교차검증** — §3.4, §9.
- **얼굴 검출·크롭 재판단** — 4단계 후속 그대로.

## 8. 기존 4단계 스펙 수정

`2026-07-16-image-sourcing-design.md`를 고친다. 안 고치면 그 스펙이 거짓말인 채로 남는다.

- **§4 아키텍처** — 다이어그램이 `issues.py → photos.py`로만 그려져 있어 "누가 그 Issue를 아는가"가 비어
  있다. 그 빈칸이 이번 결함의 발원지다. `summarizer.py`가 이슈를 실어 내보내는 흐름을 넣는다.
- **§5** — `photos.pick(issue)`의 `issue`가 **`briefing.issue`이지 `rank_issues()[0]`이 아님**을 명시하고
  이 문서를 참조로 건다.

`.superpowers/sdd/progress.md`(durable 원장)에도 이번 단계를 기록하고, "새 세션 최우선"으로 박아둔
치명적 설계 결함 표시를 해소 상태로 내린다.

## 9. 후속

- 유효하지만 거짓인 번호의 교차검증(카드 출처 ↔ 이슈 매체). 스키마의 `"A·B"` 복합 출처 지시가 걸림돌.
- 모델이 이슈를 고른 *이유*를 안 남긴다 — 디버깅 때 아쉬울 수 있으나 출력 토큰을 늘리므로 보류.

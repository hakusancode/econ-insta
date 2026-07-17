# 데일리 브리핑 크론 자동화 — 오전 해외 · 저녁 국내

- 관련: quota 스펙(2026-07-17) 완료 후 첫 자동화 단계. "데일리 파이프라인 모듈 + 호스팅"이 이 스펙이다.
- 브랜치: main(`08a6f10`) 위에서 새 브랜치
- 날짜: 2026-07-17

## 1. 배경

데일리 브리핑에는 발행 진입점이 없다 — `--publish`는 ai_brief·blog_brief·stock_brief·reels에만 있고,
지금까지의 데일리 발행(2026-07-17 오후 Da4zZMZnRff 포함)은 전부 스크래치 스크립트로 손 조립했다.
quota 재설계 완료로 파이프라인이 손 개입 없이 발행 품질을 내는 것이 실증됐다(같은 날 실측:
크로스소스 이슈 + 이슈 일치 기사 사진 + role 서사). 이제 자동화할 차례다.

## 2. 사용자 결정 (2026-07-17)

1. **오전 = 해외, 저녁 = 국내.** KST **07:30 해외** / **19:00 국내** (미 증시 마감 후 출근길 / KRX 마감 종합기사 후 퇴근길).
2. **완전 자동** — 사람 검토 없음(2026-07-10 합의 재확인). 팩트체크가 게이트키퍼. 실패 시 그날 그 에디션 건너뜀.
3. **표지 라벨(kicker)은 나누지 않는다** — 두 에디션 모두 기존 "데일리 경제 브리핑" 그대로.
   에디션 구분은 내용(기사·이슈)으로만. → `renderer.render()` 변경 없음.

## 3. 설계

### 3.1 collector — 피드를 에디션으로 나눈다

```python
KR_FEEDS: dict[str, FeedSpec] = { "연합뉴스": ..., "한국경제": ..., "매일경제": ... }
GLOBAL_FEEDS: dict[str, FeedSpec] = { "WSJ": ..., "The Economist": ... }
FEEDS: dict[str, FeedSpec] = {**KR_FEEDS, **GLOBAL_FEEDS}   # 기존 소비자 전부 불변
```

`collect(feeds: dict[str, FeedSpec] | None = None)` — 파라미터 추가, 기본 None = FEEDS 전체(기존 동작 불변).
`gather_articles`는 이미 `feeds`를 받는다.

- 해외 에디션은 매체 2곳뿐이라 크로스소스 신호가 얇다. 단 2026-07-17 오후 실측에서 WSJ+Economist
  클러스터가 상위 1·2위로 실제 형성됨 — 작동한다. Economist는 72h 창(주간지)이라 기사가 이월된다.
- 요약 출력은 두 에디션 모두 한국어(독자 기준, summarizer 변경 없음).
- 지표 카드는 두 에디션 모두 기존 8종 유지(코스피~비트코인). 에디션별 분리는 YAGNI.

### 3.2 `econ_insta/daily.py` — 데일리 발행 진입점 (신규 모듈)

```
python -m econ_insta.daily --edition kr|global --render    # 수집→요약→배경→렌더→캡션
python -m econ_insta.daily --edition kr|global --publish   # 호스팅 확인(재시도) → 캐러셀 발행
```

```python
@dataclass(frozen=True)
class Edition:
    slug: str                      # 출력 디렉터리 접미사
    feeds: dict[str, FeedSpec]

EDITIONS = {"kr": Edition("kr", KR_FEEDS), "global": Edition("global", GLOBAL_FEEDS)}
```

- **출력 경로 = `out/{now_kst():%Y-%m-%d}-{slug}/`.** 날짜는 반드시 `now_kst()` — CI는 UTC라
  오전 실행(22:30 UTC)이 전날 날짜를 잡는 함정이 있다. `--render`와 `--publish`가 같은 규칙으로
  경로를 재계산하므로 두 스텝 사이에 경로를 넘길 필요가 없다(자정 걸침은 §6 비고).
- 흐름: `collect(feeds=edition.feeds)` → `summarize(brief)` → `build_background([], briefing.bg_query or "",
  issue=briefing.issue, headline=briefing.headline)` → `renderer.render(briefing, brief.collected_at,
  out_dir=..., background=bg.image if bg else None)` → `build_caption(...)` → `caption.txt` 저장.
- **`build_caption(briefing, when, credits) -> str` (데일리 전용, 신규)** — 지금까지 없어서 손 조립했다.
  - 복합 출처 dedup: `{s.strip() for card in cards for s in card.source.split("·") if s.strip()}`
    (2026-07-17 오전 발행분의 "매일경제 · 매일경제·시장지표" 중복 버그의 수정판)
  - **credits 배선(원장 필수 항목)**: `Background.credits`의 각 항목을 `📷 {credit}` 줄로.
    CC BY 폴백(위키미디어)이 걸리면 이 줄이 없을 때 실제 라이선스 위반이다. 기사 사진은 credits가
    빈 튜플이라 줄이 안 생긴다.
  - 투자유의 문구("※ 정보 제공 목적이며 투자 권유가 아닙니다.") + 해시태그. 형식은 2026-07-17-pm
    발행분(scratch의 출력)을 정본으로 삼는다.
- `--publish`: `NN.jpg` + `caption.txt`를 raw URL로 확인 후 `InstagramClient.publish_images()`.
  ai_brief.publish_rendered와 같은 구조이되 **CDN 전파 재시도를 내장한다** — push 직후 raw가
  200이어도 메타 서버가 못 가져가는 실측 함정(`9004/2207052`)이 있으므로, URL 확인 실패·9004
  발행 실패 시 20초 간격 최대 6회 재시도. (ai_brief 쪽은 수동 실행이라 사람이 재시도했다.)
- 배경 실패·그래픽 폴백은 발행을 막지 않는다(기존 계약). `SummarizeError`(팩트체크 재생성 실패
  포함)는 그대로 전파 → 프로세스 비정상 종료 → 워크플로 실패 → 그날 그 에디션 건너뜀.

### 3.3 호스팅 — 기존 방식 유지, 커밋은 CI가

렌더 결과를 CI가 `git add -f out/<경로>` + `[skip ci]` 커밋 + push(워크플로에 `contents: write`).
raw.githubusercontent.com URL로 발행(2026-07-10 합의 그대로). 하루 2건×6장×~200KB ≈ 월 ~70MB로
저장소가 서서히 붓는 것은 알려진 비용 — 무너지면 그때 전용 브랜치로 옮긴다. 지금은 안 한다.

### 3.4 `.github/workflows/daily-briefing.yml` (신규)

```yaml
on:
  schedule:
    - cron: "30 22 * * *"   # KST 07:30 = 해외
    - cron: "0 10 * * *"    # KST 19:00 = 국내
  workflow_dispatch:
    inputs: { edition: { type: choice, options: [kr, global] } }
permissions:
  contents: write
```

- 에디션 판별: `github.event.schedule == '30 22 * * *'` → global, `'0 10 * * *'` → kr,
  dispatch면 입력값. 스텝: checkout → setup-python 3.13 → `pip install -r requirements.txt` →
  `--render` → git 커밋·push → `--publish`.
- 폰트: Pretendard 5종이 `assets/fonts/`에 번들돼 있어 **apt-get 불필요**(FontSet.discover가 번들 우선).
- **시크릿**: `ANTHROPIC_API_KEY`(신규 설정 필요), `IG_ACCESS_TOKEN`·`IG_USER_ID`
  (refresh-token.yml이 전제 — 실제 설정 여부를 구현 때 `gh secret list`로 확인, 없으면 사용자에게 요청).
  `UNSPLASH_ACCESS_KEY`는 원래 없음 — 배경 체인이 알아서 건너뜀.
- 실패 = 그날 그 에디션 건너뜀, GitHub이 저장소 소유자에게 메일. 재시도·중복발행 가드 없음
  (수동 dispatch는 의도적 행위로 간주).
- **cron은 파일이 main에 있으면 즉시 활성이다.** 롤아웃 순서(§5)가 이를 다룬다.

## 4. 테스트

| 테스트 | 잡는 뮤테이션 |
|---|---|
| `KR_FEEDS`∪`GLOBAL_FEEDS` == `FEEDS`, 교집합 없음 | 분할 시 피드 누락·중복 |
| `collect(feeds=...)`가 준 피드만 쓴다 | feeds 파라미터 무시하고 FEEDS 사용 |
| `build_caption` 복합 출처 dedup | split("·") 제거 |
| `build_caption` credits → 📷 줄 | credits 배선 삭제 (CC BY 위반 경로) |
| `build_caption` 투자유의 문구 포함 | 문구 삭제 |
| 출력 경로가 에디션 slug를 포함 | slug 무시(두 에디션이 같은 디렉터리에 덮어씀) |

뮤테이션은 실제 적용→FAIL 재현→복원(이 저장소 원칙). CLI·워크플로 yaml은 단위 테스트 대상이 아니고
§5의 수동 dispatch가 검증한다.

## 5. 롤아웃 (순서가 안전장치다)

1. 구현 + 단위 테스트, 브랜치에서 리뷰.
2. **병합 전 로컬 실증**: `--edition global --render`를 로컬에서 돌려 해외 에디션 실물(카드·캡션)을
   눈으로 확인한다 — 해외 전용은 이번이 첫 실행이다.
3. main 병합·push — 이 순간 cron이 활성된다. 단 다음 정기 실행 전에:
4. **workflow_dispatch 수동 1회**(global)로 CI 전체 경로(시크릿·폰트·커밋·push·CDN·발행)를 검증.
   실패하면 cron이 밟기 전에 고친다.
5. 첫 정기 실행 2회(오전 해외·저녁 국내)를 다음 날 확인.

## 6. 하지 않는 것 / 비고

- 인기도 스크래핑·하루 3건(3단계b), 클러스터링 노이즈 수정, `Card.role` 렌더, 릴스 주간화.
- 저장소 부풀림 대책(전용 브랜치 등) — 실제로 문제가 되면.
- 중복발행 가드, 발행 실패 자동 재시도(워크플로 재실행은 사람이).
- 비고: `--render`와 `--publish` 사이에 자정(KST)이 걸치면 경로가 갈린다. 두 스텝 사이는 수 분이고
  실행 시각(07:30/19:00)에서 자정까지 여유가 커서 가드는 넣지 않는다.
- 비용: 실행당 모델 호출 ~$0.03~0.06 × 하루 2회 ≈ 월 $2~4.

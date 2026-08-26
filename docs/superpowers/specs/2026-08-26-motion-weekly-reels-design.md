# 모션 릴스 + 주간 정기화 설계 (2026-08-26)

## 1. 배경과 목표

릴스는 2026-07-21 사용자 지시로 보류됐다. 재개하며 두 가지를 바꾼다:

1. **표지 문제**: 릴스마다 같은 레이아웃의 정지 표지가 반복된다. 전광판 컨셉 HTML 시안
   (`design/covers/reel-cover-e-board.html`)은 사용자 기각·폐기 확정(2026-08-25, git rm 완료).
   정지 표지 자체를 버리고 **영상 도입부를 애니메이션**으로 만든다.
2. **주간 정기화**: 2026-07-16 개편 결정("릴스 주 1회 정기 발행, 이번 주 최대 이슈 1건,
   로열티프리 음원 자동 삽입")을 이번에 구현한다.

사용자 결정 (2026-08-25):
- 모션 범위 = **전체 패키지** (도입부 카운트업 + 이유 카드 슬라이드인 + 차트 드로잉)
- 운영 = **바로 주간 크론까지** (수동 검증 단계를 별도 승인 없이 진행하되, §7 검증 절차는 수행)
- 음원 = **로열티프리 자동 삽입** (무음 폐기)
- 발행 시각 = **일요일 19:00 KST** (+ 예비 크론 19:25, 데일리와 같은 마커 가드 패턴)

북극성은 조회수·팔로워(2026-07-16). 무음·정지화면 릴스는 노출이 떨어진다 — 이 설계 전체가
그 레버를 당기는 일이다.

## 2. 모션 엔진 (`econ_insta/reels.py` 확장)

### 2.1 Scene 확장

```python
@dataclass(frozen=True)
class Scene:
    image: Image.Image | None          # 정지 장면 (기존)
    seconds: float
    zoom: float = 1.0                  # 정지 장면 전용
    render: Callable[[float], Image.Image] | None = None
    # render가 있으면 애니메이션 장면: 진행률 p(0.0~1.0)를 받아 그 시점의 프레임을 그린다.
    # 이때 image·zoom은 무시된다. 페이드 인/아웃은 frames()가 기존과 동일하게 얹는다.
```

`frames()`는 장면마다 `scene.render(progress)`가 있으면 그걸, 없으면 기존 `_zoomed(image, ...)`
경로를 쓴다. 페이드는 두 경로 공통. 기존 정지 장면 호출부는 무변경으로 동작해야 한다.

### 2.2 애니메이션 장면 3종

이징은 `ease_out_cubic(p) = 1 - (1-p)**3` 하나로 통일. 수치 계산은 **순수 함수로 분리**해
프레임 렌더 없이 단위 테스트한다 (§7).

- **`anim_cover(headline, pct, when, fonts, background)`** — 도입부 (3.5초):
  - 배경 사진은 전 구간 1.0→1.06 슬로우 줌 (사진 없으면 BG_COVER 단색).
  - 등락률 숫자가 0%에서 실제값까지 카운트업: `value = pct * ease_out_cubic(min(p/0.6, 1))`.
    표기·색은 기존 `_fmt_pct`·`_change_color` 재사용 (상승 빨강·하락 파랑, 한국 관행).
  - 헤드라인은 p∈[0.55, 0.80]에서 아래→위 60px 슬라이드 + 페이드인, 이후 정지.
  - 키커·날짜·"넘겨서 확인하세요 →"는 scene_cover와 같은 위치에 고정 표시.
- **`anim_reason(reason, index, total, fonts)`** — 이유 카드 (4.5초):
  - 제목·본문이 p∈[0, 0.15]에서 슬라이드인(+40px)·페이드인, 이후 기존 scene_reason과
    동일한 정지 화면. 레이아웃(세로 중앙 정렬 포함)은 scene_reason 코드를 재사용.
- **`anim_chart(series, when, fonts)`** — 차트 (5초):
  - 라인이 왼→오로 그려진다: 가시 점 개수 = `_visible_count(n, p) = max(2, ceil(n * ease_out_cubic(min(p/0.7, 1))))`.
    영역 채우기 폴리곤도 가시 구간까지만.
  - 고/저 마커는 해당 점이 가시화된 뒤에만 표시.
  - 기간별 등락 통계(1주·1개월·3개월)는 p>0.75에서 페이드인.
  - 축·수치 계산은 기존 scene_chart 코드를 재사용해 최종 프레임(p=1.0)이 기존 정지
    차트와 시각적으로 동일해야 한다.

`build_stock_reel`은 세 장면을 애니메이션 버전으로 교체한다. **표지 이미지(cover_url용
`reel-cover.jpg`)는 도입부의 마지막 프레임(p=1.0)으로 저장** — 피드 썸네일 역할은 유지된다.

## 3. 음원 (`assets/audio/` + encode 확장)

- `assets/audio/tracks.json`: `[{file, title, artist, license, credit, source_url}]`.
  people.json과 같은 원칙 — **라이선스는 기록으로 관리하고, 확인 안 되는 트랙은 넣지 않는다.**
  CC0 또는 CC BY만. CC BY는 캡션에 `🎵 <credit>` 줄을 자동 추가(빼면 라이선스 위반 —
  build_caption의 📷 줄과 같은 원칙). 구현 시 2~4트랙 소싱(차분한 일렉트로닉/코퍼레이트 계열).
- `encode(scenes, path, audio_path=None)`: audio_path가 있으면 anullsrc 대신
  `-stream_loop -1 -i <audio>` + `-shortest`로 영상 길이에 맞춰 루프·컷. 없으면 기존 무음.
- 트랙 선택은 `ISO 주차 % 트랙 수` — 결정적이고 주마다 바뀐다.
- 인스타 인앱 음원은 API 불가(기존 실측) — 그 경로는 쓰지 않는다.

## 4. 주간 소재 공급 (`econ_insta/daily.py` 소폭 수정)

국내 RSS는 D-2면 기사가 증발한다(실측: 한경·매경 0건) — 주말에 한 주를 재수집할 수 없다.
따라서 **데일리 크론이 매일 이슈 메타데이터를 남긴다**:

- 렌더 성공 시 `out/<날짜>-<에디션>/briefing.json` 저장:
  `{date, edition, headline(훅), issue_title, sources(매체 목록), article_count, cards: [{title, source}]}`.
  `briefing.issue`가 None(그래픽 폴백 등)이면 issue_title·sources는 null/빈 값으로 저장하되
  파일은 만든다 — "없었음"도 기록이다.
- CI가 out/을 이미 커밋하므로 briefing.json도 같이 실린다. 발행 실패 시에도 렌더가 됐다면
  남는다(예비 크론 재시도와 무관).

## 5. 주간 파이프라인 (`econ_insta/weekly_reel.py` 신규)

CLI: `python -m econ_insta.weekly_reel --render` / `--publish` (daily.py 패턴).

1. **수집**: 최근 7일의 `out/*/briefing.json`을 읽는다. 0건이면 발행 스킵(그날 건너뜀 —
   데일리와 같은 실패 정책). 1건 이상이면 있는 만큼으로 진행.
2. **이슈 선정**: 후보를 매체 수 내림차순 → 기사 수 내림차순으로 정렬해 상위 후보를
   프롬프트에 싣고, Claude가 "이번 주 최대 이슈" 1건을 고르고 릴스 대본을 쓴다:
   `{issue_index, hook, reasons: [{title, body, source}] (2~3개), ticker, bg_query, people}`.
   - ticker는 고정 목록에서만 선택 — 자유 입력 금지(환각 방지). 목록 = 기존 지표 8종
     (코스피·코스닥·원/달러·나스닥·S&P500·WTI·금·비트코인) + 국내 대형주 화이트리스트
     (삼성전자·SK하이닉스·현대차·LG에너지솔루션·네이버·카카오 — 구현 시 이 6종으로 시작,
     이슈에 맞는 게 없으면 지표로 폴백).
   - issue_index 계약은 이슈 계약(2026-07-17)과 같은 규칙: 범위 밖·타입 이상이면 폴백 없이
     발행 중단(주간은 스킵이 낫다).
3. **팩트체크**: factcheck의 수치 대조·환율 방향 검사 재사용. 위반 시 1회 재생성 → 남으면 스킵.
4. **차트**: yfinance `auto_adjust=False`, 3개월 시계열(기존 Series 재사용). 일요일 실행이라
   basis는 금요일 종가 — intraday 아님.
5. **배경**: `build_background(people, bg_query, issue=선정 이슈)` 체인 재사용.
6. **렌더**: §2 모션 장면 + §3 음원으로 `out/<날짜>-weekly-reel/` 에 reel.mp4·reel-cover.jpg·
   caption.txt 생성. 캡션 = 훅 + 출처 dedup(복합 출처 "·" split — 기존 버그 재발 금지) +
   📷/🎵 크레딧 + 투자유의 + 해시태그.
7. **발행**: out/ 강제 push → **Pages 빌드 대기 재시도**(hosting_ready 패턴 — 현행
   publish_reel은 첫 실패 즉시 return 1이라 크론에서 거의 매번 죽는다. video/mp4 확인을
   attempts·delay 재시도로 감싼다) → publish_reel → `published.txt` 마커 커밋.

## 6. 크론 (`.github/workflows/weekly-reel.yml` 신규)

- 본 크론 일요일 10:00 UTC(19:00 KST) + **예비 크론 10:25 UTC** + published.txt 마커 가드 —
  데일리 워크플로(3c66e4b)와 동일한 신뢰성 패턴. GitHub cron 결손·지연은 실측된 현실이다.
- 시크릿은 데일리와 동일(ANTHROPIC_API_KEY·IG_ACCESS_TOKEN·IG_USER_ID). ffmpeg는
  imageio-ffmpeg가 들고 온다(시스템 설치 불필요). 릴스 호스팅은 Pages(video/mp4) — 기존 실측.
- 강제 재발행 = 마커 삭제 후 dispatch(데일리와 동일).

## 7. 검증

- **단위 테스트 (네트워크·모델 없이)**: `ease_out_cubic` 경계값, `_count_value`(p=0→0, p=1→목표),
  `_visible_count`(p=0→2, p=1→n, 단조증가), Scene.render 경로 분기(frames가 render를 쓰는지 —
  뮤테이션: render 무시하고 image 쓰면 FAIL하는 트랩), encode audio_path 유무별 ffmpeg 인자,
  briefing.json 저장/로드 왕복, 이슈 선정 정렬, issue_index 가드, 🎵 크레딧 캡션 배선
  ("가드가 있다 ≠ 작동한다" — 배선 테스트 필수), tracks.json 스키마 검증.
- **실물 확인**: 수동 1건 실렌더(--render)해 mp4를 눈으로 확인(카운트업·차트 드로잉·음원).
  발행 전 캡션 눈 확인(발행 후 API 수정 불가 — 기존 실측).
- **첫 자동 발행**: 첫 일요일 크론 결과를 확인한다. briefing.json은 데일리 수정 배포 시점부터
  쌓이므로 첫 주는 부분 데이터로 돈다.
- 테스트 러너는 `unittest`(pytest 아님). CI 렌더에 fonts 문제 없음(Pretendard 번들).

## 8. 범위 밖

- 3단계b(인기도 스크래핑·소스 9곳·하루 3건) — 다음 설계.
- 데일리 카드(캐러셀) 쪽 변경 없음(briefing.json 저장 제외).
- 얼굴 검출(OpenCV)·표지 크롭 개선 — 기존 보류 유지.
- 주말·휴장일 데일리 대응(wrong_won_direction 재발 소지) — 별도 건.

## 9. 리스크

- 애니메이션 프레임 렌더 비용: 30fps × ~20초 ≈ 600프레임 Pillow 렌더. 로컬 실측으로 CI
  수 분 내 완료 확인(초과 시 FPS 24로 하향 검토).
- 음원 트랙 소싱 품질: 라이선스가 확실한 소스가 우선, 곡 품질은 사용자 확인 후 교체 가능.
- briefing.json 첫 주 공백: 스킵 정책으로 흡수.

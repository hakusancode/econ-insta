# 카드 렌더러 개편 구현 계획 (2단계)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 표지(다크 프리미엄 A + 버밀리언 C 변주), 본문 후크 카드, 지표 카드(리스트+미니 스파크라인)를 새 디자인으로 렌더. `Quote`에 스파크라인용 `series` 추가.

**Architecture:** 1단계에서 추가한 프리미티브(`premium_background`/`draw_sparkline`/`kicker_pill`/`vertical_gradient`)와 `DARK_PREMIUM`/FontSet 5단을 사용해 기존 `render_cover`/`render_card`/`render_indicators`를 **재작성**한다. 각 함수의 **호출 시그니처와 반환(1080×1350 RGB Image)은 유지**하여 `render()`·발행 흐름을 건드리지 않는다.

**Tech Stack:** Python 3.13, Pillow, 표준 `unittest`, yfinance.

## Global Constraints

- 테스트 러너 표준 `unittest`(`python -m unittest discover -s tests -q`). 파이썬 실행 시 `PYTHONIOENCODING=utf-8`.
- 테스트는 `StubFonts`(= `ImageFont.load_default(size)`)로 시스템 폰트 없이 돈다. 새 테스트도 이 패턴.
- 등락 색 **상승 빨강·하락 파랑**(`theme.change_color` 사용, 재정의 금지).
- 캔버스 1080×1350, `MARGIN`은 기존 상수 사용. 반환은 항상 RGB `Image`.
- **본문 카드는 세로 중앙 정렬**(기존 규칙 — 상단 정렬하면 아래 절반이 빈다).
- 타입 스케일(스펙 §5.3): 표지 헤드라인 Black 104(넘치면 축소)·키커 Bold 38 / 카드 번호 Black 120·제목 ExtraBold 74·본문 Regular 46 / 지표 값·변화 Bold, 이름 SemiBold. 폰트는 `fonts.at(size, weight=...)`.
- **검증된 구현 레퍼런스**: `docs/superpowers/reference/phase2-render-reference.py` — 얼굴 표지(`cover_face`)·그래픽 표지(`cover_graphic`)·버밀리언(`cover_verm`)·후크 카드(`content`)·스파크라인 지표(`indicators`)가 실제 렌더로 검증됨. **레이아웃·좌표·비율을 이 파일에서 가져오되**, 독립 헬퍼(`f`/`vgrad`/`glow`/`grid`) 대신 렌더러의 프리미티브·`FontSet`·`Theme` 토큰으로 바꿔 이식한다.
- 커밋은 각 태스크 끝. 메시지 한국어 + `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: Quote.series 필드 + 수집

**Files:**
- Modify: `econ_insta/collector.py` (`Quote` 데이터클래스, 지표 수집부)
- Test: `tests/test_quote_series.py`

**Interfaces:**
- Produces: `Quote.series: list[float] | None = None` (최근 종가 시계열, 없으면 None). Task 4가 스파크라인에 사용.

- [ ] **Step 1: 실패 테스트**

Create `tests/test_quote_series.py`:
```python
import unittest
from econ_insta.collector import Quote


class QuoteSeriesTest(unittest.TestCase):
    def test_series_defaults_none(self):
        q = Quote(symbol="^KS11", name="코스피", price=2981.4, change_pct=-2.14)
        self.assertIsNone(q.series)

    def test_series_accepts_list(self):
        q = Quote(symbol="^KS11", name="코스피", price=2981.4, change_pct=-2.14,
                  series=[2900.0, 2950.5, 2981.4])
        self.assertEqual(len(q.series), 3)

    def test_none_series_is_falsy_for_guard(self):
        q = Quote(symbol="^KS11", name="코스피", price=1.0, change_pct=0.0)
        self.assertFalse(q.series)  # 'if quote.series:' 가드가 성립
```

- [ ] **Step 2: 실행(실패)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_quote_series -v`
Expected: FAIL — `Quote`가 `series` 인자를 모른다.

- [ ] **Step 3: 필드 추가**

`collector.py`의 `Quote`에 필드 추가(`change_pct: float` 아래):
```python
    series: list[float] | None = None
    """스파크라인용 최근 종가 시계열. 수집 실패 시 None(발행을 막지 않는다)."""
```

- [ ] **Step 4: 지표 수집에서 series 채우기**

지표를 만드는 함수(yfinance로 `Quote`를 생성하는 곳)를 찾아, 과거 종가 시계열을 함께 받아 `series`에 넣는다. **`auto_adjust=False` 필수**(배당조정가 금지 — 메모리 기록된 함정). 최근 20~30개 종가.
- yfinance 조회 실패는 `series=None`으로 두고 넘어간다(기존 지표 수집이 실패해도 발행되는 원칙과 동일). 배치로 한 번에 받되, 개별 실패가 전체를 막지 않게.
- 구현 위치·기존 호출 형태는 `collector.py`의 현재 지표 수집 코드를 따르라(기존 패턴 유지).

- [ ] **Step 5: 실행(통과) + 회귀**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_quote_series -v`
Then: `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q`
Expected: 신규 통과 + 기존 전부 통과(기존 `Quote(...)` 생성이 series 없이도 동작).

- [ ] **Step 6: 커밋**

```bash
git add econ_insta/collector.py tests/test_quote_series.py
git commit -m "Quote.series 필드 추가(스파크라인용 시계열, 실패 시 None)"
```

---

### Task 2: render_cover — 다크 프리미엄(A) + 버밀리언 변주(C)

**Files:**
- Modify: `econ_insta/renderer.py` (`render_cover`)
- Test: `tests/test_render_cover.py`

**Interfaces:**
- Consumes: `premium_background`, `kicker_pill`, `vertical_gradient`, `DARK_PREMIUM`, `_photo_shade`, `wrap`, `_line_height`.
- Produces: `render_cover(headline, when, fonts, kicker="데일리 경제 브리핑", background=None, theme=DEFAULT_THEME, variant="dark") -> Image.Image`. `variant`는 `"dark"`(기본, A) 또는 `"color"`(C 버밀리언 풀블리드). 반환 1080×1350 RGB.

- [ ] **Step 1: 실패 테스트**

Create `tests/test_render_cover.py`:
```python
import unittest
from datetime import datetime
from pathlib import Path
from PIL import ImageFont, Image
from econ_insta.renderer import FontSet, render_cover, WIDTH, HEIGHT


class StubFonts(FontSet):
    def __init__(self):
        super().__init__(regular=Path("stub"), bold=Path("stub"))
    def at(self, size, *, bold=False, weight=None):
        return ImageFont.load_default(size)


WHEN = datetime(2026, 7, 16)


class RenderCoverTest(unittest.TestCase):
    def setUp(self):
        self.fonts = StubFonts()

    def test_dark_cover_size(self):
        img = render_cover("파월의 한 마디, 시장이 얼어붙었다", WHEN, self.fonts)
        self.assertEqual(img.size, (WIDTH, HEIGHT))
        self.assertEqual(img.mode, "RGB")

    def test_color_variant_differs_from_dark(self):
        dark = render_cover("연준 쇼크", WHEN, self.fonts, variant="dark")
        color = render_cover("연준 쇼크", WHEN, self.fonts, variant="color")
        self.assertNotEqual(list(dark.getdata()), list(color.getdata()))

    def test_photo_background_still_supported(self):
        bg = Image.new("RGB", (WIDTH, HEIGHT), (120, 120, 120))
        img = render_cover("삼성 어닝 쇼크", WHEN, self.fonts, background=bg)
        self.assertEqual(img.size, (WIDTH, HEIGHT))
```

- [ ] **Step 2: 실행(실패)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_render_cover -v`
Expected: FAIL — `variant` 인자 없음.

- [ ] **Step 3: 구현**

`render_cover`를 재작성한다. 세 경로:
- **background 있음(사진/얼굴)**: 기존 `_photo_shade` 스크림 경로를 **유지**하되, 헤드라인을 Black 104(넘치면 축소)로 하단 정렬, 좌상단 `kicker_pill`, 제목 위 골드 액센트 바. 레퍼런스 `cover_face` 참고.
- **variant="dark", background 없음**: `premium_background(theme)` 배경 + 좌상단 `kicker_pill` + 날짜 + 하단 골드 바 + 초대형 헤드라인(Black). 레퍼런스 `cover_graphic`의 배경·타이포 배치 참고(데이터 히어로 차트는 이번 스코프 아님 — 생략).
- **variant="color"**: `theme.signature`(버밀리언) 풀블리드 배경 + 상단 라벨줄(영문 키커+날짜+흰 룰) + 중앙 초대형 잉크 헤드라인 + 좌하단 큰 번호 워터마크 + 우하단 CTA. 레퍼런스 `cover_verm` 참고. 잉크색은 어두운 `(22,17,14)`.

레퍼런스의 독립 헬퍼(`f`, `vgrad`, `glow`, `grid`)를 렌더러 프리미티브(`fonts.at(weight=...)`, `premium_background`, `vertical_gradient`)로 치환한다. 헤드라인이 길면 `wrap`으로 줄바꿈하고 폰트를 단계적으로 줄여 카드 안에 넣는다(넘침 방지).

- [ ] **Step 4: 실행(통과)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_render_cover -v`
Expected: PASS 3건.

- [ ] **Step 5: 실렌더 육안 확인(선택, 시스템 폰트 있을 때)**

Run: `PYTHONIOENCODING=utf-8 python -c "from econ_insta.renderer import render_cover, FontSet; from datetime import datetime; render_cover('연준 쇼크, 시장이 얼어붙었다', datetime(2026,7,16), FontSet.discover(), variant='color').save('cover_c.jpg')"`
결과 이미지를 눈으로 확인(가능하면). 실패해도 테스트가 통과하면 다음 단계로.

- [ ] **Step 6: 커밋**

```bash
git add econ_insta/renderer.py tests/test_render_cover.py
git commit -m "render_cover 개편: 다크 프리미엄 A + 버밀리언 C 변주(사진 경로 유지)"
```

---

### Task 3: render_card — 본문 후크 카드

**Files:**
- Modify: `econ_insta/renderer.py` (`render_card`)
- Test: `tests/test_render_card.py`

**Interfaces:**
- Consumes: `premium_background`, `wrap`, `_block_height`, `_line_height`, `Card`, `DARK_PREMIUM`.
- Produces: `render_card(card, index, total, fonts, theme=DEFAULT_THEME) -> Image.Image` (시그니처 유지). 반환 1080×1350 RGB.

- [ ] **Step 1: 실패 테스트**

Create `tests/test_render_card.py`:
```python
import unittest
from pathlib import Path
from PIL import ImageFont
from econ_insta.renderer import FontSet, render_card, WIDTH, HEIGHT
from econ_insta.summarizer import Card


class StubFonts(FontSet):
    def __init__(self):
        super().__init__(regular=Path("stub"), bold=Path("stub"))
    def at(self, size, *, bold=False, weight=None):
        return ImageFont.load_default(size)


class RenderCardTest(unittest.TestCase):
    def setUp(self):
        self.fonts = StubFonts()
        self.card = Card(title="연준, 기준금리를 동결했다",
                         body="파월 의장은 인플레이션이 목표 위에 있다며 인하를 서두르지 않겠다고 밝혔다. " * 2,
                         source="WSJ")

    def test_size_and_mode(self):
        img = render_card(self.card, 1, 5, self.fonts)
        self.assertEqual(img.size, (WIDTH, HEIGHT))
        self.assertEqual(img.mode, "RGB")

    def test_short_body_still_renders(self):
        card = Card(title="짧은 제목", body="한 문장.", source="연합뉴스")
        img = render_card(card, 2, 5, self.fonts)
        self.assertEqual(img.size, (WIDTH, HEIGHT))
```

- [ ] **Step 2: 실행(계약 확인)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_render_card -v`
Expected: 기존 시그니처라 import·계약 검증은 통과할 수 있음. 시각 변경은 Step 3에서.

- [ ] **Step 3: 구현**

`render_card`를 재작성한다(레퍼런스 `content` 참고):
- 배경 `premium_background(theme)`.
- 좌측 골드 세로 바(x=0..12, 풀하이트).
- 상단 큰 번호 `f"{index:02d}"` Black 120 골드, 우측 `f"{index} / {total}"` SemiBold 뮤트.
- 제목 ExtraBold 74 + 디바이더 룰 + 본문 Regular 46(행간 1.5). **머리말/꼬리말 사이 세로 중앙 정렬**(기존 `_block_height` 기반 계산 유지).
- 하단 `출처 · {card.source}` Regular 뮤트.

레퍼런스 독립 헬퍼를 렌더러 프리미티브·`fonts.at(weight=...)`·`theme` 토큰으로 치환.

- [ ] **Step 4: 실행(통과)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_render_card -v`
Expected: PASS 2건.

- [ ] **Step 5: 커밋**

```bash
git add econ_insta/renderer.py tests/test_render_card.py
git commit -m "render_card 개편: 다크 프리미엄 후크 카드(번호·골드 바·중앙정렬)"
```

---

### Task 4: render_indicators — 리스트 + 미니 스파크라인

**Files:**
- Modify: `econ_insta/renderer.py` (`render_indicators`, 필요 시 `_indicator_layout`)
- Test: `tests/test_render_indicators.py`

**Interfaces:**
- Consumes: `premium_background`, `draw_sparkline`, `theme.change_color`, `Quote`(`series` 포함).
- Produces: `render_indicators(quotes, note, fonts, theme=DEFAULT_THEME) -> Image.Image` (시그니처 유지). 각 행에 이름·값·등락% + `series`가 있으면 미니 스파크라인(등락색). `series`가 None이면 스파크라인 생략(값만).

- [ ] **Step 1: 실패 테스트**

Create `tests/test_render_indicators.py`:
```python
import unittest
from pathlib import Path
from PIL import ImageFont
from econ_insta.renderer import FontSet, render_indicators, WIDTH, HEIGHT
from econ_insta.collector import Quote


class StubFonts(FontSet):
    def __init__(self):
        super().__init__(regular=Path("stub"), bold=Path("stub"))
    def at(self, size, *, bold=False, weight=None):
        return ImageFont.load_default(size)


def q(name, price, chg, series=None):
    return Quote(symbol=name, name=name, price=price, change_pct=chg, series=series)


class RenderIndicatorsTest(unittest.TestCase):
    def setUp(self):
        self.fonts = StubFonts()

    def test_eight_indicators_with_series_fit(self):
        quotes = [q(f"지표{i}", 100.0 + i, (-1) ** i * 1.5, series=[1.0, 2.0, 1.5, 3.0])
                  for i in range(8)]
        img = render_indicators(quotes, "오늘 지표 흐름 코멘트", self.fonts)
        self.assertEqual(img.size, (WIDTH, HEIGHT))

    def test_missing_series_degrades_gracefully(self):
        quotes = [q("코스피", 2981.4, -2.14, series=None),
                  q("원/달러", 1392.0, 0.58, series=[1.0, 1.1, 1.2])]
        img = render_indicators(quotes, "", self.fonts)  # series 없어도 안전
        self.assertEqual(img.size, (WIDTH, HEIGHT))

    def test_single_indicator(self):
        img = render_indicators([q("코스피", 2981.4, -2.14, series=[1, 2, 3])], "", self.fonts)
        self.assertEqual(img.size, (WIDTH, HEIGHT))
```

- [ ] **Step 2: 실행(실패/계약)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_render_indicators -v`
Expected: 스파크라인 미연동 상태면 계약만 통과. 스파크라인은 Step 3에서 연동.

- [ ] **Step 3: 구현**

`render_indicators`를 재작성한다(레퍼런스 `indicators` 참고):
- 배경 `premium_background(theme)`, 상단 "오늘의 지표" ExtraBold + 날짜/부제.
- 각 행: 좌측 이름(SemiBold) · 우측 값(Bold)·등락%(Bold, `theme.change_color`) · **가운데 미니 스파크라인**(`draw_sparkline`, box는 행 내부, 색은 등락색). `quote.series`가 falsy면 스파크라인 생략.
- 8개까지 세로로 넘치지 않게 행 높이를 배분(기존 `_indicator_layout`/`_SCALES` 축소 로직을 스파크라인 높이 포함해 조정하거나, 레퍼런스처럼 `(bottom-top)/n`로 균등 배분).
- `note`가 있으면 하단에 코멘트.

`draw_sparkline`은 새 이미지를 반환하므로(면적 합성), 반환 이미지를 받아 이어 그린다(레퍼런스의 `img = Image.alpha_composite(...)` 패턴처럼 재바인딩). 스파크라인 그린 뒤 `ImageDraw.Draw`를 다시 잡을 것.

- [ ] **Step 4: 실행(통과)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_render_indicators -v`
Expected: PASS 3건.

- [ ] **Step 5: 전체 회귀**

Run: `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q`
Expected: 기존 렌더러 테스트(`test_renderer.py`) 포함 전부 통과. 기존 테스트가 옛 레이아웃 좌표를 하드코딩해 깨지면, **계약(크기·모드·발행 흐름) 검증으로 남기고 좌표 의존 단언은 새 동작에 맞게 갱신**한다(디자인이 바뀌었으므로). 단언을 지우지 말고 고칠 것.

- [ ] **Step 6: 커밋**

```bash
git add econ_insta/renderer.py tests/test_render_indicators.py
git commit -m "render_indicators 개편: 리스트 + 미니 스파크라인(series 없으면 저하)"
```

---

## 이 계획이 커버하는 스펙 항목 (자기 점검)

- §5.5 표지 A(다크/사진) + C 변주 → Task 2 ✅
- §5.5 본문 후크 카드(번호·골드 바·중앙정렬) → Task 3 ✅
- §5.5 지표 리스트 + 스파크라인 + `Quote.series` + 우아한 저하 → Task 1, 4 ✅
- §8 테스트(StubFonts 주입, 카드 종류별 렌더, 스파크라인 유/무, 8지표 수용) → 각 태스크 ✅

**주의(1단계 리뷰의 Minor, 2단계에서 확인):** `FontSet._path_for`의 미인식 weight 조용한 폴백, semibold 폴백 사슬이 bold를 건너뜀 — 렌더러가 실제 weight를 넘기기 시작하므로 값이 맞는지 확인.

**후속:** 3단계 콘텐츠 단일 이슈 후크형·발행 3건(`summarizer.py`, `render()` 다건화), 4단계 이미지 소싱(`backgrounds.py` 얼굴/로고/실사·인기도 신호), 5단계 릴스 주간화·오디오·시리즈 통일.

# 디자인 시스템 기반 구현 계획 (1단계)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 피드 개편의 토대 — Pretendard 폰트 번들, FontSet 5단 굵기, Theme 그라디언트 확장(다크 프리미엄), 렌더 프리미티브(그라디언트·글로우·그리드·스파크라인·키커 pill)를 `renderer.py`에 얹는다. 완료 시 기존 카드가 새 기본 테마로 그대로 렌더된다.

**Architecture:** 기존 `renderer.py`의 `Theme`/`FontSet`을 **확장**한다(갈아엎지 않음). 새 필드는 전부 기본값을 둬 하위호환을 지키고, 프리미티브는 순수 함수로 추가한다. 롤백은 `DEFAULT_THEME` 한 줄.

**Tech Stack:** Python 3.13, Pillow(PIL), 표준 `unittest`. 폰트는 Pretendard OTF(OFL).

## Global Constraints

- 테스트 러너는 **표준 `unittest`** (`python -m unittest discover -s tests -q`). pytest 미설치.
- 등락 색은 **상승 빨강 · 하락 파랑**(한국 관행). 테마마다 재정의 금지.
- 테스트는 `ImageFont.load_default(size)`로 시스템 폰트 없이 돈다(`StubFonts` 패턴). 새 테스트도 이 패턴 유지.
- Git Bash 콘솔이 cp949 — 파이썬 실행 시 `PYTHONIOENCODING=utf-8`.
- 캔버스 `WIDTH, HEIGHT = 1080, 1350`, `Color = tuple[int, int, int]`.
- 기존 테마 4종(DARK_AMBER/PAPER/MIDNIGHT/MONO)은 **보존**한다.
- 커밋은 각 태스크 끝에서. 커밋 메시지는 한국어, 말미에 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: Pretendard 폰트 번들 + OFL

**Files:**
- Create: `assets/fonts/Pretendard-Black.otf`, `-ExtraBold.otf`, `-Bold.otf`, `-SemiBold.otf`, `-Regular.otf`
- Create: `assets/fonts/OFL.txt`
- Test: `tests/test_fonts_bundle.py`

**Interfaces:**
- Produces: `assets/fonts/Pretendard-{Black,ExtraBold,Bold,SemiBold,Regular}.otf` 존재 — Task 2가 후보 경로로 참조.

- [ ] **Step 1: 폰트 5종 내려받기**

Run (프로젝트 루트에서):
```bash
mkdir -p assets/fonts
base="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/packages/pretendard/dist/public/static"
for w in Black ExtraBold Bold SemiBold Regular; do
  curl -fsSL "$base/Pretendard-$w.otf" -o "assets/fonts/Pretendard-$w.otf"
done
ls -l assets/fonts
```
Expected: 5개 `.otf` 파일, 각 수백 KB~1MB.

- [ ] **Step 2: OFL 라이선스 원문 동봉**

Run:
```bash
curl -fsSL "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/LICENSE" -o assets/fonts/OFL.txt
head -n 3 assets/fonts/OFL.txt
```
Expected: 첫 줄에 `Copyright` 및 `SIL Open Font License` 문구.

- [ ] **Step 3: 존재·로드 검증 테스트 작성**

Create `tests/test_fonts_bundle.py`:
```python
import unittest
from pathlib import Path

from PIL import ImageFont

from econ_insta.config import PROJECT_ROOT

WEIGHTS = ("Black", "ExtraBold", "Bold", "SemiBold", "Regular")


class FontBundleTest(unittest.TestCase):
    def test_all_weights_present_and_loadable(self):
        for w in WEIGHTS:
            path = PROJECT_ROOT / "assets" / "fonts" / f"Pretendard-{w}.otf"
            self.assertTrue(path.exists(), f"누락: {path}")
            # 한글 글리프가 있는지까지 확인
            font = ImageFont.truetype(str(path), 40)
            self.assertGreater(font.getlength("경제"), 0)

    def test_ofl_license_bundled(self):
        ofl = PROJECT_ROOT / "assets" / "fonts" / "OFL.txt"
        self.assertTrue(ofl.exists())
        self.assertIn("Open Font License", ofl.read_text(encoding="utf-8"))
```

- [ ] **Step 4: 테스트 실행**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_fonts_bundle -v`
Expected: PASS 2건.

- [ ] **Step 5: 커밋**

`out/`처럼 `.gitignore`가 폰트를 막지 않는지 확인하고 강제로라도 추가한다.
```bash
git add -f assets/fonts/Pretendard-*.otf assets/fonts/OFL.txt tests/test_fonts_bundle.py
git commit -m "Pretendard 5종 번들 + OFL (맑은고딕 폴백 탈출)"
```

---

### Task 2: FontSet 5단 굵기 확장

**Files:**
- Modify: `econ_insta/renderer.py` (폰트 후보 상수 아래, `FontSet` 클래스)
- Test: `tests/test_fontset_weights.py`

**Interfaces:**
- Consumes: Task 1의 `assets/fonts/Pretendard-*.otf`.
- Produces: `FontSet.at(size, *, weight="black"|"extrabold"|"bold"|"semibold"|"regular")`. 누락 굵기는 인접 굵기로 폴백. 기존 `at(size)`·`at(size, bold=True)` 시그니처 유지. `FontSet._path_for(*, weight=None, bold=False) -> Path`. Task 3~6 및 2단계 렌더러가 사용.

- [ ] **Step 1: 실패하는 테스트 작성**

Create `tests/test_fontset_weights.py`:
```python
import unittest
from pathlib import Path

from econ_insta.renderer import FontSet


class FontSetWeightTest(unittest.TestCase):
    def test_weight_names_accepted(self):
        fonts = FontSet.discover()
        for w in ("black", "extrabold", "bold", "semibold", "regular"):
            font = fonts.at(64, weight=w)
            self.assertGreater(font.getlength("경제"), 0)

    def test_bold_flag_maps_to_bold_weight(self):
        fonts = FontSet.discover()
        self.assertEqual(fonts._path_for(weight="bold"), fonts._path_for(bold=True))

    def test_missing_weight_falls_back(self):
        # black·extrabold 파일이 없는 FontSet 이라도 예외 없이 bold 로 폴백
        f = FontSet(regular=Path("r"), bold=Path("b"))
        self.assertEqual(f._path_for(weight="black"), Path("b"))
        self.assertEqual(f._path_for(weight="semibold"), Path("r"))
```

- [ ] **Step 2: 테스트 실행(실패 확인)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_fontset_weights -v`
Expected: FAIL — `FontSet`에 `_path_for`/weight 인자 없음.

- [ ] **Step 3: 폰트 후보 상수에 굵기 추가**

`renderer.py`에서 `_BOLD_CANDIDATES` 정의 아래에 추가:
```python
_BLACK_CANDIDATES = (
    PROJECT_ROOT / "assets" / "fonts" / "Pretendard-Black.otf",
)
_EXTRABOLD_CANDIDATES = (
    PROJECT_ROOT / "assets" / "fonts" / "Pretendard-ExtraBold.otf",
)
_SEMIBOLD_CANDIDATES = (
    PROJECT_ROOT / "assets" / "fonts" / "Pretendard-SemiBold.otf",
)
```

- [ ] **Step 4: FontSet 확장 구현**

`_resolve` 함수 아래에 `_resolve_optional`을 추가하고, `FontSet` 정의를 아래로 교체(`renderer.py`):
```python
def _resolve_optional(env_name: str, candidates: tuple[Path, ...]) -> Path | None:
    """없으면 None. 필수 굵기(regular/bold)와 달리 없어도 폴백하면 되므로 예외를 안 낸다."""
    override = os.environ.get(env_name)
    if override and Path(override).exists():
        return Path(override)
    for path in candidates:
        if path.exists():
            return path
    return None


@dataclass(frozen=True)
class FontSet:
    """굵기·크기별 폰트 묶음. 테스트에서는 기본 폰트를 주입한다.

    Pretendard 5단(Black/ExtraBold/Bold/SemiBold/Regular). 없는 굵기는 인접 굵기로
    우아하게 폴백한다(맑은고딕/나눔은 Regular·Bold만 있으므로 Black→Bold).
    """

    regular: Path
    bold: Path
    black: Path | None = None
    extrabold: Path | None = None
    semibold: Path | None = None

    @classmethod
    def discover(cls) -> "FontSet":
        return cls(
            regular=_resolve("ECON_INSTA_FONT", _REGULAR_CANDIDATES),
            bold=_resolve("ECON_INSTA_FONT_BOLD", _BOLD_CANDIDATES),
            black=_resolve_optional("ECON_INSTA_FONT_BLACK", _BLACK_CANDIDATES),
            extrabold=_resolve_optional("ECON_INSTA_FONT_EXTRABOLD", _EXTRABOLD_CANDIDATES),
            semibold=_resolve_optional("ECON_INSTA_FONT_SEMIBOLD", _SEMIBOLD_CANDIDATES),
        )

    def _path_for(self, *, weight: str | None = None, bold: bool = False) -> Path:
        if weight is None:
            weight = "bold" if bold else "regular"
        # 굵은→얇은 폴백 사슬. None(파일 없음)이면 다음으로.
        chains = {
            "black": (self.black, self.extrabold, self.bold),
            "extrabold": (self.extrabold, self.bold),
            "bold": (self.bold,),
            "semibold": (self.semibold, self.regular),
            "regular": (self.regular,),
        }
        for candidate in chains.get(weight, (self.regular,)):
            if candidate is not None:
                return candidate
        return self.regular

    def at(self, size: int, *, bold: bool = False, weight: str | None = None) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self._path_for(weight=weight, bold=bold)), size)
```

- [ ] **Step 5: 테스트의 StubFonts 시그니처 맞추기**

`tests/test_renderer.py:48`의 `StubFonts.at`이 새 keyword를 받도록 수정:
```python
    def at(self, size: int, *, bold: bool = False, weight=None):
        return ImageFont.load_default(size)
```

- [ ] **Step 6: 테스트 실행(통과 확인)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_fontset_weights tests.test_renderer -v`
Expected: 신규 3건 + 기존 렌더러 테스트 전부 PASS.

- [ ] **Step 7: 커밋**

```bash
git add econ_insta/renderer.py tests/test_fontset_weights.py tests/test_renderer.py
git commit -m "FontSet 5단 굵기 확장(누락 굵기 인접 폴백)"
```

---

### Task 3: Theme 그라디언트 확장 + DARK_PREMIUM

**Files:**
- Modify: `econ_insta/renderer.py` (`Theme` 클래스, 테마 상수, `THEMES`, `DEFAULT_THEME`)
- Test: `tests/test_theme_premium.py`

**Interfaces:**
- Produces: `Theme.gradient -> tuple[Color, Color]`(위/아래), `Theme.accent_glow: Color | None`, `Theme.signature: Color | None`(C 변주색). `DARK_PREMIUM` 상수. `DEFAULT_THEME = DARK_PREMIUM`. 2단계 표지/본문 렌더러가 사용.

- [ ] **Step 1: 실패하는 테스트 작성**

Create `tests/test_theme_premium.py`:
```python
import unittest

from econ_insta.renderer import DARK_AMBER, DARK_PREMIUM, DEFAULT_THEME, THEMES


class ThemePremiumTest(unittest.TestCase):
    def test_default_is_dark_premium(self):
        self.assertIs(DEFAULT_THEME, DARK_PREMIUM)

    def test_gradient_two_stops(self):
        top, bottom = DARK_PREMIUM.gradient
        self.assertEqual(len(top), 3)
        self.assertNotEqual(top, bottom)  # 실제 그라디언트

    def test_legacy_theme_gradient_is_flat(self):
        # 단색 테마는 top==bottom 으로 하위호환
        self.assertEqual(DARK_AMBER.gradient[0], DARK_AMBER.gradient[1])

    def test_premium_has_glow_and_signature(self):
        self.assertIsNotNone(DARK_PREMIUM.accent_glow)
        self.assertIsNotNone(DARK_PREMIUM.signature)

    def test_up_is_red_down_is_blue(self):
        self.assertGreater(DARK_PREMIUM.up[0], DARK_PREMIUM.up[2])   # 빨강 우세
        self.assertGreater(DARK_PREMIUM.down[2], DARK_PREMIUM.down[0])  # 파랑 우세

    def test_all_themes_still_present(self):
        self.assertIn(DARK_PREMIUM, THEMES)
        self.assertIn(DARK_AMBER, THEMES)
```

- [ ] **Step 2: 테스트 실행(실패 확인)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_theme_premium -v`
Expected: FAIL — `DARK_PREMIUM`/`gradient`/`accent_glow`/`signature` 없음.

- [ ] **Step 3: Theme에 그라디언트·글로우·시그니처 필드 추가**

`Theme` 데이터클래스에 필드 추가(기존 `down: Color = DOWN` 아래, 전부 기본값이라 하위호환):
```python
    bg_top: Color | None = None
    bg_bottom: Color | None = None
    accent_glow: Color | None = None
    signature: Color | None = None  # C 컬러 변주(풀블리드) 시그니처색

    @property
    def gradient(self) -> tuple[Color, Color]:
        """세로 그라디언트 (위, 아래). 단색 테마는 bg 로 채워 top==bottom."""
        return (self.bg_top or self.bg, self.bg_bottom or self.bg)
```

- [ ] **Step 4: DARK_PREMIUM 정의 + 기본 테마 교체**

`MONO` 정의 뒤, `THEMES` 앞에 추가:
```python
DARK_PREMIUM = Theme(
    name="다크 프리미엄",
    bg=(11, 14, 22),
    bg_cover=(10, 12, 20),
    fg=(245, 246, 250),
    muted=(139, 147, 167),
    accent=(242, 197, 78),
    body=(206, 210, 222),
    rule=(42, 48, 62),
    bg_top=(11, 14, 22),      # #0B0E16
    bg_bottom=(20, 16, 32),   # #141020
    accent_glow=(242, 197, 78),
    signature=(240, 78, 42),  # #F04E2A 버밀리언
)
```
`THEMES`와 `DEFAULT_THEME`을 교체:
```python
THEMES = (DARK_PREMIUM, DARK_AMBER, PAPER, MIDNIGHT, MONO)

DEFAULT_THEME = DARK_PREMIUM
```

- [ ] **Step 5: 테스트 실행(통과 확인)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_theme_premium -v`
Expected: PASS 6건.

- [ ] **Step 6: 전체 회귀**

Run: `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q`
Expected: 전부 PASS(기본 테마가 바뀌었어도 렌더 흐름은 동일).

- [ ] **Step 7: 커밋**

```bash
git add econ_insta/renderer.py tests/test_theme_premium.py
git commit -m "Theme 그라디언트·글로우·시그니처 확장 + DARK_PREMIUM 기본 테마"
```

---

### Task 4: 배경 프리미티브 (그라디언트·글로우·그리드)

**Files:**
- Modify: `econ_insta/renderer.py` (상단 PIL import, `wrap` 함수 위에 프리미티브 구역 신설)
- Test: `tests/test_primitives_bg.py`

**Interfaces:**
- Consumes: `Color`, `WIDTH`, `HEIGHT`, `Theme`.
- Produces:
  - `vertical_gradient(size: tuple[int, int], top: Color, bottom: Color) -> Image.Image` (RGB)
  - `radial_glow(base: Image.Image, center: tuple[int, int], radius: int, color: Color, alpha: int) -> Image.Image`
  - `grid_overlay(base: Image.Image, *, color: Color = (255, 255, 255), step: int = 108, alpha: int = 10) -> Image.Image`
  - `premium_background(theme: Theme, size: tuple[int, int] = (WIDTH, HEIGHT), *, glow_at: tuple[int, int] | None = None) -> Image.Image`
  - 2단계 표지/본문/지표가 사용.

- [ ] **Step 1: 실패하는 테스트 작성**

Create `tests/test_primitives_bg.py`:
```python
import unittest

from PIL import Image

from econ_insta.renderer import (
    DARK_PREMIUM,
    grid_overlay,
    premium_background,
    radial_glow,
    vertical_gradient,
)


class BackgroundPrimitiveTest(unittest.TestCase):
    def test_gradient_size_and_stops(self):
        img = vertical_gradient((100, 200), (0, 0, 0), (200, 200, 200))
        self.assertEqual(img.size, (100, 200))
        self.assertEqual(img.mode, "RGB")
        self.assertLess(img.getpixel((50, 0))[0], img.getpixel((50, 199))[0])

    def test_glow_lightens_center(self):
        base = Image.new("RGB", (200, 200), (10, 10, 10))
        out = radial_glow(base, (100, 100), 80, (242, 197, 78), 120)
        self.assertGreater(out.getpixel((100, 100))[0], base.getpixel((100, 100))[0])
        self.assertEqual(out.size, base.size)

    def test_grid_changes_some_pixels(self):
        base = Image.new("RGB", (216, 216), (10, 10, 12))
        out = grid_overlay(base, step=108, alpha=40)
        self.assertNotEqual(list(base.getdata()), list(out.getdata()))

    def test_premium_background_full_canvas(self):
        img = premium_background(DARK_PREMIUM)
        self.assertEqual(img.size, (1080, 1350))
        self.assertEqual(img.mode, "RGB")
```

- [ ] **Step 2: 테스트 실행(실패 확인)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_primitives_bg -v`
Expected: FAIL — 함수 미정의(ImportError).

- [ ] **Step 3: 프리미티브 구현**

`renderer.py` 상단 import에 `ImageFilter` 추가:
```python
from PIL import Image, ImageDraw, ImageFont, ImageFilter
```
`wrap` 함수 정의 바로 위에 추가:
```python
# ── 렌더 프리미티브 (순수 함수) ────────────────────────────────────────────

def vertical_gradient(size: tuple[int, int], top: Color, bottom: Color) -> Image.Image:
    """세로 그라디언트 RGB 캔버스."""
    w, h = size
    img = Image.new("RGB", (w, h), top)
    px = img.load()
    for y in range(h):
        t = y / (h - 1) if h > 1 else 0
        c = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(w):
            px[x, y] = c
    return img


def radial_glow(base: Image.Image, center: tuple[int, int], radius: int,
                color: Color, alpha: int) -> Image.Image:
    """중심에서 퍼지는 저알파 라디얼 글로우를 얹는다."""
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse(
        [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius],
        fill=color + (alpha,),
    )
    layer = layer.filter(ImageFilter.GaussianBlur(max(1, radius // 2)))
    return Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")


def grid_overlay(base: Image.Image, *, color: Color = (255, 255, 255),
                 step: int = 108, alpha: int = 10) -> Image.Image:
    """옅은 격자 질감."""
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w, h = base.size
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=color + (alpha,), width=1)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=color + (alpha,), width=1)
    return Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")


def premium_background(theme: Theme, size: tuple[int, int] = (WIDTH, HEIGHT), *,
                       glow_at: tuple[int, int] | None = None) -> Image.Image:
    """다크 프리미엄 배경: 그라디언트 + 좌하단 골드 글로우 + 옅은 그리드."""
    top, bottom = theme.gradient
    img = vertical_gradient(size, top, bottom)
    if theme.accent_glow is not None:
        at = glow_at or (size[0] // 6, int(size[1] * 0.78))
        img = radial_glow(img, at, int(size[0] * 0.58), theme.accent_glow, 30)
    return grid_overlay(img, step=108, alpha=10)
```

- [ ] **Step 4: 테스트 실행(통과 확인)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_primitives_bg -v`
Expected: PASS 4건.

- [ ] **Step 5: 커밋**

```bash
git add econ_insta/renderer.py tests/test_primitives_bg.py
git commit -m "배경 프리미티브: vertical_gradient·radial_glow·grid_overlay·premium_background"
```

---

### Task 5: 스파크라인 프리미티브

**Files:**
- Modify: `econ_insta/renderer.py` (프리미티브 구역)
- Test: `tests/test_primitives_sparkline.py`

**Interfaces:**
- Consumes: `Color`.
- Produces: `draw_sparkline(image: Image.Image, series: list[float], box: tuple[int, int, int, int], color: Color, *, fill_alpha: int = 40, line_width: int = 4, dot: bool = True) -> Image.Image` — box=(x0,y0,x1,y1) 안에 라인+면적. 2단계 지표 카드가 행마다 호출.

- [ ] **Step 1: 실패하는 테스트 작성**

Create `tests/test_primitives_sparkline.py`:
```python
import unittest

from PIL import Image

from econ_insta.renderer import DARK_PREMIUM, draw_sparkline


class SparklineTest(unittest.TestCase):
    def setUp(self):
        self.base = Image.new("RGB", (400, 120), (12, 15, 23))

    def test_draws_within_box(self):
        out = draw_sparkline(self.base, [1, 3, 2, 5, 4], (10, 10, 390, 110),
                             DARK_PREMIUM.up)
        self.assertEqual(out.size, self.base.size)
        self.assertNotEqual(list(self.base.getdata()), list(out.getdata()))

    def test_single_point_series_no_crash(self):
        out = draw_sparkline(self.base, [2.0], (10, 10, 390, 110), DARK_PREMIUM.down)
        self.assertEqual(out.size, self.base.size)

    def test_flat_series_no_crash(self):
        # 모든 값이 같으면 0으로 나누지 않는다
        out = draw_sparkline(self.base, [5, 5, 5, 5], (10, 10, 390, 110), DARK_PREMIUM.up)
        self.assertEqual(out.size, self.base.size)
```

- [ ] **Step 2: 테스트 실행(실패 확인)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_primitives_sparkline -v`
Expected: FAIL — `draw_sparkline` 미정의.

- [ ] **Step 3: 구현**

`renderer.py` 프리미티브 구역에 추가:
```python
def draw_sparkline(image: Image.Image, series: list[float],
                   box: tuple[int, int, int, int], color: Color, *,
                   fill_alpha: int = 40, line_width: int = 4, dot: bool = True) -> Image.Image:
    """box=(x0,y0,x1,y1) 안에 스파크라인(라인+면적)을 그린다. 값이 1개거나 모두 같아도 안전."""
    x0, y0, x1, y1 = box
    n = len(series)
    if n == 0:
        return image
    lo, hi = min(series), max(series)
    span = hi - lo

    def _pt(i, v):
        px = x0 if n == 1 else x0 + (x1 - x0) * i / (n - 1)
        py = (y0 + y1) / 2 if span == 0 else y1 - (y1 - y0) * (v - lo) / span
        return (px, py)

    pts = [_pt(i, v) for i, v in enumerate(series)]
    out = image.convert("RGBA")
    if n >= 2:
        area = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(area).polygon(pts + [(x1, y1), (x0, y1)], fill=color + (fill_alpha,))
        out = Image.alpha_composite(out, area)
    d = ImageDraw.Draw(out)
    if n >= 2:
        d.line(pts, fill=color, width=line_width, joint="curve")
    if dot:
        ex, ey = pts[-1]
        r = line_width + 2
        d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=color)
    return out.convert("RGB")
```

- [ ] **Step 4: 테스트 실행(통과 확인)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_primitives_sparkline -v`
Expected: PASS 3건.

- [ ] **Step 5: 커밋**

```bash
git add econ_insta/renderer.py tests/test_primitives_sparkline.py
git commit -m "스파크라인 프리미티브(라인+면적, 단일값·평탄 안전)"
```

---

### Task 6: 키커 pill 프리미티브

**Files:**
- Modify: `econ_insta/renderer.py` (프리미티브 구역)
- Test: `tests/test_primitives_pill.py`

**Interfaces:**
- Consumes: `Color`.
- Produces: `kicker_pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, color: Color, *, pad_x: int = 34, pad_y: int = 17, height: int = 78) -> float` — 라운드 아웃라인 라벨을 그리고 pill의 오른쪽 x좌표를 반환. 2단계 표지 렌더러가 사용.

- [ ] **Step 1: 실패하는 테스트 작성**

Create `tests/test_primitives_pill.py`:
```python
import unittest

from PIL import Image, ImageDraw, ImageFont

from econ_insta.renderer import DARK_PREMIUM, kicker_pill


class KickerPillTest(unittest.TestCase):
    def test_returns_right_edge_and_draws(self):
        img = Image.new("RGB", (600, 200), (11, 14, 22))
        d = ImageDraw.Draw(img)
        font = ImageFont.load_default(38)
        right = kicker_pill(d, (40, 40), "마켓 브리핑", font, DARK_PREMIUM.accent)
        self.assertGreater(right, 40)
        self.assertTrue(any(px != (11, 14, 22) for px in img.getdata()))
```

- [ ] **Step 2: 테스트 실행(실패 확인)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_primitives_pill -v`
Expected: FAIL — `kicker_pill` 미정의.

- [ ] **Step 3: 구현**

`renderer.py` 프리미티브 구역에 추가:
```python
def kicker_pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font,
                color: Color, *, pad_x: int = 34, pad_y: int = 17, height: int = 78) -> float:
    """골드 아웃라인 라운드 라벨. pill 오른쪽 x를 반환."""
    x, y = xy
    tw = draw.textlength(text, font=font)
    right = x + tw + pad_x * 2
    draw.rounded_rectangle([x, y, right, y + height], radius=height // 2, outline=color, width=3)
    draw.text((x + pad_x, y + pad_y), text, font=font, fill=color)
    return right
```

- [ ] **Step 4: 테스트 실행(통과 확인)**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_primitives_pill -v`
Expected: PASS 1건.

- [ ] **Step 5: 전체 스위트 최종 회귀**

Run: `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q`
Expected: 기존 + 신규 전부 PASS.

- [ ] **Step 6: 커밋**

```bash
git add econ_insta/renderer.py tests/test_primitives_pill.py
git commit -m "키커 pill 프리미티브(라운드 아웃라인 라벨)"
```

---

## 이 계획이 커버하는 스펙 항목 (자기 점검)

- 스펙 §5.1 폰트 번들·FontSet 5단 → Task 1, 2 ✅
- 스펙 §5.2 DARK_PREMIUM·그라디언트·시그니처·기존 테마 보존 → Task 3 ✅
- 스펙 §5.6 프리미티브 vgrad/glow/grid/sparkline/kicker_pill → Task 4, 5, 6 ✅
- 스펙 §9 롤백(DEFAULT_THEME 한 줄) → Task 3 ✅
- 스펙 §8 테스트(프리미티브 스모크, 폰트 주입, 회귀) → 각 태스크 ✅

**후속 계획(별도 파일)**: 2단계 카드 렌더러(표지 A/C·본문·지표 스파크라인 + `Quote.series`),
3단계 콘텐츠 단일 이슈 후크형(`summarizer.py`), 4단계 이미지 소싱(`backgrounds.py`),
5단계 릴스 주간화·오디오·시리즈 통일.

"""브리핑을 인스타그램 캐러셀용 카드 이미지(1080×1350 JPEG)로 렌더한다.

인스타 Graph API는 JPEG만 받고, 이미지를 자기 서버에서 image_url로 직접 가져간다.
따라서 출력은 항상 RGB JPEG이며 저장 경로는 공개 URL로 노출될 수 있어야 한다.

카드 구성: 표지 1장 + 기사 N장(3~5) + 지표 1장 = 5~7장. 캐러셀 한도는 10장.

폰트: 한글 글리프가 있는 TTF/OTF가 필요하다. 로컬(Windows)은 맑은 고딕,
CI(우분투)는 나눔고딕/Noto를 쓴다. `ECON_INSTA_FONT`/`ECON_INSTA_FONT_BOLD`로 덮어쓸 수 있다.

CLI:
    python -m econ_insta.renderer
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from .config import PROJECT_ROOT
from .summarizer import Briefing, Card

WIDTH, HEIGHT = 1080, 1350
MARGIN = 84

BG = (14, 18, 28)
BG_COVER = (10, 12, 20)
FG = (238, 240, 245)
MUTED = (138, 146, 162)
ACCENT = (255, 196, 71)

# 한국 증시 관행: 상승 빨강, 하락 파랑. 미국과 반대다.
UP = (240, 88, 88)
DOWN = (88, 148, 240)
FLAT = MUTED

Color = tuple[int, int, int]

PHOTO_SCRIM = (10, 12, 20)
"""사진 표지를 누르는 어두운 막. 테마가 밝아도 이건 어둡다 — 흰 제목이 읽혀야 하므로."""

PHOTO_SUB = (214, 220, 230)
"""사진 위 보조 텍스트(날짜). MUTED는 밝은 배경 사진에서 묻힌다."""


@dataclass(frozen=True)
class Theme:
    """카드의 색 체계.

    디자인 시안을 HTML로 그리면 고른 뒤 렌더러로 '포팅'하는 숙제가 남는다(실제로 시안
    4종을 올려두고 반영하지 못한 채 남았다). 테마를 렌더러 안에 두면 **시안이 곧 코드다** —
    고르는 순간 DEFAULT_THEME만 바꾸면 끝난다.

    등락 색(up/down)은 테마마다 다시 정하지 말 것: 한국 증시는 상승 빨강·하락 파랑이고
    이건 취향이 아니라 관행이다.
    """

    name: str
    bg: Color
    bg_cover: Color
    fg: Color
    muted: Color
    accent: Color
    body: Color
    rule: Color
    up: Color = UP
    down: Color = DOWN
    bg_top: Color | None = None
    bg_bottom: Color | None = None
    accent_glow: Color | None = None
    signature: Color | None = None

    @property
    def flat(self) -> Color:
        return self.muted

    def change_color(self, change_pct: float) -> Color:
        if change_pct > 0:
            return self.up
        if change_pct < 0:
            return self.down
        return self.flat

    @property
    def gradient(self) -> tuple[Color, Color]:
        """세로 그라디언트 (위, 아래). 단색 테마는 bg 로 채워 top==bottom."""
        return (self.bg_top or self.bg, self.bg_bottom or self.bg)


DARK_AMBER = Theme(
    name="다크 앰버 (현재)",
    bg=BG,
    bg_cover=BG_COVER,
    fg=FG,
    muted=MUTED,
    accent=ACCENT,
    body=(206, 212, 224),
    rule=(42, 48, 62),
)

PAPER = Theme(
    name="페이퍼 (신문)",
    bg=(247, 245, 240),
    bg_cover=(247, 245, 240),
    fg=(24, 24, 26),
    muted=(122, 120, 116),
    accent=(196, 30, 58),
    body=(58, 58, 62),
    rule=(214, 210, 202),
)

MIDNIGHT = Theme(
    name="미드나잇 (딥블루·시안)",
    bg=(11, 22, 40),
    bg_cover=(8, 16, 32),
    fg=(233, 240, 250),
    muted=(124, 146, 176),
    accent=(72, 214, 214),
    body=(196, 210, 228),
    rule=(30, 48, 74),
)

MONO = Theme(
    name="모노 (미니멀)",
    bg=(18, 18, 18),
    bg_cover=(12, 12, 12),
    fg=(245, 245, 245),
    muted=(130, 130, 130),
    accent=(245, 245, 245),  # 액센트도 흰색 — 색 대신 선·여백으로 위계를 만든다
    body=(190, 190, 190),
    rule=(52, 52, 52),
)

DARK_PREMIUM = Theme(
    name="프리미엄 다크",
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

THEMES = (DARK_PREMIUM, DARK_AMBER, PAPER, MIDNIGHT, MONO)

DEFAULT_THEME = DARK_PREMIUM

JPEG_QUALITY = 92

OUTPUT_ROOT = Path(os.environ.get("ECON_INSTA_OUT", PROJECT_ROOT / "out"))

# 굵기별 후보. 앞에서부터 존재하는 첫 파일을 쓴다.
_REGULAR_CANDIDATES = (
    PROJECT_ROOT / "assets" / "fonts" / "Pretendard-Regular.otf",
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
)
_BOLD_CANDIDATES = (
    PROJECT_ROOT / "assets" / "fonts" / "Pretendard-Bold.otf",
    Path("C:/Windows/Fonts/malgunbd.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
)
_BLACK_CANDIDATES = (
    PROJECT_ROOT / "assets" / "fonts" / "Pretendard-Black.otf",
)
_EXTRABOLD_CANDIDATES = (
    PROJECT_ROOT / "assets" / "fonts" / "Pretendard-ExtraBold.otf",
)
_SEMIBOLD_CANDIDATES = (
    PROJECT_ROOT / "assets" / "fonts" / "Pretendard-SemiBold.otf",
)


class RenderError(RuntimeError):
    """렌더 실패."""


def _resolve(env_name: str, candidates: tuple[Path, ...]) -> Path:
    override = os.environ.get(env_name)
    if override:
        path = Path(override)
        if not path.exists():
            raise RenderError(f"{env_name}가 가리키는 폰트가 없습니다: {path}")
        return path

    for path in candidates:
        if path.exists():
            return path

    raise RenderError(
        "한글 폰트를 찾지 못했습니다. 우분투는 `apt-get install fonts-nanum`, "
        f"그 외에는 {env_name} 환경변수로 경로를 지정하세요."
    )


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


def kicker_pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font,
                color: Color, *, pad_x: int = 34, pad_y: int = 17, height: int = 78) -> float:
    """골드 아웃라인 라운드 라벨. pill 오른쪽 x를 반환."""
    x, y = xy
    tw = draw.textlength(text, font=font)
    right = x + tw + pad_x * 2
    draw.rounded_rectangle([x, y, right, y + height], radius=height // 2, outline=color, width=3)
    draw.text((x + pad_x, y + pad_y), text, font=font, fill=color)
    return right


def wrap(text: str, font, max_width: int) -> list[str]:
    """max_width 안에 들어가도록 줄바꿈한다.

    한국어도 어절 단위 공백이 있으므로 단어 줄바꿈이 먼저다. 한 단어가 폭을 넘으면
    (긴 URL·영문 고유명사) 글자 단위로 쪼갠다.
    """
    lines: list[str] = []

    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}".strip()
            if font.getlength(candidate) <= max_width:
                line = candidate
                continue

            if line:
                lines.append(line)
                line = ""

            # 단어 자체가 한 줄을 넘으면 글자 단위로 자른다.
            for char in word:
                if font.getlength(line + char) <= max_width or not line:
                    line += char
                else:
                    lines.append(line)
                    line = char

        lines.append(line)

    return lines


def _line_height(font) -> int:
    ascent, descent = font.getmetrics()
    return int((ascent + descent) * 1.42)


def _block_height(text: str, font, max_width: int) -> int:
    """그리지 않고 문단 높이만 잰다. 세로 중앙 정렬에 필요하다."""
    return len(wrap(text, font, max_width)) * _line_height(font)


def _draw_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    *,
    top: int,
    fill: tuple[int, int, int],
    max_width: int,
    left: int = MARGIN,
) -> int:
    """문단을 그리고 다음 y좌표를 반환한다."""
    step = _line_height(font)
    lines = wrap(text, font, max_width)
    for i, line in enumerate(lines):
        draw.text((left, top + i * step), line, font=font, fill=fill)
    return top + len(lines) * step


def _canvas(color: tuple[int, int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), color)
    return image, ImageDraw.Draw(image)


def _rule(draw: ImageDraw.ImageDraw, y: int, color: Color = DARK_AMBER.rule) -> None:
    draw.line([(MARGIN, y), (WIDTH - MARGIN, y)], fill=color, width=2)


def _photo_shade() -> Image.Image:
    """사진 배경용 세로 그라디언트 마스크. 값이 클수록 어둡게 눌린다.

    위쪽(머리말 자리)은 중간쯤, 가운데(얼굴 자리)는 살짝만, 아래쪽(제목 자리)은
    진하게 눌러 흰 글씨가 어떤 사진 위에서도 읽히게 한다.
    """
    column = []
    for y in range(HEIGHT):
        if y < 320:
            alpha = 150 - int(90 * y / 320)
        elif y < 640:
            alpha = 60
        else:
            alpha = 60 + int(175 * (y - 640) / (HEIGHT - 640))
        column.append(alpha)
    mask = Image.new("L", (1, HEIGHT))
    mask.putdata(column)
    return mask.resize((WIDTH, HEIGHT))


def _fit_headline(
    headline: str,
    fonts: FontSet,
    max_width: int,
    max_height: int,
    *,
    start_size: int,
    min_size: int,
    weight: str = "black",
) -> tuple[list[str], "ImageFont.FreeTypeFont", int]:
    """헤드라인이 (max_width × max_height) 안에 들어오는 가장 큰 폰트 크기를 고른다.

    카드 밖으로 넘치는 제목은 렌더 실패나 다름없다 — 길이는 기사 제목마다 들쭉날쭉하므로
    한 번에 맞는 크기를 계산하지 않고, 큰 크기부터 단계적으로 줄이며 실제로 들어가는지 확인한다.
    """
    size = start_size
    font = fonts.at(size, weight=weight)
    lines = wrap(headline, font, max_width)
    step = _line_height(font)
    while len(lines) * step > max_height and size > min_size:
        size = max(size - 4, min_size)
        font = fonts.at(size, weight=weight)
        lines = wrap(headline, font, max_width)
        step = _line_height(font)
    return lines, font, step


def _render_cover_photo(
    headline: str,
    when: datetime,
    fonts: FontSet,
    kicker: str,
    background: Image.Image,
    theme: Theme,
) -> Image.Image:
    """사진/얼굴 표지. 기존 _photo_shade 스크림을 재사용한다(레퍼런스 cover_face)."""
    if background.size != (WIDTH, HEIGHT):
        raise RenderError(f"배경은 {WIDTH}×{HEIGHT}이어야 합니다 (받은 것: {background.size}).")
    # 스크림은 **테마와 무관하게 어둡다.** 라이트 테마의 밝은 배경색으로 누르면
    # 사진이 하얗게 뜨고 그 위의 흰 제목이 사라진다. 사진 표지 = 어두운 스크림 + 흰 글씨.
    image = Image.composite(
        Image.new("RGB", (WIDTH, HEIGHT), PHOTO_SCRIM),
        background.convert("RGB"),
        _photo_shade(),
    )
    draw = ImageDraw.Draw(image)
    inner = WIDTH - MARGIN * 2

    # 사진 위에서는 어떤 테마든 흰 글씨여야 읽힌다(어두운 스크림으로 눌러둔 위에 얹으므로).
    # 보조 텍스트(날짜)도 MUTED로 두면 밝은 하늘 위에서 사라진다 — 실제로 삼성 사옥
    # 표지에서 날짜가 안 보였다. 사진 위에서는 한 단계 밝게 쓴다.
    kicker_pill(draw, (MARGIN, MARGIN), kicker, fonts.at(38, weight="bold"), theme.accent)
    draw.text(
        (MARGIN, MARGIN + 100), f"{when:%Y년 %m월 %d일}", font=fonts.at(30), fill=PHOTO_SUB
    )

    footer_top = HEIGHT - MARGIN - 40
    lines, title_font, step = _fit_headline(
        headline, fonts, inner, footer_top - 150 - MARGIN, start_size=104, min_size=48
    )
    top = footer_top - 110 - len(lines) * step
    draw.rectangle([MARGIN, top - 44, MARGIN + 116, top - 32], fill=theme.accent)
    for i, line in enumerate(lines):
        draw.text((MARGIN, top + i * step), line, font=title_font, fill=FG)

    draw.text((MARGIN, footer_top), "넘겨서 확인하세요 →", font=fonts.at(30), fill=PHOTO_SUB)
    return image


def _render_cover_dark(
    headline: str,
    when: datetime,
    fonts: FontSet,
    kicker: str,
    theme: Theme,
) -> Image.Image:
    """다크 프리미엄 그래픽 표지(변주 A, 배경 없음). 레퍼런스 cover_graphic (데이터 히어로 차트는 스코프 밖)."""
    image = premium_background(theme)
    draw = ImageDraw.Draw(image)
    inner = WIDTH - MARGIN * 2

    kicker_pill(draw, (MARGIN, MARGIN), kicker, fonts.at(38, weight="bold"), theme.accent)
    draw.text(
        (MARGIN, MARGIN + 100), f"{when:%Y년 %m월 %d일}", font=fonts.at(30), fill=theme.muted
    )

    footer_top = HEIGHT - MARGIN - 40
    lines, title_font, step = _fit_headline(
        headline, fonts, inner, footer_top - 150 - MARGIN, start_size=104, min_size=48
    )
    top = footer_top - 110 - len(lines) * step
    draw.rectangle([MARGIN, top - 44, MARGIN + 116, top - 32], fill=theme.accent)
    for i, line in enumerate(lines):
        draw.text((MARGIN, top + i * step), line, font=title_font, fill=theme.fg)

    draw.text(
        (MARGIN, footer_top), "넘겨서 확인하세요 →", font=fonts.at(30), fill=theme.muted
    )
    return image


_INK = (22, 17, 14)


def _render_cover_color(
    headline: str,
    when: datetime,
    fonts: FontSet,
    theme: Theme,
) -> Image.Image:
    """버밀리언 풀블리드 변주(C). 레퍼런스 cover_verm."""
    signature = theme.signature or theme.accent
    image = Image.new("RGB", (WIDTH, HEIGHT), signature)
    draw = ImageDraw.Draw(image)
    inner = WIDTH - MARGIN * 2

    draw.text(
        (MARGIN, MARGIN), "MARKET BRIEFING", font=fonts.at(34, weight="extrabold"), fill=(255, 255, 255)
    )
    date_text = f"{when:%Y.%m.%d}"
    date_font = fonts.at(32, weight="semibold")
    draw.text(
        (WIDTH - MARGIN - date_font.getlength(date_text), MARGIN + 2),
        date_text,
        font=date_font,
        fill=(255, 238, 232),
    )
    draw.line([(MARGIN, MARGIN + 64), (WIDTH - MARGIN, MARGIN + 64)], fill=(255, 255, 255), width=3)

    lines, title_font, step = _fit_headline(
        headline, fonts, inner, HEIGHT - 2 * (MARGIN + 200), start_size=118, min_size=48
    )
    top = (HEIGHT - len(lines) * step) // 2 - 30
    for i, line in enumerate(lines):
        draw.text((MARGIN, top + i * step), line, font=title_font, fill=_INK)

    draw.text((MARGIN, HEIGHT - MARGIN - 150), "01", font=fonts.at(150, weight="black"), fill=(255, 255, 255))
    cta_font = fonts.at(30, weight="semibold")
    cta = "넘겨서 →"
    draw.text(
        (WIDTH - MARGIN - cta_font.getlength(cta), HEIGHT - MARGIN - 42),
        cta,
        font=cta_font,
        fill=(255, 238, 232),
    )
    return image


def render_cover(
    headline: str,
    when: datetime,
    fonts: FontSet,
    kicker: str = "데일리 경제 브리핑",
    background: Image.Image | None = None,
    theme: Theme = DEFAULT_THEME,
    variant: str = "dark",
) -> Image.Image:
    """표지 카드를 렌더한다.

    `background`가 있으면 사진/얼굴 경로(변주와 무관하게 우선). 없으면 `variant`로
    분기한다: "dark"(기본, 프리미엄 다크 그래픽) 또는 "color"(버밀리언 풀블리드).
    """
    if background is not None:
        return _render_cover_photo(headline, when, fonts, kicker, background, theme)

    if variant == "color":
        return _render_cover_color(headline, when, fonts, theme)

    if variant != "dark":
        raise RenderError(f"알 수 없는 variant입니다: {variant!r}")

    return _render_cover_dark(headline, when, fonts, kicker, theme)


def render_card(
    card: Card, index: int, total: int, fonts: FontSet, theme: Theme = DEFAULT_THEME
) -> Image.Image:
    """본문 후크 카드: 골드 레일 + 큰 번호 + 제목/본문 세로 중앙 정렬(레퍼런스 `content` 포팅).

    표지와 달리 배경은 항상 다크 프리미엄이다(사진·컬러 변주 없음) — 카드가 3~5장 연속으로
    넘어가므로 톤을 흔들면 캐러셀이 산만해진다.
    """
    image = premium_background(theme)
    draw = ImageDraw.Draw(image)
    inner = WIDTH - MARGIN * 2

    # 좌측 골드 세로 바(풀하이트) — 이 카드가 "본문"임을 표지·지표 카드와 구분한다.
    draw.rectangle([0, 0, 12, HEIGHT], fill=theme.accent)

    draw.text(
        (MARGIN, MARGIN - 8), f"{index:02d}", font=fonts.at(120, weight="black"), fill=theme.accent
    )
    page_text = f"{index} / {total}"
    draw.text(
        (WIDTH - MARGIN, MARGIN + 40),
        page_text,
        font=fonts.at(34, weight="semibold"),
        fill=theme.muted,
        anchor="ra",
    )

    title_font = fonts.at(74, weight="extrabold")
    body_font = fonts.at(46)

    # 머리말(큰 번호)과 꼬리말(출처) 사이 영역에 제목+구분선+본문을 세로 중앙 정렬한다.
    # 번호가 커진 만큼(120) 머리말 여백도 표지/지표 카드보다 넓게 잡는다.
    gap = 40
    block = _block_height(card.title, title_font, inner) + gap * 2 + _block_height(card.body, body_font, inner)
    field_top, field_bottom = MARGIN + 210, HEIGHT - MARGIN - 90
    top = max(field_top, field_top + (field_bottom - field_top - block) // 2)

    top = _draw_block(draw, card.title, title_font, top=top, fill=theme.fg, max_width=inner)
    top += gap
    _rule(draw, top, theme.rule)
    top += gap

    _draw_block(draw, card.body, body_font, top=top, fill=theme.body, max_width=inner)

    draw.text(
        (MARGIN, HEIGHT - MARGIN - 36),
        f"출처 · {card.source}",
        font=fonts.at(30),
        fill=theme.muted,
    )
    return image


@dataclass(frozen=True)
class _IndicatorLayout:
    row_height: int
    name_size: int
    price_size: int
    change_size: int
    note_size: int
    height: int
    """지표 행 + 코멘트 전체 높이."""


# 지표 개수는 수집 결과에 따라 달라진다(3건일 때도, 8건일 때도 있다).
# 기본 축척으로 넘치면 한 단계씩 줄여 카드 안에 반드시 들어오게 한다.
_SCALES = (1.0, 0.92, 0.84, 0.76, 0.68, 0.6, 0.52)

NOTE_GAP = 60


def _indicator_layout(quotes, note: str, fonts: FontSet, inner: int, available: int) -> _IndicatorLayout:
    """지표 카드가 세로로 넘치지 않는 가장 큰 축척을 고른다."""
    layout = None
    for scale in _SCALES:
        layout = _IndicatorLayout(
            row_height=int(118 * scale),
            name_size=max(int(40 * scale), 20),
            price_size=max(int(40 * scale), 20),
            change_size=max(int(34 * scale), 18),
            note_size=max(int(36 * scale), 18),
            height=0,
        )
        height = len(quotes) * layout.row_height
        if note:
            height += int(NOTE_GAP * scale) + _block_height(note, fonts.at(layout.note_size), inner)

        layout = _IndicatorLayout(**{**layout.__dict__, "height": height})
        if height <= available:
            break

    return layout


def _change_color(change_pct: float) -> tuple[int, int, int]:
    if change_pct > 0:
        return UP
    if change_pct < 0:
        return DOWN
    return FLAT


def render_indicators(
    quotes, note: str, fonts: FontSet, theme: Theme = DEFAULT_THEME
) -> Image.Image:
    """지표 리스트 카드: 이름 · 미니 스파크라인 · 값/등락% (레퍼런스 `indicators` 포팅).

    `quote.series`가 있으면 행 가운데에 등락색 스파크라인을 얹는다. 없으면(수집 실패)
    값만 그려 우아하게 저하한다 — 스파크라인 한 줄이 없다고 카드 전체를 막을 이유는 없다.
    날짜는 시그니처에 `when`이 없어 받을 수 없으므로, 부제는 날짜 없이 일반 문구로 둔다.
    """
    image = premium_background(theme)
    draw = ImageDraw.Draw(image)
    inner = WIDTH - MARGIN * 2

    draw.text((MARGIN, MARGIN), "오늘의 지표", font=fonts.at(64, weight="extrabold"), fill=theme.fg)
    draw.text((MARGIN, MARGIN + 84), "종가 기준", font=fonts.at(30), fill=theme.muted)

    field_top, field_bottom = MARGIN + 176, HEIGHT - MARGIN
    layout = _indicator_layout(quotes, note, fonts, inner, field_bottom - field_top)

    name_font = fonts.at(layout.name_size, weight="semibold")
    price_font = fonts.at(layout.price_size, weight="bold")
    change_font = fonts.at(layout.change_size, weight="bold")
    note_font = fonts.at(layout.note_size)

    top = max(field_top, field_top + (field_bottom - field_top - layout.height) // 2)

    # 가운데 스파크라인 열. 이름/값 칸은 고정 폭으로 예약해 지표 개수·자릿수가 달라져도
    # 스파크라인이 겹치지 않게 한다(레퍼런스는 고정폭 300을 씀).
    name_col_width = 260
    value_col_width = 260
    spark_x0 = MARGIN + name_col_width + 20
    spark_x1 = WIDTH - MARGIN - value_col_width - 20

    for i, quote in enumerate(quotes):
        row_top = top
        mid_y = row_top + layout.row_height / 2
        color = theme.change_color(quote.change_pct)

        draw.text((MARGIN, mid_y), quote.name, font=name_font, fill=theme.fg, anchor="lm")

        if quote.series:
            pad = max(int(layout.row_height * 0.16), 6)
            box = (spark_x0, row_top + pad, spark_x1, row_top + layout.row_height - pad)
            image = draw_sparkline(image, quote.series, box, color)
            draw = ImageDraw.Draw(image)

        draw.text(
            (WIDTH - MARGIN, mid_y - layout.price_size * 0.32),
            quote.price_text,
            font=price_font,
            fill=theme.fg,
            anchor="rm",
        )
        draw.text(
            (WIDTH - MARGIN, mid_y + layout.change_size * 0.42),
            quote.change_text,
            font=change_font,
            fill=color,
            anchor="rm",
        )

        top += layout.row_height
        if i < len(quotes) - 1:
            _rule(draw, top - max(int(layout.row_height * 0.18), 8), theme.rule)

    if note:
        _draw_block(draw, note, note_font, top=top + NOTE_GAP, fill=theme.body, max_width=inner)

    return image


def render(
    briefing: Briefing,
    when: datetime,
    out_dir: Path | None = None,
    fonts: FontSet | None = None,
    theme: Theme = DEFAULT_THEME,
    background: Image.Image | None = None,
) -> list[Path]:
    """카드 이미지를 순서대로 저장하고 경로 목록을 반환한다.

    `background`가 있으면 표지가 사진 경로로 간다. None이면 그래픽 표지.
    배경 조달은 `backgrounds.build_background()`의 몫이고 여기서는 받아 넘기기만 한다.
    """
    if not briefing.cards:
        raise RenderError("렌더할 카드가 없습니다.")

    fonts = fonts or FontSet.discover()
    target = out_dir or OUTPUT_ROOT / f"{when:%Y-%m-%d}"
    target.mkdir(parents=True, exist_ok=True)

    total = len(briefing.cards)
    images = [render_cover(briefing.headline, when, fonts, theme=theme, background=background)]
    images += [render_card(c, i, total, fonts, theme=theme) for i, c in enumerate(briefing.cards, 1)]
    if briefing.quotes:
        images.append(
            render_indicators(briefing.quotes, briefing.indicator_note, fonts, theme=theme)
        )

    if len(images) > 10:
        raise RenderError(f"캐러셀 한도는 10장인데 {len(images)}장이 만들어졌습니다.")

    paths = []
    for i, image in enumerate(images, 1):
        path = target / f"{i:02d}.jpg"
        image.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True)
        paths.append(path)
    return paths


def main() -> int:
    from .collector import collect, now_kst
    from .summarizer import summarize

    brief = collect()
    briefing = summarize(brief)
    paths = render(briefing, now_kst())

    print(f"카드 {len(paths)}장을 렌더했습니다.")
    for path in paths:
        print(f"  {path}  ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

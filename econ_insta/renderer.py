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

from PIL import Image, ImageDraw, ImageFont

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

    @property
    def flat(self) -> Color:
        return self.muted

    def change_color(self, change_pct: float) -> Color:
        if change_pct > 0:
            return self.up
        if change_pct < 0:
            return self.down
        return self.flat


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

THEMES = (DARK_AMBER, PAPER, MIDNIGHT, MONO)

DEFAULT_THEME = DARK_AMBER

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


@dataclass(frozen=True)
class FontSet:
    """굵기·크기별 폰트 묶음. 테스트에서는 기본 폰트를 주입한다."""

    regular: Path
    bold: Path

    @classmethod
    def discover(cls) -> "FontSet":
        return cls(
            regular=_resolve("ECON_INSTA_FONT", _REGULAR_CANDIDATES),
            bold=_resolve("ECON_INSTA_FONT_BOLD", _BOLD_CANDIDATES),
        )

    def at(self, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self.bold if bold else self.regular), size)


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


def render_cover(
    headline: str,
    when: datetime,
    fonts: FontSet,
    kicker: str = "데일리 경제 브리핑",
    background: Image.Image | None = None,
    theme: Theme = DEFAULT_THEME,
) -> Image.Image:
    inner = WIDTH - MARGIN * 2

    if background is None:
        image, draw = _canvas(theme.bg_cover)
    else:
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

    # 사진 위에서는 어떤 테마든 흰 글씨여야 읽힌다(어두운 스크림으로 눌러둔 위에 얹으므로).
    # 보조 텍스트(날짜)도 MUTED로 두면 밝은 하늘 위에서 사라진다 — 실제로 삼성 사옥
    # 표지에서 날짜가 안 보였다. 사진 위에서는 한 단계 밝게 쓴다.
    title_fill = FG if background is not None else theme.fg
    sub_fill = PHOTO_SUB if background is not None else theme.muted

    draw.text((MARGIN, MARGIN), kicker, font=fonts.at(38, bold=True), fill=theme.accent)
    draw.text((MARGIN, MARGIN + 62), f"{when:%Y년 %m월 %d일}", font=fonts.at(32), fill=sub_fill)

    title_font = fonts.at(84, bold=True)
    lines = wrap(headline, title_font, inner)
    step = _line_height(title_font)
    if background is None:
        top = (HEIGHT - len(lines) * step) // 2
    else:
        # 사진 위에서는 얼굴을 가리지 않도록 제목을 어둡게 눌린 하단에 앉힌다.
        top = HEIGHT - MARGIN - 110 - len(lines) * step
    for i, line in enumerate(lines):
        draw.text((MARGIN, top + i * step), line, font=title_font, fill=title_fill)

    draw.line([(MARGIN, top - 48), (MARGIN + 120, top - 48)], fill=theme.accent, width=6)
    draw.text(
        (MARGIN, HEIGHT - MARGIN - 40), "넘겨서 확인하세요 →", font=fonts.at(30), fill=sub_fill
    )
    return image


def render_card(
    card: Card, index: int, total: int, fonts: FontSet, theme: Theme = DEFAULT_THEME
) -> Image.Image:
    image, draw = _canvas(theme.bg)
    inner = WIDTH - MARGIN * 2

    draw.text((MARGIN, MARGIN), f"{index:02d}", font=fonts.at(64, bold=True), fill=theme.accent)
    draw.text(
        (WIDTH - MARGIN, MARGIN + 24),
        f"{index} / {total}",
        font=fonts.at(28),
        fill=theme.muted,
        anchor="ra",
    )

    title_font = fonts.at(58, bold=True)
    body_font = fonts.at(40)

    # 머리말(번호)과 꼬리말(출처) 사이 영역에 제목+구분선+본문을 세로 중앙 정렬한다.
    gap = 44
    block = _block_height(card.title, title_font, inner) + gap * 2 + _block_height(card.body, body_font, inner)
    field_top, field_bottom = MARGIN + 150, HEIGHT - MARGIN - 90
    top = max(field_top, field_top + (field_bottom - field_top - block) // 2)

    top = _draw_block(draw, card.title, title_font, top=top, fill=theme.fg, max_width=inner)
    top += gap
    _rule(draw, top, theme.rule)
    top += gap

    _draw_block(draw, card.body, body_font, top=top, fill=theme.body, max_width=inner)

    draw.text(
        (MARGIN, HEIGHT - MARGIN - 36),
        f"출처 · {card.source}",
        font=fonts.at(28),
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
    image, draw = _canvas(theme.bg)
    inner = WIDTH - MARGIN * 2

    draw.text((MARGIN, MARGIN), "오늘의 지표", font=fonts.at(58, bold=True), fill=theme.accent)

    field_top, field_bottom = MARGIN + 160, HEIGHT - MARGIN
    layout = _indicator_layout(quotes, note, fonts, inner, field_bottom - field_top)

    name_font = fonts.at(layout.name_size)
    price_font = fonts.at(layout.price_size, bold=True)
    change_font = fonts.at(layout.change_size, bold=True)
    note_font = fonts.at(layout.note_size)

    top = max(field_top, field_top + (field_bottom - field_top - layout.height) // 2)

    for quote in quotes:
        draw.text((MARGIN, top), quote.name, font=name_font, fill=theme.fg)
        draw.text(
            (WIDTH - MARGIN, top), quote.price_text, font=price_font, fill=theme.fg, anchor="ra"
        )
        draw.text(
            (WIDTH - MARGIN, top + int(layout.row_height * 0.44)),
            quote.change_text,
            font=change_font,
            fill=theme.change_color(quote.change_pct),
            anchor="ra",
        )
        top += layout.row_height
        _rule(draw, top - 22, theme.rule)

    if note:
        _draw_block(draw, note, note_font, top=top + NOTE_GAP, fill=theme.body, max_width=inner)

    return image


def render(
    briefing: Briefing,
    when: datetime,
    out_dir: Path | None = None,
    fonts: FontSet | None = None,
    theme: Theme = DEFAULT_THEME,
) -> list[Path]:
    """카드 이미지를 순서대로 저장하고 경로 목록을 반환한다."""
    if not briefing.cards:
        raise RenderError("렌더할 카드가 없습니다.")

    fonts = fonts or FontSet.discover()
    target = out_dir or OUTPUT_ROOT / f"{when:%Y-%m-%d}"
    target.mkdir(parents=True, exist_ok=True)

    total = len(briefing.cards)
    images = [render_cover(briefing.headline, when, fonts, theme=theme)]
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

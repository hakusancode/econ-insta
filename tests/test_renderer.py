"""renderer 테스트. 네트워크도 시스템 폰트도 타지 않는다.

시스템 폰트에 의존하면 CI에서 깨지므로 Pillow 내장 기본 폰트를 주입해 검증한다.
(글리프는 두부로 나오지만 폭·줄바꿈·이미지 규격 검증에는 영향이 없다.)
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from PIL import Image, ImageFont

from econ_insta.collector import Quote
from econ_insta.renderer import (
    DOWN,
    MARGIN,
    FLAT,
    HEIGHT,
    UP,
    WIDTH,
    FontSet,
    RenderError,
    _change_color,
    _indicator_layout,
    _resolve,
    render,
    render_card,
    render_cover,
    render_indicators,
    wrap,
)
from econ_insta.summarizer import Briefing, Card

WHEN = datetime(2026, 7, 10, 7, 30)


class StubFonts(FontSet):
    """실제 폰트 파일 없이 크기별 기본 폰트를 돌려준다."""

    def __init__(self) -> None:
        super().__init__(regular=Path("stub"), bold=Path("stub"))

    def at(self, size: int, *, bold: bool = False, weight=None):
        return ImageFont.load_default(size)


def make_briefing(**overrides) -> Briefing:
    defaults = dict(
        headline="증시 반등, 금리 동결 신호",
        indicator_note="위험자산 선호가 하루 만에 되살아났다.",
        cards=[
            Card(title="코스피 사흘 만에 반등", body="외국인이 순매수로 돌아섰다. " * 3, source="한국경제"),
            Card(title="연준, 금리 동결 시사", body="물가 둔화가 확인됐다는 발언이 나왔다.", source="WSJ"),
            Card(title="유가 하락 지속", body="공급 우려가 완화됐다.", source="The Economist"),
        ],
        quotes=[
            Quote(symbol="^KS11", name="코스피", price=2680.5, change_pct=1.24),
            Quote(symbol="USDKRW=X", name="원/달러", price=1503.6, change_pct=-0.31),
            Quote(symbol="^GSPC", name="S&P 500", price=5400.0, change_pct=0.0),
        ],
    )
    return Briefing(**{**defaults, **overrides})


class WrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.font = StubFonts().at(40)

    def test_lines_stay_within_width(self):
        lines = wrap("외국인이 순매수로 돌아서며 지수가 사흘 만에 반등했다", self.font, 300)
        self.assertTrue(lines)
        for line in lines:
            self.assertLessEqual(self.font.getlength(line), 300)

    def test_word_longer_than_line_is_split(self):
        lines = wrap("A" * 200, self.font, 300)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(self.font.getlength(line), 300)

    def test_no_characters_are_dropped(self):
        text = "코스피가 반등했다 외국인 순매수"
        joined = "".join(wrap(text, self.font, 300))
        self.assertEqual(joined.replace(" ", ""), text.replace(" ", ""))

    def test_explicit_newlines_are_preserved(self):
        self.assertEqual(len(wrap("첫줄\n둘째줄", self.font, 10_000)), 2)

    def test_char_wider_than_line_does_not_hang(self):
        # 폭이 1픽셀이어도 무한 루프에 빠지면 안 된다.
        self.assertEqual(wrap("가나다", self.font, 1), ["가", "나", "다"])


class ChangeColorTest(unittest.TestCase):
    def test_follows_korean_convention(self):
        self.assertEqual(_change_color(1.2), UP)  # 상승은 빨강
        self.assertEqual(_change_color(-0.4), DOWN)  # 하락은 파랑
        self.assertEqual(_change_color(0.0), FLAT)


class CardImageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fonts = StubFonts()
        self.briefing = make_briefing()

    def test_cover_has_instagram_dimensions(self):
        self.assertEqual(render_cover("증시 반등", WHEN, self.fonts).size, (WIDTH, HEIGHT))

    def test_long_headline_stays_on_canvas(self):
        self.assertEqual(render_cover("아주" * 40, WHEN, self.fonts).size, (WIDTH, HEIGHT))

    def test_card_has_instagram_dimensions(self):
        image = render_card(self.briefing.cards[0], 1, 3, self.fonts)
        self.assertEqual(image.size, (WIDTH, HEIGHT))

    def test_indicator_card_has_instagram_dimensions(self):
        image = render_indicators(self.briefing.quotes, self.briefing.indicator_note, self.fonts)
        self.assertEqual(image.size, (WIDTH, HEIGHT))

    def test_indicator_card_renders_without_note(self):
        self.assertEqual(render_indicators(self.briefing.quotes, "", self.fonts).size, (WIDTH, HEIGHT))


class IndicatorLayoutTest(unittest.TestCase):
    """지표 개수는 수집 결과에 따라 3~8건으로 달라진다. 어떤 경우에도 카드를 넘치면 안 된다."""

    def setUp(self) -> None:
        self.fonts = StubFonts()
        self.inner = WIDTH - MARGIN * 2
        self.available = HEIGHT - MARGIN - (MARGIN + 160)

    def quotes(self, count: int) -> list[Quote]:
        return [
            Quote(symbol=f"S{i}", name=f"지표{i}", price=1000.0 + i, change_pct=(-1) ** i * 0.5)
            for i in range(count)
        ]

    def test_layout_fits_for_every_realistic_quote_count(self):
        note = "국내 증시가 큰 폭으로 오르며 국제유가와 금값은 소폭 하락했다"
        for count in range(1, 9):
            with self.subTest(quotes=count):
                layout = _indicator_layout(self.quotes(count), note, self.fonts, self.inner, self.available)
                self.assertLessEqual(layout.height, self.available)

    def test_eight_quotes_shrink_below_full_scale(self):
        # 8건은 기본 축척(row 118)으로는 넘친다. 축척이 줄어야 한다.
        layout = _indicator_layout(self.quotes(8), "코멘트", self.fonts, self.inner, self.available)
        self.assertLess(layout.row_height, 118)

    def test_three_quotes_keep_full_scale(self):
        layout = _indicator_layout(self.quotes(3), "코멘트", self.fonts, self.inner, self.available)
        self.assertEqual(layout.row_height, 118)

    def test_long_note_also_shrinks_layout(self):
        short = _indicator_layout(self.quotes(6), "짧다", self.fonts, self.inner, self.available)
        long = _indicator_layout(self.quotes(6), "길다 " * 40, self.fonts, self.inner, self.available)
        self.assertLessEqual(long.row_height, short.row_height)
        self.assertLessEqual(long.height, self.available)

    def test_eight_quote_card_still_renders_at_full_size(self):
        image = render_indicators(self.quotes(8), "코멘트가 여기 들어간다", self.fonts)
        self.assertEqual(image.size, (WIDTH, HEIGHT))


class RenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fonts = StubFonts()
        self.briefing = make_briefing()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_writes_cover_cards_and_indicators(self):
        paths = render(self.briefing, WHEN, out_dir=self.tmp, fonts=self.fonts)
        # 표지 1 + 기사 3 + 지표 1
        self.assertEqual([p.name for p in paths], ["01.jpg", "02.jpg", "03.jpg", "04.jpg", "05.jpg"])
        for path in paths:
            self.assertGreater(path.stat().st_size, 0)

    def test_output_is_rgb_jpeg(self):
        # 인스타는 JPEG만 받는다. RGBA·팔레트 모드로 저장되면 발행이 실패한다.
        path = render(self.briefing, WHEN, out_dir=self.tmp, fonts=self.fonts)[0]
        with Image.open(path) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(image.size, (WIDTH, HEIGHT))

    def test_indicator_card_is_skipped_without_quotes(self):
        quiet = make_briefing(quotes=[])
        self.assertEqual(len(render(quiet, WHEN, out_dir=self.tmp, fonts=self.fonts)), 4)

    def test_missing_directory_is_created(self):
        target = self.tmp / "out" / "2026-07-10"
        render(self.briefing, WHEN, out_dir=target, fonts=self.fonts)
        self.assertTrue(target.is_dir())

    def test_empty_briefing_is_rejected(self):
        empty = make_briefing(cards=[])
        with self.assertRaisesRegex(RenderError, "카드가 없습니다"):
            render(empty, WHEN, out_dir=self.tmp, fonts=self.fonts)

    def test_carousel_over_ten_is_rejected(self):
        crowded = make_briefing(cards=self.briefing.cards * 4)  # 표지1 + 12 + 지표1 = 14장
        with self.assertRaisesRegex(RenderError, "캐러셀 한도"):
            render(crowded, WHEN, out_dir=self.tmp, fonts=self.fonts)


class FontResolveTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_env_override_wins(self):
        font = self.tmp / "custom.ttf"
        font.write_bytes(b"")
        with mock.patch.dict(os.environ, {"ECON_INSTA_FONT": str(font)}):
            self.assertEqual(_resolve("ECON_INSTA_FONT", ()), font)

    def test_missing_env_font_is_rejected(self):
        with mock.patch.dict(os.environ, {"ECON_INSTA_FONT": "/nowhere/none.ttf"}):
            with self.assertRaisesRegex(RenderError, "가리키는 폰트가 없습니다"):
                _resolve("ECON_INSTA_FONT", ())

    def test_first_existing_candidate_is_picked(self):
        present = self.tmp / "there.ttf"
        present.write_bytes(b"")
        with mock.patch.dict(os.environ, {}, clear=True):
            found = _resolve("ECON_INSTA_FONT", (self.tmp / "missing.ttf", present))
        self.assertEqual(found, present)

    def test_error_explains_how_to_install(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RenderError, "fonts-nanum"):
                _resolve("ECON_INSTA_FONT", (Path("/nowhere/none.ttf"),))


if __name__ == "__main__":
    unittest.main()

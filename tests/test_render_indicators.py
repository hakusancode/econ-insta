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


if __name__ == "__main__":
    unittest.main()

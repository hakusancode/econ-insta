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

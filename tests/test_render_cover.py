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

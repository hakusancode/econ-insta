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

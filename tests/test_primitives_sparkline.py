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

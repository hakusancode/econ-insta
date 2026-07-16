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

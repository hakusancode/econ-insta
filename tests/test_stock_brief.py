"""stock_brief: 장중/종가 구분, 등락률, 캡션.

장중인데 '종가'라고 쓰면 거짓말이다 — 실제로 그렇게 발행했다. 여기서 붙잡는다.
"""

import unittest
from datetime import datetime

from econ_insta.stock_brief import Reason, Series, StockBrief, build_caption


def _series(closes, intraday=False) -> Series:
    return Series(
        name="SK하이닉스",
        ticker="000660.KS",
        closes=list(closes),
        dates=[datetime(2026, 7, d + 1) for d in range(len(closes))],
        intraday=intraday,
    )


class BasisTest(unittest.TestCase):
    def test_장마감이면_종가(self):
        self.assertEqual(_series([100, 110]).basis, "종가")

    def test_장중이면_현재가(self):
        self.assertEqual(_series([100, 110], intraday=True).basis, "현재가")

    def test_캡션은_장중을_밝힌다(self):
        brief = StockBrief(
            headline="h",
            series=_series([100, 90], intraday=True),
            reasons=[Reason("t", "b", "로이터")],
            caption_hook="hook",
        )
        caption = build_caption(brief, datetime(2026, 7, 14))
        self.assertIn("장중 현재가", caption)
        self.assertNotIn("종가", caption)

    def test_장마감이면_캡션에_종가(self):
        brief = StockBrief(
            headline="h",
            series=_series([100, 90]),
            reasons=[Reason("t", "b", "로이터")],
            caption_hook="hook",
        )
        self.assertIn("종가", build_caption(brief, datetime(2026, 7, 14)))


class ChangeTest(unittest.TestCase):
    def test_등락률(self):
        series = _series([100, 200])
        self.assertAlmostEqual(series.change_pct(1), 100.0)

    def test_데이터가_모자라면_None(self):
        self.assertIsNone(_series([100, 110]).change_pct(30))

    def test_기준값이_0이면_None(self):
        self.assertIsNone(_series([0, 110]).change_pct(1))


class CaptionTest(unittest.TestCase):
    def test_투자유의_문구와_출처가_들어간다(self):
        brief = StockBrief(
            headline="h",
            series=_series([100, 90]),
            reasons=[Reason("이유1", "본문", "로이터"), Reason("이유2", "본문", "CNBC")],
            caption_hook="hook",
            hashtags=["반도체"],
        )
        caption = build_caption(brief, datetime(2026, 7, 14), credits=("촬영자 (CC BY 4.0)",))
        self.assertIn("투자 판단의 근거로 삼지 마십시오", caption)
        self.assertIn("CNBC", caption)
        self.assertIn("로이터", caption)
        self.assertIn("📷", caption)
        self.assertIn("#반도체", caption)


if __name__ == "__main__":
    unittest.main()

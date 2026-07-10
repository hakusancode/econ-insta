"""factcheck 테스트. 실제로 관측된 오류·거짓양성 사례를 그대로 고정한다."""

from __future__ import annotations

import unittest

from econ_insta.factcheck import extract_amounts, has_digits, unsupported_amounts


def values(text):
    return [a.value for a in extract_amounts(text)]


class ExtractAmountsTest(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(values("41조"), [41e12])

    def test_comma_and_decimal(self):
        self.assertEqual(values("1,503.6"), [1503.6])

    def test_english_scale(self):
        self.assertEqual(values("$26.51 billion"), [26.51e9])

    def test_composite_korean(self):
        """265억1000만 = 265억 + 1000만"""
        self.assertEqual(values("265억1000만 달러"), [265e8 + 1000e4])

    def test_does_not_merge_increasing_units(self):
        """'3억 5조' 처럼 단위가 커지면 별개 수치다."""
        self.assertEqual(values("3억 5조"), [3e8, 5e12])

    def test_does_not_merge_separated_tokens(self):
        self.assertEqual(values("265억 그리고 1000만"), [265e8, 1000e4])

    def test_percent_flagged(self):
        amounts = extract_amounts("코스피 2.52% 상승")
        self.assertTrue(amounts[0].is_percent)

    def test_non_percent_not_flagged(self):
        self.assertFalse(extract_amounts("7,476")[0].is_percent)


class UnsupportedAmountsTest(unittest.TestCase):
    SOURCE = (
        "코스피: 7,476 (+2.52%)  원/달러: 1,503.6 (-0.01%)\n"
        "외국인 투자자 국고채 41조 순매수\n"
        "raised $26.51 billion issuing American depositary receipts\n"
        "홈플러스 6월 체불임금 333억"
    )

    def test_exact_match_supported(self):
        self.assertEqual(unsupported_amounts("국고채 41조 순매수", self.SOURCE), [])

    def test_unit_conversion_supported(self):
        """$26.51 billion == 265억 달러. 거짓 양성이었던 사례."""
        self.assertEqual(unsupported_amounts("약 265억 달러를 조달", self.SOURCE), [])

    def test_composite_conversion_supported(self):
        self.assertEqual(unsupported_amounts("265억1000만 달러", self.SOURCE), [])

    def test_decimal_truncation_supported(self):
        """1,503.6 -> '1,503원대'. 거짓 양성이었던 사례."""
        self.assertEqual(unsupported_amounts("1,503원대에서 안정", self.SOURCE), [])

    def test_percent_rounding_supported(self):
        self.assertEqual(unsupported_amounts("코스피가 2.5% 올랐다", self.SOURCE), [])

    def test_altered_number_caught(self):
        """41조를 40조로 바꾼 실제 오류."""
        self.assertEqual(unsupported_amounts("순매수 규모는 40조원", self.SOURCE), ["40조"])

    def test_invented_percent_caught(self):
        """'코스피 7% 급등' — 실제 +2.52%. 실제 허구 사례."""
        self.assertEqual(unsupported_amounts("코스피 7% 급등", self.SOURCE), ["7"])

    def test_percent_and_plain_are_different_classes(self):
        """7,476은 있지만 '7,476%'는 근거가 아니다."""
        self.assertEqual(unsupported_amounts("7,476% 상승", self.SOURCE), ["7,476"])

    def test_supported_number_reused_across_sentences(self):
        self.assertEqual(unsupported_amounts("333억원 체불, 6월 발생", self.SOURCE), [])

    def test_multiple_violations_all_reported(self):
        self.assertEqual(
            unsupported_amounts("9% 급등, 50조 순매수", self.SOURCE), ["9", "50조"]
        )

    def test_no_numbers_is_clean(self):
        self.assertEqual(unsupported_amounts("증시가 큰 폭으로 올랐다", self.SOURCE), [])


class HasDigitsTest(unittest.TestCase):
    def test_true(self):
        self.assertTrue(has_digits("코스피 7% 급등"))

    def test_false(self):
        self.assertFalse(has_digits("코스피 급등 속 반도체 훈풍"))


if __name__ == "__main__":
    unittest.main()

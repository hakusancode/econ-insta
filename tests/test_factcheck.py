"""factcheck 테스트. 실제로 관측된 오류·거짓양성 사례를 그대로 고정한다."""

from __future__ import annotations

import unittest

from econ_insta.factcheck import wrong_won_direction, extract_amounts, has_digits, unsupported_amounts


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


class WonDirectionTest(unittest.TestCase):
    """원/달러가 오르면 원화 약세. 모델이 실제로 이 방향을 뒤집어 썼다."""

    def test_claiming_strong_won_while_usdkrw_rose_is_flagged(self):
        # 실측 사고: +0.14%인 날 "원화도 강세를 보이는"이라고 썼다.
        self.assertIsNotNone(wrong_won_direction("국내 증시가 오르며 원화도 강세를 보이는 가운데", 0.14))

    def test_claiming_weak_won_while_usdkrw_fell_is_flagged(self):
        # 실측 사고: -0.58%인 날 "원화는 약세로 돌아섰고"라고 썼다.
        self.assertIsNotNone(wrong_won_direction("원화는 약세로 돌아섰고 유가만 튀어 올랐다", -0.58))

    def test_correct_directions_pass(self):
        self.assertIsNone(wrong_won_direction("원화는 강세로 방향을 튼 하루였다", -1.09))
        self.assertIsNone(wrong_won_direction("원화가 약세를 이어갔다", 0.62))

    def test_sentence_without_won_claim_passes(self):
        self.assertIsNone(wrong_won_direction("코스피와 코스닥이 나란히 크게 밀렸다", 0.9))

    def test_flat_rate_rejects_any_directional_claim(self):
        self.assertIsNotNone(wrong_won_direction("원화 강세가 뚜렷하다", 0.01))

    def test_absent_quote_is_not_flagged(self):
        # 환율 수집이 실패한 날은 판정 근거가 없다. 막지 않는다.
        self.assertIsNone(wrong_won_direction("원화 강세가 뚜렷하다", None))

    def test_won_value_phrasing_is_understood(self):
        # '원화값 상승'이 아니라 '원화값'+강세/절상 표현을 잡는다.
        self.assertIsNotNone(wrong_won_direction("원화값 절상 흐름이 뚜렷하다", 0.5))

    def test_distant_mention_is_not_matched(self):
        # 12자 넘게 떨어진 '강세'는 원화에 대한 서술이 아닐 수 있다.
        self.assertIsNone(wrong_won_direction("원화 이야기는 접어두고 증시 전반이 강세", 0.5))


if __name__ == "__main__":
    unittest.main()

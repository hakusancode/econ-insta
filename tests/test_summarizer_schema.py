import unittest
from econ_insta.summarizer import Card, CARD_BODY_MAX, _validate, SummarizeError, audit


class CardRoleTest(unittest.TestCase):
    def test_role_defaults_none(self):
        self.assertIsNone(Card(title="t", body="b", source="s").role)

    def test_role_accepts_value(self):
        self.assertEqual(Card(title="t", body="b", source="s", role="무슨 일").role, "무슨 일")

    def test_legacy_three_arg_still_works(self):
        # ai_brief / blog_brief 가 이렇게 만든다
        Card(title="t", body="b", source="s")


class BodyMaxTest(unittest.TestCase):
    def test_body_max_is_160(self):
        self.assertEqual(CARD_BODY_MAX, 160)

    def test_validate_accepts_150_char_body(self):
        payload = {
            "headline": "짧은 훅",
            "indicator_note": "지표 흐름 코멘트",
            "cards": [
                {"title": "제목", "body": "가" * 150, "source": "연합뉴스"}
                for _ in range(3)
            ],
        }
        _validate(payload)  # 예외 없어야 함 (기존 120 상한이면 여기서 터졌다)

    def test_over_limit_body는_audit가_잡는다(self):
        """길이 위반은 즉사가 아니라 재생성 대상이다(2026-07-18 크론 이틀 연속 즉사 사고).
        _validate는 더 이상 길이를 안 보고, audit가 문제로 올려 재생성 루프에 태운다."""
        payload = {
            "headline": "짧은 훅",
            "indicator_note": "코멘트",
            "cards": [{"title": "제목", "body": "가" * (CARD_BODY_MAX + 1), "source": "연합뉴스"}] * 3,
        }
        _validate(payload)   # 즉사하지 않는다
        problems = audit(payload, "자료 원문", quotes=[])
        self.assertIn("card:0", problems)
        self.assertIn("줄이십시오", " ".join(problems["card:0"]))

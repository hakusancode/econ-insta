"""daily 모듈 테스트. 네트워크·API 불필요 (순수 함수만)."""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from econ_insta.collector import KST
from econ_insta.daily import EDITIONS, build_caption, output_dir, publish_with_retry
from econ_insta.ig_client import InstagramError


def card(title, source):
    return SimpleNamespace(title=title, source=source)


WHEN = datetime(2026, 7, 17, 19, 0, tzinfo=KST)


class OutputDirTest(unittest.TestCase):
    def test_경로에_KST날짜와_에디션_슬러그가_들어간다(self):
        """CI는 UTC다 — 날짜가 UTC로 계산되면 오전 실행이 전날 디렉터리에 쓴다.
        슬러그가 빠지면 해외/국내판이 같은 디렉터리를 덮어쓴다."""
        kr = output_dir(EDITIONS["kr"], WHEN)
        global_ = output_dir(EDITIONS["global"], WHEN)
        self.assertTrue(str(kr).endswith("2026-07-17-kr"))
        self.assertTrue(str(global_).endswith("2026-07-17-global"))
        self.assertNotEqual(kr, global_)

    def test_에디션이_피드를_나눠_갖는다(self):
        self.assertEqual(set(EDITIONS["kr"].feeds), {"연합뉴스", "한국경제", "매일경제"})
        self.assertEqual(set(EDITIONS["global"].feeds), {"WSJ", "The Economist"})


class BuildCaptionTest(unittest.TestCase):
    CARDS = [
        card("레버리지 규제 상향", "연합뉴스"),
        card("반응은 엇갈려", "매일경제·연합뉴스"),
    ]

    def test_복합_출처를_쪼개_dedup한다(self):
        """'매일경제·연합뉴스'를 통째로 dedup하면 '연합뉴스'와 별개 매체로 남는다
        (2026-07-17 오전 발행분의 실제 사고)."""
        caption = build_caption("훅 문장", self.CARDS, WHEN)
        self.assertIn("출처 · 매일경제 · 연합뉴스", caption)

    def test_credits가_캡션에_실린다(self):
        """CC BY 폴백 배경이면 이 줄이 없을 때 실제 라이선스 위반이다."""
        caption = build_caption("훅", self.CARDS, WHEN, credits=("Wikimedia/aaa (CC BY 4.0)",))
        self.assertIn("📷 Wikimedia/aaa (CC BY 4.0)", caption)

    def test_credits가_없으면_사진_줄도_없다(self):
        self.assertNotIn("📷", build_caption("훅", self.CARDS, WHEN))

    def test_투자유의와_해시태그가_있다(self):
        caption = build_caption("훅", self.CARDS, WHEN)
        self.assertIn("투자 권유가 아닙니다", caption)
        self.assertIn("#경제", caption)
        self.assertTrue(caption.startswith("훅"))
        self.assertIn("2026년 07월 17일 경제 브리핑", caption)


class PublishWithRetryTest(unittest.TestCase):
    """raw CDN 미전파(9004/2207052)만 재시도한다 — push 직후 우리가 GET하면 200인데
    메타 서버가 가져갈 때 실패하는 실측 함정. 다른 오류는 기다려도 안 낫는다."""

    def test_재시도_끝에_성공하면_결과를_돌려준다(self):
        calls = {"n": 0}
        def publish():
            calls["n"] += 1
            if calls["n"] < 3:
                raise InstagramError("[9004/2207052] Only photo or video can be accepted")
            return "media"
        slept: list[float] = []
        self.assertEqual(publish_with_retry(publish, sleep=slept.append), "media")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(len(slept), 2)

    def test_재시도_불가_오류는_즉시_던진다(self):
        def publish():
            raise InstagramError("캡션이 3000자로 한도를 넘습니다.")
        slept: list[float] = []
        with self.assertRaises(InstagramError):
            publish_with_retry(publish, sleep=slept.append)
        self.assertEqual(slept, [])   # 한 번도 안 기다렸다

    def test_횟수를_다_쓰면_마지막_오류를_던진다(self):
        def publish():
            raise InstagramError("9004 계속 실패")
        slept: list[float] = []
        with self.assertRaises(InstagramError):
            publish_with_retry(publish, attempts=3, sleep=slept.append)
        self.assertEqual(len(slept), 2)   # attempts-1번 대기

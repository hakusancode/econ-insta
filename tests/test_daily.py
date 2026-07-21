"""daily 모듈 테스트. 네트워크·API 불필요 (순수 함수만)."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from econ_insta.collector import KST
from econ_insta.daily import EDITIONS, build_caption, hosting_ready, output_dir, publish_with_retry
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


class HostingReadyTest(unittest.TestCase):
    """raw CDN 전파 대기 — URL 확인도 발행처럼 재시도해야 한다(스펙 §3.2).

    push 직후엔 404가 정상이다. 첫 실패에 포기하면 cron이 거의 매번 첫 관문에서 죽는다.
    """

    @staticmethod
    def _response(status, ctype="image/jpeg"):
        return SimpleNamespace(status_code=status, headers={"Content-Type": ctype})

    def test_전파가_늦어도_재시도_끝에_통과한다(self):
        calls = {"n": 0}
        def get(url, **_kwargs):
            calls["n"] += 1
            return self._response(404) if calls["n"] <= 2 else self._response(200)
        slept: list[float] = []
        self.assertTrue(hosting_ready(["https://x/01.jpg"], sleep=slept.append, get=get))
        self.assertEqual(len(slept), 2)

    def test_끝내_전파되지_않으면_False(self):
        def get(url, **_kwargs):
            return self._response(404)
        slept: list[float] = []
        self.assertFalse(hosting_ready(["https://x/01.jpg"], attempts=3, sleep=slept.append, get=get))
        self.assertEqual(len(slept), 2)   # 마지막 시도 뒤엔 안 기다린다

    def test_잘못된_ContentType도_미전파로_본다(self):
        """raw는 전파 전 text/plain 404 페이지를 줄 수 있다 — 200이어도 jpeg가 아니면 아직이다."""
        def get(url, **_kwargs):
            return self._response(200, ctype="text/plain")
        self.assertFalse(hosting_ready(["https://x/01.jpg"], attempts=2, sleep=lambda _s: None, get=get))

    def test_본문이_잘리면_미전파로_보고_재시도한다(self):
        """2026-07-17 실제 발행 사고 — CDN이 200 image/jpeg로 응답하면서 본문을 절반만 줬고,
        메타가 그 잘린 바이트를 그대로 게시했다(지표 카드 하단 절반이 회색, media_id=18087340157553909).
        상태·타입만 봐서는 못 잡는다 — 본문 해시가 로컬 원본과 일치해야 전파 완료다."""
        full = b"\xff\xd8" + b"x" * 100 + b"\xff\xd9"
        checksums = {"https://x/01.jpg": hashlib.sha256(full).hexdigest()}
        calls = {"n": 0}
        def get(url, **_kwargs):
            calls["n"] += 1
            body = full[: len(full) // 2] if calls["n"] == 1 else full
            return SimpleNamespace(status_code=200, headers={"Content-Type": "image/jpeg"}, content=body)
        slept: list[float] = []
        self.assertTrue(hosting_ready(["https://x/01.jpg"], checksums=checksums,
                                      sleep=slept.append, get=get))
        self.assertEqual(len(slept), 1)   # 잘린 1회차 뒤 한 번 기다렸다

    def test_본문이_끝내_다르면_False(self):
        checksums = {"https://x/01.jpg": hashlib.sha256(b"full-image").hexdigest()}
        def get(url, **_kwargs):
            return SimpleNamespace(status_code=200, headers={"Content-Type": "image/jpeg"},
                                   content=b"truncated")
        self.assertFalse(hosting_ready(["https://x/01.jpg"], checksums=checksums,
                                       attempts=2, sleep=lambda _s: None, get=get))


class RenderEditionCleanupTest(unittest.TestCase):
    """재렌더 전에 이전 렌더의 NN.jpg 잔재를 지워야 한다.

    2026-07-19 실사고 — 아침 CI가 6장(카드 4)을 렌더한 같은 날짜 디렉터리에 오후 재렌더가
    5장(카드 3)을 덮어쓰자 옛 06.jpg(지표 카드)가 남아 캐러셀에 지표 카드가 두 장 발행됐다.
    """

    def test_재렌더가_이전_카드_잔재를_지운다(self):
        import econ_insta.daily as mod

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            when = datetime(2026, 7, 19, 11, 0, tzinfo=KST)
            out = root / "out" / "2026-07-19-kr"
            out.mkdir(parents=True)
            (out / "06.jpg").write_bytes(b"stale-indicator-card")

            fake_brief = SimpleNamespace(articles=[1], quotes=[], errors=[], collected_at=when)
            fake_briefing = SimpleNamespace(
                headline="훅", cards=[card("제목", "연합뉴스")], issue=None, bg_query="")

            for name, value in [
                ("PROJECT_ROOT", root),
                ("collect", lambda feeds=None: fake_brief),
                ("summarize", lambda brief, issues=None: fake_briefing),
                ("build_background", lambda *a, **k: None),
                ("rank_issues", lambda articles: []),
                ("naver_rerank", lambda issues: issues),
            ]:
                self.addCleanup(setattr, mod, name, getattr(mod, name))
                setattr(mod, name, value)
            self.addCleanup(setattr, mod.renderer, "render", mod.renderer.render)
            mod.renderer.render = lambda *a, **k: None

            mod.render_edition(mod.EDITIONS["kr"])
            self.assertFalse((out / "06.jpg").exists())
            self.assertTrue((out / "caption.txt").exists())


class PublishEditionChecksumTest(unittest.TestCase):
    """publish_edition이 로컬 파일 해시를 hosting_ready에 실제로 배선하는지.

    hosting_ready에 검사가 있어도 호출부가 checksums를 안 넘기면 프로덕션은 그대로다 —
    "가드가 있다 ≠ 가드가 작동한다"(진행 원장의 반복 사례)를 배선 테스트로 막는다.
    """

    def test_publish가_로컬_해시를_hosting_ready에_넘긴다(self):
        import econ_insta.daily as mod

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out" / f"{mod.now_kst():%Y-%m-%d}-kr"
            out.mkdir(parents=True)
            (out / "01.jpg").write_bytes(b"card-one")
            (out / "caption.txt").write_text("캡션", encoding="utf-8")

            seen: dict = {}
            def fake_ready(urls, **kwargs):
                seen["urls"] = list(urls)
                seen.update(kwargs)
                return False   # InstagramClient까지 가기 전에 멈춘다

            self.addCleanup(setattr, mod, "PROJECT_ROOT", mod.PROJECT_ROOT)
            self.addCleanup(setattr, mod, "hosting_ready", mod.hosting_ready)
            mod.PROJECT_ROOT = root
            mod.hosting_ready = fake_ready

            self.assertEqual(mod.publish_edition(mod.EDITIONS["kr"]), 1)
            expected = hashlib.sha256(b"card-one").hexdigest()
            self.assertEqual(seen["checksums"][seen["urls"][0]], expected)

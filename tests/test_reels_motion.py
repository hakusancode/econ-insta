import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageFont

from econ_insta import reels
from econ_insta.renderer import FontSet
from econ_insta.stock_brief import Reason, Series

WHEN = datetime(2026, 8, 26, 12, 0)


def _solid(color):
    return Image.new("RGB", (reels.WIDTH, reels.HEIGHT), color)


class StubFonts(FontSet):
    """실제 폰트 파일 없이 크기별 기본 폰트를 돌려준다."""

    def __init__(self) -> None:
        super().__init__(regular=Path("stub"), bold=Path("stub"))

    def at(self, size: int, *, bold: bool = False, weight=None):
        return ImageFont.load_default(size)


def _fonts() -> FontSet:
    return StubFonts()


def _series() -> Series:
    closes = [100.0 + i for i in range(63)]
    dates = [WHEN] * 63
    return Series(name="코스피", ticker="^KS11", closes=closes, dates=dates)


class SceneRenderTest(unittest.TestCase):
    def test_render가_있으면_image_대신_render를_쓴다(self):
        red, blue = _solid((255, 0, 0)), _solid((0, 0, 255))
        scene = reels.Scene(image=blue, seconds=2.0, render=lambda p: red)
        # 2.0s×30fps=60프레임, 페이드 12프레임 — 30번째는 페이드 밖이라 원색이어야 한다.
        frame = list(reels.frames([scene]))[30]
        self.assertEqual(frame.getpixel((10, 10)), (255, 0, 0))

    def test_render에_진행률이_0에서_1까지_들어온다(self):
        seen = []

        def spy(p):
            seen.append(p)
            return _solid((0, 0, 0))

        list(reels.frames([reels.Scene(image=None, seconds=1.0, render=spy)]))
        self.assertAlmostEqual(seen[0], 0.0)
        self.assertAlmostEqual(seen[-1], 1.0)
        self.assertEqual(seen, sorted(seen))

    def test_정지_장면은_기존대로_image를_쓴다(self):
        blue = _solid((0, 0, 255))
        frame = list(reels.frames([reels.Scene(image=blue, seconds=2.0)]))[30]
        self.assertEqual(frame.getpixel((10, 10)), (0, 0, 255))


class MotionMathTest(unittest.TestCase):
    def test_ease_경계(self):
        self.assertEqual(reels.ease_out_cubic(0.0), 0.0)
        self.assertEqual(reels.ease_out_cubic(1.0), 1.0)
        self.assertEqual(reels.ease_out_cubic(-1.0), 0.0)   # 클램프
        self.assertEqual(reels.ease_out_cubic(2.0), 1.0)

    def test_count_value는_p06에_목표_도달(self):
        self.assertEqual(reels._count_value(-34.0, 0.0), 0.0)
        self.assertAlmostEqual(reels._count_value(-34.0, 0.6), -34.0)
        self.assertAlmostEqual(reels._count_value(-34.0, 1.0), -34.0)

    def test_visible_count_경계와_단조증가(self):
        n = 63
        values = [reels._visible_count(n, i / 100) for i in range(101)]
        self.assertEqual(values[0], 2)
        self.assertEqual(values[-1], n)
        self.assertEqual(values, sorted(values))


class AnimSceneTest(unittest.TestCase):
    def test_anim_cover_시작과_끝_프레임이_다르다(self):
        render = reels.anim_cover("코스피 급락", -6.4, WHEN, _fonts(), kicker="주간 이슈 브리핑")
        self.assertNotEqual(render(0.0).tobytes(), render(1.0).tobytes())

    def test_anim_cover_마지막_프레임은_결정적이다(self):
        render = reels.anim_cover("코스피 급락", -6.4, WHEN, _fonts(), kicker="주간 이슈 브리핑")
        self.assertEqual(render(1.0).tobytes(), render(1.0).tobytes())

    def test_anim_chart_중간에는_선이_덜_그려진다(self):
        render = reels.anim_chart(_series(), WHEN, _fonts())
        # 진행 30% 시점 프레임은 완성 프레임과 달라야 한다(오른쪽 구간 미가시).
        self.assertNotEqual(render(0.3).tobytes(), render(1.0).tobytes())

    def test_anim_reason_끝_프레임은_정지_장면과_같다(self):
        reason = Reason(title="수급 붕괴", body="외국인 순매도가 이어졌다.", source="매일경제")
        anim = reels.anim_reason(reason, 1, 2, _fonts())(1.0)
        still = reels.scene_reason(reason, 1, 2, _fonts())
        self.assertEqual(anim.tobytes(), still.tobytes())


class FfmpegCommandTest(unittest.TestCase):
    def test_음원이_없으면_무음_트랙(self):
        cmd = reels._ffmpeg_command(Path("x.mp4"), None)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000", cmd)

    def test_음원이_있으면_루프_입력과_shortest(self):
        cmd = reels._ffmpeg_command(Path("x.mp4"), Path("bgm.mp3"))
        self.assertIn("bgm.mp3", " ".join(cmd))
        self.assertIn("-stream_loop", cmd)
        self.assertIn("-shortest", cmd)
        self.assertNotIn("anullsrc=channel_layout=stereo:sample_rate=48000", cmd)

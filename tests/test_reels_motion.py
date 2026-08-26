import unittest
from PIL import Image
from econ_insta import reels


def _solid(color):
    return Image.new("RGB", (reels.WIDTH, reels.HEIGHT), color)


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

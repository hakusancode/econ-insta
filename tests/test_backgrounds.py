"""backgrounds 모듈: 인물 콜라주와 Unsplash 폴백."""

import unittest
from io import BytesIO
from unittest import mock

from PIL import Image

from econ_insta.backgrounds import (
    HEIGHT,
    WIDTH,
    BackgroundError,
    available_people,
    build_background,
    compose_people,
    cover_crop,
    fetch_unsplash,
)


def _two_tone(width: int, height: int) -> Image.Image:
    """위 절반 빨강, 아래 절반 파랑. 크롭 위치 검증용."""
    image = Image.new("RGB", (width, height), (255, 0, 0))
    image.paste(Image.new("RGB", (width, height // 2), (0, 0, 255)), (0, height // 2))
    return image


class CoverCropTest(unittest.TestCase):
    def test_정확한_크기로_잘린다(self):
        cropped = cover_crop(_two_tone(300, 200), 100, 150)
        self.assertEqual(cropped.size, (100, 150))

    def test_top_bias_0이면_위쪽이_남는다(self):
        cropped = cover_crop(_two_tone(100, 400), 100, 100, top_bias=0.0)
        self.assertEqual(cropped.getpixel((50, 0)), (255, 0, 0))
        self.assertEqual(cropped.getpixel((50, 99)), (255, 0, 0))

    def test_top_bias_1이면_아래쪽이_남는다(self):
        cropped = cover_crop(_two_tone(100, 400), 100, 100, top_bias=1.0)
        self.assertEqual(cropped.getpixel((50, 99)), (0, 0, 255))


class ComposePeopleTest(unittest.TestCase):
    """저장소에 실제로 큐레이션된 assets/people/ 를 사용한다."""

    def test_라이브러리에_인물이_있다(self):
        self.assertIn("trump", available_people())
        self.assertIn("xi", available_people())

    def test_두_명이면_좌우_분할_콜라주(self):
        background = compose_people(["trump", "xi"])
        self.assertEqual(background.image.size, (WIDTH, HEIGHT))
        self.assertEqual(len(background.credits), 2)

    def test_한_명이면_전면_크롭(self):
        background = compose_people(["trump"])
        self.assertEqual(background.image.size, (WIDTH, HEIGHT))
        self.assertEqual(len(background.credits), 1)

    def test_모르는_키는_거부한다(self):
        with self.assertRaises(BackgroundError):
            compose_people(["trump", "putin"])

    def test_세_명은_거부한다(self):
        with self.assertRaises(BackgroundError):
            compose_people(["trump", "xi", "trump"])


class _FakeResponse:
    def __init__(self, payload=None, content=b""):
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _FakeSession:
    """search → download 트리거 → 이미지 바이트 순서로 응답한다."""

    def __init__(self, image_bytes: bytes):
        self._image_bytes = image_bytes
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "/search/photos" in url:
            return _FakeResponse(
                payload={
                    "results": [
                        {
                            "urls": {"raw": "https://images.example/raw?ixid=x"},
                            "links": {"download_location": "https://api.example/download"},
                            "user": {"name": "Jane Doe"},
                        }
                    ]
                }
            )
        if "download" in url:
            return _FakeResponse(payload={})
        return _FakeResponse(content=self._image_bytes)


class FetchUnsplashTest(unittest.TestCase):
    def test_키가_없으면_None(self):
        with mock.patch("econ_insta.backgrounds._load_dotenv"), mock.patch.dict(
            "os.environ", {}, clear=True
        ):
            self.assertIsNone(fetch_unsplash("stock market"))

    def test_검색_다운로드트리거_크레딧(self):
        buffer = BytesIO()
        Image.new("RGB", (400, 500), (10, 20, 30)).save(buffer, "JPEG")
        session = _FakeSession(buffer.getvalue())

        with mock.patch("econ_insta.backgrounds._load_dotenv"), mock.patch.dict(
            "os.environ", {"UNSPLASH_ACCESS_KEY": "test-key"}, clear=True
        ):
            background = fetch_unsplash("stock market", session=session)

        self.assertEqual(background.image.size, (WIDTH, HEIGHT))
        self.assertEqual(background.credits, ("Jane Doe on Unsplash",))
        # API 가이드라인: 사진 사용 시 download_location 호출 의무
        self.assertTrue(any("download" in url for url in session.calls))


class BuildBackgroundTest(unittest.TestCase):
    def test_인물이_우선한다(self):
        background = build_background(["trump", "xi"], "us china trade war")
        self.assertEqual(len(background.credits), 2)

    def test_인물_실패는_삼키고_errors에_남긴다(self):
        errors = []
        with mock.patch("econ_insta.backgrounds._load_dotenv"), mock.patch.dict(
            "os.environ", {}, clear=True
        ):
            background = build_background(["putin"], "", errors=errors)
        self.assertIsNone(background)
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()

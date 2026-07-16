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
    fetch_wikimedia,
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

    def test_한_명이면_전면_크롭(self):
        background = compose_people(["trump"])
        self.assertEqual(background.image.size, (WIDTH, HEIGHT))

    def test_두_명이면_첫_번째만_쓴다(self):
        """좌우 나란히 콜라주는 폐기했다 — 뉴스 썸네일처럼 보여 표지가 싸구려가 된다.
        이 경로는 기사 사진이 1순위를 가져간 뒤에야 닿는 폴백의 폴백이라,
        거의 안 쓰일 분할 합성 렌더러를 새로 짜는 건 과잉이다."""
        both = compose_people(["trump", "xi"])
        first_only = compose_people(["trump"])
        self.assertEqual(list(both.image.getdata()), list(first_only.image.getdata()))

    def test_두_명이어도_크레딧은_쓴_사람_것만(self):
        """안 쓴 사진의 크레딧을 달면 캡션이 거짓말이 된다."""
        self.assertEqual(compose_people(["trump", "xi"]).credits, compose_people(["trump"]).credits)

    def test_없는_인물은_거부한다(self):
        with self.assertRaises(BackgroundError):
            compose_people(["putin"])

    def test_빈_목록은_거부한다(self):
        with self.assertRaises(BackgroundError):
            compose_people([])


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
        """콜라주는 폐기됐으므로 2명을 줘도 첫 번째 인물 배경 한 장이 나온다."""
        background = build_background(["trump", "xi"], "us china trade war")
        self.assertEqual(len(background.credits), 1)

    def test_인물_실패는_삼키고_errors에_남긴다(self):
        errors = []
        with mock.patch("econ_insta.backgrounds._load_dotenv"), mock.patch.dict(
            "os.environ", {}, clear=True
        ):
            background = build_background(["putin"], "", errors=errors)
        self.assertIsNone(background)
        self.assertEqual(len(errors), 1)


class BuildBackgroundChainTest(unittest.TestCase):
    def _photo(self):
        return Image.new("RGB", (1600, 1200), (10, 200, 10))

    def test_기사_사진이_있으면_1순위다(self):
        with mock.patch("econ_insta.backgrounds.photos.pick", return_value=self._photo()) as picked:
            background = build_background([], "", issue=mock.Mock(), headline="코스피 급락")
        self.assertIsNotNone(background)
        self.assertEqual(background.image.size, (WIDTH, HEIGHT))
        picked.assert_called_once()

    def test_뉴스_사진에는_크레딧을_안_단다(self):
        """사용자 결정. 위키미디어 CC BY 크레딧은 이와 무관하게 유지된다."""
        with mock.patch("econ_insta.backgrounds.photos.pick", return_value=self._photo()):
            background = build_background([], "", issue=mock.Mock(), headline="제목")
        self.assertEqual(background.credits, ())

    def test_issue가_None이면_사진_경로를_건너뛴다(self):
        """AI 브리핑·블로그 요약에는 Issue라는 개념이 없다."""
        with mock.patch("econ_insta.backgrounds.photos.pick") as picked:
            build_background([], "", issue=None)
        picked.assert_not_called()

    def test_사진이_없으면_인물_라이브러리로_내려간다(self):
        with mock.patch("econ_insta.backgrounds.photos.pick", return_value=None):
            background = build_background(["trump"], "", issue=mock.Mock(), headline="제목")
        self.assertIsNotNone(background)
        self.assertNotEqual(background.credits, ())

    def test_사진_경로가_터져도_발행을_막지_않는다(self):
        errors: list[str] = []
        with mock.patch("econ_insta.backgrounds.photos.pick", side_effect=RuntimeError("터짐")):
            background = build_background(["trump"], "", issue=mock.Mock(), errors=errors)
        self.assertIsNotNone(background)
        self.assertTrue(any("터짐" in e for e in errors))

    def test_위키미디어가_Unsplash보다_먼저다(self):
        """Unsplash는 인물 커버리지가 0이다(실측). 인물·로고는 공용에서만 온다."""
        order: list[str] = []

        def commons(query, session=None):
            order.append("wikimedia")
            return None

        def unsplash(query, session=None):
            order.append("unsplash")
            return None

        with mock.patch("econ_insta.backgrounds.fetch_wikimedia", commons), mock.patch(
            "econ_insta.backgrounds.fetch_unsplash", unsplash
        ):
            build_background([], "federal reserve")
        self.assertEqual(order, ["wikimedia", "unsplash"])


if __name__ == "__main__":
    unittest.main()

"""wikimedia 모듈: 라이선스 판정, HTML 크레딧 정리, 429 재시도.

네트워크를 타지 않는다 — 공용 API 응답을 흉내 낸 가짜 세션을 넣는다.
라이선스 판정이 이 모듈의 존재 이유이므로 거기에 테스트를 몰아둔다.
"""

import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from econ_insta import wikimedia
from econ_insta.wikimedia import (
    CommonsImage,
    WikimediaError,
    contact_sheet,
    portrait_candidates,
    search_images,
    strip_html,
)


def _page(
    title: str,
    license_slug: str,
    license_name: str = "",
    artist: str = "Someone",
    width: int = 1200,
    height: int = 1500,
    mime: str = "image/jpeg",
    restrictions: str = "",
) -> dict:
    return {
        "title": title,
        "imageinfo": [
            {
                "mime": mime,
                "width": width,
                "height": height,
                "thumburl": f"https://upload.wikimedia.org/{title}.jpg",
                "thumbwidth": width,
                "thumbheight": height,
                "descriptionurl": f"https://commons.wikimedia.org/wiki/{title}",
                "extmetadata": {
                    "License": {"value": license_slug},
                    "LicenseShortName": {"value": license_name or license_slug.upper()},
                    "Artist": {"value": artist},
                    "Restrictions": {"value": restrictions},
                },
            }
        ],
    }


class FakeResponse:
    def __init__(self, payload=None, content=b"", status_code=200, headers=None):
        self._payload = payload
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise wikimedia.requests.HTTPError(f"{self.status_code}")


class FakeSearchSession:
    """검색 한 번에 지정한 페이지들을 돌려준다."""

    def __init__(self, pages: list[dict]):
        self.pages = {str(i): page for i, page in enumerate(pages)}

    def get(self, url, **kwargs):
        return FakeResponse(payload={"query": {"pages": self.pages}})


def _jpeg_bytes(width=100, height=125) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(buffer, "JPEG")
    return buffer.getvalue()


class StripHtmlTest(unittest.TestCase):
    def test_앵커_태그를_걷어낸다(self):
        raw = '<a rel="nofollow" href="https://flickr.com/x">Federalreserve</a>'
        self.assertEqual(strip_html(raw), "Federalreserve")

    def test_엔티티를_풀고_공백을_줄인다(self):
        self.assertEqual(strip_html("A &amp;  B\n C"), "A & B C")

    def test_빈_값도_견딘다(self):
        self.assertEqual(strip_html(""), "")


class LicenseFilterTest(unittest.TestCase):
    """이 모듈의 핵심: 라이선스를 추측하지 않고 API 슬러그로만 판정한다."""

    def test_퍼블릭도메인과_CC_BY는_통과한다(self):
        session = FakeSearchSession(
            [_page("File:A", "pd"), _page("File:B", "cc0"), _page("File:C", "cc-by-4.0")]
        )
        found = search_images("x", session=session)
        self.assertEqual(len(found), 3)

    def test_CC_BY_SA는_기본_제외된다(self):
        # 배경으로 깔면 카드 전체가 동일조건에 엮일 여지가 있어 기본은 막는다.
        session = FakeSearchSession([_page("File:SA", "cc-by-sa-4.0")])
        self.assertEqual(search_images("x", session=session), [])

    def test_CC_BY_SA는_명시하면_열린다(self):
        session = FakeSearchSession([_page("File:SA", "cc-by-sa-4.0")])
        found = search_images("x", session=session, allow_sharealike=True)
        self.assertEqual(len(found), 1)

    def test_비영리_변경금지_라이선스는_거른다(self):
        session = FakeSearchSession(
            [_page("File:NC", "cc-by-nc-4.0"), _page("File:ND", "cc-by-nd-4.0")]
        )
        self.assertEqual(search_images("x", session=session), [])

    def test_모르는_라이선스는_낙관하지_않고_버린다(self):
        session = FakeSearchSession([_page("File:X", ""), _page("File:Y", "fairuse")])
        self.assertEqual(search_images("x", session=session), [])

    def test_초상권_경고가_붙으면_라이선스와_무관하게_거른다(self):
        # 실제로 파월 사진 하나에 Restrictions=personality 가 달려 있었다.
        session = FakeSearchSession([_page("File:P", "pd", restrictions="personality")])
        self.assertEqual(search_images("x", session=session), [])

    def test_너무_작은_이미지는_거른다(self):
        session = FakeSearchSession([_page("File:S", "pd", width=320, height=240)])
        self.assertEqual(search_images("x", session=session), [])

    def test_비트맵이_아니면_거른다(self):
        session = FakeSearchSession([_page("File:V", "pd", mime="image/svg+xml")])
        self.assertEqual(search_images("x", session=session), [])


class RankTest(unittest.TestCase):
    def test_퍼블릭도메인이_CC_BY보다_앞선다(self):
        session = FakeSearchSession(
            [_page("File:ccby", "cc-by-4.0"), _page("File:pd", "pd")]
        )
        found = search_images("x", session=session)
        self.assertEqual(found[0].title, "File:pd")

    def test_표지_비율에_가까운_사진이_앞선다(self):
        session = FakeSearchSession(
            [
                _page("File:wide", "pd", width=2000, height=1000),
                _page("File:tall", "pd", width=1080, height=1350),
            ]
        )
        found = search_images("x", session=session)
        self.assertEqual(found[0].title, "File:tall")

    def test_초상_후보는_세로가_가로보다_앞선다(self):
        session = FakeSearchSession(
            [
                _page("File:landscape", "pd", width=1600, height=1067),
                _page("File:portrait", "cc-by-4.0", width=1024, height=1280),
            ]
        )
        found = portrait_candidates("x", session=session)
        self.assertEqual(found[0].title, "File:portrait")


class CreditTest(unittest.TestCase):
    def test_크레딧에_촬영자와_라이선스가_들어간다(self):
        session = FakeSearchSession(
            [_page("File:A", "cc-by-4.0", license_name="CC BY 4.0", artist="Peter Dasilva")]
        )
        credit = search_images("x", session=session)[0].credit
        self.assertIn("Peter Dasilva", credit)
        self.assertIn("CC BY 4.0", credit)
        self.assertIn("Wikimedia Commons", credit)

    def test_촬영자가_없으면_Unknown(self):
        session = FakeSearchSession([_page("File:A", "pd", artist="")])
        self.assertIn("Unknown", search_images("x", session=session)[0].credit)


class DownloadRetryTest(unittest.TestCase):
    """upload.wikimedia.org는 연속 요청에 429를 준다 (실제로 맞았다)."""

    def setUp(self):
        self.image = CommonsImage(
            title="File:T",
            url="https://upload.wikimedia.org/t.jpg",
            width=100,
            height=125,
            license_slug="pd",
            license_name="Public domain",
            artist="A",
            descriptionurl="https://commons.wikimedia.org/wiki/File:T",
        )

    def test_429면_물러섰다_다시_친다(self):
        responses = [
            FakeResponse(status_code=429),
            FakeResponse(content=_jpeg_bytes(), status_code=200),
        ]
        calls = []

        class Session:
            def get(self, url, **kwargs):
                return responses.pop(0)

        slept = []
        loaded = wikimedia.download(self.image, session=Session(), sleep=slept.append)
        self.assertEqual(loaded.size, (100, 125))
        self.assertEqual(len(slept), 1)  # 한 번 쉬고 재시도했다

    def test_계속_429면_포기하고_에러(self):
        class Session:
            def get(self, url, **kwargs):
                return FakeResponse(status_code=429)

        with self.assertRaises(WikimediaError):
            wikimedia.download(self.image, session=Session(), sleep=lambda _: None)

    def test_짧은_Retry_After는_그만큼_기다린다(self):
        responses = [
            FakeResponse(status_code=429, headers={"retry-after": "8"}),
            FakeResponse(content=_jpeg_bytes(), status_code=200),
        ]

        class Session:
            def get(self, url, **kwargs):
                return responses.pop(0)

        slept = []
        wikimedia.download(self.image, session=Session(), sleep=slept.append)
        self.assertEqual(slept, [8.0])

    def test_긴_차단은_기다리지_않고_즉시_실패한다(self):
        # 공용은 429에 Retry-After: 600(10분)을 붙인다. 발행 파이프라인이 10분을 자면 안 된다.
        class Session:
            def get(self, url, **kwargs):
                return FakeResponse(status_code=429, headers={"retry-after": "600"})

        slept = []
        with self.assertRaises(WikimediaError) as caught:
            wikimedia.download(self.image, session=Session(), sleep=slept.append)
        self.assertEqual(slept, [])  # 한 번도 자지 않았다
        self.assertIn("600초", str(caught.exception))


class ContactSheetTest(unittest.TestCase):
    def test_후보들을_한_장에_붙인다(self):
        import tempfile

        session_pages = [_page("File:A", "pd"), _page("File:B", "pd")]
        candidates = search_images("x", session=FakeSearchSession(session_pages))

        class Session:
            def get(self, url, **kwargs):
                return FakeResponse(content=_jpeg_bytes(), status_code=200)

        with tempfile.TemporaryDirectory() as tmp:
            path = contact_sheet(
                candidates, Path(tmp) / "sheet.jpg", session=Session(), sleep=lambda _: None
            )
            self.assertTrue(path.exists())
            with Image.open(path) as sheet:
                self.assertEqual(sheet.width, 4 * 280)  # 4열 고정


if __name__ == "__main__":
    unittest.main()

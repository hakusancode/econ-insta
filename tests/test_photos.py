"""photos: 이슈의 기사 사진에서 표지 후보 고르기."""

import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image

from econ_insta.collector import Article
from econ_insta.issues import Issue
from econ_insta.photos import (
    MAX_CANDIDATES,
    Candidate,
    candidates,
    is_placeholder,
    pick,
    usable,
)

KST = timezone(timedelta(hours=9))


def art(source: str, images: list[str], title: str = "제목") -> Article:
    """Article은 link·published가 필수다(3단계a에서 확인된 계약)."""
    return Article(
        source=source,
        title=title,
        link=f"https://example.com/{source}",
        published=datetime(2026, 7, 16, 10, 0, tzinfo=KST),
        images=images,
    )


YNA_ORIGINAL = "https://img.yna.co.kr/photo/yna/YH/2026/07/16/PYH2026071617330001300_P2.jpg"
MK_RECEIVED = "https://pimg.mk.co.kr/news/cms/202607/16/rcv.YNA.20260716.PYH2026071617330001300_R.jpg"


class PhotoKeyTest(unittest.TestCase):
    def test_연합_사진ID가_같으면_매체가_달라도_한_후보로_묶인다(self):
        """매경은 연합 사진을 rcv.YNA...PYH<ID>_R.jpg로 받아쓴다 — 파일명에 연합
        사진 ID가 박혀 있고 연합 원본은 같은 ID의 _P2.jpg다(실측).
        여러 매체가 같은 사진을 골랐다 = 그 이슈의 대표 사진."""
        issue = Issue(articles=[art("연합뉴스", [YNA_ORIGINAL]), art("매일경제", [MK_RECEIVED])])
        result = candidates(issue)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].sources, frozenset({"연합뉴스", "매일경제"}))
        self.assertEqual(result[0].freq, 2)

    def test_PCM_ID도_묶인다(self):
        issue = Issue(
            articles=[
                art("연합뉴스", ["https://img.yna.co.kr/photo/cms/PCM20260701000098990_P2.jpg"]),
                art("매일경제", ["https://pimg.mk.co.kr/rcv.YNA.PCM20260701000098990_R.jpg"]),
            ]
        )
        self.assertEqual(len(candidates(issue)), 1)

    def test_PYH_PCM_아닌_P_패턴은_느슨한_정규식에도_안_묶인다(self):
        r"""옛날 느슨한 정규식(P[A-Z]{2}\d{10,})으로 회귀해도 대처하는지 확인.
        같은 가짜 ID(예: PAB1234567890)를 두 무관한 매체에 심으면, 현재 정규식은
        ID를 못 잡아 URL로 키를 만들어 2개 후보를 낸다. 느슨해지면 같은 ID로
        1개로 뭉개진다 — 이 테스트가 그걸 잡는 안전망이다."""
        issue = Issue(
            articles=[
                art("WSJ", ["https://images.wsj.net/im-PAB1234567890_p.jpg"]),
                art("로이터", ["https://media.reuters.com/im-PAB1234567890_q.jpg"]),
            ]
        )
        result = candidates(issue)
        self.assertEqual(len(result), 2, "같은 가짜 ID PAB1234567890을 가진 두 URL은 PYH/PCM이 아니므로 병합되지 않아야 한다")
        self.assertEqual(result[0].sources, frozenset({"WSJ"}))
        self.assertEqual(result[1].sources, frozenset({"로이터"}))

    def test_ID가_없으면_URL이_키라서_병합되지_않는다(self):
        """안전한 저하 — 모르는 형식을 억지로 묶지 않는다."""
        issue = Issue(
            articles=[
                art("WSJ", ["https://images.wsj.net/im-1"]),
                art("The Economist", ["https://images.wsj.net/im-2"]),
            ]
        )
        self.assertEqual(len(candidates(issue)), 2)


class CandidatesTest(unittest.TestCase):
    def test_이미지가_없는_기사는_후보를_안_낸다(self):
        issue = Issue(articles=[art("한국경제", [])])
        self.assertEqual(candidates(issue), [])

    def test_한_기사의_사진_여러_장이_각각_후보가_된다(self):
        issue = Issue(
            articles=[art("연합뉴스", ["https://img.yna.co.kr/a.jpg", "https://img.yna.co.kr/b.jpg"])]
        )
        self.assertEqual(len(candidates(issue)), 2)

    def test_같은_매체가_같은_사진을_두_번_실으면_빈도는_2_매체는_1(self):
        issue = Issue(
            articles=[art("연합뉴스", [YNA_ORIGINAL]), art("연합뉴스", [YNA_ORIGINAL], title="다른 제목")]
        )
        result = candidates(issue)
        self.assertEqual(result[0].freq, 2)
        self.assertEqual(result[0].sources, frozenset({"연합뉴스"}))

    def test_Candidate는_불변이다(self):
        issue = Issue(articles=[art("연합뉴스", [YNA_ORIGINAL])])
        with self.assertRaises(Exception):
            candidates(issue)[0].url = "바꿀 수 없다"


def _jpeg(width: int, height: int) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (120, 30, 30)).save(buffer, "JPEG")
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, content: bytes = b"", status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.headers: dict[str, str] = {}


class FakeSession:
    """URL → 응답. 등록 안 된 URL은 404."""

    def __init__(self, routes: dict[str, FakeResponse]):
        self.routes = routes
        self.asked: list[str] = []

    def get(self, url, **kwargs):
        self.asked.append(url)
        return self.routes.get(url, FakeResponse(status_code=404))


def cand(url: str, freq: int = 1, sources=("연합뉴스",)) -> Candidate:
    return Candidate(url=url, sources=frozenset(sources), freq=freq)


class IsPlaceholderTest(unittest.TestCase):
    def test_한경_로고는_플레이스홀더다(self):
        """한경 og:image는 전부 이 로고다(실측). 안 거르면 표지에 한경 로고가 박힌다."""
        self.assertTrue(
            is_placeholder("https://static.hankyung.com/img/logo/logo-news-sns.png?v=20201130")
        )

    def test_매경_facebook_기본이미지는_플레이스홀더다(self):
        self.assertTrue(is_placeholder("https://static.mk.co.kr/facebook_mknews.jpg"))

    def test_실제_기사_사진은_플레이스홀더가_아니다(self):
        self.assertFalse(is_placeholder(YNA_ORIGINAL))
        self.assertFalse(is_placeholder(MK_RECEIVED))
        self.assertFalse(is_placeholder("https://images.wsj.net/im-925351"))

    def test_대소문자를_무시한다(self):
        self.assertTrue(is_placeholder("https://STATIC.MK.CO.KR/FACEBOOK_mknews.jpg"))


class UsableTest(unittest.TestCase):
    def test_플레이스홀더는_다운로드도_안_한다(self):
        session = FakeSession({})
        result = usable([cand("https://static.mk.co.kr/facebook_mknews.jpg")], session=session)
        self.assertEqual(result, [])
        self.assertEqual(session.asked, [])

    def test_짧은_변이_640_미만이면_버린다(self):
        """1080×1350 표지라 그 미만은 확대하면 뭉갠다."""
        url = "https://img.example.com/small.jpg"
        session = FakeSession({url: FakeResponse(_jpeg(400, 300))})
        self.assertEqual(usable([cand(url)], session=session), [])

    def test_충분히_크면_남는다(self):
        url = "https://img.example.com/big.jpg"
        session = FakeSession({url: FakeResponse(_jpeg(1200, 800))})
        result = usable([cand(url)], session=session)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0].url, url)
        self.assertEqual(result[0][1].size, (1200, 800))

    def test_연합_P2는_P4를_먼저_시도한다(self):
        """RSS는 _P2(작은 것), og:image는 같은 사진의 _P4(큰 것)를 준다(실측).
        URL 문자열 치환으로 만든다 — 기사 페이지를 가져오는 게 아니다."""
        p4 = YNA_ORIGINAL.replace("_P2.", "_P4.")
        session = FakeSession({p4: FakeResponse(_jpeg(1600, 1000))})
        result = usable([cand(YNA_ORIGINAL)], session=session)
        self.assertEqual(session.asked[0], p4)
        self.assertEqual(result[0][1].size, (1600, 1000))

    def test_P4가_없으면_P2로_내려간다(self):
        session = FakeSession({YNA_ORIGINAL: FakeResponse(_jpeg(900, 700))})
        result = usable([cand(YNA_ORIGINAL)], session=session)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1].size, (900, 700))

    def test_다운로드_실패는_그_후보만_건너뛴다(self):
        good = "https://img.example.com/good.jpg"
        session = FakeSession({good: FakeResponse(_jpeg(1000, 1000))})
        result = usable([cand("https://img.example.com/dead.jpg"), cand(good)], session=session)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0].url, good)

    def test_깨진_바이트는_그_후보만_건너뛴다(self):
        url = "https://img.example.com/broken.jpg"
        session = FakeSession({url: FakeResponse("이건 JPEG가 아니다".encode("utf-8"))})
        self.assertEqual(usable([cand(url)], session=session), [])

    def test_빈도가_높은_후보가_앞에_온다(self):
        low = "https://img.example.com/low.jpg"
        high = "https://img.example.com/high.jpg"
        session = FakeSession(
            {low: FakeResponse(_jpeg(1000, 1000)), high: FakeResponse(_jpeg(1000, 1000))}
        )
        result = usable([cand(low, freq=1), cand(high, freq=3)], session=session)
        self.assertEqual([c.url for c, _ in result], [high, low])

    def test_빈도가_같으면_큰_사진이_앞에_온다(self):
        small = "https://img.example.com/s.jpg"
        big = "https://img.example.com/b.jpg"
        session = FakeSession(
            {small: FakeResponse(_jpeg(700, 700)), big: FakeResponse(_jpeg(1600, 1600))}
        )
        result = usable([cand(small), cand(big)], session=session)
        self.assertEqual([c.url for c, _ in result], [big, small])

    def test_최대_6장까지만(self):
        routes = {}
        cands = []
        for i in range(9):
            url = f"https://img.example.com/{i}.jpg"
            routes[url] = FakeResponse(_jpeg(1000, 1000))
            cands.append(cand(url, freq=9 - i))
        result = usable(cands, session=FakeSession(routes))
        self.assertEqual(len(result), MAX_CANDIDATES)

    def test_429는_물러섰다_다시_친다(self):
        busy = FakeResponse(status_code=429)
        busy.headers = {"Retry-After": "1"}
        ok = FakeResponse(_jpeg(1000, 1000))

        class Flaky:
            def __init__(self):
                self.calls = 0

            def get(self, url, **kwargs):
                self.calls += 1
                return busy if self.calls == 1 else ok

        waits: list[float] = []
        result = usable([cand("https://img.example.com/busy.jpg")], session=Flaky(), sleep=waits.append)
        self.assertEqual(len(result), 1)
        self.assertEqual(waits, [1.0])


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeUsage:
    input_tokens = 100
    output_tokens = 20


class FakeAPIResponse:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [FakeBlock(text)]
        self.stop_reason = stop_reason
        self.usage = FakeUsage()


class FakeMessages:
    def __init__(self, text="", stop_reason="end_turn", raises=None):
        self.text = text
        self.stop_reason = stop_reason
        self.raises = raises
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.raises:
            raise self.raises
        return FakeAPIResponse(self.text, self.stop_reason)


class FakeClient:
    def __init__(self, text="", stop_reason="end_turn", raises=None):
        self.messages = FakeMessages(text, stop_reason, raises)


def _issue_with(count: int) -> tuple[Issue, FakeSession]:
    routes = {}
    urls = []
    for i in range(count):
        url = f"https://img.example.com/{i}.jpg"
        routes[url] = FakeResponse(_jpeg(1000, 1000))
        urls.append(url)
    return Issue(articles=[art("연합뉴스", urls)]), FakeSession(routes)


class PickTest(unittest.TestCase):
    def test_모델이_고른_번호의_사진을_돌려준다(self):
        issue, session = _issue_with(3)
        client = FakeClient('{"pick": 1, "reason": "인물 얼굴이 크게 잡혔다"}')
        result = pick(issue, "코스피 급락", client=client, session=session)
        self.assertIsNotNone(result)
        self.assertEqual(result.size, (1000, 1000))

    def test_pick이_null이면_None(self):
        """쓸 게 없으면 억지로 고르지 않는다 — 사물컷뿐인 이슈."""
        issue, session = _issue_with(2)
        client = FakeClient('{"pick": null, "reason": "전부 사옥 정면 사진"}')
        self.assertIsNone(pick(issue, "삼성 신사옥", client=client, session=session))

    def test_후보가_없으면_모델을_부르지_않는다(self):
        issue = Issue(articles=[art("한국경제", [])])
        client = FakeClient('{"pick": 0, "reason": "부르면 안 된다"}')
        self.assertIsNone(pick(issue, "제목", client=client, session=FakeSession({})))
        self.assertIsNone(client.messages.kwargs)

    def test_API가_죽으면_기계_1등을_자동_채택하지_않고_None(self):
        """자동 1등 채택은 정확히 팬아트 사고의 경로다. 점수를 매기는 주체가
        Claude이므로 Claude가 없으면 점수도 없다."""
        issue, session = _issue_with(3)
        client = FakeClient(raises=RuntimeError("API 죽음"))
        self.assertIsNone(pick(issue, "제목", client=client, session=session))

    def test_범위_밖_번호는_None(self):
        issue, session = _issue_with(2)
        client = FakeClient('{"pick": 7, "reason": "없는 번호"}')
        self.assertIsNone(pick(issue, "제목", client=client, session=session))

    def test_JSON이_깨져도_죽지_않고_None(self):
        issue, session = _issue_with(2)
        client = FakeClient("이건 JSON이 아니다")
        self.assertIsNone(pick(issue, "제목", client=client, session=session))

    def test_후보_이미지가_프롬프트에_첨부된다(self):
        issue, session = _issue_with(3)
        client = FakeClient('{"pick": 0, "reason": "좋다"}')
        pick(issue, "코스피 급락", client=client, session=session)
        content = client.messages.kwargs["messages"][0]["content"]
        images = [b for b in content if b["type"] == "image"]
        self.assertEqual(len(images), 3)
        self.assertEqual(images[0]["source"]["type"], "base64")

    def test_이슈_제목이_프롬프트에_들어간다(self):
        issue, session = _issue_with(1)
        client = FakeClient('{"pick": 0, "reason": "좋다"}')
        pick(issue, "코스피 7000 붕괴", client=client, session=session)
        content = client.messages.kwargs["messages"][0]["content"]
        texts = " ".join(b["text"] for b in content if b["type"] == "text")
        self.assertIn("코스피 7000 붕괴", texts)

    def test_max_tokens에서_잘리면_None(self):
        issue, session = _issue_with(2)
        client = FakeClient('{"pick": 0', stop_reason="max_tokens")
        self.assertIsNone(pick(issue, "제목", client=client, session=session))


if __name__ == "__main__":
    unittest.main()

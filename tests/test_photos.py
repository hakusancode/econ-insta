"""photos: 이슈의 기사 사진에서 표지 후보 고르기."""

import unittest
from datetime import datetime, timedelta, timezone

from econ_insta.collector import Article
from econ_insta.issues import Issue
from econ_insta.photos import Candidate, candidates

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


if __name__ == "__main__":
    unittest.main()

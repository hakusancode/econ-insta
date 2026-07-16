"""collector: RSS/Atom 항목의 이미지 URL 추출."""

import unittest

from econ_insta.collector import parse_feed

MEDIA_NS = 'xmlns:media="http://search.yahoo.com/mrss/"'


def _rss(items: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" {MEDIA_NS}>
  <channel>{items}</channel>
</rss>""".encode("utf-8")


def _item(extra: str = "") -> str:
    return f"""<item>
      <title>제목</title>
      <link>https://example.com/a</link>
      <pubDate>Thu, 16 Jul 2026 10:00:00 +0900</pubDate>
      {extra}
    </item>"""


class ArticleImagesTest(unittest.TestCase):
    def test_media_content가_여러_장이면_순서대로_모은다(self):
        xml = _rss(
            _item(
                '<media:content url="https://img.yna.co.kr/a_P2.jpg"/>'
                '<media:content url="https://img.yna.co.kr/b_P2.jpg"/>'
            )
        )
        articles = parse_feed("연합뉴스", xml)
        self.assertEqual(
            articles[0].images,
            ["https://img.yna.co.kr/a_P2.jpg", "https://img.yna.co.kr/b_P2.jpg"],
        )

    def test_media_thumbnail도_모은다(self):
        xml = _rss(_item('<media:thumbnail url="https://img.example.com/t.jpg"/>'))
        self.assertEqual(parse_feed("매일경제", xml)[0].images, ["https://img.example.com/t.jpg"])

    def test_enclosure도_모은다(self):
        xml = _rss(_item('<enclosure url="https://img.example.com/e.jpg" type="image/jpeg"/>'))
        self.assertEqual(parse_feed("매일경제", xml)[0].images, ["https://img.example.com/e.jpg"])

    def test_이미지_태그가_없으면_빈_리스트(self):
        """한경은 이미지가 구조적으로 0이다(실측). 버그가 아니라 그 매체의 성질."""
        self.assertEqual(parse_feed("한국경제", _rss(_item()))[0].images, [])

    def test_이미지가_아닌_enclosure는_거른다(self):
        """enclosure는 팟캐스트 오디오도 나른다. type이 있으면 믿는다."""
        xml = _rss(_item('<enclosure url="https://example.com/p.mp3" type="audio/mpeg"/>'))
        self.assertEqual(parse_feed("매일경제", xml)[0].images, [])

    def test_type이_없으면_받아들인다(self):
        """WSJ media:content는 확장자도 type도 없다(images.wsj.net/im-925351, 실측).
        확장자로 판정하면 WSJ 사진이 통째로 날아간다."""
        xml = _rss(_item('<media:content url="https://images.wsj.net/im-925351"/>'))
        self.assertEqual(parse_feed("WSJ", xml)[0].images, ["https://images.wsj.net/im-925351"])

    def test_같은_URL이_중복되면_한_번만(self):
        xml = _rss(
            _item(
                '<media:content url="https://img.example.com/x.jpg"/>'
                '<media:thumbnail url="https://img.example.com/x.jpg"/>'
            )
        )
        self.assertEqual(parse_feed("연합뉴스", xml)[0].images, ["https://img.example.com/x.jpg"])

    def test_Atom_항목도_이미지를_모은다(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
  <entry>
    <title>제목</title>
    <link rel="alternate" href="https://example.com/a"/>
    <published>2026-07-16T10:00:00+09:00</published>
    <media:content url="https://img.example.com/atom.jpg"/>
  </entry>
</feed>""".encode("utf-8")
        self.assertEqual(parse_feed("The Verge", xml)[0].images, ["https://img.example.com/atom.jpg"])


if __name__ == "__main__":
    unittest.main()

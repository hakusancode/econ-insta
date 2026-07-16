# 4단계 표지 이미지 소싱 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 데일리 브리핑 표지에 기사 사진을 자동으로 채운다 — RSS `media:content`에서 후보를 모아 Claude 비전이 가장 센 컷을 고른다.

**Architecture:** `collector`가 RSS 이미지 URL을 `Article.images`로 나른다 → 신규 `photos.py`가 이슈 단위로 후보를 병합·필터하고 Claude 비전으로 한 장 고른다 → `backgrounds.build_background()`가 이 경로를 1순위로 삼고 실패 시 기존 체인(인물→위키미디어→Unsplash)으로 저하한다 → `renderer.render()`가 배경을 표지로 넘긴다.

**Tech Stack:** Python 3.13, Pillow, requests, anthropic SDK(`claude-sonnet-5`, 구조화 출력), 표준 `unittest`.

**스펙:** `docs/superpowers/specs/2026-07-16-image-sourcing-design.md` (커밋 04cff57)
**브랜치:** `card-redesign` (1·2·3단계a 위에 얹음. main 미병합 상태 유지)

## Global Constraints

- **테스트 러너는 pytest가 아니라 표준 `unittest`**: `python -m unittest discover -s tests -q`. pytest 미설치.
- **테스트는 네트워크를 타지 않는다.** 모든 HTTP·Claude 호출은 가짜 객체를 주입해 검증한다.
- **콘솔이 cp949다.** 파이썬 실행 시 `PYTHONIOENCODING=utf-8`를 붙인다. 안 붙이면 한글 출력이 `UnicodeEncodeError`로 죽는다.
- **회귀 기준선: 기존 248개 테스트 전부 통과.** 매 태스크 끝에서 전체를 돌린다.
- **배경 실패는 절대 발행을 막지 않는다.** 모든 실패를 삼켜 `errors`에 남기고 다음 체인으로 간다.
- **뉴스 사진에는 크레딧을 달지 않는다**(`credits=()`). **단 위키미디어·인물 라이브러리 경로의 CC BY 크레딧은 손대지 않는다** — 라이선스 조건이라 빼면 실제 위반이다.
- **기사 페이지를 가져오지 않는다.** WSJ·Economist는 403이다(실측). RSS 태그만 쓴다.
- 주석·테스트명·커밋 메시지는 한국어. 기존 코드 스타일을 따른다.
- 커밋 메시지 말미에 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## File Structure

| 파일 | 책임 | 상태 |
|---|---|---|
| `econ_insta/collector.py` | RSS/Atom → `Article`. **이미지 URL을 나르기만** 한다(판정 없음). | 수정 |
| `econ_insta/photos.py` | **이슈에서 최고의 사진 한 장 고르기.** 후보 병합 → 기계 필터 → Claude 판정. | **신규** |
| `econ_insta/backgrounds.py` | **체인 돌려서 배경 하나 내놓기.** 소스별 fetch는 그대로. | 수정 |
| `econ_insta/renderer.py` | 카드 렌더. `render()`에 배경 통로만 뚫는다. | 수정 |
| `tests/test_collector_images.py` | `Article.images` 추출 | **신규** |
| `tests/test_photos.py` | 병합·필터·Claude 판정 | **신규** |
| `tests/test_backgrounds.py` | 체인 폴백 (기존 파일, 콜라주 테스트 대체) | 수정 |
| `tests/test_renderer.py` | `render(background=...)` (기존 파일) | 수정 |

**`photos.py`를 새로 만드는 이유:** `backgrounds.py`가 이미 262줄이고 소스별 fetch를 다 이고 있다. 후보 수집·필터·Claude 판정을 얹으면 400줄을 넘고 책임이 뒤섞인다.

**순환 import 주의:** `photos.py`는 `backgrounds`를 import하지 **않는다**. `photos.pick()`은 `Background`가 아니라 **`Image.Image | None`을 반환**하고, `backgrounds.py`가 그것을 `cover_crop`해서 `Background`로 감싼다. 반대로 하면 순환한다.

---

### Task 1: `Article.images` — RSS 이미지 URL 나르기

**Files:**
- Modify: `econ_insta/collector.py` (`Article` 103~111행, `parse_feed` 262~304행)
- Test: `tests/test_collector_images.py` (신규)

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces: `Article.images: list[str]` — RSS 항목에 실린 이미지 URL, 등장 순서. 이미지가 없으면 빈 리스트. Task 2가 이걸 읽는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_collector_images.py` 신규:

```python
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
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
  <entry>
    <title>제목</title>
    <link rel="alternate" href="https://example.com/a"/>
    <published>2026-07-16T10:00:00+09:00</published>
    <media:content url="https://img.example.com/atom.jpg"/>
  </entry>
</feed>"""
        self.assertEqual(parse_feed("The Verge", xml)[0].images, ["https://img.example.com/atom.jpg"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_collector_images -v`
Expected: FAIL — `AttributeError: 'Article' object has no attribute 'images'`

- [ ] **Step 3: 최소 구현**

`econ_insta/collector.py` — import에 `field`가 없으면 추가:

```python
from dataclasses import dataclass, field
```

`Article`에 필드 추가(103~111행). **기본값이 있는 필드라 기존 `Article(...)` 호출부는 전부 그대로 돌아간다**:

```python
@dataclass
class Article:
    source: str
    title: str
    link: str
    published: datetime
    """항상 tz-aware(KST)."""
    summary: str = ""
    language: str = "ko"
    """en이면 요약 단계에서 한국어로 옮겨야 한다."""
    images: list[str] = field(default_factory=list)
    """항목에 실린 이미지 URL(등장 순서). 표지 후보의 원천."""
```

`_text()` 아래(226행 근처)에 추출 함수 추가:

```python
IMAGE_TAGS = {"content", "thumbnail", "enclosure"}


def _images(item: ET.Element) -> list[str]:
    """항목에 직접 실린 이미지 URL.

    **기사 페이지는 가져오지 않는다.** WSJ·Economist는 페이지가 403이고(실측),
    RSS 태그만으로 연합·매경·WSJ이 덮인다. 페이지를 안 가면 빠르고 봇 차단도 없다.

    네임스페이스가 붙으므로 태그 로컬명으로 비교한다. type이 있으면 믿고,
    없으면 받아들인다 — WSJ media:content는 확장자도 type도 없다(im-925351).
    """
    urls: list[str] = []
    for element in item.iter():
        if element.tag.split("}")[-1] not in IMAGE_TAGS:
            continue
        url = (element.get("url") or "").strip()
        if not url:
            continue
        mime = (element.get("type") or "").lower()
        if mime and not mime.startswith("image/"):
            continue
        if url not in urls:
            urls.append(url)
    return urls
```

`parse_feed`의 RSS 생성부(272~281행)에 인자 추가:

```python
        articles.append(
            Article(
                source=source,
                title=title,
                link=link,
                published=published,
                summary=clean_text(_text(item, "description"))[:300],
                language=language,
                images=_images(item),
            )
        )
```

Atom 생성부(295~304행)에도 같이:

```python
        articles.append(
            Article(
                source=source,
                title=title,
                link=link,
                published=published,
                summary=clean_text(summary)[:300],
                language=language,
                images=_images(entry),
            )
        )
```

- [ ] **Step 4: 통과 확인**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_collector_images -v`
Expected: PASS (8개)

- [ ] **Step 5: 전체 회귀**

Run: `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q`
Expected: OK — 256개(기존 248 + 신규 8). 실패 0.

- [ ] **Step 6: 커밋**

```bash
git add econ_insta/collector.py tests/test_collector_images.py
git commit -m "$(cat <<'EOF'
collector: Article.images 추가(RSS media:content·enclosure)

기사 페이지는 안 간다 — WSJ·Economist는 403이고 RSS 태그만으로 연합·매경·WSJ이
덮인다(실측). type이 있으면 믿고 없으면 받아들인다: WSJ media:content는 확장자도
type도 없어서 확장자로 판정하면 WSJ 사진이 통째로 날아간다.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `photos.py` — 후보 병합과 크로스소스 빈도

**Files:**
- Create: `econ_insta/photos.py`
- Test: `tests/test_photos.py` (신규)

**Interfaces:**
- Consumes: `Article.images: list[str]` (Task 1), `issues.Issue` (3단계a 기존: `Issue.articles: list[Article]`, `Issue.keywords: set[str]`)
- Produces:
  - `Candidate` — `@dataclass(frozen=True)`, 필드 `url: str`, `sources: frozenset[str]`, `freq: int`
  - `candidates(issue: Issue) -> list[Candidate]`
  - `_photo_key(url: str) -> str`

  Task 3이 `candidates()`의 반환을 받아 필터한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_photos.py` 신규:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_photos -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'econ_insta.photos'`

- [ ] **Step 3: 최소 구현**

`econ_insta/photos.py` 신규:

```python
"""이슈의 기사 사진에서 표지 후보 한 장을 고른다.

RSS `media:content`가 표지의 1순위 소스다. og:image보다 나은 이유는 실측에 있다:
WSJ은 기사 페이지가 403인데 RSS엔 이미지가 그대로 있고, 페이지를 안 가도 되니
빠르고 봇 차단도 없다.

**후보의 관련성은 검색 랭킹이 아니라 편집자가 보장한다** — 그 이슈를 다룬 기사에
매체가 직접 붙인 사진이기 때문이다. 그래서 위키미디어 검색 1등이 팬아트 선화였던
사고가 여기서는 구조적으로 재발하지 않는다. Claude는 신원 확인을 하지 않고
'이미 관련 있는 N장 중 가장 센 컷'만 고른다.

`backgrounds`를 import하지 않는다(순환). 고른 사진을 `Image`로 돌려주면
`backgrounds`가 crop해서 `Background`로 감싼다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .issues import Issue

YNA_PHOTO_ID = re.compile(r"(P[A-Z]{2}\d{10,})")


@dataclass(frozen=True)
class Candidate:
    url: str
    sources: frozenset[str]
    """이 사진을 실은 매체들."""
    freq: int
    """등장 횟수. 크로스소스 빈도 신호."""


def _photo_key(url: str) -> str:
    """같은 사진을 매체 건너 묶는 키.

    매경은 연합 사진을 `rcv.YNA.20260716.PYH2026071617330001300_R.jpg`로 받아쓴다 —
    파일명에 연합 사진 ID가 그대로 박혀 있고, 연합 원본은 같은 ID의 `_P2.jpg`다(실측).
    ID로 묶으면 '여러 매체가 같은 사진을 골랐다'가 잡힌다.

    ID가 없으면 URL 자체를 키로 써서 병합하지 않는다 — 모르는 형식을 억지로 묶으면
    다른 사진이 한 장으로 뭉개진다.
    """
    match = YNA_PHOTO_ID.search(url)
    return match.group(1) if match else url


def candidates(issue: Issue) -> list[Candidate]:
    """이슈의 기사들에서 사진 후보를 모으고 같은 사진을 병합한다.

    등장 순서를 유지한다(dict 삽입 순서) — 테스트가 결정적이어야 한다.
    """
    slots: dict[str, dict] = {}
    for article in issue.articles:
        for url in article.images:
            key = _photo_key(url)
            slot = slots.setdefault(key, {"url": url, "sources": set(), "freq": 0})
            slot["sources"].add(article.source)
            slot["freq"] += 1
    return [
        Candidate(url=s["url"], sources=frozenset(s["sources"]), freq=s["freq"])
        for s in slots.values()
    ]
```

- [ ] **Step 4: 통과 확인**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_photos -v`
Expected: PASS (7개)

- [ ] **Step 5: 전체 회귀**

Run: `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q`
Expected: OK — 263개. 실패 0.

- [ ] **Step 6: 커밋**

```bash
git add econ_insta/photos.py tests/test_photos.py
git commit -m "$(cat <<'EOF'
photos.py: 이슈 기사 사진 후보 병합(크로스소스 빈도)

매경이 연합 사진을 받아쓸 때 파일명에 연합 사진 ID가 박힌다(rcv.YNA...PYH<ID>_R.jpg).
그 ID로 묶으면 여러 매체가 같은 사진을 골랐다는 신호가 잡힌다. ID가 없으면 URL이
키라서 병합하지 않는다 — 모르는 형식을 억지로 묶으면 다른 사진이 뭉개진다.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `photos.py` — 기계 필터(플레이스홀더·해상도·P4 승격)

**Files:**
- Modify: `econ_insta/photos.py`
- Test: `tests/test_photos.py`

**Interfaces:**
- Consumes: `Candidate`, `candidates()` (Task 2)
- Produces:
  - `is_placeholder(url: str) -> bool`
  - `_upgrade_yna(url: str) -> str | None`
  - `usable(cands: list[Candidate], session=None, sleep=time.sleep) -> list[tuple[Candidate, Image.Image]]` — 상위 6장, `(-freq, -넓이)` 순
  - `PhotoError(RuntimeError)`
  - 상수 `MIN_SHORT_EDGE = 640`, `MAX_CANDIDATES = 6`, `MAX_DOWNLOAD = 10`

  Task 4가 `usable()`의 반환을 Claude에 넘긴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_photos.py`의 import 블록을 아래로 교체:

```python
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
    usable,
)
```

파일 끝의 `if __name__` 앞에 추가:

```python
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
        session = FakeSession({url: FakeResponse(b"이건 JPEG가 아니다")})
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
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_photos -v`
Expected: FAIL — `ImportError: cannot import name 'is_placeholder' from 'econ_insta.photos'`

- [ ] **Step 3: 최소 구현**

`econ_insta/photos.py` — import 블록을 교체:

```python
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from io import BytesIO

import requests
from PIL import Image, UnidentifiedImageError

from .issues import Issue
```

파일 끝에 추가:

```python
TIMEOUT = 30
RETRIES = 3
MAX_WAIT_SECONDS = 60
USER_AGENT = "econ-insta/0.1 (github.com/hakusancode/econ-insta)"

MIN_SHORT_EDGE = 640
"""1080×1350 표지라 짧은 변이 이보다 작으면 확대할 때 뭉갠다."""
MAX_CANDIDATES = 6
"""Claude에 넘길 상한. 비용 상한이다."""
MAX_DOWNLOAD = 10
"""정렬을 위해 받아볼 상한. 6장을 채우려다 무한정 받지 않는다."""

PLACEHOLDER_PATTERNS = (
    "static.hankyung.com/img/logo/",
    "static.mk.co.kr/facebook_",
    "/logo/",
    "_sns.png",
    "facebook_",
)


class PhotoError(RuntimeError):
    """사진 후보 준비 실패."""


def is_placeholder(url: str) -> bool:
    """매체가 이미지 없는 기사에 붙이는 자사 로고·SNS 기본 이미지인가.

    한경 og:image는 전부 `static.hankyung.com/img/logo/logo-news-sns.png`이고
    매경은 이미지가 없으면 `static.mk.co.kr/facebook_mknews.jpg`를 준다(실측).
    **URL이 있다고 다 사진이 아니다** — 안 거르면 표지에 한국경제 로고가
    대문짝만하게 나간다.
    """
    low = url.lower()
    return any(pattern in low for pattern in PLACEHOLDER_PATTERNS)


def _upgrade_yna(url: str) -> str | None:
    """연합 사진의 더 큰 버전 URL. 해당 없으면 None.

    RSS는 `_P2`(작은 것), og:image는 같은 사진의 `_P4`(큰 것)를 준다(실측).
    **URL 문자열 치환으로 만든다 — 기사 페이지를 가져오는 게 아니다.**
    """
    return url.replace("_P2.", "_P4.") if "_P2." in url else None


def _retry_after(response) -> float:
    try:
        return float(response.headers.get("Retry-After", "0"))
    except (TypeError, ValueError):
        return 0.0


def _get(url: str, caller, sleep) -> bytes | None:
    """바이트를 받아온다. 실패는 None — 후보 하나가 죽어도 발행은 계속된다.

    이미지 CDN은 연속 요청에 429 + Retry-After를 준다(위키미디어에서 실제로 맞았다).
    짧은 대기면 물러섰다 다시 치고, 긴 차단이면 기다리지 않고 포기한다.
    """
    for _ in range(RETRIES):
        try:
            response = caller.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if response.status_code == 429:
            wait = _retry_after(response)
            if wait <= 0 or wait > MAX_WAIT_SECONDS:
                return None
            sleep(wait)
            continue
        if response.status_code != 200:
            return None
        return response.content
    return None


def _open(data: bytes) -> Image.Image | None:
    try:
        image = Image.open(BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    return image.convert("RGB")


def _download_best(candidate: Candidate, caller, sleep) -> Image.Image | None:
    """더 큰 버전을 먼저 시도하고 없으면 원본으로 내려간다."""
    for url in (_upgrade_yna(candidate.url), candidate.url):
        if not url:
            continue
        data = _get(url, caller, sleep)
        if data is None:
            continue
        image = _open(data)
        if image is not None:
            return image
    return None


def usable(
    cands: list[Candidate],
    session: requests.Session | None = None,
    sleep=time.sleep,
) -> list[tuple[Candidate, Image.Image]]:
    """플레이스홀더·저해상도를 걸러 상위 MAX_CANDIDATES장을 (빈도, 크기) 순으로.

    **여기서 품질 판정을 하지 않는다** — 확실한 쓰레기만 제거한다. 사옥 사진인지
    인물 사진인지는 URL로 알 수 없다. 그건 Claude가 본다(`pick`).
    """
    caller = session or requests.Session()
    fetched: list[tuple[Candidate, Image.Image]] = []
    for candidate in sorted(cands, key=lambda c: (-c.freq, c.url)):
        if len(fetched) >= MAX_DOWNLOAD:
            break
        if is_placeholder(candidate.url):
            continue
        image = _download_best(candidate, caller, sleep)
        if image is None or min(image.size) < MIN_SHORT_EDGE:
            continue
        fetched.append((candidate, image))
    fetched.sort(key=lambda pair: (-pair[0].freq, -(pair[1].width * pair[1].height)))
    return fetched[:MAX_CANDIDATES]
```

- [ ] **Step 4: 통과 확인**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_photos -v`
Expected: PASS (21개 = Task 2의 7 + 신규 14)

- [ ] **Step 5: 전체 회귀**

Run: `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q`
Expected: OK — 277개. 실패 0.

- [ ] **Step 6: 커밋**

```bash
git add econ_insta/photos.py tests/test_photos.py
git commit -m "$(cat <<'EOF'
photos: 기계 필터(플레이스홀더·해상도) + 연합 P4 승격

한경 og:image는 전부 자사 로고, 매경도 이미지 없으면 facebook 기본 이미지를 준다
(실측). URL이 있다고 다 사진이 아니다 — 안 거르면 표지에 한경 로고가 박힌다.
연합 _P2는 _P4가 더 크다: URL 치환으로 시도하고 404면 원본으로 내려간다.

여기서 품질 판정은 안 한다. 사옥 사진인지 인물 사진인지는 URL로 알 수 없고
그건 Claude가 본다.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `photos.py` — Claude 비전 선택

**Files:**
- Modify: `econ_insta/photos.py`
- Test: `tests/test_photos.py`

**Interfaces:**
- Consumes: `usable()` (Task 3)
- Produces: `pick(issue: Issue, headline: str, client=None, session=None, model: str = MODEL, sleep=time.sleep) -> Image.Image | None` — 고른 원본 크기 `Image`(크롭 안 함). 쓸 게 없거나 API가 죽으면 `None`.

  Task 5가 이걸 부르고 `cover_crop`한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_photos.py` import에 `pick` 추가(`from econ_insta.photos import (...)` 목록에 `pick,`), 파일 끝(`if __name__` 앞)에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_photos -v`
Expected: FAIL — `ImportError: cannot import name 'pick' from 'econ_insta.photos'`

- [ ] **Step 3: 최소 구현**

`econ_insta/photos.py` — import에 추가:

```python
import base64
import json

import anthropic
```

파일 끝에 추가:

```python
MODEL = "claude-sonnet-5"
MAX_TOKENS = 1024
EFFORT = "medium"
SEND_MAX_EDGE = 512
"""모델에 보낼 때 줄이는 긴 변. 판정에 이 이상은 필요 없고 비용만 는다."""

PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "pick": {"type": ["integer", "null"], "description": "고른 후보 번호. 없으면 null."},
        "reason": {"type": "string", "description": "한 문장."},
    },
    "required": ["pick", "reason"],
    "additionalProperties": False,
}

SYSTEM = """당신은 인스타그램 경제 카드뉴스의 표지 사진을 고르는 편집자입니다.
표지는 스크롤을 멈추게 하는 유일한 수단입니다. 정보 전달이 아니라 시선 강탈이 목적입니다.

우선순위 (이목 집중 순):
1. 인물 얼굴 — 감정·표정이 강한 컷(놀람·긴장·환호). 얼굴이 크게 잡힌 것.
2. 알아보는 기업 로고.
3. 극적인 실사 — 긴장·규모·움직임이 있는 장면(거래소 객장·시위·공장 라인).

반드시 배제할 것 (사물 설명 사진):
- 사옥·건물 정면, 간판
- 웨이퍼·부품·제품컷
- 스펙 표나 글자가 크게 박힌 전시 보드·차트 캡처
- 밋밋한 증명사진식 인물

이런 것뿐이면 pick을 null로 두십시오. **억지로 고르지 마십시오.**
표지가 사옥 사진으로 나가느니 그래픽으로 나가는 게 낫습니다."""


def _thumb(image: Image.Image) -> bytes:
    small = image.copy()
    small.thumbnail((SEND_MAX_EDGE, SEND_MAX_EDGE), Image.LANCZOS)
    buffer = BytesIO()
    small.save(buffer, "JPEG", quality=80)
    return buffer.getvalue()


def _blocks(pairs: list[tuple[Candidate, Image.Image]], headline: str) -> list[dict]:
    blocks: list[dict] = [
        {
            "type": "text",
            "text": f"오늘의 이슈: {headline}\n\n이 이슈를 다룬 기사들에 실린 사진 "
            f"{len(pairs)}장입니다. 표지로 쓸 한 장을 고르십시오.",
        }
    ]
    for index, (candidate, image) in enumerate(pairs):
        blocks.append(
            {"type": "text", "text": f"{index}번 (매체: {', '.join(sorted(candidate.sources))})"}
        )
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(_thumb(image)).decode("ascii"),
                },
            }
        )
    return blocks


def pick(
    issue: Issue,
    headline: str,
    client: anthropic.Anthropic | None = None,
    session: requests.Session | None = None,
    model: str = MODEL,
    sleep=time.sleep,
) -> Image.Image | None:
    """이슈의 기사 사진 중 표지로 쓸 한 장. 없거나 실패하면 None.

    **실패 시 기계 필터 1등을 자동 채택하지 않는다.** 자동 1등 채택은 정확히
    팬아트 사고의 경로다(공용 검색 1등이 팬아트 선화였고 그대로 등록됐다).
    신뢰 점수를 매기는 주체가 Claude이므로 Claude가 없으면 점수도 없다.
    API가 죽은 날은 표지가 폴백 체인으로 나간다 — 못생겨질 뿐 사고는 안 난다.
    """
    pairs = usable(candidates(issue), session=session, sleep=sleep)
    if not pairs:
        return None

    caller = client or anthropic.Anthropic()
    try:
        response = caller.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            output_config={
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": PICK_SCHEMA},
            },
            messages=[{"role": "user", "content": _blocks(pairs, headline)}],
        )
    except Exception:  # API 장애·인증·네트워크 — 배경 때문에 발행이 죽으면 안 된다
        return None

    if getattr(response, "stop_reason", "") in ("max_tokens", "refusal"):
        return None

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None

    index = payload.get("pick")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(pairs):
        return None
    return pairs[index][1]
```

> **주의:** `_load_dotenv()`를 여기서 부르지 않는다 — `pick`은 `backgrounds.build_background`가
> 부르고, 그쪽이 이미 `_load_dotenv()`를 탄다(`fetch_unsplash`). 책임을 한 곳에 둔다.

- [ ] **Step 4: 통과 확인**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_photos -v`
Expected: PASS (30개)

- [ ] **Step 5: 전체 회귀**

Run: `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q`
Expected: OK — 286개. 실패 0.

- [ ] **Step 6: 커밋**

```bash
git add econ_insta/photos.py tests/test_photos.py
git commit -m "$(cat <<'EOF'
photos: Claude 비전으로 표지 후보 판정(사물컷 배제)

URL만 봐서는 사옥 사진과 인물 사진을 구분할 수 없다. 후보를 그대로 넘겨 고르게 한다.
관련성은 이미 편집자가 보장한다 — 그 이슈를 다룬 기사에 매체가 붙인 사진이라
팬아트 오식별이 구조적으로 재발하지 않는다. Claude는 신원 확인이 아니라
'이미 관련 있는 N장 중 가장 센 컷'만 고른다.

실패 시 기계 1등을 자동 채택하지 않고 None. 자동 1등 채택이 정확히 팬아트 사고의
경로였다. 점수를 매기는 주체가 Claude이므로 Claude가 없으면 점수도 없다.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `backgrounds.py` — 체인 개편과 콜라주 폐기

**Files:**
- Modify: `econ_insta/backgrounds.py` (모듈 docstring 1~17행, `compose_people` 80~111행, `build_background` 208~238행)
- Test: `tests/test_backgrounds.py` (`ComposePeopleTest` 43~66행 대체, 체인 테스트 추가)

**Interfaces:**
- Consumes: `photos.pick(issue, headline, client=None, session=None) -> Image.Image | None` (Task 4)
- Produces: `build_background(people, bg_query, session=None, errors=None, *, issue=None, headline="", client=None) -> Background | None`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backgrounds.py`의 `ComposePeopleTest`(43~66행)를 통째로 교체:

```python
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
```

파일 끝에 추가:

```python
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
```

> **구현자 주의:** 이 파일에 `fetch_wikimedia` import가 없으면 상단 import 목록에 추가하십시오.

- [ ] **Step 2: 실패 확인**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_backgrounds -v`
Expected: FAIL — `test_두_명이면_첫_번째만_쓴다`가 콜라주라 다르고, `build_background()`에 `issue` 키워드가 없어 `TypeError`.

- [ ] **Step 3: 최소 구현**

`econ_insta/backgrounds.py` 모듈 docstring(1~17행)을 교체:

```python
"""표지 배경 이미지: 기사 사진 → 인물 라이브러리 → 위키미디어 공용 → Unsplash 순.

**1순위는 이슈 기사에 실린 사진이다**(`photos.py`). 그 이슈를 다룬 기사에 매체가
직접 붙인 사진이라 관련성이 편집자에 의해 보장되고, Claude 비전이 사물컷을 걸러
가장 센 컷을 고른다.

인물 라이브러리(assets/people/)는 **폴백**이다 — 기사에 사진이 없는 이슈(한경 단독
등)의 안전망. 확대하지 않는다: 현직 인물은 이제 기사 사진이 덮는다.
좌우 나란히 콜라주는 폐기했다(뉴스 썸네일처럼 보여 표지가 싸구려가 된다).

위키미디어가 Unsplash보다 먼저다. **Unsplash는 인물 커버리지가 0이고**(스톡 사진이라
유명인이 없다) 늘어나는 건 주제 배경의 미적 품질뿐이다 — 인물·로고는 공용에서만 온다.

전부 실패하면 None으로 폴백해 표지가 그래픽으로 나간다 — 배경 때문에 발행이 죽으면 안 된다.

**라이선스**: 뉴스 사진에는 크레딧을 달지 않는다(사용자 결정, DMCA 리스크는 사용자 소유).
**단 CC BY 크레딧은 라이선스 자체의 조건이라 유지한다** — 빼면 실제 위반이다.

CLI:
    python -m econ_insta.backgrounds "semiconductor memory chips"   # 검색 시험
"""
```

import 교체:

```python
from . import photos, wikimedia
from .config import PROJECT_ROOT, _load_dotenv
from .issues import Issue
```

`compose_people`(80~111행)을 교체:

```python
def compose_people(keys: list[str]) -> Background:
    """인물 라이브러리의 초상을 표지 배경으로. **첫 번째 인물만 전면 크롭한다.**

    좌우 나란히 콜라주는 폐기했다 — 뉴스 썸네일처럼 보여 표지가 싸구려가 된다.
    2인 이상이면 첫 번째만 쓴다: 이 경로는 기사 사진이 1순위를 가져간 뒤에야 닿는
    폴백의 폴백이라, 거의 안 쓰일 분할 합성 렌더러를 새로 짜는 것은 과잉이다.
    모델이 people을 우선순위 순으로 주므로 첫 번째가 그 이슈의 주인공이다.

    얼굴이 잘리지 않도록 위쪽을 살려(top_bias 0.1) 자른다.
    """
    meta = available_people()
    unknown = [k for k in keys if k not in meta]
    if unknown:
        raise BackgroundError(f"인물 라이브러리에 없는 키: {unknown} (보유: {sorted(meta)})")
    if not keys:
        raise BackgroundError("인물이 없습니다.")

    key = keys[0]
    path = PEOPLE_DIR / meta[key]["file"]
    if not path.exists():
        raise BackgroundError(f"인물 사진 파일이 없습니다: {path}")

    canvas = Image.new("RGB", (WIDTH, HEIGHT))
    canvas.paste(cover_crop(Image.open(path), WIDTH, HEIGHT, top_bias=0.1), (0, 0))
    # 안 쓴 사진의 크레딧을 달면 캡션이 거짓말이 된다.
    return Background(image=canvas, credits=(meta[key]["credit"],))
```

`build_background`(208~238행)를 교체:

```python
def build_background(
    people: list[str],
    bg_query: str,
    session: requests.Session | None = None,
    errors: list[str] | None = None,
    *,
    issue: Issue | None = None,
    headline: str = "",
    client=None,
) -> Background | None:
    """기사 사진 → 인물 → 위키미디어 → Unsplash → None(그래픽 폴백).

    배경은 장식이므로 실패를 삼키고 errors에만 남긴다. 발행을 막지 않는다.

    `issue`는 기존 인자 뒤의 선택 키워드다 — 현 호출부가 전부 `(people, bg_query)`
    위치 인자로 부르고, **AI 브리핑·블로그 요약에는 Issue라는 개념이 없다**
    (이슈 클러스터링은 데일리 경제 브리핑만의 것). None이면 사진 경로를 건너뛴다.
    """
    if issue is not None:
        try:
            photo = photos.pick(issue, headline, client=client, session=session)
            if photo is not None:
                # 뉴스 사진에는 크레딧을 달지 않는다(사용자 결정).
                return Background(image=cover_crop(photo, WIDTH, HEIGHT, top_bias=0.1), credits=())
        except Exception as exc:  # 사진 경로가 터져도 발행은 계속된다
            if errors is not None:
                errors.append(f"기사 사진 실패: {exc}")

    if people:
        try:
            return compose_people(people)
        except BackgroundError as exc:
            if errors is not None:
                errors.append(f"인물 배경 실패: {exc}")

    if not bg_query:
        return None

    for source in (fetch_wikimedia, fetch_unsplash):
        try:
            background = source(bg_query, session=session)
        except BackgroundError as exc:
            if errors is not None:
                errors.append(str(exc))
            continue
        if background is not None:
            return background
    return None
```

- [ ] **Step 4: 통과 확인**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_backgrounds -v`
Expected: PASS

- [ ] **Step 5: 전체 회귀**

Run: `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q`
Expected: OK — 실패 0. **`ai_brief`·`blog_brief`는 `(people, bg_query)`로 부르므로 무수정 통과해야 한다.** 여기서 깨지면 시그니처가 잘못된 것이다.

- [ ] **Step 6: 커밋**

```bash
git add econ_insta/backgrounds.py tests/test_backgrounds.py
git commit -m "$(cat <<'EOF'
backgrounds: 기사 사진 1순위로 체인 개편 + 콜라주 폐기

issue는 기존 인자 뒤의 선택 키워드다 — ai_brief·blog_brief는 (people, bg_query)로
부르고 그쪽엔 Issue 개념이 없다. None이면 사진 경로를 건너뛰어 무수정으로 돈다.

위키미디어를 Unsplash 앞으로: Unsplash는 인물 커버리지가 0이라(실측) 스펙의
인물>로고>실사 우선순위에 비추면 순서가 거꾸로였다.

콜라주 폐기, 2인 이상이면 첫 번째만. 이 경로는 폴백의 폴백이라 분할 합성 렌더러는 과잉.
뉴스 사진 크레딧 없음(사용자 결정). CC BY 크레딧은 라이선스 조건이라 그대로 유지.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `renderer.render()` — 배경 통로

**Files:**
- Modify: `econ_insta/renderer.py` (`render` 798~814행)
- Test: `tests/test_renderer.py`

**Interfaces:**
- Consumes: `render_cover(headline, when, fonts, kicker=..., background=None, theme=..., variant=...)` (2단계 기존)
- Produces: `render(briefing, when, out_dir=None, fonts=None, theme=DEFAULT_THEME, background=None) -> list[Path]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_renderer.py`에 추가:

```python
class RenderBackgroundTest(unittest.TestCase):
    """render()가 표지에 배경을 태우는지.

    render_cover는 2단계부터 사진 경로를 지원했지만 render()가 그 인자를 넘긴 적이
    없다 — 데일리 표지가 여태 늘 그래픽이었던 이유다.
    """

    def setUp(self) -> None:
        self.fonts = StubFonts()
        self.briefing = make_briefing()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_background를_render_cover로_넘긴다(self):
        background = Image.new("RGB", (WIDTH, HEIGHT), (0, 255, 0))
        with mock.patch("econ_insta.renderer.render_cover", wraps=render_cover) as cover:
            render(
                self.briefing, WHEN, out_dir=self.tmp, fonts=self.fonts, background=background
            )
        self.assertIs(cover.call_args.kwargs["background"], background)

    def test_background가_없으면_None으로_넘어간다(self):
        with mock.patch("econ_insta.renderer.render_cover", wraps=render_cover) as cover:
            render(self.briefing, WHEN, out_dir=self.tmp, fonts=self.fonts)
        self.assertIsNone(cover.call_args.kwargs["background"])
```

> **구현자 주의:** `StubFonts`·`make_briefing()`·`WHEN`은 이 파일에 **이미 있는** 헬퍼다
> (42·52행, `RenderTest`가 쓰는 것과 같음). setUp도 `RenderTest`(170~176행)와 같은 꼴이다.
> 새로 만들지 마십시오. `mock`·`tempfile`·`Path`·`Image`는 이미 import돼 있다(9~16행).
> `render_cover`·`WIDTH`·`HEIGHT`가 19행 import 목록에 없으면 추가하십시오.

- [ ] **Step 2: 실패 확인**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_renderer -v`
Expected: FAIL — `TypeError: render() got an unexpected keyword argument 'background'`

- [ ] **Step 3: 최소 구현**

`econ_insta/renderer.py` `render`(798~805행) 시그니처와 docstring을 교체:

```python
def render(
    briefing: Briefing,
    when: datetime,
    out_dir: Path | None = None,
    fonts: FontSet | None = None,
    theme: Theme = DEFAULT_THEME,
    background: Image.Image | None = None,
) -> list[Path]:
    """카드 이미지를 순서대로 저장하고 경로 목록을 반환한다.

    `background`가 있으면 표지가 사진 경로로 간다. None이면 그래픽 표지.
    배경 조달은 `backgrounds.build_background()`의 몫이고 여기서는 받아 넘기기만 한다.
    """
```

814행 교체:

```python
    images = [render_cover(briefing.headline, when, fonts, theme=theme, background=background)]
```

- [ ] **Step 4: 통과 확인**

Run: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_renderer -v`
Expected: PASS

- [ ] **Step 5: 전체 회귀**

Run: `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q`
Expected: OK — 실패 0.

- [ ] **Step 6: 커밋**

```bash
git add econ_insta/renderer.py tests/test_renderer.py
git commit -m "$(cat <<'EOF'
renderer: render()에 background 통로 추가

render_cover는 2단계부터 사진 경로를 지원했지만 render()가 그 인자를 넘긴 적이 없다.
데일리 표지가 여태 늘 그래픽이었던 이유이자, 스펙이 표지를 "최대 성장 레버"로 부른 이유다.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 완료 후

- [ ] **전체 브랜치 리뷰** (1·2단계와 같은 전략 — 태스크별 리뷰는 생략하고 마지막에 모아서)
- [ ] `.superpowers/sdd/progress.md`에 4단계 결과 기록
- [ ] **실물 확인**: 실제 이슈로 표지를 렌더해 눈으로 본다. 여기서 후속 두 개가 결정된다 —
      얼굴이 잘리는가(→ OpenCV), 로고 구멍이 남는가(→ 로고 소싱).
      메모리에 반복된 교훈: 실데이터로만 드러나는 결함이 있다(지표 8건 잘림, 환율 방향 뒤집힘).

## 범위 밖 (하지 말 것)

- **얼굴 검출(OpenCV)** — 실물 보고 판단하기로 했다.
- **기업 로고 전용 소싱** — 별도 하위 프로젝트.
- **웹 이미지 검색 API** — 구멍이 얼마나 남는지 본 뒤 판단.
- **인물 라이브러리 확대** — 기사 사진이 현직 인물을 덮는다.
- **`render_article` 제거** — **살아 있는 코드다.** `blog_brief.py:35·149`가 쓴다.
  3단계a 원장이 "죽은 코드"라고 적었지만 틀렸다. 지우면 `blog_brief`가 깨진다.
- **데일리 파이프라인 모듈화** — `render()`를 부르는 프로덕션 코드가 없다는 건 알지만
  4단계 범위가 아니다(별도 남은 일).

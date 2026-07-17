# 데일리 브리핑 크론 자동화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 오전(KST 07:30) 해외·저녁(19:00) 국내 데일리 브리핑을 GitHub Actions cron으로 완전 자동 발행한다.

**Architecture:** `collector.FEEDS`를 `KR_FEEDS`/`GLOBAL_FEEDS`로 쪼개고(합집합 불변) `collect()`에 `feeds` 파라미터를 뚫는다. 신규 `econ_insta/daily.py`가 발행 진입점 — `--render`(수집→요약→배경→렌더→캡션)와 `--publish`(호스팅 확인→재시도 발행)를 CLI로 제공하고, 데일리 전용 `build_caption`이 credits 배선(CC BY)과 복합 출처 dedup을 담당한다. 워크플로 yaml이 cron 2개로 에디션을 판별해 render→git push→publish를 잇는다.

**Tech Stack:** Python 3.13, 표준 `unittest`, requests, GitHub Actions.

**스펙:** `docs/superpowers/specs/2026-07-17-daily-cron-design.md` (커밋 `a442c60`)

## Global Constraints

- **테스트 러너는 pytest가 아니라 표준 `unittest`다.** 전체 실행: `python -m unittest discover -s tests -q`
- **콘솔이 cp949라 한글 출력이 죽는다.** 파이썬 실행 시 `PYTHONIOENCODING=utf-8` 필수.
- 시작 시점: 브랜치 `main`(`a442c60`), 전체 **325개** 통과. 새 브랜치 `daily-cron`에서 작업.
- **`FEEDS`의 내용(url·quota·언어·창)은 한 글자도 바뀌면 안 된다** — 분할·재조립만.
- **날짜는 반드시 `now_kst()`** — CI는 UTC. 오전 실행(22:30 UTC)이 전날 날짜를 잡으면 안 된다.
- **credits 배선은 이 계획의 필수 산출물** — `Background.credits` → 캡션 `📷` 줄. 빠지면 CC BY 위반.
- **공허한 테스트 금지.** 각 뮤테이션을 실제 적용→FAIL 재현→복원. 이 저장소에서 공허/불발 테스트가 5번 나왔고 전부 재현으로만 잡혔다.
- kicker(표지 라벨)는 나누지 않는다 — `renderer.render()` 무변경(사용자 결정).

---

## File Structure

| 파일 | 책임 | 변경 |
|---|---|---|
| `econ_insta/collector.py` | 피드 정의·수집 | `KR_FEEDS`/`GLOBAL_FEEDS` 분할, `collect(feeds=)` |
| `econ_insta/daily.py` | **신규** — 데일리 발행 진입점 | Edition·output_dir·build_caption·render/publish·CLI |
| `.github/workflows/daily-briefing.yml` | **신규** — cron | 에디션 판별, render→host→publish |
| `tests/test_collector.py` | | 분할·feeds 파라미터 테스트 |
| `tests/test_daily.py` | **신규** | 캡션·경로·재시도 테스트 |

`summarizer.py`·`renderer.py`·`backgrounds.py`·`ai_brief.py`는 건드리지 않는다.

---

### Task 1: collector 피드 분할 + `collect(feeds=)`

**Files:**
- Modify: `econ_insta/collector.py` (FEEDS 정의 52-69행 부근, `collect()` 474행 부근)
- Test: `tests/test_collector.py`

**Interfaces:**
- Produces: `KR_FEEDS: dict[str, FeedSpec]`(연합뉴스·한국경제·매일경제), `GLOBAL_FEEDS: dict[str, FeedSpec]`(WSJ·The Economist), `FEEDS = {**KR_FEEDS, **GLOBAL_FEEDS}`, `collect(feeds: dict[str, FeedSpec] | None = None) -> DailyBrief`. Task 2·3이 셋 다 쓴다.

- [ ] **Step 1: 실패하는 테스트** — `tests/test_collector.py`에 추가. import에 `GLOBAL_FEEDS, KR_FEEDS` 추가(알파벳 순서: `FeedSpec` 다음).

```python
class FeedSplitTest(unittest.TestCase):
    def test_분할_합집합이_FEEDS와_같고_교집합이_없다(self):
        """에디션 분할이 피드를 잃거나 겹치면 안 된다. FEEDS 내용 불변이 계약."""
        from econ_insta.collector import FEEDS
        self.assertEqual({**KR_FEEDS, **GLOBAL_FEEDS}, FEEDS)
        self.assertEqual(KR_FEEDS.keys() & GLOBAL_FEEDS.keys(), set())
        self.assertEqual(set(KR_FEEDS), {"연합뉴스", "한국경제", "매일경제"})
        self.assertEqual(set(GLOBAL_FEEDS), {"WSJ", "The Economist"})
```

`CollectArticlesTest` 클래스 안에 추가:

```python
    def test_collect은_받은_feeds만_쓴다(self):
        """에디션 분리의 근간 — feeds를 무시하고 FEEDS 전체를 돌면 해외판에 국내 기사가 섞인다."""
        import econ_insta.collector as mod

        self._patch(FakeSession({"https://a": rss(item(title="해외뉴스", link="https://x/1"))}))
        self.addCleanup(setattr, mod, "collect_quotes", mod.collect_quotes)
        mod.collect_quotes = lambda errors=None: []

        feeds = {"WSJ": FeedSpec("https://a", language="en", max_age_hours=self.FOREVER)}
        brief = mod.collect(feeds=feeds)
        self.assertEqual([a.title for a in brief.articles], ["해외뉴스"])
```

(FakeSession은 목록 밖 URL에 예외를 던지므로, `collect`가 FEEDS 전체를 돌면 실제 5개 피드 URL로 나가 전부 오류가 되고 `brief.articles`가 비어 FAIL한다.)

- [ ] **Step 2: 실패 확인** — `PYTHONIOENCODING=utf-8 python -m unittest tests.test_collector -q` → `ImportError: cannot import name 'KR_FEEDS'`

- [ ] **Step 3: 구현** — `collector.py`의 `FEEDS` 정의를 분할한다. **각 FeedSpec의 내용은 그대로 복사** (연합뉴스 주석 포함):

```python
# 주의: WSJ의 옛 주소(feeds.a.dj.com)는 HTTP 200을 주지만 2025-01에 갱신이 멈춘 죽은 피드다.
# 살아 있는 것은 feeds.content.dowjones.io 쪽이다.
KR_FEEDS: dict[str, FeedSpec] = {
    # 연합뉴스는 한때 제외했으나 사용자 지시로 복귀(2026-07-14). 앞으로 빼지 말 것.
    # 경제 섹션은 economy.xml이고 본문(description)이 온다 — 한경과 달리 카드 소재가 된다.
    "연합뉴스": FeedSpec("https://www.yna.co.kr/rss/economy.xml", quota=4),
    "한국경제": FeedSpec("https://www.hankyung.com/feed/economy"),
    "매일경제": FeedSpec("https://www.mk.co.kr/rss/30100041/"),
}

GLOBAL_FEEDS: dict[str, FeedSpec] = {
    "WSJ": FeedSpec(
        "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
        language="en",
        quota=3,
    ),
    "The Economist": FeedSpec(
        "https://www.economist.com/finance-and-economics/rss.xml",
        language="en",
        max_age_hours=72,
        quota=2,
    ),
}

# 에디션 분할(2026-07-17): 오전 해외판은 GLOBAL_FEEDS만, 저녁 국내판은 KR_FEEDS만 쓴다.
# 합집합은 기존 FEEDS와 동일해야 한다 — 기존 소비자(ai_brief 제외 전부)가 이것을 쓴다.
FEEDS: dict[str, FeedSpec] = {**KR_FEEDS, **GLOBAL_FEEDS}
```

`collect()`에 파라미터를 뚫는다:

```python
def collect(feeds: dict[str, FeedSpec] | None = None) -> DailyBrief:
    """... (기존 docstring 유지) ...

    feeds를 주면 그 피드만 수집한다(에디션 분리 — 오전 해외판/저녁 국내판). None이면 전체.
    """
    errors: list[str] = []
    articles = gather_articles(feeds=feeds, errors=errors)
```

- [ ] **Step 4: 통과 확인** — 같은 명령, OK.
- [ ] **Step 5: 뮤테이션 재현** — (a) `collect()`가 `feeds`를 무시하고 `gather_articles(errors=errors)`로 되돌림 → `test_collect은_받은_feeds만_쓴다` FAIL. (b) `KR_FEEDS`에서 매일경제를 지우고 FEEDS 사전에 직접 추가 → `test_분할_합집합이...` FAIL. 각각 복원.
- [ ] **Step 6: 전체 스위트** — `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q` → OK, 기대 327개(325+2). 회귀 0.
- [ ] **Step 7: 커밋** — `git add econ_insta/collector.py tests/test_collector.py && git commit -m "feat(collector): 피드를 국내/해외 에디션으로 분할한다"`

---

### Task 2: `daily.py` 핵심 단위 — Edition · output_dir · build_caption

**Files:**
- Create: `econ_insta/daily.py`
- Test: `tests/test_daily.py` (신규)

**Interfaces:**
- Consumes: Task 1의 `KR_FEEDS`, `GLOBAL_FEEDS`, `FeedSpec`. `config.PROJECT_ROOT`.
- Produces: `Edition(slug, feeds)`, `EDITIONS: dict[str, Edition]`(키 "kr"·"global"), `output_dir(edition: Edition, when: datetime) -> Path`, `build_caption(headline: str, cards, when: datetime, credits: tuple[str, ...] = ()) -> str`. Task 3이 전부 쓴다. `cards`는 `.title`·`.source`만 읽는다(덕 타이핑 — 테스트는 SimpleNamespace로 충분).

- [ ] **Step 1: 실패하는 테스트** — `tests/test_daily.py` 신규:

```python
"""daily 모듈 테스트. 네트워크·API 불필요 (순수 함수만)."""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from econ_insta.collector import KST
from econ_insta.daily import EDITIONS, build_caption, output_dir


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
```

- [ ] **Step 2: 실패 확인** — `PYTHONIOENCODING=utf-8 python -m unittest tests.test_daily -q` → `ModuleNotFoundError: No module named 'econ_insta.daily'`

- [ ] **Step 3: 구현** — `econ_insta/daily.py` 신규:

```python
"""데일리 브리핑 발행 진입점 — 오전 해외 · 저녁 국내 (스펙 2026-07-17-daily-cron-design.md).

지금까지 데일리 발행은 저장소 밖 스크래치 스크립트로 손 조립했다. 이 모듈이 그 정본이다.
표지 라벨(kicker)은 에디션별로 나누지 않는다 — 구분은 내용(피드)으로만 (사용자 결정 2026-07-17).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .collector import FeedSpec, GLOBAL_FEEDS, KR_FEEDS
from .config import PROJECT_ROOT


@dataclass(frozen=True)
class Edition:
    slug: str
    """출력 디렉터리 접미사. 해외/국내판이 같은 날 다른 디렉터리를 갖게 한다."""
    feeds: dict[str, FeedSpec]


EDITIONS: dict[str, Edition] = {
    "kr": Edition("kr", KR_FEEDS),
    "global": Edition("global", GLOBAL_FEEDS),
}

DISCLAIMER = "※ 정보 제공 목적이며 투자 권유가 아닙니다."
HASHTAGS = "#경제 #경제뉴스 #재테크 #투자 #주식 #경제브리핑"


def output_dir(edition: Edition, when: datetime) -> Path:
    """out/<KST날짜>-<슬러그>. when은 반드시 KST여야 한다 — CI는 UTC라
    오전 실행(22:30 UTC)이 전날 날짜를 잡는 함정이 있다."""
    return PROJECT_ROOT / "out" / f"{when:%Y-%m-%d}-{edition.slug}"


def build_caption(
    headline: str, cards, when: datetime, credits: tuple[str, ...] = ()
) -> str:
    """캡션 조립. cards는 .title·.source만 읽는다.

    복합 출처("매일경제·연합뉴스")는 쪼개서 dedup한다 — 통째로 넣으면
    "매일경제"와 별개 매체로 남는다(2026-07-17 오전 발행분의 실제 사고).
    credits는 CC BY 배경일 때 생략하면 라이선스 위반이다.
    """
    sources = sorted({s.strip() for card in cards for s in card.source.split("·") if s.strip()})
    lines = [headline, "", f"{when:%Y년 %m월 %d일} 경제 브리핑", ""]
    lines += [f"· {card.title} ({card.source})" for card in cards]
    lines += ["", "출처 · " + " · ".join(sources)]
    lines += [f"📷 {credit}" for credit in credits]
    lines += ["", DISCLAIMER, "", HASHTAGS]
    return "\n".join(lines)
```

- [ ] **Step 4: 통과 확인** — OK.
- [ ] **Step 5: 뮤테이션 재현** — (a) `card.source.split("·")`를 `[card.source]`로 → dedup 테스트 FAIL. (b) credits 줄(`lines += [f"📷 ...`)을 제거 → credits 테스트 FAIL. (c) `output_dir`의 f-string에서 `-{edition.slug}` 제거 → 경로 테스트 FAIL. 각각 복원.
- [ ] **Step 6: 전체 스위트** — 기대 333개(327+6).
- [ ] **Step 7: 커밋** — `git add econ_insta/daily.py tests/test_daily.py && git commit -m "feat(daily): 에디션·출력경로·캡션 조립을 만든다"`

---

### Task 3: `daily.py` 흐름 — render · publish(재시도) · CLI

**Files:**
- Modify: `econ_insta/daily.py`
- Test: `tests/test_daily.py`

**Interfaces:**
- Consumes: Task 1 `collect(feeds=)`, Task 2 전부. `summarize`, `build_background`, `renderer.render`, `InstagramClient.publish_images`, `InstagramError`, `now_kst`.
- Produces: `render_edition(edition) -> Path`, `publish_edition(edition, *, sleep=time.sleep) -> int`, `publish_with_retry(publish, *, attempts=6, delay=20.0, sleep=time.sleep)`, `main(argv=None) -> int`. 워크플로(Task 4)가 CLI를 부른다.

- [ ] **Step 1: 실패하는 테스트** — `tests/test_daily.py`에 추가 (import에 `publish_with_retry`, `from econ_insta.ig_client import InstagramError` 추가):

```python
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
```

- [ ] **Step 2: 실패 확인** — `ImportError: cannot import name 'publish_with_retry'`

- [ ] **Step 3: 구현** — `daily.py`에 추가:

```python
import argparse
import sys
import time

import requests

from .backgrounds import build_background
from .collector import collect, now_kst
from .ig_client import InstagramClient, InstagramError
from .summarizer import summarize
from . import renderer

# raw.githubusercontent.com은 push 직후 못 쓴다(실측) — 우리 GET은 200인데 메타 서버가
# 가져갈 땐 아직 CDN에 없어 9004/2207052로 실패한다. 잠시 뒤 재시도하면 그대로 성공한다.
RAW_BASE = "https://raw.githubusercontent.com/hakusancode/econ-insta/main"
PUBLISH_ATTEMPTS = 6
PUBLISH_DELAY_SECONDS = 20.0
RETRYABLE_MARKERS = ("9004", "2207052")


def publish_with_retry(publish, *, attempts: int = PUBLISH_ATTEMPTS,
                       delay: float = PUBLISH_DELAY_SECONDS, sleep=time.sleep):
    """CDN 미전파 오류만 재시도한다. 다른 오류(캡션 한도·토큰 만료)는 기다려도 안 낫는다."""
    for attempt in range(1, attempts + 1):
        try:
            return publish()
        except InstagramError as exc:
            if not any(m in str(exc) for m in RETRYABLE_MARKERS) or attempt == attempts:
                raise
            sleep(delay)


def render_edition(edition: Edition) -> Path:
    brief = collect(feeds=edition.feeds)
    print(f"수집: 기사 {len(brief.articles)}건, 지표 {len(brief.quotes)}건")
    for message in brief.errors:
        print(f"  ! {message}")

    briefing = summarize(brief)
    print(f"훅: {briefing.headline}")

    errors: list[str] = []
    bg = build_background([], briefing.bg_query or "", errors=errors,
                          issue=briefing.issue, headline=briefing.headline)
    for message in errors:
        print(f"  ! 배경: {message}")
    print(f"배경: {'사진' if bg else '그래픽 폴백'}")

    out = output_dir(edition, brief.collected_at)
    renderer.render(briefing, brief.collected_at, out_dir=out,
                    background=bg.image if bg else None)
    caption = build_caption(briefing.headline, briefing.cards, brief.collected_at,
                            bg.credits if bg else ())
    (out / "caption.txt").write_text(caption, encoding="utf-8")
    print(f"렌더 완료 → {out}")
    return out


def publish_edition(edition: Edition, *, sleep=time.sleep) -> int:
    out = output_dir(edition, now_kst())
    caption_path = out / "caption.txt"
    images = sorted(out.glob("[0-9][0-9].jpg"))
    if not caption_path.exists() or not images:
        print(f"카드나 캡션이 없습니다: {out}")
        return 1

    rel = out.relative_to(PROJECT_ROOT).as_posix()
    urls = [f"{RAW_BASE}/{rel}/{path.name}" for path in images]
    for url in urls:
        response = requests.get(url, timeout=20, allow_redirects=False)
        if response.status_code != 200 or response.headers.get("Content-Type") != "image/jpeg":
            print(f"호스팅 확인 실패 ({response.status_code}): {url}")
            return 1

    result = publish_with_retry(
        lambda: InstagramClient().publish_images(
            urls, caption_path.read_text(encoding="utf-8")),
        sleep=sleep,
    )
    print(f"발행 완료: media_id={result.media_id}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="데일리 브리핑 렌더·발행")
    parser.add_argument("--edition", choices=sorted(EDITIONS), required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--render", action="store_true")
    group.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)

    edition = EDITIONS[args.edition]
    if args.render:
        render_edition(edition)
        return 0
    return publish_edition(edition)


if __name__ == "__main__":
    sys.exit(main())
```

주의 두 가지: ① `PublishResult`에 permalink 계열 필드가 있으면(`ig_client.py`에서 확인) `발행 완료` 뒤에 그 줄도 출력한다 — 기존 발행 출력과 같은 형태. ② import 블록은 파일 상단 기존 import와 합친다(모듈 중간에 import를 두지 않는다).

- [ ] **Step 4: 통과 확인** — OK.
- [ ] **Step 5: 뮤테이션 재현** — (a) `RETRYABLE_MARKERS` 검사를 지워 무조건 재시도 → `test_재시도_불가_오류는_즉시_던진다` FAIL. (b) `attempt == attempts` 탈출을 지움 → `test_횟수를_다_쓰면...` FAIL. 각각 복원.
- [ ] **Step 6: 전체 스위트** — 기대 336개(333+3).
- [ ] **Step 7: 커밋** — `git add econ_insta/daily.py tests/test_daily.py && git commit -m "feat(daily): render/publish 흐름과 CDN 재시도 CLI를 만든다"`

---

### Task 4: 워크플로 + 시크릿 확인

**Files:**
- Create: `.github/workflows/daily-briefing.yml`

**Interfaces:**
- Consumes: Task 3의 CLI (`python -m econ_insta.daily --edition kr|global --render|--publish`)

- [ ] **Step 1: 워크플로 작성**

```yaml
# 데일리 브리핑 자동 발행 (스펙 docs/superpowers/specs/2026-07-17-daily-cron-design.md)
# KST 07:30 = 해외판(GLOBAL_FEEDS), KST 19:00 = 국내판(KR_FEEDS). GitHub cron은 UTC다.
# 실패하면 그날 그 에디션은 건너뛴다 — GitHub이 소유자에게 메일을 보낸다.

name: Daily briefing

on:
  schedule:
    - cron: "30 22 * * *"   # KST 다음날 07:30 — 해외판
    - cron: "0 10 * * *"    # KST 19:00 — 국내판
  workflow_dispatch:
    inputs:
      edition:
        description: "발행 에디션"
        type: choice
        options: [kr, global]
        required: true

permissions:
  contents: write   # 렌더 결과(out/)를 main에 커밋·push한다

concurrency: daily-briefing

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - run: pip install -r requirements.txt

      - name: Determine edition
        id: edition
        run: |
          if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
            echo "edition=${{ inputs.edition }}" >> "$GITHUB_OUTPUT"
          elif [ "${{ github.event.schedule }}" = "30 22 * * *" ]; then
            echo "edition=global" >> "$GITHUB_OUTPUT"
          else
            echo "edition=kr" >> "$GITHUB_OUTPUT"
          fi

      - name: Render
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python -m econ_insta.daily --edition "${{ steps.edition.outputs.edition }}" --render

      # out/은 .gitignore라 -f가 필요하다. 발행이 raw URL을 요구하므로 push가 먼저다.
      - name: Host rendered cards
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add -f out/
          git commit -m "out: daily ${{ steps.edition.outputs.edition }} $(TZ=Asia/Seoul date +%F) [skip ci]"
          git pull --rebase origin main
          git push origin main

      - name: Publish
        env:
          IG_ACCESS_TOKEN: ${{ secrets.IG_ACCESS_TOKEN }}
          IG_USER_ID: ${{ secrets.IG_USER_ID }}
        run: python -m econ_insta.daily --edition "${{ steps.edition.outputs.edition }}" --publish
```

- [ ] **Step 2: 시크릿 실재 확인**

```bash
gh secret list
```

Expected: `IG_ACCESS_TOKEN`·`GH_PAT`(refresh-token.yml의 전제)가 보인다. **`ANTHROPIC_API_KEY`·`IG_USER_ID`가 없으면 멈추고 컨트롤러에 보고** — 값은 사용자만 안다. 워크플로 yaml 자체는 시크릿 없이도 커밋 가능하다(실행이 실패할 뿐).

- [ ] **Step 3: 들여쓰기·따옴표를 눈으로 재확인하고 `git diff --check`** (yaml 린터 미설치 — push 시 GitHub이 검증).
- [ ] **Step 4: 전체 스위트** — 336개 유지(yaml은 테스트 대상 아님).
- [ ] **Step 5: 커밋** — `git add .github/workflows/daily-briefing.yml && git commit -m "ci: 데일리 브리핑 cron 워크플로 (오전 해외·저녁 국내)"`

---

### Task 5: 로컬 실증 — 해외판 첫 렌더 (컨트롤러 수행)

해외 전용 수집은 이번이 **첫 실행**이다. 스펙 §5.2 — 병합 전에 실물을 눈으로 확인한다.

- [ ] **Step 1: 실행** (모델 호출 ~$0.03~0.06 발생)

```bash
cd /c/Users/user/econ-insta && PYTHONIOENCODING=utf-8 python -m econ_insta.daily --edition global --render
```

- [ ] **Step 2: 확인 항목** — ① WSJ·Economist 기사만 수집됐는가(수집량 ~30건대), ② 이슈가 해외 뉴스인가, ③ 카드·캡션 실물 양호, ④ credits 줄(있다면) 정상. 카드를 사용자에게 보여 확인받는다.
- [ ] **Step 3: 결과를 진행 원장에 기록.** 렌더 산출물(`out/<날짜>-global/`)은 발행하지 않을 것이면 커밋하지 않는다.

이후(계획 밖, finishing 단계): main 병합·push → **즉시 workflow_dispatch 수동 1회로 CI 전 경로 검증**(cron이 먼저 밟기 전에) → 다음 날 정기 실행 2회 확인.

---

## Self-Review

**1. 스펙 커버리지**: §3.1→Task 1, §3.2→Task 2·3(credits 배선 = Task 2 캡션 + Task 3 `render_edition` 연결, CDN 재시도 = Task 3), §3.3→Task 4의 Host 스텝, §3.4→Task 4, §4 테스트 표 6행 → Task 1(2) + Task 2(4: dedup·credits·투자유의·slug경로), §5.1~2→Task 1~5, §5.3~5(병합·dispatch·정기 확인)→계획 밖(finishing 단계 + 사용자).
**2. 플레이스홀더**: 없음 — 모든 코드 스텝에 실제 코드.
**3. 타입 일관성**: `Edition`·`EDITIONS`·`output_dir`·`build_caption`은 Task 2 정의 → Task 3·5 동일 시그니처 사용. `collect(feeds=)`는 Task 1 정의 → Task 3 사용. `publish_with_retry`는 Task 3 정의·사용.

**남은 리스크 (구현자가 알아야 할 것)**
- `PublishResult`의 permalink 계열 필드 존재를 계획 작성 시점에 확인 안 함 — Task 3 Step 3 주의 ①이 적응 지침.
- `Briefing.cards[].source`는 스키마가 str을 보장. `bg_query`·`issue`는 None일 수 있고 코드가 처리.
- 워크플로 `git pull --rebase`는 push 경합 대비 — out/ 신규 파일뿐이라 충돌 없음.
- **cron은 병합 순간 활성** — 병합 직후 dispatch 검증 순서를 지킬 것.
- Task 1 테스트의 FakeSession 논거("목록 밖 URL은 예외 → collect가 전체 FEEDS를 돌면 빈 결과")는 `collect`가 `errors` 리스트를 쓰는 경로라 예외가 아니라 errors 축적으로 나타난다 — 어느 쪽이든 `brief.articles`가 "해외뉴스" 1건이 아니게 되므로 단언은 유효하다.

"""데일리 브리핑 발행 진입점 — 오전 해외 · 저녁 국내 (스펙 2026-07-17-daily-cron-design.md).

지금까지 데일리 발행은 저장소 밖 스크래치 스크립트로 손 조립했다. 이 모듈이 그 정본이다.
표지 라벨(kicker)은 에디션별로 나누지 않는다 — 구분은 내용(피드)으로만 (사용자 결정 2026-07-17).
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests

from .backgrounds import build_background
from .collector import FeedSpec, GLOBAL_FEEDS, KR_FEEDS, collect, now_kst
from .config import PROJECT_ROOT
from .ig_client import InstagramClient, InstagramError
from .summarizer import summarize
from . import renderer


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
    print(f"  {result.permalink}")
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

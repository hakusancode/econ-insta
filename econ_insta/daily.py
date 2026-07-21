"""데일리 브리핑 발행 진입점 — 오전 해외 · 저녁 국내 (스펙 2026-07-17-daily-cron-design.md).

지금까지 데일리 발행은 저장소 밖 스크래치 스크립트로 손 조립했다. 이 모듈이 그 정본이다.
표지 라벨(kicker)은 에디션별로 나누지 않는다 — 구분은 내용(피드)으로만 (사용자 결정 2026-07-17).
"""

from __future__ import annotations

import argparse
import hashlib
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
from .issues import rank_issues
from .naver import rerank as naver_rerank
from .summarizer import PROMPT_ISSUES, summarize
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


def hosting_ready(urls, *, checksums: dict[str, str] | None = None,
                  attempts: int = PUBLISH_ATTEMPTS,
                  delay: float = PUBLISH_DELAY_SECONDS,
                  sleep=time.sleep, get=requests.get) -> bool:
    """raw CDN이 모든 이미지를 온전한 바이트로 줄 때까지 기다린다.

    push 직후에는 CDN 전파가 안 끝나 404·엉뚱한 Content-Type이 온다(실측).
    상태·타입이 멀쩡해도 본문이 잘려 올 수 있다 — 2026-07-17 첫 CI 발행에서 메타가
    절반만 받은 지표 카드를 그대로 게시했다(하단 회색, media_id=18087340157553909).
    checksums(url → 로컬 파일 SHA-256)를 주면 본문 일치까지 확인해야 전파 완료로 본다.
    """
    for attempt in range(1, attempts + 1):
        bad = None
        for url in urls:
            response = get(url, timeout=20, allow_redirects=False)
            if response.status_code != 200 or response.headers.get("Content-Type") != "image/jpeg":
                bad = (url, response.status_code)
                break
            if checksums is not None and hashlib.sha256(response.content).hexdigest() != checksums[url]:
                bad = (url, "본문 불일치")
                break
        if bad is None:
            return True
        print(f"호스팅 미전파 (시도 {attempt}/{attempts}, HTTP {bad[1]}): {bad[0]}")
        if attempt < attempts:
            sleep(delay)
    return False


def render_edition(edition: Edition) -> Path:
    brief = collect(feeds=edition.feeds)
    print(f"수집: 기사 {len(brief.articles)}건, 지표 {len(brief.quotes)}건")
    for message in brief.errors:
        print(f"  ! {message}")

    # 네이버 인기도(검색 트렌드 + 뉴스 커버리지)로 이슈 순서를 재정렬해 넘긴다.
    # 키가 없거나 호출이 실패하면 rerank가 기존 크로스소스 랭킹을 그대로 돌려준다.
    issues = naver_rerank(rank_issues(brief.articles)[:PROMPT_ISSUES])
    briefing = summarize(brief, issues=issues)
    print(f"훅: {briefing.headline}")

    errors: list[str] = []
    bg = build_background([], briefing.bg_query or "", errors=errors,
                          issue=briefing.issue, headline=briefing.headline)
    for message in errors:
        print(f"  ! 배경: {message}")
    print(f"배경: {'사진' if bg else '그래픽 폴백'}")

    out = output_dir(edition, brief.collected_at)
    # 재렌더 전에 이전 렌더의 카드 잔재를 지운다 — 카드 수가 줄면 옛 NN.jpg가 남아
    # 같은 캐러셀에 옛 카드가 섞여 발행된다(2026-07-19 지표 카드 중복 실사고).
    if out.exists():
        for stale in out.glob("[0-9][0-9].jpg"):
            stale.unlink()
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
    checksums = {
        url: hashlib.sha256(path.read_bytes()).hexdigest()
        for url, path in zip(urls, images)
    }
    if not hosting_ready(urls, checksums=checksums, sleep=sleep):
        print("호스팅 확인 실패 — raw CDN이 끝내 전파되지 않았습니다.")
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

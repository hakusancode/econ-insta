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

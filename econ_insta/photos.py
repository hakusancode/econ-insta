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

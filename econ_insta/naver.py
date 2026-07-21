"""네이버 오픈 API 인기도 신호 — 이슈 랭킹 재정렬 (스펙 §4.1의 외부 신호 결합).

두 신호를 쓴다:
- **뉴스 검색**: 이슈 대표 기사 제목으로 검색해 `기사 수 + 매체(도메인) 수`를 잰다.
  우리 RSS 6~7곳 밖의 커버리지까지 포함한 실제 화제성. 하루 25,000콜 중 발행당 ~10콜.
- **데이터랩 검색어 트렌드**: 이슈 클러스터의 최빈 키워드로 최근 대중 검색 관심을 잰다.
  그룹 5개/요청 제한이라 상위 5개 이슈만, 요청 1번에 묶는다. 상대값(0~100)이므로
  같은 요청 안에서만 비교한다.

**어떤 실패도 발행을 막지 않는다** (스펙의 저하 원칙): 키가 없거나 호출이 죽으면
`rerank()`는 받은 순서를 그대로 돌려준다. 네이버는 순서를 바꿀 뿐, 이슈를 만들거나
버리지 않는다.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests

from .config import _load_dotenv
from .issues import Issue, keywords

NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
DATALAB_URL = "https://openapi.naver.com/v1/datalab/search"
TIMEOUT = 15

RERANK_LIMIT = 10   # 뉴스 검색을 붙일 이슈 수 (= summarizer.PROMPT_ISSUES)
DATALAB_LIMIT = 5   # 데이터랩 keywordGroups 상한 (API 제한)
TREND_DAYS = 7

# 검색 API가 제목에 <b> 태그를 섞어 돌려준다. 쿼리는 우리 기사 원제목이라 무관하지만
# 방어적으로 태그를 벗긴다.
_TAG_RE = re.compile(r"<[^>]+>")


class NaverError(RuntimeError):
    """네이버 API 호출 실패. 호출부는 잡아서 기존 랭킹으로 저하한다."""


def _credentials() -> tuple[str, str] | None:
    _load_dotenv()
    client_id = os.environ.get("NAVER_CLIENT_ID")
    secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not client_id or not secret:
        return None
    return client_id, secret


def has_credentials() -> bool:
    return _credentials() is not None


@dataclass(frozen=True)
class NewsSignal:
    total: int
    """네이버 뉴스 검색 총 결과 수."""
    sources: int
    """검색 상위 결과의 서로 다른 매체(originallink 도메인) 수."""


def _headers() -> dict[str, str]:
    creds = _credentials()
    if creds is None:
        raise NaverError("NAVER_CLIENT_ID/NAVER_CLIENT_SECRET 이 없습니다 (.env)")
    return {"X-Naver-Client-Id": creds[0], "X-Naver-Client-Secret": creds[1]}


def news_signal(query: str, session: requests.Session | None = None) -> NewsSignal:
    caller = session or requests.Session()
    try:
        response = caller.get(
            NEWS_URL,
            headers=_headers(),
            params={"query": query, "display": 30, "sort": "sim"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise NaverError(f"뉴스 검색 실패 ({query!r}: {exc})") from exc

    domains = set()
    for item in body.get("items") or []:
        link = item.get("originallink") or item.get("link") or ""
        host = urlparse(link).netloc
        if host:
            domains.add(host.removeprefix("www."))
    return NewsSignal(total=int(body.get("total") or 0), sources=len(domains))


def trend_scores(
    keyword_list: list[str],
    session: requests.Session | None = None,
    now: datetime | None = None,
) -> dict[str, float]:
    """키워드별 최근 2일 평균 검색 비율(0~100, 같은 요청 안에서만 비교 가능)."""
    keyword_list = keyword_list[:DATALAB_LIMIT]
    if not keyword_list:
        return {}
    end = (now or datetime.now()).date()
    start = end - timedelta(days=TREND_DAYS)
    caller = session or requests.Session()
    try:
        response = caller.post(
            DATALAB_URL,
            headers={**_headers(), "Content-Type": "application/json"},
            json={
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "timeUnit": "date",
                "keywordGroups": [
                    {"groupName": kw, "keywords": [kw]} for kw in keyword_list
                ],
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise NaverError(f"데이터랩 실패 ({exc})") from exc

    scores: dict[str, float] = {kw: 0.0 for kw in keyword_list}
    for group in body.get("results") or []:
        points = group.get("data") or []
        recent = [p["ratio"] for p in points[-2:] if "ratio" in p]
        if recent:
            scores[group.get("title", "")] = sum(recent) / len(recent)
    return scores


def issue_query(issue: Issue) -> str:
    """뉴스 검색 쿼리 = 시드(첫) 기사 제목. 클러스터의 대표 사건을 그대로 묻는다."""
    return _TAG_RE.sub("", issue.articles[0].title).strip()


def issue_keyword(issue: Issue) -> str:
    """데이터랩 키워드 = 클러스터 전체 제목에서 가장 자주 나오는 핵심어.

    사람들이 검색창에 칠 법한 낱말이어야 하므로 제목 문장이 아니라 단어를 쓴다.
    """
    counter: Counter[str] = Counter()
    for article in issue.articles:
        counter.update(keywords(article.title))
    if not counter:
        return issue_query(issue)[:20]
    # 최빈 → 긴 단어 우선(동률일 때 "금리"보다 "기준금리"가 검색어답다)
    return max(counter.items(), key=lambda kv: (kv[1], len(kv[0])))[0]


def _popularity(signal: NewsSignal, trend: float) -> float:
    """가중 결합. 매체 수가 주 신호(0~30), 기사 수는 로그(0~7), 트렌드는 보조(0~10)."""
    return signal.sources * 10 + math.log10(signal.total + 1) + trend / 10


def rerank(
    issues: list[Issue],
    session: requests.Session | None = None,
    now: datetime | None = None,
) -> list[Issue]:
    """네이버 신호로 이슈 순서를 다시 세운다. 실패하면 받은 순서 그대로.

    뉴스 검색이 죽으면 전체를 포기하고, 데이터랩만 죽으면 트렌드 0으로 계속한다.
    """
    if not issues or not has_credentials():
        return issues
    head, tail = issues[:RERANK_LIMIT], issues[RERANK_LIMIT:]
    try:
        signals = [news_signal(issue_query(issue), session=session) for issue in head]
    except NaverError as exc:
        print(f"  ! 네이버 뉴스 신호 실패 — 기존 랭킹 유지 ({exc})")
        return issues

    trends: dict[str, float] = {}
    try:
        trends = trend_scores(
            [issue_keyword(issue) for issue in head[:DATALAB_LIMIT]],
            session=session,
            now=now,
        )
    except NaverError as exc:
        print(f"  ! 데이터랩 실패 — 트렌드 없이 계속 ({exc})")

    scored = [
        (_popularity(signal, trends.get(issue_keyword(issue), 0.0)), index, issue)
        for index, (issue, signal) in enumerate(zip(head, signals))
    ]
    # 점수 내림차순, 동점이면 기존 순서(크로스소스 랭킹) 유지.
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [issue for _, _, issue in scored] + tail

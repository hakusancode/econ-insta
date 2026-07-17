"""경제뉴스 및 시장지표 수집.

두 종류를 모은다:
    1) 기사 - 언론사 RSS (연합/한경/매경)
    2) 지표 - yfinance 종가 및 전일 대비 등락률

RSS는 표준 라이브러리로 파싱한다 (feedparser 불필요).
수집 단계는 사실만 모으고, 문장 재작성은 요약 단계에서 한다.
기사 본문을 그대로 카드에 옮기면 저작권 침해이므로 여기서는 제목·요약·출처만 보관한다.

CLI:
    python -m econ_insta.collector
"""

from __future__ import annotations

import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (compatible; econ-insta/0.1)"

KST = timezone(timedelta(hours=9))

@dataclass(frozen=True)
class FeedSpec:
    url: str
    language: str = "ko"
    max_age_hours: float = 24
    """The Economist는 주간지라 24시간 창으로는 0건인 날이 생긴다."""
    quota: int = 5
    """한 매체가 브리핑을 독식하지 않도록 매체별 상한을 둔다."""

    topic: re.Pattern[str] | None = None
    """주제 필터. 지정하면 제목·요약이 이 패턴에 맞는 기사만 남긴다.

    주류 매체에는 AI 전용 섹션이 없다(연합 산업, 한경 IT는 AI가 아닌 기사가 훨씬 많다).
    **쿼터를 적용하기 전에** 걸러야 한다 — 나중에 거르면 쿼터가 비AI 기사로 다 차버려
    AI 기사가 한 건도 안 남는다.
    """


KR_FEEDS: dict[str, FeedSpec] = {
    # 연합뉴스는 한때 제외했으나 사용자 지시로 복귀(2026-07-14). 앞으로 빼지 말 것.
    # 경제 섹션은 economy.xml이고 본문(description)이 온다 — 한경과 달리 카드 소재가 된다.
    "연합뉴스": FeedSpec("https://www.yna.co.kr/rss/economy.xml", quota=4),
    "한국경제": FeedSpec("https://www.hankyung.com/feed/economy"),
    "매일경제": FeedSpec("https://www.mk.co.kr/rss/30100041/"),
}

# 주의: WSJ의 옛 주소(feeds.a.dj.com)는 HTTP 200을 주지만 2025-01에 갱신이 멈춘 죽은 피드다.
# 살아 있는 것은 feeds.content.dowjones.io 쪽이다.
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

# 브리핑에 쓸 수 없는 정형 기사의 말머리. [특징주]·[외환]은 시장 소재이므로 남긴다.
BOILERPLATE_TAGS = frozenset(
    {"인사", "동정", "프로필", "부고", "부음", "게시판", "신간", "알림", "사고", "정정", "고침"}
)
BOILERPLATE_PREFIXES = ("외국환시세",)

TICKERS: dict[str, str] = {
    "^KS11": "코스피",
    "^KQ11": "코스닥",
    "KRW=X": "원/달러",
    "^IXIC": "나스닥",
    "^GSPC": "S&P500",
    "CL=F": "WTI유가",
    "GC=F": "금",
    "BTC-USD": "비트코인",
}

# 등락률 표기 자릿수가 다르다. 환율은 소수 첫째, 지수는 정수, 코인은 정수.
_PRICE_DECIMALS: dict[str, int] = {"KRW=X": 1, "CL=F": 2, "GC=F": 1}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# RFC 2822는 타임존을 '+0900'으로 쓴다. 매경은 '+09:00'을 보내는데
# parsedate_to_datetime()은 예외 대신 조용히 타임존을 버리고 naive를 돌려준다.
_TZ_COLON_RE = re.compile(r"([+-]\d{2}):(\d{2})\s*$")


class CollectError(RuntimeError):
    """수집 실패."""


@dataclass(frozen=True)
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

    @property
    def age(self) -> timedelta:
        return now_kst() - self.published


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    price: float
    change_pct: float
    series: list[float] | None = None
    """스파크라인용 최근 종가 시계열. 수집 실패 시 None(발행을 막지 않는다)."""

    @property
    def price_text(self) -> str:
        decimals = _PRICE_DECIMALS.get(self.symbol, 0)
        return f"{self.price:,.{decimals}f}"

    @property
    def change_text(self) -> str:
        return f"{self.change_pct:+.2f}%"


@dataclass
class DailyBrief:
    collected_at: datetime
    articles: list[Article] = field(default_factory=list)
    quotes: list[Quote] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    """일부 소스가 실패해도 나머지로 발행할 수 있어야 하므로 예외 대신 여기 모은다."""


def now_kst() -> datetime:
    return datetime.now(KST)


def clean_text(raw: str) -> str:
    """HTML 태그·엔티티를 걷어내고 공백을 정규화한다."""
    text = html.unescape(_TAG_RE.sub(" ", raw or ""))
    return _WS_RE.sub(" ", text).strip()


_BYLINE_RE = re.compile(r"^\([가-힣]+=[^)]+\)\s*[가-힣·\s]{2,30}?(기자|특파원)\s*=\s*")


def strip_byline(text: str) -> str:
    """연합뉴스식 바이라인 접두 '(서울=연합뉴스) 김준태 기자 = '를 벗긴다.

    바이라인 토큰({기자, 연합뉴스, 서울, 기자이름})이 keywords()에 들어가면 같은 매체
    기사끼리 주제 무관 병합된다(2026-07-17 실측: 연합 64건 메가클러스터). 지역·기자명은
    가변이라 구조로 잡고, 문두 앵커(^)라 본문 중간 인용은 건드리지 않는다.
    """
    return _BYLINE_RE.sub("", text, count=1)


def parse_pubdate(raw: str) -> datetime:
    """발행일시를 KST tz-aware datetime으로 바꾼다.

    매경의 '+09:00'처럼 콜론이 든 오프셋은 표준 파서가 타임존을 버리므로 먼저 정규화한다.
    타임존이 끝내 없으면 KST로 간주한다 (국내 매체 피드 전제).

    RFC822만 받으면 안 된다: **AI타임스는 `2026-07-14 15:53:25`로 보낸다**(RFC822도 아니고
    타임존도 없다). 이걸 못 읽어 50건을 통째로 버렸다. Atom(`<published>`)은 ISO8601이다.
    """
    raw = (raw or "").strip()
    if not raw:
        raise CollectError("발행일시가 비어 있습니다.")

    normalized = _TZ_COLON_RE.sub(r"\1\2", raw)
    try:
        parsed = parsedate_to_datetime(normalized)
    except (TypeError, ValueError):
        parsed = None

    if parsed is None:
        # ISO8601 (Atom) 또는 'YYYY-MM-DD HH:MM:SS' (AI타임스 등 국내 CMS)
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CollectError(f"발행일시를 해석할 수 없습니다: {raw!r}") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _normalize_title(title: str) -> str:
    """중복 판정용 키. 말머리([특징주] 등)와 기호·공백을 제거한다."""
    text = unicodedata.normalize("NFKC", title)
    text = re.sub(r"^\s*\[[^\]]*\]\s*", "", text)
    text = re.sub(r"[^\w가-힣]+", "", text)
    return text.lower()


def is_boilerplate(title: str) -> bool:
    """브리핑에 쓸 수 없는 정형 기사인가. [인사]·[동정]·외국환시세 표 같은 것들."""
    text = title.strip()
    match = re.match(r"^\s*\[([^\]]*)\]", text)
    if match and match.group(1).strip() in BOILERPLATE_TAGS:
        return True
    return text.startswith(BOILERPLATE_PREFIXES)


def apply_quota(articles: list[Article], feeds: dict[str, FeedSpec]) -> list[Article]:
    """매체별 상한을 적용한다. 입력이 최신순이면 각 매체의 최신 기사가 남는다."""
    counts: dict[str, int] = {}
    kept: list[Article] = []
    for article in articles:
        spec = feeds.get(article.source)
        limit = spec.quota if spec else 0
        if counts.get(article.source, 0) >= limit:
            continue
        counts[article.source] = counts.get(article.source, 0) + 1
        kept.append(article)
    return kept


def _text(item: ET.Element, tag: str) -> str:
    """자식 태그의 전체 텍스트. findtext()는 자식 엘리먼트가 끼면 앞부분만 돌려준다.

    연합·매경은 description을 CDATA로 보내고 한경은 아예 보내지 않지만,
    포맷이 바뀌어 태그가 섞여 들어와도 요약이 조용히 사라지지 않도록 itertext를 쓴다.
    """
    element = item.find(tag)
    return "" if element is None else "".join(element.itertext())


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


ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _atom_entries(root: ET.Element) -> list[ET.Element]:
    return root.findall(f".//{ATOM_NS}entry")


def _atom_link(entry: ET.Element) -> str:
    """Atom의 link는 텍스트가 아니라 href 속성이다. alternate를 우선한다."""
    links = entry.findall(f"{ATOM_NS}link")
    for link in links:
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href", "").strip()
    return links[0].get("href", "").strip() if links else ""


def parse_feed(source: str, xml_bytes: bytes, language: str = "ko") -> list[Article]:
    """RSS/Atom 바이트를 Article 목록으로. 개별 항목 오류와 정형 기사는 건너뛴다.

    **The Verge는 Atom(`<entry>`)이라 `<item>`만 찾으면 0건이 나온다.** 죽은 피드와
    구분이 안 되므로 둘 다 읽는다.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise CollectError(f"{source}: XML 파싱 실패 ({exc})") from exc

    items = root.findall(".//item")
    entries = _atom_entries(root) if not items else []
    total = len(items) + len(entries)

    articles: list[Article] = []
    dropped_date = 0

    for item in items:
        title = clean_text(_text(item, "title"))
        link = _text(item, "link").strip()
        if not title or not link or is_boilerplate(title):
            continue
        try:
            published = parse_pubdate(_text(item, "pubDate"))
        except CollectError:
            dropped_date += 1
            continue
        articles.append(
            Article(
                source=source,
                title=title,
                link=link,
                published=published,
                summary=strip_byline(clean_text(_text(item, "description")))[:300],
                language=language,
                images=_images(item),
            )
        )

    for entry in entries:
        title = clean_text(_text(entry, f"{ATOM_NS}title"))
        link = _atom_link(entry)
        if not title or not link or is_boilerplate(title):
            continue
        raw_date = _text(entry, f"{ATOM_NS}published") or _text(entry, f"{ATOM_NS}updated")
        try:
            published = parse_pubdate(raw_date)
        except CollectError:
            dropped_date += 1
            continue
        summary = _text(entry, f"{ATOM_NS}summary") or _text(entry, f"{ATOM_NS}content")
        articles.append(
            Article(
                source=source,
                title=title,
                link=link,
                published=published,
                summary=strip_byline(clean_text(summary))[:300],
                language=language,
                images=_images(entry),
            )
        )

    # 항목은 있는데 날짜 때문에 전부 버려졌다면 형식이 바뀐 것이다. 조용히 0건으로
    # 넘어가면 '죽은 피드'로 오해한다 — 실제로 AI타임스에서 50건을 그렇게 잃었다.
    if total and not articles and dropped_date == total:
        raise CollectError(
            f"{source}: {total}건을 모두 발행일시 해석 실패로 버렸습니다. 날짜 형식이 바뀐 듯합니다."
        )
    return articles


def fetch_feed(source: str, spec: FeedSpec, session: requests.Session | None = None) -> list[Article]:
    caller = session or requests
    try:
        response = caller.get(spec.url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise CollectError(f"{source}: 요청 실패 ({exc})") from exc
    return parse_feed(source, response.content, language=spec.language)


def dedupe(articles: list[Article]) -> list[Article]:
    """제목이 같은 기사는 먼저 온 것만 남긴다 (통신사 기사 재게재 대응)."""
    seen: set[str] = set()
    unique: list[Article] = []
    for article in articles:
        key = _normalize_title(article.title)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(article)
    return unique


def gather_articles(
    feeds: dict[str, FeedSpec] | None = None,
    errors: list[str] | None = None,
) -> list[Article]:
    """모든 피드에서 신선한 기사를 **전량** 모아 최신순으로 돌려준다.

    매체별 상한도 전체 상한도 적용하지 않는다 — 버리는 것은 이슈 랭킹 뒤에서 한다
    (스펙 2026-07-17-collector-quota-design.md §4.1). 수집 기간은 매체별로 다르다
    (주간지는 창을 넓게 잡는다).

    최신순 정렬은 무엇을 버릴지 정하기 위한 것이 아니라, rank_issues의 탐욕적
    클러스터링이 시드를 여는 순서를 결정론적으로 고정하기 위한 것이다.
    """
    specs = feeds or FEEDS
    session = requests.Session()
    now = now_kst()

    gathered: list[Article] = []
    for source, spec in specs.items():
        try:
            fetched = fetch_feed(source, spec, session=session)
        except CollectError as exc:
            if errors is None:
                raise
            errors.append(str(exc))
            continue
        cutoff = now - timedelta(hours=spec.max_age_hours)
        fresh = [a for a in fetched if a.published >= cutoff]
        if spec.topic is not None:
            fresh = [a for a in fresh if spec.topic.search(f"{a.title} {a.summary}")]
        gathered.extend(fresh)

    gathered.sort(key=lambda a: a.published, reverse=True)
    return dedupe(gathered)


def collect_articles(
    limit: int = 20,
    feeds: dict[str, FeedSpec] | None = None,
    errors: list[str] | None = None,
) -> list[Article]:
    """최신순 + 매체별 쿼터 + 전체 상한.

    ai_brief·blog_brief 전용이다. **데일리 브리핑은 이 함수를 쓰지 않는다** —
    쿼터가 중요도를 못 보고 그날의 뉴스를 버리기 때문이다(스펙 §1). 데일리는
    gather_articles로 전량을 받아 rank_issues 뒤에서 자른다.
    """
    return apply_quota(gather_articles(feeds, errors), feeds or FEEDS)[:limit]


def collect_quotes(
    tickers: dict[str, str] | None = None,
    errors: list[str] | None = None,
) -> list[Quote]:
    """전일 대비 등락률을 붙인 종가 목록. 지표 하나가 실패해도 나머지는 살린다."""
    import yfinance  # 무거우므로 지연 임포트 (RSS만 쓸 때는 로드하지 않는다)

    symbols = tickers or TICKERS
    try:
        # period는 등락률 계산용 5일이 아니라 스파크라인용 시계열까지 커버해야 한다.
        # 20~30개 종가를 확보하려면 주말·휴장일을 감안해 달력일 기준 2개월 정도 필요하다.
        frame = yfinance.download(
            list(symbols), period="2mo", interval="1d", progress=False, auto_adjust=False
        )
    except Exception as exc:  # yfinance는 예외 종류를 보장하지 않는다
        raise CollectError(f"지표 조회 실패 ({exc})") from exc

    if frame.empty:
        raise CollectError("지표 조회 결과가 비어 있습니다.")

    closes = frame["Close"]
    quotes: list[Quote] = []
    for symbol, name in symbols.items():
        try:
            history = closes[symbol].dropna()
            if len(history) < 2:
                raise ValueError(f"표본 부족 ({len(history)}일)")
            last, prev = float(history.iloc[-1]), float(history.iloc[-2])
            if prev == 0:
                raise ValueError("전일 종가가 0")
        except (KeyError, IndexError, ValueError) as exc:
            message = f"{name}({symbol}) 건너뜀: {exc}"
            if errors is None:
                raise CollectError(message) from exc
            errors.append(message)
            continue

        # 스파크라인 시계열은 부가 정보다 — 만들다 실패해도 등락률 발행 자체는 막지 않는다.
        try:
            series = [float(v) for v in history.iloc[-30:]]
        except (TypeError, ValueError):
            series = None

        quotes.append(
            Quote(
                symbol=symbol,
                name=name,
                price=last,
                change_pct=(last - prev) / prev * 100,
                series=series,
            )
        )
    return quotes


def collect(feeds: dict[str, FeedSpec] | None = None) -> DailyBrief:
    """기사와 지표를 함께 모은다. 한쪽이 실패해도 brief.errors에 남기고 진행한다.

    기사는 **전량**이다(수백 건). 매체별 쿼터를 적용하지 않는다 — 쿼터는 중요도를
    못 보고 최신순으로 잘라 그날의 최대 뉴스를 버렸다(스펙 §1.1). 자르는 일은
    summarize()가 rank_issues 뒤에서 한다.

    feeds를 주면 그 피드만 수집한다(에디션 분리 — 오전 해외판/저녁 국내판). None이면 전체.
    """
    errors: list[str] = []
    articles = gather_articles(feeds=feeds, errors=errors)

    try:
        quotes = collect_quotes(errors=errors)
    except CollectError as exc:
        errors.append(str(exc))
        quotes = []

    return DailyBrief(collected_at=now_kst(), articles=articles, quotes=quotes, errors=errors)


def main() -> int:
    brief = collect()

    print(f"수집 시각: {brief.collected_at:%Y-%m-%d %H:%M} KST\n")

    print(f"지표 {len(brief.quotes)}건")
    for quote in brief.quotes:
        print(f"  {quote.name:<8} {quote.price_text:>12}  {quote.change_text:>8}")

    print(f"\n기사 {len(brief.articles)}건 (최신순)")
    for article in brief.articles:
        hours = article.age.total_seconds() / 3600
        print(f"  {article.source:<14} {hours:5.1f}h  {article.title[:56]}")

    by_source: dict[str, int] = {}
    for article in brief.articles:
        by_source[article.source] = by_source.get(article.source, 0) + 1
    print("\n매체별: " + ", ".join(f"{s} {n}건" for s, n in by_source.items()))

    if brief.errors:
        print(f"\n경고 {len(brief.errors)}건")
        for message in brief.errors:
            print(f"  - {message}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

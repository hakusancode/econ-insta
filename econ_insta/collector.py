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

FEEDS: dict[str, str] = {
    "연합뉴스": "https://www.yna.co.kr/rss/economy.xml",
    "한국경제": "https://www.hankyung.com/feed/economy",
    "매일경제": "https://www.mk.co.kr/rss/30100041/",
}

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

    @property
    def age(self) -> timedelta:
        return now_kst() - self.published


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    price: float
    change_pct: float

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


def parse_pubdate(raw: str) -> datetime:
    """RSS pubDate를 KST tz-aware datetime으로 바꾼다.

    매경의 '+09:00'처럼 콜론이 든 오프셋은 표준 파서가 타임존을 버리므로 먼저 정규화한다.
    타임존이 끝내 없으면 KST로 간주한다 (국내 매체 피드 전제).
    """
    raw = (raw or "").strip()
    if not raw:
        raise CollectError("pubDate가 비어 있습니다.")

    normalized = _TZ_COLON_RE.sub(r"\1\2", raw)
    try:
        parsed = parsedate_to_datetime(normalized)
    except (TypeError, ValueError) as exc:
        raise CollectError(f"pubDate를 해석할 수 없습니다: {raw!r}") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _normalize_title(title: str) -> str:
    """중복 판정용 키. 말머리([특징주] 등)와 기호·공백을 제거한다."""
    text = unicodedata.normalize("NFKC", title)
    text = re.sub(r"^\s*\[[^\]]*\]\s*", "", text)
    text = re.sub(r"[^\w가-힣]+", "", text)
    return text.lower()


def _text(item: ET.Element, tag: str) -> str:
    """자식 태그의 전체 텍스트. findtext()는 자식 엘리먼트가 끼면 앞부분만 돌려준다.

    연합·매경은 description을 CDATA로 보내고 한경은 아예 보내지 않지만,
    포맷이 바뀌어 태그가 섞여 들어와도 요약이 조용히 사라지지 않도록 itertext를 쓴다.
    """
    element = item.find(tag)
    return "" if element is None else "".join(element.itertext())


def parse_feed(source: str, xml_bytes: bytes) -> list[Article]:
    """RSS 바이트를 Article 목록으로. 개별 item 오류는 건너뛴다."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise CollectError(f"{source}: XML 파싱 실패 ({exc})") from exc

    articles: list[Article] = []
    for item in root.findall(".//item"):
        title = clean_text(_text(item, "title"))
        link = _text(item, "link").strip()
        if not title or not link:
            continue
        try:
            published = parse_pubdate(_text(item, "pubDate"))
        except CollectError:
            continue
        articles.append(
            Article(
                source=source,
                title=title,
                link=link,
                published=published,
                summary=clean_text(_text(item, "description"))[:300],
            )
        )
    return articles


def fetch_feed(source: str, url: str, session: requests.Session | None = None) -> list[Article]:
    caller = session or requests
    try:
        response = caller.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise CollectError(f"{source}: 요청 실패 ({exc})") from exc
    return parse_feed(source, response.content)


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


def collect_articles(
    max_age_hours: float = 24,
    limit: int = 20,
    feeds: dict[str, str] | None = None,
    errors: list[str] | None = None,
) -> list[Article]:
    """모든 피드에서 최근 기사를 모아 최신순으로 돌려준다."""
    session = requests.Session()
    cutoff = now_kst() - timedelta(hours=max_age_hours)

    gathered: list[Article] = []
    for source, url in (feeds or FEEDS).items():
        try:
            gathered.extend(fetch_feed(source, url, session=session))
        except CollectError as exc:
            if errors is None:
                raise
            errors.append(str(exc))

    fresh = [a for a in gathered if a.published >= cutoff]
    fresh.sort(key=lambda a: a.published, reverse=True)
    return dedupe(fresh)[:limit]


def collect_quotes(
    tickers: dict[str, str] | None = None,
    errors: list[str] | None = None,
) -> list[Quote]:
    """전일 대비 등락률을 붙인 종가 목록. 지표 하나가 실패해도 나머지는 살린다."""
    import yfinance  # 무거우므로 지연 임포트 (RSS만 쓸 때는 로드하지 않는다)

    symbols = tickers or TICKERS
    try:
        frame = yfinance.download(
            list(symbols), period="5d", interval="1d", progress=False, auto_adjust=False
        )
    except Exception as exc:  # yfinance는 예외 종류를 보장하지 않는다
        raise CollectError(f"지표 조회 실패 ({exc})") from exc

    if frame.empty:
        raise CollectError("지표 조회 결과가 비어 있습니다.")

    closes = frame["Close"]
    quotes: list[Quote] = []
    for symbol, name in symbols.items():
        try:
            series = closes[symbol].dropna()
            if len(series) < 2:
                raise ValueError(f"표본 부족 ({len(series)}일)")
            last, prev = float(series.iloc[-1]), float(series.iloc[-2])
            if prev == 0:
                raise ValueError("전일 종가가 0")
            quotes.append(
                Quote(symbol=symbol, name=name, price=last, change_pct=(last - prev) / prev * 100)
            )
        except (KeyError, IndexError, ValueError) as exc:
            message = f"{name}({symbol}) 건너뜀: {exc}"
            if errors is None:
                raise CollectError(message) from exc
            errors.append(message)
    return quotes


def collect(max_age_hours: float = 24, limit: int = 20) -> DailyBrief:
    """기사와 지표를 함께 모은다. 한쪽이 실패해도 brief.errors에 남기고 진행한다."""
    errors: list[str] = []
    articles = collect_articles(max_age_hours=max_age_hours, limit=limit, errors=errors)

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
        print(f"  [{article.source}] {hours:4.1f}h  {article.title[:52]}")

    if brief.errors:
        print(f"\n경고 {len(brief.errors)}건")
        for message in brief.errors:
            print(f"  - {message}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

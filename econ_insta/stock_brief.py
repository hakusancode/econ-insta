"""종목 이슈 브리핑: 표지 + 이유 카드 N장 + 주가 차트 1장.

데일리 브리핑(여러 종목·여러 기사)과 달리 **한 종목의 한 사건**을 파고드는 포맷이다.
"왜 떨어졌나/올랐나"처럼 인과를 설명해야 하는 날에 쓴다.

차트 카드는 **직접 그린다**. 증권사 HTS 화면이나 뉴스 기사에 실린 차트를 캡처하면
저작권 침해다. 종가 시계열은 사실(fact)이라 저작권이 없으므로, 값만 받아 우리가
그리면 문제가 사라진다. 덤으로 카드 본문에서 수치를 덜어낼 수 있다 — 숫자는
factcheck가 매번 붙잡는 사고 지점이라 그림으로 밀어내는 편이 안전하다.

수치는 모델이 만들지 않는다. 등락률은 여기서 종가로 계산하고, 카드 본문에는
기사에서 확인된 사실만 사람이 넣는다.

CLI:
    python -m econ_insta.stock_brief --publish out/2026-07-14-hynix
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image

from .config import PROJECT_ROOT
from .renderer import (
    ACCENT,
    BG,
    DOWN,
    FG,
    FLAT,
    HEIGHT,
    JPEG_QUALITY,
    MARGIN,
    MUTED,
    OUTPUT_ROOT,
    UP,
    WIDTH,
    FontSet,
    _block_height,
    _canvas,
    _draw_block,
    _rule,
    render_cover,
)

# 아래에 구분선·기간별 등락률·자료 표기가 순서대로 들어간다. 차트를 너무 내리면
# 등락률이 '자료' 줄과 겹친다(실제로 겹쳐서 올렸다).
CHART_TOP = 430
CHART_BOTTOM = 960
CHART_LEFT = MARGIN
CHART_RIGHT = WIDTH - MARGIN

RULE_Y = CHART_BOTTOM + 62
STATS_TOP = RULE_Y + 36


@dataclass(frozen=True)
class Reason:
    """하락·상승 이유 한 가지. source는 반드시 실제 보도 매체다."""

    title: str
    body: str
    source: str


@dataclass(frozen=True)
class Series:
    """종가 시계열. 사실이므로 저작권이 없다 — 우리가 그린다."""

    name: str
    ticker: str
    closes: list[float]
    dates: list[datetime]
    currency: str = "원"
    intraday: bool = False
    """마지막 값이 아직 확정되지 않은 장중 가격인가.

    **장중인데 '종가'라고 쓰면 거짓말이다.** yfinance는 장중에도 오늘 행을 돌려주는데,
    그 값은 종가가 아니라 현재가다. 실제로 이 구분을 놓쳐 '종가'라고 박힌 카드를
    발행했다. 호출부가 시장 시간을 확인해서 이 값을 넘겨야 한다.
    """

    @property
    def last(self) -> float:
        return self.closes[-1]

    @property
    def basis(self) -> str:
        """'종가' 또는 '현재가'. 카드·캡션 문구는 반드시 이걸 쓴다."""
        return "현재가" if self.intraday else "종가"

    def change_pct(self, sessions: int) -> float | None:
        """N거래일 전 대비 등락률. 데이터가 모자라면 None."""
        if len(self.closes) <= sessions:
            return None
        base = self.closes[-1 - sessions]
        return (self.closes[-1] / base - 1) * 100 if base else None


@dataclass(frozen=True)
class StockBrief:
    headline: str
    series: Series
    reasons: list[Reason]
    caption_hook: str
    hashtags: list[str] = field(default_factory=list)


def _change_color(value: float | None) -> tuple[int, int, int]:
    """한국 관행: 상승 빨강, 하락 파랑."""
    if value is None or abs(value) < 0.005:
        return FLAT
    return UP if value > 0 else DOWN


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def render_chart(series: Series, fonts: FontSet, when: datetime) -> Image.Image:
    """종가 추이 꺾은선 + 기간별 등락률.

    격자·눈금 없이 선과 최고/최저만 남긴다. 인스타는 작게 보이므로 눈금은 읽히지 않는다.
    """
    image, draw = _canvas(BG)
    inner = WIDTH - MARGIN * 2

    draw.text((MARGIN, MARGIN), "주가 추이", font=fonts.at(58, bold=True), fill=FG)
    draw.text(
        (MARGIN, MARGIN + 78),
        f"{series.name} · {series.ticker} · 최근 3개월",
        font=fonts.at(30),
        fill=MUTED,
    )

    day_change = series.change_pct(1)
    draw.text(
        (WIDTH - MARGIN, MARGIN + 4),
        f"{series.last:,.0f}{series.currency}",
        font=fonts.at(52, bold=True),
        fill=FG,
        anchor="ra",
    )
    draw.text(
        (WIDTH - MARGIN, MARGIN + 74),
        _fmt_pct(day_change),
        font=fonts.at(34, bold=True),
        fill=_change_color(day_change),
        anchor="ra",
    )

    closes = series.closes
    low, high = min(closes), max(closes)
    span = (high - low) or 1.0

    def point(index: int, value: float) -> tuple[float, float]:
        x = CHART_LEFT + (CHART_RIGHT - CHART_LEFT) * index / max(len(closes) - 1, 1)
        y = CHART_BOTTOM - (CHART_BOTTOM - CHART_TOP) * (value - low) / span
        return x, y

    points = [point(i, v) for i, v in enumerate(closes)]

    # 선 아래를 옅게 채워 방향이 한눈에 들어오게 한다.
    trend = _change_color(series.change_pct(len(closes) - 1))
    fill_color = tuple(int(c * 0.22 + BG[i] * 0.78) for i, c in enumerate(trend))
    draw.polygon(
        [(CHART_LEFT, CHART_BOTTOM)] + points + [(CHART_RIGHT, CHART_BOTTOM)],
        fill=fill_color,
    )
    draw.line(points, fill=trend, width=5, joint="curve")

    # 최고·최저만 표시한다.
    hi_index = closes.index(high)
    lo_index = closes.index(low)
    for index, value, label, anchor in (
        (hi_index, high, f"고 {high:,.0f}", "ls"),
        (lo_index, low, f"저 {low:,.0f}", "la"),
    ):
        x, y = point(index, value)
        draw.ellipse([x - 7, y - 7, x + 7, y + 7], fill=trend)
        dy = -18 if anchor == "ls" else 22
        draw.text(
            (min(max(x, MARGIN + 40), WIDTH - MARGIN - 40), y + dy),
            label,
            font=fonts.at(26),
            fill=MUTED,
            anchor="ms" if anchor == "ls" else "ma",
        )

    _rule(draw, RULE_Y)

    # 기간별 등락률. 3개월은 오른 상태인데 1주는 급락 — 이 대비가 이 카드의 핵심이다.
    periods = [("1주", 5), ("1개월", 21), ("3개월", len(closes) - 1)]
    cell = inner // len(periods)
    for i, (label, sessions) in enumerate(periods):
        change = series.change_pct(sessions)
        cx = MARGIN + cell * i + cell // 2
        draw.text((cx, STATS_TOP), label, font=fonts.at(30), fill=MUTED, anchor="ma")
        draw.text(
            (cx, STATS_TOP + 48),
            _fmt_pct(change),
            font=fonts.at(46, bold=True),
            fill=_change_color(change),
            anchor="ma",
        )

    basis = f"장중 {series.basis}" if series.intraday else f"{series.basis} 기준"
    draw.text(
        (WIDTH - MARGIN, HEIGHT - MARGIN - 36),
        f"자료 · {basis} ({when:%Y.%m.%d})",
        font=fonts.at(28),
        fill=MUTED,
        anchor="ra",
    )
    return image


def render_reason(reason: Reason, index: int, total: int, fonts: FontSet) -> Image.Image:
    """이유 카드. 번호를 크게 달아 '몇 번째 이유'인지 바로 보이게 한다."""
    image, draw = _canvas(BG)
    inner = WIDTH - MARGIN * 2

    draw.text((MARGIN, MARGIN), f"이유 {index}", font=fonts.at(44, bold=True), fill=ACCENT)
    draw.text(
        (WIDTH - MARGIN, MARGIN + 10),
        f"{index} / {total}",
        font=fonts.at(28),
        fill=MUTED,
        anchor="ra",
    )

    title_font = fonts.at(60, bold=True)
    body_font = fonts.at(40)

    # 머리말과 출처 사이에 세로 중앙 정렬한다. 상단에 붙이면 아래 절반이 비어
    # 실수처럼 보인다 — 기존 render_card가 같은 이유로 중앙 정렬한다.
    gap = 44
    block = (
        _block_height(reason.title, title_font, inner)
        + gap * 2
        + _block_height(reason.body, body_font, inner)
    )
    field_top, field_bottom = MARGIN + 150, HEIGHT - MARGIN - 90
    top = max(field_top, field_top + (field_bottom - field_top - block) // 2)

    top = _draw_block(draw, reason.title, title_font, top=top, fill=FG, max_width=inner)
    top += gap
    _rule(draw, top)
    top += gap
    _draw_block(draw, reason.body, body_font, top=top, fill=(206, 212, 224), max_width=inner)

    draw.text(
        (MARGIN, HEIGHT - MARGIN - 36),
        f"출처 · {reason.source}",
        font=fonts.at(28),
        fill=MUTED,
    )
    return image


def render(
    brief: StockBrief,
    when: datetime,
    out_dir: Path,
    fonts: FontSet | None = None,
    background=None,
) -> list[Path]:
    fonts = fonts or FontSet.discover()
    out_dir.mkdir(parents=True, exist_ok=True)

    images = [
        render_cover(brief.headline, when, fonts, kicker="종목 이슈 브리핑", background=background),
        *(
            render_reason(reason, i + 1, len(brief.reasons), fonts)
            for i, reason in enumerate(brief.reasons)
        ),
        render_chart(brief.series, fonts, when),
    ]

    paths = []
    for index, image in enumerate(images):
        path = out_dir / f"{index:02d}.jpg"
        image.save(path, "JPEG", quality=JPEG_QUALITY)
        paths.append(path)
    return paths


DISCLAIMER = "※ 투자 판단의 근거로 삼지 마십시오. 투자 책임은 본인에게 있습니다."


def build_caption(brief: StockBrief, when: datetime, credits: tuple[str, ...] = ()) -> str:
    series = brief.series
    lines = [
        brief.caption_hook,
        "",
        f"{series.name} ({series.ticker}) · {when:%Y.%m.%d} "
        f"{'장중 ' if series.intraday else ''}{series.basis} {series.last:,.0f}{series.currency}",
        f"1주 {_fmt_pct(series.change_pct(5))} / 1개월 {_fmt_pct(series.change_pct(21))} / "
        f"3개월 {_fmt_pct(series.change_pct(len(series.closes) - 1))}",
        "",
    ]
    lines += [f"· {reason.title} ({reason.source})" for reason in brief.reasons]
    lines += ["", DISCLAIMER]

    sources = sorted({reason.source for reason in brief.reasons})
    lines += ["", f"출처 · {' · '.join(sources)}"]
    if credits:
        lines += ["", f"📷 사진: {' · '.join(credits)}"]
    if brief.hashtags:
        lines += ["", " ".join(f"#{tag.lstrip('#')}" for tag in brief.hashtags)]
    return "\n".join(lines)


# 인스타는 image_url을 자기 서버에서 가져가므로 커밋·push 후에야 발행할 수 있다.
RAW_BASE = "https://raw.githubusercontent.com/hakusancode/econ-insta/main"


def publish_rendered(out_dir: Path) -> int:
    from .ig_client import InstagramClient

    out_dir = out_dir.resolve()
    caption_path = out_dir / "caption.txt"
    if not caption_path.exists():
        print(f"caption.txt가 없습니다: {out_dir}")
        return 1
    caption = caption_path.read_text(encoding="utf-8")

    images = sorted(out_dir.glob("[0-9][0-9].jpg"))
    if not images:
        print(f"카드 이미지(NN.jpg)가 없습니다: {out_dir}")
        return 1

    rel = out_dir.relative_to(PROJECT_ROOT.resolve()).as_posix()
    urls = [f"{RAW_BASE}/{rel}/{path.name}" for path in images]

    for url in urls:
        response = requests.get(url, timeout=20, allow_redirects=False)
        kind = response.headers.get("Content-Type", "")
        if response.status_code != 200 or kind != "image/jpeg":
            print(f"호스팅 확인 실패 ({response.status_code}, {kind}): {url}")
            print("커밋·push가 끝났는지 확인하세요.")
            return 1

    result = InstagramClient().publish_images(urls, caption)
    print(f"발행 완료: media_id={result.media_id}")
    print(f"  {result.permalink}")
    return 0


def main() -> int:
    import sys

    if "--publish" in sys.argv:
        index = sys.argv.index("--publish")
        if index + 1 >= len(sys.argv):
            print("사용법: python -m econ_insta.stock_brief --publish out/<날짜>-<종목>")
            return 1
        return publish_rendered(Path(sys.argv[index + 1]))

    print("사용법: python -m econ_insta.stock_brief --publish out/<날짜>-<종목>")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

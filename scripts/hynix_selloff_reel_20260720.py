"""2026-07-20 SK하이닉스 연쇄 하락 릴스(9:16) 생성.

7/14 릴스(scripts/hynix_reel.py)의 후속편. 이유 본문은 보도에서 확인된 것만 담는다:
- 한국일보 2026-07-16 (미국발 급락 전이·크루소 연기·뉴욕주 유예·CXMT 청약·사이드카)
- 로이터 (나스닥 ADR 데뷔 직후 차익 실현 — 7/14 카드에서 검증한 사실)
- 연합뉴스 2026-07-20 (피크아웃 우려 재부각, 4%대 하락 — 7/20 데일리 브리핑에서 검증)

yfinance의 2026-07-20 행은 Close가 NaN이다(야후 데이터 글리치). 그날 종가는
1,764,000원 — 7/20 브리핑 표지의 시세판 사진과 일치하고, 전일 종가 1,842,000원
대비 -4.23%로 보도된 등락률과 산술이 정확히 맞아떨어져 직접 보정한다.
"""

from __future__ import annotations

from datetime import datetime

import yfinance as yf

from econ_insta.config import PROJECT_ROOT
from econ_insta.reels import build_stock_reel, cover_crop
from econ_insta.renderer import FontSet
from econ_insta.stock_brief import Reason, Series, StockBrief, build_caption
from econ_insta.wikimedia import download, search_images

TICKER = "000660.KS"
WHEN = datetime(2026, 7, 20)
OUT = PROJECT_ROOT / "out" / "2026-07-20-hynix-selloff"

CLOSE_0720 = 1_764_000.0  # 시세판 사진·보도 등락률(-4.23%)로 이중 확인한 7/20 종가


def load_series() -> Series:
    # auto_adjust=False: 조정 종가는 실재한 적 없는 가격을 만든다 (hynix_20260714 참고).
    history = yf.Ticker(TICKER).history(period="3mo", auto_adjust=False)
    if history.empty:
        raise SystemExit("주가 데이터를 받지 못했습니다.")
    rows = [
        (d.to_pydatetime(), float(v))
        for d, v in history["Close"].items()
        if v == v  # NaN 행(7/20 글리치)은 버리고 아래에서 보정한다
    ]
    dates = [d for d, _ in rows]
    closes = [v for _, v in rows]
    if dates[-1].date() < WHEN.date():
        dates.append(WHEN)
        closes.append(CLOSE_0720)
    return Series(
        name="SK하이닉스",
        ticker=TICKER,
        closes=closes,
        dates=dates,
        intraday=False,  # 7/20 장 마감 후 확정 종가 기준
    )


REASONS = [
    Reason(
        title="미국발 반도체 한파가 서울로 번졌다",
        body=(
            "AI 데이터센터 개발사 크루소의 건설 연기와 뉴욕주의 데이터센터 건설 "
            "유예 소식에 뉴욕 반도체주가 일제히 급락했고, 다음 날 그 여파가 "
            "코스피를 덮쳤다. 매도 사이드카가 발동될 만큼 급했다."
        ),
        source="한국일보",
    ),
    Reason(
        title="중국 CXMT가 대규모 상장에 나섰다",
        body=(
            "중국 창신메모리(CXMT)가 조 단위 상장 청약에 돌입했다. 삼성전자·"
            "SK하이닉스·마이크론의 '메모리 3강' 구도가 흔들릴 수 있다는 우려가 "
            "투자 심리를 눌렀다."
        ),
        source="한국일보",
    ),
    Reason(
        title="나스닥 데뷔가 부른 차익 실현",
        body=(
            "미국 예탁증서(ADR)로 나스닥에 데뷔한 직후부터, 상장을 겨냥해 미리 "
            "사둔 물량이 쏟아지고 있다. 1년 반 넘게 이어진 급등 뒤라 이익을 "
            "확정하려는 매도가 두텁다."
        ),
        source="로이터",
    ),
    Reason(
        title="'피크아웃' 우려가 다시 고개를 들었다",
        body=(
            "반도체 업황이 정점을 지났다는 우려가 다시 부각되며 반등 없이 "
            "하락이 이어졌다. 폭락 다음 거래일에도 삼성전자와 SK하이닉스는 "
            "나란히 4%대 하락으로 마감했다."
        ),
        source="연합뉴스",
    ),
]


def main() -> None:
    series = load_series()

    results = search_images("Silicon wafer close view")
    background, credits = None, ()
    if results:
        best = results[0]
        background = cover_crop(download(best))
        credits = (best.credit,)
        print(f"  배경: {best.title} [{best.license_name}]")

    brief = StockBrief(
        headline="하이닉스, 폭락은 왜 멈추지 않나",
        series=series,
        reasons=REASONS,
        caption_hook=(
            "나스닥 데뷔 열흘, SK하이닉스가 사이드카를 부른 폭락 이후에도 "
            "반등 없이 미끄러지고 있습니다. 무엇이 누르고 있는지 네 가지로 "
            "정리했습니다."
        ),
        hashtags=["SK하이닉스", "반도체", "사이드카", "경제뉴스", "주식"],
    )

    video, cover = build_stock_reel(
        brief, WHEN, OUT, fonts=FontSet.discover(), background=background
    )
    (OUT / "caption.txt").write_text(build_caption(brief, WHEN, credits), encoding="utf-8")

    print(f"\n영상: {video}  ({video.stat().st_size / 1e6:.1f} MB)")
    print(f"표지: {cover}")


if __name__ == "__main__":
    main()

"""2026-07-28 코스피 사상 최대 낙폭(-10.84%) 릴스 생성.

이유 본문은 같은 날 데일리 국내판(out/2026-07-28-kr, audit 통과분)에서 검증된
사실만 옮겼다: 장중 6,000선 붕괴·사상 최대 낙폭·사이드카/서킷브레이커(연합뉴스),
반도체 중심 급락·'반도체 최악의 날'(한국경제), 6,000선 사수 공방(연합뉴스).

표지는 사진 배경(위키미디어 공용, 허용 라이선스만) — 사용자 지시로 전광판
그래픽 시안 대신 실사진을 쓴다. 사진 실패 시 단색 표지로 저하하고 발행은 계속.
"""

from __future__ import annotations

from datetime import datetime

import yfinance as yf

from econ_insta import photos
from econ_insta.collector import KR_FEEDS, collect
from econ_insta.config import PROJECT_ROOT
from econ_insta.issues import Issue, rank_issues
from econ_insta.reels import build_stock_reel, cover_crop
from econ_insta.renderer import FontSet
from econ_insta.stock_brief import Reason, Series, StockBrief, build_caption

WHEN = datetime(2026, 7, 28)
OUT = PROJECT_ROOT / "out" / "2026-07-28-kospi-crash"


def load_series() -> Series:
    history = yf.Ticker("^KS11").history(period="3mo", auto_adjust=False)
    closes = [float(v) for v in history["Close"] if v == v]
    dates = [d.to_pydatetime() for d, v in history["Close"].items() if v == v]
    if not closes:
        raise SystemExit("지수 데이터를 받지 못했습니다.")
    return Series(
        name="코스피", ticker="KOSPI", closes=closes, dates=dates,
        currency="", intraday=False,  # 장 마감 후 확정 종가 (6,023.66, -10.84%)
    )


REASONS = [
    Reason(
        title="장중 6,000선 붕괴, 사상 최대 낙폭",
        body=(
            "28일 코스피가 장중 6,000선 아래로 밀리며 사상 최대 낙폭을 기록했다. "
            "코스피는 10%, 코스닥은 8% 가까이 급락해 양 시장에 사이드카와 "
            "서킷브레이커가 잇따라 발동됐다."
        ),
        source="연합뉴스",
    ),
    Reason(
        title="반도체주가 몰고 온 패닉",
        body=(
            "이번 폭락은 반도체 업종 중심의 급락세에서 비롯됐다. 한국경제는 "
            "이날 시장을 '반도체 최악의 날'이라 표현하며 투자심리 위축을 전했다."
        ),
        source="한국경제",
    ),
    Reason(
        title="다음 관전 포인트는 6,000선 사수",
        body=(
            "급락 후 낙폭을 줄이려는 공방이 이어졌다. 6,000선 사수 여부와 "
            "반도체주 반등 여부가 다음 거래일 시장의 최대 관심사로 떠올랐다."
        ),
        source="연합뉴스",
    ),
]


def main() -> None:
    series = load_series()

    # 위키미디어가 2026-07-26경부터 전 요청 429라 뉴스 기사 사진을 쓴다
    # (실사 전면 허용은 2026-07-16 사용자 결정). 오늘 최상위 이슈 = 코스피 폭락.
    background, credits = None, ()
    brief = collect(feeds=KR_FEEDS)
    # 릴스 주제(코스피 폭락)와 맞는 사진이어야 한다. 클러스터 하나에 사진이 없을 수
    # 있어 폭락 관련 기사 전체에서 후보를 모은다(같은 사건 보도의 시세판·객장 사진).
    crash = [a for a in brief.articles
             if any(k in a.title for k in ("코스피", "증시", "급락", "폭락", "사이드카"))]
    print(f"  폭락 관련 기사 {len(crash)}건에서 사진 후보 수집")
    pairs = photos.usable(photos.candidates(Issue(articles=crash)))
    if pairs:
        candidate, image = pairs[0]
        background = cover_crop(image)
        print(f"  배경: 기사 사진 {candidate.url[:70]}")
    else:
        print("  ! 배경 사진 없음 — 단색 표지로 나갑니다")

    brief = StockBrief(
        headline="코스피 -10.8%, 무엇이 무너뜨렸나",
        series=series,
        reasons=REASONS,
        caption_hook=(
            "코스피가 하루 만에 10% 넘게 밀리며 사상 최대 낙폭을 기록했습니다. "
            "사이드카와 서킷브레이커까지 부른 폭락의 이유를 정리했습니다."
        ),
        hashtags=["코스피", "증시폭락", "사이드카", "경제뉴스", "주식"],
    )

    video, cover = build_stock_reel(
        brief, WHEN, OUT, fonts=FontSet.discover(), background=background
    )
    (OUT / "caption.txt").write_text(build_caption(brief, WHEN, credits), encoding="utf-8")
    print(f"\n영상: {video}  ({video.stat().st_size / 1e6:.1f} MB)")
    print(f"표지: {cover}")


if __name__ == "__main__":
    main()

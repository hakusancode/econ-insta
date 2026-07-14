"""2026-07-14 SK하이닉스 종목 이슈 브리핑 카드 생성.

수치는 모델이 만들지 않는다 — 등락률은 yfinance 종가로 계산하고, 이유 카드 본문은
실제 보도(로이터/CNBC/Motley Fool/한국일보 등)에서 확인된 사실만 담았다.
카드 본문에 숫자를 최소화하고 등락률은 차트 카드로 밀었다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yfinance as yf

from econ_insta.backgrounds import cover_crop
from econ_insta.config import PROJECT_ROOT
from econ_insta.renderer import HEIGHT, WIDTH, FontSet
from econ_insta.stock_brief import Reason, Series, StockBrief, build_caption, render
from econ_insta.wikimedia import download, search_images

TICKER = "000660.KS"
WHEN = datetime(2026, 7, 14)
OUT = PROJECT_ROOT / "out" / "2026-07-14-hynix"


def load_series() -> Series:
    # auto_adjust=False: 기본값(True)은 배당을 반영해 과거 종가를 조정하므로
    # 카드에 '저 1,102,816원' 같은 실제로 존재한 적 없는 가격이 찍힌다.
    history = yf.Ticker(TICKER).history(period="3mo", auto_adjust=False)
    if history.empty:
        raise SystemExit("주가 데이터를 받지 못했습니다.")
    closes = [float(v) for v in history["Close"]]
    dates = [d.to_pydatetime() for d in history.index]
    return Series(name="SK하이닉스", ticker=TICKER, closes=closes, dates=dates)


REASONS = [
    Reason(
        title="나스닥 상장 직후, 차익 실현이 몰렸다",
        body=(
            "7월 10일 미국 예탁증서(ADR)로 나스닥에 데뷔하며 첫날 급등했지만, "
            "상장을 겨냥해 미리 사둔 물량이 곧바로 쏟아졌다. 서울 증시는 다음 "
            "거래일 20년 만의 최대 낙폭을 기록했다."
        ),
        source="로이터",
    ),
    Reason(
        title="2분기 실적이 기대에 못 미칠 것이라는 전망",
        body=(
            "증권가는 SK하이닉스의 2분기 영업이익이 시장 컨센서스를 밑돌 것으로 "
            "내다봤다. 고대역폭 메모리(HBM)를 장기 계약가로 파는 구조라, 값이 "
            "올라도 실적이 그만큼 따라 오르지 못한다는 점이 지적됐다."
        ),
        source="한국투자증권",
    ),
    Reason(
        title="HBM4 출하가 기대만큼 늘지 않았다",
        body=(
            "시장은 2분기부터 차세대 HBM4 공급이 본격적으로 늘어날 것으로 봤지만, "
            "실제 출하는 그 기대에 미치지 못한 것으로 파악됐다. 성장의 속도가 "
            "의심받기 시작한 지점이다."
        ),
        source="로이터",
    ),
    Reason(
        title="AI 투자가 계속될 수 있느냐는 의심",
        body=(
            "월가에서 AI 인프라 투자 지속 가능성에 대한 의문이 번지며 반도체주 전반이 "
            "함께 밀렸다. 메모리 업황이 정점을 지난 것 아니냐는 '피크아웃' 우려가 "
            "따라붙었다."
        ),
        source="CNBC",
    ),
]


def load_background():
    """표지 배경. 실패해도 단색으로 발행은 나간다.

    공용의 SK하이닉스 사진은 전부 같은 전시 보드에서 찍혀 'Capacity / Speed' 스펙 표가
    크게 박혀 있다. 확대해도 그 글자가 같이 커져 표지 제목과 싸운다 — 실제로 두 번
    렌더해 보고 버렸다. 브랜드보다 읽히는 표지가 중요하므로 웨이퍼 사진을 쓴다.
    """
    results = search_images("Silicon wafer close view")
    if not results:
        print("  ! 배경 사진 없음 — 단색 표지로 나갑니다")
        return None, ()
    best = results[0]
    print(f"  배경: {best.title} [{best.license_name}]")
    return cover_crop(download(best), WIDTH, HEIGHT), (best.credit,)


def main() -> None:
    series = load_series()
    background, credits = load_background()

    brief = StockBrief(
        headline="하이닉스, 왜 무너졌나",
        series=series,
        reasons=REASONS,
        caption_hook=(
            "나스닥에 데뷔한 지 사흘 만에 SK하이닉스가 20년 만의 최대 낙폭을 냈습니다. "
            "무엇이 무너뜨렸는지 네 가지로 정리했습니다."
        ),
        hashtags=["SK하이닉스", "반도체", "HBM", "경제뉴스", "주식"],
    )

    paths = render(brief, WHEN, OUT, fonts=FontSet.discover(), background=background)
    caption = build_caption(brief, WHEN, credits)
    (OUT / "caption.txt").write_text(caption, encoding="utf-8")

    print(f"\n카드 {len(paths)}장 → {OUT}")
    print("-" * 60)
    print(caption)


if __name__ == "__main__":
    main()

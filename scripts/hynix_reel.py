"""2026-07-14 SK하이닉스 릴스(9:16) 생성.

카드(scripts/hynix_20260714.py)와 같은 데이터·같은 사실을 쓴다. 수치는 종가로 계산하고
이유 본문은 보도에서 확인된 것만 담는다.
"""

from __future__ import annotations

from econ_insta.reels import build_stock_reel, cover_crop
from econ_insta.renderer import FontSet
from econ_insta.stock_brief import StockBrief, build_caption
from econ_insta.wikimedia import download, search_images

from hynix_20260714 import OUT, REASONS, WHEN, load_series


def main() -> None:
    series = load_series()

    results = search_images("Silicon wafer close view")
    background, credits = None, ()
    if results:
        best = results[0]
        background = cover_crop(download(best))  # 9:16으로 다시 자른다
        credits = (best.credit,)
        print(f"  배경: {best.title} [{best.license_name}]")

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

    video, cover = build_stock_reel(
        brief, WHEN, OUT, fonts=FontSet.discover(), background=background
    )
    (OUT / "caption.txt").write_text(build_caption(brief, WHEN, credits), encoding="utf-8")

    print(f"\n영상: {video}  ({video.stat().st_size / 1e6:.1f} MB)")
    print(f"표지: {cover}")


if __name__ == "__main__":
    main()

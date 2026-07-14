"""카드 디자인 시안: 테마 4종 × 카드 3종을 **실제 렌더러로** 뽑는다.

HTML 목업이 아니라 진짜 렌더 결과다. 고른 테마를 renderer.DEFAULT_THEME으로 바꾸면
그대로 발행에 쓰인다 — '시안을 코드로 포팅'하는 단계가 없다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image

from econ_insta.backgrounds import cover_crop
from econ_insta.config import PROJECT_ROOT
from econ_insta.renderer import (
    HEIGHT,
    THEMES,
    WIDTH,
    FontSet,
    render_card,
    render_cover,
    render_indicators,
)
from econ_insta.summarizer import Card
from econ_insta.wikimedia import download, search_images

OUT = PROJECT_ROOT / "out" / "_themes"
WHEN = datetime(2026, 7, 14)

SAMPLE_CARD = Card(
    title="연준, 기준금리 동결… 인하 신호는 아꼈다",
    body=(
        "연방준비제도가 기준금리를 동결했다. 파월 의장은 물가가 목표에 다가서고 있다면서도 "
        "인하를 서두를 이유는 없다고 했다. 시장은 연내 인하 횟수 전망을 한 차례 줄였다."
    ),
    source="WSJ",
)


class Quote:
    """render_indicators가 기대하는 최소 형태."""

    def __init__(self, name, price_text, change_text, change_pct):
        self.name = name
        self.price_text = price_text
        self.change_text = change_text
        self.change_pct = change_pct


SAMPLE_QUOTES = [
    Quote("코스피", "2,847.21", "+1.24%", 1.24),
    Quote("코스닥", "864.10", "-0.38%", -0.38),
    Quote("원/달러", "1,382.5", "+0.21%", 0.21),
    Quote("나스닥", "21,043.8", "+0.87%", 0.87),
    Quote("WTI", "$71.20", "-1.10%", -1.10),
    Quote("비트코인", "$104,820", "+2.35%", 2.35),
]
SAMPLE_NOTE = "반도체가 지수를 끌어올린 하루였다. 원화는 소폭 약세로 마감했다."


def load_background() -> Image.Image | None:
    results = search_images("Federal Reserve building Washington")
    if not results:
        return None
    return cover_crop(download(results[0]), WIDTH, HEIGHT)


def main() -> None:
    fonts = FontSet.discover()
    background = load_background()
    OUT.mkdir(parents=True, exist_ok=True)

    for theme in THEMES:
        key = theme.name.split(" ")[0]
        cards = {
            "cover": render_cover(
                "연준, 금리 동결… 시장은 인하를 미뤘다",
                WHEN,
                fonts,
                background=background,
                theme=theme,
            ),
            "cover-plain": render_cover(
                "연준, 금리 동결… 시장은 인하를 미뤘다", WHEN, fonts, theme=theme
            ),
            "card": render_card(SAMPLE_CARD, 1, 4, fonts, theme=theme),
            "indicators": render_indicators(SAMPLE_QUOTES, SAMPLE_NOTE, fonts, theme=theme),
        }
        for kind, image in cards.items():
            image.save(OUT / f"{key}-{kind}.jpg", "JPEG", quality=88)
        print(f"{theme.name}: {len(cards)}장")

    print(f"\n→ {OUT}")


if __name__ == "__main__":
    main()

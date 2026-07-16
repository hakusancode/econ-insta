"""표지 배경 이미지: 기사 사진 → 인물 라이브러리 → 위키미디어 공용 → Unsplash 순.

**1순위는 이슈 기사에 실린 사진이다**(`photos.py`). 그 이슈를 다룬 기사에 매체가
직접 붙인 사진이라 관련성이 편집자에 의해 보장되고, Claude 비전이 사물컷을 걸러
가장 센 컷을 고른다.

인물 라이브러리(assets/people/)는 **폴백**이다 — 기사에 사진이 없는 이슈(한경 단독
등)의 안전망. 확대하지 않는다: 현직 인물은 이제 기사 사진이 덮는다.
좌우 나란히 콜라주는 폐기했다(뉴스 썸네일처럼 보여 표지가 싸구려가 된다).

위키미디어가 Unsplash보다 먼저다. **Unsplash는 인물 커버리지가 0이고**(스톡 사진이라
유명인이 없다) 늘어나는 건 주제 배경의 미적 품질뿐이다 — 인물·로고는 공용에서만 온다.

전부 실패하면 None으로 폴백해 표지가 그래픽으로 나간다 — 배경 때문에 발행이 죽으면 안 된다.

**라이선스**: 뉴스 사진에는 크레딧을 달지 않는다(사용자 결정, DMCA 리스크는 사용자 소유).
**단 CC BY 크레딧은 라이선스 자체의 조건이라 유지한다** — 빼면 실제 위반이다.

CLI:
    python -m econ_insta.backgrounds "semiconductor memory chips"   # 검색 시험
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

from . import photos, wikimedia
from .config import PROJECT_ROOT, _load_dotenv
from .issues import Issue

WIDTH, HEIGHT = 1080, 1350
TIMEOUT = 30

PEOPLE_DIR = PROJECT_ROOT / "assets" / "people"
PEOPLE_META = PEOPLE_DIR / "people.json"

UNSPLASH_API = "https://api.unsplash.com"
USER_AGENT = "econ-insta/0.1 (github.com/hakusancode/econ-insta)"


class BackgroundError(RuntimeError):
    """배경 이미지 준비 실패."""


@dataclass(frozen=True)
class Background:
    image: Image.Image
    """1080×1350 RGB."""
    credits: tuple[str, ...]
    """캡션에 넣을 크레딧 문자열들. CC BY 사진은 생략하면 안 된다."""


def cover_crop(image: Image.Image, width: int, height: int, top_bias: float = 0.5) -> Image.Image:
    """비율을 유지하며 확대 후 잘라 딱 맞춘다.

    top_bias는 세로로 남는 부분을 어디서 자를지다. 0.0이면 위쪽을 최대한 살리고
    (인물 초상 — 얼굴이 위쪽에 있다), 0.5면 가운데를 살린다.
    """
    image = image.convert("RGB")
    scale = max(width / image.width, height / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    left = (resized.width - width) // 2
    top = int((resized.height - height) * top_bias)
    return resized.crop((left, top, left + width, top + height))


# --- 인물 콜라주 ---------------------------------------------------------


def available_people() -> dict[str, dict]:
    """people.json의 인물 키 → 메타데이터. 라이브러리가 없으면 빈 dict."""
    if not PEOPLE_META.exists():
        return {}
    return json.loads(PEOPLE_META.read_text(encoding="utf-8"))


def compose_people(keys: list[str]) -> Background:
    """인물 라이브러리의 초상을 표지 배경으로. **첫 번째 인물만 전면 크롭한다.**

    좌우 나란히 콜라주는 폐기했다 — 뉴스 썸네일처럼 보여 표지가 싸구려가 된다.
    2인 이상이면 첫 번째만 쓴다: 이 경로는 기사 사진이 1순위를 가져간 뒤에야 닿는
    폴백의 폴백이라, 거의 안 쓰일 분할 합성 렌더러를 새로 짜는 것은 과잉이다.
    모델이 people을 우선순위 순으로 주므로 첫 번째가 그 이슈의 주인공이다.

    얼굴이 잘리지 않도록 위쪽을 살려(top_bias 0.1) 자른다.
    """
    meta = available_people()
    unknown = [k for k in keys if k not in meta]
    if unknown:
        raise BackgroundError(f"인물 라이브러리에 없는 키: {unknown} (보유: {sorted(meta)})")
    if not keys:
        raise BackgroundError("인물이 없습니다.")

    key = keys[0]
    path = PEOPLE_DIR / meta[key]["file"]
    if not path.exists():
        raise BackgroundError(f"인물 사진 파일이 없습니다: {path}")

    canvas = Image.new("RGB", (WIDTH, HEIGHT))
    canvas.paste(cover_crop(Image.open(path), WIDTH, HEIGHT, top_bias=0.1), (0, 0))
    # 안 쓴 사진의 크레딧을 달면 캡션이 거짓말이 된다.
    return Background(image=canvas, credits=(meta[key]["credit"],))


# --- Unsplash ------------------------------------------------------------


def fetch_unsplash(query: str, session: requests.Session | None = None) -> Background | None:
    """주제 키워드로 세로 사진을 받아온다. 키가 없거나 결과가 없으면 None."""
    _load_dotenv()
    key = os.environ.get("UNSPLASH_ACCESS_KEY")
    if not key:
        return None

    caller = session or requests.Session()
    headers = {
        "Authorization": f"Client-ID {key}",
        "Accept-Version": "v1",
        "User-Agent": USER_AGENT,
    }
    try:
        response = caller.get(
            f"{UNSPLASH_API}/search/photos",
            params={
                "query": query,
                "orientation": "portrait",
                "content_filter": "high",
                "per_page": 5,
            },
            headers=headers,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return None
        photo = results[0]

        # Unsplash API 가이드라인: 사진을 실제로 쓸 때 download 엔드포인트를 호출해야 한다.
        caller.get(photo["links"]["download_location"], headers=headers, timeout=TIMEOUT)

        # raw URL에 imgix 파라미터를 붙여 서버 쪽에서 잘라 받는다.
        raw = f"{photo['urls']['raw']}&w={WIDTH}&h={HEIGHT}&fit=crop&fm=jpg&q=85"
        image_bytes = caller.get(raw, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT).content
        image = Image.open(BytesIO(image_bytes))
        image.load()
    except (requests.RequestException, OSError, KeyError, ValueError) as exc:
        raise BackgroundError(f"Unsplash 조회 실패 ({exc})") from exc

    if image.size != (WIDTH, HEIGHT):
        image = cover_crop(image, WIDTH, HEIGHT)
    photographer = photo.get("user", {}).get("name", "").strip() or "Unknown"
    return Background(image=image.convert("RGB"), credits=(f"{photographer} on Unsplash",))


# --- 위키미디어 공용 -------------------------------------------------------


def _relevant(image: wikimedia.CommonsImage, query: str) -> bool:
    """파일명이 검색어와 실제로 겹치는가.

    공용 전문검색은 설명·카테고리까지 훑기 때문에 주제와 무관한 기록사진을 상위에 올린다.
    "data center servers"로 검색했더니 **1990년대 방송 조정실 사진**이 1등으로 나와
    AI 브리핑 표지에 깔릴 뻔했다. 연준 청사처럼 구체적 랜드마크는 잘 맞지만 추상적
    주제에서는 엇나간다. 파일명에 검색어가 하나도 안 들어 있으면 믿지 않는다.
    """
    title = image.title.lower()
    terms = [t for t in re.findall(r"[a-z]{4,}", query.lower())]
    return any(term in title for term in terms) if terms else True


def fetch_wikimedia(query: str, session: requests.Session | None = None) -> Background | None:
    """공용에서 재사용 가능한 주제 사진을 받아온다. 결과가 없으면 None(단색 표지).

    라이선스 판정과 크레딧 문구는 wikimedia 모듈이 API 메타데이터로 만든다.
    """
    try:
        results = [
            image
            for image in wikimedia.search_images(query, session=session)
            if _relevant(image, query)
        ]
        if not results:
            return None
        best = results[0]
        image = wikimedia.download(best, session=session)
    except wikimedia.WikimediaError as exc:
        raise BackgroundError(str(exc)) from exc

    return Background(
        image=cover_crop(image, WIDTH, HEIGHT),
        credits=(best.credit,),
    )


# --- 조합 ----------------------------------------------------------------


def build_background(
    people: list[str],
    bg_query: str,
    session: requests.Session | None = None,
    errors: list[str] | None = None,
    *,
    issue: Issue | None = None,
    headline: str = "",
    client=None,
) -> Background | None:
    """기사 사진 → 인물 → 위키미디어 → Unsplash → None(그래픽 폴백).

    배경은 장식이므로 실패를 삼키고 errors에만 남긴다. 발행을 막지 않는다.

    `issue`는 기존 인자 뒤의 선택 키워드다 — 현 호출부가 전부 `(people, bg_query)`
    위치 인자로 부르고, **AI 브리핑·블로그 요약에는 Issue라는 개념이 없다**
    (이슈 클러스터링은 데일리 경제 브리핑만의 것). None이면 사진 경로를 건너뛴다.
    """
    if issue is not None:
        try:
            photo = photos.pick(issue, headline, client=client, session=session)
            if photo is not None:
                # 뉴스 사진에는 크레딧을 달지 않는다(사용자 결정).
                return Background(image=cover_crop(photo, WIDTH, HEIGHT, top_bias=0.1), credits=())
        except Exception as exc:  # 사진 경로가 터져도 발행은 계속된다
            if errors is not None:
                errors.append(f"기사 사진 실패: {exc}")

    if people:
        try:
            return compose_people(people)
        except BackgroundError as exc:
            if errors is not None:
                errors.append(f"인물 배경 실패: {exc}")

    if not bg_query:
        return None

    for source in (fetch_wikimedia, fetch_unsplash):
        try:
            background = source(bg_query, session=session)
        except BackgroundError as exc:
            if errors is not None:
                errors.append(str(exc))
            continue
        if background is not None:
            return background
    return None


def main() -> int:
    import sys

    query = sys.argv[1] if len(sys.argv) > 1 else "stock market"
    print(f"인물 라이브러리: {sorted(available_people()) or '(비어 있음)'}")

    errors: list[str] = []
    background = build_background([], query, errors=errors)
    for message in errors:
        print(f"  ! {message}")
    if background is None:
        print(f"'{query}': 배경을 찾지 못했습니다 → 단색 표지로 발행됩니다.")
        return 1

    out = PROJECT_ROOT / "out" / "_bg_preview.jpg"
    out.parent.mkdir(exist_ok=True)
    background.image.save(out, "JPEG", quality=90)
    print(f"'{query}' → {out} (크레딧: {', '.join(background.credits)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

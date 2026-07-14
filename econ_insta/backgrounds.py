"""표지 배경 이미지: 인물 콜라주(큐레이션) 또는 Unsplash 주제 사진.

인물 사진은 assets/people/에 라이선스 메타데이터(people.json)와 함께 큐레이션한다.
퍼블릭 도메인·CC 라이선스만 쓰고, CC BY 사진의 크레딧은 캡션에 반드시 표기한다.
합성은 나란히 배치하는 콜라주뿐이다 — 없던 장면을 만들어내는 편집은 하지 않는다.

Unsplash는 UNSPLASH_ACCESS_KEY가 있어야 동작한다. 키가 없거나 검색이 실패하면
None으로 폴백해 표지가 기존 단색 배경으로 나간다. 배경 때문에 발행이 죽으면 안 된다.

CLI:
    python -m econ_insta.backgrounds "semiconductor memory chips"   # 검색 시험
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

from .config import PROJECT_ROOT, _load_dotenv

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
    """인물 1~2명의 초상을 표지 배경으로 배치한다.

    2명이면 좌우 반씩 나란히(뉴스 썸네일식 대립 구도), 1명이면 전면 크롭.
    얼굴이 잘리지 않도록 위쪽을 살려(top_bias 0.1) 자른다.
    """
    meta = available_people()
    unknown = [k for k in keys if k not in meta]
    if unknown:
        raise BackgroundError(f"인물 라이브러리에 없는 키: {unknown} (보유: {sorted(meta)})")
    if not 1 <= len(keys) <= 2:
        raise BackgroundError(f"인물은 1~2명이어야 합니다 (요청 {len(keys)}명).")

    portraits = []
    credits = []
    for key in keys:
        path = PEOPLE_DIR / meta[key]["file"]
        if not path.exists():
            raise BackgroundError(f"인물 사진 파일이 없습니다: {path}")
        portraits.append(Image.open(path))
        credits.append(meta[key]["credit"])

    canvas = Image.new("RGB", (WIDTH, HEIGHT))
    if len(portraits) == 1:
        canvas.paste(cover_crop(portraits[0], WIDTH, HEIGHT, top_bias=0.1), (0, 0))
    else:
        half = WIDTH // 2
        canvas.paste(cover_crop(portraits[0], half, HEIGHT, top_bias=0.1), (0, 0))
        canvas.paste(cover_crop(portraits[1], WIDTH - half, HEIGHT, top_bias=0.1), (half, 0))

    # 중복 크레딧(같은 출처 두 명)은 하나만 남긴다.
    return Background(image=canvas, credits=tuple(dict.fromkeys(credits)))


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


# --- 조합 ----------------------------------------------------------------


def build_background(
    people: list[str],
    bg_query: str,
    session: requests.Session | None = None,
    errors: list[str] | None = None,
) -> Background | None:
    """인물이 있으면 콜라주, 없으면 Unsplash, 그것도 안 되면 None(단색 폴백).

    배경은 장식이므로 실패를 삼키고 errors에만 남긴다. 발행을 막지 않는다.
    """
    if people:
        try:
            return compose_people(people)
        except BackgroundError as exc:
            if errors is not None:
                errors.append(f"인물 콜라주 실패: {exc}")

    if bg_query:
        try:
            return fetch_unsplash(bg_query, session=session)
        except BackgroundError as exc:
            if errors is not None:
                errors.append(str(exc))
    return None


def main() -> int:
    import sys

    query = sys.argv[1] if len(sys.argv) > 1 else "stock market"
    people = sorted(available_people())
    print(f"인물 라이브러리: {people or '(비어 있음)'}")

    background = fetch_unsplash(query)
    if background is None:
        print("Unsplash 결과 없음 — UNSPLASH_ACCESS_KEY가 .env에 있는지 확인하세요.")
        return 1

    out = PROJECT_ROOT / "out" / "_bg_preview.jpg"
    out.parent.mkdir(exist_ok=True)
    background.image.save(out, "JPEG", quality=90)
    print(f"'{query}' → {out} (크레딧: {', '.join(background.credits)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

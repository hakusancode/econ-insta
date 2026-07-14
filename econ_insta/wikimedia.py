"""위키미디어 공용(Commons) 이미지 검색 — 표지 배경과 인물 초상의 공급원.

신문기사 사진(연합·로이터·AP·게티)은 저작권 때문에 절대 쓰지 않는다. 대신 공용에서
**라이선스를 API로 직접 읽어와** 재사용 가능한 것만 고른다. 라이선스를 사람이 추측해
적어넣지 않는 것이 이 모듈의 요점이다 — `extmetadata.License`가 기계가 읽는 슬러그
(`pd`, `cc0`, `cc-by-4.0` …)를 주므로 그대로 판정에 쓴다.

**CC BY-SA는 기본 제외한다.** 동일조건변경허락은 이 사진을 배경으로 깐 카드 전체를
2차적저작물로 보고 같은 라이선스로 풀라고 요구할 여지가 있다. 매일 자동 발행하는
계정에서 그 위험을 지는 건 남는 장사가 아니다. 필요하면 allow_sharealike=True로 연다.

`Restrictions`가 붙은 파일(초상권·상표권 경고)도 거른다. 실제로 파월 사진 중
'personality' 경고가 달린 것이 있었다.

CLI:
    python -m econ_insta.wikimedia search "federal reserve building"
    python -m econ_insta.wikimedia add-person powell "제롬 파월" --search "Jerome Powell" \
        --alias 파월 --alias "연준 의장"
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from html import unescape
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

from .config import PROJECT_ROOT

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "econ-insta/0.1 (github.com/hakusancode/econ-insta)"
TIMEOUT = 30
RETRIES = 3
BACKOFF_SECONDS = 2.0

PEOPLE_DIR = PROJECT_ROOT / "assets" / "people"
PEOPLE_META = PEOPLE_DIR / "people.json"

# 재사용 가능한 라이선스 슬러그. 상업적 이용·개작이 모두 허용되는 것만.
# NC(비영리)·ND(변경금지)는 어느 쪽도 이 프로젝트에서 쓸 수 없다.
PERMISSIVE_LICENSES = frozenset({"pd", "cc0", "cc-by-2.0", "cc-by-2.5", "cc-by-3.0", "cc-by-4.0"})
SHAREALIKE_LICENSES = frozenset({"cc-by-sa-2.0", "cc-by-sa-2.5", "cc-by-sa-3.0", "cc-by-sa-4.0"})

ALLOWED_MIME = frozenset({"image/jpeg", "image/png"})
MIN_WIDTH, MIN_HEIGHT = 640, 640

TARGET_RATIO = 1080 / 1350  # 표지 비율(0.8). 세로 사진일수록 크롭 손실이 적다.


class WikimediaError(RuntimeError):
    """공용 검색·다운로드 실패."""


@dataclass(frozen=True)
class CommonsImage:
    title: str
    """File:... 형식의 공용 파일명."""
    url: str
    """내려받을 이미지 URL (요청한 폭으로 축소된 썸네일)."""
    width: int
    height: int
    license_slug: str
    license_name: str
    artist: str
    descriptionurl: str
    """공용 파일 설명 페이지. 크레딧의 출처 추적용."""

    @property
    def is_public_domain(self) -> bool:
        return self.license_slug in {"pd", "cc0"}

    @property
    def credit(self) -> str:
        """캡션에 넣을 크레딧 한 줄. CC BY 계열은 이걸 빼면 라이선스 위반이다."""
        who = self.artist or "Unknown"
        return f"{who} (Wikimedia Commons, {self.license_name})"


def strip_html(value: str) -> str:
    """extmetadata의 Artist/Credit은 <a> 태그가 섞여 온다. 사람이 읽을 텍스트만 남긴다."""
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _extract(page: dict) -> CommonsImage | None:
    """API 페이지 하나를 CommonsImage로. 형식·크기 요건을 못 맞추면 None."""
    infos = page.get("imageinfo") or []
    if not infos:
        return None
    info = infos[0]
    meta = info.get("extmetadata") or {}

    def field(key: str) -> str:
        return strip_html((meta.get(key) or {}).get("value", ""))

    if info.get("mime") not in ALLOWED_MIME:
        return None
    if info.get("width", 0) < MIN_WIDTH or info.get("height", 0) < MIN_HEIGHT:
        return None
    # 초상권·상표권 경고가 달린 파일은 라이선스와 무관하게 쓰지 않는다.
    if field("Restrictions"):
        return None

    url = info.get("thumburl") or info.get("url")
    if not url:
        return None

    return CommonsImage(
        title=page.get("title", ""),
        url=url,
        width=info.get("thumbwidth") or info.get("width", 0),
        height=info.get("thumbheight") or info.get("height", 0),
        license_slug=(field("License") or "").lower(),
        license_name=field("LicenseShortName") or "Unknown",
        artist=field("Artist"),
        descriptionurl=info.get("descriptionurl", ""),
    )


def _rank(image: CommonsImage) -> tuple:
    """표지에 쓰기 좋은 순서. 퍼블릭 도메인 우선, 그다음 세로 비율에 가까운 것."""
    ratio_gap = abs((image.width / image.height if image.height else 99) - TARGET_RATIO)
    return (not image.is_public_domain, ratio_gap)


def search_images(
    query: str,
    limit: int = 12,
    width: int = 1080,
    allow_sharealike: bool = False,
    session: requests.Session | None = None,
) -> list[CommonsImage]:
    """공용에서 재사용 가능한 이미지를 찾아 표지 적합도 순으로 돌려준다.

    라이선스는 API가 준 슬러그로만 판정한다. 판정할 수 없으면(슬러그가 비었거나
    목록에 없으면) 버린다 — 모르는 라이선스를 낙관하지 않는다.
    """
    allowed = PERMISSIVE_LICENSES | (SHAREALIKE_LICENSES if allow_sharealike else frozenset())
    caller = session or requests.Session()
    try:
        response = caller.get(
            COMMONS_API,
            params={
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": f"filetype:bitmap {query}",
                "gsrnamespace": 6,  # File:
                "gsrlimit": limit,
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "iiurlwidth": width,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        pages = (response.json().get("query") or {}).get("pages") or {}
    except (requests.RequestException, ValueError) as exc:
        raise WikimediaError(f"공용 검색 실패 ({exc})") from exc

    found = []
    for page in pages.values():
        image = _extract(page)
        if image is not None and image.license_slug in allowed:
            found.append(image)
    return sorted(found, key=_rank)


def download(
    image: CommonsImage,
    session: requests.Session | None = None,
    sleep=time.sleep,
) -> Image.Image:
    """이미지를 받아온다. upload.wikimedia.org는 연속 요청에 429를 준다 — 물러섰다 다시 친다.

    실제로 인물 4명을 연달아 등록하다 429를 맞았다. 발행은 하루 한 번이라 평시엔
    안 걸리지만, 재시도 없이 두면 배치 작업이 중간에 죽는다.
    """
    caller = session or requests.Session()
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            response = caller.get(image.url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            if response.status_code == 429:
                last = WikimediaError(f"429 rate limit ({image.title})")
                sleep(BACKOFF_SECONDS * (attempt + 1))
                continue
            response.raise_for_status()
            loaded = Image.open(BytesIO(response.content))
            loaded.load()
            return loaded.convert("RGB")
        except (requests.RequestException, OSError) as exc:
            last = exc
            sleep(BACKOFF_SECONDS * (attempt + 1))

    raise WikimediaError(f"공용 이미지 내려받기 실패 ({image.title}: {last})") from last


# --- 인물 라이브러리 -------------------------------------------------------


def load_people() -> dict[str, dict]:
    if not PEOPLE_META.exists():
        return {}
    return json.loads(PEOPLE_META.read_text(encoding="utf-8"))


def portrait_candidates(
    search: str,
    limit: int = 15,
    session: requests.Session | None = None,
) -> list[CommonsImage]:
    """초상 후보. 세로 사진을 앞에 세우되 가로도 뒤에 남긴다."""
    found = search_images(search, limit=limit, width=1600, session=session)
    return sorted(found, key=lambda c: (c.height < c.width, _rank(c)))


def contact_sheet(
    candidates: list[CommonsImage],
    path: Path,
    session: requests.Session | None = None,
    columns: int = 4,
    cell: int = 280,
    sleep=time.sleep,
) -> Path:
    """후보들을 한 장에 붙여 저장한다 — 사람이 눈으로 고르라고.

    **자동으로 1등을 고르면 안 된다.** 공용 검색은 캐리커처·팬아트·동명이인을 태연히
    상위에 올린다(실제로 머스크 검색 1등이 팬아트 그림이었다). 라이선스는 기계가
    판정할 수 있어도 "이게 그 사람 사진이 맞는가"는 기계가 판정하지 못한다.
    """
    rows = (len(candidates) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell, max(rows, 1) * cell), (24, 24, 28))
    for index, candidate in enumerate(candidates):
        try:
            thumb = download(candidate, session=session, sleep=sleep)
        except WikimediaError:
            continue
        thumb.thumbnail((cell, cell), Image.LANCZOS)
        x = (index % columns) * cell + (cell - thumb.width) // 2
        y = (index // columns) * cell + (cell - thumb.height) // 2
        sheet.paste(thumb, (x, y))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, "JPEG", quality=88)
    return path


def add_person(
    key: str,
    name: str,
    search: str,
    aliases: list[str],
    pick: int = 0,
    session: requests.Session | None = None,
) -> dict:
    """고른 초상을 assets/people/에 넣고 people.json에 등록한다.

    크레딧·라이선스는 API가 준 값을 그대로 적는다. 손으로 쓰지 않는 이유는
    사람이 적으면 틀리기 때문이다 — 틀린 크레딧은 곧 라이선스 위반이다.
    """
    candidates = portrait_candidates(search, session=session)
    if not candidates:
        raise WikimediaError(f"'{search}' 로 쓸 만한 초상을 찾지 못했습니다 (재사용 가능 라이선스 기준).")
    if not 0 <= pick < len(candidates):
        raise WikimediaError(f"--pick {pick} 은 범위를 벗어납니다 (후보 {len(candidates)}개).")
    chosen = candidates[pick]

    PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{key}.jpg"
    download(chosen, session=session).save(PEOPLE_DIR / filename, "JPEG", quality=92)

    entry = {
        "name": name,
        "aliases": aliases or [name],
        "file": filename,
        "credit": chosen.credit,
        "source": chosen.descriptionurl,
        "license": chosen.license_name,
    }
    people = load_people()
    people[key] = entry
    PEOPLE_META.write_text(
        json.dumps(people, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return entry


# --- CLI -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="위키미디어 공용 이미지 검색 / 인물 등록")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="검색 결과와 라이선스를 훑어본다")
    p_search.add_argument("query")
    p_search.add_argument("--sharealike", action="store_true", help="CC BY-SA도 포함(기본 제외)")

    p_cand = sub.add_parser("candidates", help="초상 후보를 번호와 함께 나열하고 대조표를 저장")
    p_cand.add_argument("search")
    p_cand.add_argument("--sheet", default="out/_candidates.jpg", help="대조표 저장 경로")

    p_add = sub.add_parser("add-person", help="고른 초상을 people.json에 등록")
    p_add.add_argument("key", help="모델이 고를 키 (예: powell)")
    p_add.add_argument("name", help="한글 표기 (예: 제롬 파월)")
    p_add.add_argument("--search", required=True, help="공용 검색어 (예: 'Jerome Powell')")
    p_add.add_argument("--alias", action="append", default=[], help="별칭 (반복 지정 가능)")
    p_add.add_argument(
        "--pick",
        type=int,
        default=0,
        help="candidates 로 눈으로 확인한 뒤 그 번호를 지정하십시오. 검색 1등이 "
        "캐리커처인 경우가 실제로 있었습니다.",
    )

    args = parser.parse_args(argv)

    if args.command == "search":
        results = search_images(args.query, allow_sharealike=args.sharealike)
        if not results:
            print(f"'{args.query}': 재사용 가능한 이미지 없음")
            return 1
        for image in results:
            print(f"\n{image.title}")
            print(f"  {image.width}x{image.height}  [{image.license_name}]")
            print(f"  크레딧: {image.credit}")
        return 0

    if args.command == "candidates":
        found = portrait_candidates(args.search)
        if not found:
            print(f"'{args.search}': 재사용 가능한 초상 없음")
            return 1
        for index, image in enumerate(found):
            print(f"[{index}] {image.title}")
            print(f"     {image.width}x{image.height}  [{image.license_name}]  {image.credit}")
        sheet = contact_sheet(found, PROJECT_ROOT / args.sheet)
        print(f"\n대조표: {sheet} — 눈으로 확인한 뒤 add-person --pick <번호>")
        return 0

    entry = add_person(args.key, args.name, args.search, args.alias, pick=args.pick)
    print(f"등록: {args.key} → {entry['name']}")
    print(f"  파일   : assets/people/{entry['file']}")
    print(f"  라이선스: {entry['license']}")
    print(f"  크레딧 : {entry['credit']}")
    print(f"  출처   : {entry['source']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

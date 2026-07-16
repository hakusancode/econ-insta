"""이슈의 기사 사진에서 표지 후보 한 장을 고른다.

RSS `media:content`가 표지의 1순위 소스다. og:image보다 나은 이유는 실측에 있다:
WSJ은 기사 페이지가 403인데 RSS엔 이미지가 그대로 있고, 페이지를 안 가도 되니
빠르고 봇 차단도 없다.

**후보의 관련성은 검색 랭킹이 아니라 편집자가 보장한다** — 그 이슈를 다룬 기사에
매체가 직접 붙인 사진이기 때문이다. 그래서 위키미디어 검색 1등이 팬아트 선화였던
사고가 여기서는 구조적으로 재발하지 않는다. Claude는 신원 확인을 하지 않고
'이미 관련 있는 N장 중 가장 센 컷'만 고른다.

`backgrounds`를 import하지 않는다(순환). 고른 사진을 `Image`로 돌려주면
`backgrounds`가 crop해서 `Background`로 감싼다.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from io import BytesIO

import anthropic
import requests
from PIL import Image, UnidentifiedImageError

from .issues import Issue

YNA_PHOTO_ID = re.compile(r"(P(?:YH|CM)\d{10,})")  # 실측에서 확인된 연합 사진 ID 접두사는 PYH(19자리)와 PCM(17자리)뿐


@dataclass(frozen=True)
class Candidate:
    url: str
    """가장 먼저 본 URL. 정렬 키·플레이스홀더 판정 등 대표값으로 쓴다."""
    sources: frozenset[str]
    """이 사진을 실은 매체들."""
    freq: int
    """등장 횟수. 크로스소스 빈도 신호."""
    urls: tuple[str, ...] = ()
    """같은 사진으로 병합된 모든 원본 URL(등장 순서). 비어 있으면 `url` 하나로 채운다.

    매체마다 URL 형식이 달라 P4 승격 가능 여부가 다르다(매경 재게재본엔 `_P2.`가
    없어 승격이 안 걸린다). 대표 URL 하나만 들고 있으면, 하필 승격 안 되는 쪽이
    먼저 병합됐을 때 승격되는 다른 매체의 URL을 영영 시도하지 못한다
    (4단계 최종 리뷰 #1)."""

    def __post_init__(self) -> None:
        if not self.urls:
            object.__setattr__(self, "urls", (self.url,))


def _photo_key(url: str) -> str:
    """같은 사진을 매체 건너 묶는 키.

    매경은 연합 사진을 `rcv.YNA.20260716.PYH2026071617330001300_R.jpg`로 받아쓴다 —
    파일명에 연합 사진 ID가 그대로 박혀 있고, 연합 원본은 같은 ID의 `_P2.jpg`다(실측).
    ID로 묶으면 '여러 매체가 같은 사진을 골랐다'가 잡힌다.

    ID가 없으면 URL 자체를 키로 써서 병합하지 않는다 — 모르는 형식을 억지로 묶으면
    다른 사진이 한 장으로 뭉개진다.
    """
    match = YNA_PHOTO_ID.search(url)
    return match.group(1) if match else url


def candidates(issue: Issue) -> list[Candidate]:
    """이슈의 기사들에서 사진 후보를 모으고 같은 사진을 병합한다.

    등장 순서를 유지한다(dict 삽입 순서) — 테스트가 결정적이어야 한다.
    병합된 후보가 본 URL은 전부(`urls`) 들고 있는다 — 그래야 P4 승격이 안 되는
    URL이 먼저 병합돼도 나중에 병합된 URL의 승격판을 `usable()`이 시도할 수 있다.
    """
    slots: dict[str, dict] = {}
    for article in issue.articles:
        for url in article.images:
            key = _photo_key(url)
            slot = slots.setdefault(key, {"url": url, "urls": [], "sources": set(), "freq": 0})
            if url not in slot["urls"]:
                slot["urls"].append(url)
            slot["sources"].add(article.source)
            slot["freq"] += 1
    return [
        Candidate(url=s["url"], urls=tuple(s["urls"]), sources=frozenset(s["sources"]), freq=s["freq"])
        for s in slots.values()
    ]


TIMEOUT = 30
RETRIES = 3
MAX_WAIT_SECONDS = 60
USER_AGENT = "econ-insta/0.1 (github.com/hakusancode/econ-insta)"

MIN_SHORT_EDGE = 640
"""1080×1350 표지라 짧은 변이 이보다 작으면 확대할 때 뭉갠다."""
MAX_CANDIDATES = 6
"""Claude에 넘길 상한. 비용 상한이다."""
MAX_DOWNLOAD = 10
"""정렬을 위해 받아볼 상한. 6장을 채우려다 무한정 받지 않는다."""

PLACEHOLDER_PATTERNS = (
    "static.hankyung.com/img/logo/",
    "static.mk.co.kr/facebook_",
    "/logo/",
    "_sns.png",
    "facebook_",
)


def is_placeholder(url: str) -> bool:
    """매체가 이미지 없는 기사에 붙이는 자사 로고·SNS 기본 이미지인가.

    한경 og:image는 전부 `static.hankyung.com/img/logo/logo-news-sns.png`이고
    매경은 이미지가 없으면 `static.mk.co.kr/facebook_mknews.jpg`를 준다(실측).
    **URL이 있다고 다 사진이 아니다** — 안 거르면 표지에 한국경제 로고가
    대문짝만하게 나간다.
    """
    low = url.lower()
    return any(pattern in low for pattern in PLACEHOLDER_PATTERNS)


def _upgrade_yna(url: str) -> str | None:
    """연합 사진의 더 큰 버전 URL. 해당 없으면 None.

    RSS는 `_P2`(작은 것), og:image는 같은 사진의 `_P4`(큰 것)를 준다(실측).
    **URL 문자열 치환으로 만든다 — 기사 페이지를 가져오는 게 아니다.**
    """
    return url.replace("_P2.", "_P4.") if "_P2." in url else None


def _retry_after(response) -> float:
    try:
        return float(response.headers.get("Retry-After", "0"))
    except (TypeError, ValueError):
        return 0.0


def _get(url: str, caller, sleep) -> bytes | None:
    """바이트를 받아온다. 실패는 None — 후보 하나가 죽어도 발행은 계속된다.

    이미지 CDN은 연속 요청에 429 + Retry-After를 준다(위키미디어에서 실제로 맞았다).
    짧은 대기면 물러섰다 다시 치고, 긴 차단이면 기다리지 않고 포기한다.
    """
    for _ in range(RETRIES):
        try:
            response = caller.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if response.status_code == 429:
            wait = _retry_after(response)
            if wait <= 0 or wait > MAX_WAIT_SECONDS:
                return None
            sleep(wait)
            continue
        if response.status_code != 200:
            return None
        return response.content
    return None


def _open(data: bytes) -> Image.Image | None:
    try:
        image = Image.open(BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    return image.convert("RGB")


def _download_best(candidate: Candidate, caller, sleep) -> Image.Image | None:
    """병합된 후보가 본 URL마다 더 큰 버전(P4)을 먼저 시도하고, 전부 안 되면 원본들로 내려간다.

    한 photo ID로 병합된 후보는 매체마다 다른 URL을 들고 있을 수 있다(매경 재게재본에
    연합 원본이 섞이는 식). 대표 URL(`candidate.url`) 하나만 승격 시도하면, 하필
    승격이 안 걸리는 URL이 먼저 병합됐을 때 병합된 다른 URL의 승격판을 아예 못
    가본다(4단계 최종 리뷰 #1). 그래서 모든 URL의 승격판을 먼저 훑고, 그래도 없으면
    각 URL 원본을 등장 순서대로 시도한다.
    """
    for url in candidate.urls:
        upgraded = _upgrade_yna(url)
        if not upgraded:
            continue
        data = _get(upgraded, caller, sleep)
        if data is None:
            continue
        image = _open(data)
        if image is not None:
            return image
    for url in candidate.urls:
        data = _get(url, caller, sleep)
        if data is None:
            continue
        image = _open(data)
        if image is not None:
            return image
    return None


def usable(
    cands: list[Candidate],
    session: requests.Session | None = None,
    sleep=time.sleep,
) -> list[tuple[Candidate, Image.Image]]:
    """플레이스홀더·저해상도를 걸러 상위 MAX_CANDIDATES장을 (빈도, 크기) 순으로.

    **여기서 품질 판정을 하지 않는다** — 확실한 쓰레기만 제거한다. 사옥 사진인지
    인물 사진인지는 URL로 알 수 없다. 그건 Claude가 본다(`pick`).
    """
    caller = session or requests.Session()
    fetched: list[tuple[Candidate, Image.Image]] = []
    for candidate in sorted(cands, key=lambda c: (-c.freq, c.url)):
        if len(fetched) >= MAX_DOWNLOAD:
            break
        if is_placeholder(candidate.url):
            continue
        image = _download_best(candidate, caller, sleep)
        if image is None or min(image.size) < MIN_SHORT_EDGE:
            continue
        fetched.append((candidate, image))
    fetched.sort(key=lambda pair: (-pair[0].freq, -(pair[1].width * pair[1].height)))
    return fetched[:MAX_CANDIDATES]


MODEL = "claude-sonnet-5"
MAX_TOKENS = 1024
EFFORT = "medium"
SEND_MAX_EDGE = 512
"""모델에 보낼 때 줄이는 긴 변. 판정에 이 이상은 필요 없고 비용만 는다."""

PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "pick": {"type": ["integer", "null"], "description": "고른 후보 번호. 없으면 null."},
        "reason": {"type": "string", "description": "한 문장."},
    },
    "required": ["pick", "reason"],
    "additionalProperties": False,
}

SYSTEM = """당신은 인스타그램 경제 카드뉴스의 표지 사진을 고르는 편집자입니다.
표지는 스크롤을 멈추게 하는 유일한 수단입니다. 정보 전달이 아니라 시선 강탈이 목적입니다.

우선순위 (이목 집중 순):
1. 인물 얼굴 — 감정·표정이 강한 컷(놀람·긴장·환호). 얼굴이 크게 잡힌 것.
2. 알아보는 기업 로고.
3. 극적인 실사 — 긴장·규모·움직임이 있는 장면(거래소 객장·시위·공장 라인).

반드시 배제할 것 (사물 설명 사진):
- 사옥·건물 정면, 간판
- 웨이퍼·부품·제품컷
- 스펙 표나 글자가 크게 박힌 전시 보드·차트 캡처
- 밋밋한 증명사진식 인물

이런 것뿐이면 pick을 null로 두십시오. **억지로 고르지 마십시오.**
표지가 사옥 사진으로 나가느니 그래픽으로 나가는 게 낫습니다."""


def _thumb(image: Image.Image) -> bytes:
    small = image.copy()
    small.thumbnail((SEND_MAX_EDGE, SEND_MAX_EDGE), Image.LANCZOS)
    buffer = BytesIO()
    small.save(buffer, "JPEG", quality=80)
    return buffer.getvalue()


def _blocks(pairs: list[tuple[Candidate, Image.Image]], headline: str) -> list[dict]:
    blocks: list[dict] = [
        {
            "type": "text",
            "text": f"오늘의 이슈: {headline}\n\n이 이슈를 다룬 기사들에 실린 사진 "
            f"{len(pairs)}장입니다. 표지로 쓸 한 장을 고르십시오.",
        }
    ]
    for index, (candidate, image) in enumerate(pairs):
        blocks.append(
            {"type": "text", "text": f"{index}번 (매체: {', '.join(sorted(candidate.sources))})"}
        )
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(_thumb(image)).decode("ascii"),
                },
            }
        )
    return blocks


def pick(
    issue: Issue,
    headline: str,
    client: anthropic.Anthropic | None = None,
    session: requests.Session | None = None,
    model: str = MODEL,
    sleep=time.sleep,
) -> Image.Image | None:
    """이슈의 기사 사진 중 표지로 쓸 한 장. 없거나 실패하면 None.

    **실패 시 기계 필터 1등을 자동 채택하지 않는다.** 자동 1등 채택은 정확히
    팬아트 사고의 경로다(공용 검색 1등이 팬아트 선화였고 그대로 등록됐다).
    신뢰 점수를 매기는 주체가 Claude이므로 Claude가 없으면 점수도 없다.
    API가 죽은 날은 표지가 폴백 체인으로 나간다 — 못생겨질 뿐 사고는 안 난다.
    """
    pairs = usable(candidates(issue), session=session, sleep=sleep)
    if not pairs:
        return None

    caller = client or anthropic.Anthropic()
    try:
        response = caller.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            output_config={
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": PICK_SCHEMA},
            },
            messages=[{"role": "user", "content": _blocks(pairs, headline)}],
        )
    except Exception:  # API 장애·인증·네트워크 — 배경 때문에 발행이 죽으면 안 된다
        return None

    if getattr(response, "stop_reason", "") in ("max_tokens", "refusal"):
        return None

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None

    index = payload.get("pick")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(pairs):
        return None
    return pairs[index][1]

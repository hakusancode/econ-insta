"""주간 릴스 대본: 지난 한 주 데일리 브리핑 후보 중 이슈 하나를 골라 짧은 대본을 만든다.

데일리(daily.py)가 매일 남기는 `out/<날짜>-<슬러그>/briefing.json`을 최근 7일치 모아,
Claude에게 가장 화제성 있는 이슈를 고르게 하고(issue_index), 훅 한 줄과 이유 2~3개를
받는다. 수치·한자는 summarizer/factcheck과 같은 방식으로 기계 검증한다.

렌더링·발행은 이 모듈의 책임이 아니다(별도 모듈).

CLI:
    python -m econ_insta.weekly_reel
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import anthropic

from .backgrounds import available_people
from .config import _load_dotenv
from .factcheck import unsupported_amounts
from .renderer import OUTPUT_ROOT
from .summarizer import MAX_TOKENS, MODEL, replace_hanja, residual_hanja

EFFORT = "medium"

TICKERS = {
    "코스피": "^KS11", "코스닥": "^KQ11", "원/달러": "KRW=X", "나스닥": "^IXIC",
    "S&P500": "^GSPC", "WTI": "CL=F", "금": "GC=F", "비트코인": "BTC-USD",
    "삼성전자": "005930.KS", "SK하이닉스": "000660.KS", "현대차": "005380.KS",
    "LG에너지솔루션": "373220.KS", "네이버": "035420.KS", "카카오": "035720.KS",
}
HOOK_MAX = 24            # summarizer HEADLINE_MAX와 동일
REASON_TITLE_MAX = 26
REASON_BODY_MAX = 90
MAX_CANDIDATES = 7

_PEOPLE_HINT = (
    ", ".join(f'"{key}" = {meta["name"]}' for key, meta in sorted(available_people().items()))
    or "(비어 있음 — people은 항상 빈 배열로 두십시오)"
)

SYSTEM = f"""당신은 한국어 경제 인스타그램 주간 릴스의 작가입니다.

지난 한 주(최근 7일) 데일리 경제 브리핑 후보 중 이번 주 릴스로 만들 이슈 하나를 고르고,
짧은 대본을 씁니다.

만드는 법:
- 후보는 **화제성(매체 수·기사 수) 내림차순**으로 제시됩니다. 이번 주를 대표할
  이슈 하나를 고르고, 그 번호를 issue_index에 넣으십시오(프롬프트의 [이슈 N]의 N).
- hook: 영상 첫머리에 뜰 한 줄 훅 카피. {HOOK_MAX}자 이내.
- reasons: "이번 주 이 이슈를 봐야 하는 이유" 2~3개. 각 title은 {REASON_TITLE_MAX}자 이내,
  body는 {REASON_BODY_MAX}자 이내. source에는 후보 자료에 실제로 등장한 매체명만 쓰십시오
  — 지어내지 마십시오.
- ticker_label: 이 이슈와 가장 관련이 깊은 지표를 아래 목록에서 고르십시오. 마땅한 것이
  없으면 "코스피"를 쓰십시오. 목록: {", ".join(sorted(TICKERS))}
- bg_query: 표지 배경 사진을 찾을 영어 검색어 2~4단어. **사진으로 찍을 수 있는 구체적
  대상**을 쓰십시오(기관·건물·시설·장소가 가장 잘 잡힙니다). "inflation" 같은 추상 개념은
  검색이 실패하거나 엉뚱한 사진이 나옵니다.
- people: 이슈의 중심 인물이 아래 초상 라이브러리에 있을 때만 그 키를 넣으십시오
  (최대 2명). 스쳐 지나가는 언급이면 넣지 마십시오. 없으면 빈 배열.
  초상 라이브러리: {_PEOPLE_HINT}

수치·한자 규칙 (기계적으로 검증되며, 위반 시 재생성됩니다):
- hook에는 한자를 쓰지 마십시오. 국가 약칭도 한글로 풀어 쓰십시오(美→미국, 中→중국).
- hook과 reasons의 모든 수치는 제공된 자료(후보 목록)에 있는 값이어야 합니다.
  단위 환산은 괜찮지만 값을 바꾸거나 없는 값을 만들지 마십시오."""

SCHEMA = {
    "type": "object",
    "properties": {
        "issue_index": {"type": "integer"},
        "hook": {"type": "string"},
        "reasons": {"type": "array", "minItems": 2, "maxItems": 3, "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "body": {"type": "string"},
                           "source": {"type": "string"}},
            "required": ["title", "body", "source"], "additionalProperties": False}},
        "ticker_label": {"type": "string", "enum": sorted(TICKERS)},
        "bg_query": {"type": "string"},
        "people": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["issue_index", "hook", "reasons", "ticker_label", "bg_query", "people"],
    "additionalProperties": False,
}


class WeeklyError(RuntimeError):
    """주간 릴스 대본 생성 실패."""


@dataclass(frozen=True)
class WeeklyCandidate:
    date: str
    edition: str
    headline: str
    issue_title: str | None
    sources: list[str]
    article_count: int
    cards: list[dict]


@dataclass(frozen=True)
class Reason:
    title: str
    body: str
    source: str


@dataclass(frozen=True)
class WeeklyScript:
    hook: str
    reasons: list[Reason]
    ticker_label: str
    bg_query: str
    people: tuple[str, ...]
    chosen: WeeklyCandidate


def load_candidates(out_root: Path, today: datetime, days: int = 7) -> list[WeeklyCandidate]:
    """out_root 아래 모든 briefing.json을 읽어 최근 days일치, issue_title이 있는 것만 모은다.

    (매체 수, 기사 수) 내림차순으로 정렬한다 — write_script의 프롬프트도 이 순서를 따른다.
    """
    candidates: list[WeeklyCandidate] = []
    for path in out_root.glob("*/briefing.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("issue_title") is None:
            continue
        date = datetime.strptime(data["date"], "%Y-%m-%d")
        if not (today - date).days < days:
            continue
        candidates.append(WeeklyCandidate(
            date=data["date"],
            edition=data["edition"],
            headline=data["headline"],
            issue_title=data["issue_title"],
            sources=data["sources"],
            article_count=data["article_count"],
            cards=data["cards"],
        ))
    return sorted(candidates, key=lambda c: (len(c.sources), c.article_count), reverse=True)


def _chosen(payload: dict, candidates: list[WeeklyCandidate]) -> WeeklyCandidate:
    """이슈 계약(2026-07-17)과 같은 가드 — bool은 isinstance(int)를 뚫는다.
    주간은 틀린 이슈로 나가느니 스킵이 낫다: 폴백 금지."""
    index = payload.get("issue_index")
    if type(index) is not int or not 1 <= index <= len(candidates):
        raise WeeklyError(f"issue_index가 유효하지 않습니다: {index!r}")
    return candidates[index - 1]


def render_candidate(candidate: WeeklyCandidate, index: int) -> str:
    sources = ", ".join(candidate.sources) or "(매체 없음)"
    card_titles = ", ".join(card["title"] for card in candidate.cards) or "(카드 없음)"
    return (
        f"[이슈 {index}] {candidate.issue_title} / {sources} / "
        f"기사 {candidate.article_count}건 / 카드: {card_titles}"
    )


def build_prompt(candidates: list[WeeklyCandidate]) -> str:
    blocks = "\n".join(render_candidate(c, i) for i, c in enumerate(candidates, 1))
    return (
        f"[이번 주 이슈 후보 {len(candidates)}개 — 화제성(매체 수·기사 수) 내림차순]\n{blocks}\n\n"
        "가장 이번 주를 대표할 이슈 하나를 골라 릴스 대본을 만드십시오."
    )


def _generate(caller, prompt: str) -> tuple[dict, int, int]:
    response = caller.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "max_tokens":
        raise WeeklyError(f"응답이 max_tokens({MAX_TOKENS})에서 잘렸습니다.")
    if response.stop_reason == "refusal":
        raise WeeklyError("모델이 응답을 거부했습니다.")

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise WeeklyError("응답에 텍스트 블록이 없습니다.")
    payload = json.loads(text)
    return payload, response.usage.input_tokens, response.usage.output_tokens


def _clean_texts(payload: dict) -> dict:
    """hook·각 reason의 title/body에 replace_hanja를 적용한다. source는 그대로 둔다."""
    return {
        "hook": replace_hanja(payload["hook"]),
        "reasons": [
            {"title": replace_hanja(r["title"]), "body": replace_hanja(r["body"]), "source": r["source"]}
            for r in payload["reasons"]
        ],
    }


def audit(texts: dict, source: str) -> dict[str, list[str]]:
    """근거 없는 수치·잔여 한자·길이 위반을 찾는다. 키는 'hook' | 'reason:<index>'."""
    problems: dict[str, list[str]] = {}

    hook = texts["hook"]
    bad = unsupported_amounts(hook, source)
    if bad:
        problems["hook"] = bad
    hanja = residual_hanja(hook)
    if hanja:
        problems.setdefault("hook", []).append(
            f"한자 사용 금지 — 렌더 폰트에 글리프가 없어 깨집니다. 한글로 푸십시오: {' '.join(hanja)}"
        )
    if len(hook) > HOOK_MAX:
        problems.setdefault("hook", []).append(f"{len(hook)}자 — {HOOK_MAX}자 이내로 줄이십시오")

    for index, reason in enumerate(texts["reasons"]):
        text = f"{reason['title']} {reason['body']}"
        bad = unsupported_amounts(text, source)
        if bad:
            problems[f"reason:{index}"] = bad
        hanja = residual_hanja(text)
        if hanja:
            problems.setdefault(f"reason:{index}", []).append(
                f"한자 사용 금지 — 렌더 폰트에 글리프가 없어 깨집니다. 한글로 푸십시오: {' '.join(hanja)}"
            )
        if len(reason["title"]) > REASON_TITLE_MAX:
            problems.setdefault(f"reason:{index}", []).append(
                f"title {len(reason['title'])}자 — {REASON_TITLE_MAX}자 이내로 줄이십시오"
            )
        if len(reason["body"]) > REASON_BODY_MAX:
            problems.setdefault(f"reason:{index}", []).append(
                f"body {len(reason['body'])}자 — {REASON_BODY_MAX}자 이내로 줄이십시오"
            )

    return problems


def _describe(problems: dict[str, list[str]]) -> str:
    lines = []
    for key, items in problems.items():
        if key.startswith("reason:"):
            lines.append(f"- {int(key[7:]) + 1}번 이유: {', '.join(items)}")
        else:
            lines.append(f"- {key}: {', '.join(items)}")
    return "\n".join(lines)


def write_script(candidates: list[WeeklyCandidate], client: anthropic.Anthropic | None = None) -> WeeklyScript:
    """생성 → 이슈 선정 → 수치·한자 감사 → (위반 시) 1회 재생성 → 남으면 실패."""
    if not candidates:
        raise WeeklyError("후보가 없습니다.")

    _load_dotenv()
    caller = client or anthropic.Anthropic()
    top = candidates[:MAX_CANDIDATES]
    prompt = build_prompt(top)

    payload, input_tokens, output_tokens = _generate(caller, prompt)
    chosen = _chosen(payload, top)
    texts = _clean_texts(payload)
    problems = audit(texts, prompt)

    if problems:
        retry_prompt = (
            f"{prompt}\n\n[직전 시도의 문제 — 반드시 고칠 것]\n{_describe(problems)}\n"
            "수치는 자료에 있는 값만 쓰고, 한자를 쓰지 마십시오."
        )
        payload, retry_in, retry_out = _generate(caller, retry_prompt)
        input_tokens += retry_in
        output_tokens += retry_out
        chosen = _chosen(payload, top)
        texts = _clean_texts(payload)
        problems = audit(texts, prompt)

    if problems:
        raise WeeklyError(f"수치·한자·길이 검증에 실패했습니다:\n{_describe(problems)}")

    known = available_people()
    people = tuple(p for p in payload["people"] if p in known)[:2]

    return WeeklyScript(
        hook=texts["hook"],
        reasons=[Reason(**r) for r in texts["reasons"]],
        ticker_label=payload["ticker_label"],
        bg_query=str(payload.get("bg_query") or "").strip(),
        people=people,
        chosen=chosen,
    )


def main() -> int:
    candidates = load_candidates(OUTPUT_ROOT, datetime.now())
    print(f"후보 {len(candidates)}건")
    for i, c in enumerate(candidates, 1):
        print(f"  [{i}] {c.issue_title}  (매체 {len(c.sources)}곳, 기사 {c.article_count}건)")

    try:
        script = write_script(candidates)
    except WeeklyError as exc:
        print(f"대본 생성 실패: {exc}")
        return 1

    print(f"\n■ 훅: {script.hook}")
    print(f"  이슈: {script.chosen.issue_title} ({script.chosen.date})")
    print(f"  티커: {script.ticker_label}")
    print(f"  배경: {script.bg_query} (인물: {list(script.people) or '없음'})")
    for i, reason in enumerate(script.reasons, 1):
        print(f"  {i}. {reason.title} [{reason.source}]")
        print(f"     {reason.body}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

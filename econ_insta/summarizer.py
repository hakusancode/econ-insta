"""수집한 기사·지표를 카드뉴스용 한국어 브리핑으로 요약한다 (Claude API).

저작권 원칙: 기사 본문·요약문을 그대로 옮기지 않는다. 사실만 추출해 자체 문장으로 다시 쓰고
출처 매체명을 반드시 표기한다. 영문 매체(WSJ·The Economist)는 한국어로 옮긴다.

구조화 출력(output_config.format)으로 JSON 스키마를 강제하므로 파싱이 실패하지 않는다.

CLI:
    python -m econ_insta.summarizer
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import anthropic

from .collector import Article, DailyBrief, Quote, collect
from .config import _load_dotenv
from .factcheck import has_digits, unsupported_amounts, wrong_won_direction

MODEL = "claude-sonnet-5"
MAX_TOKENS = 8000
EFFORT = "medium"

MIN_CARDS = 3
MAX_CARDS = 5

# 카드에 들어가는 글자 수 상한. 스키마로는 강제할 수 없어(JSON Schema의 maxLength 미지원)
# 프롬프트로 지시하고 코드에서 검증한다.
HEADLINE_MAX = 24
CARD_TITLE_MAX = 30
CARD_BODY_MAX = 120

SYSTEM = f"""당신은 한국어 경제 카드뉴스의 에디터입니다.

주어진 기사 목록과 시장지표로 오늘의 데일리 경제 브리핑을 만듭니다.

저작권 원칙 (반드시 지킬 것):
- 기사의 제목이나 요약문을 그대로 옮기지 마십시오. 사실만 추출해 완전히 새로운 문장으로 다시 쓰십시오.
- 각 카드에는 출처 매체명을 반드시 남기십시오.
- 영문 기사는 자연스러운 한국어로 옮기되, 직역투를 피하십시오.

사실 정확성 (반드시 지킬 것):
- has_body가 false인 기사는 제목만 제공된 것입니다. 제목이 명시한 사실만 쓰고,
  본문에 있었을 법한 수치·배경·인용을 추측해 채우지 마십시오.
- 어떤 경우에도 주어진 자료에 없는 수치, 인물, 발언을 만들어내지 마십시오.
- 확신이 서지 않는 기사는 카드로 만들지 말고 건너뛰십시오.
- 원/달러 환율이 **오르면 원화 약세**, 내리면 원화 강세입니다. 지표의 부호를 그대로
  "원화 강세/약세"로 옮기지 마십시오. 방향을 뒤집어 쓰는 실수가 실제로 나왔습니다.

수치 규칙 (기계적으로 검증되며, 위반 시 카드가 폐기됩니다):
- headline과 indicator_note에는 숫자를 단 하나도 쓰지 마십시오. 지표 수치는 카드 이미지에
  코드가 직접 새기므로 문장에서 반복할 필요가 없습니다. 흐름을 말로 서술하십시오.
- 카드 body의 모든 수치는 제공된 자료에 있는 값이어야 합니다. 단위 환산(예: $26.51 billion
  → 265억 달러)은 괜찮지만, 값을 바꾸거나(41조 → 40조) 없는 값을 만들지 마십시오.

편집 기준:
- 카드는 {MIN_CARDS}~{MAX_CARDS}장. 거시경제·시장·산업에서 파급력이 큰 것부터 고르십시오.
- 특정 기업 홍보성 기사(후원, 협약, 봉사, 수상)는 제외하십시오.
- headline은 {HEADLINE_MAX}자 이내, 카드 title은 {CARD_TITLE_MAX}자 이내,
  body는 {CARD_BODY_MAX}자 이내의 2~3문장.
- indicator_note는 오늘 지표 흐름을 한 문장으로 짚는 코멘트입니다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "표지 카드 제목"},
        "indicator_note": {"type": "string", "description": "지표 카드에 얹을 한 문장 코멘트"},
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "source": {"type": "string", "description": "출처 매체명"},
                },
                "required": ["title", "body", "source"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "indicator_note", "cards"],
    "additionalProperties": False,
}


class SummarizeError(RuntimeError):
    """요약 실패."""


@dataclass(frozen=True)
class Card:
    title: str
    body: str
    source: str


@dataclass(frozen=True)
class Briefing:
    headline: str
    indicator_note: str
    cards: list[Card]
    quotes: list[Quote]
    input_tokens: int = 0
    output_tokens: int = 0
    dropped_cards: int = 0
    """수치 검증에 걸려 폐기된 카드 수. 0이 아니면 로그로 남겨야 한다."""

    @property
    def cost_usd(self) -> float:
        """Sonnet 5 도입가 기준 ($2/$10 per 1M). 사고 토큰도 출력으로 과금된다."""
        return self.input_tokens / 1e6 * 2 + self.output_tokens / 1e6 * 10


def render_article(article: Article, index: int) -> str:
    has_body = bool(article.summary)
    lines = [
        f"[{index}] 출처: {article.source} (언어: {article.language})",
        f"    제목: {article.title}",
        f"    has_body: {str(has_body).lower()}",
    ]
    if has_body:
        lines.append(f"    본문요약: {article.summary}")
    return "\n".join(lines)


def build_prompt(brief: DailyBrief) -> str:
    if not brief.articles:
        raise SummarizeError("요약할 기사가 없습니다.")

    quotes = "\n".join(
        f"  {q.name}: {q.price_text} ({q.change_text})" for q in brief.quotes
    ) or "  (지표 수집 실패)"
    articles = "\n".join(render_article(a, i) for i, a in enumerate(brief.articles, 1))

    return (
        f"오늘 날짜: {brief.collected_at:%Y년 %m월 %d일}\n\n"
        f"[시장지표]\n{quotes}\n\n"
        f"[기사 후보 {len(brief.articles)}건]\n{articles}\n\n"
        "위 자료로 오늘의 브리핑을 만드십시오."
    )


def _validate(payload: dict) -> None:
    """스키마로 강제할 수 없는 제약(개수·길이)을 확인한다."""
    cards = payload["cards"]
    if not MIN_CARDS <= len(cards) <= MAX_CARDS:
        raise SummarizeError(f"카드가 {len(cards)}장입니다 ({MIN_CARDS}~{MAX_CARDS}장이어야 함).")

    if len(payload["headline"]) > HEADLINE_MAX:
        raise SummarizeError(f"headline이 {len(payload['headline'])}자로 한도({HEADLINE_MAX}자)를 넘습니다.")

    for i, card in enumerate(cards, 1):
        if len(card["title"]) > CARD_TITLE_MAX:
            raise SummarizeError(f"{i}번 카드 title이 {len(card['title'])}자로 한도를 넘습니다.")
        if len(card["body"]) > CARD_BODY_MAX:
            raise SummarizeError(f"{i}번 카드 body가 {len(card['body'])}자로 한도를 넘습니다.")
        if not card["source"].strip():
            raise SummarizeError(f"{i}번 카드에 출처가 없습니다.")


def usdkrw_change(quotes: list[Quote]) -> float | None:
    return next((q.change_pct for q in quotes if q.symbol == "KRW=X"), None)


def audit(payload: dict, source: str, quotes: list[Quote] | None = None) -> dict[str, list[str]]:
    """근거 없는 수치와 뒤집힌 환율 방향을 찾는다. 키는 'headline' | 'indicator_note' | 'card:<index>'."""
    problems: dict[str, list[str]] = {}

    for field in ("headline", "indicator_note"):
        if has_digits(payload[field]):
            problems[field] = ["숫자 사용 금지"]

    # 지표에서 파생된 문장만 검사한다. 카드 본문에는 전망·인용이 섞여 오탐이 난다.
    reversed_fx = wrong_won_direction(payload["indicator_note"], usdkrw_change(quotes or []))
    if reversed_fx:
        problems.setdefault("indicator_note", []).append(reversed_fx)

    for index, card in enumerate(payload["cards"]):
        bad = unsupported_amounts(f"{card['title']} {card['body']}", source)
        if bad:
            problems[f"card:{index}"] = bad

    return problems


def _describe(problems: dict[str, list[str]]) -> str:
    lines = []
    for key, items in problems.items():
        if key.startswith("card:"):
            lines.append(f"- {int(key[5:]) + 1}번 카드: 자료에 없는 수치 {', '.join(items)}")
        else:
            lines.append(f"- {key}: {'; '.join(items)}")
    return "\n".join(lines)


def _generate(caller, model: str, prompt: str) -> tuple[dict, int, int]:
    response = caller.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )

    if response.stop_reason == "max_tokens":
        raise SummarizeError(f"응답이 max_tokens({MAX_TOKENS})에서 잘렸습니다.")
    if response.stop_reason == "refusal":
        raise SummarizeError("모델이 응답을 거부했습니다.")

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise SummarizeError("응답에 텍스트 블록이 없습니다.")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:  # 구조화 출력이 보장하지만 방어적으로 둔다
        raise SummarizeError(f"JSON 파싱 실패: {exc}") from exc

    _validate(payload)
    return payload, response.usage.input_tokens, response.usage.output_tokens


def summarize(
    brief: DailyBrief,
    client: anthropic.Anthropic | None = None,
    model: str = MODEL,
) -> Briefing:
    """생성 → 수치 감사 → (위반 시) 1회 재생성 → 남은 위반 카드는 폐기."""
    _load_dotenv()
    caller = client or anthropic.Anthropic()
    prompt = build_prompt(brief)

    payload, input_tokens, output_tokens = _generate(caller, model, prompt)
    problems = audit(payload, prompt, brief.quotes)

    if problems:
        retry_prompt = (
            f"{prompt}\n\n"
            "[직전 시도의 문제 — 반드시 고칠 것]\n"
            f"{_describe(problems)}\n"
            "수치는 자료에 있는 값만 쓰고, headline과 indicator_note에는 숫자를 쓰지 마십시오."
        )
        payload, retry_in, retry_out = _generate(caller, model, retry_prompt)
        input_tokens += retry_in
        output_tokens += retry_out
        problems = audit(payload, prompt, brief.quotes)

    # 재시도 후에도 헤드라인·지표 코멘트가 틀렸다면 발행하지 않는다. 카드는 버릴 수 있지만
    # 표지 문구는 대체할 방법이 없다.
    for field in ("headline", "indicator_note"):
        if field in problems:
            raise SummarizeError(f"{field}에 근거 없는 숫자가 남았습니다: {payload[field]!r}")

    dropped = {int(key[5:]) for key in problems}
    cards = [Card(**c) for i, c in enumerate(payload["cards"]) if i not in dropped]

    if len(cards) < MIN_CARDS:
        raise SummarizeError(
            f"수치 검증 후 카드가 {len(cards)}장뿐입니다 (최소 {MIN_CARDS}장). 폐기된 카드 {len(dropped)}장."
        )

    return Briefing(
        headline=payload["headline"],
        indicator_note=payload["indicator_note"],
        cards=cards,
        quotes=brief.quotes,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        dropped_cards=len(dropped),
    )


def main() -> int:
    brief = collect()
    if brief.errors:
        print(f"수집 경고 {len(brief.errors)}건")
        for message in brief.errors:
            print(f"  - {message}")
        print()

    try:
        briefing = summarize(brief)
    except (SummarizeError, anthropic.APIStatusError) as exc:
        print(f"요약 실패: {exc}")
        return 1

    print(f"■ {briefing.headline}\n")
    for i, card in enumerate(briefing.cards, 1):
        print(f"{i}. {card.title}  [{card.source}]")
        print(f"   {card.body}\n")

    print(f"지표 코멘트: {briefing.indicator_note}")
    for quote in briefing.quotes:
        print(f"  {quote.name:<8} {quote.price_text:>12}  {quote.change_text:>8}")

    if briefing.dropped_cards:
        print(f"\n[경고] 수치 검증에 걸려 카드 {briefing.dropped_cards}장을 폐기했습니다.")

    print(
        f"\n토큰: 입력 {briefing.input_tokens}, 출력 {briefing.output_tokens} "
        f"(비용 약 ${briefing.cost_usd:.4f})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

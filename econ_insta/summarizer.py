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


def summarize(
    brief: DailyBrief,
    client: anthropic.Anthropic | None = None,
    model: str = MODEL,
) -> Briefing:
    _load_dotenv()
    caller = client or anthropic.Anthropic()

    response = caller.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": build_prompt(brief)}],
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

    return Briefing(
        headline=payload["headline"],
        indicator_note=payload["indicator_note"],
        cards=[Card(**c) for c in payload["cards"]],
        quotes=brief.quotes,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
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

    print(
        f"\n토큰: 입력 {briefing.input_tokens}, 출력 {briefing.output_tokens} "
        f"(비용 약 ${briefing.cost_usd:.4f})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

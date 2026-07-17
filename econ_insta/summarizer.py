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
from .issues import Issue, rank_issues

MODEL = "claude-sonnet-5"
MAX_TOKENS = 8000
EFFORT = "medium"

MIN_CARDS = 3
MAX_CARDS = 5

# 카드에 들어가는 글자 수 상한. 스키마로는 강제할 수 없어(JSON Schema의 maxLength 미지원)
# 프롬프트로 지시하고 코드에서 검증한다.
HEADLINE_MAX = 24
CARD_TITLE_MAX = 30
CARD_BODY_MAX = 160

PROMPT_ISSUES = 10
"""모델에 보일 이슈 수. collect()가 기사 전량을 싣게 되면서(스펙 §4.2) 자르는 지점이
여기로 옮겨왔다. 상위 10개면 매체 2곳 이상짜리 진짜 이슈는 확실히 들어온다."""

PROMPT_ARTICLES = 5
"""이슈당 모델에 보일 기사 수. 카드 4장 서사에 충분하다.

자르는 것은 프롬프트 표시분뿐이다 — Issue.articles는 온전히 남긴다.
photos.candidates(issue)가 표지 사진 후보를 그 전 기사에서 뽑기 때문이다(스펙 §4.4).
"""

SYSTEM = f"""당신은 한국어 경제 카드뉴스의 에디터입니다.

주어진 기사 목록과 시장지표로 오늘의 데일리 경제 브리핑을 만듭니다.

오늘의 후보 이슈는 **여러 매체가 함께 다룬 순서(인기도)**로 정렬돼 제시됩니다.

만드는 법:
- **가장 화제성이 큰 이슈 하나**를 고르십시오(대개 첫 번째 후보). 그 이슈 하나만 다룹니다.
- 고른 이슈의 번호를 `issue_index`에 넣으십시오(프롬프트의 `[이슈 N]`의 N). 표지 사진을 그 이슈의 기사에서 찾기 때문에, 번호가 틀리면 표지에 엉뚱한 사진이 깔립니다.
- **여러 이슈를 한 게시물에 섞지 마십시오.** 표지와 모든 카드가 같은 사건이어야 합니다.
- 고른 이슈에 묶인 기사들을 재료로, 표지=훅 한 문장, 카드=서사로 풀어냅니다:
  · 카드1 무슨 일: 핵심 사실(무엇이·얼마나)
  · 카드2 왜/배경: 맥락
  · 카드3 반응/파장: 시장·업계 반응
  · 카드4 앞으로: 다음 관전 포인트(마무리 한 방)
  각 카드 role에 국면 라벨(무슨 일|왜|반응|앞으로)을 넣으십시오.
- headline은 밋밋한 요약이 아니라 **스크롤을 멈추게 하는 훅 카피**로. (숫자 금지는 유지)
- 한 이슈로 {MIN_CARDS}장을 채울 재료가 부족하면, 다음 후보 이슈로 바꾸십시오. 억지로 추측해 채우지 마십시오.

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
- indicator_note에는 숫자를 단 하나도 쓰지 마십시오. 지표 수치는 카드 이미지에
  코드가 직접 새기므로 문장에서 반복할 필요가 없습니다. 흐름을 말로 서술하십시오.
- headline에는 **자료에 있는 수치만** 쓸 수 있습니다. 그 뉴스의 핵심이 수치라면 쓰십시오
  (예: "3년 6개월 만의 인상"). 다만 자료에 없는 값을 만들거나 바꾸면 카드가 폐기됩니다.
- 카드 body의 모든 수치는 제공된 자료에 있는 값이어야 합니다. 단위 환산(예: $26.51 billion
  → 265억 달러)은 괜찮지만, 값을 바꾸거나(41조 → 40조) 없는 값을 만들지 마십시오.

편집 기준:
- 카드는 {MIN_CARDS}~{MAX_CARDS}장.
- 특정 기업 홍보성 기사(후원, 협약, 봉사, 수상)는 제외하십시오.
- headline은 {HEADLINE_MAX}자 이내, 카드 title은 {CARD_TITLE_MAX}자 이내,
  body는 {CARD_BODY_MAX}자 이내의 2~3문장.
- indicator_note는 오늘 지표 흐름을 한 문장으로 짚는 코멘트입니다.
- bg_query: 표지 배경 사진을 찾을 **영어** 검색어 2~4단어. 항상 채우십시오.
  기사에 실린 사진을 못 구했을 때 이 검색어로 표지 사진을 찾습니다.
  **사진으로 찍을 수 있는 구체적 대상**을 쓰십시오. 두 갈래가 잘 잡힙니다:
  · 감정이 드러난 사람 — "stressed trader screen", "worried investor head in hands",
    "anxious businessman office". 표지에서 가장 센 컷이고 스크롤을 멈추게 합니다.
  · 기관·건물·시설·장소 — "Bank of Korea building", "New York Stock Exchange",
    "semiconductor fabrication plant", "container ship port".
  "inflation", "market anxiety", "artificial intelligence" 같은 **추상 개념은 쓰지 마십시오** —
  검색이 실패하거나 엉뚱한 사진이 나옵니다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "표지 카드 제목"},
        "indicator_note": {"type": "string", "description": "지표 카드에 얹을 한 문장 코멘트"},
        "issue_index": {"type": "integer", "description": "당신이 고른 이슈의 번호(프롬프트의 [이슈 N])"},
        "bg_query": {"type": "string", "description": "표지 배경 사진 검색용 영어 키워드 2~4단어"},
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "source": {"type": "string", "description": "출처 매체명(복수면 대표 1곳 또는 'A·B')"},
                    "role": {"type": "string", "description": "서사 국면: 무슨 일 | 왜 | 반응 | 앞으로 (선택)"},
                },
                "required": ["title", "body", "source"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "indicator_note", "issue_index", "bg_query", "cards"],
    "additionalProperties": False,
}


class SummarizeError(RuntimeError):
    """요약 실패."""


@dataclass(frozen=True)
class Card:
    title: str
    body: str
    source: str
    role: str | None = None
    """서사 국면 라벨(무슨 일/왜/반응/앞으로). 없어도 된다."""


@dataclass(frozen=True)
class Briefing:
    headline: str
    indicator_note: str
    cards: list[Card]
    quotes: list[Quote]
    issue: Issue | None = None
    """모델이 고른 이슈. None이면 표지 사진을 조달할 대상이 없어 그래픽으로 나간다."""
    bg_query: str = ""
    """기사 사진을 못 구했을 때 위키미디어·Unsplash에서 표지를 찾을 영어 검색어.

    이게 없으면 build_background가 `if not bg_query: return None`으로 스톡 사진 단계를
    통째로 건너뛴다(backgrounds.py). 데일리는 people도 없어서, 이 필드가 비면 기사 사진이
    실패하는 순간 표지가 무조건 그래픽이 된다 — 2026-07-16·17 이틀 연속 그렇게 나갔다.
    """
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


def render_issue(issue: Issue, index: int) -> str:
    sources = ", ".join(sorted(issue.sources))
    lines = [f"[이슈 {index}] 매체 {len(issue.sources)}곳({sources}), 기사 {len(issue.articles)}건"]
    for article in issue.articles[:PROMPT_ARTICLES]:
        has_body = bool(article.summary)
        lines.append(f"  - ({article.source}) {article.title}  [본문:{'있음' if has_body else '없음'}]")
        if has_body:
            lines.append(f"      {article.summary}")
    return "\n".join(lines)


def build_prompt(brief: DailyBrief, issues: list[Issue]) -> str:
    if not brief.articles:
        raise SummarizeError("요약할 기사가 없습니다.")

    quotes = "\n".join(
        f"  {q.name}: {q.price_text} ({q.change_text})" for q in brief.quotes
    ) or "  (지표 수집 실패)"
    blocks = "\n\n".join(render_issue(iss, i) for i, iss in enumerate(issues, 1))

    return (
        f"오늘 날짜: {brief.collected_at:%Y년 %m월 %d일}\n\n"
        f"[시장지표]\n{quotes}\n\n"
        f"[후보 이슈 {len(issues)}개 — 화제성(매체 수) 내림차순]\n{blocks}\n\n"
        "가장 화제성이 큰 이슈 하나를 골라 단일 이슈 브리핑을 만드십시오."
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

    # indicator_note는 숫자 전면 금지다. 지표 수치는 지표 카드에 코드가 직접 새기므로
    # 문장에서 반복하면 같은 값이 두 번 나온다.
    if has_digits(payload["indicator_note"]):
        problems["indicator_note"] = ["숫자 사용 금지"]

    # headline은 카드 본문과 같은 기준을 쓴다: 자료에 있는 값이면 허용, 지어낸 값은 차단.
    # 전면 금지였는데 과잉이었다 — 2026-07-16·17에 네 번 연속으로 실행을 죽였다
    # ("7조달러", "7천피", "3년 6개월 만에", "3년반 만에"). 전부 기사에 있는 사실이고
    # 지표 수치가 아니라 그 뉴스의 핵심이었다. 표지는 지표 카드와 값이 겹치지 않는다.
    bad_headline = unsupported_amounts(payload["headline"], source)
    if bad_headline:
        problems["headline"] = bad_headline

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


def _chosen_issue(payload: dict, issues: list[Issue]) -> Issue | None:
    """모델이 고른 이슈. 번호가 없거나 범위 밖이면 None(표지는 그래픽으로 저하).

    issues[0]으로 폴백하지 않는다 — 모델의 선택과 갈리는 것이 바로 이 필드가 생긴 이유다.
    2026-07-16 실측: 모델은 코스피를 골랐는데 rank_issues()[0]은 광고성 리스티클이었다.

    payload["issue_index"]가 아니라 .get()인 것도 의도적이다. 스키마 required가
    보장하지만, 만에 하나 없을 때 KeyError로 발행을 죽이는 건 장식 하나 때문에
    게시물을 버리는 것이다. 없음·범위밖·타입이상을 전부 같은 저하 경로로 모은다.
    """
    index = payload.get("issue_index")
    # isinstance(index, int)이면 bool을 통과시킨다 — bool은 int의 서브클래스라
    # isinstance(True, int)가 True이고, True == 1이라 issue_index=True가
    # issues[0]을 돌려준다(이 함수가 막으려는 바로 그 폴백). type(index) is not int로
    # bool과 int 서브클래스를 전부 배제한다.
    if type(index) is not int or not 1 <= index <= len(issues):
        return None
    return issues[index - 1]


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
    issues = rank_issues(brief.articles)[:PROMPT_ISSUES]
    prompt = build_prompt(brief, issues)

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
    cards = [
        Card(title=c["title"], body=c["body"], source=c["source"], role=c.get("role"))
        for i, c in enumerate(payload["cards"]) if i not in dropped
    ]

    if len(cards) < MIN_CARDS:
        raise SummarizeError(
            f"수치 검증 후 카드가 {len(cards)}장뿐입니다 (최소 {MIN_CARDS}장). 폐기된 카드 {len(dropped)}장."
        )

    return Briefing(
        headline=payload["headline"],
        indicator_note=payload["indicator_note"],
        cards=cards,
        quotes=brief.quotes,
        issue=_chosen_issue(payload, issues),
        bg_query=str(payload.get("bg_query") or "").strip(),
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

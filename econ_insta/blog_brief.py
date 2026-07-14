"""블로그 글(매크로 비욘드)을 요약하고 관련기사를 골라 카드뉴스로 만든다.

발행 형태는 블로거 승인 조건 그대로: 요약 + 출처 표기 + 원문 링크.
글은 분석·견해(창작물)이므로 요약문은 견해를 필자에게 귀속시켜 쓰고("필자는 ~라고 본다"),
문장을 그대로 옮기지 않는다. 수치는 summarizer와 같은 방식으로 기계 검증한다.

CLI:
    python -m econ_insta.blog_brief                            # 요약 + 카드 렌더 (발행 안 함)
    python -m econ_insta.blog_brief --publish out/<날짜>-blog   # 렌더된 카드를 발행
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import anthropic
import requests

from .blog import AUTHOR, BLOG_NAME, BlogPost, latest_market_post
from .collector import Article, collect_articles
from .config import PROJECT_ROOT, _load_dotenv
from .factcheck import has_digits, unsupported_amounts
from .renderer import OUTPUT_ROOT, FontSet, RenderError, render_card, render_cover
from .summarizer import (
    CARD_BODY_MAX,
    CARD_TITLE_MAX,
    HEADLINE_MAX,
    MAX_TOKENS,
    MODEL,
    Card,
    SummarizeError,
    render_article,
)

EFFORT = "medium"

MIN_BLOG_CARDS = 2
MAX_BLOG_CARDS = 3
MAX_RELATED_CARDS = 2
MAX_HASHTAGS = 6

SYSTEM = f"""당신은 한국어 경제 카드뉴스의 에디터입니다.

경제 블로그 「{BLOG_NAME}」({AUTHOR})의 글 한 편을 원작자 승인 하에 요약해 카드뉴스로 만들고,
오늘 기사 후보 중 이 글과 실질적으로 관련된 기사를 골라 함께 소개합니다.

블로그 요약 원칙 (반드시 지킬 것):
- 이 글은 사실 보도가 아니라 필자의 분석·견해입니다. 전망과 판단은 반드시 필자에게
  귀속시키십시오 ("필자는 ~라고 본다", "글은 ~라고 진단한다"). 견해를 사실처럼 단정하지 마십시오.
- 문장을 그대로 옮기지 말고 완전히 새로운 문장으로 다시 쓰십시오.
- blog_cards는 {MIN_BLOG_CARDS}~{MAX_BLOG_CARDS}장. 글의 논지 전개 순서를 따라
  핵심 주장만 담으십시오.

관련기사 원칙:
- related_cards는 0~{MAX_RELATED_CARDS}장. 기사 후보 중 블로그 글의 주제와 실질적으로
  맞닿은 기사만 고르십시오. 억지로 채우지 말고, 없으면 빈 배열로 두십시오.
- 기업 홍보성 기사(후원, 협약, 봉사, 수상, 인사)는 제외하십시오.
- body는 기사가 명시한 사실만 재작성하십시오. has_body가 false인 기사는 제목의 사실만 쓰십시오.
- source에는 출처 매체명을 그대로 쓰십시오.

수치 규칙 (기계적으로 검증되며, 위반 시 카드가 폐기됩니다):
- headline에는 숫자를 쓰지 마십시오.
- 카드의 모든 수치는 제공된 자료(블로그 전문·기사 목록)에 있는 값이어야 합니다.
  단위 환산은 괜찮지만 값을 바꾸거나 없는 값을 만들지 마십시오.

형식:
- headline은 표지 제목, {HEADLINE_MAX}자 이내. 글의 핵심 통찰을 담으십시오.
- 카드 title은 {CARD_TITLE_MAX}자 이내, body는 {CARD_BODY_MAX}자 이내의 2~3문장.
- caption_hook은 인스타 캡션 첫머리에 쓸 1~2문장입니다. 숫자 없이 쓰십시오.
- hashtags는 글 주제를 나타내는 한국어 해시태그 단어 3~{MAX_HASHTAGS}개('#' 없이)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "표지 카드 제목"},
        "blog_cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
                "required": ["title", "body"],
                "additionalProperties": False,
            },
        },
        "related_cards": {
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
        "caption_hook": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "blog_cards", "related_cards", "caption_hook", "hashtags"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class BlogBriefing:
    post: BlogPost
    headline: str
    blog_cards: list[Card]
    related_cards: list[Card]
    caption_hook: str
    hashtags: list[str]
    input_tokens: int = 0
    output_tokens: int = 0
    dropped_cards: int = 0

    @property
    def cards(self) -> list[Card]:
        return self.blog_cards + self.related_cards


def build_prompt(post: BlogPost, articles: list[Article]) -> str:
    article_lines = "\n".join(render_article(a, i) for i, a in enumerate(articles, 1)) or "(없음)"
    return (
        f"[블로그 글]\n"
        f"블로그: {BLOG_NAME} ({AUTHOR})\n"
        f"제목: {post.title}\n"
        f"발행: {post.published:%Y년 %m월 %d일}\n"
        f"전문:\n{post.body}\n\n"
        f"[오늘 기사 후보 {len(articles)}건]\n{article_lines}\n\n"
        "위 자료로 블로그 요약 카드뉴스를 만드십시오."
    )


def _validate(payload: dict) -> None:
    if not MIN_BLOG_CARDS <= len(payload["blog_cards"]) <= MAX_BLOG_CARDS:
        raise SummarizeError(
            f"blog_cards가 {len(payload['blog_cards'])}장입니다 ({MIN_BLOG_CARDS}~{MAX_BLOG_CARDS}장이어야 함)."
        )
    if len(payload["related_cards"]) > MAX_RELATED_CARDS:
        raise SummarizeError(f"related_cards가 {len(payload['related_cards'])}장입니다 (최대 {MAX_RELATED_CARDS}장).")
    if len(payload["headline"]) > HEADLINE_MAX:
        raise SummarizeError(f"headline이 {len(payload['headline'])}자로 한도({HEADLINE_MAX}자)를 넘습니다.")

    for kind in ("blog_cards", "related_cards"):
        for i, card in enumerate(payload[kind], 1):
            if len(card["title"]) > CARD_TITLE_MAX:
                raise SummarizeError(f"{kind} {i}번 title이 {len(card['title'])}자로 한도를 넘습니다.")
            if len(card["body"]) > CARD_BODY_MAX:
                raise SummarizeError(f"{kind} {i}번 body가 {len(card['body'])}자로 한도를 넘습니다.")
            if kind == "related_cards" and not card["source"].strip():
                raise SummarizeError(f"related_cards {i}번에 출처가 없습니다.")


def audit(payload: dict, source: str) -> dict[str, list[str]]:
    """근거 없는 수치를 찾는다. 키는 'headline' | 'caption_hook' | '<kind>:<index>'."""
    problems: dict[str, list[str]] = {}
    for field in ("headline", "caption_hook"):
        if has_digits(payload[field]):
            problems[field] = ["숫자 사용 금지"]
    for kind in ("blog_cards", "related_cards"):
        for index, card in enumerate(payload[kind]):
            bad = unsupported_amounts(f"{card['title']} {card['body']}", source)
            if bad:
                problems[f"{kind}:{index}"] = bad
    return problems


def _describe(problems: dict[str, list[str]]) -> str:
    return "\n".join(f"- {key}: {', '.join(items)}" for key, items in problems.items())


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
        raise SummarizeError(f"응답이 max_tokens({MAX_TOKENS})에서 잘렸습니다.")
    if response.stop_reason == "refusal":
        raise SummarizeError("모델이 응답을 거부했습니다.")

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise SummarizeError("응답에 텍스트 블록이 없습니다.")
    payload = json.loads(text)
    _validate(payload)
    return payload, response.usage.input_tokens, response.usage.output_tokens


def summarize_blog(
    post: BlogPost,
    articles: list[Article],
    client: anthropic.Anthropic | None = None,
) -> BlogBriefing:
    """생성 → 수치 감사 → (위반 시) 1회 재생성 → 남은 위반 카드는 폐기."""
    _load_dotenv()
    caller = client or anthropic.Anthropic()
    prompt = build_prompt(post, articles)

    payload, input_tokens, output_tokens = _generate(caller, prompt)
    problems = audit(payload, prompt)

    if problems:
        retry_prompt = (
            f"{prompt}\n\n[직전 시도의 문제 — 반드시 고칠 것]\n{_describe(problems)}\n"
            "수치는 자료에 있는 값만 쓰고, headline과 caption_hook에는 숫자를 쓰지 마십시오."
        )
        payload, retry_in, retry_out = _generate(caller, retry_prompt)
        input_tokens += retry_in
        output_tokens += retry_out
        problems = audit(payload, prompt)

    for field in ("headline", "caption_hook"):
        if field in problems:
            raise SummarizeError(f"{field}에 근거 없는 숫자가 남았습니다: {payload[field]!r}")

    dropped = 0
    blog_cards: list[Card] = []
    for i, card in enumerate(payload["blog_cards"]):
        if f"blog_cards:{i}" in problems:
            dropped += 1
            continue
        blog_cards.append(Card(title=card["title"], body=card["body"], source=f"{BLOG_NAME} · {AUTHOR}"))

    related_cards: list[Card] = []
    for i, card in enumerate(payload["related_cards"]):
        if f"related_cards:{i}" in problems:
            dropped += 1
            continue
        related_cards.append(Card(title=card["title"], body=card["body"], source=f"관련기사 · {card['source']}"))

    if len(blog_cards) < MIN_BLOG_CARDS:
        raise SummarizeError(
            f"수치 검증 후 블로그 카드가 {len(blog_cards)}장뿐입니다 (최소 {MIN_BLOG_CARDS}장)."
        )

    return BlogBriefing(
        post=post,
        headline=payload["headline"],
        blog_cards=blog_cards,
        related_cards=related_cards,
        caption_hook=payload["caption_hook"],
        hashtags=[tag.lstrip("#") for tag in payload["hashtags"][:MAX_HASHTAGS]],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        dropped_cards=dropped,
    )


DISCLAIMER = "본 콘텐츠는 정보 제공 목적이며 투자 권유가 아닙니다. 투자 판단의 책임은 투자자 본인에게 있습니다."
BASE_HASHTAGS = ("경제", "매크로", "투자", "경제공부", "카드뉴스")


def build_caption(briefing: BlogBriefing) -> str:
    lines = [
        f"🧠 {BLOG_NAME} 인사이트 | {briefing.post.title}",
        "",
        briefing.caption_hook,
        "",
        # 출처 표기는 카드 이미지 하단에 이미 있으므로 캡션에는 원문 링크만 남긴다 (사용자 결정 2026-07-14).
        f"원문 👉 {briefing.post.link}",
    ]
    if briefing.related_cards:
        lines += ["", "📰 함께 보면 좋은 기사"]
        lines += [f"· {card.title} ({card.source.removeprefix('관련기사 · ')})" for card in briefing.related_cards]

    tags = list(dict.fromkeys(list(briefing.hashtags) + list(BASE_HASHTAGS)))
    lines += ["", f"⚠️ {DISCLAIMER}", "", " ".join(f"#{tag}" for tag in tags)]
    return "\n".join(lines)


def render_blog(briefing: BlogBriefing, out_dir: Path | None = None, fonts: FontSet | None = None) -> list[Path]:
    """표지 1장 + 카드 N장을 저장하고 경로 목록을 반환한다."""
    if not briefing.blog_cards:
        raise RenderError("렌더할 카드가 없습니다.")

    fonts = fonts or FontSet.discover()
    target = out_dir or OUTPUT_ROOT / f"{briefing.post.published:%Y-%m-%d}-blog"
    target.mkdir(parents=True, exist_ok=True)

    cards = briefing.cards
    images = [render_cover(briefing.headline, briefing.post.published, fonts, kicker=f"{BLOG_NAME} 인사이트")]
    images += [render_card(c, i, len(cards), fonts) for i, c in enumerate(cards, 1)]

    paths = []
    for i, image in enumerate(images, 1):
        path = target / f"{i:02d}.jpg"
        image.save(path, "JPEG", quality=92, optimize=True)
        paths.append(path)

    (target / "caption.txt").write_text(build_caption(briefing), encoding="utf-8")
    return paths


# 인스타는 image_url을 자기 서버에서 가져가므로 커밋·push 후에야 발행할 수 있다.
RAW_BASE = "https://raw.githubusercontent.com/hakusancode/econ-insta/main"


def publish_rendered(out_dir: Path) -> int:
    """렌더·push가 끝난 카드 디렉터리를 캐러셀로 발행한다."""
    from .ig_client import InstagramClient

    out_dir = out_dir.resolve()
    caption_path = out_dir / "caption.txt"
    if not caption_path.exists():
        print(f"caption.txt가 없습니다: {out_dir}")
        return 1
    caption = caption_path.read_text(encoding="utf-8")

    images = sorted(out_dir.glob("[0-9][0-9].jpg"))
    if not images:
        print(f"카드 이미지(NN.jpg)가 없습니다: {out_dir}")
        return 1

    rel = out_dir.relative_to(PROJECT_ROOT.resolve()).as_posix()
    urls = [f"{RAW_BASE}/{rel}/{path.name}" for path in images]

    for url in urls:
        response = requests.get(url, timeout=20, allow_redirects=False)
        kind = response.headers.get("Content-Type", "")
        if response.status_code != 200 or kind != "image/jpeg":
            print(f"호스팅 확인 실패 ({response.status_code}, {kind}): {url}")
            print("커밋·push가 끝났는지 확인하세요.")
            return 1

    result = InstagramClient().publish_images(urls, caption)
    print(f"발행 완료: media_id={result.media_id}")
    print(f"  {result.permalink}")
    return 0


def main() -> int:
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "--publish":
        return publish_rendered(Path(sys.argv[2]))

    post = latest_market_post()
    print(f"블로그 글: {post.title} ({post.published:%Y-%m-%d}, 전문 {len(post.body)}자)")

    articles = collect_articles(errors=[])
    print(f"기사 후보: {len(articles)}건\n")

    briefing = summarize_blog(post, articles)

    print(f"■ 표지: {briefing.headline}\n")
    for i, card in enumerate(briefing.cards, 1):
        print(f"{i}. {card.title}  [{card.source}]")
        print(f"   {card.body}\n")
    print("--- 캡션 ---")
    print(build_caption(briefing))

    if briefing.dropped_cards:
        print(f"\n[경고] 수치 검증에 걸려 카드 {briefing.dropped_cards}장을 폐기했습니다.")

    paths = render_blog(briefing)
    print(f"\n카드 {len(paths)}장 렌더 완료:")
    for path in paths:
        print(f"  {path}  ({path.stat().st_size // 1024} KB)")

    cost = briefing.input_tokens / 1e6 * 2 + briefing.output_tokens / 1e6 * 10
    print(f"\n토큰: 입력 {briefing.input_tokens}, 출력 {briefing.output_tokens} (비용 약 ${cost:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

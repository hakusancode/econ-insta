"""주간 릴스 대본: 지난 한 주 데일리 브리핑 후보 중 이슈 하나를 골라 짧은 대본을 만든다.

데일리(daily.py)가 매일 남기는 `out/<날짜>-<슬러그>/briefing.json`을 최근 7일치 모아,
Claude에게 가장 화제성 있는 이슈를 고르게 하고(issue_index), 훅 한 줄과 이유 2~3개를
받는다. 수치·한자는 summarizer/factcheck과 같은 방식으로 기계 검증한다.

차트·렌더·캡션·발행은 이 모듈이 이어받는다(build_weekly) — reels.py의 anim_* 모션과
audio.py의 로열티프리 트랙, backgrounds.py의 배경을 조립해 릴스 mp4·표지·캡션을 낸다.

CLI:
    python -m econ_insta.weekly_reel --render|--publish
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import anthropic

from . import audio, reels
from .backgrounds import available_people, build_background
from .collector import now_kst
from .config import PROJECT_ROOT, _load_dotenv
from .factcheck import unsupported_amounts
from .renderer import FontSet
from .stock_brief import Series
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
REASONS_MIN = 2
REASONS_MAX = 3
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

수치·한자·개수 규칙 (기계적으로 검증되며, 위반 시 재생성됩니다):
- hook에는 한자를 쓰지 마십시오. 국가 약칭도 한글로 풀어 쓰십시오(美→미국, 中→중국).
- hook과 reasons의 모든 수치는 제공된 자료(후보 목록)에 있는 값이어야 합니다.
  단위 환산은 괜찮지만 값을 바꾸거나 없는 값을 만들지 마십시오.
- reasons는 반드시 2개 또는 3개여야 합니다. 1개나 4개 이상은 허용되지 않습니다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "issue_index": {"type": "integer"},
        "hook": {"type": "string"},
        "reasons": {"type": "array", "items": {
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

    today는 aware/naive 둘 다 받는다 — build_weekly의 실제 호출자(now_kst())는 aware라
    naive로 맞춰 비교한다(briefing.json의 date는 타임존 없는 "YYYY-MM-DD" 문자열이므로).
    """
    if today.tzinfo is not None:
        today = today.replace(tzinfo=None)

    candidates: list[WeeklyCandidate] = []
    for path in out_root.glob("*/briefing.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("issue_title") is None:
                continue
            date = datetime.strptime(data["date"], "%Y-%m-%d")
            delta = (today - date).days
            if not (0 <= delta < days):
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
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            print(f"! briefing.json 손상 — 건너뜀: {path} ({exc})")
            continue
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


def audit(texts: dict, source: str, candidates: list[WeeklyCandidate]) -> dict[str, list[str]]:
    """근거 없는 수치·잔여 한자·길이·지어낸 매체명 위반을 찾는다. 키는 'hook' | 'reason:<index>'.

    candidates는 프롬프트에 실제로 실린 후보들(write_script의 top) — reason.source는
    발행 캡션·영상에 "출처 · {source}"로 그대로 나가므로, candidates의 sources 합집합에
    없는 매체명(지어낸 이름)과 빈 값을 기계로 막는다.
    """
    problems: dict[str, list[str]] = {}
    known_sources = {s for c in candidates for s in c.sources}

    reason_count = len(texts["reasons"])
    if not REASONS_MIN <= reason_count <= REASONS_MAX:
        problems["reasons"] = [
            f"reasons {reason_count}개 — {REASONS_MIN}~{REASONS_MAX}개로 맞추십시오"
            " (SCHEMA의 minItems/maxItems는 API가 지원하지 않아 여기서 검사합니다)"
        ]

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

        pieces = [p.strip() for p in reason["source"].split("·")]
        bad_sources = [p for p in pieces if not p or p not in known_sources]
        if bad_sources:
            problems.setdefault(f"reason:{index}", []).append(
                f"source가 비어 있거나 자료에 없는 매체명입니다({reason['source']!r}) — "
                "후보 자료에 실제로 등장한 매체명만 '·'로 이어 쓰십시오"
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
    problems = audit(texts, prompt, top)

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
        problems = audit(texts, prompt, top)

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


# --- 통화 표시 --------------------------------------------------------------

_WON_TICKERS = {"^KS11", "^KQ11", "KRW=X"}


def _currency_for(ticker: str) -> str:
    """원화로 표시할 티커인가 — stock_brief 관용구(지수·환율·.KS 상장 종목만 "원").

    나머지(나스닥·S&P500·WTI·금·비트코인 등)는 "pt"로 둔다 — 값을 원화로 환산하지
    않으므로 원 단위라고 쓰면 거짓말이다.
    """
    return "원" if ticker.endswith(".KS") or ticker in _WON_TICKERS else "pt"


def weekly_series(label: str) -> Series:
    """TICKERS[label]의 최근 3개월 종가. 일요일 실행 전제라 intraday=False다."""
    import yfinance as yf

    ticker = TICKERS[label]
    history = yf.Ticker(ticker).history(period="3mo", auto_adjust=False)
    closes = [float(v) for v in history["Close"] if v == v]
    dates = [d.to_pydatetime() for d, v in history["Close"].items() if v == v]
    if not closes:
        raise WeeklyError(f"시세가 비어 있습니다: {label}")
    return Series(
        name=label, ticker=ticker, closes=closes, dates=dates,
        currency=_currency_for(ticker), intraday=False,
    )


# --- 캡션 --------------------------------------------------------------------

DISCLAIMER = "※ 정보 제공 목적이며 투자 권유가 아닙니다."
HASHTAGS = "#경제 #주식 #증시 #주간브리핑 #투자 #경제뉴스"


def build_caption(
    script: WeeklyScript, when: datetime, credits: tuple[str, ...] = (),
    track: audio.Track | None = None,
) -> str:
    """캡션 조립. 크레딧 줄(📷/🎵)은 라이선스 의무라 조건 충족 시 반드시 싣는다."""
    sources = sorted({s.strip() for r in script.reasons for s in r.source.split("·") if s.strip()})
    lines = [script.hook, "", f"{when:%Y년 %m월 %d일} 주간 이슈 브리핑", ""]
    lines += [f"· {r.title}" for r in script.reasons]
    lines += ["", "출처 · " + " · ".join(sources)]
    lines += [f"📷 {credit}" for credit in credits]
    if track is not None and audio.needs_credit(track):
        lines.append(f"🎵 {track.credit}")
    lines += ["", DISCLAIMER, "", HASHTAGS]
    return "\n".join(lines)


# --- 렌더 --------------------------------------------------------------------


def build_weekly(when: datetime | None = None, client: anthropic.Anthropic | None = None) -> Path | None:
    """이번 주 릴스를 렌더한다. 후보가 0건이면 skipped.txt를 남기고 None(스킵)."""
    when = when or now_kst()
    out_dir = PROJECT_ROOT / "out" / f"{when:%Y-%m-%d}-weekly-reel"

    candidates = load_candidates(PROJECT_ROOT / "out", when)
    print(f"후보 {len(candidates)}건")
    if not candidates:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "skipped.txt").write_text(f"{when:%Y-%m-%d}: 후보 없음\n", encoding="utf-8")
        print(f"소재 없음 — 스킵: {out_dir}")
        return None

    script = write_script(candidates, client=client)
    print(f"훅: {script.hook}  (티커: {script.ticker_label})")

    series = weekly_series(script.ticker_label)

    errors: list[str] = []
    bg = build_background(
        script.people, script.bg_query, errors=errors, issue=None, headline=script.hook
    )
    for message in errors:
        print(f"  ! 배경: {message}")
    print(f"배경: {'사진' if bg else '그래픽 폴백'}")

    fonts = FontSet.discover()
    cover_render = reels.anim_cover(
        script.hook, when, fonts, kicker="주간 이슈 브리핑",
        background=bg.image if bg else None,
    )
    scenes = [reels.Scene(None, reels.COVER_SECONDS, render=cover_render)]
    scenes += [
        reels.Scene(
            None, reels.REASON_SECONDS,
            render=reels.anim_reason(reason, i + 1, len(script.reasons), fonts),
        )
        for i, reason in enumerate(script.reasons)
    ]
    scenes.append(reels.Scene(None, reels.CHART_SECONDS, render=reels.anim_chart(series, when, fonts)))

    track = audio.pick_track(when)

    out_dir.mkdir(parents=True, exist_ok=True)
    cover_path = out_dir / "reel-cover.jpg"
    cover_render(1.0).save(cover_path, "JPEG", quality=92)
    reels.encode(scenes, out_dir / "reel.mp4", audio_path=track.path if track else None)

    caption = build_caption(script, when, credits=bg.credits if bg else (), track=track)
    (out_dir / "caption.txt").write_text(caption, encoding="utf-8")

    print(f"렌더 완료 → {out_dir}")
    return out_dir


# --- CLI ----------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="주간 릴스 렌더·발행")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--render", action="store_true")
    group.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)

    if args.render:
        build_weekly()
        return 0

    when = now_kst()
    out_dir = PROJECT_ROOT / "out" / f"{when:%Y-%m-%d}-weekly-reel"
    if (out_dir / "skipped.txt").exists():
        print(f"이번 주 소재 없음 — 발행 건너뜀: {out_dir}")
        return 0
    return reels.publish_reel(out_dir)


if __name__ == "__main__":
    raise SystemExit(main())

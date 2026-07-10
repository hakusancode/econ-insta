"""생성된 문장의 수치가 원자료에 근거하는지 검사한다.

모델은 간헐적으로 자료에 없는 수치를 만들어낸다(실측: 코스피 +2.52%를 "7% 급등"으로,
국고채 41조를 "40조"로). 사람 검토 없이 매일 발행되므로 코드가 막아야 한다.

단순 문자열 대조로는 안 된다. 같은 값이 다른 표기로 나타나기 때문이다:
    $26.51 billion  ==  265억 달러  ==  265억1000만 달러
    1,503.6         ==  1,503원대
반대로 41조와 40조는 반드시 다른 값으로 판정해야 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 한국어 수 단위. 큰 것부터 (복합 표기 '265억1000만' 결합에 쓰인다)
KO_UNITS: dict[str, float] = {"조": 1e12, "억": 1e8, "만": 1e4, "천": 1e3}
EN_UNITS: dict[str, float] = {"trillion": 1e12, "billion": 1e9, "million": 1e6}

_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(조|억|만|천)?")
_EN_UNIT = re.compile(r"\s*(trillion|billion|million)", re.IGNORECASE)
_PERCENT = re.compile(r"\s*(%|퍼센트|퍼센트포인트|%p)")

# 표현의 반올림을 허용한다. 265억 vs 265.1억(0.04%), 2.5% vs 2.52%(0.8%)는 통과하고
# 40조 vs 41조(2.4%)는 걸린다.
REL_TOL = 0.015
ABS_TOL = 0.05


@dataclass(frozen=True)
class Amount:
    value: float
    is_percent: bool
    text: str

    def matches(self, other: "Amount") -> bool:
        if self.is_percent != other.is_percent:
            return False
        if abs(self.value - other.value) <= ABS_TOL:
            return True
        scale = max(abs(other.value), 1e-9)
        return abs(self.value - other.value) / scale <= REL_TOL


def _unit_scale(unit: str | None) -> float:
    return KO_UNITS[unit] if unit else 1.0


def extract_amounts(text: str) -> list[Amount]:
    """문장에서 수치를 뽑는다. '265억1000만'처럼 붙어 있는 복합 표기는 하나로 합친다."""
    amounts: list[Amount] = []

    tokens = list(_NUMBER.finditer(text))
    index = 0
    while index < len(tokens):
        token = tokens[index]
        number = float(token.group(1).replace(",", ""))
        unit = token.group(2)
        total = number * _unit_scale(unit)
        span_text = token.group(0)
        end = token.end()

        # 붙어 있고 단위가 작아지는 토큰만 이어 붙인다: 265억 + 1000만
        while unit and index + 1 < len(tokens):
            nxt = tokens[index + 1]
            nxt_unit = nxt.group(2)
            if nxt.start() != end or not nxt_unit:
                break
            if _unit_scale(nxt_unit) >= _unit_scale(unit):
                break
            total += float(nxt.group(1).replace(",", "")) * _unit_scale(nxt_unit)
            span_text += nxt.group(0)
            unit, end, index = nxt_unit, nxt.end(), index + 1

        tail = text[end:]
        english = _EN_UNIT.match(tail)
        if english and not unit:
            total = number * EN_UNITS[english.group(1).lower()]
            span_text += english.group(0)
            end += english.end()
            tail = text[end:]

        percent = bool(_PERCENT.match(tail))
        amounts.append(Amount(value=total, is_percent=percent, text=span_text.strip()))
        index += 1

    return amounts


def unsupported_amounts(text: str, source: str) -> list[str]:
    """text의 수치 중 source에 근거가 없는 것들을 돌려준다."""
    pool = extract_amounts(source)
    return [a.text for a in extract_amounts(text) if not any(a.matches(p) for p in pool)]


def has_digits(text: str) -> bool:
    return any(ch.isdigit() for ch in text)

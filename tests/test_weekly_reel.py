"""weekly_reel 테스트. 가짜 클라이언트를 쓰므로 네트워크·API 키가 필요 없다."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from econ_insta import weekly_reel

TODAY = datetime(2026, 8, 30, 19, 0)


def _write_briefing(root, date, edition, sources, count, title="이슈"):
    d = root / f"{date}-{edition}"
    d.mkdir(parents=True)
    (d / "briefing.json").write_text(json.dumps({
        "date": date, "edition": edition, "headline": f"{title} 훅",
        "issue_title": title, "sources": sources, "article_count": count,
        "cards": [{"title": f"{title} 카드", "source": sources[0] if sources else "매일경제"}],
    }, ensure_ascii=False), encoding="utf-8")


def _candidate(title="이슈", sources=("매일경제",), count=3):
    return weekly_reel.WeeklyCandidate(
        date="2026-08-26", edition="kr", headline=f"{title} 훅", issue_title=title,
        sources=list(sources), article_count=count,
        cards=[{"title": f"{title} 카드", "source": sources[0]}])


class LoadCandidatesTest(unittest.TestCase):
    def test_매체수_기사수_내림차순(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_briefing(root, "2026-08-25", "kr", ["매일경제"], 9, "단독 이슈")
            _write_briefing(root, "2026-08-26", "kr", ["매일경제", "연합뉴스", "한국경제"], 5, "대형 이슈")
            _write_briefing(root, "2026-08-27", "global", ["WSJ", "이코노미스트"], 7, "해외 이슈")
            got = weekly_reel.load_candidates(root, TODAY)
            self.assertEqual([c.issue_title for c in got], ["대형 이슈", "해외 이슈", "단독 이슈"])

    def test_7일_밖과_이슈_없는_날은_제외(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_briefing(root, "2026-08-10", "kr", ["매일경제"], 3, "옛날 이슈")
            d = root / "2026-08-26-global"
            d.mkdir()
            (d / "briefing.json").write_text(json.dumps({
                "date": "2026-08-26", "edition": "global", "headline": "훅",
                "issue_title": None, "sources": [], "article_count": 0, "cards": [],
            }), encoding="utf-8")
            self.assertEqual(weekly_reel.load_candidates(root, TODAY), [])


class IssueIndexGuardTest(unittest.TestCase):
    def test_범위밖_번호와_타입이상은_전부_스킵(self):
        candidates = [_candidate("갑"), _candidate("을")]
        for bad in (0, 3, 99, -1, True, "2", None):
            with self.assertRaises(weekly_reel.WeeklyError):
                weekly_reel._chosen({"issue_index": bad}, candidates)

    def test_유효한_번호는_그_후보를_돌려준다(self):
        candidates = [_candidate("갑"), _candidate("을")]
        self.assertEqual(weekly_reel._chosen({"issue_index": 2}, candidates).issue_title, "을")


# --- write_script: FakeClient는 tests/test_summarizer.py의 관용구를 복사한다 -------------


class FakeClient:
    """messages.create()가 지정된 JSON을 텍스트 블록으로 돌려준다.

    data에 리스트를 주면 호출 순서대로 다른 응답을 낸다 (재시도 경로 검증용).
    """

    def __init__(self, data, stop_reason="end_turn", text=None):
        self.bodies = [
            text if text is not None else json.dumps(d, ensure_ascii=False)
            for d in (data if isinstance(data, list) else [data])
        ]
        self.stop_reason = stop_reason
        self.calls = 0
        self.captured = {}
        self.prompts: list[str] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.captured = kwargs
        self.prompts.append(kwargs["messages"][0]["content"])
        body = self.bodies[min(self.calls, len(self.bodies) - 1)]
        self.calls += 1
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=body)],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=300),
        )


def script_payload(issue_index=1, hook="이번 주의 훅", reasons=None, ticker="코스피",
                    bg_query="Bank of Korea building", people=None):
    """검증을 통과하는 깨끗한 응답. 숫자를 자료에 없는 값으로 넣지 않는다."""
    return {
        "issue_index": issue_index,
        "hook": hook,
        "reasons": reasons if reasons is not None else [
            {"title": "이유 하나", "body": "본문 설명입니다.", "source": "매일경제"},
            {"title": "이유 둘", "body": "다른 본문 설명입니다.", "source": "연합뉴스"},
        ],
        "ticker_label": ticker,
        "bg_query": bg_query,
        "people": people if people is not None else [],
    }


def two_candidates():
    return [
        _candidate("갑", sources=("매일경제", "연합뉴스"), count=5),
        _candidate("을", sources=("한국경제",), count=2),
    ]


class WriteScriptTest(unittest.TestCase):
    def test_parses_response(self):
        client = FakeClient(script_payload())
        result = weekly_reel.write_script(two_candidates(), client=client)
        self.assertEqual(result.hook, "이번 주의 훅")
        self.assertEqual(len(result.reasons), 2)
        self.assertEqual(result.reasons[0].source, "매일경제")
        self.assertEqual(result.chosen.issue_title, "갑")
        self.assertEqual(result.ticker_label, "코스피")
        self.assertEqual(result.bg_query, "Bank of Korea building")

    def test_requests_structured_output_with_adaptive_thinking(self):
        client = FakeClient(script_payload())
        weekly_reel.write_script(two_candidates(), client=client)
        self.assertEqual(client.captured["model"], "claude-sonnet-5")
        self.assertEqual(client.captured["thinking"], {"type": "adaptive"})
        fmt = client.captured["output_config"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertFalse(fmt["schema"]["additionalProperties"])

    def test_clean_output_does_not_retry(self):
        client = FakeClient(script_payload())
        weekly_reel.write_script(two_candidates(), client=client)
        self.assertEqual(client.calls, 1)

    def test_truncated_response_raises(self):
        client = FakeClient(script_payload(), stop_reason="max_tokens")
        with self.assertRaises(weekly_reel.WeeklyError):
            weekly_reel.write_script(two_candidates(), client=client)

    def test_no_candidates_raises(self):
        with self.assertRaises(weekly_reel.WeeklyError):
            weekly_reel.write_script([], client=FakeClient(script_payload()))

    def test_unknown_people_key_filtered_out(self):
        client = FakeClient(script_payload(people=["존재하지않는키"]))
        result = weekly_reel.write_script(two_candidates(), client=client)
        self.assertEqual(result.people, ())

    def test_issue_index_guard_fires_without_retry(self):
        """가드가 있다 ≠ 작동한다 — write_script 경로로도 실제로 막히는지 확인한다."""
        client = FakeClient(script_payload(issue_index=99))
        with self.assertRaises(weekly_reel.WeeklyError):
            weekly_reel.write_script(two_candidates(), client=client)
        self.assertEqual(client.calls, 1)  # issue_index 위반은 재생성하지 않는다


class WriteScriptRetryTest(unittest.TestCase):
    def test_unsupported_amount_triggers_retry_then_succeeds(self):
        """자료에 없는 수치는 audit이 잡아 1회 재생성으로 돌린다(summarizer와 동일 관용구)."""
        bad = script_payload(hook="코스피 7% 급등")
        good = script_payload(hook="깨끗한 훅")
        client = FakeClient([bad, good])
        result = weekly_reel.write_script(two_candidates(), client=client)
        self.assertEqual(client.calls, 2)
        self.assertEqual(result.hook, "깨끗한 훅")
        self.assertIn("직전 시도의 문제", client.prompts[1])
        self.assertIn("hook", client.prompts[1])

    def test_long_hook_triggers_retry_then_succeeds(self):
        bad = script_payload(hook="가" * (weekly_reel.HOOK_MAX + 1))
        good = script_payload(hook="짧은 훅")
        client = FakeClient([bad, good])
        result = weekly_reel.write_script(two_candidates(), client=client)
        self.assertEqual(client.calls, 2)
        self.assertEqual(result.hook, "짧은 훅")

    def test_persistent_violation_raises_weekly_error(self):
        """재생성 후에도 위반이 남으면 폴백 없이 실패한다 — 틀린 이슈로 나가느니 스킵."""
        bad = script_payload(hook="코스피 7% 급등")
        client = FakeClient([bad, bad])
        with self.assertRaises(weekly_reel.WeeklyError):
            weekly_reel.write_script(two_candidates(), client=client)
        self.assertEqual(client.calls, 2)


if __name__ == "__main__":
    unittest.main()

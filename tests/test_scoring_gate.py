"""채점 회차 게이트: cron 문자열이 아니라 지금 시각으로 할 일을 정하고, 이미 발행된 일은 건너뛴다.

GitHub cron이 4~5시간 밀려 09:37 회차가 14:10에, 16:10 회차가 21:10에 도착한다(2026-09-08~10).
어느 시각에 도착하든 그 시점에 맞는 일을 하고, 백업 회차가 같은 일을 반복하지 않아야 한다.
"""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import should_score_now as gate  # noqa: E402

KST = timezone(timedelta(hours=9))


def at(day, hour, minute):
    return datetime(2026, 9, day, hour, minute, tzinfo=KST)


def ledger(*rows):
    return "target_date,kind,status\n" + "".join(f"{d},{k},{s}\n" for d, k, s in rows)


class PhaseTests(unittest.TestCase):
    def test_phase_boundaries_match_the_scoring_rules(self):
        self.assertEqual(gate.phase_of(at(10, 9, 4)), "pre_open")
        self.assertEqual(gate.phase_of(at(10, 9, 5)), "session")      # 시가 확정
        self.assertEqual(gate.phase_of(at(10, 15, 39)), "session")
        self.assertEqual(gate.phase_of(at(10, 15, 40)), "post_close")  # 당일 봉 확정(15:40 규칙)

    def test_session_is_today_after_the_open_and_the_previous_trading_day_before(self):
        self.assertEqual(gate.session_for(at(10, 16, 0)), date(2026, 9, 10))
        self.assertEqual(gate.session_for(at(10, 14, 10)), date(2026, 9, 10))
        self.assertEqual(gate.session_for(at(11, 1, 50)), date(2026, 9, 10))   # 22:52 회차가 자정을 넘김
        self.assertEqual(gate.session_for(at(14, 1, 50)), date(2026, 9, 11))   # 월요일 새벽 → 금요일

    def test_weekday_fallback_without_the_calendar(self):
        import builtins
        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "exchange_calendars":
                raise ImportError("차단")
            return real_import(name, *args, **kwargs)
        builtins.__import__ = blocked
        try:
            self.assertEqual(gate.previous_trading_day(date(2026, 9, 14)), date(2026, 9, 11))
            self.assertTrue(gate.is_trading_day(date(2026, 9, 10)))
            self.assertFalse(gate.is_trading_day(date(2026, 9, 12)))
        finally:
            builtins.__import__ = real_import


class DelayedScheduleTests(unittest.TestCase):
    """도착 시각이 무엇이든 그 시점에 맞는 일을 한다."""

    def test_morning_cron_arriving_midsession_scores_only_the_open(self):
        r = gate.decide(at(10, 14, 10), trading=True)
        self.assertEqual((r["scope"], r["score"], r["review"], r["run"]), ("open", True, False, True))

    def test_morning_cron_arriving_after_the_close_scores_everything_and_reviews(self):
        r = gate.decide(at(10, 16, 0), trading=True)
        self.assertEqual((r["scope"], r["score"], r["review"], r["session"]), ("all", True, True, "2026-09-10"))

    def test_evening_cron_arriving_past_midnight_reviews_the_previous_session(self):
        r = gate.decide(at(11, 1, 50), trading=True)
        self.assertEqual((r["scope"], r["review"], r["session"]), ("all", True, "2026-09-10"))

    def test_holiday_skips_scheduled_runs_but_not_manual_ones(self):
        self.assertFalse(gate.decide(at(24, 16, 0), trading=False)["run"])          # 추석
        manual = gate.decide(at(24, 16, 0), event="workflow_dispatch", trading=False)
        self.assertTrue(manual["run"])
        self.assertFalse(manual["review"], "휴장일 회고는 없다")

    def test_explicit_scope_is_respected_but_review_waits_for_the_close(self):
        r = gate.decide(at(10, 14, 10), event="workflow_dispatch", requested="all", trading=True)
        self.assertEqual((r["scope"], r["review"]), ("all", False))
        r = gate.decide(at(10, 16, 0), event="workflow_dispatch", requested="open", trading=True)
        self.assertEqual((r["scope"], r["review"]), ("open", False))


class DedupeTests(unittest.TestCase):
    """백업 회차는 이미 발행된 일을 반복하지 않는다. 수동 실행은 항상 돈다."""

    def test_open_already_scored_skips_the_scheduled_run(self):
        text = ledger(("2026-09-10", "open", "scored"))
        self.assertFalse(gate.decide(at(10, 14, 10), trading=True, ledger_text=text)["run"])
        self.assertTrue(gate.decide(at(10, 14, 10), event="workflow_dispatch", trading=True, ledger_text=text)["run"])

    def test_yesterdays_scoring_does_not_count_for_today(self):
        text = ledger(("2026-09-09", "open", "scored"), ("2026-09-09", "direction", "scored"))
        r = gate.decide(at(10, 16, 0), trading=True, ledger_text=text, review_published=False)
        self.assertEqual((r["score"], r["review"]), (True, True))

    def test_close_scored_and_review_published_skips_everything(self):
        text = ledger(("2026-09-10", "direction", "scored"))
        r = gate.decide(at(10, 21, 10), trading=True, ledger_text=text, review_published=True)
        self.assertEqual((r["run"], r["score"], r["review"]), (False, False, False))
        self.assertIn("이미 발행됨", r["reason"])

    def test_close_scored_but_review_missing_retries_only_the_review(self):
        text = ledger(("2026-09-10", "direction", "scored"))
        r = gate.decide(at(10, 21, 10), trading=True, ledger_text=text, review_published=False)
        self.assertEqual((r["run"], r["score"], r["review"]), (True, False, True))

    def test_pending_rows_do_not_count_as_scored(self):
        text = ledger(("2026-09-10", "direction", "pending"), ("2026-09-10", "open", "missing_actual"))
        self.assertTrue(gate.decide(at(10, 14, 10), trading=True, ledger_text=text)["score"])
        self.assertTrue(gate.decide(at(10, 16, 0), trading=True, ledger_text=text)["score"])

    def test_manual_run_ignores_dedupe(self):
        text = ledger(("2026-09-10", "direction", "scored"))
        r = gate.decide(at(10, 21, 10), event="workflow_dispatch", trading=True, ledger_text=text, review_published=True)
        self.assertEqual((r["score"], r["review"]), (True, True))


class MainTests(unittest.TestCase):
    def test_writes_every_output_the_workflow_reads(self):
        root = Path(tempfile.mkdtemp())
        (root / "samsung" / "reviews").mkdir(parents=True)
        (root / "samsung" / "forecast_log.csv").write_text(ledger(("2026-09-10", "direction", "scored")), encoding="utf-8")
        (root / "samsung" / "reviews" / "2026-09-10.json").write_text("{}", encoding="utf-8")
        out = root / "out.txt"
        fixed = at(10, 21, 10)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed
        saved = (os.environ.get("GITHUB_OUTPUT"), sys.argv, gate.datetime)
        os.environ["GITHUB_OUTPUT"] = str(out)
        sys.argv = ["x", "--target", "samsung", "--ledger-root", str(root), "--ref", ""]
        gate.datetime = FrozenDatetime
        try:
            gate.main()
        finally:
            gate.datetime = saved[2]
            sys.argv = saved[1]
            if saved[0] is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = saved[0]
        text = out.read_text(encoding="utf-8")
        for line in ("run=false", "score=false", "review=false", "scope=all", "session=2026-09-10"):
            self.assertIn(line, text)


if __name__ == "__main__":
    unittest.main()

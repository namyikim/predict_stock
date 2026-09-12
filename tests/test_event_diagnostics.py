# -*- coding: utf-8 -*-
"""R08 2단계 진단 — 이벤트일을 어떻게 세는가, 갭을 어떻게 재는가.

정책(구간 확대·보류)은 이 숫자 위에 세워진다. 세는 규칙이 틀리면 정책이 통째로 틀린다.
"""
import sys
import unittest
import unittest.mock
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import run_event_diagnostics as ed  # noqa: E402
from data_sources import us_calendar as uc  # noqa: E402


class MoveTests(unittest.TestCase):
    def frame(self):
        index = pd.to_datetime(["2026-03-02", "2026-03-03", "2026-03-04"])
        return pd.DataFrame({"open": [100.0, 110.0, 99.0], "close": [100.0, 105.0, 99.0]}, index=index)

    def test_gap_is_measured_against_the_previous_close(self):
        moves = ed.daily_moves(self.frame())
        self.assertAlmostEqual(moves.loc["2026-03-03", "abs_gap"], 0.10)
        self.assertAlmostEqual(moves.loc["2026-03-03", "abs_move"], 0.05)
        self.assertAlmostEqual(moves.loc["2026-03-03", "abs_intraday"], 5 / 110)

    def test_the_first_day_is_dropped_because_it_has_no_previous_close(self):
        self.assertNotIn(pd.Timestamp("2026-03-02"), ed.daily_moves(self.frame()).index)

    def test_direction_is_discarded_on_purpose(self):
        """폭만 본다. 방향을 섞으면 큰 상승과 큰 하락이 서로 지워진다."""
        index = pd.to_datetime(["2026-03-02", "2026-03-03"])
        up = pd.DataFrame({"open": [100.0, 110.0], "close": [100.0, 110.0]}, index=index)
        down = pd.DataFrame({"open": [100.0, 90.0], "close": [100.0, 90.0]}, index=index)
        self.assertAlmostEqual(ed.daily_moves(up).loc["2026-03-03", "abs_gap"],
                               ed.daily_moves(down).loc["2026-03-03", "abs_gap"])


class EventDayTests(unittest.TestCase):
    """두 정의는 같지 않다. 그 차이를 모르면 진단 결과를 잘못 읽는다."""

    def test_next_trading_day_takes_only_the_first_day_after_a_release(self):
        days = pd.to_datetime(["2026-01-09", "2026-01-12", "2026-01-13", "2026-01-14"])
        first_after, flagged, mapping = ed.event_days(days)
        # 2026-01-09 은 실제 일정표에 있는 발표일(금)이다. 다음 거래일은 월요일 하나뿐이어야 한다.
        self.assertIn(pd.Timestamp("2026-01-12"), first_after)
        self.assertNotIn(pd.Timestamp("2026-01-13"), first_after)
        self.assertIn("2026-01-12", {str(k.date()) for k in mapping})

    def test_the_current_flag_rule_marks_more_days_than_the_next_trading_day(self):
        """달력일 4일 소급이라 금요일 발표가 월·화를 모두 표시한다 — 실제로 48일 대 29일이었다."""
        days = pd.to_datetime(["2026-01-09", "2026-01-12", "2026-01-13", "2026-01-14"])
        first_after, flagged, _ = ed.event_days(days)
        self.assertTrue(flagged >= first_after & set(days),
                        "표시 규칙은 다음 거래일을 포함해야 한다")
        self.assertGreaterEqual(len(flagged), len(first_after & set(days)))

    def test_a_release_with_no_later_trading_day_is_skipped(self):
        days = pd.to_datetime(["2026-01-05"])          # 첫 발표(01-09)보다 앞이다
        first_after, _, mapping = ed.event_days(days)
        self.assertEqual(first_after, set())
        self.assertEqual(mapping, {})


class BootstrapTests(unittest.TestCase):
    def test_a_real_difference_is_detected(self):
        rng = np.random.default_rng(0)
        result = ed.bootstrap_gap(rng.normal(2.0, 0.2, 200), rng.normal(1.0, 0.2, 200), b=500)
        self.assertGreater(result["lo"], 0, "확실한 차이는 구간 하한이 0보다 커야 한다")
        self.assertAlmostEqual(result["diff"], 1.0, delta=0.1)

    def test_no_difference_gives_an_interval_that_straddles_zero(self):
        rng = np.random.default_rng(1)
        result = ed.bootstrap_gap(rng.normal(1.0, 0.3, 200), rng.normal(1.0, 0.3, 200), b=500)
        self.assertLess(result["lo"], 0)
        self.assertGreater(result["hi"], 0)

    def test_too_few_points_says_so_instead_of_returning_a_number(self):
        """표본이 없을 때 0을 돌려주면 '차이 없음'으로 잘못 읽힌다."""
        result = ed.bootstrap_gap([1.0], [1.0, 2.0, 3.0])
        self.assertIsNone(result["diff"])
        self.assertIn("표본", result["reason"])

    def test_the_run_is_reproducible(self):
        rng = np.random.default_rng(2)
        a, b = rng.normal(1.5, 0.4, 60), rng.normal(1.2, 0.4, 90)
        self.assertEqual(ed.bootstrap_gap(a, b, b=300)["lo"], ed.bootstrap_gap(a, b, b=300)["lo"])


class WindowTests(unittest.TestCase):
    def test_only_the_covered_window_is_used(self):
        """일정표 밖은 '발표 없음'이 아니라 '모름'이다. 섞어 세면 평일 쪽이 오염된다."""
        low, high = uc.COVERAGE
        index = pd.bdate_range("2024-01-01", "2026-12-31")
        prices = pd.DataFrame({"open": np.linspace(100, 200, len(index)),
                               "close": np.linspace(100, 200, len(index))}, index=index)
        with unittest.mock.patch.object(ed, "load_prices", return_value=prices):
            _, summary = ed.diagnose("samsung")
        self.assertGreaterEqual(summary["window"][0], low)
        self.assertLessEqual(summary["window"][1], high)


if __name__ == "__main__":
    import unittest.mock  # noqa: F401
    unittest.main()

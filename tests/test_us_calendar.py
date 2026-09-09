# -*- coding: utf-8 -*-
"""미국 지표 일정: 발표 결과가 아니라 '언제 발표되는가'만 쓴다."""
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data_sources import us_calendar as uc  # noqa: E402


class ScheduleTests(unittest.TestCase):
    """일정은 규칙으로 추정하지 않는다. BLS 는 '매월 둘째 주' 규칙을 따르지 않는다."""

    def test_published_dates_match_the_official_schedule(self):
        self.assertEqual(uc.us_events_on("2026-09-10"), ["PPI"])
        self.assertEqual(uc.us_events_on("2026-09-11"), ["CPI"])
        self.assertEqual(uc.us_events_on("2026-09-16"), ["FOMC"])
        self.assertEqual(uc.us_events_on("2026-09-09"), [])     # 규칙이면 여기에 CPI 를 넣었을 것

    def test_dates_outside_the_table_are_unknown_not_empty(self):
        self.assertFalse(uc.covered("2027-03-01"))
        self.assertTrue(uc.covered("2026-09-11"))

    def test_korea_flag_lands_on_the_next_session(self):
        # 미국 발표는 한국 시간 밤 → 그 반응은 다음 한국 거래일 갭에 나타난다.
        self.assertEqual(uc.korea_event_flags("2026-09-10"), [])
        self.assertIn("미국지표:미국 생산자물가(PPI)", uc.korea_event_flags("2026-09-11"))
        # 금요일 CPI → 월요일 예측일에 잡혀야 한다.
        flags = uc.korea_event_flags("2026-09-14")
        self.assertIn("미국지표:미국 소비자물가(CPI)", flags)
        self.assertIn("미국지표:FOMC 금리 결정", uc.korea_event_flags("2026-09-17"))

    def test_csv_can_override_a_changed_date(self):
        root = Path(tempfile.mkdtemp())
        (root / "macro_inputs").mkdir()
        (root / "macro_inputs" / "us_calendar.csv").write_text(
            "date,event\n2026-09-15,CPI\n", encoding="utf-8")
        self.assertEqual(uc.us_events_on("2026-09-15", root), ["CPI"])
        # 덮어쓰지 않은 날짜는 표 그대로다.
        self.assertEqual(uc.us_events_on("2026-09-10", root), ["PPI"])

    def test_upcoming_lists_the_next_releases(self):
        upcoming = uc.upcoming_us_events("2026-09-09", days=10)
        self.assertEqual([e["event"] for e in upcoming], ["PPI", "CPI", "FOMC"])
        self.assertTrue(all("label" in e for e in upcoming))


class TsmcTests(unittest.TestCase):
    """TSMC 월매출은 매달 10일 전후 공시라, 그 시점에 이미 나온 달만 쓴다."""

    def frame(self):
        months = pd.date_range("2020-01-01", "2026-08-01", freq="MS")
        return pd.DataFrame({"month": months, "value": range(len(months))})

    def test_unpublished_months_are_not_used(self):
        import macro_utils as mu
        quarters = pd.PeriodIndex(["2026Q3"], freq="Q")
        # 분기 1개월 시점(7월 말): 7월 매출은 8/10 공시라 아직 못 본다.
        self.assertTrue(pd.isna(mu.tsmc_features(self.frame(), quarters, 1)["tsmc_rev_k"].iloc[0]))
        # 2개월 시점(8월 말): 7월 매출은 8/10 에 나왔으므로 쓸 수 있다.
        self.assertFalse(pd.isna(mu.tsmc_features(self.frame(), quarters, 2)["tsmc_rev_k"].iloc[0]))

    def test_missing_file_is_reported_not_raised_into_the_report(self):
        import macro_utils as mu
        with self.assertRaises(RuntimeError):
            mu.load_tsmc_revenue(Path(tempfile.mkdtemp()))

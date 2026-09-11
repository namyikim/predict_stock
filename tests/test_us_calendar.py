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

    def test_missing_source_raises_so_the_caller_can_skip(self):
        import macro_utils as mu
        with self.assertRaises(RuntimeError):
            mu.load_tsmc_revenue(Path(tempfile.mkdtemp()), fetch=False)


class TwseApiTests(unittest.TestCase):
    """대만거래소 OpenAPI: 키가 필요 없고, 한 행에 세 달치가 들어 있다."""

    PAYLOAD = [
        {"出表日期": "1150817", "資料年月": "11507", "公司代號": "2330", "公司名稱": "台積電",
         "營業收入-當月營收": "467580548", "營業收入-上月營收": "442679969",
         "營業收入-去年當月營收": "323165707"},
        {"資料年月": "11507", "公司代號": "2454", "營業收入-當月營收": "1"},
    ]

    def test_roc_year_is_converted(self):
        import macro_utils as mu
        self.assertEqual(mu.roc_month_to_date("11507").date().isoformat(), "2026-07-01")
        self.assertEqual(mu.roc_month_to_date("11412").date().isoformat(), "2025-12-01")
        with self.assertRaises(ValueError):
            mu.roc_month_to_date("2026-07")

    def test_one_row_yields_three_months_for_the_right_company(self):
        import macro_utils as mu
        got = mu.parse_twse_revenue(self.PAYLOAD).set_index("month")["value"]
        self.assertEqual(len(got), 3)
        self.assertEqual(got.loc[pd.Timestamp("2026-07-01")], 467580548)
        self.assertEqual(got.loc[pd.Timestamp("2026-06-01")], 442679969)
        self.assertEqual(got.loc[pd.Timestamp("2025-07-01")], 323165707)   # 전년 동월

    def test_missing_company_is_an_error(self):
        import macro_utils as mu
        with self.assertRaises(ValueError):
            mu.parse_twse_revenue(self.PAYLOAD, stock_code="9999")

    def test_api_result_is_merged_onto_the_stored_history(self):
        """API 는 최근 공시월만 준다. 보관본과 합쳐야 이력이 이어진다."""
        import macro_utils as mu
        root = Path(tempfile.mkdtemp())
        fallback = root / "fb"
        fallback.mkdir()
        months = pd.date_range("2024-01-01", "2026-05-01", freq="MS")
        pd.DataFrame({"month": months.strftime("%Y-%m"), "value": range(len(months))}).to_csv(
            fallback / "tsmc_revenue.csv", index=False)
        saved = mu.exports.fetch_tsmc_revenue
        mu.exports.fetch_tsmc_revenue = lambda *a, **k: mu.parse_twse_revenue(self.PAYLOAD)
        try:
            frame, info = mu.load_tsmc_revenue(root, fallback_dir=fallback)
        finally:
            mu.exports.fetch_tsmc_revenue = saved
        self.assertEqual(info["source"], "TWSE_API+cache")
        self.assertEqual((info["first"], info["last"]), ("2024-01", "2026-07"))
        self.assertTrue(info["fresh"])
        # 겹치는 달은 새 값으로 갱신된다.
        value = frame.set_index("month")["value"].loc[pd.Timestamp("2025-07-01")]
        self.assertEqual(value, 323165707)

    def test_falls_back_to_cache_when_the_api_fails(self):
        import macro_utils as mu
        root = Path(tempfile.mkdtemp())
        fallback = root / "fb"
        fallback.mkdir()
        pd.DataFrame({"month": ["2026-05"], "value": [1]}).to_csv(
            fallback / "tsmc_revenue.csv", index=False)
        saved = mu.exports.fetch_tsmc_revenue

        def boom(*a, **k):
            raise RuntimeError("TWSE 월매출 조회 실패(URLError).")
        mu.exports.fetch_tsmc_revenue = boom
        try:
            _, info = mu.load_tsmc_revenue(root, fallback_dir=fallback)
        finally:
            mu.exports.fetch_tsmc_revenue = saved
        self.assertEqual(info["source"], "last_successful_fetch")
        self.assertFalse(info["fresh"])


class SemiconductorEarningsTests(unittest.TestCase):
    """실적 발표일은 회사 공식 공지만 넣는다. 제3자 추정은 출처끼리도 어긋난다."""

    def test_only_confirmed_dates_are_in_the_table(self):
        self.assertEqual(uc.us_events_on("2026-09-30"), ["MU"])          # 회사 보도자료로 확정
        semis = {"MU", "NVDA", "TSM", "AMD"}
        for guess in ("2026-10-15", "2026-11-17", "2026-11-25"):        # 추정치들 — 넣지 않는다
            self.assertFalse(semis & set(uc.us_events_on(guess)), guess)

    def test_pending_list_names_the_unconfirmed_companies(self):
        pending = uc.pending_earnings_note()
        self.assertEqual(set(pending), {"NVDA", "TSM", "AMD"})
        self.assertIn("공지", pending["NVDA"])

    def test_csv_confirmation_removes_from_pending_and_flags_the_next_session(self):
        root = Path(tempfile.mkdtemp())
        (root / "macro_inputs").mkdir()
        (root / "macro_inputs" / "us_calendar.csv").write_text("date,event\n2026-11-17,NVDA\n", encoding="utf-8")
        self.assertNotIn("NVDA", uc.pending_earnings_note(root))
        self.assertIn("미국지표:엔비디아 실적", uc.korea_event_flags("2026-11-18", root))

    def test_micron_flags_the_following_korean_session(self):
        self.assertIn("미국지표:마이크론 실적", uc.korea_event_flags("2026-10-01"))
        self.assertNotIn("미국지표:마이크론 실적", uc.korea_event_flags("2026-09-30"))

"""금리·경기 점검: 시점 불일치·결측·부분 조회가 잘못된 방향 판단을 만들지 않는지 확인."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import monetary_watch as watch


class MonetaryWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def write_series(self, name, dates, values):
        pd.DataFrame({"date": dates, "value": values}).to_csv(self.folder / name, index=False)

    def monthly_inputs(self):
        dates = pd.bdate_range("2026-07-01", "2026-10-22")
        self.write_series("term_spread.csv", dates, [1 if d.month == 7 else .5 if d.month == 8 else .8 for d in dates])
        self.write_series("leading_cycle.csv", ["2026-07-01", "2026-08-01"], [101, 100])
        self.write_series("kospi_monthly.csv", ["2026-07-01", "2026-08-01", "2026-09-01"], [100, 90, 95])

    def test_same_month_comparison_and_unfinished_month_excluded(self):
        self.monthly_inputs()
        result = watch.build_watch(self.folder, "2026-10-23", fetch=False)
        self.assertEqual(result["comparison"]["month"], pd.Timestamp("2026-08-01"))
        self.assertAlmostEqual(result["comparison"]["changes"]["spread"], -.5)
        self.assertAlmostEqual(result["comparison"]["changes"]["kospi"], -10)
        self.assertEqual(result["monthly"]["spread"].index[-1], pd.Timestamp("2026-09-01"))
        self.assertIn("함께 관측됐습니다", watch.watch_html(result))
        self.assertIn("2026-07 → 2026-08", watch.watch_html(result))

    def test_missing_previous_calendar_month_is_not_bridged(self):
        self.monthly_inputs()
        self.write_series("leading_cycle.csv", ["2026-06-01", "2026-08-01"], [101, 100])
        result = watch.build_watch(self.folder, "2026-10-23", fetch=False)
        self.assertIsNone(result["comparison"])
        self.assertIn("방향 비교를 보류", watch.watch_html(result))

    def test_stale_common_month_does_not_describe_current_direction(self):
        self.monthly_inputs()
        result = watch.build_watch(self.folder, "2027-01-23", fetch=False)
        self.assertIn("현재 방향 판단을 보류", watch.watch_html(result))

    def test_partial_ended_month_is_excluded(self):
        dates = pd.bdate_range("2026-09-01", "2026-09-18")
        series = pd.Series(1., index=dates)
        self.assertTrue(watch.completed_spread_months(series, pd.Timestamp("2026-10-23")).empty)

    def test_daily_rates_need_same_date_and_future_values_are_ignored(self):
        pd.DataFrame({"date": ["2026-10-20", "2026-10-21", "2026-10-22", "2026-11-01"],
                      "policy": [3, np.nan, 3, 4], "kr3y": [np.nan, 3.4, 3.5, 4.8],
                      "kr10y": [3.6, np.inf, 3.8, 5]}).to_csv(self.folder / "korea_rates_daily.csv", index=False)
        result = watch.build_watch(self.folder, "2026-10-23", fetch=False)
        self.assertEqual(list(result["pair"].index), [pd.Timestamp("2026-10-22")])
        self.assertEqual(list(result["spread"].index), [pd.Timestamp("2026-10-22")])
        self.assertIn("+0.500%p", watch.watch_html(result))
        self.assertNotIn("2026-11-01", watch.watch_html(result))

    def test_no_fetch_does_not_write_and_monthly_rate_is_labelled(self):
        path = self.folder / "policy_inflation.csv"
        path.write_text("month,policy_rate\n2026-09-01,3\n2026-10-01,4\n")
        before = path.read_bytes()
        def forbidden(*args):
            self.fail("오프라인 실행은 조회하지 않아야 합니다")
        result = watch.build_watch(self.folder, "2026-10-23", fetch=False, fetch_fn=forbidden)
        html = watch.watch_html(result)
        self.assertIn("2026-09 기준금리 월평균 3.00%", html)
        self.assertIn("격차 계산을 보류", html)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.folder / "korea_rates_daily.csv").exists())

    def test_partial_fetch_preserves_other_series_and_hides_exception(self):
        cache = self.folder / "korea_rates_daily.csv"
        cache.write_text("date,policy,kr3y,kr10y\n2026-10-20,3,3.4,3.7\n")
        def fetch(stat, item, start, end, key):
            if item != "010200000":
                raise RuntimeError("https://secret.invalid/private-api-key")
            return pd.DataFrame({"date": ["2026-10-21"], "value": [3.5]})
        result = watch.build_watch(self.folder, "2026-10-23", fetch_fn=fetch)
        self.assertEqual(result["rates"].loc["2026-10-20", "policy"], 3)
        self.assertEqual(result["rates"].loc["2026-10-21", "kr3y"], 3.5)
        self.assertEqual(result["pair"].index[-1], pd.Timestamp("2026-10-20"))
        self.assertEqual(len(result["info"]["failed"]), 2)
        self.assertNotIn("private-api-key", watch.watch_html(result))
        self.assertEqual(pd.read_csv(cache)["kr10y"].dropna().iloc[0], 3.7)

    def test_empty_response_does_not_erase_cache(self):
        cache = self.folder / "korea_rates_daily.csv"
        cache.write_text("date,policy,kr3y,kr10y\n2026-10-20,3,3.4,3.7\n")
        before = cache.read_bytes()
        watch.load_rates(self.folder, "2026-10-23", fetch_fn=lambda *args: pd.DataFrame(columns=["date", "value"]))
        self.assertEqual(cache.read_bytes(), before)

    def test_invalid_observations_and_duplicate_dates(self):
        series = watch.clean_series(pd.Series(["bad", "2026-10-20", "2026-10-20", "2026-10-21", "2026-10-22"]),
                                    pd.Series([1, 2, 3, np.inf, "bad"]), pd.Timestamp("2026-10-23"))
        self.assertEqual(series.to_list(), [3])

    def test_empty_history_still_renders(self):
        html = watch.watch_html(watch.build_watch(self.folder, "2026-10-23", fetch=False))
        self.assertIn("10년−1년 금리차와는 다른 지표", html)
        self.assertIn("방향 비교를 보류", html)
        self.assertNotIn("nan", html)

    def test_kst_cutoff(self):
        self.assertEqual(watch.asof_day("2026-09-30T16:00:00+00:00"), pd.Timestamp("2026-10-01"))


if __name__ == "__main__":
    unittest.main()

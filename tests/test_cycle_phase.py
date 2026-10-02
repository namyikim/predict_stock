"""거시 보고서 '경기 국면' 절과 미국 신용위험 판정(2026-10-01, 거시 영상의 판단 방식을 우리 자료로)."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import cycle_phase as C  # noqa: E402
import build_macro_report as M  # noqa: E402
import forecast_utils as fu  # noqa: E402


def monthly(values, start="2024-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"), dtype=float)


class PhaseRuleTests(unittest.TestCase):
    def test_one_month_down_is_a_possible_peak_two_is_a_turn(self):
        base = list(np.linspace(99, 104, 20))
        self.assertIn("정점 가능성", C.phase_of(monthly(base + [103.8]))["state"])
        self.assertIn("하락 국면 전환(고점 뒤 2개월", C.phase_of(monthly(base + [103.8, 103.5]))["state"])
        self.assertIn("상승 중", C.phase_of(monthly(base))["state"])

    def test_trough_side_mirrors_the_rule(self):
        base = list(np.linspace(104, 99, 20))
        self.assertIn("저점 가능성", C.phase_of(monthly(base + [99.3]))["state"])
        self.assertIn("상승 국면 전환", C.phase_of(monthly(base + [99.3, 99.6]))["state"])

    def test_rebound_does_not_count_elapsed_months_as_declines(self):
        p = C.phase_of(monthly([98, 99, 100, 103, 99, 100, 101, 102]))
        self.assertIn("반등", p["state"])
        self.assertNotIn("하락 국면", p["state"])
        self.assertEqual(p["months"], 3)
        self.assertEqual(p["since"], pd.Timestamp("2024-05-01"))
        self.assertEqual(p["months_since_peak"], 4)

    def test_pullback_does_not_count_elapsed_months_as_rises(self):
        p = C.phase_of(monthly([104, 103, 102, 97, 101, 100, 99, 98]))
        self.assertIn("재하락", p["state"])
        self.assertNotIn("상승 국면", p["state"])
        self.assertEqual(p["months"], 3)
        self.assertEqual(p["since"], pd.Timestamp("2024-05-01"))

    def test_flat_month_breaks_the_direction_streak(self):
        for values in ([98, 99, 100, 103, 102, 102], [100] * 6):
            with self.subTest(values=values):
                p = C.phase_of(monthly(values))
                self.assertIn("횡보", p["state"])
                self.assertEqual(p["months"], 0)
        p = C.phase_of(monthly([98, 99, 100, 103, 102, 102, 101]))
        self.assertEqual(p["months"], 1)
        self.assertNotIn("국면 전환", p["state"])

    def test_missing_month_breaks_the_direction_streak(self):
        for values in ([98, 99, 100, 103, np.nan, 102, 101],
                       [98, 99, 100, 103, np.nan, 102, 103]):
            series = monthly(values)
            for input_series in (series, series.dropna()):
                with self.subTest(values=values, size=len(input_series)):
                    p = C.phase_of(input_series)
                    self.assertEqual(p["months"], 1)
                    self.assertNotIn("국면 전환", p["state"])
        p = C.phase_of(monthly([98, 99, 100, 101, 103, np.nan, 102]))
        self.assertIn("판정 보류", p["state"])
        self.assertEqual(p["months"], 0)

    def test_missing_latest_value_does_not_reuse_an_old_verdict(self):
        self.assertIsNone(C.phase_of(monthly([98, 99, 100, 103, 102, 101, np.nan])))

    def test_input_order_does_not_change_monthly_direction(self):
        series = monthly([98, 99, 100, 103, 99, 100, 101, 102])
        self.assertEqual(C.phase_of(series), C.phase_of(series.iloc[::-1]))

    def test_latest_equal_extreme_starts_a_new_streak(self):
        p = C.phase_of(monthly([98, 99, 103, 102, 103, 102]))
        self.assertEqual(p["peak_month"], pd.Timestamp("2024-05-01"))
        self.assertEqual(p["months"], 1)
        self.assertIn("정점 가능성", p["state"])

    def test_lead_lag_reports_level_and_change_separately(self):
        target = monthly(np.sin(np.linspace(0, 6, 60)) + 100, start="2021-01-01")
        candidate = target.shift(-2)          # 2개월 앞선 계열
        result = C.lead_lag(target, candidate, max_lag=4, min_n=24)
        self.assertEqual(result["best_level"][0], 2)
        self.assertEqual(result["best_change"][0], 2)

    def test_phase_returns_split_months_by_previous_direction(self):
        cycle = monthly([100, 101, 102, 101, 100, 101], start="2024-01-01")
        kospi = monthly([100, 102, 104, 103, 101, 103], start="2024-01-01")
        rows = C.phase_returns(cycle, kospi)
        self.assertIn("선행지수 상승 뒤 달", rows)
        self.assertIn("선행지수 하락 뒤 달", rows)


class SectionTests(unittest.TestCase):
    def test_section_builds_from_the_repository_data(self):
        import os
        cwd = os.getcwd(); os.chdir(ROOT)
        try:
            data = C.build_cycle_phase(fetch=False)
        finally:
            os.chdir(cwd)
        self.assertIsNotNone(data)
        html = C.cycle_phase_html(data)
        for text in ("국면 판정", "국면별 코스피 월수익률", "뉴스심리지수(3개월 이동평균)", "장단기 금리차", "자산 배분은 하지 않습니다"):
            self.assertIn(text, html)
        self.assertIn("선행지수 순환변동치", C.cycle_phase_line(data))

    def test_stock_report_line_uses_the_same_rule(self):
        import os
        cwd = os.getcwd(); os.chdir(ROOT)
        try:
            line = fu.leading_cycle_phase_line()
            data = C.build_cycle_phase(fetch=False)
        finally:
            os.chdir(cwd)
        self.assertIn(data["phase"]["state"], line)


class CreditRiskTests(unittest.TestCase):
    def frame(self, hy):
        idx = pd.bdate_range("2026-01-01", periods=len(hy))
        return pd.DataFrame({"high_yield_spread": hy, "us10y": 4.5, "nasdaq": 20000}, index=idx)

    def test_rising_from_the_low_is_flagged(self):
        hy = [2.9] * 40 + list(np.linspace(2.6, 2.6, 10)) + list(np.linspace(2.62, 2.85, 30))
        gauge = M.credit_risk_gauge(self.frame(hy))
        self.assertEqual(gauge["label"], "신용위험 상승 중")
        self.assertGreater(gauge["off_low_bp"], 15)

    def test_flat_near_low_is_stable(self):
        gauge = M.credit_risk_gauge(self.frame([2.6] * 80))
        self.assertEqual(gauge["label"], "안정")

    def test_status_warns_when_data_is_stale(self):
        note = M.us_market_status(self.frame([2.6] * 80), {}, today="2026-10-01")
        self.assertIn("자료가 오래됐습니다", note)
        fresh = M.us_market_status(self.frame([2.6] * 80), {}, today=pd.bdate_range("2026-01-01", periods=80)[-1])
        self.assertNotIn("자료가 오래됐습니다", fresh)


class WiringTests(unittest.TestCase):
    def test_macro_job_passes_the_kosis_key(self):
        import yaml
        text = (ROOT / ".github" / "workflows" / "macro-report.yml").read_text(encoding="utf-8")
        self.assertIn("KOSIS_API_KEY: ${{ secrets.KOSIS_API_KEY }}", text)
        yaml.safe_load(text)

    def test_kosis_lookup_knows_the_coincident_series(self):
        from data_sources.kosis import kosis_spec, MACRO_SERIES
        self.assertEqual(kosis_spec("coincident_cycle")["name"], "동행지수 순환변동치")
        self.assertNotIn("coincident_cycle", MACRO_SERIES)   # 노트북 특징은 그대로

    def test_fred_falls_back_to_yahoo_for_rates_and_nasdaq(self):
        text = (ROOT / "data_sources" / "fred.py").read_text(encoding="utf-8")
        self.assertIn("_yahoo_daily({'us10y': '^TNX', 'nasdaq': '^IXIC'}.get(name))", text)


if __name__ == "__main__":
    unittest.main()

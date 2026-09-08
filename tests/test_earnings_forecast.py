"""분기 영업이익 나우캐스트: 시점 정합, 기준선 게이트, 누적 공시 차분, CSV 파싱."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_earnings_forecast as ef  # noqa: E402


def synthetic(seed=0, link=True, last_month="2026-08-01"):
    months = pd.date_range("2010-01-01", last_month, freq="MS")
    rng = np.random.default_rng(seed)
    cycle = np.sin(np.arange(len(months)) / 11)
    exports = pd.Series(6e9 * np.exp(0.004 * np.arange(len(months)) + 0.45 * cycle
                                     + rng.normal(0, .05, len(months))), index=months)
    fx = pd.Series(1200 + 80 * np.sin(np.arange(len(months)) / 17), index=months)
    quarters = pd.PeriodIndex(months, freq="Q")
    qexp = pd.Series(exports.to_numpy(), index=quarters).groupby(level=0).mean()
    if link:
        profit = qexp * fx.groupby(quarters).mean() * 0.55 - 3.2e12 + rng.normal(0, 1.1e12, len(qexp))
    else:
        # 수출과 무관한 랜덤워크. 이때는 '직전 분기 그대로'가 최적이라 모델이 이겨서는 안 된다.
        profit = pd.Series(5e12 + np.cumsum(rng.normal(0, 1.5e12, len(qexp))), index=qexp.index)
    profit = profit[profit.index <= pd.Period("2026Q2")]
    return profit, exports, fx


class TimingTests(unittest.TestCase):
    def test_first_k_months_needs_all_k_months(self):
        months = pd.date_range("2026-01-01", "2026-08-01", freq="MS")
        series = pd.Series(range(len(months)), index=months, dtype=float)
        got = ef.first_k_months(series, 2)
        self.assertAlmostEqual(got[pd.Period("2026Q1", freq="Q")], 0.5)   # 1·2월 평균
        self.assertAlmostEqual(got[pd.Period("2026Q3", freq="Q")], 6.5)   # 7·8월 평균
        # 3개월을 요구하면 2개월뿐인 3분기는 비어야 한다.
        self.assertTrue(np.isnan(ef.first_k_months(series, 3)[pd.Period("2026Q3", freq="Q")]))

    def test_live_features_use_only_the_quarters_first_k_months(self):
        profit, exports, fx = synthetic()
        f = ef.build_frame(profit, exports, fx, 2)
        live = pd.Period("2026Q3", freq="Q")
        expected = exports.loc["2026-07-01":"2026-08-01"].mean() * fx.loc["2026-07-01":"2026-08-01"].mean()
        self.assertAlmostEqual(f.loc[live, "exports_krw_k"], expected, places=3)
        # 9월 수출이 나중에 들어와도 앞 2개월로 만든 값은 그대로여야 한다.
        exports2 = pd.concat([exports, pd.Series([9e12], index=pd.DatetimeIndex(["2026-09-01"]))])
        fx2 = pd.concat([fx, pd.Series([1400.], index=pd.DatetimeIndex(["2026-09-01"]))])
        f2 = ef.build_frame(profit, exports2, fx2, 2)
        self.assertAlmostEqual(f2.loc[live, "exports_krw_k"], f.loc[live, "exports_krw_k"], places=3)

    def test_walk_forward_never_trains_on_the_target_quarter(self):
        profit, exports, fx = synthetic()
        f = ef.build_frame(profit, exports, fx, 2)
        from sklearn.linear_model import Ridge
        seen = []
        real_fit = Ridge.fit

        def spy(self_, X, y, *a, **k):
            seen.append(len(y))
            return real_fit(self_, X, y, *a, **k)
        Ridge.fit = spy
        try:
            oof = ef.walk_forward(f)
        finally:
            Ridge.fit = real_fit
        usable = f.dropna(subset=ef.FEATURES + ["profit"])
        first = oof.index[0]
        self.assertEqual(seen[0], int((usable.index < first).sum()))


class GateTests(unittest.TestCase):
    def test_model_beats_baselines_when_exports_drive_profit(self):
        profit, exports, fx = synthetic(link=True)
        ev = ef.evaluate(ef.walk_forward(ef.build_frame(profit, exports, fx, 2)))
        self.assertTrue(ev["beats_baselines"])
        self.assertLess(ev["mae_model"], ev["mae_random_walk"])

    def test_no_point_when_profit_is_noise(self):
        profit, exports, fx = synthetic(seed=3, link=False)
        f = ef.build_frame(profit, exports, fx, 2)
        ev = ef.evaluate(ef.walk_forward(f))
        self.assertFalse(ev["beats_baselines"])

    def test_evaluate_handles_empty_input(self):
        ev = ef.evaluate(pd.DataFrame())
        self.assertEqual(ev["n"], 0)
        self.assertFalse(ev["beats_baselines"])


class DartAndCsvTests(unittest.TestCase):
    def test_cumulative_disclosures_are_differenced_into_quarters(self):
        cumulative = {("2025", "11013"): 1e12, ("2025", "11012"): 3e12,
                      ("2025", "11014"): 6e12, ("2025", "11011"): 10e12}

        def fake(key, params):
            value = cumulative.get((params["bsns_year"], params["reprt_code"]))
            if value is None:
                return []
            return [{"account_nm": "영업이익", "fs_div": "CFS", "thstrm_add_amount": f"{value:,.0f}"}]

        saved = ef._dart_request
        ef._dart_request = fake
        try:
            got = ef.fetch_operating_profit_dart("00126380", "key", 2025, 2025)
        finally:
            ef._dart_request = saved
        self.assertEqual([float(v) for v in got], [1e12, 2e12, 3e12, 4e12])

    def test_csv_accepts_common_quarter_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "op.csv"
            path.write_text('quarter,value\n2026-Q1,1000000000000\n2026Q2,"2,000,000,000,000"\n',
                            encoding="utf-8")
            got = ef.read_operating_profit_csv(path)
            self.assertEqual(list(map(str, got.index)), ["2026Q1", "2026Q2"])
            self.assertEqual(got.iloc[1], 2e12)

    def test_csv_rejects_duplicate_quarters(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "op.csv"
            path.write_text("quarter,value\n2026Q1,1\n2026Q1,2\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                ef.read_operating_profit_csv(path)


class RenderTests(unittest.TestCase):
    def result(self, point=3e12, beats=True):
        return {
            "name": "삼성전자", "quarter": "2026년 3분기", "quarter_code": "2026Q3", "months_used": 2,
            "point": point if beats else None, "low": 1.6e12, "high": 4.9e12,
            "change_vs_last": -.066, "no_point_reason": "" if beats else "기준선을 이기지 못했습니다.",
            "last_actual": 3.2e12, "last_actual_quarter": "2026Q2",
            "evaluation": {"n": 42, "first": "2016Q1", "last": "2026Q2", "mae_model": 1.1e12,
                           "mae_random_walk": 1.5e12, "mae_seasonal_naive": 2.5e12,
                           "corr": .86, "mape_model": .21, "beats_baselines": beats},
            "profit_source": "DART_API", "profit_n": 66, "profit_first": "2010Q1", "profit_last": "2026Q2",
            "exports_last_month": "2026-08-01", "generated_at": "2026-09-08 07:00 KST",
        }

    def test_point_is_shown_when_the_gate_passes(self):
        out = ef.render_fragment(self.result())
        self.assertIn("8. 이번 분기 영업이익 추정", out)
        self.assertIn("2026년 3분기", out)
        self.assertIn("3.00조원", out)

    def test_no_point_says_so_instead_of_printing_a_number(self):
        out = ef.render_fragment(self.result(beats=False))
        self.assertIn("예측하지 않음", out)
        self.assertNotIn("80% 구간", out)


if __name__ == "__main__":
    unittest.main()


class NowcastWindowTests(unittest.TestCase):
    """이번 분기에 확보된 달만큼 과거 분기도 같은 방식으로 자른다."""

    def test_training_and_serving_use_the_same_month_count(self):
        profit, exports, fx = synthetic()
        for k in (1, 2):
            f = ef.build_frame(profit, exports, fx, k)
            live = pd.Period("2026Q3", freq="Q")
            past = pd.Period("2025Q3", freq="Q")
            # 두 분기 모두 '앞 k개월'의 평균이어야 한다.
            for quarter, months in ((live, ["2026-07-01", "2026-08-01"][:k]),
                                    (past, ["2025-07-01", "2025-08-01"][:k])):
                expected = exports.loc[months].mean() * fx.loc[months].mean()
                self.assertAlmostEqual(f.loc[quarter, "exports_krw_k"], expected, places=3)

    def test_missing_month_is_named(self):
        import re
        result = {"name": "삼성전자", "quarter": "2026년 3분기", "quarter_code": "2026Q3",
                  "months_used": 2, "months_included": "7월, 8월", "months_missing": "9월",
                  "point": 3e12, "low": 2e12, "high": 4e12, "change_vs_last": .1,
                  "no_point_reason": "", "last_actual": 2.7e12, "last_actual_quarter": "2026Q2",
                  "evaluation": {"n": 0, "note": "표본 부족", "beats_baselines": False},
                  "profit_source": "DART_API", "profit_n": 42, "profit_first": "2016Q1",
                  "profit_last": "2026Q2", "exports_last_month": "2026-08-01",
                  "generated_at": "2026-09-08 07:00 KST"}
        html = ef.render_fragment(result)
        self.assertIn("7월, 8월 반영", re.sub(r"\s+", " ", html))
        self.assertIn("9월 수출은 아직 KOSIS에 올라오지 않았습니다", html)


class NextQuarterTests(unittest.TestCase):
    """다음 분기 전망: 타깃이 아직 모르는 행을 학습에서 빼고, CLI는 발표 지연 뒤 값만 쓴다."""

    def cli(self, exports):
        months = exports.index
        return pd.DataFrame({"month": months, "value": 100 + 1.2 * np.sin((np.arange(len(months)) + 3) / 11)})

    def test_next_quarter_training_excludes_unknown_targets(self):
        profit, exports, fx = synthetic()
        f = ef.build_frame(profit, exports, fx, 2, self.cli(exports))
        from sklearn.linear_model import Ridge
        seen = []
        real_fit = Ridge.fit

        def spy(self_, X, y, *a, **k):
            seen.append(len(y))
            return real_fit(self_, X, y, *a, **k)
        Ridge.fit = spy
        try:
            oof = ef.walk_forward(f, target="profit_next", features=ef.FEATURES_NEXT + ef.CLI_FEATURES,
                                  gap=1, rw="profit_lag1", sn="profit_lag3")
        finally:
            Ridge.fit = real_fit
        first = oof.index[0]
        usable = f.dropna(subset=ef.FEATURES_NEXT + ef.CLI_FEATURES + ["profit_next", "profit_lag1", "profit_lag3"])
        # 분기 t의 학습 행은 s <= t-2 (s+1의 영업이익이 t 중에 이미 발표된 행)뿐이어야 한다.
        self.assertEqual(seen[0], int((usable.index < first - 1).sum()))

    def test_cli_column_uses_the_release_lag(self):
        profit, exports, fx = synthetic()
        cli = self.cli(exports)
        f = ef.build_frame(profit, exports, fx, 2, cli)
        # 2026Q3, k=2 → 8월 말 시점. 그때 보이는 CLI는 7월 값(8/20 공개)이다.
        level = cli.set_index("month")["value"]
        self.assertAlmostEqual(f.loc[pd.Period("2026Q3", freq="Q"), "cli_level"], level["2026-07-01"] - 100, places=6)

    def test_next_quarter_block_is_rendered(self):
        result = {"name": "삼성전자", "quarter": "2026년 3분기", "quarter_code": "2026Q3",
                  "months_used": 2, "months_included": "7월, 8월", "months_missing": "9월",
                  "point": None, "low": None, "high": None, "change_vs_last": float("nan"),
                  "no_point_reason": "x", "last_actual": 2.7e12, "last_actual_quarter": "2026Q2",
                  "evaluation": {"n": 0, "note": "표본 부족", "beats_baselines": False},
                  "profit_source": "DART_API", "profit_n": 42, "profit_first": "2016Q1",
                  "profit_last": "2026Q2", "exports_last_month": "2026-08-01",
                  "generated_at": "2026-09-08 07:00 KST", "cli_active": True, "cli_info": {},
                  "next_quarter": {"quarter": "2026년 4분기", "quarter_code": "2026Q4", "chosen": "with_cli",
                                   "point": 3.65e12, "low": 2.6e12, "high": 6.2e12, "raw_point": 3.65e12,
                                   "no_point_reason": "",
                                   "evaluation": {"n": 41, "beats_baselines": True},
                                   "evaluation_without_cli": {"n": 41, "mae_model": 1.22e12, "mae_random_walk": 1.74e12,
                                                              "mae_seasonal_naive": 2.53e12, "beats_baselines": True},
                                   "evaluation_with_cli": {"n": 41, "mae_model": 1.21e12, "mae_random_walk": 1.74e12,
                                                           "mae_seasonal_naive": 2.53e12, "beats_baselines": True}}}
        html = ef.render_fragment(result)
        self.assertIn("다음 분기(2026년 4분기) 전망", html)
        self.assertIn("3.65조원", html)
        self.assertIn("CLI 포함", html)
        self.assertIn("낙관적", html)

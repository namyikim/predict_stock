"""분기 영업이익 나우캐스트: 시점 정합, 기준선 게이트, 누적 공시 차분, CSV 파싱."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_earnings_forecast as ef  # noqa: E402
import macro_utils as mu  # noqa: E402


class PublicationSafetyTests(unittest.TestCase):
    def test_publish_aborts_when_existing_ledger_cannot_be_read(self):
        """원격 이력을 확인하지 못한 상태에서 빈 원장으로 덮어쓰면 안 된다."""
        result = {
            "name": "삼성전자", "quarter": "2026년 3분기", "quarter_code": "2026Q3",
            "months_used": 2, "months_included": "7월, 8월", "point": None,
            "low": None, "high": None, "raw_point": None, "last_actual": 1.0,
            "last_actual_quarter": "2026Q2", "profit_source": "fallback",
            "customs_info": {}, "cli_info": {}, "evaluation": {}, "provisional": {},
        }
        profit = pd.Series([1.0], index=pd.PeriodIndex(["2026Q2"], freq="Q"))
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "samsung").mkdir()
            with patch.object(sys, "argv", ["earnings", "--out", tmp, "--publish"]), \
                patch.object(ef, "analyse", return_value=(result, pd.DataFrame(), pd.DataFrame(), profit)), \
                patch.object(ef.github_pages, "token", return_value="token"), \
                patch.object(ef.github_pages, "fetch", side_effect=RuntimeError("timeout")), \
                patch.object(ef, "append_estimate", return_value=(pd.DataFrame(), False)), \
                patch.object(ef, "score_ledger", return_value=(pd.DataFrame(), 0)), \
                patch.object(ef, "render_ledger_block", return_value=""), \
                patch.object(ef, "render_fragment", return_value=""), \
                patch.object(ef.github_pages, "publish") as publish:
                with self.assertRaisesRegex(RuntimeError, "기존 추정 원장"):
                    ef.main()
                publish.assert_not_called()


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
        # 영업이익 추정은 장기 전망 탭의 2절이다(2026-09-13, 옛 조각은 renumber_fragment 가 처리).
        self.assertIn("2. 이번 분기 영업이익 추정", out)
        self.assertIn("2026년 3분기", out)
        self.assertIn("3.00조원", out)

    def test_no_point_says_so_instead_of_printing_a_number(self):
        out = ef.render_fragment(self.result(beats=False))
        self.assertIn("예측하지 않음", out)
        self.assertNotIn("80% 구간", out)


class ConformalIntervalTests(unittest.TestCase):
    """80% 구간은 표본 수를 보정한 순위(split conformal)와 예측 크기에 비례한 잔차로 만든다(2026-09-20 검토 #4).

    발행본의 구간이 두 종목 모두 14분기 중 4개(29%)만 담았다. 절대 잔차의 10/90% 분위수는 표본 10~20개에서
    꼬리를 과소평가하고 이익 규모가 100배 오가는 구간을 따라가지 못했다.
    """

    def test_rank_rule_uses_the_sample_size(self):
        model = np.full(4, 10e12)
        actual = model * (1 + np.array([.1, -.2, .3, -.4]))
        # n=4: ⌈5×0.8⌉=4 번째 → 최댓값 0.4
        self.assertAlmostEqual(ef.conformal_rel_halfwidth(actual, model), .4)
        model9 = np.full(9, 10e12)
        actual9 = model9 * (1 + np.arange(1, 10) / 10)          # 상대 잔차 .1 … .9
        # n=9: ⌈10×0.8⌉=8 번째 → 0.8 (최댓값이 아니다)
        self.assertAlmostEqual(ef.conformal_rel_halfwidth(actual9, model9), .8)

    def test_bounds_scale_with_the_point_and_have_a_floor(self):
        lo, hi = ef.interval_bounds(50e12, .3)
        self.assertAlmostEqual(lo, 35e12); self.assertAlmostEqual(hi, 65e12)
        lo, hi = ef.interval_bounds(0.1e12, .3)                  # 1조 바닥: 0.1조 예측이어도 폭은 ±0.3조
        self.assertAlmostEqual(hi - lo, 0.6e12)
        self.assertEqual(ef.interval_bounds(None, .3), (None, None))
        self.assertEqual(ef.interval_bounds(5e12, float("nan")), (None, None))
        self.assertEqual(ef.interval_bounds(5e12, None), (None, None))

    def test_coverage_uses_the_same_interval_and_only_the_past(self):
        seen = []
        real = ef.conformal_rel_halfwidth

        def spy(actual, model, **kw):
            seen.append(len(actual)); return real(actual, model, **kw)
        oof = OverconfidenceTests().oof(n=20)
        with patch.object(ef, "conformal_rel_halfwidth", side_effect=spy):
            out = ef.interval_coverage(oof)
        self.assertEqual(seen, list(range(8, 20)))                # i 번째 판정은 앞 i 개 잔차만 본다
        self.assertEqual(out["coverage_method"], "conformal_relative")
        self.assertAlmostEqual(out["coverage_nominal"], .8)

    def test_evaluate_reports_the_halfwidth_used_for_publishing(self):
        ev = ef.evaluate(OverconfidenceTests().oof())
        self.assertGreater(ev["interval_rel_halfwidth"], 0)
        self.assertEqual(ev["interval_method"], "conformal_relative")


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
        self.assertIn("앞 절반에서 모델을 선택하고 뒤 절반에서 최종 평가", html)


class ExportsFlashTests(unittest.TestCase):
    """관세청 속보: 확정치가 없는 달만 잠정 추정하고, 확정치가 오면 무시한다."""

    def test_flash_fills_missing_months_only(self):
        exports = pd.Series({pd.Timestamp("2025-08-01"): 31e9, pd.Timestamp("2025-09-01"): 33e9,
                             pd.Timestamp("2026-07-01"): 41e9, pd.Timestamp("2026-08-01"): 42e9})
        flash = pd.DataFrame({"month": pd.to_datetime(["2026-08-01", "2026-09-01"]), "days": [20, 10],
                              "semiconductor_yoy": [0.31, 0.28], "released": [None, None]})
        out, applied = ef.apply_exports_flash(exports, flash)
        self.assertEqual(out.loc["2026-08-01"], 42e9)                     # 확정치 우선
        self.assertAlmostEqual(out.loc["2026-09-01"], 33e9 * 1.28)        # 속보로 잠정 추정
        self.assertEqual([a["month"] for a in applied], ["2026-09"])

    def test_flash_needs_last_years_month(self):
        exports = pd.Series({pd.Timestamp("2026-07-01"): 41e9})
        flash = pd.DataFrame({"month": pd.to_datetime(["2026-09-01"]), "days": [20],
                              "semiconductor_yoy": [0.3], "released": [None]})
        out, applied = ef.apply_exports_flash(exports, flash)
        self.assertEqual(len(out), 1)
        self.assertEqual(applied, [])

    def test_csv_accepts_percent_strings_and_prefers_longer_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "macro_inputs").mkdir()
            (Path(tmp) / "macro_inputs" / "exports_flash.csv").write_text(
                "month,days,semiconductor_yoy\n2026-09,10,28%\n2026-09,20,0.31\n", encoding="utf-8")
            got = ef.load_exports_flash(tmp)
            self.assertEqual(len(got), 1)
            self.assertEqual(int(got["days"].iloc[0]), 20)
            self.assertAlmostEqual(got["semiconductor_yoy"].iloc[0], 0.31)


class CustomsSourceTests(unittest.TestCase):
    """관세청 원천: HS 세부코드 합산, 연 합계 행 제외, 단위 검증, KOSIS 우선."""

    XML = """<?xml version="1.0"?><response><header><resultCode>00</resultCode>
      <resultMsg>정상서비스.</resultMsg></header><body><items>
      <item><expDlr>2286791909</expDlr><hsCode>8542311000</hsCode><year>2026.06</year></item>
      <item><expDlr>11175623231</expDlr><hsCode>8542321010</hsCode><year>2026.06</year></item>
      <item><expDlr>1000000000</expDlr><hsCode>8542321030</hsCode><year>2026.07</year></item>
      <item><expDlr>99999999999</expDlr><hsCode>8542</hsCode><year>2026</year></item>
      </items></body></response>"""

    def test_monthly_rows_are_summed_and_annual_rows_dropped(self):
        got = mu.parse_customs_xml(self.XML).set_index("month")["value"]
        self.assertAlmostEqual(got.loc["2026-06-01"], 2286791909 + 11175623231)
        self.assertAlmostEqual(got.loc["2026-07-01"], 1e9)
        self.assertEqual(len(got), 2)          # year='2026' 행은 빠진다

    def test_api_error_is_raised(self):
        bad = self.XML.replace("<resultCode>00</resultCode>", "<resultCode>30</resultCode>")
        with self.assertRaises(RuntimeError):
            mu.parse_customs_xml(bad)

    def test_unit_mismatch_is_rejected(self):
        kosis = pd.DataFrame({"month": ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"],
                              "value": [3.5e10] * 6})
        thousands = kosis.assign(value=kosis["value"] / 1000)
        ok, info = mu.reconcile_customs(kosis, thousands)
        self.assertFalse(ok)
        self.assertIn("배율", info["reason"])
        ok, info = mu.reconcile_customs(kosis, kosis)
        self.assertTrue(ok)
        self.assertAlmostEqual(info["ratio_median"], 1.0)

    def test_kosis_values_are_kept_and_only_new_months_added(self):
        kosis = pd.DataFrame({"month": ["2026-05", "2026-06"], "value": [3.5e10, 3.6e10]})
        customs = pd.DataFrame({"month": ["2026-05", "2026-06", "2026-07"], "value": [1.0, 2.0, 3.9e10]})
        merged, added = mu.merge_customs_exports(kosis, customs)
        values = merged.set_index("month")["value"]
        self.assertEqual(float(values.loc[pd.Timestamp("2026-05-01")]), 3.5e10)   # KOSIS 값 유지
        self.assertEqual(float(values.loc[pd.Timestamp("2026-07-01")]), 3.9e10)
        self.assertEqual(added, ["2026-07"])

    def test_scale_is_measured_on_recent_overlap_and_tracks_drift(self):
        """배율은 천천히 흐른다(2025-09 0.84 → 2026-06 0.76). 전 구간 중앙값을 쓰면 최근에 틀린다."""
        months = pd.period_range("2025-01", periods=12, freq="M").strftime("%Y-%m")
        kosis = pd.DataFrame({"month": months, "value": [4.0e10] * 12})
        # 앞 6개월은 0.90배, 뒤 6개월은 0.80배로 내려간 계열
        customs = kosis.assign(value=[4.0e10 * 0.90] * 6 + [4.0e10 * 0.80] * 6)
        ok, scale, info = mu.customs_scale(kosis, customs)
        self.assertTrue(ok, info.get("reason"))
        self.assertAlmostEqual(scale, 1 / 0.80, places=6, msg="최근 구간이 아니라 전 구간을 봤다")
        self.assertEqual(info["window"], 6)
        self.assertLess(info["spread"], 1e-9)

    def test_unstable_ratio_is_refused(self):
        months = pd.period_range("2026-01", periods=6, freq="M").strftime("%Y-%m")
        kosis = pd.DataFrame({"month": months, "value": [4.0e10] * 6})
        customs = kosis.assign(value=[4.0e10 * r for r in (0.6, 1.4, 0.7, 1.3, 0.8, 1.2)])
        ok, scale, info = mu.customs_scale(kosis, customs)
        self.assertFalse(ok)
        self.assertEqual(scale, 1.0)
        self.assertIn("불안정", info["reason"])

    def test_too_few_overlapping_months_is_refused(self):
        kosis = pd.DataFrame({"month": ["2026-01", "2026-02", "2026-03"], "value": [4.0e10] * 3})
        ok, scale, info = mu.customs_scale(kosis, kosis)
        self.assertFalse(ok)
        self.assertEqual(scale, 1.0)
        self.assertIn("겹치는 달", info["reason"])

    def test_scale_ignores_months_kosis_does_not_have(self):
        """배율은 겹치는 과거 달로만 정한다 — 채우려는 달의 값이 배율에 들어가면 자기참조가 된다."""
        months = pd.period_range("2026-01", periods=6, freq="M").strftime("%Y-%m")
        kosis = pd.DataFrame({"month": months, "value": [4.0e10] * 6})
        customs = pd.DataFrame({"month": list(months) + ["2026-07"],
                                "value": [4.0e10 * 0.8] * 6 + [9.9e12]})   # 마지막 달은 터무니없는 값
        ok, scale, info = mu.customs_scale(kosis, customs)
        self.assertTrue(ok, info.get("reason"))
        self.assertAlmostEqual(scale, 1 / 0.8, places=6, msg="KOSIS 에 없는 달이 배율에 섞였다")

    def test_merge_rescales_added_months_and_keeps_kosis(self):
        kosis = pd.DataFrame({"month": ["2026-05", "2026-06"], "value": [3.5e10, 3.6e10]})
        customs = pd.DataFrame({"month": ["2026-05", "2026-06", "2026-07"],
                                "value": [2.8e10, 2.88e10, 3.2e10]})       # 0.8배 계열
        merged, added = mu.merge_customs_exports(kosis, customs, scale=1.25)
        values = merged.set_index("month")["value"]
        self.assertEqual(float(values.loc[pd.Timestamp("2026-05-01")]), 3.5e10)      # KOSIS 유지
        self.assertAlmostEqual(float(values.loc[pd.Timestamp("2026-07-01")]), 4.0e10)  # 3.2e10 × 1.25
        self.assertEqual(added, ["2026-07"])

    def test_merge_without_scaling_would_break_the_level(self):
        """환산하지 않으면 단차가 생겨 전월비 부호까지 뒤집힌다(2026-08 실제로 그럴 뻔했다).

        KOSIS 410 → 관세청 원값 386 은 '하락'으로 보이지만, 같은 기준으로 환산하면 487 상승이다.
        """
        kosis = pd.DataFrame({"month": ["2026-06", "2026-07"], "value": [448.2e8, 410.2e8]})
        customs = pd.DataFrame({"month": ["2026-06", "2026-07", "2026-08"],
                                "value": [339.1e8, 331.0e8, 386.1e8]})
        ok, scale, _ = mu.customs_scale(kosis, customs, min_overlap=2, window=2)
        self.assertTrue(ok)
        raw, _ = mu.merge_customs_exports(kosis, customs, scale=1.0)
        fixed, _ = mu.merge_customs_exports(kosis, customs, scale=scale)
        july = 410.2e8
        self.assertLess(float(raw.set_index("month")["value"].loc[pd.Timestamp("2026-08-01")]), july,
                        "환산 없이 넣으면 하락으로 보인다")
        self.assertGreater(float(fixed.set_index("month")["value"].loc[pd.Timestamp("2026-08-01")]), july,
                           "환산하면 실제 방향(상승)이 나온다")

    def test_merge_refuses_an_impossible_scale(self):
        kosis = pd.DataFrame({"month": ["2026-05"], "value": [3.5e10]})
        for bad in (0, -1, float("nan")):
            with self.assertRaises(ValueError):
                mu.merge_customs_exports(kosis, kosis, scale=bad)

    def test_customs_info_keeps_the_source_so_the_cache_gets_published(self):
        """source 를 잃으면 보관본이 영영 저장되지 않고, 연결이 끊긴 날 물러설 곳이 없다.

        2026-09-12 실제로 그랬다: 환산까지 잘 돌아 months_used 가 2 로 올랐는데 customs_info 를
        통째로 새로 만드는 바람에 source 가 None 이 되어 macro_history/customs_exports.csv 가
        한 번도 만들어지지 않았고, 다음 날 타임아웃에서 관세청 계열이 통째로 꺼졌다.
        """
        source = (Path(mu.__file__).resolve().parent / "tools" / "build_earnings_forecast.py").read_text(encoding="utf-8")
        self.assertIn("customs_info.update(enabled=ok", source,
                      "customs_info 를 새로 만들면 source 가 지워진다 — update 로 갱신해야 한다")
        self.assertNotIn('customs_info = {"enabled": ok', source)
        # 발행 조건이 여전히 source 를 본다는 것도 함께 고정한다.
        self.assertIn('.get("source") == "customs_api"', source)

    def test_customs_info_update_preserves_source_and_clears_stale_reason(self):
        """실제 갱신 동작. 성공하면 source 가 남고 '키 없음' 사유는 지워진다."""
        info = {"enabled": False, "reason": "DATA_GO_KR_KEY 없음"}
        info["source"] = "customs_api"
        diag = {"overlap": 12, "window": 6, "scale": 1.26, "spread": 0.047, "ratios": {}}
        info.update(enabled=True, same_size=False, ratio_median=0.807, **diag)
        info.pop("reason", None)
        self.assertEqual(info["source"], "customs_api")
        self.assertTrue(info["enabled"])
        self.assertNotIn("reason", info)

    def test_customs_info_records_the_period_for_the_sources_table(self):
        """자료원 표의 '기간' 칸은 first~last 를 읽는다. 관세청만 비면 언제 것인지 알 수 없다.

        보관본으로 물러선 날에는 이 칸이 유일하게 '얼마나 오래된 자료인가'를 알려 준다.
        """
        source = (Path(mu.__file__).resolve().parent / "tools" / "build_earnings_forecast.py").read_text(encoding="utf-8")
        self.assertIn('customs_info["first"]', source)
        self.assertIn('customs_info["last"]', source)
        # 두 경로(API·보관본) 모두를 지나는 자리여야 한다 — source 를 정한 뒤, 환산 진단 앞.
        self.assertLess(source.index('customs_info["source"] = "customs_cache"'),
                        source.index('customs_info["first"]'))
        self.assertLess(source.index('customs_info["first"]'),
                        source.index("ok, scale, diag = customs_scale"))

    def test_sources_table_shows_the_customs_period(self):
        """렌더까지 확인한다. 기간이 들어오면 표에 'YYYY-MM ~ YYYY-MM' 로 찍힌다."""
        import report_html as rh
        html = rh.fragment_sources_html({}, {"customs_info": {
            "enabled": True, "source": "customs_api", "first": "2024-10", "last": "2026-08"}})
        self.assertIn("관세청 수출입실적", html)
        self.assertIn("customs_api", html)
        self.assertIn("2024-10 ~ 2026-08", html)
        self.assertNotIn("미포함", html)

class CustomsFlashTests(unittest.TestCase):
    """관세청 주요품목별 10일 단위 잠정치.

    1~10일치는 11일, 1~20일치는 21일, 1~말일치는 익월 1일에 나온다. 확정 월별 통계보다 2~5주 빠르고,
    말일 잠정치가 나오면 그 달을 분기 나우캐스트에 바로 넣을 수 있다.

    응답 칸 이름은 공개 명세에 없다. 그래서 이름을 못박지 않고 값의 모양으로 찾는다 —
    아래 두 가지 생김새 모두에서 같은 답이 나와야 한다.
    """

    def xml(self, body, code="00"):
        return (f"<response><header><resultCode>{code}</resultCode>"
                f"<resultMsg>OK</resultMsg></header><body><items>{body}</items></body></response>")

    # 2026-09-12 한국에서 실제로 받은 응답을 그대로 옮긴 것이다(값·칸 이름 모두).
    REAL = ("<item><itemUsdAmt00>21,263,370</itemUsdAmt00><itemUsdAmt01>9,951,704</itemUsdAmt01>"
            "<itemUsdAmt02>888,770</itemUsdAmt02><itemUsdAmt10>113,074</itemUsdAmt10>"
            "<priodDt>01~10</priodDt><priodMon>202608</priodMon><priodYear>2026</priodYear></item>"
            "<item><itemUsdAmt00>55,219,260</itemUsdAmt00><itemUsdAmt01>26,034,569</itemUsdAmt01>"
            "<itemUsdAmt02>2,391,216</itemUsdAmt02><itemUsdAmt10>306,820</itemUsdAmt10>"
            "<priodDt>01~20</priodDt><priodMon>202608</priodMon><priodYear>2026</priodYear></item>")

    def test_the_real_response_shape_is_read_correctly(self):
        """품목마다 행이 아니라 한 행에 품목을 열로 늘어놓는다. 단위는 천 달러다."""
        frame = mu.parse_customs_flash_xml(self.xml(self.REAL))
        self.assertEqual(list(frame["days"]), [10, 20])
        self.assertEqual(set(frame["month"]), {pd.Timestamp("2026-08-01")})
        # itemUsdAmt01 이 반도체, 천 달러 단위 → 1~20일 26.03십억 달러
        self.assertAlmostEqual(frame.loc[1, "value"] / 1e9, 26.034569, places=5)
        self.assertAlmostEqual(frame.loc[1, "total"] / 1e9, 55.21926, places=5)

    def test_the_month_end_flash_matches_the_kosis_level(self):
        """이 '반도체'는 KOSIS 와 같은 범위다. 자릿수나 단위가 틀리면 여기서 걸린다.

        2026-09-12 한국에서 받은 보관본으로 확인했다 — 말일치 ÷ KOSIS 확정치가 겹치는 12개월에서
        1.0027~1.0129(중앙값 1.0055)였다. 잠정치라 확정치보다 0.5% 안팎 높다.
        HS 8541+8542(KOSIS 의 0.8배)와 헷갈리면 안 된다.
        """
        body = ("<item><itemUsdAmt00>99,190,340</itemUsdAmt00><itemUsdAmt01>41,173,420</itemUsdAmt01>"
                "<priodDt>01~31</priodDt><priodMon>202607</priodMon></item>")
        frame = mu.parse_customs_flash_xml(self.xml(body))
        kosis_july = 41_015_154_158.0          # macro_history/semiconductor_exports.csv 2026-07
        ratio = float(frame.loc[0, "value"]) / kosis_july
        self.assertTrue(0.99 < ratio < 1.03, f"KOSIS 대비 {ratio:.4f} — 단위나 열 번호를 확인하라")

    def test_month_end_is_one_marker_whatever_the_last_day_is(self):
        """말일이 28·30·31 로 섞여 오면 1년 전과 짝이 안 맞아 그 달이 통째로 빠진다."""
        for last in ("01~28", "01~30", "01~31"):
            body = ("<item><itemUsdAmt00>90,000,000</itemUsdAmt00>"
                    "<itemUsdAmt01>40,000,000</itemUsdAmt01>"
                    f"<priodDt>{last}</priodDt><priodMon>202602</priodMon></item>")
            with self.subTest(priodDt=last):
                self.assertEqual(mu.parse_customs_flash_xml(self.xml(body)).loc[0, "days"], 31)

    def test_a_shifted_column_is_refused_rather_than_published(self):
        """번호가 밀리면 반도체가 전체보다 커진다. 그대로 쓰면 수출이 두 배로 뛴 것처럼 보인다."""
        body = ("<item><itemUsdAmt00>1,000</itemUsdAmt00><itemUsdAmt01>9,000</itemUsdAmt01>"
                "<priodDt>01~10</priodDt><priodMon>202608</priodMon></item>")
        with self.assertRaises(mu.CustomsRejected) as caught:
            mu.parse_customs_flash_xml(self.xml(body))
        self.assertIn("순서", str(caught.exception))

    def test_month_end_rows_count_as_the_whole_month(self):
        body = ("<item><itemUsdAmt00>90,000,000</itemUsdAmt00><itemUsdAmt01>40,000,000</itemUsdAmt01>"
                "<priodDt>01~말</priodDt><priodMon>202608</priodMon></item>")
        frame = mu.parse_customs_flash_xml(self.xml(body))
        self.assertEqual(frame.loc[0, "days"], 31)

    def test_reads_a_row_whose_period_is_one_eight_digit_date(self):
        body = ("<item><statKor>반도체</statKor><expDt>20260910</expDt>"
                "<expDlr>15,300,000,000</expDlr></item>")
        frame = mu.parse_customs_flash_xml(self.xml(body))
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.loc[0, "days"], 10)
        self.assertEqual(pd.Timestamp(frame.loc[0, "month"]), pd.Timestamp("2026-09-01"))
        self.assertEqual(frame.loc[0, "value"], 15_300_000_000.0)

    def test_reads_a_row_whose_month_and_day_marker_are_separate(self):
        body = ("<item><prlstNm>반도체</prlstNm><yymm>202609</yymm><dayGb>20</dayGb>"
                "<expDlrAmt>30100000000</expDlrAmt></item>")
        frame = mu.parse_customs_flash_xml(self.xml(body))
        self.assertEqual(frame.loc[0, "days"], 20)
        self.assertEqual(pd.Timestamp(frame.loc[0, "month"]), pd.Timestamp("2026-09-01"))

    def test_other_items_are_ignored(self):
        body = ("<item><statKor>승용차</statKor><expDt>20260910</expDt><expDlr>1</expDlr></item>"
                "<item><statKor>반도체</statKor><expDt>20260910</expDt><expDlr>2</expDlr></item>")
        frame = mu.parse_customs_flash_xml(self.xml(body))
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.loc[0, "value"], 2.0)

    def test_an_exact_item_name_wins_over_a_longer_one_that_contains_it(self):
        """'반도체제조장비' 가 섞이면 합이 부푼다. 정확히 맞는 이름이 있으면 그것만 쓴다."""
        body = ("<item><statKor>반도체제조장비</statKor><expDt>20260910</expDt>"
                "<expDlr>900000000</expDlr></item>"
                "<item><statKor>반도체</statKor><expDt>20260910</expDt>"
                "<expDlr>15300000000</expDlr></item>")
        frame = mu.parse_customs_flash_xml(self.xml(body))
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.loc[0, "value"], 15_300_000_000.0)

    def test_a_longer_name_is_still_used_when_nothing_matches_exactly(self):
        """이름이 '반도체(집적회로)' 처럼 바뀌어도 조용히 빈 계열이 되지는 않게 한다."""
        body = ("<item><statKor>반도체(집적회로)</statKor><expDt>20260910</expDt>"
                "<expDlr>15300000000</expDlr></item>")
        frame = mu.parse_customs_flash_xml(self.xml(body))
        self.assertEqual(frame.loc[0, "value"], 15_300_000_000.0)

    def test_growth_rate_columns_are_not_mistaken_for_the_amount(self):
        """증감률이 금액 칸으로 뽑히면 수출이 몇 % 로 둔갑한다."""
        body = ("<item><statKor>반도체</statKor><expDt>20260910</expDt>"
                "<incdecRt>31.4</incdecRt><expDlr>15300000000</expDlr></item>")
        frame = mu.parse_customs_flash_xml(self.xml(body))
        self.assertEqual(frame.loc[0, "value"], 15_300_000_000.0)

    def test_rejection_is_reported_as_such(self):
        with self.assertRaises(mu.CustomsRejected):
            mu.parse_customs_flash_xml(self.xml("", code="99"))

    def test_unknown_field_names_are_named_in_the_error(self):
        """칸 이름이 바뀌면 조용히 빈 계열이 되면 안 된다. 무엇을 받았는지 알려야 고칠 수 있다."""
        body = "<item><품목>반도체</품목><알수없는칸>20260910</알수없는칸></item>"
        with self.assertRaises(mu.CustomsEmpty) as caught:
            mu.parse_customs_flash_xml(self.xml(body))
        self.assertIn("알수없는칸", str(caught.exception))
        self.assertIn("품목", str(caught.exception))

    def test_yoy_compares_the_same_day_marker_only(self):
        flash = pd.DataFrame({
            "month": pd.to_datetime(["2025-09-01", "2025-09-01", "2026-09-01", "2026-09-01"]),
            "days": [10, 20, 10, 20],
            "value": [100.0, 200.0, 130.0, 240.0],
        })
        yoy = mu.flash_yoy(flash).set_index("days")["semiconductor_yoy"]
        self.assertAlmostEqual(yoy.loc[10], 0.30)
        self.assertAlmostEqual(yoy.loc[20], 0.20)

    def test_yoy_skips_months_without_a_year_earlier_match(self):
        flash = pd.DataFrame({"month": pd.to_datetime(["2026-09-01"]), "days": [10], "value": [1.0]})
        self.assertEqual(len(mu.flash_yoy(flash)), 0)

    def test_refresh_writes_a_file_the_reader_understands(self):
        """받아서 쓴 파일을 그대로 다시 읽어 수출 계열에 반영되는지까지 본다."""
        flash = pd.DataFrame({
            "month": pd.to_datetime(["2025-09-01", "2026-09-01"]),
            "days": [20, 20], "value": [100.0, 130.0],
        })
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ef, "fetch_customs_flash", return_value=flash):
                raw, yoy, path = ef.refresh_exports_flash(tmp, "key", now=pd.Timestamp("2026-09-21"))
            self.assertTrue(path.exists())
            loaded = ef.load_exports_flash(tmp)
            self.assertEqual(len(loaded), 1)
            self.assertAlmostEqual(float(loaded.loc[0, "semiconductor_yoy"]), 0.30)
            exports = pd.Series([50.0], index=pd.to_datetime(["2025-09-01"])).asfreq("MS")
            merged, applied = ef.apply_exports_flash(exports, loaded)
            self.assertEqual(len(applied), 1)
            self.assertAlmostEqual(float(merged.loc[pd.Timestamp("2026-09-01")]), 65.0)

    def test_confirmed_months_are_never_overwritten_by_a_flash(self):
        flash = pd.DataFrame({"month": pd.to_datetime(["2026-09-01"]), "days": [10],
                              "semiconductor_yoy": [5.0]})
        exports = pd.Series([50.0, 61.0], index=pd.to_datetime(["2025-09-01", "2026-09-01"])).asfreq("MS")
        merged, applied = ef.apply_exports_flash(exports, flash)
        self.assertEqual(applied, [])
        self.assertEqual(float(merged.loc[pd.Timestamp("2026-09-01")]), 61.0)

    def test_403_says_the_api_needs_its_own_application(self):
        """403 을 '키가 틀렸다'로 읽으면 엉뚱한 데를 고치게 된다.

        공공데이터포털은 API 마다 따로 활용신청을 받는다. 월별 자료가 되는 키라도 이 API 에
        신청하지 않았으면 403 이다 — 2026-09-12 실제로 그랬다.
        """
        class Forbidden(Exception):
            code = 403

            def __str__(self):
                return "HTTPError 403 Forbidden"

        secret = "SECRETKEY1234567890ABCDEFGH"
        with patch.object(mu.exports, "open_url", side_effect=Forbidden()):
            with self.assertRaises(RuntimeError) as caught:
                mu.fetch_customs_flash("2026-08-01", "2026-09-12", secret, retries=1)
        message = str(caught.exception)
        self.assertIn("활용신청", message)
        self.assertIn("15157908", message)
        # 지문(길이·앞뒤 4자)은 남기되 키 자체는 절대 나오면 안 된다.
        self.assertNotIn(secret, message)

    def test_the_archive_copy_is_written_and_read_back(self):
        """Actions 는 미국에서 돌아 관세청이 자주 막힌다. 보관본이 없으면 이 기능은 대부분 죽는다."""
        flash = pd.DataFrame({
            "month": pd.to_datetime(["2025-09-01", "2026-09-01"]),
            "days": [20, 20], "value": [100.0, 130.0],
        })
        with tempfile.TemporaryDirectory() as tmp:
            ef._write_exports_flash(tmp, flash)
            copy = Path(tmp) / "customs_flash.csv"
            self.assertTrue(copy.exists(), "보관본으로 올릴 원자료가 저장돼야 한다")
            read_back = ef.load_flash_cache(tmp)
            self.assertEqual(len(read_back), 2)
            self.assertEqual(pd.Timestamp(read_back["month"].max()), pd.Timestamp("2026-09-01"))
            self.assertAlmostEqual(float(ef.flash_yoy(read_back)["semiconductor_yoy"].iloc[0]), 0.30)

    def test_missing_archive_reads_as_nothing_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(ef.load_flash_cache(tmp))

    def test_publish_condition_uses_the_api_source_only(self):
        """보관본을 그대로 다시 올리면 같은 내용이 매일 커밋된다. 새로 받았을 때만 올린다."""
        source = (Path(ef.__file__)).read_text(encoding="utf-8")
        self.assertIn('.get("source") == "customs_flash_api"', source)
        self.assertIn("macro_history/customs_flash.csv", source)

    def test_the_report_shows_the_flash_row_with_how_many_days(self):
        import report_html as rh
        html = rh.fragment_sources_html({}, {
            "flash_info": {"enabled": True, "source": "customs_flash_api",
                           "first": "2025-08", "last": "2026-09", "last_days": 20},
            "flash_applied": [{"month": "2026-09", "days": 20, "yoy": 0.3}]})
        self.assertIn("관세청 10일 잠정치", html)
        self.assertIn("customs_flash_api", html)
        self.assertIn("마지막 20일치", html)
        self.assertIn("2026-09", html)


class CustomsRequestTests(unittest.TestCase):
    """요청을 어떤 모양으로 보내는가 — 조회 창 크기와 인증키 형태."""

    def test_actions_requests_at_most_two_windows(self):
        """창이 적을수록 해외에서 끊길 기회가 적다. 18개월 = 12개월 창 두 개."""
        source = (Path(mu.__file__).resolve().parent / "tools" / "build_earnings_forecast.py").read_text(encoding="utf-8")
        self.assertIn("DateOffset(months=18)", source)
        self.assertNotIn("DateOffset(months=30)", source)
        windows = mu.month_windows("2025-04-01", "2026-09-12")
        self.assertEqual(len(windows), 2)

    def test_encoded_key_is_kept_as_is(self):
        """포털의 인코딩 키를 디코딩해 버리면 관세청 API 가 거부한다(2026-09-11 실제 호출로 확인).

        변환은 key_variants 가 맡고, 로더는 저장된 문자열을 그대로 돌려줘야 한다.
        """
        import os
        from data_sources import exports as ex
        saved = os.environ.get("DATA_GO_KR_KEY")
        os.environ["DATA_GO_KR_KEY"] = "abc%2Bdef%3D"
        try:
            self.assertEqual(ex.data_go_kr_key(), "abc%2Bdef%3D")
        finally:
            if saved is None:
                os.environ.pop("DATA_GO_KR_KEY", None)
            else:
                os.environ["DATA_GO_KR_KEY"] = saved



class EarningsLedgerTests(unittest.TestCase):
    """영업이익 추정도 원장에 남기고 실제가 나오면 채점한다."""

    def result(self, k=2, point=122e12):
        return {"target": "samsung", "quarter_code": "2026Q3", "months_used": k,
                "months_included": "7월, 8월", "point": point, "low": 118e12, "high": 128e12,
                "last_actual": 89e12,
                "evaluation": {"beats_baselines": True, "mae_model": 4.3e12,
                               "mae_random_walk": 5.6e12, "mae_seasonal_naive": 11.7e12,
                               "n": 22, "shrink_slope": .5}}

    def test_one_row_per_quarter_and_month_count(self):
        ledger = ef.read_ledger(Path(tempfile.mkdtemp()) / "none.csv")
        ledger, added = ef.append_estimate(ledger, self.result(), "r1")
        self.assertTrue(added)
        ledger, again = ef.append_estimate(ledger, self.result(point=999e12), "r2")
        self.assertFalse(again, "같은 (분기, 반영 개월)은 첫 추정만 남아야 한다")
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger["point"].iloc[0], 122e12)     # 나중 값으로 덮이지 않는다
        ledger, added3 = ef.append_estimate(ledger, self.result(k=3), "r3")
        self.assertTrue(added3)
        self.assertEqual(len(ledger), 2)

    def test_confirmed_overwrites_provisional_and_is_final(self):
        ledger = ef.read_ledger(Path(tempfile.mkdtemp()) / "none.csv")
        ledger, _ = ef.append_estimate(ledger, self.result(), "r1")
        ledger, n = ef.score_ledger(ledger, pd.Series(dtype=float), {"2026Q3": 120e12})
        self.assertEqual((n, ledger["actual_source"].iloc[0]), (1, "provisional"))
        confirmed = pd.Series({pd.Period("2026Q3", freq="Q"): 118e12})
        ledger, n = ef.score_ledger(ledger, confirmed, {"2026Q3": 120e12})
        self.assertEqual((n, ledger["actual_source"].iloc[0]), (1, "confirmed"))
        self.assertAlmostEqual(ledger["actual"].iloc[0], 118e12)
        self.assertAlmostEqual(ledger["error"].iloc[0], 4e12)
        ledger, n = ef.score_ledger(ledger, confirmed, {})
        self.assertEqual(n, 0, "확정치로 채점한 행은 다시 건드리지 않는다")

    def test_round_trip_through_csv_keeps_numbers(self):
        tmp = Path(tempfile.mkdtemp()) / "l.csv"
        ledger = ef.read_ledger(tmp)
        ledger, _ = ef.append_estimate(ledger, self.result(), "r1")
        ledger.to_csv(tmp, index=False)
        again = ef.read_ledger(tmp)
        self.assertEqual(again["point"].iloc[0], 122e12)
        self.assertEqual(int(again["months_used"].iloc[0]), 2)


class ProvisionalDisclosureTests(unittest.TestCase):
    """잠정실적 공시는 확정치보다 5주 빠르지만 본문을 읽어야 해서 불안정하다."""

    def test_parses_amount_with_a_declared_unit(self):
        html_text = ('<table><tr><td>단위 : 억원</td></tr>'
                     '<tr><td>영업이익</td><td>1,222,000</td><td>895,000</td></tr></table>')
        self.assertAlmostEqual(ef.parse_provisional_amount(html_text), 122.2e12)

    def test_refuses_without_a_unit_or_without_the_line(self):
        self.assertIsNone(ef.parse_provisional_amount("<p>영업이익 1,222,000</p>"))
        self.assertIsNone(ef.parse_provisional_amount("단위 : 억원 매출액 900,000"))

    def test_picks_only_provisional_filings(self):
        got = ef.find_provisional([
            {"report_nm": "연결재무제표기준영업(잠정)실적(공정공시)", "rcept_dt": "20261007"},
            {"report_nm": "분기보고서 (2026.09)", "rcept_dt": "20261114"},
        ])
        self.assertEqual([d["rcept_dt"] for d in got], ["20261007"])


class ModelChoiceRuleTests(unittest.TestCase):
    """CLI 포함/제외 선택 구간과 최종 성능 평가 구간을 시간 순서로 분리한다."""

    def test_selection_and_evaluation_use_separate_oof_periods(self):
        source = (Path(__file__).resolve().parents[1] / "tools" / "build_earnings_forecast.py").read_text(encoding="utf-8")
        self.assertIn("selection_oof, evaluation_oof = split_selection_evaluation(oof_n)", source)
        self.assertIn('next_results["without_cli"]["selection"].get("beats_baselines")', source)
        self.assertIn('chosen = "with_cli" if (not simple_ok and cli_ok) else "without_cli"', source)

    def test_split_keeps_later_period_only_for_final_evaluation(self):
        index = pd.period_range("2018Q1", periods=9, freq="Q")
        oof = pd.DataFrame({"actual": range(9)}, index=index)
        selection, evaluation = ef.split_selection_evaluation(oof)
        self.assertEqual(list(selection.index), list(index[:4]))
        self.assertEqual(list(evaluation.index), list(index[4:]))
        self.assertLess(selection.index.max(), evaluation.index.min())


class UnitPriceFeatureTests(unittest.TestCase):
    """수출액은 가격 × 물량이라 그 자체로는 이익과의 관계가 국면마다 다르다.

    2026-09-13: 3분기 추정 122.9조 vs 증권사 컨센서스 111조. 수출액 하나만 보면 '가격이 올라서'와
    '물량이 늘어서'를 구분하지 못하는 것이 원인 후보라, 관세청 중량으로 단가를 만들어 재 본다.
    """

    def quantity(self, n=141):
        months = pd.date_range("2015-01-01", periods=n, freq="MS")
        rng = np.random.default_rng(0)
        return pd.DataFrame({"month": months,
                             "unit_price": np.linspace(800, 3000, n) * (1 + rng.normal(0, .03, n)),
                             "volume": np.linspace(1e6, 1.4e6, n) * (1 + rng.normal(0, .05, n))})

    def test_parser_reads_weight_only_when_asked(self):
        from data_sources.exports import parse_customs_xml
        xml = ('<response><header><resultCode>00</resultCode><resultMsg>정상</resultMsg></header>'
               '<body><items><item><expDlr>5170747</expDlr><expWgt>4478</expWgt>'
               '<hsCode>8541101000</hsCode><year>2026.01</year></item></items></body></response>')
        self.assertEqual(list(parse_customs_xml(xml).columns), ["month", "value"])
        self.assertIn("weight", parse_customs_xml(xml, with_weight=True).columns)

    def test_unit_price_divides_value_by_weight(self):
        from data_sources.exports import customs_unit_price
        frame = pd.DataFrame({"month": ["2026-01-01"], "value": [1.0e9], "weight": [1.0e6]})
        out = customs_unit_price(frame)
        self.assertAlmostEqual(out["unit_price"].iloc[0], 1000.0)
        self.assertAlmostEqual(out["volume"].iloc[0], 1.0e6)

    def test_zero_weight_does_not_divide_by_zero(self):
        from data_sources.exports import customs_unit_price
        frame = pd.DataFrame({"month": ["2026-01-01", "2026-02-01"], "value": [1e9, 1e9],
                              "weight": [0.0, 1e6]})
        out = customs_unit_price(frame)
        self.assertEqual(len(out), 1)                     # 0 인 달은 빠진다
        self.assertEqual(out["month"].iloc[0], pd.Timestamp("2026-02-01"))

    def test_frame_adds_price_and_volume_features(self):
        months = pd.date_range("2015-01-01", periods=141, freq="MS")
        quantity = self.quantity()
        exports = pd.Series(quantity["unit_price"].to_numpy() * quantity["volume"].to_numpy(),
                            index=months)
        fx = pd.Series(1300.0, index=months)
        quarters = pd.PeriodIndex(pd.date_range("2016-01-01", "2026-06-01", freq="QS"), freq="Q")
        profit = pd.Series(np.linspace(6e12, 90e12, len(quarters)), index=quarters)
        frame = ef.build_frame(profit, exports, fx, 3, quantity=quantity)
        for column in ("unit_price_k", "volume_k", "unit_price_yoy", "volume_yoy"):
            self.assertIn(column, frame.columns)
        self.assertLess(frame["unit_price_yoy"].isna().mean(), 0.2)

    def test_frame_without_quantity_is_unchanged(self):
        # 단가 자료가 없으면 예전과 똑같이 동작해야 한다.
        months = pd.date_range("2015-01-01", periods=141, freq="MS")
        exports = pd.Series(np.linspace(1e9, 4e9, 141), index=months)
        fx = pd.Series(1300.0, index=months)
        quarters = pd.PeriodIndex(pd.date_range("2016-01-01", "2026-06-01", freq="QS"), freq="Q")
        profit = pd.Series(np.linspace(6e12, 90e12, len(quarters)), index=quarters)
        frame = ef.build_frame(profit, exports, fx, 3)
        self.assertNotIn("unit_price_k", frame.columns)

    def test_features_are_only_a_candidate_not_published(self):
        source = (ROOT / "tools" / "build_earnings_forecast.py").read_text(encoding="utf-8")
        # 발행 모델(FEATURES)에 들어가면 안 된다. 쌍체 비교로 재고 관찰만 한다.
        headline = source[source.index("FEATURES = ["):source.index("FEATURES = [") + 120]
        self.assertNotIn("unit_price", headline)
        self.assertNotIn("volume", headline)
        self.assertIn("UNIT_PRICE_FEATURES", source)
        self.assertIn("지금은 관찰만 합니다", source)


class OverconfidenceTests(unittest.TestCase):
    """'80% 구간'이 실제로 몇 %를 담는지, 추정이 학습 범위 밖인지를 보고서에 적는다.

    2026-09-13 측정: 삼성전자 구간 적중률 29%(14분기 중 4개). 명목 80%와 차이가 크다.
    그리고 2026Q2 89.5조는 2016~2025년 이력(대부분 5~20조)을 크게 벗어난다.
    """

    def oof(self, n=30, bias=0.0):
        quarters = pd.PeriodIndex(pd.date_range("2016-01-01", periods=n, freq="QS"), freq="Q")
        rng = np.random.default_rng(0)
        actual = np.linspace(6e12, 90e12, n)
        model = actual * (1 - bias) + rng.normal(0, 1e12, n)
        return pd.DataFrame({"actual": actual, "model": model,
                             "random_walk": np.r_[actual[0], actual[:-1]],
                             "seasonal_naive": np.r_[actual[:4], actual[:-4]]}, index=quarters)

    def test_coverage_is_measured_with_only_past_residuals(self):
        out = ef.interval_coverage(self.oof())
        self.assertEqual(out["coverage_n"], 30 - 8)
        self.assertLessEqual(out["coverage_hit"], out["coverage_n"])
        self.assertAlmostEqual(out["coverage_nominal"], 0.80)

    def test_biased_model_shows_poor_coverage(self):
        # 계속 과소 추정하면 구간이 실제를 못 담는다 — 지금 상황이 그렇다.
        good = ef.interval_coverage(self.oof(bias=0.0))["coverage_rate"]
        biased = ef.interval_coverage(self.oof(bias=0.30))["coverage_rate"]
        self.assertLess(biased, good)

    def test_short_series_reports_nothing(self):
        self.assertEqual(ef.interval_coverage(self.oof(n=9))["coverage_n"], 0)

    def test_evaluate_includes_coverage(self):
        out = ef.evaluate(self.oof())
        self.assertIn("coverage_rate", out)

    def test_extrapolation_note_fires_above_history(self):
        history = pd.Series([6e12, 12e12, 20e12, 89.5e12],
                            index=pd.PeriodIndex(["2025Q3", "2025Q4", "2026Q1", "2026Q2"], freq="Q"))
        note = ef.extrapolation_note(history, 122.9e12)
        self.assertIsNotNone(note)
        self.assertAlmostEqual(note["ratio"], 122.9 / 89.5, places=2)
        self.assertEqual(note["quarter"], "2026Q2")

    def test_extrapolation_note_silent_inside_history(self):
        history = pd.Series([6e12, 89.5e12],
                            index=pd.PeriodIndex(["2026Q1", "2026Q2"], freq="Q"))
        self.assertIsNone(ef.extrapolation_note(history, 50e12))
        self.assertIsNone(ef.extrapolation_note(history, None))

    def test_warning_text_is_next_to_the_estimate(self):
        source = (ROOT / "tools" / "build_earnings_forecast.py").read_text(encoding="utf-8")
        estimate = source.index('<h4 style="font-size:14px;margin:18px 0 6px">추정</h4>')
        warning = source.index("학습 이력의 최대치", estimate)
        verification = source.index("검증 — 기준선을 이기는가", estimate)
        self.assertLess(warning, verification, "경고가 검증 절보다 뒤에 있으면 대부분 놓친다")
        self.assertIn("만 담았습니다", source)


class LeverageFeatureTests(unittest.TestCase):
    """영업 레버리지: 많이 팔면서 빠르게 늘 때 이익은 비례 이상으로 늘어난다.

    2026-09-13 측정: 수출 증가율과 오차의 상관이 +0.62 였다(수출이 늘 때 과소 추정).
    규모 × 증가율 항을 넣으니 -0.31 로 떨어지고, 분할 검증 뒤 절반 -9%, 하이닉스 -24% 로
    재현됐다. 오늘 시험한 아홉 가지 중 유일하게 살아남았다(로그·표준화·이익률·변화율·최근가중·
    보정·단가는 모두 실패).
    """

    def frame(self):
        months = pd.date_range("2015-01-01", periods=141, freq="MS")
        rng = np.random.default_rng(1)
        exports = pd.Series(np.linspace(1e9, 4e9, 141) * (1 + rng.normal(0, .05, 141)), index=months)
        quarters = pd.PeriodIndex(pd.date_range("2016-01-01", "2026-06-01", freq="QS"), freq="Q")
        profit = pd.Series(np.linspace(6e12, 90e12, len(quarters)), index=quarters)
        return ef.build_frame(profit, exports, pd.Series(1300.0, index=months), 3)

    def test_interaction_terms_exist_and_are_products(self):
        frame = self.frame()
        for column in ef.LEVERAGE_FEATURES:
            self.assertIn(column, frame.columns)
        row = frame.dropna(subset=["exports_krw_k", "exports_qoq", "scale_x_growth"]).iloc[0]
        self.assertAlmostEqual(row["scale_x_growth"], row["exports_krw_k"] * row["exports_qoq"],
                               delta=abs(row["scale_x_growth"]) * 1e-9)

    def test_no_new_data_source_needed(self):
        # 두 항 모두 이미 쓰는 값의 곱이다. 새 자료를 받지 않는다.
        source = (ROOT / "tools" / "build_earnings_forecast.py").read_text(encoding="utf-8")
        self.assertIn('f["scale_x_growth"] = f["exports_krw_k"] * f["exports_qoq"]', source)
        self.assertIn('f["scale_x_yoy"] = f["exports_krw_k"] * f["exports_yoy"]', source)

    def test_ablation_reports_bias_not_only_mae(self):
        # 이 항의 목적은 편향 제거다. MAE 만 보면 두 분기가 지배해 판단이 흔들린다.
        source = (ROOT / "tools" / "build_earnings_forecast.py").read_text(encoding="utf-8")
        self.assertIn('"bias_with"', source)
        self.assertIn('"bias_without"', source)
        self.assertIn("증가율-오차 상관", source)

    def test_not_in_the_published_model(self):
        source = (ROOT / "tools" / "build_earnings_forecast.py").read_text(encoding="utf-8")
        headline = source[source.index("FEATURES = ["):source.index("FEATURES = [") + 120]
        self.assertNotIn("scale_x", headline)
        self.assertIn("LEVERAGE_FEATURES", source)

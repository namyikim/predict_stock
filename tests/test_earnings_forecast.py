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
import macro_utils as mu  # noqa: E402


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

    def test_encoded_key_is_decoded(self):
        import os
        saved = os.environ.get("DATA_GO_KR_KEY")
        os.environ["DATA_GO_KR_KEY"] = "abc%2Bdef%3D"
        try:
            self.assertEqual(mu.data_go_kr_key(), "abc+def=")
        finally:
            if saved is None:
                os.environ.pop("DATA_GO_KR_KEY", None)
            else:
                os.environ["DATA_GO_KR_KEY"] = saved


class EarningsLedgerTests(unittest.TestCase):
    """8절 추정도 원장에 남기고 실제가 나오면 채점한다."""

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
    """CLI 포함/제외를 성능 순위로 고르면 선택과 평가가 같은 표본이 된다. 규칙으로 정한다."""

    def test_simpler_model_wins_unless_it_fails_the_baselines(self):
        source = (Path(__file__).resolve().parents[1] / "tools" / "build_earnings_forecast.py").read_text(encoding="utf-8")
        self.assertIn('chosen = "with_cli" if (not simple_ok and cli_ok) else "without_cli"', source)
        # MAE 비교로 고르는 옛 규칙이 남아 있으면 안 된다.
        self.assertNotIn('next_results["with_cli"]["evaluation"].get("mae_model", np.inf)\n              <', source)

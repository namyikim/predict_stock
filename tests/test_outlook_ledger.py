# -*- coding: utf-8 -*-
"""장기 전망 탭의 전망을 처음 값 그대로 기록하고 발표된 값으로 채점한다(2026-09-27)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import outlook_ledger as ol  # noqa: E402


def longterm(**over):
    lt = {
        "as_of": "2026-08-31",
        "cli_outlook": {"last_month": "2026-08", "last_value": 102.0, "rows": [
            {"horizon": 1, "month": "2026-09", "point": 101.8, "low": 101.6, "high": 102.0, "model": "ar"},
            {"horizon": 2, "month": "2026-10", "point": 101.6, "low": 101.2, "high": 102.0, "model": "ar"}]},
        "g20_outlook": {"g20_last_month": "2026-08", "g20_last": 100.3, "growth_last_month": "2026-06",
                        "growth_last": 0.70, "rows": [{"horizon": 1, "month": "2026-09", "point": 100.29}],
                        "growth_rows": [{"horizon": 3, "month": "2026-09", "point": 0.60, "low": .5, "high": .8}]},
        "evaluation": {"6": {"beats_zero": True}, "3": {"beats_zero": False}},
        "forecast": {"6": {"point": 0.10}, "3": {"point": 0.05}},
    }
    lt.update(over)
    return lt


EARNINGS = {"flash_applied": [{"month": "2026-09", "days": 20, "yoy": 1.0, "monthly_usd": 60e9}]}


class CollectAndRecordTests(unittest.TestCase):
    def test_only_what_the_page_shows_is_recorded(self):
        rows = ol.collect_forecasts(longterm(), EARNINGS)
        series = [r["series"] for r in rows]
        self.assertEqual(series.count("cli_kor"), 2)
        self.assertEqual(series.count("stock_return"), 1, "검증을 통과한 6개월만(3개월은 화면에 없다)")
        stock = next(r for r in rows if r["series"] == "stock_return")
        self.assertEqual((stock["info_as_of"], stock["target_period"]), ("2026-08", "2027-02"))
        semi = next(r for r in rows if r["series"] == "semi_exports_month")
        self.assertAlmostEqual(semi["baseline"], 30e9, "작년 같은 달 = 환산값 / (1+증가율)")

    def test_first_value_is_kept_and_never_rewritten(self):
        ledger, added = ol.record(ol.read_ledger_text(""), ol.collect_forecasts(longterm(), EARNINGS))
        changed = longterm()
        changed["cli_outlook"]["rows"][0]["point"] = 99.0        # 같은 달 기준의 전망을 다시 냈다
        ledger, again = ol.record(ledger, ol.collect_forecasts(changed, EARNINGS))
        self.assertEqual(again, 0)
        self.assertEqual(ledger.set_index("record_id").loc["cli_kor|2026-08|2026-09", "point"], 101.8)


class ScoringTests(unittest.TestCase):
    def ledger(self):
        ledger, _ = ol.record(ol.read_ledger_text(""), ol.collect_forecasts(longterm(), EARNINGS))
        return ledger

    def test_value_forecast_is_scored_against_the_baseline_and_band(self):
        ledger, n = ol.score(self.ledger(), {"cli_kor": lambda row: {"2026-09": 101.7}.get(row["target_period"])})
        self.assertEqual(n, 1)
        row = ledger.set_index("record_id").loc["cli_kor|2026-08|2026-09"]
        self.assertEqual(row["status"], "scored")
        self.assertAlmostEqual(row["error"], 0.1)
        self.assertAlmostEqual(row["baseline_abs_error"], 0.3)       # 마지막 값 102.0 그대로
        self.assertEqual(row["in_band"], 1.0)
        self.assertEqual(ledger.set_index("record_id").loc["cli_kor|2026-08|2026-10", "status"], "pending")

    def test_first_release_is_kept_when_the_value_is_revised_later(self):
        ledger, _ = ol.score(self.ledger(), {"cli_kor": lambda row: 101.7 if row["target_period"] == "2026-09" else None})
        ledger, n = ol.score(ledger, {"cli_kor": lambda row: 105.0 if row["target_period"] == "2026-09" else None})
        self.assertEqual(n, 0)
        self.assertEqual(ledger.set_index("record_id").loc["cli_kor|2026-08|2026-09", "actual"], 101.7)

    def test_level_reach_resolves_early_on_a_hit_or_after_the_deadline(self):
        prices = pd.Series([100., 105., 111., 108.], index=pd.bdate_range("2026-09-25", periods=4))
        row = {"target_period": "110", "note": "issued=2026-09-25;days=126"}
        self.assertEqual(ol.level_reach_outcome(prices, row), 1.0, "기한 전이라도 닿으면 일어남")
        self.assertIsNone(ol.level_reach_outcome(prices, {**row, "target_period": "120"}), "아직 모른다")
        self.assertEqual(ol.level_reach_outcome(prices, {**row, "target_period": "120", "note": "issued=2026-09-25;days=3"}), 0.0)

    def test_probability_is_scored_with_brier(self):
        ledger, _ = ol.record(ol.read_ledger_text(""), [{
            "record_id": "level_reach|2026-09|110|110@3d", "series": "level_reach", "label": "가격", "unit": "probability",
            "kind": "probability", "info_as_of": "2026-09", "target_period": "110", "horizon": "6개월",
            "detail": "110@3d", "probability": 0.7, "note": "issued=2026-09-25;days=3"}])
        prices = pd.Series([100., 105., 111.], index=pd.bdate_range("2026-09-25", periods=3))
        ledger, n = ol.score(ledger, {"level_reach": lambda row: ol.level_reach_outcome(prices, row)})
        self.assertEqual(n, 1)
        self.assertAlmostEqual(ledger["brier"].iloc[0], 0.09)

    def test_stock_return_uses_month_end_closes_only_after_the_month_ends(self):
        idx = pd.to_datetime(["2026-08-31", "2027-02-26", "2027-03-02"])
        prices = pd.Series([100., 110., 111.], index=idx)
        row = {"info_as_of": "2026-08", "target_period": "2027-02"}
        self.assertAlmostEqual(ol.actuals_from(tempfile.mkdtemp(), prices=prices)["stock_return"](row), np.log(1.1))
        self.assertIsNone(ol.actuals_from(tempfile.mkdtemp(), prices=prices.iloc[:2])["stock_return"](row),
                          "2월 마지막 날 뒤 종가가 없으면 아직 달이 안 끝났다")

    def test_flash_months_are_not_actuals_until_the_full_month_arrives(self):
        snap = {"series": [{"month": "2026-08", "value": 5.0}, {"month": "2026-09", "value": 6.0}],
                "applied": [{"month": "2026-09"}]}
        actual = ol.exports_actuals(snap)
        self.assertEqual(list(actual.index), ["2026-08"])

    def test_cli_actuals_are_read_from_the_runs_files(self):
        d = Path(tempfile.mkdtemp())
        (d / "macro_cache").mkdir()
        pd.DataFrame({"month": ["2026-09-01"], "value": [101.7]}).to_csv(d / "macro_cache" / "cli_kor.csv", index=False)
        self.assertEqual(ol.actuals_from(d)["cli_kor"]({"target_period": "2026-09"}), 101.7)


class MergeAndRenderTests(unittest.TestCase):
    def test_merge_keeps_remote_scores_and_adds_our_new_rows(self):
        ours, _ = ol.record(ol.read_ledger_text(""), ol.collect_forecasts(longterm(), EARNINGS))
        remote, _ = ol.score(ours.copy(), {"cli_kor": lambda row: 101.7 if row["target_period"] == "2026-09" else None})
        remote = remote.iloc[:3]                                    # 원격에는 일부만, 대신 하나는 채점됨
        merged = ol.merge_ledgers(ol.to_csv(remote), ours)
        self.assertEqual(len(merged), len(ours))
        self.assertEqual(merged.set_index("record_id").loc["cli_kor|2026-08|2026-09", "status"], "scored")

    def test_merge_rescores_remote_first_forecast_with_local_observation(self):
        remote, _ = ol.record(ol.read_ledger_text(""), ol.collect_forecasts(longterm()), stamp="2026-09-01 07:00")
        remote = remote.iloc[[0]].copy()
        remote.loc[:, "point"] = 100.
        remote.loc[:, "baseline"] = 105.
        remote.loc[:, "low"] = 95.
        remote.loc[:, "high"] = 108.
        ours = remote.copy()
        ours.loc[:, "point"] = 120.
        ours.loc[:, "baseline"] = 110.
        ours.loc[:, "high"] = 125.
        ours.loc[:, "issued_at_kst"] = "2026-09-02 07:00"
        ours, _ = ol.score(ours, {"cli_kor": lambda row: 110.}, stamp="2026-10-01 09:00")
        got = ol.merge_ledgers(ol.to_csv(remote), ours).iloc[0]
        self.assertEqual(got["point"], 100.)
        self.assertEqual(got["issued_at_kst"], "2026-09-01 07:00")
        self.assertEqual(got["actual_seen_kst"], "2026-10-01 09:00")
        self.assertEqual(got["actual"], 110.)
        self.assertEqual(got["error"], -10.)
        self.assertEqual(got["abs_error"], 10.)
        self.assertEqual(got["baseline_abs_error"], 5.)
        self.assertEqual(got["in_band"], 0.)

    def test_merge_rescores_probability_for_stock_and_metals(self):
        for series in ("phase_up_12m", "bucket_up_12m", "level_reach"):
            with self.subTest(series=series):
                row = {"record_id": series + "|2026-09|2027-09", "series": series,
                       "kind": "probability", "probability": .2, "baseline_probability": .5,
                       "info_as_of": "2026-09", "target_period": "2027-09"}
                remote, _ = ol.record(ol.read_ledger_text(""), [row], stamp="2026-09-01 07:00")
                ours = remote.copy()
                ours.loc[:, "probability"] = .8
                ours.loc[:, "baseline_probability"] = .9
                ours, _ = ol.score(ours, {series: lambda row: 1.})
                got = ol.merge_ledgers(ol.to_csv(remote), ours).iloc[0]
                self.assertEqual(got["probability"], .2)
                self.assertAlmostEqual(got["brier"], .64)
                self.assertAlmostEqual(got["baseline_brier"], .25)
                self.assertEqual(got["outcome"], 1.)
                again = ol.merge_ledgers(ol.to_csv(got.to_frame().T), ours).iloc[0]
                pd.testing.assert_series_equal(again, got, check_names=False)

    def test_merge_does_not_transfer_outcome_from_a_different_observation_window(self):
        row = {"record_id": "level_reach|2026-09|110|110@126d", "series": "level_reach",
               "kind": "probability", "probability": .2, "target_period": "110",
               "note": "issued=2026-09-01;days=126"}
        remote, _ = ol.record(ol.read_ledger_text(""), [row])
        ours = remote.copy()
        ours.loc[:, "note"] = "issued=2026-09-02;days=126"
        ours, _ = ol.score(ours, {"level_reach": lambda row: 1.})
        got = ol.merge_ledgers(ol.to_csv(remote), ours).iloc[0]
        self.assertEqual(got["status"], "pending")
        self.assertEqual(got["note"], "issued=2026-09-01;days=126")
        self.assertTrue(pd.isna(got["actual"]))

    def test_remote_score_is_immutable_even_when_local_score_differs(self):
        remote, _ = ol.record(ol.read_ledger_text(""), ol.collect_forecasts(longterm()))
        ours = remote.copy()
        remote, _ = ol.score(remote, {"cli_kor": lambda row: 100.}, stamp="2026-10-01 09:00")
        ours.loc[:, "point"] = 120.
        ours, _ = ol.score(ours, {"cli_kor": lambda row: 110.}, stamp="2026-10-02 09:00")
        got = ol.merge_ledgers(ol.to_csv(remote), ours)
        expected = ol.read_ledger_text(ol.to_csv(remote))
        pd.testing.assert_frame_equal(got, expected, check_dtype=False)

    def test_render_explains_pending_rows_and_shows_scores(self):
        ledger, _ = ol.record(ol.read_ledger_text(""), ol.collect_forecasts(longterm(), EARNINGS))
        html = ol.render(ledger)
        self.assertIn("3. 지난 전망은 맞았나", html)
        self.assertIn("아직 채점 전", html)
        ledger, _ = ol.score(ledger, {"cli_kor": lambda row: 101.7 if row["target_period"] == "2026-09" else None})
        html = ol.render(ledger)
        self.assertIn("최근 채점", html)
        self.assertIn("더 가까운 비율 100%", html)


class MatureReachTests(unittest.TestCase):
    def ledger(self):
        rows = [{"record_id": f"reach-{level}-{days}", "series": "level_reach",
                 "label": "가격 도달 확률", "unit": "probability", "kind": "probability",
                 "info_as_of": "2026-09", "target_period": str(level), "horizon": label,
                 "probability": .7, "note": f"issued=2026-09-25;days={days}"}
                for days, label in ((3, "3거래일"), (5, "5거래일")) for level in (110, 120)]
        return ol.record(ol.read_ledger_text(""), rows)[0]

    def test_early_hit_is_recorded_but_not_in_performance(self):
        prices = pd.Series([100., 105., 111.], index=pd.bdate_range("2026-09-25", periods=3))
        ledger, _ = ol.score(self.ledger(), {"level_reach": lambda r: ol.level_reach_outcome(prices, r)})
        self.assertEqual((ledger.status == "scored").sum(), 2)
        output = ol.render(ledger, prices=prices)
        self.assertNotIn("실제 100%", output)
        self.assertNotIn("Brier 0.090", output)
        self.assertIn("조기 도달 1건", output)
        self.assertIn("만기 완료 0건", output)

    def test_mature_cohort_includes_both_hit_and_miss_and_splits_horizons(self):
        prices = pd.Series([100., 105., 111., 108.], index=pd.bdate_range("2026-09-25", periods=4))
        ledger, _ = ol.score(self.ledger(), {"level_reach": lambda r: ol.level_reach_outcome(prices, r)})
        output = ol.render(ledger, prices=prices)
        self.assertIn("Brier 0.290 · 예상 70% / 실제 50%", output)
        self.assertIn("가격 도달 확률 · 3거래일", output)
        self.assertIn("가격 도달 확률 · 5거래일", output)
        self.assertIn("만기 완료 2건", output)
        self.assertIn("조기 도달 1건", output)
        self.assertNotIn("실제 100%", output)

    def test_already_scored_hit_enters_performance_only_after_maturity(self):
        prices = pd.Series([100., 105., 111., 108.], index=pd.bdate_range("2026-09-25", periods=4))
        ledger, _ = ol.score(self.ledger(), {"level_reach": lambda r: ol.level_reach_outcome(prices.iloc[:3], r)})
        before = ledger.copy(deep=True)
        self.assertNotIn("Brier 0.090", ol.render(ledger, prices=prices.iloc[:3]))
        self.assertIn("Brier 0.090", ol.render(ledger, prices=prices))
        pd.testing.assert_frame_equal(ledger, before)

    def test_duplicate_and_missing_prices_do_not_count_as_extra_sessions(self):
        prices = pd.Series([100., 111., 111., np.nan],
                           index=pd.to_datetime(["2026-09-25", "2026-09-28", "2026-09-28", "2026-09-29"]))
        row = {"note": "issued=2026-09-25;days=3"}
        self.assertFalse(ol.level_reach_matured(prices, row))
        for note in ("", "issued=bad;days=3", "issued=2026-09-25;days=0"):
            self.assertFalse(ol.level_reach_matured(prices, {"note": note}))

    def test_no_prices_or_truncated_history_cannot_establish_maturity(self):
        prices = pd.Series([100., 111., 112., 113., 114., 115.],
                           index=pd.bdate_range("2026-09-25", periods=6))
        ledger, _ = ol.score(self.ledger(), {"level_reach": lambda r: ol.level_reach_outcome(prices, r)})
        for history in (None, prices.iloc[1:]):
            self.assertNotIn("Brier 0.090", ol.render(ledger, prices=history))


class WiringTests(unittest.TestCase):
    def test_tab_tool_records_scores_and_publishes_the_fragment(self):
        src = (ROOT / "tools" / "refresh_longterm_tab.py").read_text(encoding="utf-8")
        for needle in ("outlook_ledger.collect_forecasts(", "outlook_ledger.record(", "outlook_ledger.score(",
                       "expected_sha=ledger_sha", "docs/{args.target}/outlook.html", "summary_level_odds(prices, levels)"):
            self.assertIn(needle, src)

    def test_notebook_and_tabs_include_the_scorecard(self):
        import report_html
        self.assertIn("지난 전망은 맞았나", dict(report_html.TAB_GROUPS)["장기 전망"])
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        source = "".join("".join(c["source"]) for c in nb["cells"])
        self.assertIn('_outlook = _load_fragment("outlook.html")', source)
        self.assertIn("f'{outlook_html}'", source)
        gate = (ROOT / "tools" / "should_run_today.py").read_text(encoding="utf-8")
        self.assertIn('"outlook.html")', gate)

    def test_summary_odds_match_the_card(self):
        from forecast_utils import summary_level_odds, level_reach_odds, LEVEL_ODDS_HORIZON, LEVEL_ODDS_LOOKBACK
        rng = np.random.default_rng(1)
        close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, .02, 900))), index=pd.bdate_range("2023-01-02", periods=900))
        a = summary_level_odds(close, (150.,), n_paths=2000)
        b = level_reach_odds(close, [150.], horizon_days=LEVEL_ODDS_HORIZON, lookback_days=LEVEL_ODDS_LOOKBACK, n_paths=2000,
                             seed=20260913)
        self.assertTrue(np.allclose(a["levels"][0]["curve"], b["levels"][0]["curve"]))


class EarningsNextQuarterTests(unittest.TestCase):
    def test_next_quarter_estimate_is_logged_as_a_zero_month_row(self):
        import build_earnings_forecast as ef
        result = {"target": "samsung", "quarter_code": "2026Q3", "months_used": 3, "point": 120e12,
                  "next_quarter": {"quarter_code": "2026Q4", "point": 160e12, "raw_point": 160e12}}
        ledger, added = ef.append_estimate(pd.DataFrame(columns=ef.LEDGER_COLUMNS), result, "r1")
        self.assertTrue(added)
        self.assertEqual(sorted(ledger["record_id"]), ["samsung:2026Q3:k3", "samsung:2026Q4:k0"])
        ledger, again = ef.append_estimate(ledger, {**result, "next_quarter": {"quarter_code": "2026Q4", "point": 1.0}}, "r2")
        self.assertFalse(again, "다음 분기도 첫 추정만")
        ledger, scored = ef.score_ledger(ledger, {"2026Q4": 150e12})
        row = ledger.set_index("record_id").loc["samsung:2026Q4:k0"]
        self.assertEqual(row["status"], "scored")
        self.assertAlmostEqual(row["error"], 10e12)


if __name__ == "__main__":
    unittest.main()

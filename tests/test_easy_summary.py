"""Plain-language report summaries must respect the model's publication gates."""
import ast
import json
import unittest
from pathlib import Path

import pandas as pd

import forecast_utils


class EasySummaryTests(unittest.TestCase):
    def render(self, **changes):
        renderer = getattr(forecast_utils, "easy_summary_html", None)
        self.assertTrue(callable(renderer), "The plain-language summary renderer is missing")
        args = dict(
            name="삼성전자", prediction_date="2026-09-09", data_date="2026-09-08",
            summary={"live": {"prediction": "상승", "p_up": .6, "p_flat": .25, "p_down": .15},
                     "session_tradeable": False},
            open_forecast={"signal": "있음", "predicted_open": 70100, "target_date": "2026-09-09"},
            price_forecasts=[{"signal": "있음", "predicted_close": 71000,
                              "trading_days": 1, "target_date": "2026-09-09"}],
            review={}, longterm={}, earnings={},
        )
        args.update(changes)
        return renderer(**args)

    def test_open_and_close_have_distinct_prices_dates_and_meanings(self):
        html = self.render()
        self.assertIn("시초가예측", html)
        self.assertIn("장이 시작할 때", html)
        self.assertIn("70,100원", html)
        self.assertIn("종가예측", html)
        self.assertIn("장이 끝날 때", html)
        self.assertIn("71,000원", html)
        self.assertIn("2026-09-09", html)
        self.assertIn("60.0%", html)
        self.assertIn("실제 적중률이 아닙니다", html)
        self.assertIn("수익을 낼 만큼", html)

    def test_failed_price_gate_never_publishes_raw_or_center_prices(self):
        html = self.render(open_forecast={"signal": "없음", "predicted_open": 70100,
                                         "center_open": 69000},
                           price_forecasts=[{"signal": "없음", "predicted_close": 71000,
                                             "center_close": 68000, "trading_days": 1}])
        self.assertNotIn("70,100", html)
        self.assertNotIn("71,000", html)
        self.assertNotIn("69,000", html)
        self.assertNotIn("68,000", html)
        self.assertIn("예측하기 어렵습니다", html)
        self.assertIn("가격이 그대로라는 뜻은 아닙니다", html)

    def test_missing_nan_and_tied_direction_are_not_confident_forecasts(self):
        for live in ({}, {"p_up": float("nan")}, {"p_up": .5, "p_down": .5, "p_flat": 0}):
            with self.subTest(live=live):
                html = self.render(summary={"live": live}, open_forecast={}, price_forecasts=[])
                self.assertIn("방향을 판단하기 어렵습니다", html)
                self.assertNotIn("nan", html.lower())

    def test_longterm_and_earnings_gates_hide_unvalidated_estimates(self):
        html = self.render(
            longterm={"as_of": "2026-08-31", "forecast": {"3": {"point": .876}},
                      "evaluation": {"3": {"beats_zero": False}}},
            earnings={"quarter": "2026년 3분기", "point": 123.4e12,
                      "evaluation": {"beats_baselines": False}},
        )
        self.assertNotIn("87.6%", html)
        self.assertNotIn("123.4조", html)
        self.assertIn("2026-08-31", html)
        self.assertIn("3개월", html)

    def test_valid_earnings_use_trillion_won_and_disclose_partial_data(self):
        html = self.render(earnings={"quarter": "2026년 3분기", "point": 12.34e12,
                                    "months_used": 1, "months_included": "7월",
                                    "exports_last_month": "2026-07-01",
                                    "evaluation": {"beats_baselines": True},
                                    "next_quarter": {"quarter": "2026년 4분기", "point": None}})
        self.assertIn("12.3조 원", html)
        self.assertIn("3개월 중 1개월", html)
        self.assertIn("회사 발표나 증권사 전망 평균이 아닌", html)
        self.assertIn("2026년 4분기", html)

    def test_longterm_log_returns_are_shown_as_ordinary_price_changes(self):
        html = self.render(longterm={"as_of": "2026-08-31",
                                    "forecast": {"3": {"point": .4}, "6": {"point": -.4}},
                                    "evaluation": {"3": {"beats_zero": True},
                                                   "6": {"beats_zero": True}}})
        self.assertIn("+49.2%", html)
        self.assertIn("-33.0%", html)
        self.assertNotIn("+40.0%", html)

    def test_null_optional_sections_do_not_abort_the_report(self):
        for value in (None, [], "unavailable"):
            with self.subTest(value=value):
                html = self.render(longterm={"forecast": value, "evaluation": value},
                                   earnings={"evaluation": value, "next_quarter": value})
                self.assertIn("한눈에 보는 쉬운 요약", html)
                self.assertIn("판단 근거 부족", html)

    def test_track_record_is_actual_and_generation_time_snapshot(self):
        review = {"n_scored_days": 7, "latest_date": pd.Timestamp("2026-09-08"),
                  "rolling": pd.DataFrame([{"kind": "direction", "window": 20,
                                            "n": 5, "hit_rate": .4}])}
        html = self.render(review=review)
        self.assertIn("5일", html)
        self.assertIn("40.0%", html)
        self.assertIn("실제 사전 예측", html)
        self.assertIn("생성 시점", html)
        self.assertIn("자료가 적어", html)

    def test_escapes_text_and_warns_about_unrecorded_predictions(self):
        html = self.render(name="SK하이닉스<script>", record_forecast=False,
                           macro_active=False, nsi_active=False, target_mode="open_to_close")
        self.assertNotIn("<script>", html)
        self.assertIn("SK하이닉스&lt;script&gt;", html)
        self.assertIn("원장에 기록되지 않는 참고값", html)
        self.assertIn("당일 시초가 대비", html)
        self.assertIn("월별 경제지표", html)
        self.assertIn("뉴스 분위기 지표", html)

    def test_notebook_connects_computed_forecasts_and_handles_missing_fragments(self):
        # Catch wrong wiring (e.g. close prices used for opening prices), not just imports.
        notebook = json.loads((Path(__file__).resolve().parents[1]
                               / "samsung_direction_model_colab.ipynb").read_text())
        source = next("".join(c["source"]) for c in notebook["cells"]
                      if "def build_summary():" in "".join(c.get("source", [])))
        tree = ast.parse(source)
        nodes = [n for n in tree.body if
                 (isinstance(n, ast.FunctionDef) and n.name == "_load_summary_data") or
                 (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "easy_html"
                                                  for t in n.targets))]
        for name in ("삼성전자", "SK하이닉스"):
            for fragment in (None, "not json", "[]", '{"quarter":"2026년 3분기"}'):
                with self.subTest(name=name, fragment=fragment):
                    ns = dict(json=json, _load_fragment=lambda _: fragment,
                              easy_summary_html=forecast_utils.easy_summary_html,
                              TARGET_NAME=name, prediction_date=pd.Timestamp("2026-09-09"),
                              last_samsung_date=pd.Timestamp("2026-09-08"),
                              S={"live": {"p_up": .6, "p_flat": .25, "p_down": .15}},
                              open_forecast_row={"signal": "있음", "predicted_open": 70100},
                              price_forecast_rows=[{"signal": "있음", "predicted_close": 71000,
                                                    "trading_days": 1}],
                              ledger_review={}, TARGET_MODE="close_to_close", RECORD_FORECAST=True,
                              MACRO_ACTIVE=True, NSI_ACTIVE=True)
                    exec(compile(ast.Module(body=nodes, type_ignores=[]), "report-summary", "exec"), ns)
                    self.assertIn("easy_html", ns, "Report must generate a summary from its computed inputs")
                    self.assertIn(name, ns["easy_html"])
                    self.assertIn("70,100원", ns["easy_html"])
                    self.assertIn("71,000원", ns["easy_html"])


if __name__ == "__main__":
    unittest.main()

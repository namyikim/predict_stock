"""Plain-language report summaries must respect the model's publication gates."""
import ast
import json
import unittest
from pathlib import Path

import re

import numpy as np
import pandas as pd

import forecast_utils


def scored_review():
    """2026-09-11(금) 채점: 시초가 구간 안, 방향 틀림, 종가 구간 안. 채점일 25일(창 60일 이하)."""
    day = pd.Timestamp("2026-09-11")
    latest = pd.DataFrame([
        dict(target_date=day, kind="open", horizon_days=1, model="Ridge", predicted_open=70100.,
             low_open=69500., high_open=70800., actual_open=70500., interval_hit=1.),
        # 후보 모델 행은 맞았지만 판정은 대표 앙상블 행으로 한다.
        dict(target_date=day, kind="direction", horizon_days=1, model="Candidate X", prediction="하락",
             actual_class=0, direction_correct=1., actual_return=-.012),
        dict(target_date=day, kind="direction", horizon_days=1, model="Mean ensemble", prediction="상승",
             actual_class=0, direction_correct=0., actual_return=-.012),
        dict(target_date=day, kind="price", horizon_days=1, model="Ridge", predicted_close=np.nan,
             low_close=69000., high_close=72000., actual_close=70900., interval_hit=1.),
    ])
    rolling = pd.DataFrame([
        {"window": 20, "kind": "direction", "horizon_days": 1, "n": 20, "hit_rate": .45, "prior_hit_rate": .40},
        {"window": 60, "kind": "direction", "horizon_days": 1, "n": 25, "hit_rate": .52, "prior_hit_rate": .44},
        {"window": 60, "kind": "open", "horizon_days": 1, "n": 25, "interval_coverage": .84,
         "nominal_coverage": .8},
        {"window": 60, "kind": "price", "horizon_days": 1, "n": 25, "interval_coverage": .76,
         "nominal_coverage": .8},
        {"window": 60, "kind": "price", "horizon_days": 5, "n": 21, "interval_coverage": .10,
         "nominal_coverage": .8},
    ])
    return {"latest": latest, "rolling": rolling, "n_scored_days": 25, "latest_date": day}


class TopOfSummaryTests(unittest.TestCase):
    """2026-09-13 재구성: 요약 맨 위에 다음 거래일 시초가·방향·종가, 지난 예측 결과, 지금까지 성적."""

    def render(self, **changes):
        args = dict(
            name="삼성전자", prediction_date=pd.Timestamp("2026-09-14"), data_date=pd.Timestamp("2026-09-11"),
            summary={"live": {"prediction": "상승", "p_up": .6, "p_flat": .25, "p_down": .15},
                     "ensemble": "Mean ensemble"},
            open_forecast={"signal": "있음", "predicted_open": 70100, "predicted_return": .004,
                           "target_date": pd.Timestamp("2026-09-14")},
            price_forecasts=[{"signal": "있음", "predicted_close": 71000, "predicted_return": .012,
                              "trading_days": 1, "target_date": pd.Timestamp("2026-09-14")}],
            review=scored_review())
        args.update(changes)
        return forecast_utils.easy_summary_html(**args)

    def test_next_day_open_direction_and_close_come_before_everything_else(self):
        html = self.render()
        top = html.index("다음 거래일 2026-09-14 (월) 예측")
        last = html.index("지난 예측은 맞았나")
        self.assertLess(top, last)
        self.assertLess(last, html.index("지금까지 성적"))
        self.assertLess(html.index("지금까지 성적"), html.index("전체 결론"))
        cards = html[top:last]
        self.assertLess(cards.index("시초가 · 09:00"), cards.index("종가 방향"))
        self.assertLess(cards.index("종가 방향"), cards.index("종가 · 15:30"))
        for text in ("70,100원", "+0.40%", "▲ 오름", "71,000원", "+1.20%"):
            self.assertIn(text, cards)

    def test_the_rest_of_the_summary_stays_below(self):
        html = self.render()
        for label in ("전체 결론", "시초가예측 — 장이 시작할 때의 가격", "종가예측 — 장이 끝날 때의 가격",
                      "중장기 전망", "회사 실적 — 본업으로 번 이익", "얼마나 믿을 수 있나요?", "주의할 점"):
            self.assertGreater(html.index(label), html.index("지금까지 성적"), label)

    def test_last_result_says_right_or_wrong_per_item(self):
        card = forecast_utils.scorecard_html(scored_review(), "Mean ensemble")
        self.assertIn("2026-09-11 (금) 예측", card)
        marks = re.findall(r">(맞음|틀림|채점 전)</span><span[^>]*>(시초가|방향|종가)</span>", card)
        self.assertEqual(marks, [("맞음", "시초가"), ("틀림", "방향"), ("맞음", "종가")])
        self.assertIn("예측 오름 → 실제 내림 (-1.20%)", card)
        self.assertIn("예측 70,100원 → 실제 70,500원 · 구간 69,500원~70,800원", card)
        # 신호가 없던 종가는 숫자를 지어내지 않는다.
        self.assertIn("예측 숫자 없음(구간만) → 실제 70,900원", card)

    def test_track_record_uses_the_longest_window_and_its_baselines(self):
        card = forecast_utils.scorecard_html(scored_review(), "Mean ensemble")
        self.assertIn("채점한 25거래일 전체 · 미리 낸 예측만", card)       # 채점일 25일 ≤ 창 60일
        for text in (">52%<", "늘 같은 답이면 44%", ">84%<", ">76%<", "목표 80%", "25일 중"):
            self.assertIn(text, card)
        self.assertNotIn(">45%<", card)       # 짧은 창은 쓰지 않는다
        self.assertNotIn(">10%<", card)       # 5거래일 종가는 여기서 다루지 않는다
        self.assertNotIn("판단하기 이릅니다", card)

    def test_long_history_is_labelled_as_the_recent_window(self):
        review = dict(scored_review(), n_scored_days=140)
        self.assertIn("최근 60거래일", forecast_utils.scorecard_html(review, "Mean ensemble"))

    def test_small_samples_are_muted_and_flagged(self):
        review = scored_review()
        review["rolling"] = review["rolling"].assign(n=8)
        card = forecast_utils.scorecard_html(review, "Mean ensemble")
        self.assertIn("판단하기 이릅니다", card)
        self.assertIn('color:#8a9199">52%', card)

    def test_nothing_scored_yet(self):
        for review in (None, {}, {"n_scored_days": 0, "latest": pd.DataFrame(), "rolling": pd.DataFrame()}):
            with self.subTest(review=review):
                card = forecast_utils.scorecard_html(review)
                self.assertIn("아직 채점된 예측이 없습니다", card)
                self.assertIn("아직 성적을 낼 만큼", card)
                self.assertNotIn("nan", card.lower())

    def test_failed_gates_hide_next_day_prices_in_the_cards(self):
        html = self.render(open_forecast={"signal": "없음", "predicted_open": 70100},
                           price_forecasts=[{"signal": "없음", "predicted_close": 71000, "trading_days": 1}])
        top = html[:html.index("지난 예측은 맞았나")]
        self.assertNotIn("70,100", top)
        self.assertNotIn("71,000", top)
        self.assertEqual(top.count("예측 안 함"), 2)

    def test_scorecard_is_marked_for_the_afternoon_refresh(self):
        html = self.render()
        self.assertEqual(html.count(forecast_utils.SCORECARD_START), 1)
        self.assertEqual(html.count(forecast_utils.SCORECARD_END), 1)
        self.assertLess(html.index(forecast_utils.SCORECARD_START), html.index("지난 예측은 맞았나"))
        self.assertGreater(html.index(forecast_utils.SCORECARD_END), html.index("지금까지 성적"))

    def test_top_blocks_add_no_headings(self):
        """h3 가 늘면 탭 나누기가 요약을 쪼갠다. 맨 위 블록은 h3 를 쓰지 않는다."""
        self.assertEqual(self.render().count("<h3"), 1)


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
                               / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        source = next("".join(c["source"]) for c in notebook["cells"]
                      if "def build_summary():" in "".join(c.get("source", [])))
        tree = ast.parse(source)
        # _load_summary_data 는 report_html.load_summary_data 를 감싸는 어댑터라 여기서는 직접 준다.
        nodes = [n for n in tree.body if
                 (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "easy_html"
                                                  for t in n.targets))]

        def summary_loader(fragment):
            def load(_name):
                try:
                    payload = json.loads(fragment or "{}")
                    return payload if isinstance(payload, dict) else {}
                except (TypeError, ValueError):
                    return {}
            return load
        for name in ("삼성전자", "SK하이닉스"):
            for fragment in (None, "not json", "[]", '{"quarter":"2026년 3분기"}'):
                with self.subTest(name=name, fragment=fragment):
                    ns = dict(json=json, _load_fragment=lambda _: fragment,
                              _load_summary_data=summary_loader(fragment),
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

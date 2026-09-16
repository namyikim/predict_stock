# -*- coding: utf-8 -*-
"""종가 방향은 확률이 기준 이상인 날만 낸다(2026-09-16).

P08 진단: 최대 확률 0.5 이상인 날만 고르면 외부 구간 정확도가 0.46→0.67(삼성)·0.50→0.64(하이닉스).
대표 예측은 시초가(갭)로 두고, 종가 방향은 '판단 유보'를 허용한다. 원장 기록은 바뀌지 않는다 —
유보는 확률에서 다시 계산하는 표시·집계 정책이다.
"""
import json
import re
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forecast_utils as fu  # noqa: E402


def plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


class DirectionCallTests(unittest.TestCase):
    def test_threshold_is_the_one_p08_selected(self):
        self.assertEqual(fu.DIRECTION_ISSUE_MIN_PROB, 0.50)

    def test_issued_only_at_or_above_the_threshold(self):
        issued = fu.direction_call({"p_down": .2, "p_flat": .3, "p_up": .5})
        self.assertEqual((issued["issued"], issued["label"], issued["argmax"]), (True, "▲ 오름", 2))
        held = fu.direction_call({"p_down": .30, "p_flat": .36, "p_up": .34})
        self.assertEqual((held["valid"], held["issued"], held["label"]), (True, False, "판단 유보"))
        self.assertAlmostEqual(held["max_prob"], .36)
        self.assertIn("36%로 기준 50%에 못 미쳐", fu.direction_hold_note(held))
        self.assertIn("기울기: 큰 변화 없음", fu.direction_hold_note(held))

    def test_invalid_probabilities_are_not_a_call(self):
        for live in ({}, {"p_up": float("nan")}, {"p_up": .5, "p_down": .5, "p_flat": 0}, None):
            with self.subTest(live=live):
                call = fu.direction_call(live)
                self.assertFalse(call["valid"])
                self.assertEqual(call["label"], "판단 어려움")

    def test_pandas_row_works(self):
        row = pd.Series({"p_down": .1, "p_flat": .2, "p_up": .7, "prediction": "상승"})
        self.assertTrue(fu.direction_call(row)["issued"])


class SummaryTests(unittest.TestCase):
    def render(self, live, open_signal="있음"):
        return fu.easy_summary_html(
            name="삼성전자", prediction_date=pd.Timestamp("2026-09-17"), data_date=pd.Timestamp("2026-09-16"),
            summary={"live": live, "ensemble": "No macro ensemble"},
            open_forecast={"signal": open_signal, "predicted_open": 250100., "predicted_return": .004,
                           "target_date": pd.Timestamp("2026-09-17")},
            price_forecasts=[{"signal": "있음", "predicted_close": 251000., "predicted_return": .008,
                              "trading_days": 1}],
            review={"n_scored_days": 0})

    def test_open_forecast_is_the_headline_and_the_big_card(self):
        html = self.render({"p_down": .30, "p_flat": .36, "p_up": .34})
        text = plain(html)
        self.assertIn("삼성전자 · 2026-09-17: 시초가 약 250,100원(+0.40%) 예상. 종가 방향은 판단 유보 — "
                      "가장 높은 확률 36.0%가 기준 50%에 못 미쳐 방향을 내지 않습니다.", text)
        # 시초가는 밤사이 미국 시장을 보고 내는 값이라 '믿을 만하다'고 적지 않는다(2026-09-16 지적).
        self.assertIn("시초가 · 09:00 · 밤사이 미국 시장을 반영한 값 · 09:00 전에만 의미", text)
        self.assertNotIn("가장 믿을 만한", text)
        self.assertRegex(html, r'font-size:24px[^>]*>250,100원<')            # 시초가가 가장 큰 글자
        self.assertRegex(html, r'color:#8a9199">판단 유보<')                   # 방향은 흐리게

    def test_confident_direction_is_issued(self):
        text = plain(self.render({"p_down": .2, "p_flat": .3, "p_up": .5}))
        self.assertIn("종가 방향은 ‘오름’ 쪽의 계산상 가능성이 가장 높습니다 (50.0%)", text)
        self.assertIn("▲ 오름", text)
        self.assertIn("계산상 가능성 50% (기준 50% 이상)", text)

    def test_no_open_signal_says_so_first(self):
        text = plain(self.render({"p_down": .2, "p_flat": .3, "p_up": .5}, open_signal="없음"))
        self.assertIn("시초가는 예측하지 않습니다(검증 근거 부족). 전일 종가 대비 종가 방향은", text)
        self.assertNotIn("250,100", text)


def scored(probs, correct):
    """direction 행 하나로 채점 결과를 만든다. probs 는 (down, flat, up), 원장의 라벨은 argmax 다."""
    day = pd.Timestamp("2026-09-16")
    label = ("하락", "보합", "상승")[int(np.argmax(probs))]
    latest = pd.DataFrame([dict(target_date=day, kind="direction", horizon_days=1, model="No macro ensemble",
                                prediction=label, p_down=probs[0], p_flat=probs[1], p_up=probs[2],
                                actual_class=2, direction_correct=float(correct), actual_return=.02)])
    return {"latest": latest, "rolling": pd.DataFrame(), "n_scored_days": 1, "latest_date": day}


class ScorecardTests(unittest.TestCase):
    def test_held_day_is_marked_held_not_right_or_wrong(self):
        card = fu.scorecard_html(scored((.30, .36, .34), correct=0), "No macro ensemble")
        self.assertRegex(card, r">유보</span><span[^>]*>방향</span>")
        text = plain(card)
        self.assertIn("판단 유보(가장 높은 확률 36%, 기준 50% 미만) → 실제 오름 (+2.00%) · 참고: 계산상 기울기 큰 변화 없음, 틀림", text)

    def test_issued_day_is_scored(self):
        card = fu.scorecard_html(scored((.2, .3, .5), correct=1), "No macro ensemble")
        self.assertRegex(card, r">맞음</span><span[^>]*>방향</span>")

    def test_track_record_counts_only_issued_days(self):
        rolling = pd.DataFrame([
            {"window": 60, "kind": "direction", "n": 9, "hit_rate": .44, "prior_hit_rate": .44},
            {"window": 60, "kind": "direction_issued", "n": 2, "held": 7, "hit_rate": 1.0, "prior_hit_rate": .44},
        ])
        review = {"latest": pd.DataFrame(), "rolling": rolling, "n_scored_days": 9,
                  "latest_date": pd.Timestamp("2026-09-16")}
        text = plain(fu.scorecard_html(review, "No macro ensemble"))
        self.assertIn("종가 방향 적중률 100% 2일 중 · 방향을 낸 날만 · 유보 7일 · 늘 같은 답이면 44%", text)
        self.assertNotIn("44% 9일 중", text)

    def test_all_days_held_is_stated(self):
        rolling = pd.DataFrame([
            {"window": 60, "kind": "direction", "n": 9, "hit_rate": .44, "prior_hit_rate": .44},
            {"window": 60, "kind": "direction_issued", "n": 0, "held": 9, "hit_rate": np.nan, "prior_hit_rate": .44},
        ])
        review = {"latest": pd.DataFrame(), "rolling": rolling, "n_scored_days": 9,
                  "latest_date": pd.Timestamp("2026-09-16")}
        text = plain(fu.scorecard_html(review, "No macro ensemble"))
        self.assertIn("종가 방향은 최근 9일 모두 판단 유보였습니다", text)
        self.assertNotIn("nan", text.lower())


class ReviewLedgerTests(unittest.TestCase):
    def review(self):
        bars = pd.DataFrame({"open": [100., 101., 102., 103.], "close": [100., 102., 101., 105.],
                             "adj_close": [100., 102., 101., 105.]},
                            index=pd.to_datetime(["2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16"]))
        rows = []
        for day, probs, actual, correct in (("2026-09-14", (.2, .3, .5), 2, 1.), ("2026-09-15", (.3, .36, .34), 0, 0.),
                                            ("2026-09-16", (.6, .2, .2), 2, 0.)):
            rows.append(dict(record_id=day, run_id="r", target_date=day, kind="direction", horizon_days=1,
                             model="No macro ensemble", status="scored", is_prospective=True,
                             prediction="x", p_down=probs[0], p_flat=probs[1], p_up=probs[2], band=.01,
                             actual_class=actual, direction_correct=correct, log_loss=1.0,
                             actual_return=.01, current_close=100.))
        return fu.review_ledger(pd.DataFrame(rows), bars, ensemble_model="No macro ensemble")

    def test_rolling_has_an_issued_only_row(self):
        roll = self.review()["rolling"].set_index(["window", "kind"])
        issued = roll.loc[(60, "direction_issued")]
        self.assertEqual((int(issued["n"]), int(issued["held"])), (2, 1))
        self.assertAlmostEqual(float(issued["hit_rate"]), .5)
        self.assertEqual(int(roll.loc[(60, "direction"), "n"]), 3)

    def test_ledger_section_renders_the_issued_row(self):
        html = fu.ledger_section_html(self.review(), "No macro ensemble")
        text = plain(html)
        self.assertIn("종가 방향(발행일만)", text)
        self.assertIn("적중률 50% · 발행 2일 · 유보 1일", text)
        # 2026-09-16(최대 확률 60%)은 발행됐고 틀렸다.
        self.assertIn("미적중", text)


class NotebookWiringTests(unittest.TestCase):
    def test_section_one_uses_the_gate(self):
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        report = next("".join(c["source"]) for c in nb["cells"] if "def build_summary():" in "".join(c["source"]))
        self.assertIn('_direction = direction_call(S["live"])', report)
        self.assertIn("{_direction_big}", report)
        self.assertNotIn('margin-bottom:9px">{S["live"]["prediction"]}</div>', report)
        self.assertIn("direction_hold_note(_direction)", report)


if __name__ == "__main__":
    unittest.main()

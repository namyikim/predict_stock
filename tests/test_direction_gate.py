# -*- coding: utf-8 -*-
"""종가 방향 발행 기준(direction_call).

2026-09-16 오전에 '최대 확률 0.5 이상인 날만 방향을 내고 나머지는 판단 유보'로 켰다가(P08 진단), 같은 날
사용자 결정으로 껐다 — 판단을 안 하는 것은 비겁하고, 정확도를 높이는 노력을 해야 한다는 것. 그래서 기준은 0
(세 확률 중 가장 높은 방향을 매일 낸다). 장치는 남겨 두므로 기준을 넘긴 경우의 동작도 함께 지킨다.
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


class PolicyTests(unittest.TestCase):
    def test_gate_is_off_so_the_top_class_is_always_issued(self):
        self.assertEqual(fu.DIRECTION_ISSUE_MIN_PROB, 0.0)
        call = fu.direction_call({"p_down": .35, "p_flat": .33, "p_up": .32})
        self.assertEqual((call["valid"], call["issued"], call["label"], call["argmax"]), (True, True, "▼ 내림", 0))
        self.assertEqual(fu.direction_hold_note(call), "계산상 가능성 35%")

    def test_the_machinery_still_holds_when_a_threshold_is_given(self):
        held = fu.direction_call({"p_down": .30, "p_flat": .36, "p_up": .34}, min_prob=.5)
        self.assertEqual((held["issued"], held["label"]), (False, "판단 유보"))
        self.assertIn("36%로 기준 50%에 못 미쳐", fu.direction_hold_note(held, min_prob=.5))
        issued = fu.direction_call({"p_down": .2, "p_flat": .3, "p_up": .5}, min_prob=.5)
        self.assertEqual(issued["label"], "▲ 오름")
        self.assertIn("(기준 50% 이상)", fu.direction_hold_note(issued, min_prob=.5))

    def test_invalid_probabilities_are_not_a_call(self):
        for live in ({}, {"p_up": float("nan")}, {"p_up": .5, "p_down": .5, "p_flat": 0}, None):
            with self.subTest(live=live):
                call = fu.direction_call(live)
                self.assertFalse(call["valid"])
                self.assertEqual(call["label"], "판단 어려움")


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

    def test_low_confidence_direction_is_still_shown(self):
        html = self.render({"p_down": .35, "p_flat": .33, "p_up": .32})
        text = plain(html)
        self.assertIn("전일 종가 대비 종가 방향은 ‘내림’ 쪽의 계산상 가능성이 가장 높습니다 (35.0%)", text)
        self.assertIn("▼ 내림", text)
        self.assertIn("전일 종가 대비 · 계산상 가능성 35%", text)
        self.assertNotIn("판단 유보", text)

    def test_open_forecast_leads_but_is_labelled_honestly(self):
        text = plain(self.render({"p_down": .2, "p_flat": .3, "p_up": .5}))
        self.assertIn("삼성전자 · 2026-09-17: 시초가 약 250,100원(+0.40%) 예상. 전일 종가 대비 종가 방향은 ‘오름’", text)
        self.assertIn("시초가 · 09:00 · 밤사이 미국 시장을 반영한 값 · 09:00 전에만 의미", text)
        self.assertNotIn("가장 믿을 만한", text)

    def test_no_open_signal_says_so_first(self):
        text = plain(self.render({"p_down": .2, "p_flat": .3, "p_up": .5}, open_signal="없음"))
        self.assertIn("시초가는 예측하지 않습니다(검증 근거 부족). 전일 종가 대비 종가 방향은", text)
        self.assertNotIn("250,100", text)


def scored(probs, correct):
    day = pd.Timestamp("2026-09-16")
    label = ("하락", "보합", "상승")[int(np.argmax(probs))]
    latest = pd.DataFrame([dict(target_date=day, kind="direction", horizon_days=1, model="No macro ensemble",
                                prediction=label, p_down=probs[0], p_flat=probs[1], p_up=probs[2],
                                actual_class=2, direction_correct=float(correct), actual_return=.02)])
    return {"latest": latest, "rolling": pd.DataFrame(), "n_scored_days": 1, "latest_date": day}


class ScorecardTests(unittest.TestCase):
    def test_low_confidence_day_is_scored_right_or_wrong(self):
        card = fu.scorecard_html(scored((.30, .36, .34), correct=0), "No macro ensemble")
        self.assertRegex(card, r">틀림</span><span[^>]*>방향</span>")
        self.assertIn("예측 큰 변화 없음 → 실제 오름 (+2.00%)", plain(card))
        self.assertNotIn("유보", plain(card))

    def test_track_record_uses_every_day(self):
        rolling = pd.DataFrame([{"window": 60, "kind": "direction", "n": 9, "hit_rate": .44, "prior_hit_rate": .44}])
        review = {"latest": pd.DataFrame(), "rolling": rolling, "n_scored_days": 9,
                  "latest_date": pd.Timestamp("2026-09-16")}
        text = plain(fu.scorecard_html(review, "No macro ensemble"))
        self.assertIn("종가 방향 적중률 44% 9일 중 · 늘 같은 답이면 44%", text)
        self.assertNotIn("유보", text)


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

    def test_no_issued_only_rows_while_the_gate_is_off(self):
        roll = self.review()["rolling"]
        self.assertNotIn("direction_issued", set(roll["kind"]))
        self.assertEqual(int(roll[(roll["window"] == 60) & (roll["kind"] == "direction")]["n"].iloc[0]), 3)

    def test_ledger_section_has_no_hold_wording(self):
        text = plain(fu.ledger_section_html(self.review(), "No macro ensemble"))
        self.assertNotIn("유보", text)
        self.assertNotIn("발행일만", text)
        self.assertIn("미적중", text)


class NotebookWiringTests(unittest.TestCase):
    def test_section_one_shows_the_top_class_with_its_probability(self):
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        report = next("".join(c["source"]) for c in nb["cells"] if "def build_summary():" in "".join(c["source"]))
        self.assertIn('_direction = direction_call(S["live"])', report)
        self.assertIn("{_direction_big}", report)
        self.assertIn("direction_hold_note(_direction)", report)
        self.assertIn("세 확률 중 최댓값이 예측 클래스입니다", report)
        self.assertNotIn("이상인 날만 방향을 냅니다", report)


if __name__ == "__main__":
    unittest.main()

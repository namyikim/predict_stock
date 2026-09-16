# -*- coding: utf-8 -*-
"""기록하지 않는 재실행은 원장의 공식 사전 예측을 보여 준다.

2026-09-16 SK하이닉스: 15:25 KST 재실행이 새로 학습한 '하락'을 맨 위에 띄웠는데, 원장에 기록되고
채점된 그날 아침 예측은 '보합'이었다. 화면과 채점이 다른 예측을 보면 안 된다.
"""
import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forecast_utils as fu  # noqa: E402

MORNING = "2026-09-15T21:35:17+00:00"


def ledger():
    base = dict(prediction_date="2026-09-16", target_date="2026-09-16", as_of_date="2026-09-15",
                is_prospective=True, band=.0148, horizon_days=1)
    rows = [
        # 전날 저녁 후보 — 먼저 기록됐어도 공식 예측이 아니다.
        dict(base, run_id="evening", created_at_utc="2026-09-15T11:57:43+00:00", kind="direction",
             model="Candidate evening forecast", prediction="하락", p_down=.58, p_flat=.27, p_up=.15),
        # 장 마감 뒤 기록하지 않은(사전 예측이 아닌) 행 — 무시한다.
        dict(base, run_id="afterclose", is_prospective=False, created_at_utc="2026-09-15T08:00:00+00:00",
             kind="direction", model="No macro ensemble", prediction="상승", p_down=.2, p_flat=.3, p_up=.5),
        dict(base, run_id="morning", created_at_utc=MORNING, kind="direction", model="No macro ensemble",
             prediction="보합", p_down=.30, p_flat=.36, p_up=.34),
        dict(base, run_id="morning", created_at_utc=MORNING, kind="direction", model="Mean ensemble",
             prediction="상승", p_down=.33, p_flat=.33, p_up=.34),
        dict(base, run_id="morning", created_at_utc=MORNING, kind="open", model="Ridge", signal="있음",
             predicted_open=1691186., center_open=1691186., low_open=1622022., high_open=1760350.,
             predicted_return=.0007, band_coverage=np.nan, gap_sign_auc=.81),
        dict(base, run_id="morning", created_at_utc=MORNING, kind="price", model="Ridge", trading_days=1,
             signal="없음", predicted_close=np.nan, predicted_return=np.nan, center_close=1690000.,
             low_close=1599526., high_close=1789177.),
        dict(base, run_id="morning", created_at_utc=MORNING, kind="price", model="Candidate strict gate",
             trading_days=1, signal="있음", predicted_close=1.0),
        # 같은 날 뒤늦게 기록된 사전 예측 — 공식 예측은 먼저 것이다.
        dict(base, run_id="later", created_at_utc="2026-09-15T22:30:00+00:00", kind="direction",
             model="No macro ensemble", prediction="하락", p_down=.4, p_flat=.3, p_up=.3),
        dict(base, run_id="yesterday", prediction_date="2026-09-15", created_at_utc="2026-09-14T23:59:00+00:00",
             kind="direction", model="No macro ensemble", prediction="하락", p_down=.5, p_flat=.3, p_up=.2),
    ]
    return pd.DataFrame(rows)


def fresh():
    table = pd.DataFrame([
        {"model": "No macro ensemble", "prediction": "하락", "p_down": .34, "p_flat": .34, "p_up": .32},
        {"model": "Mean ensemble", "prediction": "하락", "p_down": .36, "p_flat": .33, "p_up": .31},
        {"model": "Logistic", "prediction": "하락", "p_down": .40, "p_flat": .30, "p_up": .30},
    ]).set_index("model")
    open_row = {"horizon": "시초가예측", "trading_days": 1, "signal": "있음", "predicted_open": 1688892.,
                "center_open": 1688892., "low_open": 1620000., "high_open": 1760000., "band_coverage": .8,
                "gap_sign_auc": .79}
    price_rows = [{"horizon": "1거래일", "trading_days": 1, "signal": "있음", "predicted_close": 1691524.,
                   "predicted_return": .0009, "center_close": 1691524., "low_close": 1.6e6, "high_close": 1.78e6,
                   "band_coverage": .8},
                  {"horizon": "1주", "trading_days": 5, "signal": "없음", "predicted_close": np.nan,
                   "center_close": 1690000., "low_close": 1.5e6, "high_close": 1.9e6, "band_coverage": .8}]
    return table, open_row, price_rows, .02


class OfficialForecastTests(unittest.TestCase):
    def test_picks_the_first_prospective_run_of_the_day(self):
        official = fu.official_forecast(ledger(), pd.Timestamp("2026-09-16"))
        self.assertEqual(official["run_id"], "morning")
        self.assertEqual(set(official["direction"]), {"No macro ensemble", "Mean ensemble"})
        self.assertEqual(official["open"]["predicted_open"], 1691186.)
        self.assertEqual(list(official["price"]), [1])
        self.assertEqual(official["price"][1]["model"], "Ridge")      # 관찰 후보가 아니라 대표 행

    def test_display_follows_the_official_forecast(self):
        table, open_row, price_rows, band = fresh()
        official = fu.official_forecast(ledger(), "2026-09-16")
        new_table, new_open, new_prices, new_band = fu.apply_official_forecast(
            official, table, open_row, price_rows, band)
        self.assertEqual(new_table.loc["No macro ensemble", "prediction"], "보합")
        self.assertAlmostEqual(new_table.loc["No macro ensemble", "p_flat"], .36)
        self.assertEqual(new_table.loc["Mean ensemble", "prediction"], "상승")
        self.assertEqual(new_table.loc["Logistic", "prediction"], "하락")     # 원장에 없는 모델은 그대로
        self.assertEqual(new_open["predicted_open"], 1691186.)
        self.assertAlmostEqual(new_open["gap_sign_auc"], .81)
        self.assertEqual(new_open["band_coverage"], .8)                        # 원장이 비었으면 새 값 유지
        self.assertEqual(new_prices[0]["signal"], "없음")
        self.assertTrue(np.isnan(new_prices[0]["predicted_close"]))            # 비어 있는 것 자체가 '예측 안 함'
        self.assertEqual(new_prices[0]["center_close"], 1690000.)
        self.assertEqual(new_prices[1], price_rows[1])                         # 원장에 없는 지평은 그대로
        self.assertAlmostEqual(new_band, .0148)
        # 받은 객체는 바꾸지 않는다.
        self.assertEqual(table.loc["No macro ensemble", "prediction"], "하락")
        self.assertEqual(open_row["predicted_open"], 1688892.)
        self.assertEqual(price_rows[0]["signal"], "있음")

    def test_nothing_recorded_means_no_official_forecast(self):
        self.assertIsNone(fu.official_forecast(ledger(), "2026-09-17"))       # 장 마감 뒤 다음 거래일 대상
        self.assertIsNone(fu.official_forecast(pd.DataFrame(), "2026-09-16"))
        self.assertIsNone(fu.official_forecast(None, "2026-09-16"))
        self.assertIsNone(fu.official_forecast(pd.DataFrame({"run_id": ["x"]}), "2026-09-16"))

    def test_only_the_evening_candidate_is_not_enough(self):
        evening_only = ledger().query("run_id == 'evening'")
        self.assertIsNone(fu.official_forecast(evening_only, "2026-09-16"))

    def test_note_names_the_run_and_its_time(self):
        note = fu.official_forecast_note(fu.official_forecast(ledger(), "2026-09-16"))
        self.assertIn("09-16 06:35 KST", note)
        self.assertIn("morning", note)
        self.assertIn("채점도 이 예측으로", note)


class NotebookWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        cls.cells = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]

    def cell(self, token):
        return next(source for source in self.cells if token in source)

    def test_unrecorded_runs_swap_in_the_official_forecast_after_the_ledger_is_merged(self):
        ledger_cell = self.cell('"kind": "open", "horizon_days": 1')
        call = ledger_cell.index("official_forecast(daily, prediction_date")
        self.assertLess(ledger_cell.index("if SYNC_LEDGER_TO_GITHUB:"), call)     # 원격 원장과 합친 뒤
        self.assertLess(ledger_cell.rindex("if not RECORD_FORECAST:"), call)
        self.assertIn("apply_official_forecast(", ledger_cell)

    def test_report_says_which_forecast_it_shows(self):
        report = self.cell("def build_summary():")
        self.assertIn("official_note=", report)
        self.assertIn("다시 만든 보고서", report)
        # 요약·표는 build_summary 가 live_table 에서 읽으므로 바꿔 끼운 값이 그대로 쓰인다.
        self.assertIn('"live": live_table.loc[ens]', report)


if __name__ == "__main__":
    unittest.main()

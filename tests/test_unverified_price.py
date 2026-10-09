"""검증을 통과하지 못한 종가예측도 '미확인'으로 발행한다(2026-10-09 요청).

1주일·1개월 종가가 매일 '예측하지 않음'이었다. 판단을 접지 않고 보정 기울기로 축소한 값을 내되,
검증 우위가 확인되지 않았다는 사실은 숫자 옆에 남기고 원장에서 실제 결과로 채점한다.
'없음'(발행하지 않음)의 뜻은 그대로다 — 옛 원장과 금·은 보고서가 쓴다.
"""
import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import forecast_utils as fu

ROOT = Path(__file__).resolve().parents[1]


def unverified_row(days=5):
    return {"horizon": "1주일", "trading_days": days, "target_date": pd.Timestamp("2026-10-16"),
            "signal": fu.PRICE_SIGNAL_UNVERIFIED, "predicted_close": 71234., "predicted_return": .0123,
            "center_close": 70370., "low_close": 66000., "high_close": 74000., "current_close": 70370.}


class UnverifiedSignalTests(unittest.TestCase):
    def test_published_signals(self):
        self.assertTrue(fu.price_is_published({"signal": "있음"}))
        self.assertTrue(fu.price_is_published({"signal": "미확인"}))
        self.assertFalse(fu.price_is_published({"signal": "없음"}))
        self.assertFalse(fu.price_is_published(None))

    def test_range_chart_shows_the_point_with_a_note(self):
        html = fu.price_range_html({}, [unverified_row()])
        self.assertIn("71,234", html)
        self.assertIn("검증 우위 미확인", html)
        self.assertIn("66,000~74,000원", html)

    def test_metal_rows_with_no_signal_still_hide_the_point(self):
        row = dict(unverified_row(), signal="없음")
        html = fu.price_rows_range_html([row], "t")
        self.assertNotIn("71,234", html)

    def test_next_day_card_marks_unverified_close(self):
        html = fu.next_day_forecast_html(
            prediction_date=pd.Timestamp("2026-10-12"), summary={"live": {"p_up": .5, "p_flat": .3, "p_down": .2}},
            open_forecast={"signal": "있음", "predicted_open": 70100, "predicted_return": .004},
            price_forecasts=[unverified_row(days=1)])
        self.assertIn("71,234원", html)
        self.assertIn("검증 우위 미확인", html)


class IssuanceTests(unittest.TestCase):
    def test_unverified_counts_as_issued_not_verified(self):
        days = pd.bdate_range("2026-09-01", periods=6).strftime("%Y-%m-%d")
        signals = ["없음", "없음", "있음", "미확인", "미확인", "있음"]
        log = pd.DataFrame({"kind": "price", "horizon_days": 5, "prediction_date": days, "signal": signals,
                            "created_at_utc": days})
        s = fu.price_issuance_summary(log, 5)
        self.assertEqual((s["n"], s["issued"], s["verified"]), (6, 4, 2))


class CalibrationTests(unittest.TestCase):
    def test_failed_gate_keeps_the_calibration_slope(self):
        rng = np.random.default_rng(0)
        n = 400
        y = rng.normal(0, .02, n)
        pred = rng.normal(0, .02, n) + .2 * y          # 약한 신호: 보정 기울기는 양수지만 검증은 통과 못 할 정도
        ci = lambda dates, f: (-1.0, 1.0)              # noqa: E731 — 신뢰구간이 0을 포함 → 검증 미통과
        st = fu.calibrate_price_forecast(y, pred, np.full(n, .02), pd.bdate_range("2020-01-01", periods=n), 5, ci)
        self.assertFalse(st["beats_baseline"])
        self.assertEqual(st["oof_slope"], 0.)
        self.assertGreater(st["calibration_slope"], 0.)
        self.assertTrue(np.isfinite(st["calibrated_model_mae"]))


class NotebookWiringTests(unittest.TestCase):
    def test_notebook_publishes_unverified_close(self):
        cells = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))["cells"]
        code = "\n".join("".join(c["source"]) for c in cells if c["cell_type"] == "code")
        self.assertIn('"signal": "있음" if stats["beats_baseline"] else PRICE_SIGNAL_UNVERIFIED', code)
        self.assertIn('"predicted_close": current_close * (1 + published)', code)
        self.assertNotIn('"predicted_close": center if stats["beats_baseline"] else np.nan', code)


if __name__ == "__main__":
    unittest.main()

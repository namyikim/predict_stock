"""금·은 보고서의 데이터 위생과 표시 규칙."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_metals_report as mr  # noqa: E402
import forecast_utils as fu  # noqa: E402


class UnclosedBarTests(unittest.TestCase):
    def frame(self, dates):
        idx = pd.to_datetime(dates)
        return pd.DataFrame({"open": 1., "high": 1., "low": 1., "close": 1., "volume": 1}, index=idx)

    def test_weekend_dated_bar_is_dropped(self):
        # Yahoo가 일요일 18:00 ET Globex 개장 직후 몇 시간을 '일요일 봉'으로 내보낸다.
        f = self.frame(["2026-09-03", "2026-09-04", "2026-09-06"])
        out = mr.drop_unclosed(f, now=pd.Timestamp("2026-09-06 19:52", tz="America/New_York"))
        self.assertEqual(out.index[-1].date().isoformat(), "2026-09-04")

    def test_friday_bar_survives_a_sunday_run(self):
        f = self.frame(["2026-09-03", "2026-09-04"])
        out = mr.drop_unclosed(f, now=pd.Timestamp("2026-09-06 17:30", tz="America/New_York"))
        self.assertEqual(len(out), 2)

    def test_todays_bar_is_dropped_before_settlement(self):
        f = self.frame(["2026-09-07", "2026-09-08"])
        early = mr.drop_unclosed(f, now=pd.Timestamp("2026-09-08 12:00", tz="America/New_York"))
        late = mr.drop_unclosed(f, now=pd.Timestamp("2026-09-08 17:30", tz="America/New_York"))
        self.assertEqual(len(early), 1)
        self.assertEqual(len(late), 2)


class RenderTests(unittest.TestCase):
    def make_res(self, skill, review=None):
        wf = dict(n=3689, folds=30, first="2012-01-03", last="2026-09-04", balanced_accuracy=.357,
                  prior_balanced_accuracy=.337, log_loss=1.0956, prior_log_loss=1.0930, auc_up=.527,
                  auc_up_vs_down=.534, class_share=np.array([.33, .29, .38]),
                  log_loss_diff_lo=-.003, log_loss_diff_hi=(-.001 if skill else .008))
        live = dict(p_down=.296, p_flat=.322, p_up=.382, band=.0045, as_of=pd.Timestamp("2026-09-04"))
        row = dict(horizon="1주일", trading_days=5, as_of_date="2026-09-04", target_date="2026-09-11",
                   current_close=4476.6, signal="없음", predicted_return=np.nan, predicted_close=np.nan,
                   center_close=4476.6, low_close=4278., high_close=4675., band_coverage=.79, vol_model="simple",
                   model_mae=.02, zero_baseline_mae=.02, mae_diff_lo=0., mae_diff_hi=0., oof_slope=0.)
        return dict(live=live, wf=wf, price_rows=[row], price_stats={"1주일": dict(selection_mae_diff_lo=0., selection_mae_diff_hi=0.)},
                    prediction_date=pd.Timestamp("2026-09-07"), scored=pd.DataFrame(),
                    review=review or fu.review_ledger(pd.DataFrame(), pd.DataFrame()))

    def test_no_verdict_without_skill(self):
        html = mr.render_asset("gold", self.make_res(skill=False), 1355.)
        self.assertIn("방향 판정: 보류", html)
        self.assertNotIn("최빈 판정은", html)

    def test_verdict_with_skill(self):
        html = mr.render_asset("gold", self.make_res(skill=True), 1355.)
        self.assertIn("최빈 판정은 <b>상승</b>", html)

    def test_ledger_review_is_rendered_when_scored(self):
        bars = pd.DataFrame({"open": [100., 101.], "close": [100., 99.], "adj_close": [100., 99.]},
                            index=pd.to_datetime(["2026-09-04", "2026-09-07"]))
        daily = pd.DataFrame([dict(record_id="a", run_id="r", target_date="2026-09-07", kind="direction", horizon_days=1,
                                   model="Logistic", status="scored", is_prospective=True, prediction="상승",
                                   p_down=.3, p_flat=.32, p_up=.38, band=.0045, actual_class=0, direction_correct=0.,
                                   log_loss=-np.log(.3), actual_return=-.01, current_close=100.),
                              dict(record_id="b", run_id="r", target_date="2026-09-07", kind="price", horizon_days=1,
                                   model="Ridge", status="scored", is_prospective=True, predicted_close=100.03,
                                   predicted_return=.0003, actual_close=99., actual_return=-.01, interval_hit=1.,
                                   current_close=100., oof_slope=.3)])
        review = fu.review_ledger(daily, bars, ensemble_model="Logistic")
        html = mr.render_asset("gold", self.make_res(skill=False, review=review), 1355.)
        self.assertIn("어제 예측 vs 실제", html)
        self.assertIn("미적중", html)
        self.assertIn("$99.00", html)


if __name__ == "__main__":
    unittest.main()

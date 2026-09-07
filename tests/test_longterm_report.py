"""장기 전망 모듈: 겹치는 타깃의 purge, 국면 분류, 유사 시기, 잡음에서의 판정."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_longterm_report as lt  # noqa: E402


def synthetic(seed=0, with_cycle=True, n_years=26):
    rng = np.random.default_rng(seed)
    months = pd.date_range("2000-01-31", periods=n_years * 12, freq="ME")
    n = len(months)
    cycle = np.sin(np.arange(n) / 9) if with_cycle else np.zeros(n)
    exports = pd.DataFrame({"month": months.to_period("M").to_timestamp(),
                            "value": (5e9 * np.exp(0.006 * np.arange(n) + 0.4 * cycle + rng.normal(0, .05, n))).round()})
    leading = pd.DataFrame({"month": months.to_period("M").to_timestamp(),
                            "value": 100 + 3 * np.roll(cycle, -2) + rng.normal(0, .3, n)})
    drift = 0.03 * np.roll(cycle, -4) if with_cycle else 0.
    price = pd.Series(1000 * np.exp(np.cumsum(0.005 + drift + rng.normal(0, .07, n))), index=months)
    return price, {"semiconductor_exports": exports, "leading_cycle": leading}


class LongTermTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lt.BOOTSTRAP_B = 100
        price, macro = synthetic()
        cls.frame = lt.build_frame(price, macro)
        cls.cols = [c for c, _ in lt.FEATURES if c in cls.frame.columns]

    def test_features_do_not_use_the_future(self):
        f = self.frame
        # 월+2 지연: t월 말에 보이는 수출 YoY는 늦어도 (t-2)월 값이다 → 마지막 두 달의 지표를 바꿔도 앞선 행은 그대로.
        price, macro = synthetic()
        macro["semiconductor_exports"].loc[macro["semiconductor_exports"].index[-2:], "value"] *= 3
        g = lt.build_frame(price, macro)
        cut = f.index[-3]
        pd.testing.assert_series_equal(f.loc[:cut, "macro_semiconductor_yoy"], g.loc[:cut, "macro_semiconductor_yoy"])

    def test_walk_forward_purges_overlapping_targets(self):
        # 시험 시점 t의 학습 행은 타깃이 t 이전에 실현된 행(t-h 이하)뿐이어야 한다.
        seen = {}
        original = lt.walk_forward

        from sklearn.linear_model import Ridge
        captured = []
        real_fit = Ridge.fit

        def spy(self_, X, y, *a, **k):
            captured.append(len(y))
            return real_fit(self_, X, y, *a, **k)
        Ridge.fit = spy
        try:
            oof = lt.walk_forward(self.frame, self.cols, 12)
        finally:
            Ridge.fit = real_fit
        first_t = oof.dropna().index[0]
        f = self.frame
        allowed = f.loc[f.index <= first_t - pd.DateOffset(months=12)]
        allowed = allowed[allowed[self.cols].notna().all(axis=1) & allowed["fwd_12m"].notna()]
        self.assertEqual(captured[0], len(allowed))

    def test_phase_classification(self):
        self.assertEqual(lt.phase_of(-.1, .02), lt.PHASES[0])
        self.assertEqual(lt.phase_of(.1, .02), lt.PHASES[1])
        self.assertEqual(lt.phase_of(.1, -.02), lt.PHASES[2])
        self.assertEqual(lt.phase_of(-.1, -.02), lt.PHASES[3])
        self.assertIsNone(lt.phase_of(np.nan, .1))

    def test_similar_episodes_are_realized_and_spaced(self):
        sims = lt.similar_episodes(self.frame, self.cols, 12, k=3)
        self.assertEqual(len(sims), 3)
        last = self.frame.index[-1]
        for s in sims:
            self.assertLessEqual(pd.Timestamp(s["date"]), last - pd.DateOffset(months=12))
            self.assertTrue(np.isfinite(s["fwd"]))
        dates = [pd.Timestamp(s["date"]) for s in sims]
        for a in dates:
            for b in dates:
                if a != b:
                    self.assertGreaterEqual(abs((a - b).days), 360)

    def test_no_signal_on_pure_noise(self):
        price, macro = synthetic(seed=1, with_cycle=False)
        f = lt.build_frame(price, macro)
        cols = [c for c, _ in lt.FEATURES if c in f.columns]
        ev, _ = lt.evaluate(f, cols, 12)
        self.assertFalse(ev["beats_zero"])

    def test_signal_on_embedded_cycle(self):
        ev, _ = lt.evaluate(self.frame, self.cols, 12)
        self.assertGreater(ev["corr_spearman"], 0.3)

    def test_nonpositive_prices_are_dropped_not_logged(self):
        price, macro = synthetic()
        bad = price.copy()
        bad.iloc[:6] = 0.0              # Yahoo 수정종가 오류 흉내
        f = lt.build_frame(bad, macro)
        self.assertEqual(f.index[0], price.index[6])
        self.assertTrue((f["price"] > 0).all())
        self.assertIn("<svg", lt.render_chart(f, "x"))

    def test_log_ticks_cover_any_price_level(self):
        self.assertIn(1000000, lt.log_ticks(15000, 1650000))     # SK하이닉스는 100만원대다
        self.assertIn(500, lt.log_ticks(300, 260000))
        self.assertEqual(lt.log_ticks(0, 10), [])                # 0 이하는 로그축에 못 그린다
        self.assertEqual(lt.log_ticks(float("nan"), 10), [])

    def test_chart_shows_no_data_instead_of_nan_labels(self):
        idx = pd.date_range("2000-01-31", periods=120, freq="ME")
        f = pd.DataFrame({"price": np.linspace(1000, 50000, len(idx))}, index=idx)   # 지표 없음
        svg = lt.render_chart(f, "x")
        self.assertIn("자료 없음", svg)
        self.assertNotIn("nan", svg)

    def test_late_starting_exports_series_does_not_crash(self):
        price, macro = synthetic()
        ex = macro["semiconductor_exports"]
        macro["semiconductor_exports"] = ex[ex["month"] >= "2009-01-01"].reset_index(drop=True)
        f = lt.build_frame(price, macro)
        cols = [c for c, _ in lt.FEATURES if c in f.columns]
        ev, _ = lt.evaluate(f, cols, 12)
        self.assertGreater(ev["n_oof"], 0)
        self.assertIn("<svg", lt.render_chart(f, "x"))

    def test_fragment_renders(self):
        from pathlib import Path
        lt.monthly_prices = lambda t, c, fetch=True: synthetic()[0]
        lt.load_macro_data = lambda *a, **k: (synthetic()[1], {"latest_month": {}, "snapshot_hash": "x"})
        res, _ = lt.analyse("samsung", Path("/tmp/lt_test"), fetch=False)
        html_ = lt.render_fragment(res)
        self.assertIn("7. 장기 전망", html_)
        self.assertIn("검증", html_)
        self.assertNotIn("HP 필터를 적용", html_)


if __name__ == "__main__":
    unittest.main()


class DetrendedChartTests(unittest.TestCase):
    """수준이 아니라 추세를 뺀 사이클끼리 겹쳐야 오르내림이 맞물려 보인다."""

    def test_chart_uses_price_returns_not_levels_for_the_overlay(self):
        price, macro = synthetic()
        f = lt.build_frame(price, macro)
        svg = lt.render_chart(f, "삼성전자")
        self.assertIn("12개월 수익률", svg)          # 위 칸은 수익률
        self.assertIn("실제 주가 수준", svg)          # 수준은 아래 참고 칸에만
        self.assertEqual(svg.count("<polyline"), 4)  # 수익률·수출·선행 + 수준

    def test_lead_lag_finds_a_known_shift(self):
        # 수출이 주가보다 6개월 뒤에 오도록 만들면, 주가가 6개월 선행으로 잡혀야 한다.
        idx = pd.date_range("2000-01-31", periods=300, freq="ME")
        cycle = np.sin(np.arange(len(idx)) / 9)
        f = pd.DataFrame({"mom_12m": cycle,
                          "macro_semiconductor_yoy": np.roll(cycle, 6)}, index=idx)
        got = lt.lead_lag(f)
        self.assertEqual(got["lead_months"], 6)
        self.assertGreater(got["corr"], 0.9)

    def test_lead_lag_is_none_without_the_columns(self):
        self.assertIsNone(lt.lead_lag(pd.DataFrame({"mom_12m": [1, 2, 3]})))

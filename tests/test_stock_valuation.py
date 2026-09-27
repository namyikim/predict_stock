# -*- coding: utf-8 -*-
"""주가 결정 요인과 평가(2026-09-28): 반도체 수출액·원/달러 회귀의 적정 주가."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import stock_valuation as sv  # noqa: E402


def data(n=200, krw_coef=0.5, noise=0.05, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.period_range("2005-01", periods=n + 11, freq="M")
    exports = pd.Series(np.exp(np.linspace(21, 23, len(idx)) + rng.normal(0, .05, len(idx))), index=idx)
    krw = pd.Series(1100 * np.exp(rng.normal(0, .05, len(idx))), index=idx)
    exp12 = exports.rolling(12).sum()
    price = np.exp(-20 + 1.2 * np.log(exp12) + krw_coef * np.log(krw) + rng.normal(0, noise, len(idx)))
    return price, exports, krw


class FitTests(unittest.TestCase):
    def test_recovers_coefficients_and_reports_stability(self):
        price, exports, krw = data()
        frame = sv.build(price, exports, krw, today="2030-01-01")
        out, fit = sv.fit(frame)
        self.assertAlmostEqual(fit["coef"][1], 1.2, delta=0.05)
        self.assertAlmostEqual(fit["coef"][2], 0.5, delta=0.1)
        self.assertGreater(fit["r2"], 0.9)
        self.assertTrue(fit["stable"])
        self.assertAlmostEqual(float(out["gap"].iloc[-1]), float(out["price"].iloc[-1] / out["fair"].iloc[-1] - 1))

    def test_the_month_in_progress_is_left_out(self):
        price, exports, krw = data()
        last = price.index[-1]
        frame = sv.build(price, exports, krw, today=str(last.to_timestamp()))
        self.assertLess(frame.index.max(), last)


class RenderTests(unittest.TestCase):
    def render(self, krw_coef):
        import build_longterm_report as bl
        price, exports, krw = data(krw_coef=krw_coef)
        out, fit = sv.fit(sv.build(price, exports, krw, today="2030-01-01"))
        return "".join(sv.render({"out": out, "fit": fit}, "삼성전자", bl.table, bl.TD, bl.TDR, bl.TH, bl.THR))

    def test_block_shows_the_chart_formula_and_limits(self):
        html = self.render(0.5)
        for text in ("주가 결정 요인과 평가", "적정 주가 대비 괴리(우, %)", "식: ln(주가) =", "목표가나 매수·매도 의견이 아닙니다",
                     "원화가 약할수록"):
            self.assertIn(text, html)
        self.assertNotIn("+ -", html)

    def test_direction_sentence_follows_the_sign(self):
        self.assertIn("원화가 강할수록", self.render(-0.8))

    def test_wired_into_the_long_term_report(self):
        src = (ROOT / "tools" / "build_longterm_report.py").read_text(encoding="utf-8")
        self.assertIn("stock_valuation.analyse(spec[\"ticker\"], macro, cache, fetch=fetch)", src)
        self.assertIn("stock_valuation.render(r.get(\"stock_value\")", src)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""금값 결정 요인과 평가(2026-09-27): 회귀식의 적정 가격·괴리와 그림."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import gold_valuation as gv  # noqa: E402


def frame(n=60):
    idx = pd.period_range("2000-01", periods=n, freq="M")
    rng = np.random.default_rng(0)
    cpi = np.linspace(170, 330, n)
    dxy = 100 + rng.normal(0, 5, n)
    y10 = 4 + rng.normal(0, 1, n)
    f = pd.DataFrame({"cpi": cpi, "dxy": dxy, "y10": y10, "cpi_carried": False}, index=idx)
    f["gold"] = gv.fair_value(f) * np.exp(rng.normal(0, .1, n))
    return f


class FormulaTests(unittest.TestCase):
    def test_fair_value_is_the_given_formula(self):
        one = pd.DataFrame({"cpi": [334.131], "dxy": [98.0], "y10": [4.2]})
        expected = np.exp(-6.74 + 3.69 * np.log(334.131) - 1.37 * np.log(98.0) - 0.06 * 4.2)
        self.assertAlmostEqual(float(gv.fair_value(one).iloc[0]), expected)
        self.assertTrue(3000 < expected < 4000, "2026년 수준에서 적정 가격은 3천 달러대")

    def test_evaluate_reports_gap_and_refit(self):
        out, fit = gv.evaluate(frame())
        self.assertIn("gap", out)
        self.assertAlmostEqual(float(out["gap"].iloc[0]), float(out["gold"].iloc[0] / out["fair"].iloc[0] - 1))
        self.assertEqual(len(fit["coef"]), 4)
        self.assertGreater(fit["r2"], 0.5)


class ChartTests(unittest.TestCase):
    def test_axes_match_the_request(self):
        out, _ = gv.evaluate(frame())
        svg = gv.chart_svg(out)
        self.assertIn("-30%", svg)
        self.assertIn("+70%", svg)
        self.assertIn("$0", svg)
        self.assertIn(">2000<", svg, "가로축은 2000년부터")
        for label in ("금 가격(좌, 달러/온스)", "적정 가격(좌, 회귀식)", "적정 가격 대비 괴리(우, %)"):
            self.assertIn(label, svg)

    def test_gap_outside_the_axis_is_clipped_not_drawn_off_chart(self):
        out, _ = gv.evaluate(frame())
        out.loc[out.index[5], "gap"] = 2.0
        svg = gv.chart_svg(out)
        ys = [float(p.split(",")[1]) for p in svg.split('stroke="#c0392b" stroke-width="1.2"')[0].split('points="')[-1]
              .split('"')[0].split()]
        self.assertGreaterEqual(min(ys), 52 - 1e-6)

    def test_render_block_explains_the_formula_and_limits(self):
        import build_metals_report as mr
        out, fit = gv.evaluate(frame())
        html = "".join(gv.render(out, fit, {"cpi_source": "BLS", "cpi_last": "2004-12", "gold_source": "세계은행"},
                                 mr.table, mr.TD, mr.TDR, mr.TH, mr.THR, mr.note))
        self.assertIn("금값 결정 요인과 평가", html)
        self.assertIn("ln(금값) = -6.74 + 3.69·ln(미국 CPI)", html)
        self.assertIn("사후적", html)
        self.assertIn("목표가나 매수·매도 의견이 아닙니다", html)


class DataTests(unittest.TestCase):
    def test_archives_cover_2000(self):
        gold = gv._monthly_csv(ROOT / "macro_history" / "gold_worldbank_monthly.csv")
        cpi = gv._monthly_csv(ROOT / "macro_history" / "us_cpi.csv")
        self.assertEqual(str(gold.index.min()), "2000-01")
        self.assertEqual(str(cpi.index.min()), "2000-01")
        self.assertAlmostEqual(float(cpi.loc["2000-01"]), 169.3)

    def test_metals_report_wires_the_block_and_the_fred_key(self):
        src = (ROOT / "tools" / "build_metals_report.py").read_text(encoding="utf-8")
        self.assertIn("gold_valuation.load_inputs(", src)
        self.assertIn('results["gold"]["valuation"]', src)
        workflow = (ROOT / ".github" / "workflows" / "daily-report.yml").read_text(encoding="utf-8")
        block = workflow[workflow.index("- name: 금·은 예측 보고서"):]
        self.assertIn("FRED_API_KEY: ${{ secrets.FRED_API_KEY }}", block[:600])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Colab 갱신 공지·단기 위치 그림·PER/PBR 재무제표 계산(2026-09-28)."""
import sys
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import colab_freshness as cf  # noqa: E402
import forecast_utils as fu  # noqa: E402
import report_html  # noqa: E402
import valuation_reference as vr  # noqa: E402


class ColabFreshnessTests(unittest.TestCase):
    def test_release_calendar(self):
        # 수출입 확정은 다음 달 15일 무렵 → 25일부터 지난달이 있어야 한다
        self.assertEqual(cf.expected_monthly(date(2026, 9, 28), 25, 1), "2026-08")
        self.assertEqual(cf.expected_monthly(date(2026, 9, 20), 25, 1), "2026-07")
        # 산업활동동향(선행지수)은 다음 달 말 → 8일부터 두 달 전
        self.assertEqual(cf.expected_monthly(date(2026, 10, 9), 8, 2), "2026-08")
        self.assertEqual(cf.expected_monthly(date(2026, 10, 3), 8, 2), "2026-07")
        self.assertEqual(cf.expected_flash(date(2026, 9, 28)), ("2026-09", 20))
        self.assertEqual(cf.expected_flash(date(2026, 9, 16)), ("2026-09", 10))
        self.assertEqual(cf.expected_flash(date(2026, 10, 5)), ("2026-09", 20))

    def test_late_archives_turn_the_notice_on(self):
        root = Path(__import__("tempfile").mkdtemp())
        (root / "macro_history").mkdir()
        pd.DataFrame({"month": ["2026-06-01"], "value": [1.0]}).to_csv(root / "macro_history" / "semiconductor_exports.csv", index=False)
        pd.DataFrame({"month": ["2026-09"], "days": [20], "usd": [1.0]}).to_csv(root / "macro_history" / "customs_flash.csv", index=False)
        status = cf.check(today=date(2026, 9, 28), root=root)
        self.assertTrue(status["needed"])
        self.assertEqual([i["file"] for i in status["late"]], ["semiconductor_exports.csv"])
        self.assertIn("colab.research.google.com", status["colab_url"])

    def test_only_the_admin_page_reads_the_status_file(self):
        # 공지는 관리자 페이지에만(2026-09-28 요청). 공개 페이지(메인·종목 보고서)에는 띄우지 않는다.
        admin = (ROOT / "docs" / "admin" / "index.html").read_text(encoding="utf-8")
        self.assertIn('fetch("../colab_status.json"', admin)
        self.assertIn("Colab 갱신 점검", admin)
        self.assertNotIn("colab_status.json", (ROOT / "docs" / "index.html").read_text(encoding="utf-8"))
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        self.assertNotIn("colab_notice_html(", "".join("".join(c["source"]) for c in nb["cells"] if "tags" not in c.get("metadata", {})))
        self.assertIn("Colab 업데이트 필요 없음", report_html.colab_notice_html("../", show_ok=True))
        workflow = (ROOT / ".github" / "workflows" / "daily-report.yml").read_text(encoding="utf-8")
        self.assertIn("python tools/colab_freshness.py --publish", workflow)


class PositionChartTests(unittest.TestCase):
    def test_chart_shows_price_range_gauges_and_comparison(self):
        rng = np.random.default_rng(0)
        close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, .02, 900))), index=pd.bdate_range("2023-01-02", periods=900))
        pos = fu.price_position(close)
        svg = fu.price_position_svg(close, pos)
        for text in ("석 달 최고", "석 달 최저", "한 달(20일)", "석 달(60일)", "반년(120일)", f"{pos['position_60']:.0%} 지점"):
            self.assertIn(text, svg)
        self.assertIn("같은 자리(", svg)
        self.assertEqual(fu.price_position_svg(close.iloc[:50], pos), "")


class StatementBasisTests(unittest.TestCase):
    def test_eps_uses_the_share_count_behind_reported_eps(self):
        cols = pd.to_datetime(["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])

        class Stock:
            quarterly_income_stmt = pd.DataFrame(
                [[66.0, 33.0, 22.0, 11.0], [10.0, 5.0, np.nan, 1.0]],
                index=["Net Income Common Stockholders", "Basic EPS"], columns=cols)
            quarterly_balance_sheet = pd.DataFrame([[660.0, 600.0]], index=["Common Stock Equity"], columns=cols[:2])

        basis = vr.statement_basis(Stock())
        # 주식 수 = 순이익 ÷ EPS 의 중앙값 = 6.6(66/10), 6.6(33/5), 11(11/1) → 6.6
        self.assertAlmostEqual(basis["trailingEps"], 132 / 6.6)
        self.assertAlmostEqual(basis["bookValue"], 660 / 6.6)
        result = vr.calculate({"currency": "KRW", "financialCurrency": "KRW", **basis}, 100.0, "2026-09-25",
                              "2026-09-28", "X.KS", {"per": [8, 12, 16], "pbr": [1, 1.5, 2], "basis": "b"})
        self.assertEqual(result["status"], "available")


if __name__ == "__main__":
    unittest.main()

"""가격 범위 막대 그림을 금·은 보고서(달러)에도 쓴다(2026-10-02). 종목 보고서의 그림과 같은 함수다."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forecast_utils as fu  # noqa: E402

ROWS = [
    {"horizon": "1주일", "target_date": "2026-10-09", "signal": "없음", "predicted_close": 4400.0,
     "predicted_return": .01, "center_close": 4321.2, "low_close": 4200.5, "high_close": 4450.25, "current_close": 4321.2},
    {"horizon": "1개월", "target_date": "2026-10-30", "signal": "있음", "predicted_close": 4380.0,
     "predicted_return": .0136, "center_close": 4380.0, "low_close": 4050.0, "high_close": 4700.0, "current_close": 4321.2},
]
DOLLAR = lambda v: f"${v:,.2f}"   # noqa: E731


class MetalChartTests(unittest.TestCase):
    def html(self, rows=ROWS):
        return fu.price_rows_range_html(rows, "예상 구간 한눈에 (달러/온스)", money=DOLLAR)

    def test_rows_and_dollar_formatting(self):
        html = self.html()
        self.assertEqual(html.count('class="pr-row"'), 2)
        self.assertIn("$4,200.50~$4,450.25", html)
        self.assertIn("$4,050.00~$4,700.00", html)
        self.assertIn("기준 가격(기준 봉 종가) $4,321.20", html)
        self.assertIn("예상 구간 한눈에 (달러/온스)", html)
        self.assertNotIn("원", html.split("</style>", 1)[1].replace("예상 구간", ""))   # 원화 표기가 섞이지 않는다

    def test_failed_gate_shows_the_band_but_no_point(self):
        html = self.html()
        self.assertNotIn("$4,400.00", html)                          # 신호 없는 기간의 예상가는 숫자·점 모두 없다
        self.assertIn("$4,380.00", html)
        self.assertIn("+1.36%", html)
        self.assertEqual(html.count("box-shadow:0 0 0 1px #1a5490"), 1)
        self.assertEqual(html.count("예측하기 어렵습니다"), 1)

    def test_empty_rows_render_nothing(self):
        self.assertEqual(self.html([]), "")
        self.assertEqual(self.html(None), "")

    def test_stock_chart_text_is_unchanged(self):
        html = fu.price_range_html({"signal": "있음", "predicted_open": 70100, "predicted_return": .004,
                                    "current_close": 69820, "low_open": 69500, "high_open": 70700}, [])
        self.assertIn("69,500~70,700원", html)
        self.assertIn("기준 가격(전일 종가) 69,820원", html)
        self.assertIn("가격 전망 — 시초가예측과 종가예측", html)

    def test_metals_report_uses_the_chart(self):
        source = (ROOT / "tools" / "build_metals_report.py").read_text(encoding="utf-8")
        self.assertIn('price_rows_range_html(rows, "예상 구간 한눈에 (달러/온스)"', source)


if __name__ == "__main__":
    unittest.main()

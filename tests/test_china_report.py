"""중국 보고서 절 순서: 진행 중인 15차가 맨 앞, 과거 계획은 최신순."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_china_report as cr  # noqa: E402

SOURCE = (ROOT / "tools" / "build_china_report.py").read_text(encoding="utf-8")


class SectionOrderTests(unittest.TestCase):
    def test_section_titles_are_numbered_with_the_current_plan_first(self):
        for title in ('h3("1. 한눈에")', 'h3("2. 15차 계획(2026~2030)과 후보 종목")',
                      'h3("3. 계획별: 정책 업종 대표 종목의 계획 기간 수익률")',
                      'h3("4. CSI 300(沪深300) 분석")', 'h3("5. 데이터와 방법")'):
            self.assertIn(title, SOURCE)

    def test_parts_are_reordered_before_returning(self):
        self.assertIn("parts[_i_15:_i_data]", SOURCE)
        # 재배치가 5절(데이터와 방법) 뒤가 아니라 그 앞의 세 절만 옮기는지
        self.assertIn("parts[:_i_plans] + parts[_i_15:_i_data]", SOURCE)

    def test_past_plans_stay_newest_first(self):
        self.assertIn("for pr in reversed(plan_results):", SOURCE)
        self.assertIn('(2026, 2030, "15차(진행 중)"), (2021, 2025, "14차")', SOURCE)

    def test_cross_references_match_the_new_numbers(self):
        self.assertIn("지수 자체가 약했다(4절)", SOURCE)
        self.assertIn("3절의 교훈을 그대로 적용하면", SOURCE)
        self.assertNotIn("2절의 교훈", SOURCE)
        self.assertNotIn("약했다(3절)", SOURCE)


if __name__ == "__main__":
    unittest.main()


def fake_row(name="종목", missing=False, late=False):
    return {"ticker": "000001.SZ", "name": name, "sector": "업종", "why": "이유",
            "missing": missing, "late": late, "theme": "테마", "price": 12.3, "ret": .15, "bench_ret": .08,
            "cagr": .03, "mdd": -.4, "start": "2021-01-04", "end": "2025-12-31",
            "ytd": .1, "r1": .2, "r3": .3, "r5": .4, "from_5y_peak": -.2, "to_today": .5}


def fake_render_inputs():
    import pandas as pd
    plan_results = []
    for plan in cr.PLANS:
        rows = [fake_row(), fake_row(missing=True)]
        agg = {"n": 1, "n_b": 1, "beat": 1, "median_excess": .05,
               "basket": .15, "bench_ret": .08}
        plan_results.append({"plan": plan, "rows": rows, "agg": agg})
    index = pd.bdate_range("2012-05-01", "2026-09-01", freq="B")
    series = pd.Series(range(len(index)), index=index, dtype=float) + 100
    csi = {"series": series, "start": "2012-05-04", "end": "2026-09-01", "years": 14.3,
           "ret": 1.2, "cagr": .06, "vol": .22, "mdd": -.45, "peak_date": "2021-02-10",
           "from_peak": -.3, "ma200": 150., "index_level": 4100., "index_date": "2026-09-01",
           "yearly": [(2024, .1, False), (2025, -.05, False), (2026, .2, True)],
           "plans": [{"label": "15차(진행 중)", "ret": .1, "cagr": .1, "mdd": -.1, "bench": .05,
                      "start": "2026-01-02", "end": "2026-09-01"},
                     {"label": "14차", "ret": .2, "cagr": .04, "mdd": -.3, "bench": .12,
                      "start": "2021-01-04", "end": "2025-12-31"}],
           "peers": [{"name": "S&P 500", "ticker": "^GSPC", "ret": .5, "cagr": .12,
                      "vol": .17, "mdd": -.34}]}
    return plan_results, csi, [fake_row("15차 후보")]


class RenderSmokeTests(unittest.TestCase):
    """절 재배치는 모든 절을 만든 뒤에 해야 한다(경계값이 마지막 절에서 정해진다)."""

    def test_render_runs_and_orders_sections(self):
        plan_results, csi, rows15 = fake_render_inputs()
        html = cr.render(plan_results, csi, rows15, "2026-09-08", "2026-09-08")
        titles = re.findall(r'border-bottom:1px solid #ddd">([^<]+)</h3>', html)
        self.assertEqual([t[:2] for t in titles], ["1.", "2.", "3.", "4.", "5."])
        self.assertIn("15차", titles[1])
        self.assertIn("계획별", titles[2])
        self.assertIn("CSI", titles[3])
        # 잘라 붙이는 과정에서 조각이 사라지거나 중복되지 않아야 한다.
        self.assertEqual(html.count("5. 데이터와 방법"), 1)
        self.assertEqual(html.count("14차 (2021~2025)"), 1)

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
    def test_past_plans_stay_newest_first(self):
        self.assertIn("for pr in reversed(plan_results):", SOURCE)
        self.assertIn('(2026, 2030, "15차(진행 중)"), (2021, 2025, "14차")', SOURCE)

    def test_cross_references_use_menu_names_not_section_numbers(self):
        # 절마다 메뉴가 되어 번호가 없다(2026-09-28). 'n절' 참조가 남으면 가리킬 곳이 없다.
        self.assertIn("지수 자체가 약했다(CSI 300 메뉴)", SOURCE)
        self.assertNotRegex(SOURCE, r"[1-5]절(의|이|:|\))")


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
    """8차~15차가 한 페이지에 모두 있어 보기 힘들었다 — 차수마다 왼쪽 메뉴 하나(2026-09-28 요청)."""

    def render(self):
        plan_results, csi, rows15 = fake_render_inputs()
        return cr.render(plan_results, csi, rows15, "2026-09-08", "2026-09-08")

    def test_each_plan_is_its_own_menu_newest_first(self):
        html = self.render()
        menu = re.findall(r'<a href="#rtab-\d+" aria-selected="\w+">([^<]+)</a>', html)
        self.assertEqual(menu[0], "한눈에")
        self.assertEqual(menu[1], "15차 (진행 중)")
        self.assertEqual([m.split("차")[0] for m in menu[2:9]], ["14", "13", "12", "11", "10", "9", "8"])
        self.assertEqual(menu[9:], ["CSI 300", "데이터와 방법"])

    def test_one_section_per_menu_without_numbers(self):
        import report_html as rh
        html = self.render()
        self.assertEqual(rh.tab_structure_problems(html), [])
        titles = [re.findall(r"<h3[^>]*>([^<]+)</h3>", p["inner"]) for p in rh.panels(html)]
        self.assertEqual([len(t) for t in titles], [1] * 11)
        self.assertEqual(titles[2], ["14차 (2021~2025)"])        # '1. 14차'처럼 번호가 붙지 않는다
        self.assertEqual(html.count("14차 (2021~2025)</h3>"), 1)

    def test_overview_links_to_every_plan(self):
        html = self.render()
        for n in range(8, 16):
            self.assertIn(f'href="#plan-{n}"', html)
            self.assertIn(f'id="plan-{n}"', html)


if __name__ == "__main__":
    unittest.main()

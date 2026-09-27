# -*- coding: utf-8 -*-
"""금·은 장기 전망(2026-09-27): 비슷했던 달의 1년 뒤, 가격 도달 확률, 전망 원장."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import metals_longterm as ml  # noqa: E402
import outlook_ledger as ol  # noqa: E402


def monthly(values, start="2010-01"):
    return pd.Series(values, index=pd.period_range(start, periods=len(values), freq="M"), dtype=float)


class BucketTests(unittest.TestCase):
    def test_forward_return_is_twelve_months_ahead(self):
        price = monthly(np.arange(1, 30, dtype=float))
        fwd = ml.forward_log_return(price)
        self.assertAlmostEqual(fwd.iloc[0], np.log(13 / 1))
        self.assertTrue(np.isnan(fwd.iloc[-1]))

    def test_buckets_count_months_and_up_share(self):
        price = monthly(np.linspace(100, 200, 40))
        signal = monthly([0.35] * 10 + [0.05] * 30)
        rows, overall = ml.bucket_stats(signal, price, ml.SPECS["gold"]["edges"], ml.SPECS["gold"]["labels"])
        high = next(r for r in rows if r["bucket"] == "+30% 이상")
        self.assertEqual(high["n"], 10)
        self.assertEqual(high["up"], 1.0)
        self.assertEqual(overall["n"], 28)                     # 마지막 12개월은 1년 뒤가 없다

    def test_the_month_in_progress_is_not_used_to_pick_the_bucket(self):
        s = monthly([1., 2., 3.], start="2026-07")
        self.assertEqual(list(ml.last_complete(s, today="2026-09-15").index.astype(str)), ["2026-07", "2026-08"])


class LedgerTests(unittest.TestCase):
    def lt(self):
        return {"key": "gold", "as_of": "2026-08", "now_bucket": "+30% 이상",
                "rows": [{"bucket": "+30% 이상", "n": 6, "median": 0.08, "q25": -0.01, "q75": 0.2, "up": 0.5}],
                "odds": None, "price_date": None,
                "price_monthly": monthly([100.] * 20 + [110.], start="2026-01")}

    def test_current_bucket_is_recorded_once_for_the_month(self):
        rows = ml.collect(self.lt())
        self.assertEqual({r["series"] for r in rows}, {"bucket_return_12m", "bucket_up_12m"})
        self.assertTrue(all(r["target_period"] == "2027-08" for r in rows))
        ledger, added = ol.record(ol.read_ledger_text(""), rows)
        ledger, again = ol.record(ledger, ml.collect(self.lt()))
        self.assertEqual((added, again), (2, 0))

    def test_scored_with_monthly_averages_after_the_target_month_ends(self):
        lt = self.lt()
        ledger, _ = ol.record(ol.read_ledger_text(""), ml.collect(lt))
        close = pd.Series([1.0], index=pd.to_datetime(["2026-09-01"]))
        ledger, n = ol.score(ledger, ml.actuals(lt, close, today="2027-08-15"))
        self.assertEqual(n, 0, "대상 달(2027-08)이 끝나기 전에는 채점하지 않는다")
        lt["price_monthly"] = monthly([100.] * 19 + [110.], start="2026-01")      # 2026-01~2027-08
        ledger, n = ol.score(ledger, ml.actuals(lt, close, today="2027-09-02"))
        self.assertEqual(n, 2)
        row = ledger.set_index("series").loc["bucket_return_12m"]
        self.assertAlmostEqual(row["actual"], np.log(110 / 100))


class RenderTests(unittest.TestCase):
    def test_section_is_honest_about_what_it_is(self):
        html = "".join(ml.render("gold", "금", {"key": "gold", "rows": None}, [], [], None,
                                 lambda h, b, w=0: h + b, "", "", "", "", lambda t, warn=False: t))
        self.assertIn("금 · 장기 전망", html)
        self.assertIn("검증된 금·은 모델이 없습니다", html)
        self.assertIn("지난 장기 전망은 맞았나", html)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""P10 — 공동 학습 패널의 누수·결측 계약.

  1. 같은 날짜의 모든 종목이 같은 폴드 쪽에 들어간다.
  2. 상장 전·거래정지 날짜를 만들어 채우지 않는다(보간 없음).
  3. 특징은 d일 종가까지의 정보이고, 라벨은 그 종목의 다음날 수익률과 과거 변동성 밴드로만 정한다.
  4. 선정 규칙·기준일이 파일에 고정돼 있고 기준 미달 종목은 사유와 함께 제외된다.
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "model_improvement"))
import panel_data as pdm  # noqa: E402


def _bars(start, n, seed):
    rng = np.random.default_rng(seed)
    idx = pd.DatetimeIndex(pd.bdate_range(start, periods=n))
    close = 100 * np.cumprod(1 + rng.normal(0, .015, n))
    return pd.DataFrame({"open": close * (1 + rng.normal(0, .003, n)), "close": close,
                         "volume": rng.integers(1e5, 1e6, n)}, index=idx)


class UniverseTests(unittest.TestCase):
    def test_selection_rule_and_date_are_fixed(self):
        self.assertEqual(pdm.SELECTION_DATE, "2026-09-10")
        self.assertTrue(pdm.SELECTION_RULE)
        self.assertGreaterEqual(len(pdm.PANEL_UNIVERSE), 5)

    def test_late_listings_are_excluded_with_a_reason(self):
        first = {t: "2010-01-04" for t, _, _ in pdm.PANEL_UNIVERSE}
        first["403870.KS"] = "2022-07-15"
        kept, excluded = pdm.eligible_universe(first)
        self.assertIn("403870.KS", excluded)
        self.assertIn("2022-07-15", excluded["403870.KS"])
        self.assertNotIn("403870.KS", [t for t, _, _ in kept])

    def test_missing_prices_are_excluded(self):
        kept, excluded = pdm.eligible_universe({})
        self.assertEqual(kept, [])
        self.assertEqual(len(excluded), len(pdm.PANEL_UNIVERSE))


class PanelBuildTests(unittest.TestCase):
    def setUp(self):
        self.bars = {"A": _bars("2020-01-01", 400, 1),
                     "B": _bars("2020-06-01", 300, 2)}      # B는 늦게 상장
        self.panel = pdm.build_panel(self.bars)

    def test_no_dates_are_fabricated_for_late_listing(self):
        self.assertEqual(pdm.check_no_interpolation(self.panel, self.bars), 0)
        b_dates = self.panel[self.panel["instrument"] == "B"]["date"]
        self.assertGreaterEqual(b_dates.min(), pd.Timestamp("2020-06-01"))

    def test_suspension_gap_is_not_filled(self):
        bars = {"A": _bars("2021-01-01", 200, 3)}
        bars["A"] = bars["A"].drop(bars["A"].index[100:110])       # 10일 거래정지
        panel = pdm.build_panel(bars)
        self.assertEqual(len(panel), 190)
        self.assertEqual(pdm.check_no_interpolation(panel, bars), 0)

    def test_features_use_only_past_and_label_uses_next_day(self):
        a = self.bars["A"]
        rows = self.panel[self.panel["instrument"] == "A"].set_index("date")
        i = 30
        d, nxt = a.index[i], a.index[i + 1]
        self.assertAlmostEqual(rows.loc[d, "ret_1"], a["close"].iloc[i] / a["close"].iloc[i - 1] - 1, places=12)
        self.assertAlmostEqual(rows.loc[d, "target_ret"], a["close"].loc[nxt] / a["close"].loc[d] - 1, places=12)

    def test_band_never_uses_the_labeled_day(self):
        """행 d의 라벨은 d+1 수익률이고 밴드는 d까지의 변동성이다. d+1 가격을 바꿔도 d의 밴드는 그대로."""
        a = self.bars["A"].copy()
        panel_before = pdm.build_panel({"A": a}).set_index("date")
        a.loc[a.index[101], "close"] *= 1.5                      # 라벨 대상일(d+1)의 가격만 바꿈
        panel_after = pdm.build_panel({"A": a}).set_index("date")
        d = a.index[100]
        self.assertAlmostEqual(panel_before.loc[d, "band"], panel_after.loc[d, "band"],
                               msg="라벨 대상일의 가격이 그 행의 밴드에 들어갔다")
        self.assertNotAlmostEqual(panel_before.loc[d, "target_ret"], panel_after.loc[d, "target_ret"])

    def test_last_day_has_no_label(self):
        rows = self.panel[self.panel["instrument"] == "A"]
        self.assertTrue(np.isnan(rows["y"].iloc[-1]))

    def test_no_price_level_feature(self):
        for col in self.panel.columns:
            self.assertNotIn("close", col.lower())
            self.assertNotIn("price", col.lower())


class DateFoldTests(unittest.TestCase):
    def test_all_instruments_of_a_date_share_a_side(self):
        bars = {"A": _bars("2020-01-01", 300, 4), "B": _bars("2020-01-01", 300, 5)}
        panel = pdm.build_panel(bars)
        folds = pdm.date_folds(panel["date"], [("2020-01-01", "2020-09-01", "2021-01-01")])
        tr, te = folds[0]["train_idx"], folds[0]["test_idx"]
        train_dates, test_dates = set(panel["date"].iloc[tr]), set(panel["date"].iloc[te])
        self.assertTrue(train_dates.isdisjoint(test_dates), "같은 날짜가 학습·시험 양쪽에 있다")
        for d in test_dates:
            n = ((panel["date"] == d)).sum()
            self.assertEqual((panel["date"].iloc[te] == d).sum(), n, "한 날짜의 일부 종목만 시험에 들어갔다")

    def test_folds_look_forward(self):
        bars = {"A": _bars("2020-01-01", 300, 6)}
        panel = pdm.build_panel(bars)
        folds = pdm.date_folds(panel["date"], [("2020-01-01", "2020-09-01", "2021-01-01")])
        self.assertLess(panel["date"].iloc[folds[0]["train_idx"]].max(),
                        panel["date"].iloc[folds[0]["test_idx"]].min())


if __name__ == "__main__":
    unittest.main()

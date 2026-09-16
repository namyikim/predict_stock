# -*- coding: utf-8 -*-
"""원/달러 결정 요인 — VAR 분산분해(2026-09-15).

분산분해는 설정에 따라 결과가 크게 달라진다. 무엇을 왜 골랐는지와, 계산이 실제로 맞는지를
테스트로 고정한다.
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import fx_variance as fx  # noqa: E402


def synthetic(n=300, dxy_beta=0.9, seed=0):
    """달러지수가 원/달러를 dxy_beta 만큼 끌고 나머지는 무관한 자료."""
    rng = np.random.default_rng(seed)
    index = pd.date_range("2001-01-01", periods=n, freq="MS")
    dxy = np.cumsum(rng.normal(0, .01, n))
    krw = np.cumsum(dxy_beta * np.diff(np.r_[0, dxy]) + rng.normal(0, .002, n))
    return pd.DataFrame({
        "dxy": 100 * np.exp(dxy),
        "jpy": 110 * np.exp(np.cumsum(rng.normal(0, .01, n))),
        "cny": 7 * np.exp(np.cumsum(rng.normal(0, .003, n))),
        "rate_gap": np.cumsum(rng.normal(0, .05, n)),
        "current_account": rng.normal(0, 3, n),
        "usdkrw": 1200 * np.exp(krw)}, index=index)


class DecompositionTests(unittest.TestCase):
    def test_finds_the_driver_we_planted(self):
        table, info = fx.fit_and_decompose(synthetic(dxy_beta=0.9))
        self.assertIsNotNone(table)
        for horizon in fx.HORIZONS:
            row = table[horizon]
            others = [row[k] for k in ("jpy", "cny", "rate_gap", "current_account")]
            self.assertGreater(row["dxy"], max(others) * 5,
                               f"{horizon}개월: 심어 둔 요인을 찾지 못했습니다")

    def test_weak_driver_shows_a_smaller_share(self):
        strong, _ = fx.fit_and_decompose(synthetic(dxy_beta=0.9))
        weak, _ = fx.fit_and_decompose(synthetic(dxy_beta=0.2))
        self.assertGreater(strong[1]["dxy"], weak[1]["dxy"])

    def test_each_row_sums_to_one(self):
        table, _ = fx.fit_and_decompose(synthetic())
        for horizon in fx.HORIZONS:
            self.assertAlmostEqual(sum(table[horizon].values()), 1.0, places=6)

    def test_order_does_not_matter(self):
        """일반화 분산분해를 쓰는 이유. 촐레스키였다면 열 순서에 결과가 달라진다."""
        frame = synthetic()
        first, _ = fx.fit_and_decompose(frame)
        shuffled = frame[["current_account", "usdkrw", "cny", "dxy", "rate_gap", "jpy"]]
        second, _ = fx.fit_and_decompose(shuffled)
        for horizon in fx.HORIZONS:
            for name in first[horizon]:
                self.assertAlmostEqual(first[horizon][name], second[horizon][name], places=6,
                                       msg=f"{horizon}개월 {name}: 순서에 따라 결과가 달라졌습니다")

    def test_short_sample_is_refused_instead_of_guessed(self):
        table, info = fx.fit_and_decompose(synthetic(n=20))
        self.assertIsNone(table)
        self.assertIn("부족", info["error"])

    def test_level_series_are_differenced_not_logged(self):
        # 금리차·경상수지는 음수가 될 수 있어 로그를 씌우면 안 된다.
        frame = synthetic()
        frame["rate_gap"] = -abs(frame["rate_gap"])
        prepared = fx.prepare(frame)
        self.assertFalse(prepared["rate_gap"].isna().all())
        self.assertIn("rate_gap", fx.LEVEL_DIFF_ONLY)

    def test_horizons_and_labels_match_the_request(self):
        self.assertEqual(fx.HORIZONS, (1, 3, 6, 12))
        self.assertEqual(fx.FX_FACTORS,
                         ("dxy", "jpy", "cny", "rate_gap", "current_account", "usdkrw"))


class ReportTests(unittest.TestCase):
    def test_table_renders_with_all_columns(self):
        import build_macro_report as macro
        html = macro.build_page(fx_frame=synthetic(), fx_info={"source": "test", "failed": {}})
        for label in fx.FX_LABELS.values():
            self.assertIn(label, html)
        for horizon in fx.HORIZONS:
            self.assertIn(f"{horizon}개월", html)

    def test_states_it_is_not_a_forecast_or_causation(self):
        import build_macro_report as macro
        html = macro.build_page(fx_frame=synthetic(), fx_info={"source": "test", "failed": {}})
        self.assertIn("예측이 아닙니다", html)
        self.assertIn("인과가 아니라", html)
        self.assertIn("행별로 정규화", html)

    def test_missing_data_says_why(self):
        import build_macro_report as macro
        html = macro.build_page(fx_frame=pd.DataFrame(),
                                fx_info={"failed": {"usdkrw": "빈 응답"}})
        self.assertIn("자료를 받지 못했습니다", html)
        self.assertIn("빈 응답", html)

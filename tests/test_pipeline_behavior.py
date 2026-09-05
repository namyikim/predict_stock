"""노트북 코드의 *동작*을 합성 데이터로 검증한다.

문자열 매칭 테스트와 달리, 여기 테스트는 누수를 실제로 주입하면 실패한다.
예를 들어 CELL 9의 `feat["sam_ret_1"] = sam_ret.reindex(all_dates).shift(1)` 에서
`.shift(1)`을 지우면 test_features_use_only_past_information이 실패한다.

노트북에서 코드를 꺼내오는 방식:
- `load_notebook_functions()` 는 지정한 셀에서 함수·클래스 정의만 뽑아 실행한다.
- `run_feature_cell()` 은 CELL 9 전체를 합성 `raw` 딕셔너리 위에서 실행한다.
"""

import ast
import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

NOTEBOOK_PATH = Path(__file__).resolve().parents[1] / "samsung_direction_model_colab.ipynb"


def _code_cells():
    nb = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    return [
        "".join(cell.get("source", []))
        for cell in nb["cells"]
        if cell.get("cell_type") == "code"
    ]


def _cell_containing(marker):
    for source in _code_cells():
        if marker in source:
            return source
    raise AssertionError(f"노트북에서 {marker!r} 를 포함한 코드 셀을 찾지 못했습니다.")


def load_notebook_functions(markers, extra_globals=None):
    """지정한 셀들에서 함수/클래스 정의만 뽑아 실행하고 네임스페이스를 돌려준다."""
    ns = {"np": np, "pd": pd, "math": __import__("math")}
    ns.update(extra_globals or {})
    for marker in markers:
        tree = ast.parse(_cell_containing(marker))
        defs = [
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        module = ast.Module(body=defs, type_ignores=[])
        exec(compile(module, "notebook-defs", "exec"), ns)
    return ns


def make_synthetic_raw(n_days=520, seed=0):
    """합성 OHLCV. 삼성 수익률은 '전일 해외 신호'와 '당일 숨은 충격'의 합으로 만든다."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp("2025-06-30"), periods=n_days)

    foreign_shock = rng.normal(0, 0.01, n_days)      # 해외 시장이 d일에 움직인 크기
    hidden_shock = rng.normal(0, 0.01, n_days)       # 삼성 d일 장중에만 발생하는 충격

    # 삼성 d일 수익률 = 전일 해외 신호 + 당일 숨은 충격
    sam_ret = np.concatenate([[0.0], foreign_shock[:-1] + hidden_shock[1:]])
    close = 50000 * np.exp(np.cumsum(sam_ret))
    frame = pd.DataFrame({
        "open": close * (1 + rng.normal(0, 0.001, n_days)),
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "adj_close": close,
        "volume": rng.integers(5_000_000, 20_000_000, n_days).astype(float),
    }, index=dates)

    def series_like(returns, start=100.0):
        px = start * np.exp(np.cumsum(returns))
        return pd.DataFrame({
            "open": px, "high": px * 1.01, "low": px * 0.99,
            "close": px, "adj_close": px,
            "volume": rng.integers(1_000_000, 5_000_000, n_days).astype(float),
        }, index=dates)

    raw = {"samsung": frame}
    for name in ["kospi", "sk_hynix"]:
        raw[name] = series_like(rng.normal(0, 0.008, n_days))
    for name in ["sox", "nasdaq", "sp500", "micron", "nvidia", "tsmc_adr",
                 "korea_etf", "usdkrw", "dxy", "vix", "us10y", "wti", "samsung_gdr"]:
        raw[name] = series_like(foreign_shock)          # 전부 해외 신호를 담고 있다
    return raw, dates, sam_ret, hidden_shock


def run_feature_cell(raw, **overrides):
    """CELL 9(특징 생성)을 합성 데이터 위에서 그대로 실행한다."""
    cell = _cell_containing('feat["sam_ret_1"]')
    ns = {
        "np": np, "pd": pd, "math": __import__("math"),
        "raw": raw,
        "USE_MACRO_FEATURES": False,
        "TARGET_MODE": "close_to_close",
        "BAND_MODE": "vol_scaled",
        "VOL_BAND_MULT": 0.3,
        "NEUTRAL_BAND": 0.005,
        "LIVE_OPEN_PRICE": None,
        "PREDICTION_DATE_OVERRIDE": None,
        "GLOBAL_ASSETS": [
            "sox", "nasdaq", "sp500", "micron", "nvidia", "tsmc_adr",
            "korea_etf", "usdkrw", "dxy", "vix", "us10y", "wti", "samsung_gdr",
        ],
    }
    ns.update(overrides)
    exec(compile(cell, "cell-features", "exec"), ns)
    return ns


class FeatureLeakageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw, cls.dates, cls.sam_ret, cls.hidden = make_synthetic_raw()
        # 예측일이 합성 데이터 범위 밖으로 나가지 않도록 override를 준다.
        cls.ns = run_feature_cell(
            cls.raw, PREDICTION_DATE_OVERRIDE=(cls.dates[-1] + pd.Timedelta(days=3)).isoformat()
        )
        cls.feat = cls.ns["feat"]
        cls.model_df = cls.ns["model_df"]
        cls.feature_cols = cls.ns["feature_cols"]

    def test_sam_ret_1_is_the_previous_day_return(self):
        """가장 기본적인 누수 검사: 오늘 행의 sam_ret_1은 '어제' 수익률이어야 한다."""
        ret = self.raw["samsung"]["adj_close"].pct_change()
        aligned = self.feat["sam_ret_1"].reindex(self.dates).dropna()
        expected = ret.shift(1).reindex(aligned.index)
        np.testing.assert_allclose(aligned.to_numpy(), expected.to_numpy(), rtol=1e-9, atol=1e-12)

    def test_features_use_only_past_information(self):
        """어떤 특징도 '당일에만 발생한 숨은 충격'과 상관이 있으면 안 된다.

        합성 데이터에서 당일 수익률 = 전일 해외 신호 + 당일 숨은 충격이므로,
        특징이 숨은 충격을 알고 있다면 그것은 미래(당일 장중) 정보다.
        """
        hidden = pd.Series(self.hidden, index=self.dates).reindex(self.model_df.index)
        offenders = []
        for col in self.feature_cols:
            for label, series in (("당일 충격", hidden), ("당일 충격 크기", hidden.abs())):
                corr = self.model_df[col].corr(series)
                if pd.notna(corr) and abs(corr) > 0.15:
                    offenders.append((col, label, round(float(corr), 3)))
        self.assertEqual(offenders, [], f"당일 정보와 상관된 특징: {offenders}")

    def test_band_uses_only_past_volatility(self):
        """밴드가 당일 수익률의 크기를 알고 있으면 라벨이 자기참조가 된다."""
        band = self.model_df["band"]
        same_day_abs = self.model_df["target_return"].abs()
        self.assertLess(abs(float(band.corr(same_day_abs))), 0.35)

    def test_todays_data_cannot_change_todays_features_or_band(self):
        """가장 강한 누수 검사: d일의 가격을 바꿔도 d일 행의 특징과 밴드는 그대로여야 한다.

        d일 행은 d일을 예측하는 행이므로 d일에 발생한 어떤 값도 알아서는 안 된다.
        `.shift(1)` 하나를 지우거나 해외 자산 정렬을 당일로 바꾸면 이 테스트가 깨진다.
        """
        pivot = self.dates[-40]                      # 충분히 뒤쪽의 한 거래일
        perturbed = {name: frame.copy() for name, frame in self.raw.items()}
        for name, frame in perturbed.items():
            if pivot in frame.index:
                for col in ("open", "high", "low", "close", "adj_close"):
                    frame.loc[pivot, col] = float(frame.loc[pivot, col]) * 1.05
                frame.loc[pivot, "volume"] = float(frame.loc[pivot, "volume"]) * 3

        ns_b = run_feature_cell(
            perturbed,
            PREDICTION_DATE_OVERRIDE=(self.dates[-1] + pd.Timedelta(days=3)).isoformat(),
        )
        before = self.feat.loc[self.feat.index <= pivot, self.feature_cols + ["band"]]
        after = ns_b["feat"].loc[ns_b["feat"].index <= pivot, self.feature_cols + ["band"]]

        changed = []
        for col in before.columns:
            a, b = before[col].to_numpy(dtype=float), after[col].to_numpy(dtype=float)
            both_nan = np.isnan(a) & np.isnan(b)
            if not np.allclose(a[~both_nan], b[~both_nan], rtol=1e-9, atol=1e-12, equal_nan=True):
                changed.append(col)
        self.assertEqual(changed, [], f"{pivot.date()} 의 가격이 같은 날 행의 값을 바꿨습니다: {changed}")

    def test_global_features_come_from_an_earlier_bar(self):
        """해외 자산 특징은 예측일보다 앞선 세션의 값이어야 한다.

        합성 데이터에서 해외 자산 수익률은 서로 독립이므로, 정렬이 맞으면
        d일 행은 d-1일 수익률과 완전히 일치하고 d일 수익률과는 무관해야 한다.
        """
        for name in ("sox", "nasdaq", "vix"):
            with self.subTest(asset=name):
                asset_ret = self.raw[name]["adj_close"].pct_change()
                feature = self.feat[f"{name}_ret_1"].reindex(self.dates)
                same_day = float(feature.corr(asset_ret))
                previous_day = float(feature.corr(asset_ret.shift(1)))
                self.assertGreater(previous_day, 0.95, f"{name}: 전일 값과 일치해야 합니다.")
                self.assertLess(abs(same_day), 0.5, f"{name}: 당일 세션 값이 들어갔습니다.")

    def test_band_is_not_a_feature(self):
        for col in ("band", "target", "target_return"):
            self.assertNotIn(col, self.feature_cols)

    def test_label_matches_band_rule(self):
        label_from_return = self.ns["label_from_return"]
        got = label_from_return(
            np.array([-0.02, 0.0, 0.02, np.nan]),
            np.array([0.01, 0.01, 0.01, 0.01]),
        )
        np.testing.assert_array_equal(got[:3], np.array([0.0, 1.0, 2.0]))
        self.assertTrue(np.isnan(got[3]))

    def test_model_df_reaches_the_last_bar(self):
        """보조 시계열의 공백이 최근 구간을 조용히 잘라내면 안 된다."""
        gap_days = (self.ns["last_samsung_date"] - self.model_df.index.max()).days
        self.assertLessEqual(gap_days, 10)


class AlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = load_notebook_functions(["def merge_latest_available", "def safe_pct_change"])

    def test_foreign_value_is_visible_only_from_the_next_day(self):
        merge_latest_available = self.ns["merge_latest_available"]
        dates = pd.bdate_range("2024-01-01", periods=10)
        series = pd.Series(range(10), index=dates, dtype=float)
        merged = merge_latest_available(dates, series, availability_days=1)
        # d일 값은 d일 행에 있으면 안 되고(그건 미래), d+1 행에 처음 나타나야 한다.
        self.assertNotEqual(merged.iloc[5], series.iloc[5])
        self.assertEqual(merged.iloc[5], series.iloc[4])

    def test_availability_days_zero_would_leak(self):
        """테스트가 실제로 누수를 구분하는지 확인한다(대조군)."""
        merge_latest_available = self.ns["merge_latest_available"]
        dates = pd.bdate_range("2024-01-01", periods=10)
        series = pd.Series(range(10), index=dates, dtype=float)
        leaky = merge_latest_available(dates, series, availability_days=0)
        self.assertEqual(leaky.iloc[5], series.iloc[5])

    def test_safe_pct_change_blocks_multi_day_moves(self):
        """시계열에 구멍이 있으면 1일 수익률로 둔갑시키지 말고 NaN으로 만들어야 한다."""
        safe_pct_change = self.ns["safe_pct_change"]
        index = pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-03-01"])
        close = pd.Series([100.0, 101.0, 80.0], index=index)
        got = safe_pct_change(close, 1)
        self.assertAlmostEqual(float(got.iloc[1]), 0.01, places=9)
        self.assertTrue(np.isnan(got.iloc[2]), "7주짜리 이동이 1일 수익률로 들어갔습니다.")


class ValidationHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = load_notebook_functions(
            ["def make_walk_forward_folds", "def prediction_frame"],
            extra_globals={
                "FIRST_TEST_DATE": "2021-01-01",
                "TEST_MONTHS": 6,
                "ROLLING_TRAIN_YEARS": 5,
                "MAX_FOLDS": None,
                "SEED": 42,
                "PROB_COLS": ["p_down", "p_flat", "p_up"],
                "BOOTSTRAP_B": 200,
                "COST_BP": 20.0,
                "GAP": pd.Series(dtype=float),
                "SESSION": pd.Series(dtype=float),
            },
        )
        cls.dates = pd.bdate_range("2015-01-01", "2026-06-30")
        cls.folds = cls.ns["make_walk_forward_folds"](cls.dates)

    def test_folds_never_overlap_and_always_look_forward(self):
        self.assertGreater(len(self.folds), 5)
        for fold in self.folds:
            self.assertEqual(len(np.intersect1d(fold["train_idx"], fold["test_idx"])), 0)
            self.assertLess(fold["train_end"], fold["test_start"])
            self.assertLess(fold["train_idx"].max(), fold["test_idx"].min())

    def test_training_window_is_the_configured_length(self):
        for fold in self.folds:
            span_years = (fold["train_end"] - fold["train_start"]).days / 365.25
            self.assertLess(abs(span_years - 5), 0.15)

    def test_test_windows_are_consecutive(self):
        for previous, current in zip(self.folds, self.folds[1:]):
            self.assertLess(previous["test_end"], current["test_start"])

    def test_prediction_frame_normalises_probabilities(self):
        prediction_frame = self.ns["prediction_frame"]
        dates = pd.bdate_range("2024-01-01", periods=4)
        probs = np.array([[10.0, 1.0, 1.0]] * 4)
        frame = prediction_frame("m", dates, [0, 1, 2, 0], probs, 0)
        np.testing.assert_allclose(frame[["p_down", "p_flat", "p_up"]].sum(axis=1), 1.0)
        self.assertTrue((frame["y_pred"] == 0).all())

    def test_prediction_frame_allows_a_separate_hard_prediction(self):
        """'항상 보합' 기준선은 확률을 왜곡하지 않고 하드 예측만 고정해야 한다."""
        prediction_frame = self.ns["prediction_frame"]
        dates = pd.bdate_range("2024-01-01", periods=3)
        prior = np.tile([0.35, 0.27, 0.38], (3, 1))
        frame = prediction_frame("Always flat", dates, [0, 1, 2], prior, 0,
                                 y_pred=np.ones(3, dtype=int))
        self.assertTrue((frame["y_pred"] == 1).all())
        np.testing.assert_allclose(frame["p_up"].to_numpy(), 0.38, atol=1e-9)


class ForecastCalendarTests(unittest.TestCase):
    def test_horizon_counts_start_session_inclusively(self):
        fn = load_notebook_functions(["def krx_sessions_ahead"])["krx_sessions_ahead"]
        self.assertEqual(fn("2026-09-07", 1), pd.Timestamp("2026-09-07"))
        self.assertEqual(fn("2026-09-07", 5), pd.Timestamp("2026-09-11"))


class BootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = load_notebook_functions(
            ["def block_bootstrap_ci"],
            extra_globals={
                "BOOTSTRAP_B": 400, "SEED": 42, "COST_BP": 20.0,
                "PROB_COLS": ["p_down", "p_flat", "p_up"],
                "GAP": pd.Series(dtype=float), "SESSION": pd.Series(dtype=float),
            },
        )

    def test_interval_brackets_the_sample_statistic(self):
        """퍼센타일 부트스트랩 구간은 표본 통계량을 감싸야 한다."""
        block_bootstrap_ci = self.ns["block_bootstrap_ci"]
        rng = np.random.default_rng(1)
        dates = pd.bdate_range("2021-01-01", periods=800)
        values = rng.normal(0.5, 1.0, len(dates))
        sample_mean = float(values.mean())
        lo, hi = block_bootstrap_ci(dates, lambda idx: float(values[idx].mean()))
        self.assertLess(lo, sample_mean)
        self.assertGreater(hi, sample_mean)
        self.assertGreater(hi - lo, 0.0)
        self.assertLess(hi - lo, 0.6)

    def test_interval_is_wider_than_an_iid_interval(self):
        """블록 부트스트랩은 자기상관을 반영하므로 iid 가정보다 구간이 넓어야 한다."""
        block_bootstrap_ci = self.ns["block_bootstrap_ci"]
        rng = np.random.default_rng(7)
        dates = pd.bdate_range("2021-01-01", periods=800)
        # 월 단위로 공통 충격을 넣어 강한 자기상관을 만든다.
        month = pd.PeriodIndex(dates, freq="M")
        shocks = {m: rng.normal(0, 1.0) for m in month.unique()}
        values = np.array([shocks[m] for m in month]) + rng.normal(0, 0.2, len(dates))
        lo, hi = block_bootstrap_ci(dates, lambda idx: float(values[idx].mean()))
        iid_halfwidth = 1.96 * values.std(ddof=1) / np.sqrt(len(values))
        self.assertGreater((hi - lo) / 2, iid_halfwidth * 2)

    def test_interval_excludes_zero_for_a_strong_effect(self):
        block_bootstrap_ci = self.ns["block_bootstrap_ci"]
        rng = np.random.default_rng(2)
        dates = pd.bdate_range("2021-01-01", periods=800)
        values = rng.normal(2.0, 1.0, len(dates))
        lo, hi = block_bootstrap_ci(dates, lambda idx: float(values[idx].mean()))
        self.assertGreater(lo, 0.0)


if __name__ == "__main__":
    unittest.main()

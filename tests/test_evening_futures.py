# -*- coding: utf-8 -*-
"""P18 — 전날 저녁(20:00 KST) 예측에 저녁 거래 자산을 더할 수 있는가 (guides/model-improvement-plan.md P18).

이 실험의 위험은 둘이다. (1) 저녁 기준선 `t_evening` 이 저녁에 없는 해외 세션을 보는 것, (2) 선물·유럽 지수 특징이
마감 시각(전 한국 세션 날짜의 20:00 KST) 뒤의 봉을 보는 것. 둘 다 기계로 고정한다.

계약:
  1. 마감 시각에 끝나지 않은 봉(시작 ≥ 마감, 또는 마감에 걸쳐 있는 봉)은 어느 열도 바꾸지 못한다. 마감 전에 끝난 마지막
     봉을 바꾸면 행 d 가 바뀐다.
  2. 선물의 기준은 직전 미국 정규 세션 마감 봉(16:00 ET)이다. 정규 마감 뒤 18:00 부터의 야간 봉은 기준이 아니다.
  3. 유럽 지수는 그 날짜 세션 첫 봉의 시가 → 마감 전 마지막 봉의 종가다. 그 날짜에 봉이 없으면 결측이고, 가용 행에서는 0 으로 채우되 센다.
  4. 저녁 특징 행렬: 한국·달력 열은 아침 행 d, 해외·연속 열은 아침 행 d−1, 교차 열은 두 시점을 각각 쓴다. 아침 행 d 의 해외 열을
     바꿔도 저녁 행 d 는 그대로다. 분류할 수 없는 열은 예외다.
  5. 서머타임 양쪽에서 마감 시각이 맞는다(여름 20:00 KST = 07:00 EDT = 13:00 CEST, 겨울 = 06:00 EST = 12:00 CET).
  6. nq_rule 은 모델 없는 규칙이고 확률은 학습 구간에서만 만든다.
  7. 러너는 합성 입력에서 계약 파일을 남긴다 — 다섯 모델이 같은 날짜, 선물 특징 학습 행이 부족한 폴드는 제외·기록, 발행 차단.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import run_model_improvement as runner  # noqa: E402
import forecast_utils as fu  # noqa: E402
from test_overnight_intraday import _prediction_frame, _summarize, _paired_delta_ci  # noqa: E402

ET, BERLIN, PARIS, KST = "America/New_York", "Europe/Berlin", "Europe/Paris", "Asia/Seoul"


def _futures_hourly(start, end, seed=0):
    """CME 야간 선물 흉내: 일~금 18:00 ET 부터 다음 날 16:00 ET 까지 시간봉(17:00 휴장). 인덱스는 UTC 봉 시작."""
    stamps = pd.date_range(start, end, freq="1h", tz=ET)
    keep = (stamps.hour != 17) & (stamps.dayofweek < 5) | ((stamps.dayofweek == 6) & (stamps.hour >= 18))
    keep &= ~((stamps.dayofweek == 4) & (stamps.hour >= 18))
    stamps = stamps[keep]
    rng = np.random.default_rng(seed)
    close = 10000 * np.cumprod(1 + rng.normal(0, .002, len(stamps)))
    open_ = np.r_[close[0], close[:-1]]
    frame = pd.DataFrame({"open": open_, "high": np.maximum(open_, close), "low": np.minimum(open_, close),
                          "close": close, "volume": 1.}, index=stamps.tz_convert("UTC"))
    return frame


def _index_hourly(start, end, tz, seed=1, first_hour=9, last_hour=17):
    """유럽 현물 지수 흉내: 평일 09:00~17:00 현지 시간봉."""
    stamps = pd.date_range(start, end, freq="1h", tz=tz)
    stamps = stamps[(stamps.dayofweek < 5) & (stamps.hour >= first_hour) & (stamps.hour <= last_hour)]
    rng = np.random.default_rng(seed)
    close = 5000 * np.cumprod(1 + rng.normal(0, .002, len(stamps)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close), "low": np.minimum(open_, close),
                         "close": close, "volume": 0.}, index=stamps.tz_convert("UTC"))


def _hourly_set(start="2024-01-01", end="2024-12-31"):
    return {"nq": _futures_hourly(start, end, 0), "es": _futures_hourly(start, end, 2),
            "dax": _index_hourly(start, end, BERLIN, 1), "stoxx": _index_hourly(start, end, PARIS, 3)}


def _kr_calendar(start="2024-01-01", end="2024-12-31"):
    return pd.DatetimeIndex(pd.bdate_range(start, end))


def _bar_close_at(bars, when_local, tz):
    stamp = pd.Timestamp(when_local, tz=tz).tz_convert("UTC")
    return float(bars.loc[stamp, "close"])


class CutoffLeakageTests(unittest.TestCase):
    """계약 1·2·3·5 — 마감 시각 뒤의 봉은 어느 열도 바꾸지 못한다."""

    def setUp(self):
        self.hourly = _hourly_set()
        self.cal = _kr_calendar()
        self.f = runner.evening_futures_features(self.hourly, self.cal, (20, 0))
        self.summer = pd.Timestamp("2024-07-10")      # 수요일. 전 세션 07-09(화) 20:00 KST = 07:00 EDT = 13:00 CEST
        self.winter = pd.Timestamp("2024-02-14")      # 수요일. 전 세션 02-13(화) 20:00 KST = 06:00 EST = 12:00 CET

    def test_summer_cutoff_uses_the_bar_ending_at_2000_kst_and_the_previous_regular_close(self):
        nq = self.hourly["nq"]
        expected = _bar_close_at(nq, "2024-07-09 06:00", ET) / _bar_close_at(nq, "2024-07-08 16:00", ET) - 1
        self.assertAlmostEqual(self.f.loc[self.summer, "nq_ret"], expected, places=12)
        self.assertEqual(self.f.loc[self.summer, "nq_sign"], np.sign(expected))
        dax = self.hourly["dax"]
        first = pd.Timestamp("2024-07-09 09:00", tz=BERLIN).tz_convert("UTC")
        expected_dax = _bar_close_at(dax, "2024-07-09 12:00", BERLIN) / float(dax.loc[first, "open"]) - 1
        self.assertAlmostEqual(self.f.loc[self.summer, "dax_ret"], expected_dax, places=12)

    def test_winter_cutoff_shifts_with_daylight_saving(self):
        nq = self.hourly["nq"]
        expected = _bar_close_at(nq, "2024-02-13 05:00", ET) / _bar_close_at(nq, "2024-02-12 16:00", ET) - 1
        self.assertAlmostEqual(self.f.loc[self.winter, "nq_ret"], expected, places=12)
        dax = self.hourly["dax"]
        first = pd.Timestamp("2024-02-13 09:00", tz=BERLIN).tz_convert("UTC")
        expected_dax = _bar_close_at(dax, "2024-02-13 11:00", BERLIN) / float(dax.loc[first, "open"]) - 1
        self.assertAlmostEqual(self.f.loc[self.winter, "dax_ret"], expected_dax, places=12)

    def test_bars_at_or_after_the_cutoff_do_not_change_row_d(self):
        cutoff = pd.Timestamp("2024-07-09 20:00", tz=KST).tz_convert("UTC")
        bumped = {}
        for name, bars in self.hourly.items():
            b = bars.copy()
            late = b.index + pd.Timedelta(hours=1) > cutoff          # 마감 뒤에 끝나는 봉 전부(마감에 걸친 봉 포함)
            b.loc[late, ["open", "high", "low", "close"]] *= 1.5
            bumped[name] = b
        after = runner.evening_futures_features(bumped, self.cal, (20, 0))
        pd.testing.assert_series_equal(self.f.loc[self.summer], after.loc[self.summer], check_names=False)
        # 앞선 날짜도 바뀌지 않는다(미래 봉이 과거 행으로 새지 않는다).
        past = self.cal[self.cal <= self.summer]
        pd.testing.assert_frame_equal(self.f.loc[past], after.loc[past])

    def test_the_bar_straddling_the_cutoff_is_not_used_but_the_one_before_it_is(self):
        nq = self.hourly["nq"].copy()
        straddle = pd.Timestamp("2024-07-09 07:00", tz=ET).tz_convert("UTC")   # 20:00~21:00 KST 봉
        nq.loc[straddle, "close"] *= 1.2
        after = runner.evening_futures_features({**self.hourly, "nq": nq}, self.cal, (20, 0))
        self.assertEqual(self.f.loc[self.summer, "nq_ret"], after.loc[self.summer, "nq_ret"])
        nq2 = self.hourly["nq"].copy()
        last_ok = pd.Timestamp("2024-07-09 06:00", tz=ET).tz_convert("UTC")      # 19:00~20:00 KST 봉
        nq2.loc[last_ok, "close"] *= 1.2
        after2 = runner.evening_futures_features({**self.hourly, "nq": nq2}, self.cal, (20, 0))
        self.assertNotEqual(self.f.loc[self.summer, "nq_ret"], after2.loc[self.summer, "nq_ret"])

    def test_reference_is_the_regular_close_not_the_overnight_bars(self):
        nq = self.hourly["nq"].copy()
        overnight = [pd.Timestamp(f"2024-07-08 {h:02d}:00", tz=ET).tz_convert("UTC") for h in (18, 19, 20, 21, 22, 23)]
        nq.loc[overnight, "close"] *= 1.3
        after = runner.evening_futures_features({**self.hourly, "nq": nq}, self.cal, (20, 0))
        self.assertEqual(self.f.loc[self.summer, "nq_ret"], after.loc[self.summer, "nq_ret"])
        nq2 = self.hourly["nq"].copy()
        nq2.loc[pd.Timestamp("2024-07-08 16:00", tz=ET).tz_convert("UTC"), "close"] *= 1.3
        after2 = runner.evening_futures_features({**self.hourly, "nq": nq2}, self.cal, (20, 0))
        self.assertNotEqual(self.f.loc[self.summer, "nq_ret"], after2.loc[self.summer, "nq_ret"])

    def test_monday_row_uses_friday_evening_and_thursday_regular_close(self):
        monday = pd.Timestamp("2024-07-15")
        nq = self.hourly["nq"]
        expected = _bar_close_at(nq, "2024-07-12 06:00", ET) / _bar_close_at(nq, "2024-07-11 16:00", ET) - 1
        self.assertAlmostEqual(self.f.loc[monday, "nq_ret"], expected, places=12)

    def test_five_session_cumulative_uses_the_fifth_previous_regular_close(self):
        nq = self.hourly["nq"]
        expected = _bar_close_at(nq, "2024-07-09 06:00", ET) / _bar_close_at(nq, "2024-07-02 16:00", ET) - 1
        self.assertAlmostEqual(self.f.loc[self.summer, "nq_cum5"], expected, places=12)

    def test_2200_cutoff_uses_two_more_hours(self):
        late = runner.evening_futures_features(self.hourly, self.cal, (22, 0))
        nq = self.hourly["nq"]
        expected = _bar_close_at(nq, "2024-07-09 08:00", ET) / _bar_close_at(nq, "2024-07-08 16:00", ET) - 1
        self.assertAlmostEqual(late.loc[self.summer, "nq_ret"], expected, places=12)
        self.assertNotEqual(late.loc[self.summer, "nq_ret"], self.f.loc[self.summer, "nq_ret"])

    def test_first_row_and_rows_before_coverage_are_missing(self):
        self.assertTrue(self.f.iloc[0].isna().all())
        cal = _kr_calendar("2023-06-01", "2024-12-31")
        f = runner.evening_futures_features(self.hourly, cal, (20, 0))
        self.assertTrue(f.loc[pd.Timestamp("2023-09-06")].isna().all())

    def test_european_holiday_is_missing_then_filled_zero_and_counted(self):
        dax = self.hourly["dax"]
        day = pd.Timestamp("2024-07-09")
        holed = dax[dax.index.tz_convert(BERLIN).normalize() != pd.Timestamp(day, tz=BERLIN)]
        raw = runner.evening_futures_features({**self.hourly, "dax": holed}, self.cal, (20, 0))
        self.assertTrue(np.isnan(raw.loc[self.summer, "dax_ret"]))
        self.assertTrue(np.isfinite(raw.loc[self.summer, "nq_ret"]))
        filled, available, n_eu = runner.p18_fill_policy(raw)
        self.assertEqual(filled.loc[self.summer, "dax_ret"], 0.)
        self.assertTrue(available.loc[self.summer])
        self.assertEqual(n_eu, 1)
        self.assertEqual(filled.loc[self.summer, runner.P18_AVAILABLE_FLAG], 1.)
        # 선물이 없는 행은 비가용이고 전부 0 이다.
        self.assertFalse(available.iloc[0])
        self.assertTrue((filled.iloc[0] == 0.).all())


class EveningBaselineTests(unittest.TestCase):
    """계약 4 — 저녁 기준선은 해외 열을 아침 행 d−1 에서 가져온다."""

    def setUp(self):
        rng = np.random.default_rng(9)
        self.cal = _kr_calendar("2024-01-01", "2024-03-31")
        cols = ["sam_ret_1", "kospi_ret_5", "peer_vol_20", "sox_ret_1", "nasdaq_ret_5", "tsmc_adr_ret_1",
                "usdkrw_level_z60", "krwjpy_ret_1", "target_gdr_ret_1", "cal_dow", "gdr_overnight_signal"]
        self.feat = pd.DataFrame(rng.normal(size=(len(self.cal), len(cols))), index=self.cal, columns=cols)
        self.feat["gdr_overnight_signal"] = self.feat["target_gdr_ret_1"] - self.feat["sam_ret_1"]
        self.cols = cols
        self.ev, self.sessions = runner.evening_feature_frame(self.feat, cols)
        self.day = self.cal[30]
        self.prev = self.cal[29]

    def test_sessions_are_classified_by_prefix(self):
        self.assertEqual(self.sessions["sam_ret_1"], "korea")
        self.assertEqual(self.sessions["kospi_ret_5"], "korea")
        self.assertEqual(self.sessions["sox_ret_1"], "us")
        self.assertEqual(self.sessions["tsmc_adr_ret_1"], "us")
        self.assertEqual(self.sessions["usdkrw_level_z60"], "cont")
        self.assertEqual(self.sessions["krwjpy_ret_1"], "cont")
        self.assertEqual(self.sessions["target_gdr_ret_1"], "london")      # target_ 이 아니라 target_gdr_
        self.assertEqual(self.sessions["cal_dow"], "calendar")
        self.assertEqual(self.sessions["gdr_overnight_signal"], "cross")

    def test_korean_and_calendar_columns_are_row_d_and_foreign_columns_are_row_d_minus_1(self):
        for col in ("sam_ret_1", "kospi_ret_5", "peer_vol_20", "cal_dow"):
            self.assertEqual(self.ev.loc[self.day, col], self.feat.loc[self.day, col], col)
        for col in ("sox_ret_1", "nasdaq_ret_5", "tsmc_adr_ret_1", "usdkrw_level_z60", "krwjpy_ret_1", "target_gdr_ret_1"):
            self.assertEqual(self.ev.loc[self.day, col], self.feat.loc[self.prev, col], col)
        expected = self.feat.loc[self.prev, "target_gdr_ret_1"] - self.feat.loc[self.day, "sam_ret_1"]
        self.assertAlmostEqual(self.ev.loc[self.day, "gdr_overnight_signal"], expected, places=12)
        self.assertTrue(self.ev.iloc[0][["sox_ret_1", "gdr_overnight_signal"]].isna().all())
        self.assertTrue(np.isfinite(self.ev.iloc[0]["sam_ret_1"]))

    def test_changing_todays_foreign_row_does_not_change_the_evening_row(self):
        bumped = self.feat.copy()
        foreign = ["sox_ret_1", "nasdaq_ret_5", "tsmc_adr_ret_1", "usdkrw_level_z60", "krwjpy_ret_1", "target_gdr_ret_1"]
        bumped.loc[self.day, foreign] += 5.
        after, _ = runner.evening_feature_frame(bumped, self.cols)
        pd.testing.assert_series_equal(self.ev.loc[self.day], after.loc[self.day], check_names=False)
        bumped2 = self.feat.copy()
        bumped2.loc[self.prev, foreign] += 5.
        after2, _ = runner.evening_feature_frame(bumped2, self.cols)
        for col in foreign:
            self.assertNotEqual(self.ev.loc[self.day, col], after2.loc[self.day, col], col)

    def test_unknown_column_raises_instead_of_being_treated_as_korean(self):
        with self.assertRaises(ValueError):
            runner.evening_column_sessions(["mystery_ret_1"])

    def test_notebook_asset_session_overrides_the_default_map(self):
        sessions = runner.evening_column_sessions(["vix_ret_1"], {"vix": "us"})
        self.assertEqual(sessions["vix_ret_1"], "us")


class FuturesRuleTests(unittest.TestCase):
    """계약 6."""

    def test_rule_reads_the_sign_against_the_band(self):
        out = fu.gap_rule_labels(np.array([.01, -.01, .001, np.nan]), np.array([.005] * 4))
        np.testing.assert_array_equal(out[:3], [2., 0., 1.])
        self.assertTrue(np.isnan(out[3]))

    def test_probabilities_come_from_training_rows_only(self):
        rng = np.random.default_rng(3)
        rule_train, y_train = rng.integers(0, 3, 300), rng.integers(0, 3, 300)
        a, _ = runner.gap_rule_probabilities(rule_train, y_train, np.array([0, 1, 2]))
        b, _ = runner.gap_rule_probabilities(rule_train, y_train, np.array([0, 1, 2]))
        np.testing.assert_array_equal(a, b)
        np.testing.assert_allclose(a.sum(axis=1), 1.)


class FakeEveningNotebook:
    """러너가 읽는 이름만 흉내 낸다. 시세 봉·밴드·특징 프레임·폴드. 시간봉은 달력 뒷부분만 덮는다."""

    def __init__(self, n=1100, seed=5):
        rng = np.random.default_rng(seed)
        close = 100 * np.cumprod(1 + rng.normal(0, .012, n))
        open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, .006, n))
        idx = pd.DatetimeIndex(pd.bdate_range("2021-01-04", periods=n))
        bars = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.01, "low": np.minimum(open_, close) * .99,
                             "close": close, "adj_close": close, "volume": rng.integers(1_000, 9_000, n).astype(float)}, index=idx)
        pred_date = idx[-1] + pd.offsets.BDay(1)
        calendar = idx.union(pd.DatetimeIndex([pred_date]))
        ret = bars["close"].pct_change()
        band = (.3 * ret.rolling(20).std()).shift(1)
        y = np.where(ret < -band, 0, np.where(ret > band, 2, 1)).astype(float)
        y[~(ret.notna() & band.notna()).to_numpy()] = np.nan
        feat = pd.DataFrame(index=calendar)
        for name in ("sox_ret_1", "nasdaq_ret_1", "usdkrw_level_z60", "kospi_ret_1", "sam_ret_1", "macro_lead", "target_gdr_ret_1"):
            feat[name] = rng.normal(0, 1, len(feat))
        feat["gdr_overnight_signal"] = feat["target_gdr_ret_1"] - feat["sam_ret_1"]
        feat["cal_dow"] = feat.index.dayofweek.astype(float)
        feat["band"] = band.reindex(calendar)
        self.feature_cols = [c for c in feat.columns if c != "band"]
        self.feat = feat
        self.market_idx = [i for i, c in enumerate(self.feature_cols) if not c.startswith("macro_")]
        keep = np.isfinite(y)
        self.dates = pd.DatetimeIndex(idx[keep])
        self.y = y[keep].astype(int)
        self.X = feat.loc[self.dates, self.feature_cols].to_numpy(dtype=np.float32)
        self.market_X = self.X[:, self.market_idx]
        self.raw = {"target": bars}
        self.calendar = calendar
        folds = []
        for k, start in enumerate((pd.Timestamp("2023-07-01"), pd.Timestamp("2024-01-01"), pd.Timestamp("2024-07-01"))):
            end = start + pd.DateOffset(months=6)
            tr = np.flatnonzero((self.dates >= start - pd.DateOffset(years=5)) & (self.dates < start))
            te = np.flatnonzero((self.dates >= start) & (self.dates < end))
            folds.append({"fold": k, "train_idx": tr, "test_idx": te})
        self.folds = folds

    def __call__(self, storage, targets=None, quick=False, use_cache=False, no_macro=False):
        ns = {"folds": self.folds, "dates": self.dates, "y": self.y, "SEED": 42, "SELECTION_METRIC": "log_loss",
              "HEADLINE_MODEL": "No macro ensemble", "feat": self.feat, "feature_cols": self.feature_cols,
              "market_feature_idx": self.market_idx, "market_X": self.market_X, "raw": self.raw,
              "ASSET_SESSION": dict(runner.P18_DEFAULT_ASSET_SESSION),
              "fit_direction_model": fu.fit_direction_model, "predict_direction_model": fu.predict_direction_model,
              "prediction_frame": _prediction_frame, "summarize_predictions": _summarize, "paired_delta_ci": _paired_delta_ci}
        return {targets: ns}


class EveningRunnerTests(unittest.TestCase):
    """계약 7 — 러너 종단. 성능 수치는 합성 입력이라 어떤 주장에도 쓰지 않는다."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.storage = Path(cls.tmp.name)
        cache = cls.storage / "samsung" / "data_cache"
        cache.mkdir(parents=True)
        (cache / "target.parquet").write_bytes(b"snapshot-v1")
        cls.fake = FakeEveningNotebook()
        # 시간봉은 2023-06 부터만 있다 → 첫 폴드(2023-07 시작)는 학습 구간에 선물 특징 가용 행이 100 미만이라 제외돼야 한다.
        hourly = _hourly_set("2023-06-01", "2025-03-31")
        ledger_root = cls.storage / "ledger"
        (ledger_root / "samsung").mkdir(parents=True)
        pd.DataFrame({"model": ["Candidate evening forecast", "Candidate evening forecast", "No macro ensemble"],
                      "kind": ["direction", "direction", "direction"], "created_at_utc": ["2024-03-04T11:00", "2024-03-05T11:00", "2024-03-05T22:00"],
                      "target_date": ["2024-03-05", "2024-03-06", "2024-03-06"], "prediction": ["상승", "하락", "보합"]}
                     ).to_csv(ledger_root / "samsung" / "forecast_log.csv", index=False, encoding="utf-8-sig")
        cls.saved = (runner.load_hourly_bars, runner.ensure_hourly_cache, runner.P18_LEDGER_ROOT)
        runner.load_hourly_bars = lambda storage: hourly
        runner.ensure_hourly_cache = lambda storage, period=None: cls.storage / "P18" / "cache"
        runner.P18_LEDGER_ROOT = ledger_root
        os.environ["PREDICT_STOCK_PUBLISH"] = "true"
        cls.state = runner.execute("P18", "samsung", "quick", cls.storage, resume=False, run_notebook_fn=cls.fake)
        cls.detail = runner.read_json(cls.state.run_dir / "p18_samsung.json")

    @classmethod
    def tearDownClass(cls):
        runner.load_hourly_bars, runner.ensure_hourly_cache, runner.P18_LEDGER_ROOT = cls.saved
        cls.tmp.cleanup()

    def test_all_five_models_are_scored_on_the_same_dates(self):
        metrics = pd.read_csv(self.state.run_dir / "metrics.csv")
        expected = {runner.P18_T0700, runner.P18_T_EVENING, runner.P18_T_EVENING_F, runner.P18_T_EVENING_F22, runner.P18_NQ_RULE}
        self.assertEqual(set(metrics["model"]), expected)
        self.assertEqual(metrics["n"].nunique(), 1, "모델마다 평가일이 다르면 쌍체 비교가 아니다")
        self.assertEqual(int(metrics["n"].iloc[0]), self.detail["evaluation_days"])
        self.assertEqual(self.detail["n_features"][runner.P18_T_EVENING], self.detail["n_features"][runner.P18_T0700])
        self.assertEqual(self.detail["n_features"][runner.P18_T_EVENING_F],
                         self.detail["n_features"][runner.P18_T_EVENING] + len(runner.P18_FUTURES_COLUMNS) + 1)

    def test_folds_without_enough_futures_training_rows_are_skipped_and_recorded(self):
        skipped = {s["fold"]: s for s in self.detail["skipped_folds"]}
        self.assertIn(0, skipped)
        self.assertIn("선물 특징 가용 행", skipped[0]["reason"])
        used = [f["fold"] for f in self.detail["folds"]]
        self.assertTrue(used)
        self.assertNotIn(0, used)
        for f in self.detail["folds"]:
            self.assertGreaterEqual(f["train_rows_with_futures"], runner.P18_MIN_TRAIN_F_ROWS)
        self.assertEqual(self.detail["evaluation_first"] >= "2024-01-01", True)

    def test_comparisons_cover_the_declared_pairs_with_leg_auc(self):
        comparisons = pd.read_csv(self.state.run_dir / "comparisons.csv")
        for label in (f"{runner.P18_T_EVENING_F} − {runner.P18_T_EVENING}", f"{runner.P18_T_EVENING_F} − {runner.P18_T0700}",
                      f"{runner.P18_T_EVENING} − {runner.P18_T0700}", f"{runner.P18_NQ_RULE} − {runner.P18_T_EVENING}",
                      f"{runner.P18_T_EVENING_F22} − {runner.P18_T_EVENING}"):
            sub = comparisons[comparisons["comparison"] == label]
            self.assertEqual(set(sub["metric"]), {"log_loss", "balanced_accuracy", "accuracy", "auc_gap", "auc_session"}, label)
            self.assertTrue((sub["ci_low"] <= sub["delta"]).all() and (sub["delta"] <= sub["ci_high"]).all(), label)

    def test_ledger_check_reads_only_the_evening_candidate_rows(self):
        ledger = self.detail["ledger_check"]
        self.assertTrue(ledger["available"])
        self.assertEqual(ledger["ledger_days"], 2)
        self.assertEqual(ledger["overlap_days"], 2)
        by_date = {r["target_date"]: r for r in ledger["rows"]}
        self.assertEqual(by_date["2024-03-05"]["ledger_class"], 2)
        self.assertEqual(by_date["2024-03-06"]["ledger_class"], 0)       # 대표 모델의 '보합' 행은 읽지 않는다
        self.assertIn(f"{runner.P18_T_EVENING}_pred", by_date["2024-03-05"])

    def test_session_map_rebuild_check_and_publishing_guard(self):
        self.assertEqual(self.detail["column_sessions"]["sox_ret_1"], "us")
        self.assertEqual(self.detail["column_sessions"]["gdr_overnight_signal"], "cross")
        self.assertLess(self.detail["market_x_rebuild_max_abs_diff"], 1e-5)
        self.assertEqual(os.environ.get("PREDICT_STOCK_PUBLISH"), "false")
        self.assertEqual(self.state.completed(), ["samsung:evening_futures"])
        self.assertEqual(self.state.manifest["status"], "completed")
        self.assertEqual(self.state.manifest["config"]["reference"], runner.P18_T_EVENING)
        self.assertEqual(self.state.manifest["config"]["cutoffs_kst"], {"f2000": "20:00", "f2200": "22:00"})


if __name__ == "__main__":
    unittest.main()

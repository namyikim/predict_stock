# -*- coding: utf-8 -*-
"""S01 — 누수 없는 주간(5거래일) OHLCV 시퀀스와 날짜 계약.

계획: guides/weekly-sequence-s01-implementation.md
합성 데이터로 코드 동작만 검증한다. 이 테스트 통과는 실제 예측 성능에 대해 아무것도 말하지 않는다.
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from weekly_sequence_utils import CHANNELS, build_sequences  # noqa: E402

KST = "Asia/Seoul"
LOOKBACK, HORIZON, WARMUP = 60, 5, 20


def krx_sessions(n=160):
    import exchange_calendars as xc
    sessions = xc.get_calendar("XKRX").sessions_in_range("2025-01-01", "2025-12-31")[:n]
    return pd.DatetimeIndex(sessions).tz_localize(None)


def fixture(n=160):
    """계획서 fixture: 2025년 KRX 거래일 160개, 양의 증가 종가, open=close, high=close*1.01,
    low=close*0.99, volume=1000, 공개 16:00, 예측 07:00, 기업행동 없음."""
    sessions = krx_sessions(n)
    close = pd.Series(np.linspace(50000.0, 66000.0, n), index=sessions)
    bars = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": 1000.0}, index=sessions)
    available_at = pd.Series(sessions + pd.Timedelta(hours=16), index=sessions).dt.tz_localize(KST)
    prediction_at = pd.Series(sessions + pd.Timedelta(hours=7), index=sessions).dt.tz_localize(KST)
    corporate_actions = pd.Series(False, index=sessions)
    return {"bars": bars, "sessions": sessions, "available_at": available_at,
            "prediction_at": prediction_at, "corporate_actions": corporate_actions}


def excluded_reason(batch, date):
    row = batch.excluded[batch.excluded["prediction_date"] == pd.Timestamp(date)]
    return None if row.empty else row["reason"].iloc[0]


def sample_index(batch, date):
    hits = np.flatnonzero(batch.metadata["prediction_date"].to_numpy() == np.datetime64(pd.Timestamp(date)))
    return int(hits[0]) if len(hits) else None


class TargetContractTests(unittest.TestCase):
    def setUp(self):
        self.inputs = fixture()

    def test_target_matches_existing_five_session_definition(self):
        batch = build_sequences(**self.inputs, lookback=LOOKBACK, horizon=HORIZON)
        row = batch.metadata.iloc[0]
        sessions = self.inputs["sessions"]
        close = self.inputs["bars"].close
        d = sessions.get_loc(row.prediction_date)
        self.assertEqual(row.origin_date, sessions[d - 1])
        self.assertEqual(row.target_date, sessions[d + 4])
        self.assertAlmostEqual(batch.y[0], close.loc[sessions[d + 4]] / close.loc[sessions[d - 1]] - 1)
        self.assertEqual(batch.X.shape[1:], (LOOKBACK, len(CHANNELS)))

    def test_target_equals_the_operational_price_model_target(self):
        """운영 가격 모델(forecast_utils.price_design_frame)의 future_return 과 같은 값이어야 한다."""
        close = self.inputs["bars"].close
        operational = close.shift(-(HORIZON - 1)) / close.shift(1) - 1
        batch = build_sequences(**self.inputs, lookback=LOOKBACK, horizon=HORIZON)
        expected = operational.reindex(batch.metadata["prediction_date"]).to_numpy()
        np.testing.assert_allclose(batch.y, expected, rtol=0, atol=1e-12)

    def test_sample_counts_follow_warmup_and_pending_rules(self):
        batch = build_sequences(**self.inputs, lookback=LOOKBACK, horizon=HORIZON)
        # 첫 표본은 d = lookback + warmup = 80, 마지막은 d + 4 <= 159 → d <= 155.
        self.assertEqual(len(batch.y), 155 - 80 + 1)
        counts = batch.excluded["reason"].value_counts().to_dict()
        self.assertEqual(counts.get("insufficient_history"), 80)
        self.assertEqual(counts.get("pending_target"), 4)
        self.assertEqual(len(batch.y) + len(batch.excluded), 160)

    def test_output_types_and_metadata_columns(self):
        batch = build_sequences(**self.inputs, lookback=LOOKBACK, horizon=HORIZON)
        self.assertEqual(batch.X.dtype, np.float32)
        self.assertEqual(batch.y.dtype, np.float64)
        for column in ("origin_date", "prediction_date", "target_date", "available_at",
                       "label_available_at", "prediction_at", "current_close"):
            self.assertIn(column, batch.metadata.columns)
        self.assertTrue(np.isfinite(batch.X).all())

    def test_channels_are_the_documented_causal_ratios(self):
        batch = build_sequences(**self.inputs, lookback=LOOKBACK, horizon=HORIZON)
        self.assertEqual(CHANNELS, ("close_return", "open_gap", "high_relative", "low_relative",
                                    "volume_relative"))
        bars = self.inputs["bars"]
        sessions = self.inputs["sessions"]
        d = sessions.get_loc(batch.metadata["prediction_date"].iloc[0])
        last = sessions[d - 1]
        prev = sessions[d - 2]
        x_last = batch.X[0, -1]
        self.assertAlmostEqual(float(x_last[0]), bars.close[last] / bars.close[prev] - 1, places=6)
        self.assertAlmostEqual(float(x_last[2]), bars.high[last] / bars.close[prev] - 1, places=6)
        self.assertAlmostEqual(float(x_last[4]), 0.0, places=6)          # 거래량 일정 → 평균 대비 0

    def test_no_samples_keeps_the_array_shape(self):
        inputs = fixture(n=70)                                             # 워밍업+입력에 못 미친다
        batch = build_sequences(**inputs, lookback=LOOKBACK, horizon=HORIZON)
        self.assertEqual(batch.X.shape, (0, LOOKBACK, len(CHANNELS)))
        self.assertEqual(batch.y.shape, (0,))


class LeakageTests(unittest.TestCase):
    def setUp(self):
        self.inputs = fixture()
        self.batch = build_sequences(**self.inputs, lookback=LOOKBACK, horizon=HORIZON)
        self.first = self.batch.metadata.iloc[0]

    def test_changing_data_from_the_prediction_date_on_leaves_x_unchanged(self):
        changed = fixture()
        later = changed["bars"].index >= self.first.prediction_date
        for column in ("open", "high", "low", "close", "volume"):
            changed["bars"].loc[later, column] *= 1.5
        after = build_sequences(**changed, lookback=LOOKBACK, horizon=HORIZON)
        np.testing.assert_array_equal(self.batch.X[0], after.X[0])
        self.assertNotEqual(self.batch.y[0], after.y[0])

    def test_changing_data_after_maturity_leaves_x_and_y_unchanged(self):
        changed = fixture()
        later = changed["bars"].index > self.first.target_date
        for column in ("open", "high", "low", "close"):
            changed["bars"].loc[later, column] *= 1.5
        after = build_sequences(**changed, lookback=LOOKBACK, horizon=HORIZON)
        np.testing.assert_array_equal(self.batch.X[0], after.X[0])
        self.assertEqual(self.batch.y[0], after.y[0])

    def test_input_published_after_prediction_time_is_excluded(self):
        changed = fixture()
        origin = self.first.origin_date
        changed["available_at"].loc[origin] = self.first.prediction_at + pd.Timedelta(hours=1)
        after = build_sequences(**changed, lookback=LOOKBACK, horizon=HORIZON)
        self.assertEqual(excluded_reason(after, self.first.prediction_date), "unavailable_input")

    def test_every_sample_respects_the_time_order(self):
        meta = self.batch.metadata
        self.assertTrue((meta["available_at"] < meta["prediction_at"]).all())
        self.assertTrue((meta["origin_date"] < meta["prediction_date"]).all())
        self.assertTrue((meta["prediction_date"] <= meta["target_date"]).all())


class CalendarAndMissingBarTests(unittest.TestCase):
    def test_holiday_crossing_target_uses_session_order_not_business_days(self):
        inputs = fixture()
        batch = build_sequences(**inputs, lookback=LOOKBACK, horizon=HORIZON)
        # 2025-05-02(금) 예측 → 5/5·5/6 휴장. 세션 d+4 는 5/12, 단순 영업일 +4 는 5/8 이다.
        i = sample_index(batch, "2025-05-02")
        self.assertIsNotNone(i)
        self.assertEqual(batch.metadata["target_date"].iloc[i], pd.Timestamp("2025-05-12"))
        naive = pd.Timestamp("2025-05-02") + pd.offsets.BDay(4)
        self.assertNotEqual(batch.metadata["target_date"].iloc[i], naive)

    def test_missing_bar_in_the_input_excludes_instead_of_shifting(self):
        inputs = fixture()
        sessions = inputs["sessions"]
        target = sessions[100]                                    # 이 날짜의 입력 중간
        dropped = sessions[70]
        for key in ("bars", "available_at", "corporate_actions"):
            inputs[key] = inputs[key].drop(dropped)
        batch = build_sequences(**inputs, lookback=LOOKBACK, horizon=HORIZON)
        self.assertEqual(excluded_reason(batch, target), "missing_bar")
        self.assertIsNone(sample_index(batch, target))

    def test_samples_that_do_not_touch_the_gap_are_unchanged(self):
        base = build_sequences(**fixture(), lookback=LOOKBACK, horizon=HORIZON)
        inputs = fixture()
        dropped = inputs["sessions"][70]
        for key in ("bars", "available_at", "corporate_actions"):
            inputs[key] = inputs[key].drop(dropped)
        after = build_sequences(**inputs, lookback=LOOKBACK, horizon=HORIZON)
        # d-80 > 70 인 표본(d >= 151)은 결측 봉을 전혀 쓰지 않는다.
        date = inputs["sessions"][152]
        np.testing.assert_array_equal(base.X[sample_index(base, date)], after.X[sample_index(after, date)])

    def test_missing_target_bar_is_missing_not_pending(self):
        inputs = fixture()
        sessions = inputs["sessions"]
        dropped = sessions[120]
        for key in ("bars", "available_at", "corporate_actions"):
            inputs[key] = inputs[key].drop(dropped)
        batch = build_sequences(**inputs, lookback=LOOKBACK, horizon=HORIZON)
        self.assertEqual(excluded_reason(batch, sessions[116]), "missing_bar")   # 만기가 120

    def test_zero_volume_history_is_excluded(self):
        inputs = fixture()
        sessions = inputs["sessions"]
        inputs["bars"].loc[sessions[90:110], "volume"] = 0.0      # 110 행의 과거 20일 평균 = 0
        batch = build_sequences(**inputs, lookback=LOOKBACK, horizon=HORIZON)
        self.assertEqual(excluded_reason(batch, sessions[120]), "zero_volume_mean")


class CorporateActionTests(unittest.TestCase):
    def check(self, flagged_position, prediction_position):
        inputs = fixture()
        sessions = inputs["sessions"]
        inputs["corporate_actions"].loc[sessions[flagged_position]] = True
        batch = build_sequences(**inputs, lookback=LOOKBACK, horizon=HORIZON)
        return excluded_reason(batch, sessions[prediction_position])

    def test_action_inside_the_input_window(self):
        self.assertEqual(self.check(flagged_position=100, prediction_position=120), "corporate_action")

    def test_action_inside_the_maturity_window(self):
        self.assertEqual(self.check(flagged_position=122, prediction_position=120), "corporate_action")

    def test_action_inside_the_volume_warmup(self):
        # d=120 의 워밍업은 40..59. 워밍업의 분할도 거래량 비율을 오염시킨다.
        self.assertEqual(self.check(flagged_position=45, prediction_position=120), "corporate_action")

    def test_action_outside_the_whole_range_does_not_exclude(self):
        self.assertIsNone(self.check(flagged_position=30, prediction_position=120))

    def test_split_like_raw_prices_are_excluded_when_flagged(self):
        inputs = fixture()
        sessions = inputs["sessions"]
        split = sessions[100]
        after = inputs["bars"].index >= split
        for column in ("open", "high", "low", "close"):
            inputs["bars"].loc[after, column] /= 2.0
        inputs["bars"].loc[after, "volume"] *= 2.0
        inputs["corporate_actions"].loc[split] = True
        batch = build_sequences(**inputs, lookback=LOOKBACK, horizon=HORIZON)
        for position in (100, 110, 130, 150):
            self.assertEqual(excluded_reason(batch, sessions[position]), "corporate_action")
        self.assertTrue(np.isfinite(batch.X).all())


class InputValidationTests(unittest.TestCase):
    """입력 오류는 조용히 넘기지 않고 ValueError 로 거부한다. 메시지로 '올바른 이유'로 거부됐는지 본다."""

    def assertRejects(self, mutate, message, **kwargs):
        inputs = fixture()
        mutate(inputs)
        with self.assertRaisesRegex(ValueError, message):
            build_sequences(**inputs, **{"lookback": LOOKBACK, "horizon": HORIZON, **kwargs})

    def test_nan_and_inf_prices(self):
        def nan_close(i):
            i["bars"].loc[i["sessions"][50], "close"] = np.nan
        def inf_high(i):
            i["bars"].loc[i["sessions"][50], "high"] = np.inf
        self.assertRejects(nan_close, "무한대|숫자가 아니")
        self.assertRejects(inf_high, "무한대|숫자가 아니")

    def test_non_positive_price_and_negative_volume(self):
        def zero_price(i):
            i["bars"].loc[i["sessions"][50], ["open", "low", "close"]] = 0.0
        def negative_volume(i):
            i["bars"].loc[i["sessions"][50], "volume"] = -1.0
        self.assertRejects(zero_price, "양수")
        self.assertRejects(negative_volume, "음수")

    def test_inconsistent_high_low(self):
        def bad(i):
            i["bars"].loc[i["sessions"][50], "high"] = i["bars"].loc[i["sessions"][50], "low"] * 0.5
        self.assertRejects(bad, "고가·저가")

    def test_duplicate_or_unsorted_dates(self):
        def duplicate(i):
            i["bars"] = pd.concat([i["bars"], i["bars"].iloc[[10]]])
        def unsorted(i):
            i["bars"] = i["bars"].iloc[::-1]
        self.assertRejects(duplicate, "중복")
        self.assertRejects(unsorted, "정렬")

    def test_bar_on_a_non_session_day(self):
        def weekend(i):
            extra = i["bars"].iloc[[0]].copy()
            extra.index = pd.DatetimeIndex([pd.Timestamp("2025-01-04")])      # 토요일
            i["bars"] = pd.concat([i["bars"], extra]).sort_index()
            i["available_at"] = pd.concat([i["available_at"], pd.Series(
                [pd.Timestamp("2025-01-04 16:00", tz=KST)], index=extra.index)]).sort_index()
            i["corporate_actions"] = pd.concat([i["corporate_actions"],
                                                pd.Series([False], index=extra.index)]).sort_index()
        self.assertRejects(weekend, "세션 달력에 없는")

    def test_naive_timestamps(self):
        def naive_available(i):
            i["available_at"] = i["available_at"].dt.tz_localize(None)
        def naive_prediction(i):
            i["prediction_at"] = i["prediction_at"].dt.tz_localize(None)
        self.assertRejects(naive_available, "시간대")
        self.assertRejects(naive_prediction, "시간대")

    def test_bad_lookback_and_horizon(self):
        for bad in (True, 0, -1, 60.0):
            self.assertRejects(lambda i: None, "lookback", lookback=bad)
        for bad in (False, 0, -5, 5.0):
            self.assertRejects(lambda i: None, "horizon", horizon=bad)

    def test_corporate_actions_must_be_boolean_and_aligned(self):
        def as_int(i):
            i["corporate_actions"] = i["corporate_actions"].astype(int)
        def short(i):
            i["corporate_actions"] = i["corporate_actions"].iloc[:-1]
        self.assertRejects(as_int, "bool")
        self.assertRejects(short, "날짜가 bars 와")


if __name__ == "__main__":
    unittest.main()


# ----------------------------------------------------------------------------------------------
# S02 Task 1: 고정 스냅샷 로더
# ----------------------------------------------------------------------------------------------
import tempfile  # noqa: E402

from weekly_sequence_utils import (  # noqa: E402
    OhlcvSnapshot, availability_policy, corporate_action_flags, load_ohlcv_snapshot, session_calendar,
)


def write_snapshot(cache_dir, bars, adj_close=None, fmt="csv"):
    """노트북 load_raw 와 같은 형식(open/high/low/close/adj_close/volume, tz 없는 날짜 인덱스)."""
    frame = bars[["open", "high", "low", "close"]].copy()
    frame["adj_close"] = bars["close"] if adj_close is None else adj_close
    frame["volume"] = bars["volume"]
    frame = frame.round(6)
    path = Path(cache_dir) / f"target.{fmt}"
    if fmt == "csv":
        frame.to_csv(path)
    else:
        frame.to_parquet(path)
    return path


class SnapshotLoaderTests(unittest.TestCase):
    def setUp(self):
        self.cache = Path(tempfile.mkdtemp())
        self.inputs = fixture(n=160)

    def test_missing_snapshot_is_refused_not_downloaded(self):
        with self.assertRaises(FileNotFoundError):
            load_ohlcv_snapshot(self.cache, "samsung")

    def test_loader_feeds_build_sequences_unchanged(self):
        write_snapshot(self.cache, self.inputs["bars"])
        snap = load_ohlcv_snapshot(self.cache, "samsung")
        self.assertIsInstance(snap, OhlcvSnapshot)
        self.assertEqual(list(snap.bars.columns), ["open", "high", "low", "close", "volume"])
        self.assertFalse(snap.adjusted)
        self.assertEqual(snap.ticker, "005930.KS")
        sessions = session_calendar(snap.bars.index[0], snap.bars.index[-1])
        available_at, prediction_at = availability_policy(snap.bars, sessions)
        batch = build_sequences(snap.bars, sessions, available_at, prediction_at,
                                corporate_action_flags(snap.bars), lookback=LOOKBACK, horizon=HORIZON)
        reference = build_sequences(**self.inputs, lookback=LOOKBACK, horizon=HORIZON)
        np.testing.assert_allclose(batch.X, reference.X, rtol=0, atol=1e-6)
        np.testing.assert_allclose(batch.y, reference.y, rtol=0, atol=1e-9)

    def test_raw_close_is_used_even_when_adj_close_differs(self):
        # 조정 종가와 비조정 OHLC 를 섞지 않는다: close(원본)를 쓰고 adjusted=False 로 기록한다.
        write_snapshot(self.cache, self.inputs["bars"], adj_close=self.inputs["bars"]["close"] * 0.9)
        snap = load_ohlcv_snapshot(self.cache, "samsung")
        np.testing.assert_allclose(snap.bars["close"].to_numpy(),
                                   self.inputs["bars"]["close"].round(6).to_numpy())
        self.assertFalse(snap.adjusted)

    def test_adjusted_ohlc_marker_is_rejected(self):
        path = write_snapshot(self.cache, self.inputs["bars"])
        frame = pd.read_csv(path, index_col=0)
        frame.attrs = {}
        frame["adjusted"] = True                      # 조정된 OHLC 표시 열
        frame.to_csv(path)
        with self.assertRaisesRegex(ValueError, "조정"):
            load_ohlcv_snapshot(self.cache, "samsung")

    def test_sha256_changes_with_content(self):
        path = write_snapshot(self.cache, self.inputs["bars"])
        first = load_ohlcv_snapshot(self.cache, "samsung").sha256
        frame = pd.read_csv(path, index_col=0)
        frame.iloc[-1, frame.columns.get_loc("close")] += 1.0
        frame.to_csv(path)
        second = load_ohlcv_snapshot(self.cache, "samsung").sha256
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_parquet_and_csv_give_the_same_bars(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow 없음")
        write_snapshot(self.cache, self.inputs["bars"], fmt="parquet")
        a = load_ohlcv_snapshot(self.cache, "samsung").bars
        other = Path(tempfile.mkdtemp())
        write_snapshot(other, self.inputs["bars"], fmt="csv")
        b = load_ohlcv_snapshot(other, "samsung").bars
        pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=0, atol=1e-6)


class AvailabilityPolicyTests(unittest.TestCase):
    def test_policy_times_are_aware_and_ordered(self):
        bars = fixture(n=40)["bars"]
        sessions = session_calendar(bars.index[0], bars.index[-1])
        available_at, prediction_at = availability_policy(bars, sessions)
        self.assertEqual(str(available_at.dt.tz), "Asia/Seoul")
        self.assertEqual(str(prediction_at.dt.tz), "Asia/Seoul")
        self.assertTrue((available_at.dt.hour == 16).all())
        self.assertTrue((prediction_at.dt.hour == 7).all())
        # 각 봉의 공개 시각은 다음 세션의 예측 시각보다 앞선다.
        nxt = prediction_at.shift(-1).dropna()
        self.assertTrue((available_at.loc[nxt.index] < nxt).all())

    def test_recorded_fetch_time_takes_precedence_over_policy(self):
        bars = fixture(n=40)["bars"].copy()
        sessions = session_calendar(bars.index[0], bars.index[-1])
        recorded = pd.Series(bars.index + pd.Timedelta(hours=18, minutes=30), index=bars.index).dt.tz_localize(KST)
        available_at, _ = availability_policy(bars, sessions, recorded_at=recorded)
        self.assertTrue((available_at == recorded).all())

    def test_policy_marks_itself_assumed(self):
        bars = fixture(n=40)["bars"]
        sessions = session_calendar(bars.index[0], bars.index[-1])
        available_at, _ = availability_policy(bars, sessions)
        self.assertEqual(available_at.attrs.get("policy"), "assumed")
        recorded = pd.Series(bars.index + pd.Timedelta(hours=18), index=bars.index).dt.tz_localize(KST)
        available_at, _ = availability_policy(bars, sessions, recorded_at=recorded)
        self.assertEqual(available_at.attrs.get("policy"), "recorded")


class CorporateActionHeuristicTests(unittest.TestCase):
    def test_split_like_gap_is_flagged(self):
        bars = fixture(n=100)["bars"].copy()
        split = bars.index[50]
        after = bars.index >= split
        for column in ("open", "high", "low", "close"):
            bars.loc[after, column] /= 2.0
        flags = corporate_action_flags(bars)
        self.assertTrue(flags.loc[split])
        self.assertEqual(int(flags.sum()), 1)

    def test_ordinary_moves_are_not_flagged(self):
        bars = fixture(n=100)["bars"].copy()
        day = bars.index[50]
        for column in ("open", "high", "low", "close"):
            bars.loc[day, column] *= 1.10          # +10% 는 흔한 급등
        self.assertFalse(corporate_action_flags(bars).any())

    def test_heuristic_is_labelled_as_such(self):
        flags = corporate_action_flags(fixture(n=40)["bars"])
        self.assertEqual(flags.attrs.get("source"), "heuristic")
        self.assertEqual(flags.dtype, bool)


# ----------------------------------------------------------------------------------------------
# S02 Task 2: 공통 날짜 기준선
# ----------------------------------------------------------------------------------------------
from weekly_sequence_utils import (  # noqa: E402
    common_dates, flatten_windows, persistence_baseline, ridge_baseline, score, walk_forward_folds,
)


def long_fixture(n=700, seed=0):
    """2023~2025 KRX 세션 ~700개, 무작위 보행 종가. 폴드 테스트용(합성 — 성능 의미 없음)."""
    import exchange_calendars as xc
    sessions = pd.DatetimeIndex(xc.get_calendar("XKRX").sessions_in_range("2023-01-01", "2025-12-31")[:n]).tz_localize(None)
    rng = np.random.default_rng(seed)
    close = pd.Series(60000 * np.exp(np.cumsum(rng.normal(0, 0.015, len(sessions)))), index=sessions)
    bars = pd.DataFrame({"open": close * (1 + rng.normal(0, 0.003, len(sessions))),
                         "close": close, "volume": rng.integers(500, 2000, len(sessions)).astype(float)},
                        index=sessions)
    bars["high"] = bars[["open", "close"]].max(axis=1) * 1.01
    bars["low"] = bars[["open", "close"]].min(axis=1) * 0.99
    available_at = pd.Series(sessions + pd.Timedelta(hours=16), index=sessions).dt.tz_localize(KST)
    prediction_at = pd.Series(sessions + pd.Timedelta(hours=7), index=sessions).dt.tz_localize(KST)
    return {"bars": bars, "sessions": sessions, "available_at": available_at,
            "prediction_at": prediction_at, "corporate_actions": pd.Series(False, index=sessions)}


class FoldContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = long_fixture()
        cls.batch = build_sequences(**cls.inputs, lookback=LOOKBACK, horizon=HORIZON)
        cls.folds, cls.lock_start = walk_forward_folds(cls.batch.metadata, first_test="2024-01-01",
                                                       test_months=6, lock_months=12, min_train_rows=100)

    def test_purge_zero_violations_in_every_fold(self):
        meta = self.batch.metadata
        for fold in self.folds:
            if fold.get("excluded") or not len(fold["test"]):
                continue
            test_start = meta["prediction_date"].iloc[fold["test"][0]]
            train_maturity = meta["target_date"].iloc[fold["train"]]
            self.assertEqual(int((train_maturity >= test_start).sum()), 0, fold["name"])

    def test_locked_period_is_excluded_from_development(self):
        names = [f["name"] for f in self.folds]
        self.assertTrue(all(n.startswith("dev_") for n in names))          # S02 는 잠금 폴드를 만들지 않는다
        last_test_end = max(pd.Timestamp(f["test_end"]) for f in self.folds)
        self.assertLess(last_test_end, self.lock_start)
        meta = self.batch.metadata
        self.assertEqual(self.lock_start,
                         (meta["prediction_date"].iloc[-1] - pd.DateOffset(months=12) + pd.Timedelta(days=1)).normalize())

    def test_folds_are_six_calendar_months_in_order(self):
        starts = [pd.Timestamp(f["test_start"]) for f in self.folds]
        self.assertEqual(starts, sorted(starts))
        for a, b in zip(starts, starts[1:]):
            self.assertEqual(b, a + pd.DateOffset(months=6))

    def test_small_train_fold_is_excluded_with_reason_not_relaxed(self):
        folds, _ = walk_forward_folds(self.batch.metadata, first_test="2024-01-01", test_months=6,
                                      lock_months=12, min_train_rows=10_000)
        self.assertTrue(all("학습 행" in f.get("excluded", "") for f in folds))


class BaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = long_fixture()
        cls.batch = build_sequences(**cls.inputs, lookback=LOOKBACK, horizon=HORIZON)
        cls.folds, _ = walk_forward_folds(cls.batch.metadata, first_test="2024-01-01", test_months=6,
                                          lock_months=12, min_train_rows=100)
        cls.fold = next(f for f in cls.folds if not f.get("excluded"))

    def test_flatten_shape(self):
        flat = flatten_windows(self.batch.X)
        self.assertEqual(flat.shape, (len(self.batch.y), LOOKBACK * len(CHANNELS)))

    def test_ridge_predictions_ignore_future_data(self):
        X = flatten_windows(self.batch.X)
        train, test = self.fold["train"], self.fold["test"]
        before = ridge_baseline(X[train], self.batch.y[train], X[test], alpha=1e4)
        changed = long_fixture()
        after_start = self.batch.metadata["target_date"].iloc[test[-1]]
        later = changed["bars"].index > after_start
        for column in ("open", "high", "low", "close"):
            changed["bars"].loc[later, column] *= 1.5
        batch2 = build_sequences(**changed, lookback=LOOKBACK, horizon=HORIZON)
        X2 = flatten_windows(batch2.X)
        after = ridge_baseline(X2[train], batch2.y[train], X2[test], alpha=1e4)
        np.testing.assert_allclose(before, after, rtol=0, atol=1e-9)

    def test_scaler_uses_training_rows_only(self):
        X = flatten_windows(self.batch.X)
        train, test = self.fold["train"], self.fold["test"]
        a = ridge_baseline(X[train], self.batch.y[train], X[test], alpha=1e4)
        X_extreme = X.copy()
        X_extreme[test] *= 1000.0           # 시험 구간을 극단으로 바꿔도 학습 통계는 변하면 안 된다
        b = ridge_baseline(X_extreme[train], self.batch.y[train], X_extreme[test], alpha=1e4)
        # 예측은 시험 입력에 따라 달라지지만, 학습 구간 예측(자기 자신)은 같아야 한다.
        a_train = ridge_baseline(X[train], self.batch.y[train], X[train], alpha=1e4)
        b_train = ridge_baseline(X_extreme[train], self.batch.y[train], X_extreme[train], alpha=1e4)
        np.testing.assert_allclose(a_train, b_train, rtol=0, atol=1e-9)
        self.assertFalse(np.allclose(a, b))

    def test_persistence_is_zero_return(self):
        np.testing.assert_array_equal(persistence_baseline(self.batch.y[:5]), np.zeros(5))

    def test_common_dates_is_the_intersection(self):
        a = pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-04"])
        b = pd.DatetimeIndex(["2024-01-03", "2024-01-04", "2024-01-05"])
        c = pd.DatetimeIndex(["2024-01-04", "2024-01-05"])
        self.assertEqual(list(common_dates(a, b, c)), [pd.Timestamp("2024-01-04")])

    def test_score_reports_mae_first_and_no_probability_keys(self):
        y = np.array([0.01, -0.02, 0.03, -0.01])
        pred = np.array([0.02, -0.01, -0.01, -0.02])
        out = score(y, pred)
        self.assertAlmostEqual(out["mae"], float(np.mean(np.abs(y - pred))))
        self.assertAlmostEqual(out["direction_hit"], 0.75)
        self.assertEqual(out["n"], 4)
        self.assertFalse(any(k.startswith("p_") for k in out))
        # 방향을 부르지 않은 모델(현재가 유지)은 적중률이 없다 — 0 이 아니다.
        zero = score(y, np.zeros(4))
        self.assertTrue(np.isnan(zero["direction_hit"]))
        self.assertEqual(zero["direction_n"], 0)

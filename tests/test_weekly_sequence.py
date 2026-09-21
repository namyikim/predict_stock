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

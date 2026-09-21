# -*- coding: utf-8 -*-
"""주간(5거래일) 예측용 OHLCV 시퀀스 — S01 날짜 계약.

계획: guides/weekly-sequence-model-plan.md, guides/weekly-sequence-s01-implementation.md

운영 코드와 분리된 순수 함수 모듈이다. 학습·다운로드·발행을 하지 않고, 운영 원장·보고서·노트북을
건드리지 않는다. 주어진 KRX 세션 달력으로 시점을 정하고, 빠진 봉을 압축하거나 보간하지 않는다.

날짜 계약(운영 가격 모델 forecast_utils.price_design_frame 과 같다)
    예측일 d 의 입력 마지막 봉 = d-1 (origin_date)
    만기 = d + horizon - 1 (horizon=5 면 d+4, target_date)
    타깃 = close[target_date] / close[origin_date] - 1  (원본 종가)
세션 순서로 셈한다 — 영업일 오프셋은 휴장일을 모른다.

입력 채널(인과적 비율 변환, 최종 스케일러 아님 — 학습 통계 적합은 S02 이후 학습 구간에서만)
    close_return    = close / 전일 close - 1
    open_gap        = open  / 전일 close - 1
    high_relative   = high  / 전일 close - 1
    low_relative    = low   / 전일 close - 1
    volume_relative = volume / 직전 20일 평균 거래량 - 1

표본 하나가 쓰는 봉의 범위는 [d - lookback - 20, d + horizon - 1] 이다. 앞의 20일은 거래량 평균의
워밍업(첫 입력 봉의 전일 종가도 여기 들어간다), 뒤는 만기까지다. 이 범위 안에서 봉이 하나라도
빠졌거나 기업행동이 있으면 표본을 뺀다. 줄여서 통과시키지 않는다.

제외 사유(하나만 기록, 아래 순서로 먼저 걸리는 것)
    insufficient_history  워밍업+입력 범위가 자료 시작보다 앞선다
    pending_target        만기 봉이 아직 없다(자료 끝 이후이거나 세션 달력 밖) — 학습에 쓰지 않는다
    missing_bar           범위 안 세션에 봉이 없다
    corporate_action      범위 안에 분할·배당 표시가 있다(워밍업 포함)
    unavailable_input     입력 봉의 공개 시각이 예측 시각보다 늦거나 같다
    zero_volume_mean      입력 봉의 직전 20일 평균 거래량이 0 이다
기업행동 자료가 없다는 것은 기업행동이 없었다는 뜻이 아니다. 이 모듈은 주어진 표시만 믿는다.
"""
from dataclasses import dataclass
from numbers import Integral

import numpy as np
import pandas as pd

CHANNELS = ("close_return", "open_gap", "high_relative", "low_relative", "volume_relative")
VOLUME_WINDOW = 20
PRICE_COLUMNS = ("open", "high", "low", "close")
REASONS = ("insufficient_history", "pending_target", "missing_bar", "corporate_action",
           "unavailable_input", "zero_volume_mean")
METADATA_COLUMNS = ("origin_date", "prediction_date", "target_date", "available_at",
                    "label_available_at", "prediction_at", "current_close")


@dataclass
class SequenceBatch:
    """X: (N, lookback, 5) float32 · y: (N,) float64 · metadata: 표본별 날짜·시각 · excluded: 빠진 예측일과 사유."""
    X: np.ndarray
    y: np.ndarray
    metadata: pd.DataFrame
    excluded: pd.DataFrame


def _positive_int(value, name):
    # bool 은 Integral 이지만 True=1 로 조용히 통과시키면 안 된다. 60.0 같은 실수도 거부한다.
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
        raise ValueError(f"{name} 은 1 이상의 정수여야 합니다(받은 값: {value!r}).")
    return int(value)


def _date_index(index, name):
    index = pd.DatetimeIndex(index)
    if index.tz is not None:
        raise ValueError(f"{name} 의 날짜 인덱스에는 시간대가 없어야 합니다(세션 날짜).")
    if index.has_duplicates:
        raise ValueError(f"{name} 에 중복 날짜가 있습니다.")
    if not index.is_monotonic_increasing:
        raise ValueError(f"{name} 의 날짜가 정렬돼 있지 않습니다.")
    return index


def _aware_series(series, index, name):
    if not isinstance(series, pd.Series):
        raise ValueError(f"{name} 은 pandas Series 여야 합니다.")
    values = pd.to_datetime(series)
    if getattr(values.dt, "tz", None) is None:
        raise ValueError(f"{name} 은 시간대가 있는 시각이어야 합니다(예: Asia/Seoul).")
    if index is not None and not pd.DatetimeIndex(series.index).equals(index):
        raise ValueError(f"{name} 의 날짜가 bars 와 다릅니다.")
    if values.isna().any():
        raise ValueError(f"{name} 에 빈 시각이 있습니다.")
    return values


def _validate_bars(bars):
    missing = [c for c in (*PRICE_COLUMNS, "volume") if c not in bars.columns]
    if missing:
        raise ValueError(f"bars 에 열이 없습니다: {missing}")
    frame = bars[[*PRICE_COLUMNS, "volume"]].apply(pd.to_numeric, errors="coerce").astype(float)
    if not np.isfinite(frame.to_numpy()).all():
        raise ValueError("bars 에 숫자가 아니거나 무한대인 값이 있습니다.")
    if (frame[list(PRICE_COLUMNS)] <= 0).any().any():
        raise ValueError("bars 의 가격은 모두 양수여야 합니다.")
    if (frame["volume"] < 0).any():
        raise ValueError("bars 의 거래량이 음수입니다.")
    high, low = frame["high"], frame["low"]
    body_top = frame[["open", "close"]].max(axis=1)
    body_bottom = frame[["open", "close"]].min(axis=1)
    if (high < low).any() or (high < body_top).any() or (low > body_bottom).any():
        raise ValueError("bars 의 고가·저가가 시가·종가와 맞지 않습니다.")
    return frame


def causal_features(bars_on_sessions):
    """세션 달력에 맞춰 다시 색인한(빠진 봉은 NaN) OHLCV → 다섯 채널. 빠진 봉은 채우지 않는다."""
    b = bars_on_sessions
    previous = b["close"].shift(1)
    volume_mean = b["volume"].shift(1).rolling(VOLUME_WINDOW).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        features = pd.DataFrame({
            "close_return": b["close"] / previous - 1,
            "open_gap": b["open"] / previous - 1,
            "high_relative": b["high"] / previous - 1,
            "low_relative": b["low"] / previous - 1,
            "volume_relative": b["volume"] / volume_mean - 1,
        }, index=b.index)
    return features[list(CHANNELS)], volume_mean


def build_sequences(bars, sessions, available_at, prediction_at, corporate_actions,
                    lookback=60, horizon=5):
    """과거 OHLCV → 5거래일 예측용 입력 배열과 누수 방지 메타데이터. SequenceBatch 반환.

    bars: 날짜 인덱스(세션 날짜, 시간대 없음), 원본 open/high/low/close/volume.
    sessions: 정렬·중복 없는 KRX 세션 날짜. 입력 범위와 만기 범위를 포함해야 한다.
    available_at: bars 와 같은 날짜의 시간대 있는 공개 시각.
    prediction_at: 예측 세션 날짜 → 시간대 있는 예측 시각.
    corporate_actions: bars 와 같은 날짜의 bool(분할·배당 표시).
    """
    lookback = _positive_int(lookback, "lookback")
    horizon = _positive_int(horizon, "horizon")
    if not isinstance(bars, pd.DataFrame):
        raise ValueError("bars 는 pandas DataFrame 이어야 합니다.")
    bar_index = _date_index(bars.index, "bars")
    sessions = _date_index(sessions, "sessions")
    frame = _validate_bars(bars)
    published = _aware_series(available_at, bar_index, "available_at")
    predict_at = _aware_series(prediction_at, None, "prediction_at")
    predict_dates = _date_index(prediction_at.index, "prediction_at")
    if not isinstance(corporate_actions, pd.Series) or corporate_actions.dtype != bool:
        raise ValueError("corporate_actions 는 bool Series 여야 합니다.")
    if not pd.DatetimeIndex(corporate_actions.index).equals(bar_index):
        raise ValueError("corporate_actions 의 날짜가 bars 와 다릅니다.")
    off_calendar = bar_index.difference(sessions)
    if len(off_calendar):
        raise ValueError(f"세션 달력에 없는 날짜의 봉이 있습니다: {off_calendar[:3].date.tolist()}")
    not_sessions = predict_dates.difference(sessions)
    if len(not_sessions):
        raise ValueError(f"세션이 아닌 예측일이 있습니다: {not_sessions[:3].date.tolist()}")

    # 세션 달력에 맞춰 다시 색인한다. 빠진 봉은 NaN 으로 남기고 절대 채우지 않는다.
    on_sessions = frame.reindex(sessions)
    present = on_sessions["close"].notna().to_numpy()
    features, volume_mean = causal_features(on_sessions)
    feature_values = features.to_numpy(dtype=float)
    zero_mean = (volume_mean == 0).to_numpy()
    action = corporate_actions.reindex(sessions, fill_value=False).to_numpy(dtype=bool)
    published_on = published.reindex(sessions)
    close = on_sessions["close"].to_numpy(dtype=float)
    first_bar = sessions.get_loc(bar_index[0]) if len(bar_index) else len(sessions)
    last_bar = sessions.get_loc(bar_index[-1]) if len(bar_index) else -1

    rows, targets, windows, excluded = [], [], [], []
    for prediction_date in predict_dates:
        d = sessions.get_loc(prediction_date)
        start = d - lookback - VOLUME_WINDOW          # 워밍업 첫 봉
        origin = d - 1
        target = d + horizon - 1
        reason = None
        if start < first_bar:
            reason = "insufficient_history"
        elif target >= len(sessions) or target > last_bar:
            reason = "pending_target"
        elif not present[start:target + 1].all():
            reason = "missing_bar"
        elif action[start:target + 1].any():
            reason = "corporate_action"
        elif published_on.iloc[start:origin + 1].max() >= predict_at.loc[prediction_date]:
            reason = "unavailable_input"
        elif zero_mean[d - lookback:origin + 1].any():
            reason = "zero_volume_mean"
        if reason is not None:
            excluded.append({"prediction_date": prediction_date, "reason": reason})
            continue
        window = feature_values[d - lookback:origin + 1]
        if not np.isfinite(window).all():                  # 위 검사를 모두 통과했으면 일어나지 않는다
            raise AssertionError(f"{prediction_date.date()} 입력에 유한하지 않은 값이 남았습니다.")
        windows.append(window)
        targets.append(close[target] / close[origin] - 1)
        rows.append({
            "origin_date": sessions[origin],
            "prediction_date": prediction_date,
            "target_date": sessions[target],
            "available_at": published_on.iloc[start:origin + 1].max(),
            "label_available_at": published_on.iloc[target],
            "prediction_at": predict_at.loc[prediction_date],
            "current_close": close[origin],
        })

    X = (np.stack(windows).astype(np.float32) if windows
         else np.zeros((0, lookback, len(CHANNELS)), dtype=np.float32))
    y = np.asarray(targets, dtype=np.float64)
    metadata = pd.DataFrame(rows, columns=list(METADATA_COLUMNS))
    excluded = pd.DataFrame(excluded, columns=["prediction_date", "reason"])
    return SequenceBatch(X=X, y=y, metadata=metadata, excluded=excluded)

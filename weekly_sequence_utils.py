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
    return frame


def inconsistent_bars(frame, tolerance=0.005):
    """고가·저가가 시가·종가와 맞지 않는 봉. bool Series(True = 어긋남).

    2024-10-14 삼성전자처럼 종가가 저가보다 100원 낮게 기록된 봉이 실제 야후 자료에 있다(2,855봉 중 1봉,
    종가 대비 0.17%). 값을 고치지 않고 그 봉을 결측으로 취급해, S01 규칙대로 그 봉이 범위에 드는 표본만
    빠지게 한다. tolerance(종가 대비)를 넘게 어긋나면 잡음이 아니라 자료 오류로 보고 거부한다.
    """
    high, low = frame["high"], frame["low"]
    body_top = frame[["open", "close"]].max(axis=1)
    body_bottom = frame[["open", "close"]].min(axis=1)
    off = (high < low) | (high < body_top) | (low > body_bottom)
    if off.any():
        gap = pd.concat([(body_top - high).clip(lower=0), (low - body_bottom).clip(lower=0),
                         (low - high).clip(lower=0)], axis=1).max(axis=1) / frame["close"]
        worst = float(gap[off].max())
        if worst > tolerance:
            raise ValueError(f"bars 의 고가·저가가 시가·종가와 맞지 않습니다(최대 종가 대비 {worst:.2%}, "
                             f"허용 {tolerance:.1%}). 잡음 수준을 넘어 자료 오류로 봅니다.")
    return off


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

    # 고가·저가가 시가·종가와 어긋난 봉은 값을 고치지 않고 결측으로 둔다(사유는 missing_bar 로 잡힌다).
    off = inconsistent_bars(frame)
    if off.any():
        frame = frame.mask(off)
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


# ==============================================================================================
# S02 Task 1 — 고정 스냅샷 로더
#
# 시세는 기존 실험 기반(runs/medium_horizon/<target>/data_cache)의 고정 스냅샷만 읽는다. 여기서
# 다운로드하지 않는다 — 새로 내려받으면 다른 스냅샷이 되어 이전 실험과 같은 자료라고 말할 수 없다.
# 스냅샷 형식은 노트북 load_raw 가 쓰는 것과 같다: <cache>/target.parquet(또는 .csv),
# 열 open/high/low/close/adj_close/volume, auto_adjust=False 라 OHLC·close 는 원본이다.
# ==============================================================================================
import hashlib
from pathlib import Path

TICKERS = {"samsung": "005930.KS", "sk_hynix": "000660.KS"}
SNAPSHOT_NAME = "target"                # 노트북 ASSETS 의 종목 키
TZ = "Asia/Seoul"


@dataclass
class OhlcvSnapshot:
    """원본 OHLCV 스냅샷. bars 는 open/high/low/close/volume 만(원본), adjusted 는 항상 False 여야 한다."""
    bars: pd.DataFrame
    adjusted: bool
    source: str
    fetched_at: object
    sha256: str
    ticker: str
    path: str


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_ohlcv_snapshot(cache_dir, target):
    """<cache_dir>/target.parquet(없으면 .csv) 를 읽는다. 없으면 FileNotFoundError — 내려받지 않는다."""
    if target not in TICKERS:
        raise ValueError(f"알 수 없는 target: {target!r} (가능: {sorted(TICKERS)})")
    cache = Path(cache_dir)
    parquet, csv = cache / f"{SNAPSHOT_NAME}.parquet", cache / f"{SNAPSHOT_NAME}.csv"
    if parquet.is_file():
        frame, path = pd.read_parquet(parquet), parquet
    elif csv.is_file():
        frame, path = pd.read_csv(csv, index_col=0), csv
    else:
        raise FileNotFoundError(
            f"{cache} 에 고정 시세 스냅샷({SNAPSHOT_NAME}.parquet/.csv)이 없습니다. 이 러너는 내려받지 않습니다 — "
            "기존 medium_horizon 실험의 data_cache 를 복사하거나 노트북을 캐시 모드로 한 번 실행하세요.")
    frame = frame.copy()
    frame.columns = [str(c).strip().lower().replace(" ", "_") for c in frame.columns]
    if "adjusted" in frame.columns and frame["adjusted"].astype(bool).any():
        raise ValueError("스냅샷의 OHLC 가 조정된 것으로 표시돼 있습니다. 원본 OHLC 스냅샷만 씁니다(조정 종가와 혼용 금지).")
    missing = [c for c in ("open", "high", "low", "close", "volume") if c not in frame.columns]
    if missing:
        raise ValueError(f"스냅샷에 열이 없습니다: {missing}")
    index = pd.DatetimeIndex(pd.to_datetime(frame.index))
    if index.tz is not None:
        index = index.tz_localize(None)
    # parquet 은 ns, csv 파싱은 us 정밀도로 와서 같은 날짜가 다른 dtype 이 된다. ns 로 통일한다.
    frame.index = index.normalize().as_unit("ns")
    bars = frame[["open", "high", "low", "close", "volume"]].astype(float)
    bars = bars[~bars.index.duplicated(keep="last")].sort_index()
    fetched_at = pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC")
    return OhlcvSnapshot(bars=bars, adjusted=False, source=f"data_cache/{path.name}",
                         fetched_at=fetched_at, sha256=_sha256(path), ticker=TICKERS[target],
                         path=str(path))


def session_calendar(start, end):
    """KRX 세션 날짜(tz 없음). S01 테스트와 같은 exchange_calendars XKRX."""
    import exchange_calendars as xc
    sessions = xc.get_calendar("XKRX").sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    return pd.DatetimeIndex(sessions).tz_localize(None)


def availability_policy(bars, sessions, recorded_at=None, publish_hour=16, predict_hour=7, tz=TZ):
    """(available_at, prediction_at).

    봉의 공개 시각은 실제 수집 시각(recorded_at)이 있으면 그것을, 없으면 '그 세션 publish_hour' 라는
    정책값을 쓴다. 정책값은 사실이 아니라 가정이므로 attrs["policy"]="assumed" 로 표시하고, 러너는
    그것을 manifest 에 남긴다. 봉 날짜만으로 공개 시각을 확정하지 않는다는 S01 제약을 지키는 방법이다.
    prediction_at 은 각 세션의 predict_hour(장 전).
    """
    index = pd.DatetimeIndex(bars.index)
    if recorded_at is not None:
        recorded = _aware_series(recorded_at, index, "recorded_at")
        available_at = pd.Series(recorded.to_numpy(), index=index)
        available_at.attrs["policy"] = "recorded"
    else:
        available_at = pd.Series(index + pd.Timedelta(hours=publish_hour), index=index).dt.tz_localize(tz)
        available_at.attrs["policy"] = "assumed"
    sessions = pd.DatetimeIndex(sessions)
    prediction_at = pd.Series(sessions + pd.Timedelta(hours=predict_hour), index=sessions).dt.tz_localize(tz)
    prediction_at.attrs["policy"] = "assumed"
    return available_at, prediction_at


def corporate_action_flags(bars, ratio_threshold=0.3):
    """가격 불연속 휴리스틱으로 분할·병합 후보를 표시한다. bool Series, attrs["source"]="heuristic".

    기업행동 자료원이 없다. 전일 종가 대비 당일 시가·종가가 **모두** (1-threshold)배 아래이거나
    1/(1-threshold)배 위이면 True 로 둔다. 기본 threshold 0.3 은 KRX 일일 가격제한폭(±30%)이다 —
    시가와 종가가 함께 제한폭 밖으로 뛰는 것은 정상 거래로는 불가능하므로 기준가 변경(분할·병합·
    액면 변경)으로 본다. 2:1 분할(0.5배)은 잡히고 ±10% 급등락은 잡히지 않는다.
    배당·소규모 행동은 잡지 못한다 — 이것으로 기업행동이 없었다고 말하지 않는다. 결과 문서에 한계로 적는다.
    """
    previous = bars["close"].shift(1)
    lo, hi = 1.0 - ratio_threshold, 1.0 / (1.0 - ratio_threshold)
    open_ratio = bars["open"] / previous
    close_ratio = bars["close"] / previous
    down = (open_ratio <= lo) & (close_ratio <= lo)
    up = (open_ratio >= hi) & (close_ratio >= hi)
    flags = (down | up).fillna(False).astype(bool)
    flags.attrs["source"] = "heuristic"
    flags.attrs["ratio_threshold"] = ratio_threshold
    return flags


# ==============================================================================================
# S02 Task 2 — 공통 날짜 기준선
#
# 폴드 계약은 tools/run_medium_horizon.evaluation_folds 와 같다: first_test 부터 test_months 개월씩
# 개발 폴드, 학습 행은 라벨 만기(target_date)가 시험 시작일보다 앞선 행만(purge). 마지막
# lock_months 개월(잠금)은 S02 에서 만들지 않는다 — M07 이 이미 본 구간이고 S04 까지 열지 않는다.
# ==============================================================================================


def walk_forward_folds(metadata, first_test, test_months=6, lock_months=12, min_train_rows=500,
                       min_test_rows=60):
    """(폴드 목록, 잠금 시작일). 각 폴드: name, test_start, test_end, train, test, train_rows, test_rows, excluded?"""
    dates = pd.DatetimeIndex(metadata["prediction_date"])
    maturity = pd.DatetimeIndex(metadata["target_date"])
    last = dates[-1]
    lock_start = (last - pd.DateOffset(months=lock_months) + pd.Timedelta(days=1)).normalize()
    folds = []
    t0 = pd.Timestamp(first_test)
    while t0 < lock_start:
        t1 = min(t0 + pd.DateOffset(months=test_months), lock_start)
        test = np.flatnonzero((dates >= t0) & (dates < t1))
        train = np.flatnonzero(maturity < t0)                      # purge: 만기가 시험 시작 전
        entry = {"name": f"dev_{len(folds) + 1:02d}", "test_start": str(t0.date()),
                 "test_end": str((t1 - pd.Timedelta(days=1)).date()),
                 "train_rows": int(len(train)), "test_rows": int(len(test)), "train": train, "test": test,
                 "train_start": str(dates[train[0]].date()) if len(train) else None,
                 "train_end": str(dates[train[-1]].date()) if len(train) else None}
        if len(test) == 0:
            entry["excluded"] = "시험 행 없음"
        elif len(test) < min_test_rows:
            entry["excluded"] = f"시험 행 {len(test)} < {min_test_rows}(자료 끝에서 잘린 폴드)"
        elif len(train) < min_train_rows:
            entry["excluded"] = f"학습 행 {len(train)} < {min_train_rows}"
        folds.append(entry)
        t0 = t1
    return folds, lock_start


def flatten_windows(X):
    """(N, lookback, 5) → (N, lookback*5). Ridge 입력."""
    X = np.asarray(X, dtype=np.float64)
    return X.reshape(len(X), -1)


def ridge_baseline(X_train, y_train, X_test, alpha=1e4):
    """StandardScaler(학습 구간만) + Ridge. 운영 5일 가격 모델과 같은 alpha 가 기본."""
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    model = make_pipeline(StandardScaler(), Ridge(alpha=float(alpha)))
    model.fit(np.asarray(X_train, dtype=float), np.asarray(y_train, dtype=float))
    return model.predict(np.asarray(X_test, dtype=float))


def persistence_baseline(y):
    """현재가 유지 — 5일 수익률 0."""
    return np.zeros(len(y), dtype=float)


def common_dates(*date_sets):
    """모든 모델이 예측을 낸 날짜만. 표본 수가 다른 비교를 같은 표에 놓지 않기 위해서다."""
    out = pd.DatetimeIndex(date_sets[0])
    for other in date_sets[1:]:
        out = out.intersection(pd.DatetimeIndex(other))
    return out.sort_values()


def score(y, pred):
    """주 지표 MAE, 보조 RMSE·방향 적중률·표본 수. 확률(p_*)은 만들지 않는다.

    방향 적중률은 예측이 방향을 부른 행(pred != 0)에서만 센다. 현재가 유지(예측 0)는 방향을 부르지
    않으므로 NaN — 0 으로 세면 '항상 틀린 모델'처럼 보인다.
    """
    y, pred = np.asarray(y, dtype=float), np.asarray(pred, dtype=float)
    error = y - pred
    called = pred != 0
    hit = float(np.mean(np.sign(y[called]) == np.sign(pred[called]))) if called.any() else float("nan")
    return {"mae": float(np.mean(np.abs(error))), "rmse": float(np.sqrt(np.mean(error ** 2))),
            "direction_hit": hit, "direction_n": int(called.sum()), "n": int(len(y))}

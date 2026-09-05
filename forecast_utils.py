"""시간순 예측 모델 선택과 누적 예측 원장. 노트북에도 동일 소스를 포함한다."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


def rolling_train_indices(date_index, before, years=5):
    dates = pd.DatetimeIndex(date_index)
    before = pd.Timestamp(before)
    return np.flatnonzero((dates >= before - pd.DateOffset(years=years)) & (dates < before))


def direction_estimator(family, params, seed=42):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    if family == "Logistic":
        return make_pipeline(StandardScaler(), LogisticRegression(
            C=params["C"], class_weight=params["class_weight"], max_iter=3000, random_state=seed))
    if family != "LightGBM":
        raise ValueError(f"Unknown model: {family}")
    from lightgbm import LGBMClassifier
    return LGBMClassifier(n_estimators=params["n_estimators"], num_leaves=7,
                          learning_rate=.03, min_child_samples=50, colsample_bytree=.85,
                          reg_alpha=.5, reg_lambda=2., class_weight=params["class_weight"],
                          random_state=seed, n_jobs=2, verbosity=-1)


def aligned_probabilities(estimator, X):
    p = np.full((len(X), 3), 1e-7)
    p[:, np.asarray(estimator.classes_, dtype=int)] = estimator.predict_proba(X)
    p = np.clip(p, 1e-7, 1.)
    return p / p.sum(axis=1, keepdims=True)


def temperature_probabilities(probs, temperature):
    logits = np.log(np.clip(probs, 1e-7, 1.)) / temperature
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    return p / p.sum(axis=1, keepdims=True)


def probability_loss(y, p):
    return float(-np.log(np.clip(p[np.arange(len(y)), np.asarray(y, dtype=int)], 1e-7, 1.)).mean())


def fit_direction_model(X, y, train_indices, family, seed=42):
    """바깥 평가 구간을 보지 않고, 과거 내부 3개 구간의 정확도로 설정을 고른다.

    정확도가 같으면 log loss가 낮은 설정을 선택한다. 온도 보정은 argmax를 유지한다.
    반환값은 표준 estimator와 dict뿐이어서 노트북/로컬 모두 joblib로 다시 읽을 수 있다.
    """
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.dummy import DummyClassifier
    indices = np.asarray(train_indices, dtype=int)
    if len(indices) < 100 or np.any(np.diff(indices) <= 0):
        raise ValueError("At least 100 chronologically ordered training rows are required")
    xt, yt = np.asarray(X)[indices], np.asarray(y)[indices]
    if family == "Logistic":
        candidates = [{"C": c, "class_weight": w} for c in (.003, .01, .03) for w in (None, "balanced")]
    elif family == "LightGBM":
        candidates = [{"n_estimators": n, "class_weight": w} for n in (60, 120) for w in (None, "balanced")]
    else:
        raise ValueError(family)
    splits = list(TimeSeriesSplit(n_splits=3, test_size=min(126, len(yt) // 5)).split(xt))
    labels = np.concatenate([yt[va] for _, va in splits])
    trials = []
    for params in candidates:
        predictions = []
        for tr, va in splits:
            estimator = (direction_estimator(family, params, seed) if len(np.unique(yt[tr])) > 1
                         else DummyClassifier(strategy="prior"))
            estimator.fit(xt[tr], yt[tr])
            predictions.append(aligned_probabilities(estimator, xt[va]))
        probs = np.vstack(predictions)
        accuracy = float(np.mean(probs.argmax(axis=1) == labels))
        trials.append((accuracy, probability_loss(labels, probs), params, probs))
    best = min(trials, key=lambda t: (-t[0], t[1]))
    temperatures = (1., .75, 1.5, 2.)
    temperature = min(temperatures, key=lambda t: probability_loss(labels, temperature_probabilities(best[3], t)))
    estimator = (direction_estimator(family, best[2], seed) if len(np.unique(yt)) > 1
                 else DummyClassifier(strategy="prior"))
    estimator.fit(xt, yt)
    return {"estimator": estimator, "temperature": temperature, "selection": {
        "family": family, "params": best[2], "temperature": temperature,
        "inner_accuracy": best[0], "inner_log_loss": probability_loss(labels, temperature_probabilities(best[3], temperature)),
        "last_validation_position": int(indices[splits[-1][1][-1]]), "training_rows": len(indices),
    }}


def predict_direction_model(fitted, X):
    return temperature_probabilities(aligned_probabilities(fitted["estimator"], X), fitted["temperature"])


def calibrate_price_forecast(y, prediction, sigma, dates, horizon, ci_function, coverage=.8):
    """OOF를 보정 50% / 신호 선택 25% / 최종 평가 25%로 나누고 경계 라벨을 제거한다.

    최종 평가 정답은 slope, 신호 선택, 구간 폭 결정에 사용하지 않는다.
    """
    y, prediction, sigma = map(lambda a: np.asarray(a, dtype=float), (y, prediction, sigma))
    n = len(y)
    cut1, cut2, gap = n // 2, n * 3 // 4, horizon - 1
    cal = np.arange(max(0, cut1 - gap))
    gate = np.arange(cut1, max(cut1, cut2 - gap))
    evaluation = np.arange(cut2, n)
    if min(len(cal), len(gate), len(evaluation)) < 30:
        raise ValueError("Not enough OOF rows for separate price calibration, selection and evaluation")
    denom = np.sum(prediction[cal] ** 2)
    slope = float(np.clip(np.sum(prediction[cal] * y[cal]) / denom, 0., 1.)) if denom > 0 else 0.
    gate_diff = np.abs(y[gate] - slope * prediction[gate]) - np.abs(y[gate])
    gate_lo, gate_hi = ci_function(pd.DatetimeIndex(dates)[gate], lambda i: float(gate_diff[i].mean()))
    beats_baseline = bool(np.isfinite(gate_hi) and gate_hi < 0)
    if not beats_baseline:
        slope = 0.
    residual = np.abs(y[cal] - slope * prediction[cal]) / np.maximum(sigma[cal], 1e-6)
    q = float(np.quantile(residual, coverage))
    test_error = np.abs(y[evaluation] - slope * prediction[evaluation])
    diff = test_error - np.abs(y[evaluation])
    lo, hi = ci_function(pd.DatetimeIndex(dates)[evaluation], lambda i: float(diff[i].mean()))
    return {
        "zero_baseline_mae": float(np.abs(y[evaluation]).mean()),
        "raw_model_mae": float(np.abs(y[evaluation] - prediction[evaluation]).mean()),
        "shrunk_model_mae": float(test_error.mean()), "mae_diff_vs_zero": float(diff.mean()),
        "mae_diff_lo": float(lo), "mae_diff_hi": float(hi),
        "selection_mae_diff_lo": float(gate_lo), "selection_mae_diff_hi": float(gate_hi),
        "oof_slope": slope, "beats_baseline": beats_baseline, "band_q": q,
        "band_coverage_realized": float(np.mean(test_error <= q * sigma[evaluation])),
        "n_oof": n, "n_evaluation": len(evaluation), "calibration_end": int(cal[-1]),
        "gate_start": int(gate[0]), "gate_end": int(gate[-1]), "evaluation_start": int(evaluation[0]),
    }


def snapshot_hash(raw):
    digest = hashlib.sha256()
    for name, frame in sorted(raw.items()):
        digest.update(name.encode())
        digest.update(json.dumps(list(frame.columns)).encode())
        digest.update(pd.util.hash_pandas_object(frame.sort_index(), index=True).values.tobytes())
    return digest.hexdigest()[:20]


def atomic_csv(frame, path):
    """단일 실행자용 원자적 교체. 중간에 런타임이 끊겨도 기존 원장을 보존한다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
            frame.to_csv(stream, index=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def append_forecasts(path, new_rows):
    """예측은 불변: 같은 record_id 재실행은 무시하고, 새 run_id는 추가한다."""
    path = Path(path)
    previous = pd.read_csv(path) if path.exists() else pd.DataFrame()
    if len(previous):
        if "record_id" not in previous:
            previous["record_id"] = pd.Series(index=previous.index, dtype="str")
        for i in previous.index[previous.record_id.isna()]:
            payload = previous.loc[i].to_json() + str(i)
            previous.loc[i, "record_id"] = "legacy-" + hashlib.sha256(payload.encode()).hexdigest()[:20]
    combined = pd.concat([previous, new_rows], ignore_index=True)
    combined = combined.drop_duplicates("record_id", keep="first")
    atomic_csv(combined, path)
    return combined


def evaluate_forecasts(log, bars, now=None):
    """확정 봉으로 실제값/오차를 갱신한다. 당시 band/mode 및 원래 예측은 보존한다."""
    result = log.copy()
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    market_now = now.tz_convert("Asia/Seoul")
    bars = bars.sort_index().copy()
    bars.index = pd.DatetimeIndex(bars.index).tz_localize(None).normalize()
    numeric = ["actual_open", "actual_close", "actual_return", "actual_class", "direction_correct",
               "price_error", "absolute_price_error", "price_ape", "return_error", "interval_hit",
               "center_price_error", "log_loss", "brier"]
    for col in numeric:
        result[col] = np.nan
    result["status"] = "pending"
    result["is_prospective"] = False
    result["actual_updated_at_utc"] = now.isoformat()
    for i, row in result.iterrows():
        target_value = row.get("target_date")
        target_value = row.get("prediction_date") if pd.isna(target_value) else target_value
        target = pd.to_datetime(target_value, errors="coerce")
        if pd.isna(target):
            result.loc[i, "status"] = "invalid_target"
            continue
        target = pd.Timestamp(target).tz_localize(None).normalize()
        start = pd.to_datetime(row.get("prediction_date", target), errors="coerce")
        created = pd.to_datetime(row.get("created_at_utc"), utc=True, errors="coerce")
        mode = row.get("target_mode", "close_to_close")
        # 09:00 시가 기반 예측은 시가 확인 직후(09:05까지)만 별도 집계한다.
        if pd.notna(start) and pd.notna(created):
            deadline = pd.Timestamp(start).tz_localize(None).normalize().tz_localize("Asia/Seoul") + pd.Timedelta(hours=9)
            if mode == "open_to_close":
                deadline += pd.Timedelta(minutes=5)
            result.loc[i, "is_prospective"] = bool(created < deadline)
        if target.date() > market_now.date() or (target.date() == market_now.date() and (market_now.hour, market_now.minute) < (15, 40)):
            continue
        if target not in bars.index:
            result.loc[i, "status"] = "missing_actual"
            continue
        bar = bars.loc[target]
        if not np.isfinite(bar["close"]) or bar["close"] <= 0:
            result.loc[i, "status"] = "missing_actual"
            continue
        result.loc[i, ["actual_open", "actual_close"]] = [bar["open"], bar["close"]]
        kind = row.get("kind", "direction")
        if pd.isna(kind):
            kind = "direction"
        if kind == "price":
            base = row.get("current_close", np.nan)
            if not np.isfinite(base) or base <= 0:
                result.loc[i, "status"] = "missing_reference"
                continue
            actual_return = float(bar["close"] / base - 1)
            predicted = row.get("predicted_close", np.nan)
            if pd.notna(predicted):
                error = float(predicted - bar["close"])
                result.loc[i, ["price_error", "absolute_price_error", "price_ape"]] = [error, abs(error), abs(error) / bar["close"]]
            center = row.get("center_close", np.nan)
            if pd.notna(center):
                result.loc[i, "center_price_error"] = center - bar["close"]
            predicted_return = row.get("predicted_return", np.nan)
            if pd.notna(predicted_return):
                result.loc[i, "return_error"] = predicted_return - actual_return
            low, high = row.get("low_close", np.nan), row.get("high_close", np.nan)
            if pd.notna(low) and pd.notna(high):
                result.loc[i, "interval_hit"] = float(low <= bar["close"] <= high)
        else:
            if mode == "open_to_close":
                if not np.isfinite(bar["open"]) or bar["open"] <= 0:
                    result.loc[i, "status"] = "missing_actual"
                    continue
                actual_return = float(bar["close"] / bar["open"] - 1)
            elif mode == "close_to_close":
                if not np.isfinite(bar["adj_close"]) or bar["adj_close"] <= 0:
                    result.loc[i, "status"] = "missing_actual"
                    continue
                base_date = pd.to_datetime(row.get("as_of_date"), errors="coerce")
                if pd.isna(base_date):
                    earlier = bars.index[bars.index < target]
                    base_date = earlier[-1] if len(earlier) else pd.NaT
                if base_date not in bars.index or base_date >= target:
                    result.loc[i, "status"] = "missing_reference"
                    continue
                if not np.isfinite(bars.loc[base_date, "adj_close"]) or bars.loc[base_date, "adj_close"] <= 0:
                    result.loc[i, "status"] = "missing_reference"
                    continue
                # 양쪽 배당조정 가격은 같은 최신 스냅샷에서 읽어 조정계수 변경을 상쇄한다.
                actual_return = float(bar["adj_close"] / bars.loc[base_date, "adj_close"] - 1)
            else:
                result.loc[i, "status"] = "invalid_target_mode"
                continue
            band = row.get("band", np.nan)
            if pd.isna(band) or band < 0:
                result.loc[i, "status"] = "missing_band"
                continue
            actual_class = 0 if actual_return < -band else 2 if actual_return > band else 1
            p = np.asarray([row.get(c, np.nan) for c in ["p_down", "p_flat", "p_up"]], dtype=float)
            result.loc[i, "actual_class"] = actual_class
            if np.isfinite(p).all() and (p >= 0).all() and p.sum() > 0:
                p = p / p.sum()
                result.loc[i, "direction_correct"] = float(p.argmax() == actual_class)
                result.loc[i, "log_loss"] = -np.log(max(p[actual_class], 1e-7))
                result.loc[i, "brier"] = float(np.sum((p - np.eye(3)[actual_class]) ** 2))
        result.loc[i, "actual_return"] = actual_return
        result.loc[i, "status"] = "scored"
    return result


def daily_comparison(evaluated):
    """동일 날짜/모델/설정의 최초 사전 예측만 선택하여 재실행으로 표본이 늘지 않게 한다."""
    if evaluated.empty:
        return evaluated.copy()
    eligible = evaluated.loc[evaluated["is_prospective"].eq(True)].copy()
    if "created_at_utc" not in eligible:
        eligible["created_at_utc"] = pd.NaT
    if "record_id" not in eligible:
        eligible["record_id"] = pd.Series(index=eligible.index, dtype="str")
    eligible["created_at_utc"] = pd.to_datetime(eligible["created_at_utc"], utc=True)
    keys = ["target_date", "model", "kind", "horizon_days", "target_mode", "config_hash"]
    for key in keys:
        if key not in eligible:
            eligible[key] = "legacy"
    return eligible.sort_values("created_at_utc").drop_duplicates(keys, keep="first")


def summarize_daily(daily):
    scored = daily.loc[daily.status == "scored"]
    return scored.groupby(["model", "kind", "horizon_days", "target_mode", "config_hash"], dropna=False).agg(
        n=("record_id", "size"), accuracy=("direction_correct", "mean"),
        mean_log_loss=("log_loss", "mean"), mean_brier=("brier", "mean"),
        point_forecasts=("absolute_price_error", "count"), price_mae=("absolute_price_error", "mean"),
        price_mape=("price_ape", "mean"), interval_coverage=("interval_hit", "mean"),
    ).reset_index()

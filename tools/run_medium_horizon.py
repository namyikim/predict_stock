# -*- coding: utf-8 -*-
"""5·20거래일 가격 예측 개선 계획(guides/medium-horizon-improvement-plan.md)의 실험 러너.

    python tools/run_medium_horizon.py --task M00 --target samsung --mode quick --storage runs/medium_horizon --resume
    python tools/run_medium_horizon.py --task M01 --target samsung --mode full --resume

원시 예측은 --storage(기본 runs/medium_horizon)/<target>/ 에, 작은 요약은 --results(기본
experiments/medium_horizon)/<task>/<run_id>/ 에 둔다. 시세는 <storage>/<target>/data_cache 의 고정
스냅샷만 읽는다(없으면 실행을 거부한다 — 새로 내려받으면 다른 스냅샷이 된다). 발행은 항상 끈다.

고정 입력 로더: 노트북을 한 번 캐시로 실행해 특징 행렬·시세·설정을 <storage>/<target>/inputs_<mode>_<data_hash>.pkl
에 저장하고, 이후 작업은 그것을 읽는다(같은 data_hash 일 때만). 재개·해시 격리·원자적 쓰기는
tools/run_model_improvement.py 의 것을 그대로 쓴다. 완료 표시가 있어도 산출물(요약·OOF 파일)이 없거나
해시가 다르면 그 단위를 다시 계산한다.

M00 — 현행 5·20일 가격 모델(StandardScaler+Ridge(1e4), 변동성 스케일 타깃, simple 변동성)을 노트북과
같은 행·분할로 재현하고 노트북 통계와 동등성을 확인한 뒤, 현재가 유지 대비 raw/보정/발행 중심값 오차,
구간 품질, 변동성 구간별 오차, 비중첩 부분집합, 날짜 기준 purge 검사, 분할·배당 경계, 원장 만기 현황을 저장한다.
M01 — 평가 계약(개발 6개월 외부 폴드 + 마지막 12개월 잠금 + 내부 3구간)을 네 조합에 적용해 폴드 날짜·
학습 행·purge 검사·제외 사유를 저장하고, 고정 입력 로더로 M00 기준선이 그대로 재현되는지 확인한다.
"""
import argparse
import hashlib
import os
import pickle
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
from run_model_improvement import (  # noqa: E402
    RunState, code_commit, config_hash, data_hash, replace_with_retry, snapshot_paths, start_run,
    write_atomic, write_json, write_metrics, write_metrics_named,
)
import forecast_utils as fu  # noqa: E402

SUPPORTED_TARGETS = ("samsung", "sk_hynix")
MODES = ("quick", "full")
HORIZONS = (5, 20)
COVERAGE = 0.8
TASKS = {"M00": "현행 5·20일 가격 모델 기준선과 실패 유형 진단",
         "M01": "평가 계약(외부 6개월 폴드·잠금 12개월·내부 3구간)과 고정 입력 로더 검증"}
HORIZON_LABELS = {5: "1주일", 20: "1개월"}

# 평가 계약(계획 4절). 점수를 보기 전에 고정한다.
FIRST_TEST = "2021-01-01"
TEST_MONTHS = 6
LOCK_MONTHS = 12
MIN_TRAIN_ROWS = 500
MIN_TEST_ROWS = 60        # 6개월 폴드가 자료 끝에서 잘려 한두 달만 남으면 제외한다(점수를 보기 전 규칙)
INNER_BLOCKS = 3
INNER_MONTHS = 6
SEED = 42


# ---------------------------------------------------------------------------
# 고정 입력 로더
# ---------------------------------------------------------------------------
INPUT_KEYS = ("feat", "sam", "feature_cols", "sam_raw_close", "live_row", "prediction_date", "last_bar",
              "versions", "data_snapshot_hash", "macro_active", "bootstrap_b", "quick_mode", "vol_model",
              "band_coverage", "price_forecast_stats")


def extract_inputs(ns):
    """노트북 네임스페이스에서 중기 실험이 읽는 것만 뽑는다(함수는 forecast_utils 에서 가져온다)."""
    return {
        "feat": ns["feat"], "sam": ns["sam"], "feature_cols": list(ns["feature_cols"]),
        "sam_raw_close": ns["sam_raw_close"], "live_row": ns["live_row"],
        "prediction_date": pd.Timestamp(ns["prediction_date"]), "last_bar": pd.Timestamp(ns["last_samsung_date"]),
        "versions": dict(ns.get("VERSIONS") or {}), "data_snapshot_hash": ns.get("DATA_SNAPSHOT_HASH"),
        "macro_active": bool(ns.get("MACRO_ACTIVE")), "bootstrap_b": int(ns.get("BOOTSTRAP_B", 2000)),
        "quick_mode": bool(ns.get("QUICK_MODE")), "vol_model": ns.get("VOL_MODEL", "simple"),
        "band_coverage": float(ns.get("BAND_COVERAGE", COVERAGE)),
        "price_forecast_stats": {k: dict(v) for k, v in (ns.get("price_forecast_stats") or {}).items()},
    }


def inputs_path(storage, target, mode, dhash):
    return Path(storage) / target / f"inputs_{mode}_{dhash}.pkl"


def load_inputs(target, mode, storage, run_notebook_fn=None, refresh=False):
    """고정 입력. 같은 data_hash 의 캐시가 있으면 노트북을 다시 돌리지 않는다."""
    snapshot = snapshot_paths(storage, target)
    if not snapshot:
        raise SystemExit(f"{storage}/{target}/data_cache 에 고정 스냅샷이 없습니다. "
                         "runs/model_improvement/P00/<target>/{data_cache,macro_cache,macro_fallback,macro_snapshots} 를 복사하세요.")
    dhash = data_hash(snapshot)
    path = inputs_path(storage, target, mode, dhash)
    if path.is_file() and not refresh:
        with open(path, "rb") as handle:
            inputs = pickle.load(handle)
        if inputs.get("data_hash") == dhash and all(k in inputs for k in INPUT_KEYS):
            inputs["from_cache"] = True
            return inputs
    if run_notebook_fn is None:
        from run_notebook import run_notebook as run_notebook_fn
    ns = run_notebook_fn(Path(storage) / target, targets=target, quick=(mode == "quick"), use_cache=True)[target]
    inputs = extract_inputs(ns)
    inputs.update(data_hash=dhash, mode=mode, target=target, notebook_commit=code_commit(),
                  saved_at_utc=datetime.now(timezone.utc).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "wb") as handle:
        pickle.dump(inputs, handle, protocol=pickle.HIGHEST_PROTOCOL)
    replace_with_retry(tmp, path)
    inputs["from_cache"] = False
    return inputs


# ---------------------------------------------------------------------------
# 설계 행렬·부트스트랩 — 노트북과 같은 함수·같은 규칙
# ---------------------------------------------------------------------------
def price_design(inputs, horizon):
    reg, _ = fu.price_design_frame(inputs["feat"], inputs["sam"].index, inputs["feature_cols"],
                                   inputs["sam_raw_close"], inputs["feat"]["sam_vol_20"], horizon)
    return reg, list(inputs["feature_cols"])


def month_blocks(date_index):
    key = pd.PeriodIndex(pd.DatetimeIndex(date_index), freq="M")
    return [np.where(key == m)[0] for m in key.unique()]


def month_block_ci(date_index, stat_fn, b=2000, seed=SEED, alpha=0.05):
    """노트북 block_bootstrap_ci 와 같은 달력 월 블록 부트스트랩(같은 b·seed 면 같은 값)."""
    blocks = month_blocks(date_index)
    if not blocks:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(b):
        pick = rng.integers(0, len(blocks), len(blocks))
        idx = np.concatenate([blocks[i] for i in pick])
        try:
            draws.append(stat_fn(idx))
        except Exception:
            continue
    if not draws:
        return (np.nan, np.nan)
    return tuple(float(v) for v in np.percentile(draws, [100 * alpha / 2, 100 * (1 - alpha / 2)]))


def contiguous_block_ci(n, stat_fn, block_len, b=2000, seed=SEED, alpha=0.05):
    """연속 위치 블록(길이 block_len) 복원추출 부트스트랩. stat_fn(row_positions) -> 스칼라."""
    if n <= 0:
        return (np.nan, np.nan)
    block_len = max(1, min(int(block_len), n))
    n_blocks = int(np.ceil(n / block_len))
    starts_max = n - block_len
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(b):
        starts = rng.integers(0, starts_max + 1, n_blocks)
        idx = np.concatenate([np.arange(s, s + block_len) for s in starts])[:n]
        try:
            draws.append(stat_fn(idx))
        except Exception:
            continue
    if not draws:
        return (np.nan, np.nan)
    return tuple(float(v) for v in np.percentile(draws, [100 * alpha / 2, 100 * (1 - alpha / 2)]))


# ---------------------------------------------------------------------------
# 진단 도구
# ---------------------------------------------------------------------------
def interval_score(y, lower, upper, coverage=COVERAGE):
    """Gneiting-Raftery 구간 점수(작을수록 좋다). 폭 + 벗어난 만큼의 벌점(2/alpha)."""
    alpha = 1 - coverage
    y, lower, upper = (np.asarray(a, dtype=float) for a in (y, lower, upper))
    return (upper - lower) + (2 / alpha) * np.clip(lower - y, 0, None) + (2 / alpha) * np.clip(y - upper, 0, None)


def maturity_positions(reg_index, sam_index, horizon):
    """설계 행렬 각 행의 (원본 봉 위치, 라벨 만기 봉 위치). 휴장일은 봉 위치로 자연히 건너뛴다."""
    pos = np.asarray(sam_index.get_indexer(reg_index))
    if (pos < 0).any():
        raise ValueError("설계 행렬에 원본 봉에 없는 날짜가 있다")
    return pos, pos + horizon - 1


def purge_check(reg_index, sam_index, horizon, splits):
    """날짜 기준 purge 검사. 학습 라벨의 만기 종가가 시험 행의 정보 마감(전일 종가)보다 늦으면 위반.

    splits: [(train_positions, test_positions)] — reg 행 위치. 위반 목록을 돌려준다(비어 있으면 통과).
    """
    pos, mature = maturity_positions(reg_index, sam_index, horizon)
    if mature.max() >= len(sam_index):
        raise ValueError("만기 봉이 원본 범위를 넘는 행이 설계 행렬에 남아 있다(미래 라벨)")
    violations = []
    for k, (train, test) in enumerate(splits):
        if len(train) == 0 or len(test) == 0:
            continue
        train_mature = sam_index[mature[np.asarray(train)].max()]
        test_origin = sam_index[pos[np.asarray(test)].min() - 1]
        if train_mature > test_origin:
            violations.append({"split": k, "train_label_matures": str(train_mature.date()),
                               "test_origin": str(test_origin.date())})
    return violations


def calibration_splits(n, horizon):
    """forecast_utils.calibrate_price_forecast 와 같은 보정/선택/평가 구간(OOF 행 위치)."""
    cut1, cut2, gap = n // 2, n * 3 // 4, horizon - 1
    return (np.arange(max(0, cut1 - gap)), np.arange(cut1, max(cut1, cut2 - gap)), np.arange(cut2, n))


def boundary_anomalies(sam, horizon, future_return):
    """분할·배당·결측 경계. 원본 종가의 하루 변동이 30%를 넘거나 수정/원본 비율이 하루에 5% 넘게 뛰면 기록."""
    raw, adj = sam["close"].astype(float), sam["adj_close"].astype(float)
    daily = raw.pct_change().abs()
    ratio_jump = (adj / raw).pct_change().abs()
    out = {
        "max_abs_daily_raw_return": float(daily.max()),
        "days_abs_daily_return_over_30pct": int((daily > .3).sum()),
        "max_adj_raw_ratio_jump": float(ratio_jump.max()),
        "days_adj_raw_ratio_jump_over_5pct": int((ratio_jump > .05).sum()),
        "max_abs_future_return": float(np.nanmax(np.abs(future_return))),
        "n_zero_volume_days": int((sam["volume"] <= 0).sum()) if "volume" in sam else None,
    }
    out["flag"] = bool(out["days_abs_daily_return_over_30pct"] or out["days_adj_raw_ratio_jump_over_5pct"])
    return out


def regime_table(sigma_cal, sigma_eval, y_eval, raw_eval, shrunk_eval, coverage_hit):
    """보정 구간의 sigma 3분위로 평상시/급등락 구간을 미리 고정하고 평가 구간 오차를 나눈다."""
    q1, q2 = np.quantile(sigma_cal, [1 / 3, 2 / 3])
    rows = []
    for name, mask in (("low", sigma_eval <= q1), ("mid", (sigma_eval > q1) & (sigma_eval <= q2)),
                       ("high", sigma_eval > q2)):
        if mask.sum() == 0:
            rows.append({"regime": name, "n": 0})
            continue
        rows.append({"regime": name, "n": int(mask.sum()), "sigma_cut_low": float(q1), "sigma_cut_high": float(q2),
                     "zero_mae": float(np.abs(y_eval[mask]).mean()),
                     "raw_mae": float(np.abs(y_eval[mask] - raw_eval[mask]).mean()),
                     "shrunk_mae": float(np.abs(y_eval[mask] - shrunk_eval[mask]).mean()),
                     "interval_coverage": float(coverage_hit[mask].mean())})
    return rows


def nonoverlap_table(evaluation, y, raw, shrunk, horizon):
    """h거래일마다 하나씩 뽑은 비중첩 부분집합을 모든 시작 offset 에 대해 진단한다. 좋은 offset 만 고르지 않는다."""
    rows = []
    for offset in range(horizon):
        idx = evaluation[offset::horizon]
        rows.append({"offset": offset, "n": int(len(idx)),
                     "raw_minus_zero": float((np.abs(y[idx] - raw[idx]) - np.abs(y[idx])).mean()),
                     "shrunk_minus_zero": float((np.abs(y[idx] - shrunk[idx]) - np.abs(y[idx])).mean())})
    return rows


def ledger_summary(target, horizon, last_bar_date, ledger_root=ROOT / "forecast_history"):
    """공식 원장의 h일 가격 예측 현황. 만기 도래·보류·미도래·결측을 구분한다(원장은 읽기만 한다)."""
    path = Path(ledger_root) / target / "forecast_log.csv"
    if not path.is_file():
        return {"n": 0, "note": "원장 없음"}
    log = pd.read_csv(path)
    rows = log[(log.get("kind", "direction") == "price") & (log["horizon_days"] == horizon)]
    matured = rows[pd.to_datetime(rows["target_date"]) <= pd.Timestamp(last_bar_date)]
    return {
        "n": int(len(rows)), "n_prospective": int(rows["is_prospective"].astype(str).str.lower().eq("true").sum()),
        "n_issued": int((rows["signal"] == "있음").sum()), "n_held": int((rows["signal"] == "없음").sum()),
        "n_matured": int(len(matured)), "n_scored": int((rows["status"] == "scored").sum()),
        "n_pending": int((rows["status"] == "pending").sum()),
        "first_prediction_date": str(rows["prediction_date"].min()) if len(rows) else None,
        "first_target_date": str(rows["target_date"].min()) if len(rows) else None,
        "snapshot_last_bar": str(pd.Timestamp(last_bar_date).date()),
    }


def failure_type(stats, eval_raw_ci, purge_violations, boundary):
    """실패 원인 분류. 원인 미확인은 가설로 표시한다."""
    kinds = []
    if purge_violations or boundary.get("flag"):
        kinds.append("데이터·채점 결함")
    lo, hi = eval_raw_ci
    if not stats["beats_baseline"]:
        if np.isfinite(hi) and hi < 0:
            kinds.append("과도한 축소 의심(가설: 선택 구간은 기각했지만 평가 구간 raw 는 유의하게 우위)")
        else:
            kinds.append("신호 부족(가설: raw 예측이 현재가 유지를 이기지 못함)")
    cov = stats["band_coverage_realized"]
    if abs(cov - COVERAGE) > .08:
        kinds.append(f"구간 오차(실현 포함률 {cov:.2f} vs 목표 {COVERAGE:.2f})")
    return kinds or ["해당 없음(현행 발행 조건 통과)"]


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 평가 계약 — 개발 외부 폴드(6개월) + 잠금(마지막 12개월) + 내부 3구간
# ---------------------------------------------------------------------------
def evaluation_folds(reg_index, sam_index, horizon, first_test=FIRST_TEST, test_months=TEST_MONTHS,
                     lock_months=LOCK_MONTHS, min_train_rows=MIN_TRAIN_ROWS, inner_blocks=INNER_BLOCKS,
                     inner_months=INNER_MONTHS, min_test_rows=MIN_TEST_ROWS):
    """날짜 기준 폴드. 학습 행은 라벨 만기 종가가 시험 시작일보다 앞선 행만(purge).

    개발 폴드는 first_test 부터 6개월씩, 잠금은 라벨이 있는 마지막 lock_months 개월(개발 폴드에서 제외;
    설계 행렬의 마지막 행이 h-1 봉 앞이므로 잠금 시작일은 지평마다 며칠 다르다). 내부 선택 구간은
    각 외부 폴드의 과거에서만 만든다(마지막 inner_blocks × inner_months 개월, 각 블록도 purge). 학습 행이
    min_train_rows 미만인 폴드·내부 블록은 사유와 함께 제외로 표시한다(기준을 낮추지 않는다).
    """
    dates = pd.DatetimeIndex(reg_index)
    pos, mature = maturity_positions(dates, sam_index, horizon)
    mature_dates = pd.DatetimeIndex(sam_index[mature])
    last = dates[-1]
    lock_start = (last - pd.DateOffset(months=lock_months) + pd.Timedelta(days=1)).normalize()

    def block(name, t0, t1):
        test = np.flatnonzero((dates >= t0) & (dates < t1))
        train = np.flatnonzero(mature_dates < t0)
        entry = {"name": name, "test_start": str(t0.date()), "test_end": str((t1 - pd.Timedelta(days=1)).date()),
                 "train_rows": int(len(train)), "test_rows": int(len(test)), "train": train, "test": test}
        if len(test) == 0:
            entry["excluded"] = "시험 행 없음"
        elif len(test) < min_test_rows and name.startswith("dev"):
            entry["excluded"] = f"시험 행 {len(test)} < {min_test_rows}(자료 끝에서 잘린 폴드)"
        elif len(train) < min_train_rows:
            entry["excluded"] = f"학습 행 {len(train)} < {min_train_rows}"
        entry["train_start"] = str(dates[train[0]].date()) if len(train) else None
        entry["train_end"] = str(dates[train[-1]].date()) if len(train) else None
        return entry

    folds = []
    t0 = pd.Timestamp(first_test)
    while t0 < lock_start:
        t1 = min(t0 + pd.DateOffset(months=test_months), lock_start)
        entry = block(f"dev_{len(folds) + 1:02d}", t0, t1)
        entry["inner"] = []
        for k in range(1, inner_blocks + 1):
            v1, v0 = t0 - pd.DateOffset(months=inner_months * (k - 1)), t0 - pd.DateOffset(months=inner_months * k)
            inner = block(f"inner_{k}", v0, v1)
            inner["train"] = inner["train"][mature_dates[inner["train"]] < v0]     # 이미 purge 됨(명시)
            entry["inner"].append(inner)
        folds.append(entry)
        t0 = t1
    lock = block("lock", lock_start, last + pd.Timedelta(days=1))
    lock["inner"] = []
    folds.append(lock)
    return folds, lock_start


def fold_rows(folds, target, horizon):
    rows = []
    for f in folds:
        rows.append({"target": target, "horizon": horizon, "fold": f["name"], "test_start": f["test_start"],
                     "test_end": f["test_end"], "train_start": f["train_start"], "train_end": f["train_end"],
                     "train_rows": f["train_rows"], "test_rows": f["test_rows"], "excluded": f.get("excluded", ""),
                     "inner_blocks_ok": sum(1 for i in f.get("inner", []) if not i.get("excluded")),
                     "inner_blocks": len(f.get("inner", []))})
    return rows


# ---------------------------------------------------------------------------
# M00
# ---------------------------------------------------------------------------
def m00_config(mode):
    return {"task": "M00", "mode": mode, "horizons": list(HORIZONS),
            "model": "StandardScaler+Ridge(alpha=1e4), target=future_return/sigma_simple",
            "vol_model": "simple", "coverage": COVERAGE, "n_splits": 3 if mode == "quick" else 5,
            "bootstrap_b": 400 if mode == "quick" else 2000, "contiguous_block": "max(20, 2h)", "seed": SEED,
            "calibration": "OOF 50% 보정 / 25% 신호 선택 / 25% 평가 (forecast_utils.calibrate_price_forecast)"}


def m01_config(mode):
    return {"task": "M01", "mode": mode, "horizons": list(HORIZONS), "first_test": FIRST_TEST,
            "test_months": TEST_MONTHS, "lock_months": LOCK_MONTHS, "min_train_rows": MIN_TRAIN_ROWS,
            "inner_blocks": INNER_BLOCKS, "inner_months": INNER_MONTHS, "min_test_rows": MIN_TEST_ROWS, "seed": SEED,
            "purge": "학습 라벨 만기 종가 < 시험 시작일(날짜 기준), 폴드·내부 블록 모두"}


def analyse_horizon(inputs, target, horizon, mode, storage, tag="M00"):
    """한 조합(target, horizon)의 기준선·진단. 노트북 통계와 동등성을 확인한다."""
    reg, feature_cols = price_design(inputs, horizon)
    sam = inputs["sam"]
    X = reg[feature_cols].to_numpy(dtype=np.float32)
    y = reg["future_return"].to_numpy(dtype=np.float64)
    sigma = reg["sigma_simple"].to_numpy(dtype=np.float64)
    n_splits = 3 if mode == "quick" else 5
    oof, folds = fu.price_oof_predictions(X, y, sigma, horizon, fu.make_price_model(), n_splits)
    mask = ~np.isnan(oof)
    b = int(inputs["bootstrap_b"])
    ci_fn = lambda dates, fn: month_block_ci(dates, fn, b=b)      # noqa: E731 — 노트북과 같은 b·seed
    stats = fu.calibrate_price_forecast(y[mask], oof[mask], sigma[mask], reg.index[mask], horizon,
                                        ci_fn, coverage=inputs["band_coverage"])
    notebook_stats = inputs["price_forecast_stats"].get(HORIZON_LABELS[horizon], {})
    equivalence = {}
    for key in ("raw_model_mae", "zero_baseline_mae", "shrunk_model_mae", "oof_slope", "band_q",
                "band_coverage_realized", "n_oof", "n_evaluation", "beats_baseline"):
        mine, theirs = stats.get(key), notebook_stats.get(key)
        equivalence[key] = {"runner": mine, "notebook": theirs,
                            "equal": bool(theirs is not None and np.isclose(float(mine), float(theirs), rtol=0, atol=1e-9))}
    equivalent = all(v["equal"] for v in equivalence.values())

    ym, om, sm, dm = y[mask], oof[mask], sigma[mask], reg.index[mask]
    cal, gate, evaluation = calibration_splits(len(ym), horizon)
    slope, q = stats["oof_slope"], stats["band_q"]
    shrunk = slope * om
    raw_eval, shrunk_eval, y_eval, s_eval = om[evaluation], shrunk[evaluation], ym[evaluation], sm[evaluation]
    lower, upper = shrunk_eval - q * s_eval, shrunk_eval + q * s_eval
    hit = (y_eval >= lower) & (y_eval <= upper)
    current_close = float(inputs["sam_raw_close"].iloc[-1])

    def paired(diff, dates, label):
        block = max(20, 2 * horizon)
        lo_m, hi_m = ci_fn(pd.DatetimeIndex(dates), lambda i: float(diff[i].mean()))
        lo_c, hi_c = contiguous_block_ci(len(diff), lambda i: float(diff[i].mean()), block, b=b)
        base = {"target": target, "horizon": horizon, "comparison": label, "metric": "mae_return",
                "delta": float(diff.mean()), "common_n": int(len(diff)), "calibrated": "raw" not in label}
        return [dict(base, ci_lo=float(lo_m), ci_hi=float(hi_m), block="month"),
                dict(base, ci_lo=lo_c, ci_hi=hi_c, block=f"contiguous_{block}")]

    eval_dates = dm[evaluation]
    raw_diff = np.abs(y_eval - raw_eval) - np.abs(y_eval)
    shrunk_diff = np.abs(y_eval - shrunk_eval) - np.abs(y_eval)
    comparisons = paired(raw_diff, eval_dates, "current_ridge_raw - hold_current")
    comparisons += paired(shrunk_diff, eval_dates, "current_ridge_calibrated - hold_current")
    eval_raw_ci = (comparisons[0]["ci_lo"], comparisons[0]["ci_hi"])

    fold_splits = [(np.arange(f["train_pos"][0], f["train_pos"][1] + 1), np.arange(f["test_pos"][0], f["test_pos"][1] + 1))
                   for f in folds]
    mask_pos = np.flatnonzero(mask)
    stage_splits = [(mask_pos[cal], mask_pos[gate]), (mask_pos[gate], mask_pos[evaluation]), (mask_pos[cal], mask_pos[evaluation])]
    violations = purge_check(reg.index, sam.index, horizon, fold_splits + stage_splits)
    boundary = boundary_anomalies(sam, horizon, reg["future_return"].to_numpy())
    kinds = failure_type(stats, eval_raw_ci, violations, boundary)

    raw_dir = Path(storage) / target
    raw_dir.mkdir(parents=True, exist_ok=True)
    pos, mature = maturity_positions(reg.index, sam.index, horizon)
    stage = np.full(len(ym), "", dtype=object)
    stage[cal], stage[gate], stage[evaluation] = "calibration", "gate", "evaluation"
    oof_frame = pd.DataFrame({"prediction_date": dm, "origin_date": sam.index[pos[mask] - 1],
                              "target_date": sam.index[mature[mask]], "y": ym, "oof_raw": om, "sigma": sm, "stage": stage})
    oof_path = raw_dir / f"{tag}_h{horizon}_{mode}_oof.csv"
    oof_frame.to_csv(oof_path, index=False, lineterminator="\n")

    def metric_row(candidate, stage_name, center, n, extra=None):
        err = y_eval - center if center is not None else y_eval
        row = {"target": target, "horizon": horizon, "candidate": candidate, "fold": "all", "evaluation_stage": stage_name,
               "n": int(n), "mae": float(np.abs(err).mean()), "rmse": float(np.sqrt(np.mean(err ** 2))),
               "mae_krw": float(np.abs(err).mean() * current_close),
               "log_loss": "", "brier": "", "balanced_accuracy": "", "metric_note": "방향 확률 모델 없음(M04에서)"}
        row.update(extra or {})
        return row
    metrics = [
        metric_row("hold_current", "evaluation", None, len(y_eval)),
        metric_row("current_ridge_raw", "evaluation", raw_eval, len(y_eval)),
        metric_row("current_ridge_calibrated", "evaluation", shrunk_eval, len(y_eval),
                   {"interval_score": float(interval_score(y_eval, lower, upper).mean()),
                    "interval_coverage": float(hit.mean()), "mean_width": float((upper - lower).mean()),
                    "issuance_rate": 1.0 if stats["beats_baseline"] else 0.0, "oof_slope": slope, "band_q": q}),
        metric_row("current_ridge_issued", "evaluation", shrunk_eval if stats["beats_baseline"] else np.zeros_like(y_eval), len(y_eval),
                   {"issuance_rate": 1.0 if stats["beats_baseline"] else 0.0,
                    "note": "발행 중심값. 신호 없음이면 현재가(0% 수익률)"}),
    ]
    for f in folds:
        te = np.arange(f["test_pos"][0], f["test_pos"][1] + 1)
        metrics.append({"target": target, "horizon": horizon, "candidate": "current_ridge_raw", "fold": f["fold"],
                        "evaluation_stage": "oof_fold", "n": int(len(te)), "mae": float(np.abs(y[te] - oof[te]).mean()),
                        "rmse": float(np.sqrt(np.mean((y[te] - oof[te]) ** 2))), "zero_mae": float(np.abs(y[te]).mean()),
                        "train_rows": f["train_rows"], "train_start": str(reg.index[f["train_pos"][0]].date()),
                        "train_end": str(reg.index[f["train_pos"][1]].date()), "test_start": str(reg.index[te[0]].date()),
                        "test_end": str(reg.index[te[-1]].date())})

    summary = {
        "target": target, "horizon": horizon, "n_design_rows": int(len(reg)), "n_features": len(feature_cols),
        "n_macro_features": int(sum(c.startswith("macro_") for c in feature_cols)),
        "design_start": str(reg.index[0].date()), "design_end": str(reg.index[-1].date()),
        "har_warmup_rows_dropped": int(len(sam) - len(reg) - (horizon - 1)),
        "information_cutoff": "행 d의 특징은 d-1 종가까지(예측일 아침 생성). 라벨은 d-1 종가 대비 d+h-1 원본 종가",
        "n_oof": int(mask.sum()), "stages": {"calibration": int(len(cal)), "gate": int(len(gate)), "evaluation": int(len(evaluation)),
                                             "evaluation_start": str(eval_dates[0].date()), "evaluation_end": str(eval_dates[-1].date())},
        "notebook_equivalence": equivalence, "equivalent": equivalent, "inputs_from_cache": bool(inputs.get("from_cache")),
        "stats": {k: (float(v) if isinstance(v, (int, float, np.floating, np.integer)) and not isinstance(v, bool) else v)
                  for k, v in stats.items()},
        "zero_mae_evaluation": float(np.abs(y_eval).mean()), "eval_raw_vs_zero_ci_month": list(eval_raw_ci),
        "regimes": regime_table(sm[cal], s_eval, y_eval, raw_eval, shrunk_eval, hit),
        "nonoverlap": nonoverlap_table(evaluation, ym, om, shrunk, horizon),
        "purge_violations": violations, "boundary": boundary,
        "ledger": ledger_summary(target, horizon, sam.index[-1]),
        "failure_types": kinds, "features": feature_cols,
        "oof_file": str(oof_path), "oof_sha256": file_sha256(oof_path),
    }
    return summary, metrics, comparisons


def unit_rows_path(state, target, horizon):
    return state.run_dir / f"rows_{target}_h{horizon}.json"


def save_unit_rows(state, target, horizon, metrics, comparisons):
    """단위 하나의 지표·비교 행. 재개 때 완료 단위의 행을 metrics.csv 에 다시 넣기 위해 남긴다."""
    write_json(unit_rows_path(state, target, horizon), {"metrics": metrics, "comparisons": comparisons})


def collect_rows(state, target):
    """완료된 모든 단위의 행을 지평 순서로 모은다."""
    from run_model_improvement import read_json
    metrics, comparisons = [], []
    for horizon in HORIZONS:
        payload = read_json(unit_rows_path(state, target, horizon))
        if payload:
            metrics += payload.get("metrics", [])
            comparisons += payload.get("comparisons", [])
    return metrics, comparisons


def artifacts_intact(state, unit, target, horizon):
    """완료 표시가 있어도 요약·행·OOF 파일이 없거나 해시가 다르면 False."""
    result = state.results().get(unit) or {}
    summary_path = state.run_dir / f"summary_{target}_h{horizon}.json"
    if not summary_path.is_file() or not unit_rows_path(state, target, horizon).is_file():
        return False
    oof_file, expected = result.get("oof_file"), result.get("oof_sha256")
    if not oof_file or not expected or not Path(oof_file).is_file():
        return False
    return file_sha256(oof_file) == expected


def record_inputs(state, inputs):
    state.manifest["notebook"] = {
        "data_snapshot_hash": inputs.get("data_snapshot_hash"), "versions": inputs.get("versions"),
        "prediction_date": str(inputs["prediction_date"].date()), "last_bar": str(inputs["last_bar"].date()),
        "macro_active": inputs.get("macro_active"), "quick_mode": inputs.get("quick_mode"),
        "bootstrap_b": inputs.get("bootstrap_b"), "vol_model": inputs.get("vol_model"),
        "inputs_from_cache": bool(inputs.get("from_cache")), "notebook_commit": inputs.get("notebook_commit"),
    }


def run_m00(target, mode, storage, results_dir, state, run_notebook_fn=None):
    inputs = None
    for horizon in HORIZONS:
        unit = f"{target}:h{horizon}"
        if state.is_done(unit) and artifacts_intact(state, unit, target, horizon):
            print(f"  이미 완료된 단위 건너뜀: {unit}")
            continue
        if state.is_done(unit):
            print(f"  완료 표시는 있지만 산출물이 없거나 해시가 다릅니다. 다시 계산: {unit}")
        if inputs is None:
            inputs = load_inputs(target, mode, storage, run_notebook_fn)
            record_inputs(state, inputs)
        summary, metrics, comparisons = analyse_horizon(inputs, target, horizon, mode, storage, tag="M00")
        write_json(state.run_dir / f"summary_{target}_h{horizon}.json", summary)
        save_unit_rows(state, target, horizon, metrics, comparisons)
        state.mark(unit, {"equivalent": summary["equivalent"], "failure_types": summary["failure_types"],
                          "beats_baseline": summary["stats"]["beats_baseline"], "oof_sha256": summary["oof_sha256"],
                          "oof_file": summary["oof_file"]})
        print(f"  {unit}: 동등성={summary['equivalent']} 발행={summary['stats']['beats_baseline']} "
              f"raw MAE {summary['stats']['raw_model_mae']:.5f} vs 유지 {summary['zero_mae_evaluation']:.5f} · "
              + " / ".join(summary["failure_types"]))
    return collect_rows(state, target)


def run_m01(target, mode, storage, results_dir, state, run_notebook_fn=None):
    """평가 계약을 네 조합(이 종목의 두 지평)에 적용하고 고정 입력 로더로 기준선이 재현되는지 확인한다."""
    inputs = None
    for horizon in HORIZONS:
        unit = f"{target}:h{horizon}"
        if state.is_done(unit) and artifacts_intact(state, unit, target, horizon):
            print(f"  이미 완료된 단위 건너뜀: {unit}")
            continue
        if inputs is None:
            inputs = load_inputs(target, mode, storage, run_notebook_fn)
            record_inputs(state, inputs)
        reg, _ = price_design(inputs, horizon)
        folds, lock_start = evaluation_folds(reg.index, inputs["sam"].index, horizon)
        splits = [(f["train"], f["test"]) for f in folds if not f.get("excluded")]
        splits += [(i["train"], i["test"]) for f in folds for i in f.get("inner", []) if not i.get("excluded")]
        violations = purge_check(reg.index, inputs["sam"].index, horizon, splits)
        # 고정 입력에서 기준선(M00 계산)이 그대로 나오는지 — 로더의 동등성 증거
        summary, metrics, comparisons = analyse_horizon(inputs, target, horizon, mode, storage, tag="M01")
        summary.update(contract={"lock_start": str(lock_start.date()), "first_test": FIRST_TEST, "test_months": TEST_MONTHS,
                                 "lock_months": LOCK_MONTHS, "min_train_rows": MIN_TRAIN_ROWS,
                                 "dev_folds": sum(1 for f in folds if f["name"].startswith("dev") and not f.get("excluded")),
                                 "dev_folds_excluded": [f["name"] + ": " + f["excluded"] for f in folds if f.get("excluded")],
                                 "inner_blocks_excluded": [f["name"] + "/" + i["name"] + ": " + i["excluded"]
                                                           for f in folds for i in f.get("inner", []) if i.get("excluded")],
                                 "purge_violations": violations},
                       folds=[{k: v for k, v in f.items() if k not in ("train", "test", "inner")}
                              | {"inner": [{k: v for k, v in i.items() if k not in ("train", "test")} for i in f.get("inner", [])]}
                              for f in folds])
        write_json(state.run_dir / f"summary_{target}_h{horizon}.json", summary)
        save_unit_rows(state, target, horizon, fold_rows(folds, target, horizon), comparisons)
        state.mark(unit, {"equivalent": summary["equivalent"], "purge_violations": len(violations),
                          "dev_folds": summary["contract"]["dev_folds"], "lock_start": summary["contract"]["lock_start"],
                          "oof_sha256": summary["oof_sha256"], "oof_file": summary["oof_file"],
                          "inputs_from_cache": bool(inputs.get("from_cache"))})
        print(f"  {unit}: 개발 폴드 {summary['contract']['dev_folds']} · 잠금 {summary['contract']['lock_start']}~ · "
              f"purge 위반 {len(violations)} · 기준선 동등성={summary['equivalent']} · 입력 캐시={bool(inputs.get('from_cache'))}")
    return collect_rows(state, target)


TASK_RUNNERS = {"M00": run_m00, "M01": run_m01}
TASK_CONFIGS = {"M00": m00_config, "M01": m01_config}


def execute(task, target, mode, storage, results_dir, resume, run_notebook_fn=None):
    if task not in TASKS:
        raise SystemExit(f"등록되지 않은 작업 ID입니다: {task}. 사용 가능: {', '.join(sorted(TASKS))}")
    if target not in SUPPORTED_TARGETS:
        raise SystemExit(f"지원하지 않는 종목입니다: {target}")
    if mode not in MODES:
        raise SystemExit(f"mode는 quick 또는 full 입니다: {mode}")
    os.environ["PREDICT_STOCK_PUBLISH"] = "false"       # 실험은 절대 발행하지 않는다
    storage, results_dir = Path(storage).resolve(), Path(results_dir).resolve()
    config = TASK_CONFIGS[task](mode)
    snapshot = snapshot_paths(storage, target)
    identity = {"task_id": task, "target": target, "mode": mode,
                "data_hash": data_hash(snapshot), "config_hash": config_hash(config)}
    if identity["data_hash"] == "nodata":
        raise SystemExit(f"{storage}/{target}/data_cache 에 고정 스냅샷이 없습니다.")
    state, resumed = start_run(results_dir, task, target, mode, identity, config,
                               extra={"storage": str(storage), "snapshot_files": [p.name for p in snapshot],
                                      "snapshot_sha256": {p.name: file_sha256(p) for p in snapshot}})
    print(f"[{task}] {target} / {mode} → {state.run_dir}  data_hash={identity['data_hash']} "
          f"config_hash={identity['config_hash']} {'(재개)' if resumed else '(신규)'}")
    try:
        metrics, comparisons = TASK_RUNNERS[task](target, mode, storage, results_dir, state, run_notebook_fn)
        if not metrics and not (state.run_dir / "metrics.csv").is_file():
            print("  완료 표시는 있는데 metrics.csv가 없습니다. 단위를 다시 계산합니다.")
            state.reset_units()
            metrics, comparisons = TASK_RUNNERS[task](target, mode, storage, results_dir, state, run_notebook_fn)
    except Exception as exc:
        state.fail(f"{target}:{task.lower()}", f"{type(exc).__name__}: {exc}")
        raise
    if metrics:
        write_metrics(state, metrics)
        write_metrics_named(state, comparisons, "comparisons.csv")
    state.finish()
    print(f"  완료 단위: {state.completed()}")
    return state


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", required=True)
    parser.add_argument("--target", required=True, choices=SUPPORTED_TARGETS)
    parser.add_argument("--horizon", type=int, choices=HORIZONS, help="(정보용) 두 지평을 항상 함께 계산한다")
    parser.add_argument("--mode", default="quick", choices=MODES)
    parser.add_argument("--storage", type=Path, default=ROOT / "runs" / "medium_horizon")
    parser.add_argument("--results", type=Path, default=ROOT / "experiments" / "medium_horizon")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    execute(args.task, args.target, args.mode, args.storage, args.results, args.resume)


if __name__ == "__main__":
    main()

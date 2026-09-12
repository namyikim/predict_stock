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
import json
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
         "M01": "평가 계약(외부 6개월 폴드·잠금 12개월·내부 3구간)과 고정 입력 로더 검증",
         "M02": "지평별 특징군 비교(현행 전체 vs 시세만 vs 월별 제외 vs 그룹 A 추가)",
         "M03": "Ridge 규제 강도·학습 창 내부 선택(alpha {100,1000,10000} × {5년, expanding})",
         "M04": "5·20일 직접 방향 확률 모델(규제 Logistic) vs 학습 구간 사전확률",
         "M05": "예측 구간(simple 내부 q / HAR / 최근 확정 잔차 252개)과 보류 정책 비교",
         "M06": "국내 반도체 패널 공동 학습(pooled Ridge) vs 같은 입력 단독 모델 vs 현행",
         "M07": "고정 후보의 잠금 평가 1회와 사전 예측 관찰 현황",
         "R07": "금리 커브 특징군(단기·장기 금리와 기울기) 비교 — 5·20일만"}
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


# ---------------------------------------------------------------------------
# M02 — 특징군 후보
# ---------------------------------------------------------------------------
# 그룹 A: 종목·KOSPI·동종·반도체 해외 자산의 20/60일 누적 수익률, 종목의 상대 수익률(5/20/60), 상대 변동성.
# 기존 열과 겹치는 것(5일 누적: sam_ret_5·kospi_ret_5·sox_ret_5…, sam_ret_20, *_vol_20)은 다시 만들지 않는다.
# 그룹 B(수급 비율)와 그룹 C(월별 변화율)는 기존 flow_*·macro_* 열이 이미 같은 정의라 후보를 만들지 않는다
# (summary 의 duplicates 에 기록). 대신 계획이 요구하는 '시세만' 비교와 노트북의 '월별 제외' 비교를 둔다.
GROUP_A_ASSETS = ("kospi", "peer", "sox", "nasdaq", "micron", "nvidia", "tsmc_adr", "korea_etf")
GROUP_A_WINDOWS = (20, 60)
GROUP_A_RELATIVE = ("kospi", "sox")
DUPLICATE_CANDIDATES = {
    "A": ["sam_ret_5", "sam_ret_20", "kospi_ret_5", "peer_ret_5", "sox_ret_5", "nasdaq_ret_5", "micron_ret_5",
          "nvidia_ret_5", "tsmc_adr_ret_5", "korea_etf_ret_5", "sam_vol_20", "kospi_vol_20", "peer_vol_20"],
    "B": ["flow_frgn_5", "flow_frgn_20", "flow_inst_5", "flow_frgn_1", "flow_frgn_streak", "flow_frgn_ratio_chg_20"],
    "C": ["macro_leading_change_1m", "macro_leading_change_3m", "macro_semiconductor_mom",
          "macro_semiconductor_yoy_change_1m", "macro_semiconductor_yoy_change_3m", "macro_semiconductor_yoy_3m"],
}
DROP_PREFIXES = {"market_only": ("macro_", "nsi_", "flow_"), "no_macro": ("macro_",)}
CANDIDATE_ORDER = ("current_full", "market_only", "no_macro", "full_plus_A")


def load_asset_close(storage, target, name):
    """고정 스냅샷의 자산 종가(수정 종가 우선). 자산 자체 거래일 인덱스를 유지한다."""
    path = Path(storage) / target / "data_cache" / f"{name}.parquet"
    if not path.is_file():
        return None
    frame = pd.read_parquet(path)
    frame.columns = [str(c).strip().lower().replace(" ", "_") for c in frame.columns]
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index)).tz_localize(None).normalize()
    col = "adj_close" if "adj_close" in frame.columns else "close"
    return frame[col].astype(float).dropna().sort_index()


def align_before(series, dates):
    """예측일 d 에는 d 보다 앞선 마지막 관측만 쓴다(같은 날 값은 쓰지 않는다). 6일 넘게 오래된 값은 결측."""
    dates = pd.DatetimeIndex(dates)
    left = pd.DataFrame({"date": dates.as_unit("ns"), "_order": np.arange(len(dates))}).sort_values("date")
    right = pd.DataFrame({"date": pd.DatetimeIndex(series.index).as_unit("ns"), "value": series.to_numpy()}).sort_values("date")
    joined = pd.merge_asof(left, right, on="date", direction="backward", allow_exact_matches=False,
                           tolerance=pd.Timedelta(days=6)).sort_values("_order")
    return pd.Series(joined["value"].to_numpy(), index=dates)


def group_a_features(sam_adj_close, asset_closes, dates, windows=GROUP_A_WINDOWS, relative=GROUP_A_RELATIVE):
    """그룹 A. 각 자산의 k거래일(자산 달력) 누적 수익률을 d 전 마지막 관측으로 정렬한다.

    sam_adj_close: 대상 종목 수정 종가(KRX 달력). asset_closes: {이름: 종가 시계열}. 반환 열 이름은 모두 새 것이다.
    """
    dates = pd.DatetimeIndex(dates)
    out = pd.DataFrame(index=dates)
    sam = pd.Series(sam_adj_close).astype(float).sort_index()
    sam_cum = {k: align_before(sam.pct_change(k).dropna(), dates) for k in (5,) + tuple(windows)}
    out["sam_cum_60"] = sam_cum[60] if 60 in sam_cum else np.nan
    sam_vol = align_before(sam.pct_change().rolling(20).std().dropna(), dates)
    for name, close in asset_closes.items():
        if close is None or close.empty:
            continue
        close = pd.Series(close).astype(float).sort_index()
        cum = {k: align_before(close.pct_change(k).dropna(), dates) for k in (5,) + tuple(windows)}
        for k in windows:
            out[f"{name}_cum_{k}"] = cum[k]
        if name in relative:
            for k in (5,) + tuple(windows):
                out[f"sam_rel_{name}_{k}"] = sam_cum[k] - cum[k]
            vol = align_before(close.pct_change().rolling(20).std().dropna(), dates)
            out[f"sam_relvol_{name}_20"] = sam_vol / vol.replace(0, np.nan)
            if name != "kospi":
                out[f"{name}_vol_20"] = vol
    return out.replace([np.inf, -np.inf], np.nan)


def candidate_columns(base_cols, group_a_cols):
    """후보 → 열 목록. 후보 이름은 CANDIDATE_ORDER 에 고정한다."""
    base = list(base_cols)
    return {
        "current_full": base,
        "market_only": [c for c in base if not c.startswith(DROP_PREFIXES["market_only"])],
        "no_macro": [c for c in base if not c.startswith(DROP_PREFIXES["no_macro"])],
        "full_plus_A": base + list(group_a_cols),
    }


def augment_inputs(inputs, storage, target):
    """feat 에 그룹 A 열을 붙인 사본과 새 열 이름을 돌려준다."""
    closes = {name: load_asset_close(storage, target, name) for name in GROUP_A_ASSETS}
    a = group_a_features(inputs["sam"]["adj_close"], closes, inputs["feat"].index)
    feat = inputs["feat"].copy()
    for col in a.columns:
        feat[col] = a[col]
    return feat, list(a.columns), {k: (v is not None and not v.empty) for k, v in closes.items()}


def fit_predict(template, X, z, sigma, train, test):
    from sklearn.base import clone
    model = clone(template).fit(X[train], z[train])
    return model.predict(X[test]) * sigma[test]


def inner_slope(pred, y):
    denom = float(np.sum(pred ** 2))
    return float(np.clip(np.sum(pred * y) / denom, 0., 1.)) if denom > 0 else 0.


def fold_candidate_predictions(reg, cols_by_candidate, folds, horizon, template):
    """개발 폴드마다 후보별 외부 예측(raw)·내부 기울기·내부 MAE 를 만든다. 모든 후보가 같은 행을 쓴다."""
    y = reg["future_return"].to_numpy(dtype=float)
    sigma = reg["sigma_simple"].to_numpy(dtype=float)
    z = y / np.maximum(sigma, 1e-6)
    matrices = {name: reg[cols].to_numpy(dtype=np.float32) for name, cols in cols_by_candidate.items()}
    dev = [f for f in folds if f["name"].startswith("dev") and not f.get("excluded")]
    records = []
    for f in dev:
        record = {"fold": f["name"], "test": f["test"], "candidates": {}}
        for name, X in matrices.items():
            raw = fit_predict(template, X, z, sigma, f["train"], f["test"])
            inner_pred, inner_y, inner_mae = [], [], []
            for block in f["inner"]:
                if block.get("excluded") or len(block["train"]) < MIN_TRAIN_ROWS:
                    continue
                p = fit_predict(template, X, z, sigma, block["train"], block["test"])
                inner_pred.append(p); inner_y.append(y[block["test"]])
                inner_mae.append(float(np.abs(y[block["test"]] - p).mean()))
            slope = inner_slope(np.concatenate(inner_pred), np.concatenate(inner_y)) if inner_pred else 0.
            record["candidates"][name] = {"raw": raw, "slope": slope, "inner_mae": inner_mae}
        records.append(record)
    return records, y, sigma


def select_by_inner(record, reference="current_full"):
    """내부 3구간 평균 raw MAE 가 가장 낮고 기준 후보를 2개 이상 구간에서 이기는 후보. 없으면 기준 유지."""
    ref = record["candidates"][reference]["inner_mae"]
    best, best_mean = reference, float(np.mean(ref)) if ref else np.inf
    for name, c in record["candidates"].items():
        if name == reference or not c["inner_mae"] or len(c["inner_mae"]) != len(ref):
            continue
        wins = sum(a < b for a, b in zip(c["inner_mae"], ref))
        mean = float(np.mean(c["inner_mae"]))
        if wins >= 2 and mean < best_mean:
            best, best_mean = name, mean
    return best


def m02_config(mode):
    return {"task": "M02", "mode": mode, "horizons": list(HORIZONS), "candidates": list(CANDIDATE_ORDER),
            "group_a": {"assets": list(GROUP_A_ASSETS), "windows": list(GROUP_A_WINDOWS), "relative": list(GROUP_A_RELATIVE)},
            "model": "StandardScaler+Ridge(alpha=1e4), target=future_return/sigma_simple", "vol_model": "simple",
            "folds": {"first_test": FIRST_TEST, "test_months": TEST_MONTHS, "lock_months": LOCK_MONTHS,
                      "min_train_rows": MIN_TRAIN_ROWS, "min_test_rows": MIN_TEST_ROWS, "inner_blocks": INNER_BLOCKS},
            "selection": "내부 3구간 평균 raw MAE 최소 + 현행을 2구간 이상 이길 때만 교체",
            "bootstrap_b": 400 if mode == "quick" else 2000, "seed": SEED}


def run_m02(target, mode, storage, results_dir, state, run_notebook_fn=None):
    inputs = None
    for horizon in HORIZONS:
        unit = f"{target}:h{horizon}"
        if state.is_done(unit) and artifacts_intact(state, unit, target, horizon):
            print(f"  이미 완료된 단위 건너뜀: {unit}")
            continue
        if inputs is None:
            inputs = load_inputs(target, mode, storage, run_notebook_fn)
            record_inputs(state, inputs)
            feat_aug, a_cols, available = augment_inputs(inputs, storage, target)
        base_cols = list(inputs["feature_cols"])
        cols_by_candidate = candidate_columns(base_cols, a_cols)
        union = base_cols + a_cols
        sam = inputs["sam"]
        reg, _ = fu.price_design_frame(feat_aug, sam.index, union, inputs["sam_raw_close"], feat_aug["sam_vol_20"], horizon)
        reg_base, _ = fu.price_design_frame(feat_aug, sam.index, base_cols, inputs["sam_raw_close"], feat_aug["sam_vol_20"], horizon)
        folds, lock_start = evaluation_folds(reg.index, sam.index, horizon)
        dev = [f for f in folds if f["name"].startswith("dev") and not f.get("excluded")]
        violations = purge_check(reg.index, sam.index, horizon, [(f["train"], f["test"]) for f in dev]
                                 + [(i["train"], i["test"]) for f in dev for i in f["inner"] if not i.get("excluded")])
        template = fu.make_price_model()
        records, y, sigma = fold_candidate_predictions(reg, cols_by_candidate, folds, horizon, template)
        if not records:
            raise SystemExit(f"{unit}: 학습 행 {MIN_TRAIN_ROWS} 이상인 개발 폴드가 없습니다(설계 행렬 {len(reg)}행, {reg.index[0].date()}~). 기준을 낮추지 않는다.")
        b = 400 if mode == "quick" else 2000

        test_idx = np.concatenate([r["test"] for r in records])
        y_dev, dates_dev = y[test_idx], reg.index[test_idx]
        preds = {}
        for name in CANDIDATE_ORDER:
            preds[name] = {"raw": np.concatenate([r["candidates"][name]["raw"] for r in records]),
                           "calibrated": np.concatenate([r["candidates"][name]["slope"] * r["candidates"][name]["raw"] for r in records])}
        selections = [{"fold": r["fold"], "selected": select_by_inner(r),
                       "inner_mae": {n: [round(v, 6) for v in c["inner_mae"]] for n, c in r["candidates"].items()},
                       "slope": {n: round(c["slope"], 4) for n, c in r["candidates"].items()}} for r in records]
        preds["inner_selected"] = {
            "raw": np.concatenate([r["candidates"][s["selected"]]["raw"] for r, s in zip(records, selections)]),
            "calibrated": np.concatenate([r["candidates"][s["selected"]]["slope"] * r["candidates"][s["selected"]]["raw"]
                                          for r, s in zip(records, selections)])}
        zero = np.abs(y_dev)
        block = max(20, 2 * horizon)

        def ci_pair(diff, label, metric):
            lo_m, hi_m = month_block_ci(dates_dev, lambda i: float(diff[i].mean()), b=b)
            lo_c, hi_c = contiguous_block_ci(len(diff), lambda i: float(diff[i].mean()), block, b=b)
            base = {"target": target, "horizon": horizon, "comparison": label, "metric": metric, "delta": float(diff.mean()),
                    "common_n": int(len(diff)), "calibrated": "calibrated" in metric}
            return [dict(base, ci_lo=lo_m, ci_hi=hi_m, block="month"), dict(base, ci_lo=lo_c, ci_hi=hi_c, block=f"contiguous_{block}")]

        metrics, comparisons = [], []
        ref_raw, ref_cal = preds["current_full"]["raw"], preds["current_full"]["calibrated"]
        metrics.append({"target": target, "horizon": horizon, "candidate": "hold_current", "fold": "dev_all",
                        "evaluation_stage": "dev_common", "n": int(len(y_dev)), "mae": float(zero.mean()),
                        "rmse": float(np.sqrt(np.mean(y_dev ** 2)))})
        for name, p in preds.items():
            err_raw, err_cal = np.abs(y_dev - p["raw"]), np.abs(y_dev - p["calibrated"])
            metrics.append({"target": target, "horizon": horizon, "candidate": name, "fold": "dev_all", "evaluation_stage": "dev_common",
                            "n": int(len(y_dev)), "mae": float(err_raw.mean()), "rmse": float(np.sqrt(np.mean((y_dev - p["raw"]) ** 2))),
                            "mae_calibrated": float(err_cal.mean()), "n_features": len(cols_by_candidate.get(name, [])) or "",
                            "mean_slope": float(np.mean([r["candidates"][name]["slope"] for r in records])) if name in cols_by_candidate else ""})
            for r in records:
                te = r["test"]; sel = name if name in cols_by_candidate else None
                rp = (r["candidates"][sel]["raw"] if sel else
                      r["candidates"][next(s["selected"] for s in selections if s["fold"] == r["fold"])]["raw"])
                metrics.append({"target": target, "horizon": horizon, "candidate": name, "fold": r["fold"], "evaluation_stage": "dev_fold",
                                "n": int(len(te)), "mae": float(np.abs(y[te] - rp).mean()), "zero_mae": float(np.abs(y[te]).mean())})
            if name != "current_full":
                comparisons += ci_pair(err_raw - np.abs(y_dev - ref_raw), f"{name} - current_full", "mae_return_raw")
                comparisons += ci_pair(err_cal - np.abs(y_dev - ref_cal), f"{name} - current_full", "mae_return_calibrated")
            comparisons += ci_pair(err_cal - zero, f"{name} - hold_current", "mae_return_calibrated")
            comparisons += ci_pair(err_raw - zero, f"{name} - hold_current", "mae_return_raw")

        # 후보별 전체 가용 행(후보 자신의 열만으로 결측 제거) — 공통 행과 비교해 '쉬운 날짜만 남았는지' 본다
        available_rows = {}
        for name, cols in cols_by_candidate.items():
            r_own, _ = fu.price_design_frame(feat_aug, sam.index, cols, inputs["sam_raw_close"], feat_aug["sam_vol_20"], horizon)
            f_own, _ = evaluation_folds(r_own.index, sam.index, horizon)
            d_own = [f for f in f_own if f["name"].startswith("dev") and not f.get("excluded")]
            yo, so = r_own["future_return"].to_numpy(float), r_own["sigma_simple"].to_numpy(float)
            Xo = r_own[cols].to_numpy(np.float32); zo = yo / np.maximum(so, 1e-6)
            po = np.concatenate([fit_predict(template, Xo, zo, so, f["train"], f["test"]) for f in d_own])
            to = np.concatenate([f["test"] for f in d_own])
            available_rows[name] = {"n": int(len(to)), "mae_raw": float(np.abs(yo[to] - po).mean()), "zero_mae": float(np.abs(yo[to]).mean())}

        raw_dir = Path(storage) / target
        raw_dir.mkdir(parents=True, exist_ok=True)
        oof_frame = pd.DataFrame({"prediction_date": dates_dev, "y": y_dev, "sigma": sigma[test_idx],
                                  **{f"raw_{n}": p["raw"] for n, p in preds.items()},
                                  **{f"cal_{n}": p["calibrated"] for n, p in preds.items()}})
        oof_path = raw_dir / f"M02_h{horizon}_{mode}_oof.csv"
        oof_frame.to_csv(oof_path, index=False, lineterminator="\n")

        sel_delta = next(c for c in comparisons if c["comparison"] == "inner_selected - current_full"
                         and c["metric"] == "mae_return_calibrated" and c["block"] == "month")
        counts = pd.Series([s["selected"] for s in selections]).value_counts().to_dict()
        summary = {
            "target": target, "horizon": horizon, "n_common_rows": int(len(reg)), "n_base_rows": int(len(reg_base)),
            "rows_excluded_by_group_a": int(len(reg_base) - len(reg)), "design_start": str(reg.index[0].date()),
            "candidates": {n: {"n_features": len(c), "columns": c} for n, c in cols_by_candidate.items()},
            "group_a_new_columns": a_cols, "group_a_assets_available": available, "duplicates_not_reimplemented": DUPLICATE_CANDIDATES,
            "dev_folds": [r["fold"] for r in records], "lock_start": str(lock_start.date()), "n_dev_rows": int(len(y_dev)),
            "purge_violations": violations, "selections": selections, "selection_counts": counts,
            "all_available_rows": available_rows,
            "inner_selected_vs_current_calibrated_month_ci": [sel_delta["ci_lo"], sel_delta["ci_hi"]],
            "fixed_features_for_m03": ("inner_selected 경로가 현행보다 유의하게 낫지 않음 → current_full 유지"
                                        if not (np.isfinite(sel_delta["ci_hi"]) and sel_delta["ci_hi"] < 0)
                                        else f"inner_selected 경로 우위 → 가장 자주 선택된 후보 {max(counts, key=counts.get)}"),
            "oof_file": str(oof_path), "oof_sha256": file_sha256(oof_path),
        }
        write_json(state.run_dir / f"summary_{target}_h{horizon}.json", summary)
        save_unit_rows(state, target, horizon, metrics, comparisons)
        state.mark(unit, {"oof_sha256": summary["oof_sha256"], "oof_file": summary["oof_file"],
                          "selection_counts": counts, "purge_violations": len(violations),
                          "fixed_features_for_m03": summary["fixed_features_for_m03"]})
        print(f"  {unit}: 공통 {len(reg)}행(그룹 A 로 {len(reg_base) - len(reg)}행 제외) · 개발 {len(records)}폴드 · 선택 {counts} · "
              f"inner_selected−현행(보정) {sel_delta['delta']:+.5f} [{sel_delta['ci_lo']:+.5f}, {sel_delta['ci_hi']:+.5f}] · purge {len(violations)}")
    return collect_rows(state, target)


# ---------------------------------------------------------------------------
# M03 — 규제 강도 × 학습 창
# ---------------------------------------------------------------------------
ALPHA_CANDIDATES = (100., 1000., 10000.)
WINDOW_CANDIDATES = ("5y", "expanding")
CURRENT_SETTING = (10000., "expanding")


def setting_name(alpha, window):
    return f"a{int(alpha)}_{window}"


def window_rows(train, dates, anchor, window):
    """학습 창. expanding 은 purge 된 전체 과거, 5y 는 anchor(시험 시작일) 앞 5년만."""
    train = np.asarray(train)
    if window == "expanding":
        return train
    if window == "5y":
        start = pd.Timestamp(anchor) - pd.DateOffset(years=5)
        return train[pd.DatetimeIndex(dates[train]) >= start]
    raise ValueError(f"알 수 없는 학습 창: {window}")


def select_setting(inner_mae_by_setting):
    """내부 3구간 평균 raw MAE 최소. 동률(1e-12)이면 alpha 큰 쪽, 그다음 expanding."""
    def key(item):
        (alpha, window), maes = item
        return (round(float(np.mean(maes)), 12), -alpha, 0 if window == "expanding" else 1)
    return min(inner_mae_by_setting.items(), key=key)[0]


def fold_gate(inner_pred, inner_y, slope):
    """폴드 발행 판정: 내부 3구간 중 2구간 이상에서 보정 예측이 현재가 유지보다 MAE 가 낮아야 발행."""
    wins = 0
    for p, y in zip(inner_pred, inner_y):
        if np.abs(y - slope * p).mean() < np.abs(y).mean():
            wins += 1
    return wins >= 2 and len(inner_pred) >= 2


def m03_config(mode):
    return {"task": "M03", "mode": mode, "horizons": list(HORIZONS), "features": "current_full",
            "alphas": list(ALPHA_CANDIDATES), "windows": list(WINDOW_CANDIDATES), "current": list(CURRENT_SETTING),
            "selection": "내부 3구간 평균 raw MAE 최소, 동률이면 alpha 큰 쪽 → expanding",
            "gate": "내부 3구간 중 2구간 이상 보정 MAE < 유지 MAE 이면 발행",
            "folds": {"first_test": FIRST_TEST, "test_months": TEST_MONTHS, "lock_months": LOCK_MONTHS,
                      "min_train_rows": MIN_TRAIN_ROWS, "min_test_rows": MIN_TEST_ROWS, "inner_blocks": INNER_BLOCKS},
            "bootstrap_b": 400 if mode == "quick" else 2000, "seed": SEED}


def run_m03(target, mode, storage, results_dir, state, run_notebook_fn=None):
    import time
    inputs = None
    for horizon in HORIZONS:
        unit = f"{target}:h{horizon}"
        if state.is_done(unit) and artifacts_intact(state, unit, target, horizon):
            print(f"  이미 완료된 단위 건너뜀: {unit}")
            continue
        if inputs is None:
            inputs = load_inputs(target, mode, storage, run_notebook_fn)
            record_inputs(state, inputs)
        reg, cols = price_design(inputs, horizon)
        sam = inputs["sam"]
        folds, lock_start = evaluation_folds(reg.index, sam.index, horizon)
        dev = [f for f in folds if f["name"].startswith("dev") and not f.get("excluded")]
        if not dev:
            raise SystemExit(f"{unit}: 개발 폴드가 없습니다.")
        violations = purge_check(reg.index, sam.index, horizon, [(f["train"], f["test"]) for f in dev]
                                 + [(i["train"], i["test"]) for f in dev for i in f["inner"] if not i.get("excluded")])
        X = reg[cols].to_numpy(dtype=np.float32)
        y = reg["future_return"].to_numpy(dtype=float)
        sigma = reg["sigma_simple"].to_numpy(dtype=float)
        z = y / np.maximum(sigma, 1e-6)
        settings = [(a, w) for a in ALPHA_CANDIDATES for w in WINDOW_CANDIDATES]
        templates = {a: fu.make_price_model(alpha=a) for a in ALPHA_CANDIDATES}

        records = []
        for f in dev:
            rec = {"fold": f["name"], "test": f["test"], "settings": {}, "seconds": {}}
            for alpha, window in settings:
                t0 = time.perf_counter()
                train = window_rows(f["train"], reg.index, f["test_start"], window)
                raw = fit_predict(templates[alpha], X, z, sigma, train, f["test"])
                inner_pred, inner_y, inner_mae = [], [], []
                for block in f["inner"]:
                    if block.get("excluded"):
                        continue
                    btrain = window_rows(block["train"], reg.index, block["test_start"], window)
                    if len(btrain) < MIN_TRAIN_ROWS:
                        continue
                    p_ = fit_predict(templates[alpha], X, z, sigma, btrain, block["test"])
                    inner_pred.append(p_); inner_y.append(y[block["test"]]); inner_mae.append(float(np.abs(y[block["test"]] - p_).mean()))
                slope = inner_slope(np.concatenate(inner_pred), np.concatenate(inner_y)) if inner_pred else 0.
                issued = fold_gate(inner_pred, inner_y, slope)
                rec["settings"][(alpha, window)] = {"raw": raw, "slope": slope, "inner_mae": inner_mae, "issued": issued,
                                                     "train_rows": int(len(train)), "inner_blocks": len(inner_pred),
                                                     "mean_abs_raw": float(np.abs(raw).mean())}
                rec["seconds"][(alpha, window)] = time.perf_counter() - t0
            usable = {s: v["inner_mae"] for s, v in rec["settings"].items() if v["inner_mae"]}
            rec["selected"] = select_setting(usable) if usable else CURRENT_SETTING
            records.append(rec)

        test_idx = np.concatenate([r["test"] for r in records])
        y_dev, dates_dev = y[test_idx], reg.index[test_idx]
        zero = np.abs(y_dev)
        b = 400 if mode == "quick" else 2000
        block = max(20, 2 * horizon)

        def path(name_fn):
            raw = np.concatenate([r["settings"][name_fn(r)]["raw"] for r in records])
            cal = np.concatenate([r["settings"][name_fn(r)]["slope"] * r["settings"][name_fn(r)]["raw"] for r in records])
            iss = np.concatenate([(r["settings"][name_fn(r)]["slope"] * r["settings"][name_fn(r)]["raw"]) if r["settings"][name_fn(r)]["issued"]
                                  else np.zeros(len(r["test"])) for r in records])
            rate = float(np.mean([r["settings"][name_fn(r)]["issued"] for r in records]))
            return {"raw": raw, "calibrated": cal, "issued": iss, "issuance_rate": rate}

        paths = {setting_name(a, w): path(lambda r, s=(a, w): s) for a, w in settings}
        paths["inner_selected"] = path(lambda r: r["selected"])
        current = setting_name(*CURRENT_SETTING)

        def ci_pair(diff, label, metric):
            lo_m, hi_m = month_block_ci(dates_dev, lambda i: float(diff[i].mean()), b=b)
            lo_c, hi_c = contiguous_block_ci(len(diff), lambda i: float(diff[i].mean()), block, b=b)
            base = {"target": target, "horizon": horizon, "comparison": label, "metric": metric, "delta": float(diff.mean()),
                    "common_n": int(len(diff)), "calibrated": "raw" not in metric}
            return [dict(base, ci_lo=lo_m, ci_hi=hi_m, block="month"), dict(base, ci_lo=lo_c, ci_hi=hi_c, block=f"contiguous_{block}")]

        metrics = [{"target": target, "horizon": horizon, "candidate": "hold_current", "fold": "dev_all", "evaluation_stage": "dev_common",
                    "n": int(len(y_dev)), "mae": float(zero.mean()), "rmse": float(np.sqrt(np.mean(y_dev ** 2)))}]
        comparisons = []
        ref = paths[current]
        for name, pth in paths.items():
            err = {k: np.abs(y_dev - pth[k]) for k in ("raw", "calibrated", "issued")}
            row = {"target": target, "horizon": horizon, "candidate": name, "fold": "dev_all", "evaluation_stage": "dev_common",
                   "n": int(len(y_dev)), "mae": float(err["raw"].mean()), "rmse": float(np.sqrt(np.mean((y_dev - pth["raw"]) ** 2))),
                   "mae_calibrated": float(err["calibrated"].mean()), "mae_issued": float(err["issued"].mean()),
                   "issuance_rate": pth["issuance_rate"], "mean_abs_raw": float(np.abs(pth["raw"]).mean())}
            if name != "inner_selected":
                key = next(s for s in settings if setting_name(*s) == name)
                row["mean_train_rows"] = float(np.mean([r["settings"][key]["train_rows"] for r in records]))
                row["mean_slope"] = float(np.mean([r["settings"][key]["slope"] for r in records]))
                row["seconds_per_fold"] = float(np.mean([r["seconds"][key] for r in records]))
            metrics.append(row)
            for r in records:
                key = r["selected"] if name == "inner_selected" else next(s for s in settings if setting_name(*s) == name)
                te = r["test"]; v = r["settings"][key]
                metrics.append({"target": target, "horizon": horizon, "candidate": name, "fold": r["fold"], "evaluation_stage": "dev_fold",
                                "n": int(len(te)), "mae": float(np.abs(y[te] - v["raw"]).mean()), "zero_mae": float(np.abs(y[te]).mean()),
                                "train_rows": v["train_rows"], "slope": v["slope"], "issued": v["issued"],
                                "setting": setting_name(*key)})
            if name != current:
                for k, metric in (("raw", "mae_return_raw"), ("calibrated", "mae_return_calibrated"), ("issued", "mae_return_issued")):
                    comparisons += ci_pair(err[k] - np.abs(y_dev - ref[k]), f"{name} - {current}", metric)
            for k, metric in (("calibrated", "mae_return_calibrated"), ("issued", "mae_return_issued")):
                comparisons += ci_pair(err[k] - zero, f"{name} - hold_current", metric)

        raw_dir = Path(storage) / target
        raw_dir.mkdir(parents=True, exist_ok=True)
        oof_frame = pd.DataFrame({"prediction_date": dates_dev, "y": y_dev, "sigma": sigma[test_idx],
                                  **{f"raw_{n}": p_["raw"] for n, p_ in paths.items()},
                                  **{f"issued_{n}": p_["issued"] for n, p_ in paths.items()}})
        oof_path = raw_dir / f"M03_h{horizon}_{mode}_oof.csv"
        oof_frame.to_csv(oof_path, index=False, lineterminator="\n")

        sel_delta = next(c for c in comparisons if c["comparison"] == f"inner_selected - {current}"
                         and c["metric"] == "mae_return_issued" and c["block"] == "month")
        counts = pd.Series([setting_name(*r["selected"]) for r in records]).value_counts().to_dict()
        summary = {
            "target": target, "horizon": horizon, "n_rows": int(len(reg)), "n_features": len(cols),
            "dev_folds": [r["fold"] for r in records], "lock_start": str(lock_start.date()), "n_dev_rows": int(len(y_dev)),
            "purge_violations": violations,
            "selections": [{"fold": r["fold"], "selected": setting_name(*r["selected"]),
                            "inner_mae": {setting_name(*s): [round(v, 6) for v in d["inner_mae"]] for s, d in r["settings"].items()},
                            "train_rows": {setting_name(*s): d["train_rows"] for s, d in r["settings"].items()},
                            "issued": {setting_name(*s): d["issued"] for s, d in r["settings"].items()}} for r in records],
            "selection_counts": counts,
            "window_rows_differ": bool(all(r["settings"][(10000., "5y")]["train_rows"] < r["settings"][(10000., "expanding")]["train_rows"]
                                           for r in records if r["settings"][(10000., "expanding")]["train_rows"] > 1300)),
            "inner_selected_vs_current_issued_month_ci": [sel_delta["ci_lo"], sel_delta["ci_hi"]],
            "verdict": ("inner_selected 발행 중심값이 현행보다 유의하게 낫지 않음 → 현행(alpha=1e4, expanding) 유지"
                        if not (np.isfinite(sel_delta["ci_hi"]) and sel_delta["ci_hi"] < 0)
                        else "inner_selected 발행 중심값 우위 → M07 후보 검토"),
            "oof_file": str(oof_path), "oof_sha256": file_sha256(oof_path),
        }
        write_json(state.run_dir / f"summary_{target}_h{horizon}.json", summary)
        save_unit_rows(state, target, horizon, metrics, comparisons)
        state.mark(unit, {"oof_sha256": summary["oof_sha256"], "oof_file": summary["oof_file"], "selection_counts": counts,
                          "purge_violations": len(violations), "verdict": summary["verdict"]})
        print(f"  {unit}: 개발 {len(records)}폴드 · 선택 {counts} · inner_selected−현행(발행) {sel_delta['delta']:+.5f} "
              f"[{sel_delta['ci_lo']:+.5f}, {sel_delta['ci_hi']:+.5f}] · 발행률 선택 {paths['inner_selected']['issuance_rate']:.2f} / 현행 {ref['issuance_rate']:.2f}")
    return collect_rows(state, target)


# ---------------------------------------------------------------------------
# M04 — 직접 방향 확률 모델
# ---------------------------------------------------------------------------
DIRECTION_BAND_MULT = 0.3          # 밴드 = 과거 변동성 × sqrt(h) × 0.3 (초기 계약, 평가 결과로 바꾸지 않는다)
DIRECTION_C = (0.003, 0.01, 0.03)
DIRECTION_TEMPERATURES = (1., .75, 1.5, 2.)


def direction_labels(future_return, sigma_h, mult=DIRECTION_BAND_MULT):
    """3클래스 라벨: 0 하락(< -band), 1 보합, 2 상승(> band). band = sigma_h × mult."""
    r, s = np.asarray(future_return, dtype=float), np.asarray(sigma_h, dtype=float)
    if np.any(~np.isfinite(r)) or np.any(~np.isfinite(s)) or np.any(s <= 0):
        raise ValueError("라벨에는 유한한 수익률과 양의 변동성이 필요하다")
    band = s * mult
    return np.where(r < -band, 0, np.where(r > band, 2, 1)).astype(int), band


def class_prior_probabilities(y_train, n):
    """학습 구간 클래스 사전확률(빈도)을 n행에 복제한다. 없는 클래스는 아주 작은 값."""
    counts = np.bincount(np.asarray(y_train, dtype=int), minlength=3).astype(float)
    prior = np.clip(counts / counts.sum(), 1e-7, 1.)
    prior = prior / prior.sum()
    return np.tile(prior, (n, 1))


def fit_direction_probabilities(X, y, train, test, C, seed=SEED):
    from sklearn.dummy import DummyClassifier
    if len(np.unique(y[train])) > 1:
        estimator = fu.direction_estimator("Logistic", {"C": C, "class_weight": None}, seed)
    else:
        estimator = DummyClassifier(strategy="prior")
    estimator.fit(X[train], y[train])
    return fu.aligned_probabilities(estimator, X[test])


def brier_score(y, p):
    onehot = np.eye(3)[np.asarray(y, dtype=int)]
    return float(np.mean(np.sum((p - onehot) ** 2, axis=1)))


def balanced_accuracy(y, p):
    from sklearn.metrics import balanced_accuracy_score
    return float(balanced_accuracy_score(np.asarray(y, dtype=int), p.argmax(axis=1)))


def m04_config(mode):
    return {"task": "M04", "mode": mode, "horizons": list(HORIZONS), "features": "current_full",
            "band": f"sigma_simple(vol_20*sqrt(h)) * {DIRECTION_BAND_MULT}", "model": "StandardScaler+Logistic(multinomial), class_weight=None",
            "C": list(DIRECTION_C), "temperatures": list(DIRECTION_TEMPERATURES),
            "selection": "내부 3구간 합산 log loss 최소 C, 같은 예측으로 온도 선택",
            "baseline": "학습 구간 클래스 사전확률",
            "folds": {"first_test": FIRST_TEST, "test_months": TEST_MONTHS, "lock_months": LOCK_MONTHS,
                      "min_train_rows": MIN_TRAIN_ROWS, "min_test_rows": MIN_TEST_ROWS, "inner_blocks": INNER_BLOCKS},
            "bootstrap_b": 400 if mode == "quick" else 2000, "seed": SEED}


def run_m04(target, mode, storage, results_dir, state, run_notebook_fn=None):
    inputs = None
    for horizon in HORIZONS:
        unit = f"{target}:h{horizon}"
        if state.is_done(unit) and artifacts_intact(state, unit, target, horizon):
            print(f"  이미 완료된 단위 건너뜀: {unit}")
            continue
        if inputs is None:
            inputs = load_inputs(target, mode, storage, run_notebook_fn)
            record_inputs(state, inputs)
        reg, cols = price_design(inputs, horizon)
        sam = inputs["sam"]
        folds, lock_start = evaluation_folds(reg.index, sam.index, horizon)
        dev = [f for f in folds if f["name"].startswith("dev") and not f.get("excluded")]
        if not dev:
            raise SystemExit(f"{unit}: 개발 폴드가 없습니다.")
        violations = purge_check(reg.index, sam.index, horizon, [(f["train"], f["test"]) for f in dev]
                                 + [(i["train"], i["test"]) for f in dev for i in f["inner"] if not i.get("excluded")])
        X = reg[cols].to_numpy(dtype=np.float32)
        ret = reg["future_return"].to_numpy(dtype=float)
        sigma = reg["sigma_simple"].to_numpy(dtype=float)
        y, band = direction_labels(ret, sigma)

        records = []
        for f in dev:
            inner = [b_ for b_ in f["inner"] if not b_.get("excluded")]
            inner_y = np.concatenate([y[b_["test"]] for b_ in inner]) if inner else np.array([], dtype=int)
            trials = {}
            for C in DIRECTION_C:
                probs = np.vstack([fit_direction_probabilities(X, y, b_["train"], b_["test"], C) for b_ in inner]) if inner else None
                trials[C] = probs
            if inner:
                best_C = min(DIRECTION_C, key=lambda c: fu.probability_loss(inner_y, trials[c]))
                temperature = min(DIRECTION_TEMPERATURES,
                                  key=lambda tt: fu.probability_loss(inner_y, fu.temperature_probabilities(trials[best_C], tt)))
                inner_loss = fu.probability_loss(inner_y, fu.temperature_probabilities(trials[best_C], temperature))
                inner_prior_loss = fu.probability_loss(inner_y, np.vstack([class_prior_probabilities(y[b_["train"]], len(b_["test"])) for b_ in inner]))
            else:
                best_C, temperature, inner_loss, inner_prior_loss = DIRECTION_C[-1], 1., np.nan, np.nan
            probs = fu.temperature_probabilities(fit_direction_probabilities(X, y, f["train"], f["test"], best_C), temperature)
            prior = class_prior_probabilities(y[f["train"]], len(f["test"]))
            records.append({"fold": f["name"], "test": f["test"], "C": best_C, "temperature": temperature,
                            "inner_log_loss": inner_loss, "inner_prior_log_loss": inner_prior_loss,
                            "probs": probs, "prior": prior, "train_rows": int(len(f["train"])),
                            "train_class_share": (np.bincount(y[f["train"]], minlength=3) / len(f["train"])).round(4).tolist()})

        test_idx = np.concatenate([r["test"] for r in records])
        y_dev, dates_dev = y[test_idx], reg.index[test_idx]
        P = {"logistic_direction": np.vstack([r["probs"] for r in records]), "class_prior": np.vstack([r["prior"] for r in records])}
        for name, pr in P.items():
            if not np.all(np.isfinite(pr)) or not np.allclose(pr.sum(axis=1), 1., atol=1e-6):
                raise RuntimeError(f"{name}: 확률이 유한하지 않거나 합이 1이 아니다")
        b = 400 if mode == "quick" else 2000
        block = max(20, 2 * horizon)

        def per_row_loss(pr):
            return -np.log(np.clip(pr[np.arange(len(y_dev)), y_dev], 1e-7, 1.))

        def per_row_brier(pr):
            return np.sum((pr - np.eye(3)[y_dev]) ** 2, axis=1)

        def ci_pair(stat_fn, label, metric, n):
            lo_m, hi_m = month_block_ci(dates_dev, stat_fn, b=b)
            lo_c, hi_c = contiguous_block_ci(n, stat_fn, block, b=b)
            base = {"target": target, "horizon": horizon, "comparison": label, "metric": metric,
                    "delta": float(stat_fn(np.arange(n))), "common_n": int(n), "calibrated": True}
            return [dict(base, ci_lo=lo_m, ci_hi=hi_m, block="month"), dict(base, ci_lo=lo_c, ci_hi=hi_c, block=f"contiguous_{block}")]

        metrics, comparisons = [], []
        for name, pr in P.items():
            metrics.append({"target": target, "horizon": horizon, "candidate": name, "fold": "dev_all", "evaluation_stage": "dev_common",
                            "n": int(len(y_dev)), "log_loss": fu.probability_loss(y_dev, pr), "brier": brier_score(y_dev, pr),
                            "balanced_accuracy": balanced_accuracy(y_dev, pr), "accuracy": float(np.mean(pr.argmax(axis=1) == y_dev)),
                            "class_share_dev": (np.bincount(y_dev, minlength=3) / len(y_dev)).round(4).tolist(),
                            "metric_note": "3클래스 확률 모델. 가격 회귀의 부호 적중률과 섞지 않는다"})
            for r in records:
                pr_f = r["probs"] if name == "logistic_direction" else r["prior"]
                yy = y[r["test"]]
                metrics.append({"target": target, "horizon": horizon, "candidate": name, "fold": r["fold"], "evaluation_stage": "dev_fold",
                                "n": int(len(yy)), "log_loss": fu.probability_loss(yy, pr_f), "brier": brier_score(yy, pr_f),
                                "balanced_accuracy": balanced_accuracy(yy, pr_f), "C": r["C"] if name == "logistic_direction" else "",
                                "temperature": r["temperature"] if name == "logistic_direction" else "",
                                "inner_log_loss": r["inner_log_loss"] if name == "logistic_direction" else r["inner_prior_log_loss"],
                                "train_rows": r["train_rows"], "train_class_share": r["train_class_share"]})
        d_loss = per_row_loss(P["logistic_direction"]) - per_row_loss(P["class_prior"])
        d_brier = per_row_brier(P["logistic_direction"]) - per_row_brier(P["class_prior"])
        n = len(y_dev)
        comparisons += ci_pair(lambda i: float(d_loss[i].mean()), "logistic_direction - class_prior", "log_loss", n)
        comparisons += ci_pair(lambda i: float(d_brier[i].mean()), "logistic_direction - class_prior", "brier", n)
        comparisons += ci_pair(lambda i: balanced_accuracy(y_dev[i], P["logistic_direction"][i]) - balanced_accuracy(y_dev[i], P["class_prior"][i]),
                               "logistic_direction - class_prior", "balanced_accuracy", n)

        raw_dir = Path(storage) / target
        raw_dir.mkdir(parents=True, exist_ok=True)
        oof_frame = pd.DataFrame({"prediction_date": dates_dev, "y": y_dev, "band": band[test_idx],
                                  "p_down": P["logistic_direction"][:, 0], "p_flat": P["logistic_direction"][:, 1], "p_up": P["logistic_direction"][:, 2],
                                  "prior_down": P["class_prior"][:, 0], "prior_flat": P["class_prior"][:, 1], "prior_up": P["class_prior"][:, 2]})
        oof_path = raw_dir / f"M04_h{horizon}_{mode}_oof.csv"
        oof_frame.to_csv(oof_path, index=False, lineterminator="\n")

        ll = next(c for c in comparisons if c["metric"] == "log_loss" and c["block"] == "month")
        ba = next(c for c in comparisons if c["metric"] == "balanced_accuracy" and c["block"] == "month")
        verdict = ("사전확률 대비 log loss 유의 우위" if np.isfinite(ll["ci_hi"]) and ll["ci_hi"] < 0 else
                   "사전확률 대비 log loss 유의 열위" if np.isfinite(ll["ci_lo"]) and ll["ci_lo"] > 0 else "동률(CI가 0 포함)")
        summary = {
            "target": target, "horizon": horizon, "n_rows": int(len(reg)), "n_features": len(cols),
            "band_mult": DIRECTION_BAND_MULT, "dev_folds": [r["fold"] for r in records], "lock_start": str(lock_start.date()),
            "n_dev_rows": int(n), "purge_violations": violations,
            "class_share_dev": (np.bincount(y_dev, minlength=3) / n).round(4).tolist(),
            "selections": [{"fold": r["fold"], "C": r["C"], "temperature": r["temperature"], "inner_log_loss": r["inner_log_loss"],
                            "inner_prior_log_loss": r["inner_prior_log_loss"], "train_rows": r["train_rows"]} for r in records],
            "log_loss_delta_month_ci": [ll["delta"], ll["ci_lo"], ll["ci_hi"]],
            "balanced_accuracy_delta_month_ci": [ba["delta"], ba["ci_lo"], ba["ci_hi"]],
            "verdict": verdict, "oof_file": str(oof_path), "oof_sha256": file_sha256(oof_path),
        }
        write_json(state.run_dir / f"summary_{target}_h{horizon}.json", summary)
        save_unit_rows(state, target, horizon, metrics, comparisons)
        state.mark(unit, {"oof_sha256": summary["oof_sha256"], "oof_file": summary["oof_file"], "verdict": verdict,
                          "purge_violations": len(violations)})
        print(f"  {unit}: {len(records)}폴드 · log loss 로지스틱−사전확률 {ll['delta']:+.4f} [{ll['ci_lo']:+.4f}, {ll['ci_hi']:+.4f}] · "
              f"균형정확도 {ba['delta']:+.4f} [{ba['ci_lo']:+.4f}, {ba['ci_hi']:+.4f}] · {verdict}")
    return collect_rows(state, target)


# ---------------------------------------------------------------------------
# M05 — 구간과 보류
# ---------------------------------------------------------------------------
RESIDUAL_WINDOW = 252          # 최근 확정 잔차 개수
RESIDUAL_MIN = 100             # 이보다 적으면 현행(내부 q) 방식으로 물러난다
INTERVAL_VARIANTS = ("simple_inner_q", "har_inner_q", "simple_resid252")
ABSTAIN_POLICIES = ("gate_2of3", "gate_3of3", "always_issue", "never_issue")


def band_quantile(residuals, sigma, coverage=COVERAGE):
    """|잔차|/sigma 의 coverage 분위수. 구간 반폭 = q × sigma."""
    r, s = np.asarray(residuals, dtype=float), np.asarray(sigma, dtype=float)
    return float(np.quantile(np.abs(r) / np.maximum(s, 1e-6), coverage))


def matured_residuals(history, before, window=RESIDUAL_WINDOW):
    """before(시험 시작일) 전에 만기가 도래한 사전 예측의 잔차 중 최근 window 개.

    history: (target_date, residual, sigma) 행의 DataFrame — 앞선 외부 폴드의 예측만 들어 있다.
    """
    if history is None or len(history) == 0:
        return history
    done = history[pd.to_datetime(history["target_date"]) < pd.Timestamp(before)].sort_values("target_date")
    return done.tail(window)


def coverage_and_score(y, center, halfwidth, coverage=COVERAGE):
    lower, upper = center - halfwidth, center + halfwidth
    hit = (y >= lower) & (y <= upper)
    return hit, interval_score(y, lower, upper, coverage), (upper - lower)


def gate_wins(inner_pred, inner_y, slope):
    return sum(np.abs(y - slope * p).mean() < np.abs(y).mean() for p, y in zip(inner_pred, inner_y)), len(inner_pred)


def m05_config(mode):
    return {"task": "M05", "mode": mode, "horizons": list(HORIZONS), "features": "current_full", "model": "alpha=1e4, expanding",
            "coverage": COVERAGE, "intervals": list(INTERVAL_VARIANTS), "residual_window": RESIDUAL_WINDOW, "residual_min": RESIDUAL_MIN,
            "abstain_policies": list(ABSTAIN_POLICIES),
            "folds": {"first_test": FIRST_TEST, "test_months": TEST_MONTHS, "lock_months": LOCK_MONTHS,
                      "min_train_rows": MIN_TRAIN_ROWS, "min_test_rows": MIN_TEST_ROWS, "inner_blocks": INNER_BLOCKS},
            "bootstrap_b": 400 if mode == "quick" else 2000, "seed": SEED}


def run_m05(target, mode, storage, results_dir, state, run_notebook_fn=None):
    inputs = None
    for horizon in HORIZONS:
        unit = f"{target}:h{horizon}"
        if state.is_done(unit) and artifacts_intact(state, unit, target, horizon):
            print(f"  이미 완료된 단위 건너뜀: {unit}")
            continue
        if inputs is None:
            inputs = load_inputs(target, mode, storage, run_notebook_fn)
            record_inputs(state, inputs)
        reg, cols = price_design(inputs, horizon)
        sam = inputs["sam"]
        folds, lock_start = evaluation_folds(reg.index, sam.index, horizon)
        dev = [f for f in folds if f["name"].startswith("dev") and not f.get("excluded")]
        if not dev:
            raise SystemExit(f"{unit}: 개발 폴드가 없습니다.")
        violations = purge_check(reg.index, sam.index, horizon, [(f["train"], f["test"]) for f in dev]
                                 + [(i["train"], i["test"]) for f in dev for i in f["inner"] if not i.get("excluded")])
        X = reg[cols].to_numpy(dtype=np.float32)
        y = reg["future_return"].to_numpy(dtype=float)
        sigma = reg["sigma_simple"].to_numpy(dtype=float)
        sigma_har = reg["sigma_har"].to_numpy(dtype=float)
        z = y / np.maximum(sigma, 1e-6)
        template = fu.make_price_model()
        pos, mature = maturity_positions(reg.index, sam.index, horizon)
        target_dates = pd.DatetimeIndex(sam.index[mature])

        history = pd.DataFrame(columns=["target_date", "residual", "sigma"])
        records = []
        for f in dev:
            raw = fit_predict(template, X, z, sigma, f["train"], f["test"])
            inner_pred, inner_y, inner_idx = [], [], []
            for block in f["inner"]:
                if block.get("excluded"):
                    continue
                p_ = fit_predict(template, X, z, sigma, block["train"], block["test"])
                inner_pred.append(p_); inner_y.append(y[block["test"]]); inner_idx.append(block["test"])
            slope = inner_slope(np.concatenate(inner_pred), np.concatenate(inner_y)) if inner_pred else 0.
            wins, n_inner = gate_wins(inner_pred, inner_y, slope)
            center = slope * raw
            inner_all = np.concatenate(inner_idx) if inner_idx else np.array([], dtype=int)
            inner_resid = np.concatenate(inner_y) - slope * np.concatenate(inner_pred) if inner_pred else np.array([])
            q_simple = band_quantile(inner_resid, sigma[inner_all]) if len(inner_resid) else np.nan
            q_har = band_quantile(inner_resid, sigma_har[inner_all]) if len(inner_resid) else np.nan
            recent = matured_residuals(history, f["test_start"])
            if recent is not None and len(recent) >= RESIDUAL_MIN:
                q_resid, resid_source, n_resid = band_quantile(recent["residual"], recent["sigma"]), "resid252", int(len(recent))
            else:
                q_resid, resid_source, n_resid = q_simple, "fallback_inner_q", int(0 if recent is None else len(recent))
            records.append({"fold": f["name"], "test": f["test"], "raw": raw, "center": center, "slope": slope,
                            "wins": wins, "n_inner": n_inner, "q_simple": q_simple, "q_har": q_har, "q_resid": q_resid,
                            "resid_source": resid_source, "n_resid": n_resid, "test_start": f["test_start"]})
            # 이 폴드의 예측은 만기가 도래한 뒤에만 다음 폴드의 잔차 창에 들어간다(matured_residuals 가 날짜로 거른다)
            history = pd.concat([history, pd.DataFrame({"target_date": target_dates[f["test"]], "residual": y[f["test"]] - center,
                                                        "sigma": sigma[f["test"]]})], ignore_index=True)

        test_idx = np.concatenate([r["test"] for r in records])
        y_dev, dates_dev = y[test_idx], reg.index[test_idx]
        center_dev = np.concatenate([r["center"] for r in records])
        s_dev, sh_dev = sigma[test_idx], sigma_har[test_idx]
        halfwidths = {
            "simple_inner_q": np.concatenate([r["q_simple"] * sigma[r["test"]] for r in records]),
            "har_inner_q": np.concatenate([r["q_har"] * sigma_har[r["test"]] for r in records]),
            "simple_resid252": np.concatenate([r["q_resid"] * sigma[r["test"]] for r in records]),
        }
        b = 400 if mode == "quick" else 2000
        block = max(20, 2 * horizon)
        regime_cut = np.quantile(s_dev, [1 / 3, 2 / 3])
        regime = np.where(s_dev <= regime_cut[0], "low", np.where(s_dev <= regime_cut[1], "mid", "high"))

        def ci_pair(diff_fn, label, metric):
            lo_m, hi_m = month_block_ci(dates_dev, diff_fn, b=b)
            lo_c, hi_c = contiguous_block_ci(len(y_dev), diff_fn, block, b=b)
            base = {"target": target, "horizon": horizon, "comparison": label, "metric": metric,
                    "delta": float(diff_fn(np.arange(len(y_dev)))), "common_n": int(len(y_dev)), "calibrated": True}
            return [dict(base, ci_lo=lo_m, ci_hi=hi_m, block="month"), dict(base, ci_lo=lo_c, ci_hi=hi_c, block=f"contiguous_{block}")]

        metrics, comparisons = [], []
        results = {}
        for name, hw in halfwidths.items():
            hit, score, width = coverage_and_score(y_dev, center_dev, hw)
            results[name] = (hit, score, width)
            row = {"target": target, "horizon": horizon, "candidate": name, "fold": "dev_all", "evaluation_stage": "dev_common",
                   "n": int(len(y_dev)), "interval_score": float(score.mean()), "interval_coverage": float(hit.mean()),
                   "mean_width": float(width.mean()), "mean_width_over_sigma": float((width / s_dev).mean())}
            for rg in ("low", "mid", "high"):
                mk = regime == rg
                row[f"coverage_{rg}"] = float(hit[mk].mean()) if mk.any() else ""
                row[f"width_{rg}"] = float(width[mk].mean()) if mk.any() else ""
            metrics.append(row)
            for r in records:
                te = r["test"]; q = {"simple_inner_q": r["q_simple"], "har_inner_q": r["q_har"], "simple_resid252": r["q_resid"]}[name]
                sg = sigma_har[te] if name == "har_inner_q" else sigma[te]
                h_, sc_, w_ = coverage_and_score(y[te], r["center"], q * sg)
                metrics.append({"target": target, "horizon": horizon, "candidate": name, "fold": r["fold"], "evaluation_stage": "dev_fold",
                                "n": int(len(te)), "interval_score": float(sc_.mean()), "interval_coverage": float(h_.mean()),
                                "mean_width": float(w_.mean()), "q": q, "resid_source": r["resid_source"] if name == "simple_resid252" else "",
                                "n_resid": r["n_resid"] if name == "simple_resid252" else ""})
            if name != "simple_inner_q":
                ref_hit, ref_score, ref_width = results["simple_inner_q"]
                comparisons += ci_pair(lambda i, s_=score, rs=ref_score: float((s_[i] - rs[i]).mean()), f"{name} - simple_inner_q", "interval_score")
                comparisons += ci_pair(lambda i, h_=hit, rh=ref_hit: float(h_[i].mean() - rh[i].mean()), f"{name} - simple_inner_q", "interval_coverage")
                comparisons += ci_pair(lambda i, w_=width, rw=ref_width: float((w_[i] - rw[i]).mean()), f"{name} - simple_inner_q", "mean_width")
        for name, (hit, score, width) in results.items():
            comparisons += ci_pair(lambda i, h_=hit: float(h_[i].mean() - COVERAGE), f"{name} - target_coverage", "interval_coverage")

        # 보류 정책(가격 중심값). 방향 확률과는 별개다.
        zero = np.abs(y_dev)
        policies = {}
        for pol in ABSTAIN_POLICIES:
            issued = np.concatenate([np.full(len(r["test"]),
                                             {"gate_2of3": r["wins"] >= 2 and r["n_inner"] >= 2, "gate_3of3": r["wins"] == r["n_inner"] and r["n_inner"] >= 2,
                                              "always_issue": True, "never_issue": False}[pol]) for r in records])
            centre = np.where(issued, center_dev, 0.)
            policies[pol] = (issued, centre)
            err = np.abs(y_dev - centre)
            metrics.append({"target": target, "horizon": horizon, "candidate": pol, "fold": "dev_all", "evaluation_stage": "abstention",
                            "n": int(len(y_dev)), "mae_issued_center": float(err.mean()), "mae_hold": float(zero.mean()),
                            "issuance_rate": float(issued.mean()), "n_issued": int(issued.sum()), "n_held": int((~issued).sum()),
                            "mae_on_issued_days": float(err[issued].mean()) if issued.any() else "",
                            "hold_mae_on_issued_days": float(zero[issued].mean()) if issued.any() else ""})
            comparisons += ci_pair(lambda i, e=err: float((e[i] - zero[i]).mean()), f"{pol} - hold_current", "mae_return_issued")

        raw_dir = Path(storage) / target
        raw_dir.mkdir(parents=True, exist_ok=True)
        oof_frame = pd.DataFrame({"prediction_date": dates_dev, "target_date": target_dates[test_idx], "y": y_dev, "center": center_dev,
                                  "sigma": s_dev, "sigma_har": sh_dev, **{f"halfwidth_{n}": hw for n, hw in halfwidths.items()},
                                  **{f"issued_{p_}": v[0] for p_, v in policies.items()}})
        oof_path = raw_dir / f"M05_h{horizon}_{mode}_oof.csv"
        oof_frame.to_csv(oof_path, index=False, lineterminator="\n")

        def month(label, metric):
            return next(c for c in comparisons if c["comparison"] == label and c["metric"] == metric and c["block"] == "month")
        resid = month("simple_resid252 - simple_inner_q", "interval_score")
        har = month("har_inner_q - simple_inner_q", "interval_score")
        summary = {
            "target": target, "horizon": horizon, "n_dev_rows": int(len(y_dev)), "dev_folds": [r["fold"] for r in records],
            "lock_start": str(lock_start.date()), "purge_violations": violations,
            "coverage": {n: float(v[0].mean()) for n, v in results.items()}, "interval_score": {n: float(v[1].mean()) for n, v in results.items()},
            "mean_width": {n: float(v[2].mean()) for n, v in results.items()},
            "regime_cuts_sigma": [float(regime_cut[0]), float(regime_cut[1])],
            "residual_window": [{"fold": r["fold"], "source": r["resid_source"], "n": r["n_resid"], "q": r["q_resid"], "q_inner": r["q_simple"]} for r in records],
            "resid252_vs_inner_score_month_ci": [resid["delta"], resid["ci_lo"], resid["ci_hi"]],
            "har_vs_inner_score_month_ci": [har["delta"], har["ci_lo"], har["ci_hi"]],
            "abstention": {p_: {"issuance_rate": float(v[0].mean()), "delta_vs_hold_month": [month(f"{p_} - hold_current", "mae_return_issued")[k] for k in ("delta", "ci_lo", "ci_hi")]}
                           for p_, v in policies.items()},
            "verdict_interval": ("잔차 보정 구간이 유의하게 낫다(구간 점수)" if np.isfinite(resid["ci_hi"]) and resid["ci_hi"] < 0 else
                                 "잔차 보정 구간 우위 미확인 → 현행 구간 유지"),
            "verdict_har": ("HAR 구간이 유의하게 낫다(구간 점수)" if np.isfinite(har["ci_hi"]) and har["ci_hi"] < 0 else "HAR 우위 미확인(자동 채택 없음)"),
            "oof_file": str(oof_path), "oof_sha256": file_sha256(oof_path),
        }
        write_json(state.run_dir / f"summary_{target}_h{horizon}.json", summary)
        save_unit_rows(state, target, horizon, metrics, comparisons)
        state.mark(unit, {"oof_sha256": summary["oof_sha256"], "oof_file": summary["oof_file"], "purge_violations": len(violations),
                          "verdict_interval": summary["verdict_interval"], "verdict_har": summary["verdict_har"]})
        print(f"  {unit}: 포함률 {{{', '.join(f'{k} {v:.2f}' for k, v in summary['coverage'].items())}}} · "
              f"잔차252−내부q 점수 {resid['delta']:+.5f} [{resid['ci_lo']:+.5f}, {resid['ci_hi']:+.5f}] · HAR−내부q {har['delta']:+.5f} · "
              f"보류 {{{', '.join(f'{k} {v['issuance_rate']:.2f}' for k, v in summary['abstention'].items())}}}")
    return collect_rows(state, target)


# ---------------------------------------------------------------------------
# M06 — 공동 학습(선택 작업)
# ---------------------------------------------------------------------------
PANEL_CACHE = "panel_cache"                 # <storage>/panel_cache/<ticker>.csv — P10 스냅샷을 복사해 쓴다
PANEL_MARKET_ASSETS = ("kospi", "sox")      # 모든 종목이 공유하는 시장·해외 입력(그룹 A 방식으로 d 전 마지막 관측)
PANEL_TARGET_TICKER = {"samsung": "005930.KS", "sk_hynix": "000660.KS"}


def load_panel_cache(storage):
    """{ticker: bars} 와 첫 거래일. 파일이 없는 종목은 건너뛴다(보간·대체 없음)."""
    cache = Path(storage) / PANEL_CACHE
    bars, first = {}, {}
    for path in sorted(cache.glob("*.csv")):
        ticker = path.stem.replace("_", ".", 1)
        frame = pd.read_csv(path, index_col=0, parse_dates=True)
        frame.index = pd.DatetimeIndex(frame.index).tz_localize(None).normalize()
        frame = frame.dropna(subset=["close"])
        if len(frame):
            bars[ticker] = frame
            first[ticker] = frame.index[0]
    return bars, first


def panel_instrument_frame(bars, horizon, market_features=None):
    """한 종목의 (특징 through d-1, 라벨 d-1 종가 대비 d+h-1 종가, sigma, 만기 날짜). 중기 계약과 같은 시점 규칙."""
    sys.path.insert(0, str(ROOT / "experiments" / "model_improvement"))
    import panel_data as pdm
    bars = bars.sort_index()
    close = bars["close"].astype(float)
    f = pdm.instrument_features(close, bars.get("volume")).shift(1)      # d행에는 d-1 종가까지의 값
    f.columns = [f"inst_{c}" for c in f.columns]
    f["future_return"] = close.shift(-(horizon - 1)) / close.shift(1) - 1
    f["sigma_simple"] = close.pct_change().rolling(20).std().shift(1) * np.sqrt(horizon)
    pos = np.arange(len(f))
    mature = pos + horizon - 1
    f["target_date"] = pd.NaT
    ok = mature < len(f)
    f.loc[ok, "target_date"] = bars.index[mature[ok]]
    if market_features is not None:
        for col in market_features.columns:
            f[col] = market_features[col].reindex(f.index)
        f["rel_kospi_20"] = f["inst_mom_20"] - f["kospi_cum_20"] if "kospi_cum_20" in f else np.nan
    return f.replace([np.inf, -np.inf], np.nan)


def build_medium_panel(bars_by_ticker, horizon, market_features=None):
    frames = []
    for ticker, bars in bars_by_ticker.items():
        f = panel_instrument_frame(bars, horizon, market_features)
        f["instrument"] = ticker
        f.index.name = "date"
        frames.append(f.reset_index())
    panel = pd.concat(frames, ignore_index=True)
    feature_cols = [c for c in panel.columns if c.startswith(("inst_", "kospi_", "sox_", "rel_"))]
    return panel.dropna(subset=feature_cols + ["future_return", "sigma_simple", "target_date"]).reset_index(drop=True), feature_cols


def panel_train_rows(panel, before, instruments=None):
    """라벨 만기 종가가 before(시험 시작일) 앞인 행. instruments 를 주면 그 종목만(단독 모델)."""
    mask = pd.to_datetime(panel["target_date"]) < pd.Timestamp(before)
    if instruments is not None:
        mask &= panel["instrument"].isin(list(instruments))
    return np.flatnonzero(mask.to_numpy())


def m06_config(mode):
    return {"task": "M06", "mode": mode, "horizons": list(HORIZONS), "panel_selection_date": "2026-09-10",
            "panel_rule": "experiments/model_improvement/panel_data.py (KRX 반도체, 2015-01-01 이전 상장)", "market_assets": list(PANEL_MARKET_ASSETS),
            "model": "StandardScaler+Ridge(alpha=1e4) on panel features, target=future_return/sigma_simple", "instrument_id": "미사용",
            "folds": {"first_test": FIRST_TEST, "test_months": TEST_MONTHS, "lock_months": LOCK_MONTHS,
                      "min_train_rows": MIN_TRAIN_ROWS, "min_test_rows": MIN_TEST_ROWS, "inner_blocks": INNER_BLOCKS},
            "bootstrap_b": 400 if mode == "quick" else 2000, "seed": SEED}


def run_m06(target, mode, storage, results_dir, state, run_notebook_fn=None):
    sys.path.insert(0, str(ROOT / "experiments" / "model_improvement"))
    import panel_data as pdm
    inputs = None
    bars, first = load_panel_cache(storage)
    if not bars:
        raise SystemExit(f"{storage}/{PANEL_CACHE} 에 패널 종목 CSV 가 없습니다(runs/model_improvement/P00/panel_cache 를 복사하세요).")
    kept, excluded = pdm.eligible_universe(first)
    kept_tickers = [tk for tk, _, _ in kept if tk in bars]
    ticker = PANEL_TARGET_TICKER[target]
    if ticker not in kept_tickers:
        raise SystemExit(f"{target}({ticker}) 이 패널 선정 기준을 통과하지 못했습니다.")
    for horizon in HORIZONS:
        unit = f"{target}:h{horizon}"
        if state.is_done(unit) and artifacts_intact(state, unit, target, horizon):
            print(f"  이미 완료된 단위 건너뜀: {unit}")
            continue
        if inputs is None:
            inputs = load_inputs(target, mode, storage, run_notebook_fn)
            record_inputs(state, inputs)
        closes = {name: load_asset_close(storage, target, name) for name in PANEL_MARKET_ASSETS}
        all_dates = pd.DatetimeIndex(sorted(set().union(*[set(b.index) for b in bars.values()])))
        market = group_a_features(inputs["sam"]["adj_close"], closes, all_dates, windows=(20, 60), relative=())
        market = market[[c for c in market.columns if c.startswith(("kospi_cum", "sox_cum"))]]
        panel, pcols = build_medium_panel({tk: bars[tk] for tk in kept_tickers}, horizon, market)
        bad = pdm.check_no_interpolation(panel.rename(columns={}).assign(date=pd.to_datetime(panel["date"])), {tk: bars[tk] for tk in kept_tickers})
        if bad:
            raise RuntimeError(f"패널에 원본에 없는 날짜가 {bad}행 있다(보간 금지)")

        # 현행 모델(M00 계약)의 같은 종목 행과 날짜를 맞춘다
        reg, cols = price_design(inputs, horizon)
        sam = inputs["sam"]
        folds, lock_start = evaluation_folds(reg.index, sam.index, horizon)
        dev = [f for f in folds if f["name"].startswith("dev") and not f.get("excluded")]
        if not dev:
            raise SystemExit(f"{unit}: 개발 폴드가 없습니다.")
        X_cur = reg[cols].to_numpy(dtype=np.float32)
        y_cur = reg["future_return"].to_numpy(dtype=float)
        s_cur = reg["sigma_simple"].to_numpy(dtype=float)
        z_cur = y_cur / np.maximum(s_cur, 1e-6)
        own = panel[panel["instrument"] == ticker].set_index(pd.to_datetime(panel.loc[panel["instrument"] == ticker, "date"]))
        Xp = panel[pcols].to_numpy(dtype=np.float32)
        yp = panel["future_return"].to_numpy(dtype=float)
        sp = panel["sigma_simple"].to_numpy(dtype=float)
        zp = yp / np.maximum(sp, 1e-6)
        template = fu.make_price_model()

        records = []
        for f in dev:
            test_dates = reg.index[f["test"]]
            common = test_dates[test_dates.isin(own.index)]
            if len(common) == 0:
                continue
            cur_pos = np.asarray(reg.index.get_indexer(common))
            own_rows = np.flatnonzero((panel["instrument"] == ticker).to_numpy() & pd.to_datetime(panel["date"]).isin(common).to_numpy())
            own_rows = own_rows[np.argsort(pd.to_datetime(panel["date"].iloc[own_rows]).to_numpy())]
            pooled_train = panel_train_rows(panel, f["test_start"])
            single_train = panel_train_rows(panel, f["test_start"], [ticker])
            rec = {"fold": f["name"], "dates": common, "cur_pos": cur_pos, "y": yp[own_rows],
                   "n_pooled_train": int(len(pooled_train)), "n_single_train": int(len(single_train)),
                   "raw": {}, "slope": {}}
            rec["raw"]["current"] = fit_predict(template, X_cur, z_cur, s_cur, f["train"], cur_pos)
            rec["raw"]["single"] = fit_predict(template, Xp, zp, sp, single_train, own_rows) if len(single_train) >= MIN_TRAIN_ROWS else np.full(len(own_rows), np.nan)
            rec["raw"]["pooled"] = fit_predict(template, Xp, zp, sp, pooled_train, own_rows)
            # 내부 기울기(각 모델의 내부 3구간 예측으로)
            for name in ("current", "single", "pooled"):
                ip, iy = [], []
                for block in f["inner"]:
                    if block.get("excluded"):
                        continue
                    b_dates = reg.index[block["test"]]
                    b_common = b_dates[b_dates.isin(own.index)]
                    if len(b_common) == 0:
                        continue
                    if name == "current":
                        p_ = fit_predict(template, X_cur, z_cur, s_cur, block["train"], np.asarray(reg.index.get_indexer(b_common)))
                        yy = y_cur[np.asarray(reg.index.get_indexer(b_common))]
                    else:
                        rows = np.flatnonzero((panel["instrument"] == ticker).to_numpy() & pd.to_datetime(panel["date"]).isin(b_common).to_numpy())
                        tr = panel_train_rows(panel, block["test_start"], None if name == "pooled" else [ticker])
                        if len(tr) < MIN_TRAIN_ROWS:
                            continue
                        p_ = fit_predict(template, Xp, zp, sp, tr, rows); yy = yp[rows]
                    ip.append(p_); iy.append(yy)
                rec["slope"][name] = inner_slope(np.concatenate(ip), np.concatenate(iy)) if ip else 0.
            records.append(rec)
        if not records:
            raise SystemExit(f"{unit}: 패널과 겹치는 시험 날짜가 없습니다.")

        y_dev = np.concatenate([r["y"] for r in records])
        y_cur_dev = np.concatenate([y_cur[r["cur_pos"]] for r in records])
        dates_dev = pd.DatetimeIndex(np.concatenate([r["dates"] for r in records]))
        preds = {name: {"raw": np.concatenate([r["raw"][name] for r in records]),
                        "calibrated": np.concatenate([r["slope"][name] * r["raw"][name] for r in records])}
                 for name in ("current", "single", "pooled")}
        b = 400 if mode == "quick" else 2000
        block = max(20, 2 * horizon)
        valid = ~np.isnan(preds["single"]["raw"])

        def ci_pair(diff, label, metric):
            lo_m, hi_m = month_block_ci(dates_dev[valid], lambda i: float(diff[i].mean()), b=b)
            lo_c, hi_c = contiguous_block_ci(int(valid.sum()), lambda i: float(diff[i].mean()), block, b=b)
            base = {"target": target, "horizon": horizon, "comparison": label, "metric": metric, "delta": float(diff.mean()),
                    "common_n": int(valid.sum()), "calibrated": "calibrated" in metric}
            return [dict(base, ci_lo=lo_m, ci_hi=hi_m, block="month"), dict(base, ci_lo=lo_c, ci_hi=hi_c, block=f"contiguous_{block}")]

        metrics, comparisons = [], []
        metrics.append({"target": target, "horizon": horizon, "candidate": "hold_current", "fold": "dev_all", "evaluation_stage": "dev_common",
                        "n": int(valid.sum()), "mae": float(np.abs(y_dev[valid]).mean()), "note": "패널 라벨(수정 종가)",
                        "mae_current_label": float(np.abs(y_cur_dev[valid]).mean())})
        err = {}
        for name, pth in preds.items():
            yy = y_cur_dev if name == "current" else y_dev
            err[name] = {k: np.abs(yy - pth[k])[valid] for k in ("raw", "calibrated")}
            metrics.append({"target": target, "horizon": horizon, "candidate": name, "fold": "dev_all", "evaluation_stage": "dev_common",
                            "n": int(valid.sum()), "mae": float(err[name]["raw"].mean()), "mae_calibrated": float(err[name]["calibrated"].mean()),
                            "n_features": len(cols) if name == "current" else len(pcols),
                            "mean_train_rows": float(np.mean([r["n_pooled_train"] if name == "pooled" else r["n_single_train"] for r in records])) if name != "current" else "",
                            "mean_slope": float(np.mean([r["slope"][name] for r in records]))})
            for r in records:
                yy = y_cur[r["cur_pos"]] if name == "current" else r["y"]
                metrics.append({"target": target, "horizon": horizon, "candidate": name, "fold": r["fold"], "evaluation_stage": "dev_fold",
                                "n": int(len(yy)), "mae": float(np.nanmean(np.abs(yy - r["raw"][name]))), "zero_mae": float(np.abs(yy).mean()),
                                "train_rows": r["n_pooled_train"] if name == "pooled" else (r["n_single_train"] if name == "single" else ""),
                                "slope": r["slope"][name]})
        for a_, b_ in (("pooled", "single"), ("pooled", "current"), ("single", "current")):
            for k in ("raw", "calibrated"):
                comparisons += ci_pair(err[a_][k] - err[b_][k], f"{a_} - {b_}", f"mae_return_{k}")
        zero = np.abs(y_dev)[valid]
        for name in preds:
            comparisons += ci_pair(err[name]["calibrated"] - zero, f"{name} - hold_current", "mae_return_calibrated")

        raw_dir = Path(storage) / target
        raw_dir.mkdir(parents=True, exist_ok=True)
        oof_frame = pd.DataFrame({"prediction_date": dates_dev, "y_panel": y_dev, "y_current_label": y_cur_dev,
                                  **{f"raw_{n}": p_["raw"] for n, p_ in preds.items()}, **{f"cal_{n}": p_["calibrated"] for n, p_ in preds.items()}})
        oof_path = raw_dir / f"M06_h{horizon}_{mode}_oof.csv"
        oof_frame.to_csv(oof_path, index=False, lineterminator="\n")

        def month(label, metric):
            return next(c for c in comparisons if c["comparison"] == label and c["metric"] == metric and c["block"] == "month")
        ps = month("pooled - single", "mae_return_calibrated"); pc = month("pooled - current", "mae_return_calibrated")
        summary = {
            "target": target, "horizon": horizon, "panel_instruments": kept_tickers, "excluded_instruments": excluded,
            "survivorship_note": "종목 목록은 2026-09-10 시점 상장 종목이라 과거로 적용하면 생존 편향이 있다(panel_data.py). 종목 수 증가를 독립 날짜 표본 증가로 보지 않는다.",
            "panel_rows": int(len(panel)), "panel_features": pcols, "n_dev_rows": int(valid.sum()), "dev_folds": [r["fold"] for r in records],
            "lock_start": str(lock_start.date()), "no_interpolation_violations": int(bad),
            "mean_pooled_train_rows": float(np.mean([r["n_pooled_train"] for r in records])),
            "mean_single_train_rows": float(np.mean([r["n_single_train"] for r in records])),
            "pooled_vs_single_calibrated_month_ci": [ps["delta"], ps["ci_lo"], ps["ci_hi"]],
            "pooled_vs_current_calibrated_month_ci": [pc["delta"], pc["ci_lo"], pc["ci_hi"]],
            "verdict": ("pooled 가 현행보다 유의하게 낫다" if np.isfinite(pc["ci_hi"]) and pc["ci_hi"] < 0 else
                        "pooled 우위 미확인 → 현행 유지"),
            "oof_file": str(oof_path), "oof_sha256": file_sha256(oof_path),
        }
        write_json(state.run_dir / f"summary_{target}_h{horizon}.json", summary)
        save_unit_rows(state, target, horizon, metrics, comparisons)
        state.mark(unit, {"oof_sha256": summary["oof_sha256"], "oof_file": summary["oof_file"], "verdict": summary["verdict"]})
        print(f"  {unit}: 패널 {len(kept_tickers)}종목 {len(panel)}행 · 공통 {int(valid.sum())}행 · pooled−single(보정) {ps['delta']:+.5f} "
              f"[{ps['ci_lo']:+.5f}, {ps['ci_hi']:+.5f}] · pooled−현행(보정) {pc['delta']:+.5f} [{pc['ci_lo']:+.5f}, {pc['ci_hi']:+.5f}] · {summary['verdict']}")
    return collect_rows(state, target)


# ---------------------------------------------------------------------------
# M07 — 고정 후보, 잠금 평가 1회, 관찰 현황
# ---------------------------------------------------------------------------
# 개발 폴드(M05)에서 기준을 통과한 후보만. 2026-09-10 에 고정했고 잠금 점수를 본 뒤 바꾸지 않는다.
LOCKED_CANDIDATES = {
    ("sk_hynix", 5): [{"name": "har_interval", "purpose": "interval", "ledger_model": "Candidate HAR interval"},
                      {"name": "strict_gate", "purpose": "price", "ledger_model": "Candidate strict gate"}],
    ("sk_hynix", 20): [{"name": "har_interval", "purpose": "interval", "ledger_model": "Candidate HAR interval"}],
}
FIRST_CHECK = {5: {"matured": 60, "nonoverlap": 12}, 20: {"matured": 120, "nonoverlap": 6}}


def month_block_draws(date_index, stat_fn, b=2000, seed=SEED):
    """month_block_ci 와 같은 추출의 통계량 표본(부트스트랩 p 값용)."""
    blocks = month_blocks(date_index)
    if not blocks:
        return np.array([])
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(b):
        pick = rng.integers(0, len(blocks), len(blocks))
        idx = np.concatenate([blocks[i] for i in pick])
        try:
            draws.append(stat_fn(idx))
        except Exception:
            continue
    return np.asarray(draws, dtype=float)


def bootstrap_pvalue(draws):
    """H0: delta = 0 에 대한 양측 부트스트랩 p 값(백분위 방식)."""
    draws = np.asarray(draws, dtype=float)
    if len(draws) == 0:
        return np.nan
    return float(min(1., 2 * min(np.mean(draws >= 0), np.mean(draws <= 0))))


def holm_adjust(pvalues):
    """Holm 단계적 보정. 입력 순서대로 보정 p 값을 돌려준다."""
    p = np.asarray(pvalues, dtype=float)
    m = len(p)
    order = np.argsort(p)
    adjusted = np.empty(m)
    running = 0.
    for rank, i in enumerate(order):
        running = max(running, (m - rank) * p[i])
        adjusted[i] = min(1., running)
    return adjusted.tolist()


def candidate_ledger_status(target, ledger_model, horizon, last_bar, ledger_root=ROOT / "forecast_history"):
    """공식 원장의 후보 행 현황(읽기만): 관찰일·발행일·만기 도래·비중첩 수."""
    path = Path(ledger_root) / target / "forecast_log.csv"
    if not path.is_file():
        return {"observed": 0, "issued": 0, "matured": 0, "nonoverlap": 0}
    log = pd.read_csv(path)
    rows = log[(log.get("model", "") == ledger_model) & (log.get("kind", "") == "price") & (log["horizon_days"] == horizon)]
    if rows.empty:
        return {"observed": 0, "issued": 0, "matured": 0, "nonoverlap": 0}
    first = rows.sort_values("created_at_utc").groupby("prediction_date").head(1)
    matured = first[pd.to_datetime(first["target_date"]) <= pd.Timestamp(last_bar)]
    dates = pd.to_datetime(matured["prediction_date"]).sort_values()
    nonoverlap, last = 0, None
    for d in dates:
        if last is None or (d - last).days >= 7 * (horizon / 5):
            nonoverlap += 1; last = d
    return {"observed": int(len(first)), "issued": int((first["signal"] == "있음").sum()), "matured": int(len(matured)), "nonoverlap": int(nonoverlap)}


def m07_config(mode):
    return {"task": "M07", "mode": mode, "horizons": list(HORIZONS), "candidates": {f"{k[0]}:{k[1]}": [c["name"] for c in v] for k, v in LOCKED_CANDIDATES.items()},
            "fixed_on": "2026-09-10", "lock": "라벨이 있는 마지막 12개월(M01 계약)", "first_check": FIRST_CHECK,
            "bootstrap_b": 400 if mode == "quick" else 2000, "seed": SEED}


def run_m07(target, mode, storage, results_dir, state, run_notebook_fn=None):
    inputs = None
    for horizon in HORIZONS:
        unit = f"{target}:h{horizon}"
        if state.is_done(unit) and artifacts_intact(state, unit, target, horizon):
            print(f"  이미 완료된 단위 건너뜀: {unit}")
            continue
        if inputs is None:
            inputs = load_inputs(target, mode, storage, run_notebook_fn)
            record_inputs(state, inputs)
        candidates = LOCKED_CANDIDATES.get((target, horizon), [])
        reg, cols = price_design(inputs, horizon)
        sam = inputs["sam"]
        folds, lock_start = evaluation_folds(reg.index, sam.index, horizon)
        lock = folds[-1]
        assert lock["name"] == "lock"
        # 잠금 폴드의 내부 블록: 잠금 시작 전 6개월 × 3(개발 폴드와 같은 규칙)
        t0 = pd.Timestamp(lock["test_start"])
        pos, mature = maturity_positions(reg.index, sam.index, horizon)
        mature_dates = pd.DatetimeIndex(sam.index[mature])
        inner = []
        for k in range(1, INNER_BLOCKS + 1):
            v1, v0 = t0 - pd.DateOffset(months=INNER_MONTHS * (k - 1)), t0 - pd.DateOffset(months=INNER_MONTHS * k)
            test = np.flatnonzero((reg.index >= v0) & (reg.index < v1))
            train = np.flatnonzero(mature_dates < v0)
            if len(test) and len(train) >= MIN_TRAIN_ROWS:
                inner.append({"train": train, "test": test, "test_start": str(v0.date())})
        violations = purge_check(reg.index, sam.index, horizon, [(lock["train"], lock["test"])] + [(i["train"], i["test"]) for i in inner])
        X = reg[cols].to_numpy(dtype=np.float32)
        y = reg["future_return"].to_numpy(dtype=float)
        sigma = reg["sigma_simple"].to_numpy(dtype=float)
        sigma_har = reg["sigma_har"].to_numpy(dtype=float)
        z = y / np.maximum(sigma, 1e-6)
        template = fu.make_price_model()
        raw = fit_predict(template, X, z, sigma, lock["train"], lock["test"])
        ip, iy, ii = [], [], []
        for blk in inner:
            p_ = fit_predict(template, X, z, sigma, blk["train"], blk["test"])
            ip.append(p_); iy.append(y[blk["test"]]); ii.append(blk["test"])
        slope = inner_slope(np.concatenate(ip), np.concatenate(iy)) if ip else 0.
        wins, n_inner = gate_wins(ip, iy, slope)
        centre = slope * raw
        iall = np.concatenate(ii) if ii else np.array([], dtype=int)
        iresid = np.concatenate(iy) - slope * np.concatenate(ip) if ip else np.array([])
        q_simple = band_quantile(iresid, sigma[iall]) if len(iresid) else np.nan
        q_har = band_quantile(iresid, sigma_har[iall]) if len(iresid) else np.nan
        te = lock["test"]
        y_lock, dates_lock = y[te], reg.index[te]
        zero = np.abs(y_lock)
        b = 400 if mode == "quick" else 2000
        block = max(20, 2 * horizon)

        hit_s, score_s, width_s = coverage_and_score(y_lock, centre, q_simple * sigma[te])
        hit_h, score_h, width_h = coverage_and_score(y_lock, centre, q_har * sigma_har[te])
        issued_2 = wins >= 2 and n_inner >= 2
        issued_3 = wins == n_inner and n_inner >= 2
        err_2 = np.abs(y_lock - (centre if issued_2 else 0.))
        err_3 = np.abs(y_lock - (centre if issued_3 else 0.))

        tests, metrics, comparisons = [], [], []
        metrics.append({"target": target, "horizon": horizon, "candidate": "current_context", "fold": "lock", "evaluation_stage": "lock",
                        "n": int(len(y_lock)), "lock_start": str(t0.date()), "lock_end": str(dates_lock[-1].date()), "train_rows": int(len(lock["train"])),
                        "zero_mae": float(zero.mean()), "raw_mae": float(np.abs(y_lock - raw).mean()), "calibrated_mae": float(np.abs(y_lock - centre).mean()),
                        "slope": slope, "gate_wins": f"{wins}/{n_inner}", "interval_coverage_simple": float(hit_s.mean()),
                        "interval_score_simple": float(score_s.mean()), "mean_width_simple": float(width_s.mean()),
                        "note": "잠금 구간은 M00 평가 구간(2024-08~)과 겹쳐 이미 본 점수다. 독립 검증이 아니다."})

        def add_test(label, metric, diff_fn, n):
            lo_m, hi_m = month_block_ci(dates_lock, diff_fn, b=b)
            lo_c, hi_c = contiguous_block_ci(n, diff_fn, block, b=b)
            draws = month_block_draws(dates_lock, diff_fn, b=b)
            delta = float(diff_fn(np.arange(n)))
            comparisons.append({"target": target, "horizon": horizon, "comparison": label, "metric": metric, "delta": delta,
                                "ci_lo": lo_m, "ci_hi": hi_m, "common_n": int(n), "block": "month", "calibrated": True})
            comparisons.append({"target": target, "horizon": horizon, "comparison": label, "metric": metric, "delta": delta,
                                "ci_lo": lo_c, "ci_hi": hi_c, "common_n": int(n), "block": f"contiguous_{block}", "calibrated": True})
            tests.append({"label": label, "metric": metric, "delta": delta, "ci_lo": lo_m, "ci_hi": hi_m, "p_boot": bootstrap_pvalue(draws)})

        cand_rows = []
        for cand in candidates:
            if cand["name"] == "har_interval":
                add_test("har_interval - simple_inner_q", "interval_score", lambda i: float((score_h[i] - score_s[i]).mean()), len(y_lock))
                add_test("har_interval - simple_inner_q", "interval_coverage", lambda i: float(hit_h[i].mean() - hit_s[i].mean()), len(y_lock))
                cand_rows.append({"target": target, "horizon": horizon, "candidate": cand["name"], "fold": "lock", "evaluation_stage": "lock",
                                  "n": int(len(y_lock)), "interval_score": float(score_h.mean()), "interval_coverage": float(hit_h.mean()),
                                  "mean_width": float(width_h.mean()), "reference_interval_score": float(score_s.mean()),
                                  "reference_interval_coverage": float(hit_s.mean()), "reference_mean_width": float(width_s.mean())})
            elif cand["name"] == "strict_gate":
                add_test("strict_gate - hold_current", "mae_return_issued", lambda i: float((err_3[i] - zero[i]).mean()), len(y_lock))
                add_test("strict_gate - gate_2of3", "mae_return_issued", lambda i: float((err_3[i] - err_2[i]).mean()), len(y_lock))
                cand_rows.append({"target": target, "horizon": horizon, "candidate": cand["name"], "fold": "lock", "evaluation_stage": "lock",
                                  "n": int(len(y_lock)), "mae_issued_center": float(err_3.mean()), "issued": bool(issued_3), "gate_wins": f"{wins}/{n_inner}",
                                  "mae_hold": float(zero.mean()), "mae_gate_2of3": float(err_2.mean()), "issued_2of3": bool(issued_2)})
            cand_rows[-1]["ledger"] = candidate_ledger_status(target, cand["ledger_model"], horizon, sam.index[-1])
            cand_rows[-1]["first_check"] = FIRST_CHECK[horizon]
        metrics += [{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v) for k, v in r.items()} for r in cand_rows]
        # 같은 목적의 잠금 검정에 Holm 보정(주 지표만: 구간 점수·발행 중심값)
        primary = [tt for tt in tests if tt["metric"] in ("interval_score", "mae_return_issued") and "gate_2of3" not in tt["label"]]
        for tt, adj in zip(primary, holm_adjust([tt["p_boot"] for tt in primary]) if primary else []):
            tt["p_holm_within_unit"] = adj

        raw_dir = Path(storage) / target
        raw_dir.mkdir(parents=True, exist_ok=True)
        oof_frame = pd.DataFrame({"prediction_date": dates_lock, "y": y_lock, "raw": raw, "center": centre, "sigma": sigma[te], "sigma_har": sigma_har[te],
                                  "halfwidth_simple": q_simple * sigma[te], "halfwidth_har": q_har * sigma_har[te],
                                  "issued_2of3": issued_2, "issued_3of3": issued_3})
        oof_path = raw_dir / f"M07_h{horizon}_{mode}_lock.csv"
        oof_frame.to_csv(oof_path, index=False, lineterminator="\n")

        summary = {
            "target": target, "horizon": horizon, "candidates": [c["name"] for c in candidates], "fixed_on": "2026-09-10",
            "lock": {"start": str(t0.date()), "end": str(dates_lock[-1].date()), "n": int(len(y_lock)), "train_rows": int(len(lock["train"])),
                     "inner_blocks": len(inner), "purge_violations": violations},
            "context": {"zero_mae": float(zero.mean()), "raw_mae": float(np.abs(y_lock - raw).mean()), "calibrated_mae": float(np.abs(y_lock - centre).mean()),
                        "slope": slope, "gate_wins": f"{wins}/{n_inner}", "coverage_simple": float(hit_s.mean()), "coverage_har": float(hit_h.mean())},
            "tests": tests, "candidate_rows": cand_rows,
            "note": "잠금 구간은 M00 평가 구간과 겹쳐 새로운 독립 검증이 아니다. 최종 확인은 원장의 사전 예측(관찰)이다. 통과 못한 후보를 같은 잠금 기간에 재조정하지 않는다.",
            "oof_file": str(oof_path), "oof_sha256": file_sha256(oof_path),
        }
        write_json(state.run_dir / f"summary_{target}_h{horizon}.json", summary)
        save_unit_rows(state, target, horizon, metrics, comparisons)
        state.mark(unit, {"oof_sha256": summary["oof_sha256"], "oof_file": summary["oof_file"], "candidates": [c["name"] for c in candidates],
                          "purge_violations": len(violations)})
        if candidates:
            print(f"  {unit}: 잠금 {t0.date()}~{dates_lock[-1].date()} ({len(y_lock)}행) · " + " · ".join(
                f"{tt['label']}[{tt['metric']}] {tt['delta']:+.5f} [{tt['ci_lo']:+.5f}, {tt['ci_hi']:+.5f}] p={tt['p_boot']:.3f}" for tt in tests))
        else:
            print(f"  {unit}: 고정 후보 없음(미채택으로 종료). 잠금 구간 참고치만 저장.")
    return collect_rows(state, target)


# ---------------------------------------------------------------------------
# R07 — 금리 커브 특징군 (guides/research-candidates-plan.md)
# ---------------------------------------------------------------------------
# 근거: Campbell(1987), Rapach·Strauss·Zhou(2010) — 단기 금리와 커브 기울기가 월 단위 이상
# 지평에서 주식 수익률을 예측한다. 현재 입력에는 10년물(^TNX) 하나뿐이라 커브 정보가 없다.
#
# 자료원은 FRED 의 국채 고정만기(CMT) 금리다. 네 구간을 **한 출처·한 기준**으로 받아야 기울기가
# 성립한다. Yahoo 로 섞으면 ^IRX(13주 할인율)와 ^TNX(CMT)의 기준이 달라 10년−3개월에 계통 편향이
# 들어가고, 무엇보다 2년물이 Yahoo 에 없다(^UST2YR 은 404). 2년물은 시장이 보는 연준 경로의
# 표준 대리 변수라 이 실험의 핵심이다.
#
# FRED 키가 없으면 Yahoo 로 물러서되(3개월·5년·30년) 그 사실을 설정에 남긴다 — config_hash 가
# 달라지므로 두 실행이 섞이지 않는다.
#
# 1일 방향 모델에는 넣지 않는다 — P03 에서 거시류 특징군이 두 종목 모두 유의하게 열위였다.
CURVE_CACHE = "curve_cache"
CURVE_FRED = {"us3m": "DGS3MO", "us2y": "DGS2", "us10y": "DGS10", "us30y": "DGS30"}
CURVE_YAHOO = {"us3m": "^IRX", "us5y": "^FVX", "us10y": "^TNX", "us30y": "^TYX"}


def curve_source():
    """('fred', 시리즈들) 또는 ('yahoo', 티커들). 키가 있으면 FRED 를 쓴다."""
    sys.path.insert(0, str(ROOT))
    from data_sources.oecd import fred_key
    return ("fred", dict(CURVE_FRED)) if fred_key() else ("yahoo", dict(CURVE_YAHOO))


def _fetch_curve_series(source, code, start):
    """한 구간의 일별 금리(%). 반환: Series(index=날짜)."""
    if source == "fred":
        from data_sources.oecd import _fred_request, fred_key
        payload = _fred_request(fred_key(), "series/observations",
                                {"series_id": code, "observation_start": str(start)})
        rows = payload.get("observations") or []
        frame = pd.DataFrame({"date": [r["date"] for r in rows],
                              "close": [r["value"] for r in rows]})
        frame["date"] = pd.to_datetime(frame["date"])
        # 휴일은 '.' 로 온다. 숫자가 아닌 행은 버린다(채우지 않는다).
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        return frame.dropna().set_index("date")["close"]
    import yfinance as yf
    frame = yf.Ticker(code).history(start=str(start), auto_adjust=False)
    if frame is None or frame.empty:
        raise SystemExit(f"{code} 시세를 받지 못했습니다.")
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index)).tz_localize(None).normalize()
    frame.columns = [str(c).strip().lower().replace(" ", "_") for c in frame.columns]
    return frame["close"].astype(float).dropna()


def load_curve_cache(storage, start="2014-01-01"):
    """(출처, {이름: 금리 시계열}). <storage>/curve_cache/<출처>_<이름>.csv 에 받아 두고 다시 쓴다.

    data_cache 와 분리한다 — 거기에 넣으면 data_hash 가 바뀌어 M00~M07 의 고정 입력이 모두
    무효가 된다(M06 의 panel_cache 와 같은 이유). 파일 이름에 출처를 넣어 두 출처가 섞이지 않게 한다.
    """
    source, codes = curve_source()
    cache = Path(storage) / CURVE_CACHE
    cache.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, code in codes.items():
        path = cache / f"{source}_{name}.csv"
        if path.is_file():
            series = pd.read_csv(path, index_col=0, parse_dates=True)["close"]
        else:
            series = _fetch_curve_series(source, code, start)
            series.rename("close").to_frame().to_csv(path)      # 자료만 남긴다. 인증키는 어디에도 쓰지 않는다.
        series = pd.Series(series).astype(float).dropna().sort_index()
        out[name] = series[series > 0]
    return source, out


def curve_features(tenors, dates):
    """금리 커브 특징. 행 d 에는 d 보다 앞선 마지막 관측만 쓴다(그룹 A 와 같은 규칙).

    금리는 수준(%)이라 수익률이 아니라 **차분**을 쓴다. pct_change 는 금리가 0 근처일 때 폭발하고,
    '금리가 몇 %p 움직였나'가 시장이 실제로 보는 양이다. 기울기도 같은 단위의 차다.

    tenors 에는 us10y 가 반드시 있어야 하고, 단기 구간은 us2y(FRED) 또는 us5y(Yahoo 대체)다.
    """
    dates = pd.DatetimeIndex(dates)
    if "us10y" not in tenors:
        raise ValueError("커브에는 us10y 가 필요하다")
    short = "us2y" if "us2y" in tenors else "us5y"
    out = pd.DataFrame(index=dates)
    # 변화·z점수·기울기는 **미국 달력**에서 먼저 계산하고 그 뒤에 한국 예측일로 정렬한다.
    # 정렬을 먼저 하면 창이 한국 달력이 되어 미국 휴장일마다 5일이 4일로 줄어든다(그룹 A 와 같은 규칙).
    for name, series in tenors.items():
        if name == "us10y":
            continue            # 기존 입력의 us10y_* 와 겹치지 않게 수준·차분은 넣지 않는다
        out[f"{name}_chg_5"] = align_before(series.diff(5).dropna(), dates)
        z = (series - series.rolling(60).mean()) / series.rolling(60).std()
        out[f"{name}_level_z60"] = align_before(z.dropna(), dates)
    for label, long_name, short_name in ((f"curve_10y{short[2:]}", "us10y", short),
                                         ("curve_10y3m", "us10y", "us3m"),
                                         ("curve_30y10y", "us30y", "us10y")):
        slope = (tenors[long_name] - tenors[short_name]).dropna()
        out[label] = align_before(slope, dates)
        if label.startswith(f"curve_10y{short[2:]}"):
            out[f"{label}_chg_20"] = align_before(slope.diff(20).dropna(), dates)
    return out.replace([np.inf, -np.inf], np.nan)


def r07_config(mode):
    source, codes = curve_source()
    return {"task": "R07", "mode": mode, "horizons": list(HORIZONS),
            "candidates": ["current_full", "full_plus_E"],
            "curve_source": source, "tenors": codes,
            "substitution": ("" if source == "fred" else
                             "FRED 키가 없어 2년물 대신 5년물(^FVX)을 쓰고 3개월은 할인율 기준(^IRX)이다"),
            "model": "StandardScaler+Ridge(alpha=1e4), target=future_return/sigma_simple",
            "folds": {"first_test": FIRST_TEST, "test_months": TEST_MONTHS, "lock_months": LOCK_MONTHS,
                      "min_train_rows": MIN_TRAIN_ROWS, "min_test_rows": MIN_TEST_ROWS, "inner_blocks": INNER_BLOCKS},
            "bootstrap_b": 400 if mode == "quick" else 2000, "seed": SEED}


def run_r07(target, mode, storage, results_dir, state, run_notebook_fn=None):
    inputs = None
    for horizon in HORIZONS:
        unit = f"{target}:h{horizon}"
        if state.is_done(unit) and artifacts_intact(state, unit, target, horizon):
            print(f"  이미 완료된 단위 건너뜀: {unit}")
            continue
        if inputs is None:
            inputs = load_inputs(target, mode, storage, run_notebook_fn)
            record_inputs(state, inputs)
            curve_src, tenors = load_curve_cache(storage)
            curve = curve_features(tenors, inputs["feat"].index)
            feat_aug = inputs["feat"].copy()
            for col in curve.columns:
                feat_aug[col] = curve[col]
            e_cols = list(curve.columns)
        base_cols = list(inputs["feature_cols"])
        cols_by_candidate = {"current_full": base_cols, "full_plus_E": base_cols + e_cols}
        sam = inputs["sam"]
        reg, _ = fu.price_design_frame(feat_aug, sam.index, base_cols + e_cols,
                                       inputs["sam_raw_close"], feat_aug["sam_vol_20"], horizon)
        reg_base, _ = fu.price_design_frame(feat_aug, sam.index, base_cols,
                                            inputs["sam_raw_close"], feat_aug["sam_vol_20"], horizon)
        folds, lock_start = evaluation_folds(reg.index, sam.index, horizon)
        dev = [f for f in folds if f["name"].startswith("dev") and not f.get("excluded")]
        violations = purge_check(reg.index, sam.index, horizon, [(f["train"], f["test"]) for f in dev]
                                 + [(i["train"], i["test"]) for f in dev for i in f["inner"] if not i.get("excluded")])
        template = fu.make_price_model()
        records, y, sigma = fold_candidate_predictions(reg, cols_by_candidate, folds, horizon, template)
        if not records:
            raise SystemExit(f"{unit}: 개발 폴드가 없습니다.")

        test_idx = np.concatenate([r["test"] for r in records])
        y_dev, dates_dev = y[test_idx], reg.index[test_idx]
        zero = np.abs(y_dev)
        b = 400 if mode == "quick" else 2000
        block = max(20, 2 * horizon)
        preds = {name: {"raw": np.concatenate([r["candidates"][name]["raw"] for r in records]),
                        "calibrated": np.concatenate([r["candidates"][name]["slope"] * r["candidates"][name]["raw"]
                                                      for r in records])}
                 for name in cols_by_candidate}

        def ci_pair(diff, label, metric):
            lo_m, hi_m = month_block_ci(dates_dev, lambda i: float(diff[i].mean()), b=b)
            lo_c, hi_c = contiguous_block_ci(len(diff), lambda i: float(diff[i].mean()), block, b=b)
            base = {"target": target, "horizon": horizon, "comparison": label, "metric": metric,
                    "delta": float(diff.mean()), "common_n": int(len(diff)), "calibrated": "calibrated" in metric}
            return [dict(base, ci_lo=lo_m, ci_hi=hi_m, block="month"),
                    dict(base, ci_lo=lo_c, ci_hi=hi_c, block=f"contiguous_{block}")]

        metrics = [{"target": target, "horizon": horizon, "candidate": "hold_current", "fold": "dev_all",
                    "evaluation_stage": "dev_common", "n": int(len(y_dev)), "mae": float(zero.mean())}]
        comparisons = []
        ref = preds["current_full"]
        for name, pth in preds.items():
            err_raw, err_cal = np.abs(y_dev - pth["raw"]), np.abs(y_dev - pth["calibrated"])
            metrics.append({"target": target, "horizon": horizon, "candidate": name, "fold": "dev_all",
                            "evaluation_stage": "dev_common", "n": int(len(y_dev)),
                            "mae": float(err_raw.mean()), "mae_calibrated": float(err_cal.mean()),
                            "n_features": len(cols_by_candidate[name]),
                            "mean_slope": float(np.mean([r["candidates"][name]["slope"] for r in records]))})
            for r in records:
                te = r["test"]
                metrics.append({"target": target, "horizon": horizon, "candidate": name, "fold": r["fold"],
                                "evaluation_stage": "dev_fold", "n": int(len(te)),
                                "mae": float(np.abs(y[te] - r["candidates"][name]["raw"]).mean()),
                                "zero_mae": float(np.abs(y[te]).mean())})
            if name != "current_full":
                comparisons += ci_pair(err_raw - np.abs(y_dev - ref["raw"]), f"{name} - current_full", "mae_return_raw")
                comparisons += ci_pair(err_cal - np.abs(y_dev - ref["calibrated"]), f"{name} - current_full", "mae_return_calibrated")
            comparisons += ci_pair(err_cal - zero, f"{name} - hold_current", "mae_return_calibrated")

        raw_dir = Path(storage) / target
        raw_dir.mkdir(parents=True, exist_ok=True)
        oof_frame = pd.DataFrame({"prediction_date": dates_dev, "y": y_dev,
                                  **{f"raw_{n}": p_["raw"] for n, p_ in preds.items()},
                                  **{f"cal_{n}": p_["calibrated"] for n, p_ in preds.items()}})
        oof_path = raw_dir / f"R07_h{horizon}_{mode}_oof.csv"
        oof_frame.to_csv(oof_path, index=False, lineterminator="\n")

        delta = next(c for c in comparisons if c["comparison"] == "full_plus_E - current_full"
                     and c["metric"] == "mae_return_calibrated" and c["block"] == "month")
        summary = {
            "target": target, "horizon": horizon, "curve_columns": e_cols,
            "curve_source": curve_src, "tenors": r07_config(mode)["tenors"],
            "substitution": r07_config(mode)["substitution"],
            "n_common_rows": int(len(reg)), "rows_excluded_by_curve": int(len(reg_base) - len(reg)),
            "dev_folds": [r["fold"] for r in records], "n_dev_rows": int(len(y_dev)),
            "lock_start": str(lock_start.date()), "purge_violations": violations,
            "full_plus_E_vs_current_calibrated_month_ci": [delta["delta"], delta["ci_lo"], delta["ci_hi"]],
            "verdict": verdict_for_price(delta),
            "oof_file": str(oof_path), "oof_sha256": file_sha256(oof_path),
        }
        write_json(state.run_dir / f"summary_{target}_h{horizon}.json", summary)
        save_unit_rows(state, target, horizon, metrics, comparisons)
        state.mark(unit, {"oof_sha256": summary["oof_sha256"], "oof_file": summary["oof_file"],
                          "verdict": summary["verdict"], "purge_violations": len(violations)})
        print(f"  {unit}: 커브({curve_src}) {len(e_cols)}열 추가 · 공통 {len(reg)}행(제외 {len(reg_base) - len(reg)}) · "
              f"개발 {len(records)}폴드 · full_plus_E−현행(보정) {delta['delta']:+.5f} "
              f"[{delta['ci_lo']:+.5f}, {delta['ci_hi']:+.5f}] · {summary['verdict']}")
    return collect_rows(state, target)


def verdict_for_price(delta):
    """가격 MAE 비교 판정. 작을수록 좋으므로 CI 상한 < 0 이면 우위다."""
    lo, hi = delta["ci_lo"], delta["ci_hi"]
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return "판단 보류(구간 없음)"
    if hi < 0:
        return "후보가 유의하게 우위"
    if lo > 0:
        return "후보가 유의하게 열위"
    return "동률(CI가 0 포함)"


TASK_RUNNERS = {"M00": run_m00, "M01": run_m01, "M02": run_m02, "M03": run_m03, "M04": run_m04,
                "M05": run_m05, "M06": run_m06, "M07": run_m07, "R07": run_r07}
TASK_CONFIGS = {"M00": m00_config, "M01": m01_config, "M02": m02_config, "M03": m03_config, "M04": m04_config,
                "M05": m05_config, "M06": m06_config, "M07": m07_config, "R07": r07_config}


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

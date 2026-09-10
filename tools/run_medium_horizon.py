# -*- coding: utf-8 -*-
"""5·20거래일 가격 예측 개선 계획(guides/medium-horizon-improvement-plan.md)의 실험 러너.

    python tools/run_medium_horizon.py --task M00 --target samsung --mode quick --storage runs/medium_horizon --resume

원시 예측은 --storage(기본 runs/medium_horizon)/<target>/ 에, 작은 요약은 --results(기본
experiments/medium_horizon)/<task>/<run_id>/ 에 둔다. 시세는 <storage>/<target>/data_cache 의 고정
스냅샷만 읽는다(없으면 실행을 거부한다 — 새로 내려받으면 다른 스냅샷이 된다). 발행은 항상 끈다.

재개·해시 격리·원자적 쓰기는 tools/run_model_improvement.py 의 것을 그대로 쓴다.

M00 — 현행 5·20일 가격 모델(StandardScaler+Ridge(1e4), 변동성 스케일 타깃, simple 변동성)을
노트북과 같은 행·분할로 재현하고, 노트북이 계산한 통계와 수치가 같은지 확인한 뒤, 현재가 유지 대비
raw/보정/발행 중심값 오차, 구간 품질, 변동성 구간별 오차, 비중첩 부분집합, 날짜 기준 purge 검사,
분할·배당 경계 검사, 원장 만기 현황을 저장한다. 성능 개선 주장은 하지 않는다.
"""
import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from run_model_improvement import (  # noqa: E402
    RunState, code_commit, config_hash, data_hash, snapshot_paths, start_run, write_atomic,
    write_json, write_metrics, write_metrics_named,
)

SUPPORTED_TARGETS = ("samsung", "sk_hynix")
MODES = ("quick", "full")
HORIZONS = (5, 20)
COVERAGE = 0.8
TASKS = {"M00": "현행 5·20일 가격 모델 기준선과 실패 유형 진단"}
HORIZON_LABELS = {5: "1주일", 20: "1개월"}


# ---------------------------------------------------------------------------
# 설계 행렬 — 노트북 price_forecast_variants 와 같은 행·열·타깃
# ---------------------------------------------------------------------------
def price_design(ns, horizon):
    """노트북과 같은 설계 행렬. 행 d의 특징은 d-1 종가까지, 타깃은 d-1 종가 대비 d+h-1 원본 종가 수익률.

    HAR 변동성이 비어 있는 앞부분 행은 노트북과 똑같이 제거한다(simple 만 쓰더라도 같은 행에서
    비교한다는 노트북의 규칙). 그래서 학습 행 수가 HAR 워밍업(500행)만큼 줄어 있다.
    """
    feat, sam, feature_cols = ns["feat"], ns["sam"], list(ns["feature_cols"])
    raw_close = ns["sam_raw_close"]
    future_return = raw_close.shift(-(horizon - 1)) / raw_close.shift(1) - 1
    har_series, _ = ns["har_sigma_forecast"](raw_close.pct_change(), horizon)
    reg = feat.loc[sam.index, feature_cols].copy()
    reg["future_return"] = future_return.reindex(reg.index)
    reg["sigma_simple"] = feat.loc[sam.index, "sam_vol_20"] * np.sqrt(horizon)
    reg["sigma_har"] = har_series.reindex(reg.index)
    reg = reg.replace([np.inf, -np.inf], np.nan).dropna()
    return reg, feature_cols


def oof_predictions(X, y, sigma, horizon, template, n_splits):
    """노트북과 같은 TimeSeriesSplit(gap=h-1) OOF. 폴드별 학습·시험 위치도 돌려준다."""
    from sklearn.base import clone
    from sklearn.model_selection import TimeSeriesSplit
    z = y / np.maximum(sigma, 1e-6)
    oof = np.full(len(z), np.nan)
    folds = []
    for k, (train, valid) in enumerate(TimeSeriesSplit(n_splits=n_splits, gap=horizon - 1).split(X)):
        model = clone(template).fit(X[train], z[train])
        oof[valid] = model.predict(X[valid]) * sigma[valid]
        folds.append({"fold": k, "train_rows": int(len(train)), "train_pos": (int(train[0]), int(train[-1])),
                      "test_pos": (int(valid[0]), int(valid[-1]))})
    return oof, folds


# ---------------------------------------------------------------------------
# 진단 도구
# ---------------------------------------------------------------------------
def contiguous_block_ci(n, stat_fn, block_len, b=2000, seed=42, alpha=0.05):
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


def interval_score(y, lower, upper, coverage=COVERAGE):
    """Gneiting-Raftery 구간 점수(작을수록 좋다). 폭 + 벗어난 만큼의 벌점(2/alpha)."""
    alpha = 1 - coverage
    y, lower, upper = (np.asarray(a, dtype=float) for a in (y, lower, upper))
    width = upper - lower
    below = np.clip(lower - y, 0, None)
    above = np.clip(y - upper, 0, None)
    return width + (2 / alpha) * below + (2 / alpha) * above


def maturity_positions(reg_index, sam_index, horizon):
    """설계 행렬 각 행의 (원본 봉 위치, 라벨 만기 봉 위치)."""
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
    cal = np.arange(max(0, cut1 - gap))
    gate = np.arange(cut1, max(cut1, cut2 - gap))
    evaluation = np.arange(cut2, n)
    return cal, gate, evaluation


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
# M00
# ---------------------------------------------------------------------------
def m00_config(mode):
    return {"task": "M00", "mode": mode, "horizons": list(HORIZONS),
            "model": "StandardScaler+Ridge(alpha=1e4), target=future_return/sigma_simple",
            "vol_model": "simple", "coverage": COVERAGE, "n_splits": 3 if mode == "quick" else 5,
            "bootstrap_b": 400 if mode == "quick" else 2000, "contiguous_block": "max(20, 2h)", "seed": 42,
            "calibration": "OOF 50% 보정 / 25% 신호 선택 / 25% 평가 (forecast_utils.calibrate_price_forecast)"}


def load_namespace(target, mode, storage, run_notebook_fn=None):
    snapshot = snapshot_paths(storage, target)
    if not snapshot:
        raise SystemExit(f"{storage}/{target}/data_cache 에 고정 스냅샷이 없습니다. "
                         "runs/model_improvement/P00/<target>/{data_cache,macro_cache,macro_fallback,macro_snapshots} 를 복사하세요.")
    if run_notebook_fn is None:
        from run_notebook import run_notebook as run_notebook_fn
    return run_notebook_fn(Path(storage) / target, targets=target, quick=(mode == "quick"), use_cache=True)[target]


def analyse_horizon(ns, target, horizon, mode, storage):
    """한 조합(target, horizon)의 기준선·진단. 노트북 통계와 동등성을 확인한다."""
    reg, feature_cols = price_design(ns, horizon)
    sam = ns["sam"]
    X = reg[feature_cols].to_numpy(dtype=np.float32)
    y = reg["future_return"].to_numpy(dtype=np.float64)
    sigma = reg["sigma_simple"].to_numpy(dtype=np.float64)
    n_splits = 3 if mode == "quick" else 5
    oof, folds = oof_predictions(X, y, sigma, horizon, ns["make_price_model"](), n_splits)
    mask = ~np.isnan(oof)
    ci_fn = ns["block_bootstrap_ci"]
    stats = ns["calibrate_price_forecast"](y[mask], oof[mask], sigma[mask], reg.index[mask], horizon,
                                           ci_fn, coverage=ns["BAND_COVERAGE"])
    # 노트북이 같은 실행에서 계산한 통계와 수치 동등성
    notebook_stats = ns["price_forecast_stats"].get(HORIZON_LABELS[horizon], {})
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
    current_close = float(ns["sam_raw_close"].iloc[-1])

    def paired(diff, dates, label):
        block = max(20, 2 * horizon)
        lo_m, hi_m = ci_fn(pd.DatetimeIndex(dates), lambda i: float(diff[i].mean()))
        lo_c, hi_c = contiguous_block_ci(len(diff), lambda i: float(diff[i].mean()), block,
                                         b=400 if mode == "quick" else 2000)
        return [{"target": target, "horizon": horizon, "comparison": label, "metric": "mae_return", "delta": float(diff.mean()),
                 "ci_lo": float(lo_m), "ci_hi": float(hi_m), "common_n": int(len(diff)), "block": "month", "calibrated": "raw" not in label},
                {"target": target, "horizon": horizon, "comparison": label, "metric": "mae_return", "delta": float(diff.mean()),
                 "ci_lo": lo_c, "ci_hi": hi_c, "common_n": int(len(diff)), "block": f"contiguous_{block}", "calibrated": "raw" not in label}]

    eval_dates = dm[evaluation]
    comparisons = []
    raw_diff = np.abs(y_eval - raw_eval) - np.abs(y_eval)
    shrunk_diff = np.abs(y_eval - shrunk_eval) - np.abs(y_eval)
    comparisons += paired(raw_diff, eval_dates, "current_ridge_raw - hold_current")
    comparisons += paired(shrunk_diff, eval_dates, "current_ridge_calibrated - hold_current")
    eval_raw_ci = (comparisons[0]["ci_lo"], comparisons[0]["ci_hi"])

    # 날짜 기준 purge 검사: 폴드와 보정/선택/평가 구간 모두
    fold_splits = [(np.arange(f["train_pos"][0], f["train_pos"][1] + 1), np.arange(f["test_pos"][0], f["test_pos"][1] + 1))
                   for f in folds]
    mask_pos = np.flatnonzero(mask)
    stage_splits = [(mask_pos[cal], mask_pos[gate]), (mask_pos[gate], mask_pos[evaluation]), (mask_pos[cal], mask_pos[evaluation])]
    violations = purge_check(reg.index, sam.index, horizon, fold_splits + stage_splits)
    boundary = boundary_anomalies(sam, horizon, reg["future_return"].to_numpy())
    kinds = failure_type(stats, eval_raw_ci, violations, boundary)

    # 원시 OOF 저장(Git 밖). 해시만 manifest 에 남긴다.
    raw_dir = Path(storage) / target
    raw_dir.mkdir(parents=True, exist_ok=True)
    pos, mature = maturity_positions(reg.index, sam.index, horizon)
    stage = np.full(len(ym), "", dtype=object)
    stage[cal], stage[gate], stage[evaluation] = "calibration", "gate", "evaluation"
    oof_frame = pd.DataFrame({"prediction_date": dm, "origin_date": sam.index[pos[mask] - 1],
                              "target_date": sam.index[mature[mask]], "y": ym, "oof_raw": om, "sigma": sm, "stage": stage})
    oof_path = raw_dir / f"M00_h{horizon}_{mode}_oof.csv"
    oof_frame.to_csv(oof_path, index=False, lineterminator="\n")

    zero_mae = float(np.abs(y_eval).mean())
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
        "notebook_equivalence": equivalence, "equivalent": equivalent,
        "stats": {k: (float(v) if isinstance(v, (int, float, np.floating, np.integer)) and not isinstance(v, bool) else v)
                  for k, v in stats.items()},
        "zero_mae_evaluation": zero_mae, "eval_raw_vs_zero_ci_month": list(eval_raw_ci),
        "regimes": regime_table(sm[cal], s_eval, y_eval, raw_eval, shrunk_eval, hit),
        "nonoverlap": nonoverlap_table(evaluation, ym, om, shrunk, horizon),
        "purge_violations": violations, "boundary": boundary,
        "ledger": ledger_summary(target, horizon, sam.index[-1]),
        "failure_types": kinds, "features": feature_cols,
        "oof_file": str(oof_path), "oof_sha256": file_sha256(oof_path),
    }
    return summary, metrics, comparisons


def run_m00(target, mode, storage, results_dir, state, run_notebook_fn=None):
    ns = None
    all_metrics, all_comparisons = [], []
    for horizon in HORIZONS:
        unit = f"{target}:h{horizon}"
        if state.is_done(unit):
            print(f"  이미 완료된 단위 건너뜀: {unit}")
            continue
        if ns is None:
            ns = load_namespace(target, mode, storage, run_notebook_fn)
            state.manifest["notebook"] = {
                "data_snapshot_hash": ns.get("DATA_SNAPSHOT_HASH"), "versions": ns.get("VERSIONS"),
                "prediction_date": str(ns["prediction_date"].date()), "last_bar": str(ns["last_samsung_date"].date()),
                "macro_active": bool(ns.get("MACRO_ACTIVE")), "quick_mode": bool(ns.get("QUICK_MODE")),
                "bootstrap_b": int(ns.get("BOOTSTRAP_B", 0)), "vol_model": ns.get("VOL_MODEL"),
            }
        summary, metrics, comparisons = analyse_horizon(ns, target, horizon, mode, storage)
        write_json(state.run_dir / f"summary_{target}_h{horizon}.json", summary)
        all_metrics += metrics
        all_comparisons += comparisons
        state.mark(unit, {"equivalent": summary["equivalent"], "failure_types": summary["failure_types"],
                          "beats_baseline": summary["stats"]["beats_baseline"], "oof_sha256": summary["oof_sha256"]})
        print(f"  {unit}: 동등성={summary['equivalent']} 발행={summary['stats']['beats_baseline']} "
              f"raw MAE {summary['stats']['raw_model_mae']:.5f} vs 유지 {summary['zero_mae_evaluation']:.5f} · {kinds_text(summary)}")
    return all_metrics, all_comparisons


def kinds_text(summary):
    return " / ".join(summary["failure_types"])


TASK_RUNNERS = {"M00": run_m00}


def execute(task, target, mode, storage, results_dir, resume, run_notebook_fn=None):
    if task not in TASKS:
        raise SystemExit(f"등록되지 않은 작업 ID입니다: {task}. 사용 가능: {', '.join(sorted(TASKS))}")
    if target not in SUPPORTED_TARGETS:
        raise SystemExit(f"지원하지 않는 종목입니다: {target}")
    if mode not in MODES:
        raise SystemExit(f"mode는 quick 또는 full 입니다: {mode}")
    os.environ["PREDICT_STOCK_PUBLISH"] = "false"       # 실험은 절대 발행하지 않는다
    storage, results_dir = Path(storage).resolve(), Path(results_dir).resolve()
    config = m00_config(mode)
    identity = {"task_id": task, "target": target, "mode": mode,
                "data_hash": data_hash(snapshot_paths(storage, target)), "config_hash": config_hash(config)}
    if identity["data_hash"] == "nodata":
        raise SystemExit(f"{storage}/{target}/data_cache 에 고정 스냅샷이 없습니다.")
    state, resumed = start_run(results_dir, task, target, mode, identity, config,
                               extra={"storage": str(storage), "snapshot_files": [p.name for p in snapshot_paths(storage, target)],
                                      "snapshot_sha256": {p.name: file_sha256(p) for p in snapshot_paths(storage, target)}})
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

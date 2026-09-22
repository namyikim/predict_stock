# -*- coding: utf-8 -*-
"""주간(5거래일) OHLCV 시퀀스 실험 러너 — S02.

    python tools/run_weekly_sequence.py --task S02 --target samsung --mode quick \
        --storage runs/weekly_sequence --results experiments/weekly_sequence --resume

계획: guides/weekly-sequence-model-plan.md, guides/weekly-sequence-s02-implementation.md

시세는 <storage>/<target>/data_cache 의 고정 스냅샷(target.parquet/.csv)만 읽는다. 없으면 종료 코드 2 로
거부하고 내려받지 않는다. 산출물은 <results>/<task>/<run_id>/ 에 둔다: manifest.json, metrics.csv,
comparisons.csv, folds.csv, decision.md, checkpoint.json. 재개·해시 격리·원자적 쓰기는
tools/run_model_improvement.py 의 RunState 를 그대로 쓴다. 완료 표시가 있어도 산출물이 없으면 그 단위를
다시 계산한다.

S02 는 인프라 단계다. 세 기준선(현재가 유지, 동일 OHLCV 입력 Ridge, 운영 Ridge — 고정 입력 pkl 이
있을 때만)을 공통 예측일에서 비교해 기준값을 남긴다. 채택 판단을 하지 않고, 잠금 12개월은 열지 않는다
(개발 구간만 — '탐색 평가').
"""
import argparse
import os
import platform
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from run_model_improvement import (  # noqa: E402
    RunState, code_commit, config_hash, data_hash, read_json, replace_with_retry, start_run, write_json,
)
from weekly_sequence_utils import (  # noqa: E402
    CHANNELS, availability_policy, build_sequences, common_dates, corporate_action_flags, flatten_windows,
    load_ohlcv_snapshot, persistence_baseline, ridge_baseline, score, session_calendar, walk_forward_folds,
)

SEED = 42
FIRST_TEST = "2021-01-01"
TEST_MONTHS = 6
LOCK_MONTHS = 12
MIN_TRAIN_ROWS = 500
MIN_TEST_ROWS = 60
UNITS = ("snapshot", "sequences", "folds", "baseline:persistence", "baseline:ohlcv_ridge",
         "baseline:operational_ridge", "comparison")
TCN_SEEDS = (42, 43, 44)
INNER_MONTHS = 6                      # early stopping 용 내부 검증: 학습 구간 마지막 6개월(purge 적용)
ARTIFACTS = {"comparison": ("metrics.csv", "comparisons.csv", "decision.md"), "folds": ("folds.csv",),
             **{f"tcn:seed{k}": (f"predictions_tcn_seed{k}.csv",) for k in TCN_SEEDS},
             "tcn:summary": ("metrics.csv", "comparisons.csv", "decision.md")}


def torch_available():
    if os.environ.get("WEEKLY_SEQ_NO_TORCH"):          # 테스트용: torch 부재를 흉내 낸다
        return False
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def tcn_config(mode, lookback):
    """quick 은 CPU 에서 몇 분 안에 끝나야 한다. full 설정은 상수로만 두고 S03 에서 돌리지 않는다."""
    from weekly_sequence_tcn import TcnConfig
    if mode == "quick":
        return TcnConfig(lookback=lookback, blocks=2, filters=16, kernel=3, dropout=0.1, lr=1e-3,
                         epochs=20, batch=64, patience=5)
    return TcnConfig(lookback=lookback, blocks=3, filters=32, kernel=3, dropout=0.1, lr=1e-3,
                     epochs=60, batch=64, patience=8)


def inner_split(fold, metadata, months=INNER_MONTHS):
    """폴드 학습 행을 (학습, 내부 검증)으로. 검증은 학습 구간 마지막 months 개월, 학습은 그 시작 전에 만기된 행."""
    dates = pd.DatetimeIndex(metadata["prediction_date"])
    maturity = pd.DatetimeIndex(metadata["target_date"])
    train = fold["train"]
    if len(train) == 0:
        return train, train
    v0 = (dates[train[-1]] - pd.DateOffset(months=months)).normalize()
    valid = train[dates[train] >= v0]
    inner_train = train[maturity[train] < v0]                     # purge: 만기가 검증 시작 전
    return inner_train, valid


def s02_config(mode, alpha, lookback, horizon, task="S02"):
    quick = mode == "quick"
    return {"task": task, "lookback": int(lookback), "horizon": int(horizon), "alpha": float(alpha),
            "first_test": FIRST_TEST, "test_months": TEST_MONTHS, "lock_months": LOCK_MONTHS,
            "min_train_rows": 100 if quick else MIN_TRAIN_ROWS, "min_test_rows": 20 if quick else MIN_TEST_ROWS,
            "max_folds": 2 if quick else None, "bootstrap_b": 200 if quick else 2000,
            "channels": list(CHANNELS), "seed": SEED}


def versions():
    import sklearn
    return {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
            "sklearn": sklearn.__version__}


def is_dirty():
    import subprocess
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, timeout=20)
        return bool(out.stdout.strip())
    except Exception:
        return None


def block_bootstrap_diff_ci(dates, err_a, err_b, b, seed):
    """월 블록 부트스트랩으로 MAE(a) − MAE(b) 의 95% CI. 5일 타깃은 날이 겹치므로 날을 독립으로 보지 않는다."""
    months = pd.DatetimeIndex(dates).to_period("M")
    groups = [np.flatnonzero(months == m) for m in months.unique()]
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(b):
        picked = np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))])
        diffs.append(float(np.mean(err_a[picked]) - np.mean(err_b[picked])))
    return float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))


def write_csv(path, frame):
    tmp = Path(str(path) + ".tmp")
    frame.to_csv(tmp, index=False)
    replace_with_retry(tmp, path)


def write_text(path, text):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    replace_with_retry(tmp, path)


def artifacts_intact(state, unit):
    return all((state.run_dir / name).is_file() for name in ARTIFACTS.get(unit, ()))


def maybe_fail_after(unit):
    # 테스트용: 이 단위를 완료한 직후 중단을 흉내 낸다. 운영에서는 설정하지 않는다.
    if os.environ.get("WEEKLY_SEQ_FAIL_AFTER") == unit:
        raise KeyboardInterrupt(f"WEEKLY_SEQ_FAIL_AFTER={unit}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="주간 OHLCV 시퀀스 실험(S02 기준선)")
    parser.add_argument("--task", default="S02", choices=["S02", "S03", "S04"])
    parser.add_argument("--target", required=True, choices=["samsung", "sk_hynix"])
    parser.add_argument("--mode", default="quick", choices=["quick", "full"])
    parser.add_argument("--storage", default="runs/weekly_sequence")
    parser.add_argument("--results", default="experiments/weekly_sequence")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--alpha", type=float, default=1e4)
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--horizon", type=int, default=5)
    args = parser.parse_args(argv)

    storage = Path(args.storage)
    cache_dir = storage / args.target / "data_cache"
    try:
        snapshot = load_ohlcv_snapshot(cache_dir, args.target)
    except FileNotFoundError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    operational = None
    if args.task == "S04":
        # 운영 Ridge 없이는 설계 기준을 적용할 수 없다. skipped 가 아니라 거부한다.
        operational = load_operational_inputs(storage, args.target, args.mode)
        if operational is None:
            print(f"오류: 운영 입력이 없습니다 — {storage}/{args.target}/inputs_{args.mode}_*.pkl. "
                  "S04 는 네 후보 비교라 운영 Ridge 가 필요합니다. medium_horizon 러너(M01 고정 입력)를 먼저 만드세요.",
                  file=sys.stderr)
            return 2
        op_last = pd.Timestamp(operational["sam"].index[-1]).normalize()
        if op_last != snapshot.bars.index[-1]:
            print(f"오류: 운영 입력의 마지막 봉({op_last.date()})과 스냅샷의 마지막 봉({snapshot.bars.index[-1].date()})이 "
                  "다릅니다. 같은 시점의 자료여야 공통 날짜 비교가 성립합니다.", file=sys.stderr)
            return 2
        marker = lock_marker_path(Path(args.results), args.target)
        if marker.is_file():
            opened = read_json(marker) or {}
            print(f"오류: {args.target} 의 잠금 구간은 이미 열렸습니다(run {opened.get('run_id')}, "
                  f"{opened.get('opened_at_utc')}). 두 번째로 열 수 없습니다 — 첫 결과가 판정 근거입니다.", file=sys.stderr)
            return 3
    config = s02_config(args.mode, args.alpha, args.lookback, args.horizon, task=args.task)
    if args.task in ("S03", "S04"):
        config["tcn"] = {"seeds": list(TCN_SEEDS), "inner_months": INNER_MONTHS,
                         "torch": torch_available(),
                         "config": (tcn_config(args.mode, args.lookback).__dict__ if torch_available() else None)}
    dhash = data_hash([snapshot.path])
    identity = {"task_id": args.task, "target": args.target, "mode": args.mode,
                "data_hash": dhash, "config_hash": config_hash(config)}
    extra = {"seed": SEED, "versions": versions(), "dirty": is_dirty(),
             "snapshot": {"path": os.path.relpath(snapshot.path, ROOT), "sha256": snapshot.sha256,
                          "ticker": snapshot.ticker, "adjusted": snapshot.adjusted,
                          "fetched_at": str(snapshot.fetched_at)},
             "availability_policy": None, "corporate_actions": None, "date_range": None,
             "evaluation": "exploratory (development folds only; locked 12 months not opened)"}
    state, resumed = start_run(Path(args.results), args.task, args.target, args.mode, identity, config, extra)
    print(f"{'재개' if resumed else '새 실행'}: {state.run_dir.relative_to(ROOT) if state.run_dir.is_relative_to(ROOT) else state.run_dir}")

    def done(unit):
        if state.is_done(unit) and artifacts_intact(state, unit):
            print(f"  [{unit}] 건너뜀(완료)")
            return True
        if state.is_done(unit):
            print(f"  [{unit}] 완료 표시가 있지만 산출물이 없어 다시 계산")
        return False

    results = state.results()

    try:
        # -- snapshot
        if not done("snapshot"):
            sessions = session_calendar(snapshot.bars.index[0], snapshot.bars.index[-1])
            available_at, prediction_at = availability_policy(snapshot.bars, sessions)
            flags = corporate_action_flags(snapshot.bars)
            state.manifest["availability_policy"] = available_at.attrs.get("policy")
            state.manifest["corporate_actions"] = flags.attrs.get("source")
            state.manifest["date_range"] = {"first_bar": str(snapshot.bars.index[0].date()),
                                            "last_bar": str(snapshot.bars.index[-1].date()),
                                            "bars": int(len(snapshot.bars)), "sessions": int(len(sessions)),
                                            "flagged_corporate_actions": int(flags.sum())}
            state.mark("snapshot", {"bars": int(len(snapshot.bars))})
            maybe_fail_after("snapshot")
        else:
            sessions = session_calendar(snapshot.bars.index[0], snapshot.bars.index[-1])
            available_at, prediction_at = availability_policy(snapshot.bars, sessions)
            flags = corporate_action_flags(snapshot.bars)

        # -- sequences (결정적이라 재계산이 싸다; 캐시하지 않고 다시 만든다)
        batch = build_sequences(snapshot.bars, sessions, available_at, prediction_at, flags,
                                lookback=config["lookback"], horizon=config["horizon"])
        if not done("sequences"):
            reasons = batch.excluded["reason"].value_counts().to_dict() if len(batch.excluded) else {}
            state.mark("sequences", {"samples": int(len(batch.y)), "excluded": {k: int(v) for k, v in reasons.items()}})
            maybe_fail_after("sequences")

        # -- folds
        folds, lock_start = walk_forward_folds(batch.metadata, first_test=config["first_test"],
                                               test_months=config["test_months"], lock_months=config["lock_months"],
                                               min_train_rows=config["min_train_rows"],
                                               min_test_rows=config["min_test_rows"])
        usable = [f for f in folds if not f.get("excluded")]
        if config["max_folds"]:
            usable = usable[-config["max_folds"]:]
        if not done("folds"):
            rows = [{k: v for k, v in f.items() if k not in ("train", "test")} | {"used": f in usable} for f in folds]
            write_csv(state.run_dir / "folds.csv", pd.DataFrame(rows))
            state.mark("folds", {"folds": len(folds), "used": len(usable), "lock_start": str(lock_start.date())})
            maybe_fail_after("folds")
        if not usable:
            raise SystemExit("쓸 수 있는 개발 폴드가 없습니다(자료가 짧거나 first_test 가 자료 뒤입니다).")

        # -- baselines (공통 예측일에서만)
        X = flatten_windows(batch.X)
        y = batch.y
        dates = pd.DatetimeIndex(batch.metadata["prediction_date"])
        test_idx = np.concatenate([f["test"] for f in usable])
        predictions = {}

        if not done("baseline:persistence"):
            predictions["persistence"] = persistence_baseline(y[test_idx])
            state.mark("baseline:persistence", {"n": int(len(test_idx))})
            maybe_fail_after("baseline:persistence")
        else:
            predictions["persistence"] = persistence_baseline(y[test_idx])

        if not done("baseline:ohlcv_ridge"):
            parts = [ridge_baseline(X[f["train"]], y[f["train"]], X[f["test"]], alpha=config["alpha"]) for f in usable]
            predictions["ohlcv_ridge"] = np.concatenate(parts)
            state.mark("baseline:ohlcv_ridge", {"n": int(len(test_idx))})
            maybe_fail_after("baseline:ohlcv_ridge")
        else:
            parts = [ridge_baseline(X[f["train"]], y[f["train"]], X[f["test"]], alpha=config["alpha"]) for f in usable]
            predictions["ohlcv_ridge"] = np.concatenate(parts)

        # 운영 Ridge: medium_horizon 의 고정 입력 pkl 이 있을 때만. 이 러너는 노트북을 돌리지 않는다.
        operational_note = None
        if not done("baseline:operational_ridge"):
            pkl = sorted((storage / args.target).glob(f"inputs_{args.mode}_*.pkl"))
            if pkl:
                operational_note = f"고정 입력 pkl 이 있지만 S02 에서는 연결하지 않았다({pkl[0].name}) — S04 에서 공통 날짜 비교에 넣는다."
            else:
                operational_note = "skipped: operational inputs missing (runs/medium_horizon 고정 입력 pkl 없음)"
            state.mark("baseline:operational_ridge", {"status": "skipped", "note": operational_note})
            maybe_fail_after("baseline:operational_ridge")
        else:
            operational_note = state.results().get("baseline:operational_ridge", {}).get("note")

        # -- comparison
        if not done("comparison"):
            common = common_dates(dates[test_idx], dates[test_idx])       # 두 기준선은 같은 날짜 집합
            mask = np.isin(dates[test_idx], common)
            yt, dt = y[test_idx][mask], dates[test_idx][mask]
            metric_rows, comp_rows = [], []
            for fold in usable:
                fmask = np.isin(dt, dates[fold["test"]])
                for model, pred in predictions.items():
                    s = score(yt[fmask], pred[mask][fmask])
                    metric_rows.append({"target": args.target, "model": model, "fold": fold["name"],
                                        "test_start": fold["test_start"], "test_end": fold["test_end"], **s})
            for model, pred in predictions.items():
                metric_rows.append({"target": args.target, "model": model, "fold": "all_used",
                                    "test_start": usable[0]["test_start"], "test_end": usable[-1]["test_end"],
                                    **score(yt, pred[mask])})
            err_r = np.abs(yt - predictions["ohlcv_ridge"][mask])
            err_p = np.abs(yt - predictions["persistence"][mask])
            lo, hi = block_bootstrap_diff_ci(dt, err_r, err_p, config["bootstrap_b"], SEED)
            comp_rows.append({"target": args.target, "comparison": "ohlcv_ridge_vs_persistence",
                              "mae_diff": float(err_r.mean() - err_p.mean()), "ci_low": lo, "ci_high": hi,
                              "n": int(len(yt)), "bootstrap_b": config["bootstrap_b"], "block": "month"})
            write_csv(state.run_dir / "metrics.csv", pd.DataFrame(metric_rows))
            write_csv(state.run_dir / "comparisons.csv", pd.DataFrame(comp_rows))
            write_text(state.run_dir / "decision.md", decision_text(args, config, state, usable, lock_start,
                                                                     metric_rows, comp_rows, operational_note))
            state.mark("comparison", {"n": int(len(yt)), "mae_diff": float(err_r.mean() - err_p.mean()),
                                      "ci": [lo, hi]})
            maybe_fail_after("comparison")

        # ---------------------------------------------------------------- S03: TCN 후보
        tcn_preds = None
        if args.task in ("S03", "S04"):
            tcn_preds = run_tcn_units(args, config, state, storage, batch, usable, X, y, dates, test_idx,
                                      predictions, done, maybe_fail_after)
            if not done("tcn:summary"):
                write_tcn_summary(args, config, state, usable, lock_start, dates, test_idx, y,
                                  predictions, tcn_preds, operational_note)
                state.mark("tcn:summary", {"seeds": sorted(tcn_preds) if tcn_preds else [],
                                           "status": "skipped" if tcn_preds is None else "done",
                                           **({"note": "skipped: torch missing"} if tcn_preds is None else {})})
                maybe_fail_after("tcn:summary")

        # ---------------------------------------------------------------- S04: 운영 Ridge · 잠금 · 판정
        if args.task == "S04":
            run_s04_units(args, config, state, storage, snapshot, batch, folds, usable, lock_start, X, y, dates,
                          test_idx, predictions, tcn_preds, operational, done, maybe_fail_after)

        state.finish("completed")
        write_json(state.manifest_path, state.manifest)
        print(f"완료: {state.run_dir}")
        return 0
    except KeyboardInterrupt as exc:
        state.manifest["status"] = "interrupted"
        state.manifest["interrupted_reason"] = str(exc)
        write_json(state.manifest_path, state.manifest)
        print(f"중단됨: {exc} — --resume 로 이어서 실행할 수 있습니다.", file=sys.stderr)
        return 130


def run_tcn_units(args, config, state, storage, batch, usable, X, y, dates, test_idx, predictions, done,
                  maybe_fail_after):
    """seed 마다 폴드별 학습·예측. torch 가 없으면 None 을 돌려주고 단위를 skipped 로 기록한다."""
    if not torch_available():
        for k in TCN_SEEDS:
            if not state.is_done(f"tcn:seed{k}"):
                state.mark(f"tcn:seed{k}", {"status": "skipped", "note": "skipped: torch missing (선택 의존성)"})
        print("  [tcn] torch 없음 — S03 단위를 건너뜁니다(requirements.txt 주석 참고).")
        return None
    from weekly_sequence_tcn import fit_tcn, predict_tcn
    cfg = tcn_config(args.mode, config["lookback"])
    ck_root = storage / args.target / "weekly_sequence" / args.task / state.run_dir.name
    state.manifest.setdefault("checkpoints", {})
    preds = {}
    for k in TCN_SEEDS:
        unit = f"tcn:seed{k}"
        out_path = state.run_dir / f"predictions_tcn_seed{k}.csv"
        if done(unit):
            frame = pd.read_csv(out_path, parse_dates=["prediction_date"])
            preds[k] = frame.set_index("prediction_date")["pred"].reindex(dates[test_idx]).to_numpy()
            continue
        rows, parts = [], []
        for fold in usable:
            inner_train, valid = inner_split(fold, batch.metadata)
            if len(inner_train) < 50 or len(valid) < 10:
                raise SystemExit(f"{fold['name']}: 내부 검증을 만들 학습 행이 부족합니다"
                                 f"(학습 {len(inner_train)}, 검증 {len(valid)}).")
            ck = ck_root / f"seed{k}_{fold['name']}.pt"
            fit = fit_tcn(batch.X[inner_train], y[inner_train], batch.X[valid], y[valid], cfg, seed=k,
                          checkpoint_path=ck, resume=True)
            pred = predict_tcn(fit, batch.X[fold["test"]])
            parts.append(pred)
            for i, p_ in zip(fold["test"], pred):
                rows.append({"prediction_date": dates[i], "target_date": batch.metadata["target_date"].iloc[i],
                             "fold": fold["name"], "y": y[i], "pred": p_, "best_epoch": fit.best_epoch})
            state.manifest["checkpoints"][str(k)] = {"path": str(ck), "sha256": _file_sha256(ck),
                                                     "parameters": fit.notes.get("parameters")}
        preds[k] = np.concatenate(parts)
        write_csv(out_path, pd.DataFrame(rows))
        state.mark(unit, {"n": int(len(preds[k])), "parameters": fit.notes.get("parameters")})
        write_json(state.manifest_path, state.manifest)
        maybe_fail_after(unit)
    return preds


# ==============================================================================================
# S04 — 운영 Ridge 연결, 잠금 1회 열기, 기계적 판정
# ==============================================================================================
BASELINES_FOR_VERDICT = ("persistence", "operational_ridge", "ohlcv_ridge")


def lock_marker_path(results_dir, target):
    return Path(results_dir) / "S04" / f"LOCK_OPENED_{target}.json"


def load_operational_inputs(storage, target, mode):
    """runs/medium_horizon 형식의 고정 입력 pkl. 없으면 None."""
    import pickle
    paths = sorted((Path(storage) / target).glob(f"inputs_{mode}_*.pkl"))
    if not paths:
        return None
    with open(paths[-1], "rb") as handle:
        inputs = pickle.load(handle)
    inputs["_path"] = str(paths[-1])
    return inputs


def operational_ridge_predictions(inputs, horizon=5, alpha=1e4, folds=None):
    """운영 5일 가격 모델(StandardScaler+Ridge, sigma 로 나눈 타깃)을 날짜 기준 폴드로 재현.

    특징 행의 날짜는 원본 봉 d-1 이므로 예측일은 그 다음 봉이다. 반환 Series 의 인덱스는 예측일.
    folds 가 None 이면 전 기간을 하나의 확장 창으로 보지 않고, 호출부가 준 폴드마다 학습·예측한다.
    """
    import forecast_utils as fu
    reg, feature_cols = price_design_from_inputs(inputs, horizon)
    # M00 analyse_horizon 과 동일한 수치 경로를 유지한다.
    X = reg[feature_cols].to_numpy(dtype=np.float32)
    y = reg["future_return"].to_numpy(dtype=float)
    sigma = reg["sigma_simple"].to_numpy(dtype=float)
    z = y / np.maximum(sigma, 1e-6)
    sam_index = pd.DatetimeIndex(inputs["sam"].index)
    pos = sam_index.get_indexer(reg.index)
    next_pos = pos + 1
    valid = next_pos < len(sam_index)
    pred_dates = pd.DatetimeIndex([sam_index[i] if ok else pd.NaT for i, ok in zip(next_pos, valid)])
    out = pd.Series(np.nan, index=reg.index, dtype=float)
    if folds is None:
        folds = [{"train": np.arange(len(reg)), "test": np.arange(len(reg))}]
    for fold in folds:
        tr, te = fold["train"], fold["test"]
        if len(tr) == 0 or len(te) == 0:
            continue
        model = fu.make_price_model(alpha=alpha).fit(X[tr], z[tr])
        out.iloc[te] = model.predict(X[te]) * sigma[te]
    out.index = pred_dates
    return out[out.index.notna()]


def price_design_from_inputs(inputs, horizon):
    import forecast_utils as fu
    reg, _ = fu.price_design_frame(inputs["feat"], inputs["sam"].index, inputs["feature_cols"],
                                   inputs["sam_raw_close"], inputs["feat"]["sam_vol_20"], horizon)
    return reg, list(inputs["feature_cols"])


def operational_folds(reg_index, sam_index, horizon, test_windows):
    """운영 설계 행렬 위에 날짜 기준 폴드를 만든다. test_windows: [(t0, t1)] (예측일 기준 반개구간)."""
    feature_dates = pd.DatetimeIndex(reg_index)
    sam_index = pd.DatetimeIndex(sam_index)
    pos = sam_index.get_indexer(feature_dates)
    pred_pos = pos + 1
    maturity_pos = pos + horizon                     # 특징 행 d-1 의 만기 = d + h - 1 = (d-1) + h
    ok = maturity_pos < len(sam_index)
    pred_dates = pd.DatetimeIndex([sam_index[i] if i < len(sam_index) else pd.NaT for i in pred_pos])
    maturity = pd.DatetimeIndex([sam_index[i] if o else pd.NaT for i, o in zip(maturity_pos, ok)])
    folds = []
    for t0, t1 in test_windows:
        test = np.flatnonzero((pred_dates >= t0) & (pred_dates < t1))
        train = np.flatnonzero(ok & (maturity < t0))
        folds.append({"train": train, "test": test})
    return folds, pred_dates


def verdict(dev_comps, lock_comps, seed_mae, operational_mae):
    """설계 기준을 기계적으로 적용한다. (판정, 근거 목록).

    채택 후보: 개발·잠금 모두에서 tcn_mean 이 세 기준선 각각보다 낫고(CI 상한 < 0), seed 셋 모두 운영 Ridge 보다 낫다.
    관찰 후보: 개발에서만 통과. 미채택: 개발에서 실패하거나 seed 하나라도 운영 Ridge 보다 나쁘다.
    """
    reasons = []
    seeds_ok = all(m < operational_mae for m in seed_mae.values())
    if not seeds_ok:
        worse = [k for k, m in seed_mae.items() if m >= operational_mae]
        reasons.append(f"seed {worse} 의 MAE 가 운영 Ridge({operational_mae:.5f}) 이상 — 후보 탈락")
    def passes(comps, label):
        ok = True
        for b in BASELINES_FOR_VERDICT:
            c = comps.get(f"tcn_mean_vs_{b}")
            if c is None:
                reasons.append(f"{label}: {b} 비교 없음"); ok = False; continue
            good = c["mae_diff"] < 0 and c["ci_high"] < 0
            reasons.append(f"{label} vs {b}: MAE 차이 {c['mae_diff']:+.5f}, CI 상한 {c['ci_high']:+.5f} → {'통과' if good else '미통과'}")
            ok = ok and good
        return ok
    dev_ok = passes(dev_comps, "개발")
    lock_ok = passes(lock_comps, "잠금") if lock_comps else False
    if not seeds_ok or not dev_ok:
        return "미채택", reasons
    if dev_ok and lock_ok:
        return "채택 후보", reasons
    return "관찰 후보", reasons


def _comps_map(rows):
    return {r["comparison"]: r for r in rows}


def run_s04_units(args, config, state, storage, snapshot, batch, folds, usable, lock_start, X, y, dates,
                  test_idx, predictions, tcn_preds, operational, done, maybe_fail_after):
    from weekly_sequence_tcn import fit_tcn, predict_tcn
    if tcn_preds is None:
        raise SystemExit("S04 는 TCN 후보가 필요합니다(torch). 설치 후 다시 실행하세요.")
    sam_index = pd.DatetimeIndex(operational["sam"].index)
    reg, _ = price_design_from_inputs(operational, config["horizon"])

    # ---- dev: 운영 Ridge 를 같은 시험 창으로 예측하고 네 후보를 공통 날짜에서 비교
    if not done("dev"):
        windows = [(pd.Timestamp(f["test_start"]), pd.Timestamp(f["test_end"]) + pd.Timedelta(days=1)) for f in usable]
        op_folds, _ = operational_folds(reg.index, sam_index, config["horizon"], windows)
        op_pred = operational_ridge_predictions(operational, config["horizon"], folds=op_folds)
        seq_dates = dates[test_idx]
        common = common_dates(seq_dates, op_pred.index)
        mask = np.isin(seq_dates, common)
        cands = {"persistence": predictions["persistence"][mask], "ohlcv_ridge": predictions["ohlcv_ridge"][mask],
                 "operational_ridge": op_pred.reindex(seq_dates[mask]).to_numpy()}
        for k, pred in tcn_preds.items():
            cands[f"tcn_seed{k}"] = pred[mask]
        cands["tcn_mean"] = np.mean(np.stack([tcn_preds[k] for k in sorted(tcn_preds)]), axis=0)[mask]
        yt, dt = y[test_idx][mask], seq_dates[mask]
        rows, comps = [], []
        for name, pred in cands.items():
            for fold in usable:
                m = np.isin(dt, dates[fold["test"]])
                if m.any():
                    rows.append({"target": args.target, "model": name, "fold": fold["name"],
                                 "seed": int(name[-2:]) if name.startswith("tcn_seed") else np.nan, **score(yt[m], pred[m])})
            rows.append({"target": args.target, "model": name, "fold": "all_used",
                         "seed": int(name[-2:]) if name.startswith("tcn_seed") else np.nan, **score(yt, pred)})
        err_t = np.abs(yt - cands["tcn_mean"])
        for b in BASELINES_FOR_VERDICT:
            err_b = np.abs(yt - cands[b])
            lo, hi = block_bootstrap_diff_ci(dt, err_t, err_b, config["bootstrap_b"], SEED)
            comps.append({"target": args.target, "comparison": f"tcn_mean_vs_{b}", "mae_diff": float(err_t.mean() - err_b.mean()),
                          "ci_low": lo, "ci_high": hi, "n": int(len(yt)), "bootstrap_b": config["bootstrap_b"], "block": "month"})
        write_csv(state.run_dir / "metrics_dev.csv", pd.DataFrame(rows))
        write_csv(state.run_dir / "comparisons_dev.csv", pd.DataFrame(comps))
        state.mark("dev", {"common_n": int(len(yt)), "dropped_seq_dates": int((~mask).sum()),
                           "dropped_operational_dates": int(len(op_pred) - len(common))})
        maybe_fail_after("dev")

    # ---- lock: 잠금 폴드 하나. 학습은 잠금 시작 전에 만기된 행만.
    if not done("lock"):
        marker = lock_marker_path(Path(args.results), args.target)
        if marker.is_file():
            raise SystemExit(f"{args.target} 잠금 구간은 이미 열렸습니다({read_json(marker).get('run_id')}).")
        maturity = pd.DatetimeIndex(batch.metadata["target_date"])
        seq_train = np.flatnonzero(maturity < lock_start)
        seq_test = np.flatnonzero(dates >= lock_start)
        if len(seq_test) == 0 or len(seq_train) < config["min_train_rows"]:
            raise SystemExit(f"잠금 폴드를 만들 수 없습니다(학습 {len(seq_train)}, 시험 {len(seq_test)}).")
        lock_end = dates[-1] + pd.Timedelta(days=1)
        op_folds, _ = operational_folds(reg.index, sam_index, config["horizon"], [(lock_start, lock_end)])
        op_pred = operational_ridge_predictions(operational, config["horizon"], folds=op_folds)
        seq_dates = dates[seq_test]
        common = common_dates(seq_dates, op_pred.index)
        mask = np.isin(seq_dates, common)
        yt, dt = y[seq_test][mask], seq_dates[mask]
        lock_preds = {"persistence": persistence_baseline(yt),
                      "ohlcv_ridge": ridge_baseline(X[seq_train], y[seq_train], X[seq_test], alpha=config["alpha"])[mask],
                      "operational_ridge": op_pred.reindex(seq_dates[mask]).to_numpy()}
        cfg = tcn_config(args.mode, config["lookback"])
        inner_train, valid = inner_split({"train": seq_train}, batch.metadata)
        ck_root = storage / args.target / "weekly_sequence" / args.task / state.run_dir.name
        seed_preds = {}
        for k in TCN_SEEDS:
            fit = fit_tcn(batch.X[inner_train], y[inner_train], batch.X[valid], y[valid], cfg, seed=k,
                          checkpoint_path=ck_root / f"lock_seed{k}.pt", resume=True)
            seed_preds[k] = predict_tcn(fit, batch.X[seq_test])[mask]
            lock_preds[f"tcn_seed{k}"] = seed_preds[k]
        lock_preds["tcn_mean"] = np.mean(np.stack([seed_preds[k] for k in sorted(seed_preds)]), axis=0)
        rows = [{"target": args.target, "model": name, "fold": "lock",
                 "seed": int(name[-2:]) if name.startswith("tcn_seed") else np.nan, **score(yt, pred)}
                for name, pred in lock_preds.items()]
        err_t = np.abs(yt - lock_preds["tcn_mean"])
        comps = []
        for b in BASELINES_FOR_VERDICT:
            err_b = np.abs(yt - lock_preds[b])
            lo, hi = block_bootstrap_diff_ci(dt, err_t, err_b, config["bootstrap_b"], SEED)
            comps.append({"target": args.target, "comparison": f"tcn_mean_vs_{b}", "mae_diff": float(err_t.mean() - err_b.mean()),
                          "ci_low": lo, "ci_high": hi, "n": int(len(yt)), "bootstrap_b": config["bootstrap_b"], "block": "month"})
        write_csv(state.run_dir / "metrics_lock.csv", pd.DataFrame(rows))
        write_csv(state.run_dir / "comparisons_lock.csv", pd.DataFrame(comps))
        # folds.csv 에 잠금 폴드 행을 추가한다(학습 만기 최댓값을 남겨 purge 를 확인할 수 있게).
        fold_frame = pd.read_csv(state.run_dir / "folds.csv")
        lock_row = {"name": "lock", "test_start": str(lock_start.date()), "test_end": str(dates[-1].date()),
                    "train_rows": int(len(seq_train)), "test_rows": int(len(seq_test)),
                    "train_start": str(dates[seq_train[0]].date()), "train_end": str(dates[seq_train[-1]].date()),
                    "train_maturity_max": str(maturity[seq_train].max().date()), "used": True}
        write_csv(state.run_dir / "folds.csv", pd.concat([fold_frame, pd.DataFrame([lock_row])], ignore_index=True))
        from datetime import datetime, timezone
        opened = {"target": args.target, "run_id": state.run_dir.name, "opened_at_utc": datetime.now(timezone.utc).isoformat(),
                  "data_hash": state.manifest.get("data_hash"), "lock_start": str(lock_start.date()),
                  "last_bar": str(snapshot.bars.index[-1].date())}
        write_json(marker, opened)
        state.manifest["lock_opened_at"] = opened["opened_at_utc"]
        state.mark("lock", {"common_n": int(len(yt)), "lock_start": str(lock_start.date())})
        maybe_fail_after("lock")

    # ---- verdict
    if not done("verdict"):
        dev_c = _comps_map(pd.read_csv(state.run_dir / "comparisons_dev.csv").to_dict("records"))
        lock_c = _comps_map(pd.read_csv(state.run_dir / "comparisons_lock.csv").to_dict("records"))
        dev_m = pd.read_csv(state.run_dir / "metrics_dev.csv")
        overall = dev_m[dev_m["fold"] == "all_used"].set_index("model")
        seed_mae = {k: float(overall.loc[f"tcn_seed{k}", "mae"]) for k in TCN_SEEDS}
        label, reasons = verdict(dev_c, lock_c, seed_mae, float(overall.loc["operational_ridge", "mae"]))
        lock_m = pd.read_csv(state.run_dir / "metrics_lock.csv").set_index("model")
        write_text(state.run_dir / "decision.md", s04_decision_text(args, config, state, label, reasons, overall, lock_m,
                                                                     dev_c, lock_c))
        state.mark("verdict", {"verdict": label})
        maybe_fail_after("verdict")


def s04_decision_text(args, config, state, label, reasons, dev_overall, lock_m, dev_c, lock_c):
    def table(frame, title):
        lines = [f"### {title}", "", "| 모델 | seed | MAE | RMSE | 방향 적중률 | n |", "| --- | --- | --- | --- | --- | --- |"]
        for name in ("persistence", "operational_ridge", "ohlcv_ridge", "tcn_seed42", "tcn_seed43", "tcn_seed44", "tcn_mean"):
            if name in frame.index:
                r = frame.loc[name]
                seed = "—" if pd.isna(r.get("seed", np.nan)) else int(r["seed"])
                hit = "—" if pd.isna(r["direction_hit"]) else f"{r['direction_hit']:.3f}"
                lines.append(f"| {name} | {seed} | {r['mae']:.5f} | {r['rmse']:.5f} | {hit} | {int(r['n'])} |")
        return "\n".join(lines) + "\n"
    def comps(c, title):
        lines = [f"### {title}", ""]
        for b in BASELINES_FOR_VERDICT:
            r = c.get(f"tcn_mean_vs_{b}")
            if r:
                lines.append(f"- tcn_mean vs {b}: MAE 차이 {r['mae_diff']:+.5f}, 95% CI [{r['ci_low']:+.5f}, {r['ci_high']:+.5f}], n={int(r['n'])}")
        return "\n".join(lines) + "\n"
    dev_n = int(dev_overall["n"].iloc[0]) if len(dev_overall) else 0
    return "\n".join([
        f"# S04 판정 — {args.target} ({args.mode})", "",
        f"판정: **{label}**", "",
        "**운영 채택은 별도 결정이다**(설계 문서 8절). 이 문서는 설계 기준을 기계적으로 적용한 결과이고, 사람의 판단을 담지 않았다.", "",
        "## 기준", "",
        "- 채택 후보: 개발 폴드와 잠금 구간 **모두**에서 tcn_mean 의 MAE 가 현재가 유지·운영 Ridge·OHLCV Ridge 각각보다 낮고 95% CI 상한 < 0. seed 셋 모두 운영 Ridge 보다 낮아야 한다.",
        "- 관찰 후보: 개발에서만 통과. 미채택: 개발 실패 또는 seed 하나라도 운영 Ridge 이상.", "",
        "## 근거", "", *[f"- {r}" for r in reasons], "",
        f"## 개발 폴드 (공통 예측일 {dev_n}개)", "", table(dev_overall, "지표"), comps(dev_c, "비교"),
        f"## 잠금 구간 (잠금 시작 {state.manifest.get('lock_opened_at', '')[:10] and read_json(lock_marker_path(Path(args.results), args.target)).get('lock_start')})", "",
        table(lock_m, "지표"), comps(lock_c, "비교"),
        "## 한계", "",
        f"- 봉 공개 시각 {state.manifest.get('availability_policy')} · 기업행동 {state.manifest.get('corporate_actions')} — S02 와 같은 가정.",
        "- 잠금 구간은 이 실행에서 한 번 열렸다. 같은 종목에 대해 다시 열 수 없다(LOCK_OPENED 표식).",
        "- 합성 자료로 돌렸다면 판정은 코드 동작 확인일 뿐이다. manifest.snapshot 으로 실제 자료 여부를 확인한다.", "",
        f"code_commit {state.manifest.get('code_commit')} · data_hash {state.manifest.get('data_hash')} · config_hash {state.manifest.get('config_hash')}",
    ]) + "\n"


def _file_sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_tcn_summary(args, config, state, usable, lock_start, dates, test_idx, y, predictions, tcn_preds,
                      operational_note):
    """seed 별·평균 지표를 metrics.csv 에 더하고 비교와 decision.md 를 다시 쓴다."""
    metrics = pd.read_csv(state.run_dir / "metrics.csv")
    metrics = metrics[~metrics["model"].astype(str).str.startswith("tcn")]
    if "seed" not in metrics.columns:
        metrics["seed"] = np.nan
    comps = pd.read_csv(state.run_dir / "comparisons.csv")
    comps = comps[~comps["comparison"].astype(str).str.startswith("tcn")]
    yt, dt = y[test_idx], dates[test_idx]
    if tcn_preds:
        rows = []
        for k, pred in tcn_preds.items():
            for fold in usable:
                m = np.isin(dt, dates[fold["test"]])
                rows.append({"target": args.target, "model": f"tcn_seed{k}", "seed": k, "fold": fold["name"],
                             "test_start": fold["test_start"], "test_end": fold["test_end"], **score(yt[m], pred[m])})
            rows.append({"target": args.target, "model": f"tcn_seed{k}", "seed": k, "fold": "all_used",
                         "test_start": usable[0]["test_start"], "test_end": usable[-1]["test_end"], **score(yt, pred)})
        mean_pred = np.mean(np.stack(list(tcn_preds.values())), axis=0)
        rows.append({"target": args.target, "model": "tcn_mean", "seed": np.nan, "fold": "all_used",
                     "test_start": usable[0]["test_start"], "test_end": usable[-1]["test_end"], **score(yt, mean_pred)})
        metrics = pd.concat([metrics, pd.DataFrame(rows)], ignore_index=True)
        err_t = np.abs(yt - mean_pred)
        new_comps = []
        for name, other in (("persistence", predictions["persistence"]), ("ohlcv_ridge", predictions["ohlcv_ridge"])):
            err_o = np.abs(yt - other)
            lo, hi = block_bootstrap_diff_ci(dt, err_t, err_o, config["bootstrap_b"], SEED)
            new_comps.append({"target": args.target, "comparison": f"tcn_mean_vs_{name}",
                              "mae_diff": float(err_t.mean() - err_o.mean()), "ci_low": lo, "ci_high": hi,
                              "n": int(len(yt)), "bootstrap_b": config["bootstrap_b"], "block": "month"})
        comps = pd.concat([comps, pd.DataFrame(new_comps)], ignore_index=True)
    write_csv(state.run_dir / "metrics.csv", metrics)
    write_csv(state.run_dir / "comparisons.csv", comps)
    base = decision_text(args, config, state, usable, lock_start, metrics.to_dict("records"),
                         comps[comps["comparison"] == "ohlcv_ridge_vs_persistence"].to_dict("records"),
                         operational_note)
    write_text(state.run_dir / "decision.md", base + tcn_decision_text(config, metrics, comps, tcn_preds))


def tcn_decision_text(config, metrics, comps, tcn_preds):
    lines = ["", "## S03 — TCN 후보", ""]
    if not tcn_preds:
        lines.append("- **skipped: torch missing** — 선택 의존성이 없어 TCN 단위를 건너뛰었다. S02 기준선은 위와 같다.")
        return "\n".join(lines) + "\n"
    cfg = config["tcn"]["config"]
    lines += [
        f"- 설정: 블록 {cfg['blocks']}, 필터 {cfg['filters']}, 커널 {cfg['kernel']}, epoch ≤ {cfg['epochs']}, "
        f"patience {cfg['patience']}. 회귀(5일 수익률), 확률·구간 없음.",
        f"- early stopping 은 각 폴드 학습 구간의 마지막 {config['tcn']['inner_months']}개월(purge 적용)로만 했다. 시험 폴드를 보고 고르지 않았다.",
        "- **seed 3개(42·43·44)를 모두 보고한다. 최고 seed 선택 없음.** 평균(tcn_mean)은 세 예측의 평균이다.",
        "", "| 모델 | seed | MAE | RMSE | 방향 적중률 | n |", "| --- | --- | --- | --- | --- | --- |",
    ]
    overall = metrics[(metrics["fold"] == "all_used") & metrics["model"].astype(str).str.startswith("tcn")]
    for _, r in overall.iterrows():
        seed = "—" if pd.isna(r["seed"]) else int(r["seed"])
        hit = "—" if pd.isna(r["direction_hit"]) else f"{r['direction_hit']:.3f}"
        lines.append(f"| {r['model']} | {seed} | {r['mae']:.5f} | {r['rmse']:.5f} | {hit} | {int(r['n'])} |")
    lines.append("")
    for _, c in comps[comps["comparison"].astype(str).str.startswith("tcn")].iterrows():
        lines.append(f"- {c['comparison']}: MAE 차이 {c['mae_diff']:+.5f}, 95% CI [{c['ci_low']:+.5f}, {c['ci_high']:+.5f}] (n={int(c['n'])}).")
    lines += ["", "**채택 판단 없음.** 실제 스냅샷 full 비교와 잠금 구간 평가는 S04 다. 합성 자료라면 위 수치는 코드 동작 확인일 뿐이다."]
    return "\n".join(lines) + "\n"


def decision_text(args, config, state, usable, lock_start, metric_rows, comp_rows, operational_note):
    metrics = pd.DataFrame(metric_rows)
    overall = metrics[metrics["fold"] == "all_used"].set_index("model")
    comp = comp_rows[0]
    lines = [
        f"# S02 결정 — {args.target} ({args.mode})",
        "",
        "**결정: 채택 판단 없음.** S02 는 인프라 단계다. 아래 수치는 기준선의 기준값이지 개선 증거가 아니다.",
        "",
        "## 평가 성격",
        "",
        f"- **탐색 평가**: 개발 폴드만 썼다({usable[0]['test_start']} ~ {usable[-1]['test_end']}, {len(usable)}개). "
        f"잠금 12개월(시작 {lock_start.date()})은 열지 않았다 — M07 이 이미 본 구간이며 S04 까지 닫아 둔다.",
        f"- 공통 예측일 {comp['n']}개에서만 비교했다. 5일 타깃은 날이 겹치므로 월 블록 부트스트랩({comp['bootstrap_b']}회)으로 CI 를 냈다.",
        f"- 입력: {config['lookback']}거래일 × {len(config['channels'])}채널(인과 비율), 세션 기준 d+{config['horizon'] - 1} 만기, 원본 종가 타깃.",
        "",
        "## 기준선 (공통 표본)",
        "",
        "| 모델 | MAE(5일 수익률) | RMSE | 방향 적중률 | n |",
        "| --- | --- | --- | --- | --- |",
    ]
    for model in ("persistence", "ohlcv_ridge"):
        if model in overall.index:
            r = overall.loc[model]
            hit = "—" if pd.isna(r["direction_hit"]) else f"{r['direction_hit']:.3f}"
            lines.append(f"| {model} | {r['mae']:.5f} | {r['rmse']:.5f} | {hit} | {int(r['n'])} |")
    lines += [
        f"| operational_ridge | — | — | — | — |",
        "",
        f"- ohlcv_ridge − persistence MAE 차이 {comp['mae_diff']:+.5f}, 95% CI [{comp['ci_low']:+.5f}, {comp['ci_high']:+.5f}]. "
        "CI 상한이 0보다 작아야 '낫다'고 말할 수 있다(설계 문서 채택 기준). 여기서는 판단하지 않는다.",
        f"- operational_ridge: {operational_note}",
        "",
        "## 한계",
        "",
        f"- 봉 공개 시각: **{state.manifest.get('availability_policy')}** — 세션 16:00 KST 공개라는 정책 가정이다. 실제 수집 시각 자료가 아니다.",
        f"- 기업행동: **{state.manifest.get('corporate_actions')}** — 가격 불연속(KRX 일일 제한폭 ±30% 초과) 휴리스틱. 배당·소규모 행동은 잡지 못한다.",
        "- 이 문서의 수치는 합성 데이터로 돌렸다면 코드 동작만 증명한다. 실제 스냅샷 여부는 manifest 의 snapshot 항목으로 확인한다.",
        "- TCN·확률·구간은 만들지 않았다.",
        "",
        "## 재현",
        "",
        f"    python tools/run_weekly_sequence.py --task S02 --target {args.target} --mode {args.mode} "
        f"--storage {args.storage} --results {args.results} --resume --alpha {config['alpha']:g}",
        "",
        f"code_commit {state.manifest.get('code_commit')} · data_hash {state.manifest.get('data_hash')} · "
        f"config_hash {state.manifest.get('config_hash')} · dirty {state.manifest.get('dirty')}",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())

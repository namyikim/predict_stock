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
    parser.add_argument("--task", default="S02", choices=["S02", "S03"])
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

    config = s02_config(args.mode, args.alpha, args.lookback, args.horizon, task=args.task)
    if args.task == "S03":
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
        if args.task == "S03":
            tcn_preds = run_tcn_units(args, config, state, storage, batch, usable, X, y, dates, test_idx,
                                      predictions, done, maybe_fail_after)
            if not done("tcn:summary"):
                write_tcn_summary(args, config, state, usable, lock_start, dates, test_idx, y,
                                  predictions, tcn_preds, operational_note)
                state.mark("tcn:summary", {"seeds": sorted(tcn_preds) if tcn_preds else [],
                                           "status": "skipped" if tcn_preds is None else "done",
                                           **({"note": "skipped: torch missing"} if tcn_preds is None else {})})
                maybe_fail_after("tcn:summary")

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

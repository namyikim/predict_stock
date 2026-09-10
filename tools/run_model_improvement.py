"""모델 개선 실험 러너 — 작업 하나씩 실행하고 중단 지점에서 재개한다.

`guides/model-improvement-plan.md`의 P00~P15를 한 번에 하나씩 돌린다. 토큰·시간 한계로
중간에 끊겨도 완료한 단위는 남고, 같은 설정으로 다시 부르면 그 단위를 다시 학습하지 않는다.

결과는 계획 4절의 저장 계약을 따른다.

    <storage>/<task_id>/<run_id>/manifest.json    실행 설정·해시·완료 단위
    <storage>/<task_id>/<run_id>/metrics.csv      target,model,fold,... 지표
    <storage>/<task_id>/<run_id>/comparisons.csv  쌍체 비교(있을 때만)
    <storage>/<task_id>/<run_id>/decision.md      채택/동률/미채택 결론
    <storage>/<task_id>/<run_id>/checkpoint.json  재개용 완료 단위

재개 판정은 (task_id, target, mode, data_hash, config_hash)가 모두 같을 때만 성립한다.
입력 데이터나 설정이 바뀌면 옛 체크포인트를 쓰지 않고 새 run_id로 시작한다 — 다른 자료로
얻은 결과가 같은 실험인 척 섞이면 비교가 무의미해지기 때문이다.

공식 발행은 항상 끈다. 이 러너는 forecast_history/와 docs/를 절대 건드리지 않는다.

    python tools/run_model_improvement.py --task P00 --target samsung --mode quick \
        --storage ./outputs/model_improvement
    python tools/run_model_improvement.py --task P00 --target samsung --mode full \
        --storage ./outputs/model_improvement --resume
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_TARGETS = ("samsung", "sk_hynix")
MODES = ("quick", "full")

# 등록된 작업만 실행한다. 계획에 있어도 구현 전이면 여기 없고, 그때는 명확히 거절한다.
# 각 작업을 구현할 때 이 표에 한 줄씩 추가한다.
TASKS = {
    "P00": "현행 기준선과 평가 계약 고정",
    "P03": "기존 특징군의 추가 가치 비교",
    "P04": "학습 기간 비교",
    "P05": "최근 표본 가중 학습",
    "P06": "재학습 주기 비교",
}

# P03에서 비교하는 특징군. 노트북이 같은 폴드·같은 날짜로 이미 계산해 CSV로 남기므로
# 여기서 다시 학습하지 않는다(계획 P03: "이미 있는 것을 새로 수집하는 작업으로 바꾸지 않는다").
FEATURE_GROUPS = {
    "macro_comparison": {
        "group": "월별 지표(macro_)",
        "comparison": "No NSI ensemble − No macro ensemble",
        "note": "선행지수·반도체 수출 등 월별 통계의 추가 효과",
    },
    "nsi_comparison": {
        "group": "뉴스심리(nsi_)",
        "comparison": "Mean ensemble − No NSI ensemble",
        "note": "뉴스심리지수의 추가 효과",
    },
    "flow_comparison": {
        "group": "수급(flow_)",
        "comparison": "Mean ensemble − No flow ensemble",
        "note": "외국인·기관 순매수의 추가 효과",
    },
    "macro_price_comparison": {
        "group": "거시 가격(wti_·usdjpy_·krwjpy_)",
        "comparison": "Mean ensemble − No macro price ensemble",
        "note": "유가·엔화의 추가 효과",
    },
    "improvement_vs_previous": {
        "group": "설정 선택(참고)",
        "comparison": "Mean ensemble − Previous ensemble",
        "note": "특징군이 아니라 설정 변경 효과. 비교용으로만 둔다",
    },
}


# ---------------------------------------------------------------------------
# 해시와 원자적 쓰기
# ---------------------------------------------------------------------------
def code_commit():
    """실행 시점의 Git 커밋. 저장소 밖이거나 git이 없으면 unknown."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def config_hash(config):
    """정렬된 설정의 해시. 키 순서가 달라도 같은 설정이면 같은 값이 나온다."""
    payload = json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def data_hash(paths):
    """입력 스냅샷의 해시. 파일 내용과 상대 경로를 함께 넣어 이름이 바뀌어도 구분된다.

    파일이 하나도 없으면 "nodata"를 돌려준다. 그 상태로 완료 표시가 남으면 나중에
    자료가 생겨도 재개가 옛 결과를 재사용하므로, 호출부에서 그 경우를 걸러야 한다.
    """
    digest = hashlib.sha256()
    found = 0
    for path in sorted(Path(p) for p in paths):
        if not path.is_file():
            continue
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
        found += 1
    return digest.hexdigest()[:20] if found else "nodata"


def write_atomic(path, text):
    """같은 디렉터리에 임시 파일로 쓴 뒤 교체한다. 중간에 끊겨도 반쪽 파일이 남지 않는다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path, payload):
    write_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")


def read_json(path):
    path = Path(path)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None      # 손상된 체크포인트는 없는 것으로 본다(재실행이 안전).


# ---------------------------------------------------------------------------
# 실행 상태
# ---------------------------------------------------------------------------
class RunState:
    """한 실행의 디렉터리와 완료 단위를 관리한다."""

    def __init__(self, run_dir, manifest):
        self.run_dir = Path(run_dir)
        self.manifest = manifest

    # -- 경로
    @property
    def manifest_path(self):
        return self.run_dir / "manifest.json"

    @property
    def checkpoint_path(self):
        return self.run_dir / "checkpoint.json"

    # -- 완료 단위
    def completed(self):
        payload = read_json(self.checkpoint_path) or {}
        return list(payload.get("completed_units", []))

    def results(self):
        payload = read_json(self.checkpoint_path) or {}
        return dict(payload.get("results", {}))

    def is_done(self, unit):
        return unit in self.completed()

    def mark(self, unit, result=None):
        """단위 하나를 완료로 기록한다. 체크포인트를 먼저 쓰고 manifest를 맞춘다."""
        payload = read_json(self.checkpoint_path) or {"completed_units": [], "results": {}}
        units = payload.setdefault("completed_units", [])
        if unit not in units:
            units.append(unit)
        if result is not None:
            payload.setdefault("results", {})[unit] = result
        payload["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(self.checkpoint_path, payload)
        self.manifest["completed_units"] = units
        write_json(self.manifest_path, self.manifest)

    def fail(self, unit, reason):
        payload = read_json(self.checkpoint_path) or {"completed_units": [], "results": {}}
        failed = payload.setdefault("failed_units", [])
        entry = {"unit": unit, "reason": str(reason)[:400]}
        if entry not in failed:
            failed.append(entry)
        write_json(self.checkpoint_path, payload)
        self.manifest["failed_units"] = failed
        self.manifest["status"] = "failed"
        write_json(self.manifest_path, self.manifest)

    def finish(self, status="completed"):
        self.manifest["status"] = status
        self.manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(self.manifest_path, self.manifest)


def find_resumable(task_dir, identity):
    """같은 신원(task/target/mode/data/config)의 기존 실행을 찾는다. 없으면 None.

    실패로 끝난 실행도 재개 대상이다 — 완료한 단위는 그대로 쓰고 실패한 것만 다시 돌린다.
    가장 최근 실행부터 본다.
    """
    task_dir = Path(task_dir)
    if not task_dir.is_dir():
        return None
    for run_dir in sorted(task_dir.iterdir(), reverse=True):
        manifest = read_json(run_dir / "manifest.json")
        if not manifest:
            continue
        if all(manifest.get(key) == value for key, value in identity.items()):
            return RunState(run_dir, manifest)
    return None


def start_run(storage, task_id, target, mode, identity, config, extra=None):
    """재개 가능한 실행이 있으면 그것을, 없으면 새 실행을 돌려준다."""
    task_dir = Path(storage) / task_id
    existing = find_resumable(task_dir, identity)
    if existing is not None:
        existing.manifest["status"] = "running"
        existing.manifest["resumed_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(existing.manifest_path, existing.manifest)
        return existing, True

    # 초 단위 시각만으로는 같은 초에 시작한 두 실행이 같은 이름을 갖는다(설정이 달라
    # 별도 실행이어야 하는 경우에도). 이미 있으면 뒤에 번호를 붙여 갈라놓는다.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = task_dir / f"{stamp}_{target}_{task_id.lower()}"
    run_dir, suffix = base, 1
    while run_dir.exists():
        run_dir = base.with_name(f"{base.name}-{suffix}")
        suffix += 1
    manifest = {
        "task_id": task_id,
        "run_id": run_dir.name,
        "target": target,
        "code_commit": code_commit(),
        "seed": 42,
        "mode": mode,
        "status": "running",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "completed_units": [],
        "failed_units": [],
        "artifact_paths": {},
        "config": config,
    }
    manifest.update(identity)
    if extra:
        manifest.update(extra)
    state = RunState(run_dir, manifest)
    write_json(state.manifest_path, manifest)
    write_json(state.checkpoint_path, {"completed_units": [], "results": {}})
    return state, False


# ---------------------------------------------------------------------------
# P00 — 기준선
# ---------------------------------------------------------------------------
def snapshot_paths(storage_root, target):
    """이 실행이 읽는 고정 시세 스냅샷 파일들."""
    cache = Path(storage_root) / target / "data_cache"
    return sorted(cache.glob("*.parquet")) + sorted(cache.glob("*.csv"))


def p00_config(mode):
    """기준선 계약. 노트북 설정 중 비교에 영향을 주는 값만 담는다."""
    return {
        "headline_model": "No macro ensemble",
        "ensemble_models": ["Logistic", "LightGBM"],
        "target_mode": "close_to_close",
        "band_mode": "vol_scaled",
        "vol_band_mult": 0.3,
        "start_date": "2015-01-01",
        "first_test_date": "2021-01-01",
        "test_months": 6,
        "rolling_train_years": 5,
        "cost_bp": 20.0,
        "seed": 42,
        "mode": mode,
        "classes": ["down", "flat", "up"],
    }


def metrics_rows(namespace, target):
    """노트북 native_metrics를 계약 필드로 옮긴다. 없는 값은 빈 칸으로 두고 0으로 채우지 않는다."""
    frame = namespace["native_metrics"]
    wanted = ["n", "accuracy", "balanced_accuracy", "log_loss", "brier", "auc_gap", "auc_session"]
    rows = []
    for model, row in frame.iterrows():
        record = {"target": target, "model": model,
                  "target_mode": namespace.get("TARGET_MODE", ""), "fold": "all"}
        for key in wanted:
            record[key] = row[key] if key in frame.columns else ""
        rows.append(record)
    return rows


def run_p00(target, mode, storage, state, run_notebook_fn=None):
    """기준선 한 종목을 돌리고 지표를 저장한다. 단위는 종목 하나다."""
    unit = f"{target}:baseline"
    if state.is_done(unit):
        # 지표는 이미 metrics.csv에 있다. None을 돌려 다시 쓰지 않게 한다 —
        # 여기서 요약 dict를 돌려주면 호출부가 그것을 지표 행으로 착각한다.
        print(f"  이미 완료된 단위 건너뜀: {unit} (metrics.csv 유지)")
        return None

    if run_notebook_fn is None:
        sys.path.insert(0, str(ROOT / "tools"))
        from run_notebook import run_notebook as run_notebook_fn

    snapshot = snapshot_paths(storage, target)
    namespaces = run_notebook_fn(Path(storage) / target, targets=target,
                                 quick=(mode == "quick"), use_cache=bool(snapshot))
    namespace = namespaces[target]
    rows = metrics_rows(namespace, target)
    state.mark(unit, {"models": len(rows),
                      "evaluation_days": int(namespace["native_metrics"]["n"].max())})
    return rows


# ---------------------------------------------------------------------------
# P03 — 기존 특징군의 추가 가치
# ---------------------------------------------------------------------------
def latest_notebook_run(storage, target):
    """그 종목의 가장 최근 노트북 실행 디렉터리. 없으면 None."""
    runs = Path(storage) / target / "runs"
    dirs = [d for d in sorted(runs.iterdir()) if d.is_dir()] if runs.is_dir() else []
    return dirs[-1] if dirs else None


def verdict_for(metric, lo, hi):
    """계획 4절 판정 규칙. log_loss는 작을수록, 정확도는 클수록 좋다."""
    if lo is None or hi is None or any(v != v for v in (lo, hi)):
        return "판단 보류(구간 없음)"
    if lo <= 0 <= hi:
        return "동률(CI가 0 포함)"
    better = hi < 0 if metric == "log_loss" else lo > 0
    return "후보가 유의하게 우위" if better else "후보가 유의하게 열위"


def run_p03(target, mode, storage, state, run_notebook_fn=None):
    """노트북이 이미 낸 특징군 비교표를 읽어 판정과 함께 계약 형식으로 모은다.

    다시 학습하지 않는다. 같은 폴드·같은 날짜에서 계산된 값이라야 비교가 성립하는데,
    노트북 실행이 그 조건을 이미 만족하기 때문이다.
    """
    import csv

    unit = f"{target}:feature_groups"
    if state.is_done(unit):
        print(f"  이미 완료된 단위 건너뜀: {unit} (metrics.csv 유지)")
        return None

    run_dir = latest_notebook_run(storage, target)
    if run_dir is None:
        raise SystemExit(
            f"{target}의 노트북 실행 결과가 없습니다: {Path(storage) / target / 'runs'}\n"
            "  먼저 P00을 실행하세요: python tools/run_notebook.py --storage <storage> "
            f"--targets {target} --use-cache")

    features = []
    feature_file = run_dir / "feature_list.csv"
    if feature_file.is_file():
        with open(feature_file, encoding="utf-8-sig") as stream:
            features = [row["feature"] for row in csv.DictReader(stream)]

    rows, missing = [], []
    for name, spec in FEATURE_GROUPS.items():
        path = run_dir / f"{name}.csv"
        if not path.is_file():
            missing.append(name)
            continue
        with open(path, encoding="utf-8-sig") as stream:
            for record in csv.DictReader(stream):
                lo, hi = float(record["lo"]), float(record["hi"])
                rows.append({
                    "target": target, "group": spec["group"],
                    "comparison": spec["comparison"], "metric": record["metric"],
                    "delta": record["delta"], "ci_low": record["lo"], "ci_high": record["hi"],
                    "common_n": record["n"],
                    "verdict": verdict_for(record["metric"], lo, hi),
                    "note": spec["note"],
                })
    if missing:
        print(f"  ⚠️ 비교표 없음(그 지표를 못 받은 실행일 수 있다): {', '.join(missing)}")

    # 특징군별 실제 특징 목록. 접두어로 나눈다 — 노트북의 ablation도 접두어로 뺀다.
    groups = {
        "월별 지표(macro_)": [f for f in features if f.startswith("macro_")],
        "뉴스심리(nsi_)": [f for f in features if f.startswith("nsi_")],
        "수급(flow_)": [f for f in features if f.startswith("flow_")],
        "거시 가격(wti_·usdjpy_·krwjpy_)": [f for f in features
                                        if f.startswith(("wti_", "usdjpy_", "krwjpy_"))],
    }
    known = {f for names in groups.values() for f in names}
    groups["시세만(나머지)"] = [f for f in features if f not in known]

    write_json(state.run_dir / f"feature_groups_{target}.json",
               {"run_dir": str(run_dir), "total_features": len(features),
                "groups": {k: {"n": len(v), "features": v} for k, v in groups.items()},
                "missing_comparisons": missing})
    state.mark(unit, {"comparisons": len(rows), "features": len(features),
                      "notebook_run": run_dir.name})
    return rows


# ---------------------------------------------------------------------------
# P04 — 학습 기간 비교
# ---------------------------------------------------------------------------
# 후보는 넷으로 제한한다. 후보를 늘릴수록 "그중 하나는 좋아 보인다"가 쉬워진다
# (계획 4절: 반복 비교의 낙관 편향).
WINDOW_CANDIDATES = ("2y", "3y", "5y", "expanding")
BASELINE_WINDOW = "5y"
MIN_TRAIN_ROWS = 500      # 노트북 폴드 생성과 같은 기준


def window_train_indices(date_index, before, window):
    """창 이름으로 학습 행 위치를 고른다. 예측일 당일은 어떤 창에서도 넣지 않는다."""
    # 선언한 후보만 받는다. 형식만 맞으면 통과시키면 후보를 몰래 늘릴 수 있고, 그러면
    # "그중 하나는 좋아 보인다"가 쉬워진다(계획 4절의 반복 비교 편향).
    if window not in WINDOW_CANDIDATES:
        raise ValueError(f"등록되지 않은 학습 창입니다: {window}. 사용 가능: {', '.join(WINDOW_CANDIDATES)}")
    dates = pd.DatetimeIndex(date_index)
    before = pd.Timestamp(before)
    if window == "expanding":
        return np.flatnonzero(dates < before)
    years = int(window[:-1])
    return np.flatnonzero((dates >= before - pd.DateOffset(years=years)) & (dates < before))


def _ensemble_probabilities(ns, features, y, train_idx, test_idx):
    """대표 모델과 같은 방식: Logistic·LightGBM을 각각 적합해 확률을 단순 평균한다."""
    probs = []
    for family in ("Logistic", "LightGBM"):
        fitted = ns["fit_direction_model"](features, y, train_idx, family,
                                           seed=ns.get("SEED", 42),
                                           selection=ns.get("SELECTION_METRIC", "log_loss"))
        probs.append(ns["predict_direction_model"](fitted, features[test_idx]))
    return np.mean(probs, axis=0)


def _inner_choice(ns, features, y, dates, train_idx, inner_months=6):
    """외부 평가를 보지 않고 창을 고른다.

    학습 구간의 마지막 inner_months를 내부 검증으로 떼고, 각 창 후보를 그 앞 자료로만
    학습해 내부 log loss가 가장 낮은 창을 고른다. 외부 폴드의 라벨은 쓰지 않는다.
    """
    train_dates = pd.DatetimeIndex(dates)[train_idx]
    split = train_dates[-1] - pd.DateOffset(months=inner_months)
    inner_test = train_idx[train_dates > split]
    if len(inner_test) < 20:
        return BASELINE_WINDOW, {}
    scores = {}
    for window in WINDOW_CANDIDATES:
        inner_train = window_train_indices(dates, train_dates[train_dates > split][0], window)
        inner_train = np.intersect1d(inner_train, train_idx)
        if len(inner_train) < MIN_TRAIN_ROWS or len(np.unique(y[inner_train])) < 2:
            continue
        probs = _ensemble_probabilities(ns, features, y, inner_train, inner_test)
        scores[window] = ns["probability_loss"](y[inner_test], probs)
    if not scores:
        return BASELINE_WINDOW, {}
    return min(scores, key=scores.get), scores


def run_p04(target, mode, storage, state, run_notebook_fn=None):
    """창 후보별로 같은 외부 폴드에서 워크포워드를 다시 돌리고 쌍체 비교한다."""
    import time

    unit = f"{target}:windows"
    if state.is_done(unit):
        print(f"  이미 완료된 단위 건너뜀: {unit} (metrics.csv 유지)")
        return None

    if run_notebook_fn is None:
        sys.path.insert(0, str(ROOT / "tools"))
        from run_notebook import run_notebook as run_notebook_fn

    snapshot = snapshot_paths(storage, target)
    ns = run_notebook_fn(Path(storage) / target, targets=target,
                         quick=(mode == "quick"), use_cache=bool(snapshot))[target]

    # 대표 모델의 입력(시세만)을 그대로 쓴다. P03에서 고정한 특징 목록이다.
    features, y, dates, folds = ns["market_X"], ns["y"], ns["dates"], ns["folds"]
    frames, notes, timing = [], [], {}

    for window in WINDOW_CANDIDATES:
        started, kept, skipped = time.time(), [], []
        rows_used = []
        for fold in folds:
            test_idx = fold["test_idx"]
            train_idx = window_train_indices(dates, dates[test_idx[0]], window)
            if len(train_idx) < MIN_TRAIN_ROWS:
                skipped.append({"fold": fold["fold"], "reason": f"학습 행 {len(train_idx)} < {MIN_TRAIN_ROWS}"})
                continue
            if len(np.unique(y[train_idx])) < 3:
                skipped.append({"fold": fold["fold"],
                                "reason": f"클래스 {len(np.unique(y[train_idx]))}종만 존재"})
                continue
            probs = _ensemble_probabilities(ns, features, y, train_idx, test_idx)
            frames.append(ns["prediction_frame"](f"window {window}", dates[test_idx],
                                                 y[test_idx], probs, fold["fold"]))
            kept.append(fold["fold"])
            rows_used.append(len(train_idx))
        timing[window] = {"seconds": round(time.time() - started, 1), "folds": len(kept),
                          "mean_train_rows": int(np.mean(rows_used)) if rows_used else 0,
                          "skipped": skipped}
        if skipped:
            notes.append(f"{window}: {len(skipped)}개 폴드 제외 — {skipped[0]['reason']}")
        print(f"  {window}: {len(kept)}폴드 · 평균 학습 {timing[window]['mean_train_rows']}행 "
              f"· {timing[window]['seconds']}초" + (f" · 제외 {len(skipped)}" if skipped else ""))

    predictions = pd.concat(frames, ignore_index=True)

    # 내부 검증만으로 고른 창(외부 라벨을 보지 않는다)
    chosen, inner_scores = _inner_choice(ns, features, y, dates, folds[-1]["train_idx"])
    print(f"  내부 검증이 고른 창: {chosen}  (내부 log loss {inner_scores})")

    rows, comparisons = [], []
    metrics = ns["summarize_predictions"](predictions, with_ci=False)
    for model, row in metrics.iterrows():
        window = model.replace("window ", "")
        rows.append({"target": target, "model": model, "window": window,
                     "target_mode": "close_to_close", "fold": "all",
                     **{k: row.get(k, "") for k in ("n", "accuracy", "balanced_accuracy",
                                                    "log_loss", "brier", "auc_gap", "auc_session")},
                     "seconds": timing[window]["seconds"],
                     "mean_train_rows": timing[window]["mean_train_rows"],
                     "folds": timing[window]["folds"]})
    for window in WINDOW_CANDIDATES:
        if window == BASELINE_WINDOW:
            continue
        for metric in ("balanced_accuracy", "log_loss"):
            delta = ns["paired_delta_ci"](predictions, f"window {window}",
                                          f"window {BASELINE_WINDOW}", metric)
            comparisons.append({
                "target": target, "comparison": f"{window} − {BASELINE_WINDOW}", "metric": metric,
                "delta": delta["delta"], "ci_low": delta["lo"], "ci_high": delta["hi"],
                "common_n": delta["n"],
                "verdict": verdict_for(metric, delta["lo"], delta["hi"])})

    write_json(state.run_dir / f"windows_{target}.json",
               {"timing": timing, "inner_selection": {"chosen": chosen, "scores": inner_scores},
                "notes": notes, "comparisons": comparisons})
    write_metrics_named(state, comparisons, "comparisons.csv")
    state.mark(unit, {"windows": len(WINDOW_CANDIDATES), "chosen_by_inner": chosen,
                      "evaluation_days": int(metrics["n"].max())})
    return rows


# ---------------------------------------------------------------------------
# P05 — 최근 표본 가중 학습
# ---------------------------------------------------------------------------
# 계획이 정한 네 후보만 쓴다. None은 무가중(기준선)이다.
HALF_LIFE_CANDIDATES = (None, 126, 252, 504)


def _weights_for(ns, dates, train_idx, half_life):
    """학습 구간 안에서의 거래일 나이로 가중치를 만들어 전체 길이 배열에 채운다.

    각 폴드의 마지막 학습 관측이 age 0이다. 학습 구간 밖은 1로 두지만
    fit_direction_model이 train_indices로 자르므로 값 자체는 쓰이지 않는다.

    노트북 네임스페이스의 recency_weights를 쓴다. 실험이 노트북과 같은 코드로 가중치를
    만들어야 결과를 그대로 반영할 수 있다.
    """
    weights = np.ones(len(dates), dtype=float)
    weights[train_idx] = ns["recency_weights"](len(train_idx), half_life)
    return weights


def _weighted_ensemble(ns, features, y, dates, train_idx, test_idx, half_life):
    weights = None if half_life is None else _weights_for(ns, dates, train_idx, half_life)
    probs = []
    for family in ("Logistic", "LightGBM"):
        fitted = ns["fit_direction_model"](features, y, train_idx, family,
                                           seed=ns.get("SEED", 42),
                                           selection=ns.get("SELECTION_METRIC", "log_loss"),
                                           sample_weight=weights)
        probs.append(ns["predict_direction_model"](fitted, features[test_idx]))
    return np.mean(probs, axis=0)


def _inner_half_life(ns, features, y, dates, train_idx, inner_months=6):
    """외부 라벨을 보지 않고 반감기를 고른다. 내부 검증 점수는 동일 가중으로 잰다."""
    train_dates = pd.DatetimeIndex(dates)[train_idx]
    split = train_dates[-1] - pd.DateOffset(months=inner_months)
    inner_test = train_idx[train_dates > split]
    inner_train = train_idx[train_dates <= split]
    if len(inner_test) < 20 or len(inner_train) < MIN_TRAIN_ROWS:
        return None, {}
    scores = {}
    for half_life in HALF_LIFE_CANDIDATES:
        probs = _weighted_ensemble(ns, features, y, dates, inner_train, inner_test, half_life)
        scores["none" if half_life is None else str(half_life)] = ns["probability_loss"](y[inner_test], probs)
    best = min(scores, key=scores.get)
    return (None if best == "none" else int(best)), scores


def run_p05(target, mode, storage, state, run_notebook_fn=None):
    """반감기 후보별로 같은 외부 폴드에서 워크포워드를 다시 돌리고 무가중과 비교한다."""
    import time

    unit = f"{target}:half_life"
    if state.is_done(unit):
        print(f"  이미 완료된 단위 건너뜀: {unit} (metrics.csv 유지)")
        return None
    if run_notebook_fn is None:
        sys.path.insert(0, str(ROOT / "tools"))
        from run_notebook import run_notebook as run_notebook_fn

    snapshot = snapshot_paths(storage, target)
    ns = run_notebook_fn(Path(storage) / target, targets=target,
                         quick=(mode == "quick"), use_cache=bool(snapshot))[target]
    features, y, dates, folds = ns["market_X"], ns["y"], ns["dates"], ns["folds"]

    frames, timing = [], {}
    for half_life in HALF_LIFE_CANDIDATES:
        label = "none" if half_life is None else str(half_life)
        started = time.time()
        for fold in folds:
            probs = _weighted_ensemble(ns, features, y, dates,
                                       fold["train_idx"], fold["test_idx"], half_life)
            frames.append(ns["prediction_frame"](f"half_life {label}", dates[fold["test_idx"]],
                                                 y[fold["test_idx"]], probs, fold["fold"]))
        timing[label] = {"seconds": round(time.time() - started, 1), "folds": len(folds)}
        print(f"  half_life={label}: {len(folds)}폴드 · {timing[label]['seconds']}초")

    predictions = pd.concat(frames, ignore_index=True)
    chosen, inner_scores = _inner_half_life(ns, features, y, dates, folds[-1]["train_idx"])
    print(f"  내부 검증이 고른 반감기: {chosen}  {inner_scores}")

    rows, comparisons = [], []
    metrics = ns["summarize_predictions"](predictions, with_ci=False)
    for model, row in metrics.iterrows():
        label = model.replace("half_life ", "")
        rows.append({"target": target, "model": model, "half_life": label,
                     "target_mode": "close_to_close", "fold": "all",
                     **{k: row.get(k, "") for k in ("n", "accuracy", "balanced_accuracy",
                                                    "log_loss", "brier", "auc_gap", "auc_session")},
                     "seconds": timing[label]["seconds"]})
    for half_life in HALF_LIFE_CANDIDATES:
        if half_life is None:
            continue
        for metric in ("balanced_accuracy", "log_loss"):
            delta = ns["paired_delta_ci"](predictions, f"half_life {half_life}",
                                          "half_life none", metric)
            comparisons.append({
                "target": target, "comparison": f"half_life {half_life} − 무가중", "metric": metric,
                "delta": delta["delta"], "ci_low": delta["lo"], "ci_high": delta["hi"],
                "common_n": delta["n"], "verdict": verdict_for(metric, delta["lo"], delta["hi"])})

    write_json(state.run_dir / f"half_life_{target}.json",
               {"timing": timing, "inner_selection": {"chosen": chosen, "scores": inner_scores},
                "comparisons": comparisons,
                "note": "class_weight='balanced'와 곱해진다. 선택된 params는 metrics의 모델별 기록 참조."})
    write_metrics_named(state, comparisons, "comparisons.csv")
    state.mark(unit, {"candidates": len(HALF_LIFE_CANDIDATES), "chosen_by_inner": chosen,
                      "evaluation_days": int(metrics["n"].max())})
    return rows


# ---------------------------------------------------------------------------
# P06 — 재학습 주기
# ---------------------------------------------------------------------------
# 기준선은 폴드 시작 1회 학습(= 약 126거래일 주기). 후보는 계획이 정한 셋뿐이다.
RETRAIN_CANDIDATES = (1, 5, 21)


def retrain_positions(n_days, every):
    """시험 구간 안에서 재학습하는 위치(0부터). 달력이 아니라 거래일 인덱스로 센다.

    휴일이 며칠 끼든 '5거래일마다'는 위치 0, 5, 10…이다. 결정적이라 재개해도 같다.
    """
    if every not in RETRAIN_CANDIDATES:
        raise ValueError(f"등록되지 않은 재학습 주기입니다: {every}. 사용 가능: {RETRAIN_CANDIDATES}")
    return list(range(0, int(n_days), int(every)))


class ScheduledPredictor:
    """폴드 시작에 설정(params·온도)을 한 번 고르고, 정해진 주기에만 추정기를 다시 적합한다.

    재학습하지 않는 날은 직전 모델을 그대로 쓰고 그날의 새 특징으로만 예측한다. 하루 틀렸다고
    그날 다시 학습하지 않는다 — 그러면 주기 비교가 아니라 오답 반응 비교가 된다.
    재학습 시점의 학습 창은 그 예측일 직전까지의 지정 창(기본 5년)이다.
    """

    def __init__(self, X, y, dates, family, every, window="5y", seed=42, selection="log_loss", ns=None):
        self.X, self.y, self.dates = np.asarray(X), np.asarray(y), pd.DatetimeIndex(dates)
        self.family, self.every, self.window, self.seed, self.selection = family, every, window, seed, selection
        # 노트북 네임스페이스가 있으면 그 함수를(같은 코드 보장), 없으면 forecast_utils를 쓴다.
        if ns is None:
            sys.path.insert(0, str(ROOT))
            import forecast_utils as fu
            self._fit, self._predict = fu.fit_direction_model, fu.predict_direction_model
            self._estimator, self._aligned, self._temper = (
                fu.direction_estimator, fu.aligned_probabilities, fu.temperature_probabilities)
        else:
            self._fit, self._predict = ns["fit_direction_model"], ns["predict_direction_model"]
            self._estimator, self._aligned, self._temper = (
                ns["direction_estimator"], ns["aligned_probabilities"], ns["temperature_probabilities"])

    def predict_window(self, train_idx, test_idx):
        train_idx, test_idx = np.asarray(train_idx), np.asarray(test_idx)
        # 설정 선택은 폴드 시작에 한 번. 외부 구간 라벨은 보지 않는다.
        first = self._fit(self.X, self.y, train_idx, self.family, seed=self.seed, selection=self.selection)
        params, temperature = first["selection"]["params"], first["temperature"]
        schedule = set(retrain_positions(len(test_idx), self.every))
        probs = np.zeros((len(test_idx), 3), dtype=float)
        log, estimator, version, trained_until = [], first["estimator"], 0, self.dates[train_idx[-1]]
        for position, row in enumerate(test_idx):
            retrained = False
            if position in schedule and position > 0:
                before = self.dates[row]
                idx = window_train_indices(self.dates, before, self.window)
                if len(idx) >= 100 and len(np.unique(self.y[idx])) > 1:
                    estimator = self._estimator(self.family, params, self.seed).fit(self.X[idx], self.y[idx])
                    version += 1
                    trained_until = self.dates[idx[-1]]
                    retrained = True
            elif position == 0:
                retrained = True
            probs[position] = self._temper(self._aligned(estimator, self.X[[row]]), temperature)[0]
            log.append({"position": position, "date": self.dates[row], "retrained": retrained,
                        "model_version": version, "trained_until": trained_until,
                        "params": params, "temperature": temperature})
        return probs, log


def run_p06(target, mode, storage, state, run_notebook_fn=None):
    """주기별로 같은 외부 폴드·같은 날짜를 예측하고 기준선(폴드당 1회)과 비교한다."""
    import time

    unit = f"{target}:retrain"
    if state.is_done(unit):
        print(f"  이미 완료된 단위 건너뜀: {unit} (metrics.csv 유지)")
        return None
    if run_notebook_fn is None:
        sys.path.insert(0, str(ROOT / "tools"))
        from run_notebook import run_notebook as run_notebook_fn

    snapshot = snapshot_paths(storage, target)
    ns = run_notebook_fn(Path(storage) / target, targets=target,
                         quick=(mode == "quick"), use_cache=bool(snapshot))[target]
    features, y, dates, folds = ns["market_X"], ns["y"], ns["dates"], ns["folds"]
    seed, selection = ns.get("SEED", 42), ns.get("SELECTION_METRIC", "log_loss")

    frames, timing, train_counts = [], {}, {}
    # 기준선: 폴드 시작 1회 학습 = 기존 방식 그대로.
    started = time.time()
    for fold in folds:
        probs = _ensemble_probabilities(ns, features, y, fold["train_idx"], fold["test_idx"])
        frames.append(ns["prediction_frame"]("retrain fold", dates[fold["test_idx"]],
                                             y[fold["test_idx"]], probs, fold["fold"]))
    timing["fold"] = round(time.time() - started, 1); train_counts["fold"] = len(folds)
    print(f"  retrain=fold(기준): {len(folds)}폴드 · {timing['fold']}초")

    for every in RETRAIN_CANDIDATES:
        started, fits = time.time(), 0
        for fold in folds:
            member_probs = []
            for family in ("Logistic", "LightGBM"):
                predictor = ScheduledPredictor(features, y, dates, family, every=every,
                                               window=BASELINE_WINDOW, seed=seed, selection=selection, ns=ns)
                probs, log = predictor.predict_window(fold["train_idx"], fold["test_idx"])
                member_probs.append(probs)
                fits += sum(r["retrained"] for r in log)
            frames.append(ns["prediction_frame"](f"retrain {every}", dates[fold["test_idx"]],
                                                 y[fold["test_idx"]], np.mean(member_probs, axis=0), fold["fold"]))
        timing[str(every)] = round(time.time() - started, 1); train_counts[str(every)] = fits
        print(f"  retrain={every}: 실제 학습 {fits}회 · {timing[str(every)]}초")

    predictions = pd.concat(frames, ignore_index=True)
    metrics = ns["summarize_predictions"](predictions, with_ci=False)
    rows, comparisons = [], []
    for model, row in metrics.iterrows():
        label = model.replace("retrain ", "")
        rows.append({"target": target, "model": model, "retrain_every": label,
                     "target_mode": "close_to_close", "fold": "all",
                     **{k: row.get(k, "") for k in ("n", "accuracy", "balanced_accuracy",
                                                    "log_loss", "brier", "auc_gap", "auc_session")},
                     "seconds": timing[label], "actual_trainings": train_counts[label]})
    for every in RETRAIN_CANDIDATES:
        for metric in ("balanced_accuracy", "log_loss"):
            delta = ns["paired_delta_ci"](predictions, f"retrain {every}", "retrain fold", metric)
            comparisons.append({"target": target, "comparison": f"매 {every}거래일 − 폴드당 1회",
                                "metric": metric, "delta": delta["delta"], "ci_low": delta["lo"],
                                "ci_high": delta["hi"], "common_n": delta["n"],
                                "verdict": verdict_for(metric, delta["lo"], delta["hi"])})
    write_json(state.run_dir / f"retrain_{target}.json",
               {"timing": timing, "actual_trainings": train_counts, "comparisons": comparisons,
                "note": "설정(params·온도)은 폴드 시작에 고정. 재학습은 추정기 재적합만."})
    write_metrics_named(state, comparisons, "comparisons.csv")
    state.mark(unit, {"candidates": list(RETRAIN_CANDIDATES), "evaluation_days": int(metrics["n"].max())})
    return rows


TASK_RUNNERS = {"P00": run_p00, "P03": run_p03, "P04": run_p04, "P05": run_p05, "P06": run_p06}


# ---------------------------------------------------------------------------
def write_metrics(state, rows):
    if not rows:
        return
    import csv
    import io
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path = state.run_dir / "metrics.csv"
    write_atomic(path, buffer.getvalue())
    state.manifest.setdefault("artifact_paths", {})["metrics"] = str(path)
    write_json(state.manifest_path, state.manifest)


def write_metrics_named(state, rows, filename):
    """metrics.csv 외의 표(비교표 등)를 같은 방식으로 쓴다."""
    if not rows:
        return
    import csv
    import io as _io
    buffer = _io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path = state.run_dir / filename
    write_atomic(path, buffer.getvalue())
    state.manifest.setdefault("artifact_paths", {})[Path(filename).stem] = str(path)
    write_json(state.manifest_path, state.manifest)


def execute(task, target, mode, storage, resume, run_notebook_fn=None):
    if task not in TASKS:
        raise SystemExit(f"등록되지 않은 작업 ID입니다: {task}. 사용 가능: {', '.join(sorted(TASKS))}")
    if target not in SUPPORTED_TARGETS:
        raise SystemExit(f"지원하지 않는 종목입니다: {target}. 사용 가능: {', '.join(SUPPORTED_TARGETS)}")
    if mode not in MODES:
        raise SystemExit(f"mode는 {' 또는 '.join(MODES)} 입니다: {mode}")

    # 실험은 절대 발행하지 않는다. 러너가 직접 끄고, 호출자가 켜 놨어도 덮어쓴다.
    os.environ["PREDICT_STOCK_PUBLISH"] = "false"

    storage = Path(storage).resolve()
    if task == "P00":
        config = p00_config(mode)
    elif task == "P03":
        # 비교 대상 목록이 곧 이 작업의 설정이다. 목록이 바뀌면 다른 실험이다.
        config = {"task": "P03", "mode": mode, "groups": sorted(FEATURE_GROUPS)}
    elif task == "P04":
        config = {"task": "P04", "mode": mode, "windows": list(WINDOW_CANDIDATES),
                  "baseline": BASELINE_WINDOW, "min_train_rows": MIN_TRAIN_ROWS}
    elif task == "P05":
        config = {"task": "P05", "mode": mode,
                  "half_lives": ["none" if h is None else h for h in HALF_LIFE_CANDIDATES],
                  "window": BASELINE_WINDOW}
    elif task == "P06":
        config = {"task": "P06", "mode": mode, "retrain_every": list(RETRAIN_CANDIDATES),
                  "baseline": "fold", "window": BASELINE_WINDOW}
    else:
        config = {"mode": mode}
    identity = {
        "task_id": task, "target": target, "mode": mode,
        "data_hash": data_hash(snapshot_paths(storage, target)),
        "config_hash": config_hash(config),
    }
    state, resumed = start_run(storage, task, target, mode, identity, config)
    if resumed and not resume:
        print(f"같은 설정의 기존 실행을 찾았습니다: {state.run_dir.name}")
        print("  완료 단위를 다시 쓰려면 --resume 을 붙이세요. 지금은 이어서 실행합니다.")
    print(f"[{task}] {target} / {mode} → {state.run_dir}")
    print(f"  data_hash={identity['data_hash']} config_hash={identity['config_hash']}"
          f" {'(재개)' if resumed else '(신규)'}")

    try:
        rows = TASK_RUNNERS[task](target, mode, storage, state, run_notebook_fn=run_notebook_fn)
    except Exception as exc:
        state.fail(f"{target}:{task.lower()}", f"{type(exc).__name__}: {exc}")
        raise
    write_metrics(state, rows or [])
    state.finish()
    print(f"  완료 단위: {state.completed()}")
    return state


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", required=True, help=f"작업 ID ({', '.join(sorted(TASKS))})")
    parser.add_argument("--target", required=True, choices=SUPPORTED_TARGETS)
    parser.add_argument("--mode", default="quick", choices=MODES)
    parser.add_argument("--storage", type=Path, required=True, help="실험 결과를 둘 디렉터리")
    parser.add_argument("--resume", action="store_true",
                        help="같은 설정의 기존 실행에서 완료 단위를 재사용한다")
    args = parser.parse_args()
    execute(args.task, args.target, args.mode, args.storage, args.resume)


if __name__ == "__main__":
    main()

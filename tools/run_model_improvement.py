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
    "P07": "소수 모델 앙상블 비교",
    "P08": "확률 신뢰도·예측 보류 평가",
    "P09": "갭·장중 별도 학습 비교",
    "P10": "국내 관련 종목 공동 학습 기반",
    "P10b": "해외 자산 특징을 넣은 pooled 패널 vs 대표 모델",
    "R02c": "누적 야간/장중 특징 그룹 D 를 다음 날 방향 대표 모델에 더했을 때(같은 날짜, 갭·세션 AUC 병기)",
    "P16": "시가 확정 후(09:37) 종가 방향 재예측 — 모델이 아니라 정보 마감 시각을 옮긴다",
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


def replace_with_retry(src, dst, attempts=20, wait=0.05):
    """os.replace. Windows 에서는 방금 닫은 파일을 백신·인덱서가 잠깐 잡고 있어 PermissionError 가 나므로
    짧게 기다렸다 다시 시도한다(테스트에서 실제로 간헐적으로 실패했다)."""
    import time
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(wait * (attempt + 1))


def write_atomic(path, text):
    """같은 디렉터리에 임시 파일로 쓴 뒤 교체한다. 중간에 끊겨도 반쪽 파일이 남지 않는다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        replace_with_retry(tmp, path)
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

    def reset_units(self):
        """완료 표시를 지운다. 완료 표시는 있는데 산출물이 없을 때만 쓴다."""
        payload = read_json(self.checkpoint_path) or {}
        payload["completed_units"], payload["results"] = [], {}
        write_json(self.checkpoint_path, payload)
        self.manifest["completed_units"] = []
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


# ---------------------------------------------------------------------------
# P07 — 소수 모델 앙상블
# ---------------------------------------------------------------------------
# 두 모델 결합 가중치는 이 다섯 점에서만 고른다. 연속 최적화는 외부 구간에 맞춰 조정할 여지를 준다.
ENSEMBLE_WEIGHT_GRID = (0., .25, .5, .75, 1.)
# 결합할 후보. 계획대로 셋을 넘지 않는다: 현행 대표(5년·무가중) / 최근 창(P04) / 최근 가중(P05).
ENSEMBLE_CANDIDATES = ("headline_5y", "expanding", "half_life_504")


def _check_probs(name, probs, n=None):
    probs = np.asarray(probs, dtype=float)
    if probs.ndim != 2 or probs.shape[1] != 3:
        raise ValueError(f"{name}: 확률은 (n, 3) 이어야 합니다: {probs.shape}")
    if n is not None and probs.shape[0] != n:
        raise ValueError(f"{name}: 행 수가 다릅니다 {probs.shape[0]} vs {n}")
    if not np.isfinite(probs).all():
        raise ValueError(f"{name}: 비유한 확률이 있습니다")
    return probs


def combine_probabilities(candidates, weights=None):
    """후보 확률을 결합한다. 클래스 순서 [하락, 보합, 상승]은 그대로, 합은 1로 맞춘다.

    None인 후보는 빠진 것으로 보고 나머지로만 결합한다(fallback). 전부 빠지면 실패한다.
    weights가 없으면 단순 평균. 있으면 이름별 가중을 쓰고 합이 1이 아니어도 정규화한다.
    """
    present = {k: v for k, v in candidates.items() if v is not None}
    if not present:
        raise ValueError("결합할 후보가 하나도 없습니다")
    n = None
    arrays = {}
    for name, probs in present.items():
        arrays[name] = _check_probs(name, probs, n)
        n = arrays[name].shape[0]
    if weights is None:
        weights = {name: 1.0 for name in arrays}
    total = sum(float(weights.get(name, 0.)) for name in arrays)
    if total <= 0:
        raise ValueError("가중치 합이 0입니다")
    out = sum(float(weights.get(name, 0.)) / total * arrays[name] for name in arrays)
    out = np.clip(out, 1e-9, 1.)
    return out / out.sum(axis=1, keepdims=True)


def dedupe_candidates(candidates, atol=1e-9):
    """같은 예측을 내는 후보는 하나만 남긴다. 이름만 다른 중복이 평균을 왜곡하지 않게."""
    kept = {}
    for name, probs in candidates.items():
        if probs is None:
            continue
        probs = np.asarray(probs, dtype=float)
        if any(probs.shape == k.shape and np.allclose(probs, k, atol=atol) for k in kept.values()):
            continue
        kept[name] = probs
    return kept


def select_pair_weight(probs_a, probs_b, y, inner_idx):
    """a에 줄 가중치를 격자에서 고른다. inner_idx 행의 log loss만 본다(외부 라벨 불가).

    내부 행이 20개 미만이면 고르지 않고 단순 평균(0.5)을 돌려준다.
    """
    inner_idx = np.asarray(inner_idx, dtype=int)
    if len(inner_idx) < 20:
        return .5
    a, b, yy = np.asarray(probs_a)[inner_idx], np.asarray(probs_b)[inner_idx], np.asarray(y)[inner_idx]
    best, best_loss = .5, np.inf
    for w in ENSEMBLE_WEIGHT_GRID:
        mixed = np.clip(w * a + (1 - w) * b, 1e-9, 1.)
        mixed /= mixed.sum(axis=1, keepdims=True)
        loss = float(-np.log(mixed[np.arange(len(yy)), yy]).mean())
        if loss < best_loss - 1e-12:
            best, best_loss = w, loss
    return best


def run_p07(target, mode, storage, state, run_notebook_fn=None):
    """세 후보(대표·expanding·504 가중)의 OOF 확률을 만들고 단순 평균·격자 가중 결합을 비교한다.

    결합 가중치는 각 폴드의 학습 구간 끝 6개월(내부)에서만 고른다. 그 구간의 후보 확률은
    그 앞 자료로만 학습해 만든다. 외부 시험 구간 라벨은 어디에도 쓰지 않는다.
    """
    import time

    unit = f"{target}:ensemble"
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

    def candidate_probs(name, train_idx, test_idx):
        if name == "headline_5y":
            return _ensemble_probabilities(ns, features, y, train_idx, test_idx)
        if name == "expanding":
            idx = window_train_indices(dates, dates[test_idx[0]], "expanding")
            return _ensemble_probabilities(ns, features, y, idx, test_idx)
        if name == "half_life_504":
            return _weighted_ensemble(ns, features, y, dates, train_idx, test_idx, 504)
        raise ValueError(name)

    frames, chosen_weights, timing = [], [], {}
    started = time.time()
    for fold in folds:
        tr, te = fold["train_idx"], fold["test_idx"]
        # 외부 예측
        outer = {name: candidate_probs(name, tr, te) for name in ENSEMBLE_CANDIDATES}
        outer = dedupe_candidates(outer)
        for name, probs in outer.items():
            frames.append(ns["prediction_frame"](f"cand {name}", dates[te], y[te], probs, fold["fold"]))
        frames.append(ns["prediction_frame"]("ens mean", dates[te], y[te],
                                             combine_probabilities(outer), fold["fold"]))
        # 내부 구간: 학습 구간 끝 6개월. 후보는 그 앞 자료로만 학습.
        train_dates = pd.DatetimeIndex(dates)[tr]
        split = train_dates[-1] - pd.DateOffset(months=6)
        inner_te, inner_tr = tr[train_dates > split], tr[train_dates <= split]
        pair_weights = {}
        if len(inner_te) >= 20 and len(inner_tr) >= MIN_TRAIN_ROWS:
            inner = {name: candidate_probs(name, inner_tr, inner_te) for name in outer}
            names = list(outer)
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    a, b = names[i], names[j]
                    w = _select_on(inner[a], inner[b], y[inner_te])
                    pair_weights[f"{a}|{b}"] = w
                    frames.append(ns["prediction_frame"](
                        f"ens {a}|{b}", dates[te], y[te],
                        combine_probabilities({a: outer[a], b: outer[b]}, {a: w, b: 1 - w}), fold["fold"]))
        chosen_weights.append({"fold": fold["fold"], "pairs": pair_weights, "candidates": list(outer)})
    timing["seconds"] = round(time.time() - started, 1)

    predictions = pd.concat(frames, ignore_index=True)
    metrics = ns["summarize_predictions"](predictions, with_ci=False)
    rows, comparisons = [], []
    for model, row in metrics.iterrows():
        rows.append({"target": target, "model": model, "target_mode": "close_to_close", "fold": "all",
                     **{k: row.get(k, "") for k in ("n", "accuracy", "balanced_accuracy",
                                                    "log_loss", "brier", "auc_gap", "auc_session")}})
    baseline = "cand headline_5y"
    best_single = min((m for m in metrics.index if m.startswith("cand ")), key=lambda m: metrics.loc[m, "log_loss"])
    for model in metrics.index:
        if not model.startswith("ens "):
            continue
        for ref, label in ((baseline, "현행 대표"), (best_single, "최선 단일")):
            for metric in ("balanced_accuracy", "log_loss"):
                d = ns["paired_delta_ci"](predictions, model, ref, metric)
                comparisons.append({"target": target, "comparison": f"{model} − {ref} ({label})",
                                    "metric": metric, "delta": d["delta"], "ci_low": d["lo"],
                                    "ci_high": d["hi"], "common_n": d["n"],
                                    "verdict": verdict_for(metric, d["lo"], d["hi"])})
    write_json(state.run_dir / f"ensemble_{target}.json",
               {"timing": timing, "weights_by_fold": chosen_weights, "best_single": best_single,
                "grid": list(ENSEMBLE_WEIGHT_GRID), "comparisons": comparisons})
    write_metrics_named(state, comparisons, "comparisons.csv")
    state.mark(unit, {"candidates": list(ENSEMBLE_CANDIDATES), "best_single": best_single,
                      "evaluation_days": int(metrics["n"].max())})
    return rows


def _select_on(probs_a, probs_b, y_inner):
    """내부 구간 배열이 이미 잘려 있을 때의 격자 선택."""
    return select_pair_weight(probs_a, probs_b, y_inner, np.arange(len(y_inner)))


# ---------------------------------------------------------------------------
# P08 — 확률 보정과 예측 보류
# ---------------------------------------------------------------------------
ABSTAIN_THRESHOLDS = (None, .5, .6, .7)


def abstention_table(probs, y):
    """최대 확률이 임계치 이상인 날만 고른 정확도와 coverage. 0건이면 정확도는 NaN."""
    probs, y = np.asarray(probs, dtype=float), np.asarray(y, dtype=int)
    conf, pred = probs.max(axis=1), probs.argmax(axis=1)
    overall = float(np.mean(pred == y)) if len(y) else float("nan")
    rows = []
    for threshold in ABSTAIN_THRESHOLDS:
        mask = np.ones(len(y), dtype=bool) if threshold is None else conf >= threshold
        selected = int(mask.sum())
        rows.append({"threshold": "none" if threshold is None else str(threshold),
                     "selected": selected, "total": int(len(y)),
                     "coverage": selected / len(y) if len(y) else float("nan"),
                     "selected_accuracy": float(np.mean(pred[mask] == y[mask])) if selected else float("nan"),
                     "overall_accuracy": overall})
    return rows


def reliability_bins(probs, y, n_bins=5):
    """최대 확률을 구간으로 나눠 구간별 평균 확률과 실제 적중률을 낸다."""
    probs, y = np.asarray(probs, dtype=float), np.asarray(y, dtype=int)
    conf, hit = probs.max(axis=1), (probs.argmax(axis=1) == y)
    edges = np.linspace(1 / 3, 1.0, n_bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (conf >= lo) & (conf < hi if hi < 1.0 else conf <= hi)
        out.append({"bin": f"[{lo:.2f}, {hi:.2f})", "count": int(mask.sum()),
                    "mean_confidence": float(conf[mask].mean()) if mask.any() else float("nan"),
                    "hit_rate": float(hit[mask].mean()) if mask.any() else float("nan")})
    return out


def run_p08(target, mode, storage, state, run_notebook_fn=None):
    """대표 모델의 외부 OOF 확률로 온도 보정 전후·신뢰도 구간·보류 임계치를 진단한다.

    새 보정을 만들지 않는다. 노트북의 fit_direction_model이 이미 고른 온도(temperature)를
    '전'(온도 1)과 '후'로 나눠 같은 날짜에서 비교한다. 임계치 선택은 각 폴드 학습 구간 끝
    6개월(내부)에서 하고, 외부 구간에서 다시 고르지 않는다.
    """
    unit = f"{target}:calibration"
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

    frames, inner_choice, temps = [], [], []
    for fold in folds:
        tr, te = fold["train_idx"], fold["test_idx"]
        raw, cal = [], []
        for family in ("Logistic", "LightGBM"):
            fitted = ns["fit_direction_model"](features, y, tr, family, seed=seed, selection=selection)
            base = ns["aligned_probabilities"](fitted["estimator"], features[te])
            raw.append(base)
            cal.append(ns["temperature_probabilities"](base, fitted["temperature"]))
            temps.append({"fold": fold["fold"], "family": family, "temperature": fitted["temperature"]})
        raw_p, cal_p = np.mean(raw, axis=0), np.mean(cal, axis=0)
        frames.append(ns["prediction_frame"]("calib before", dates[te], y[te], raw_p, fold["fold"]))
        frames.append(ns["prediction_frame"]("calib after", dates[te], y[te], cal_p, fold["fold"]))
        # 임계치 선택: 내부 구간(학습 끝 6개월)에서 정확도가 가장 높은 임계치.
        train_dates = pd.DatetimeIndex(dates)[tr]
        split = train_dates[-1] - pd.DateOffset(months=6)
        inner_te, inner_tr = tr[train_dates > split], tr[train_dates <= split]
        chosen = "none"
        if len(inner_te) >= 20 and len(inner_tr) >= MIN_TRAIN_ROWS:
            inner_p = _ensemble_probabilities(ns, features, y, inner_tr, inner_te)
            table = abstention_table(inner_p, y[inner_te])
            valid = [r for r in table if r["selected"] >= 10]
            chosen = max(valid, key=lambda r: r["selected_accuracy"])["threshold"] if valid else "none"
        inner_choice.append({"fold": fold["fold"], "chosen_threshold": chosen})

    predictions = pd.concat(frames, ignore_index=True)
    metrics = ns["summarize_predictions"](predictions, with_ci=False)
    after = predictions[predictions["model"] == "calib after"].sort_values("date")
    probs_after = after[["p_down", "p_flat", "p_up"]].to_numpy()
    y_after = after["y_true"].to_numpy()

    rows = []
    for model, row in metrics.iterrows():
        rows.append({"target": target, "model": model, "target_mode": "close_to_close", "fold": "all",
                     **{k: row.get(k, "") for k in ("n", "accuracy", "balanced_accuracy",
                                                    "log_loss", "brier", "auc_gap", "auc_session")}})
    comparisons = []
    for metric in ("balanced_accuracy", "log_loss", "accuracy"):
        d = ns["paired_delta_ci"](predictions, "calib after", "calib before", metric)
        comparisons.append({"target": target, "comparison": "온도 보정 후 − 전", "metric": metric,
                            "delta": d["delta"], "ci_low": d["lo"], "ci_high": d["hi"], "common_n": d["n"],
                            "verdict": verdict_for(metric, d["lo"], d["hi"])})
    argmax_same = bool((probs_after.argmax(axis=1) ==
                        predictions[predictions["model"] == "calib before"].sort_values("date")
                        [["p_down", "p_flat", "p_up"]].to_numpy().argmax(axis=1)).all())
    write_json(state.run_dir / f"calibration_{target}.json", {
        "temperatures": temps, "argmax_unchanged_by_temperature": argmax_same,
        "reliability_after": reliability_bins(probs_after, y_after),
        "reliability_before": reliability_bins(
            predictions[predictions["model"] == "calib before"].sort_values("date")[["p_down", "p_flat", "p_up"]].to_numpy(),
            y_after),
        "abstention_outer_after": abstention_table(probs_after, y_after),
        "inner_threshold_choice_by_fold": inner_choice,
        "comparisons": comparisons,
        "note": "보류 임계치는 내부 구간에서 골랐다. 외부 표는 모든 임계치를 나란히 보여 줄 뿐 선택 근거가 아니다.",
    })
    write_metrics_named(state, comparisons, "comparisons.csv")
    state.mark(unit, {"argmax_unchanged": argmax_same, "evaluation_days": int(metrics["n"].max())})
    return rows


# ---------------------------------------------------------------------------
# P09 — 갭과 장중의 별도 학습
# ---------------------------------------------------------------------------
TARGET_LEGS = ("close_to_close", "gap", "session")


def decompose_returns(bars):
    """종가→종가를 갭(전일 종가→시가)과 세션(시가→종가)으로 나눈다.

    같은 봉의 open·close만 쓰므로 분할·배당 조정이 두 값에 같은 비율로 걸리면 그대로 성립한다.
    항등식 (1+gap)(1+session)-1 = close_to_close 를 테스트로 고정한다.
    """
    close, open_ = bars["close"].astype(float), bars["open"].astype(float)
    prev_close = close.shift(1)
    return pd.DataFrame({
        "close_to_close": close / prev_close - 1,
        "gap": open_ / prev_close - 1,
        "session": close / open_ - 1,
    }, index=bars.index)


def leg_labels(returns, band):
    """하락 0 / 보합 1 / 상승 2. 수익률이 NaN이면 NaN."""
    r, b = np.asarray(returns, dtype=float), np.asarray(band, dtype=float)
    out = np.where(r < -b, 0., np.where(r > b, 2., 1.))
    out[~np.isfinite(r) | ~np.isfinite(b)] = np.nan
    return out


def session_pnl_bp(session_returns, predictions, cost_bp=20.):
    """장중 방향 예측대로 시가 진입·종가 청산했을 때의 일평균 손익(bp). 보합은 미거래.

    상승 예측은 롱, 하락 예측은 숏. 비용은 거래일마다 왕복 cost_bp를 뺀다.
    거래일이 없으면 NaN.
    """
    session, pred = np.asarray(session_returns, dtype=float), np.asarray(predictions, dtype=int)
    side = np.where(pred == 2, 1., np.where(pred == 0, -1., 0.))
    traded = side != 0
    if not traded.any():
        return float("nan"), float("nan")
    gross = float((side[traded] * session[traded]).mean() * 1e4)
    return gross, gross - float(cost_bp)


def run_p09(target, mode, storage, state, run_notebook_fn=None):
    """세 타깃(종가→종가·갭·세션)을 각각 학습해 나란히 비교한다.

    특징은 P03에서 고정한 시세만 입력(전일까지의 정보)으로 셋 다 동일하다. 세션 타깃의
    정답에는 그날 시가·종가가 들어가지만 특징에는 시가가 없다 — 07:00 예측 시점의 정보다.
    갭·세션 확률을 합쳐 종가→종가 확률을 만들지 않는다.
    """
    unit = f"{target}:legs"
    if state.is_done(unit):
        print(f"  이미 완료된 단위 건너뜀: {unit} (metrics.csv 유지)")
        return None
    if run_notebook_fn is None:
        sys.path.insert(0, str(ROOT / "tools"))
        from run_notebook import run_notebook as run_notebook_fn

    snapshot = snapshot_paths(storage, target)
    ns = run_notebook_fn(Path(storage) / target, targets=target,
                         quick=(mode == "quick"), use_cache=bool(snapshot))[target]
    features, dates, folds = ns["market_X"], pd.DatetimeIndex(ns["dates"]), ns["folds"]
    cost_bp = float(ns.get("COST_BP", 20.))
    vol_mult = float(ns.get("VOL_BAND_MULT", .3))

    bars = ns["raw"]["target"].copy()
    bars.index = pd.DatetimeIndex(bars.index).tz_localize(None).normalize()
    legs = decompose_returns(bars).reindex(dates)
    # 시가가 없는 봉(유령봉 제거 등)은 갭·세션 라벨을 만들 수 없다. 그 날짜는 두 타깃에서 빠진다.
    labels = {}
    for leg in TARGET_LEGS:
        band = legs[leg].rolling(20).std().shift(1) * vol_mult
        labels[leg] = leg_labels(legs[leg], band)
    # 원래 종가→종가 라벨은 노트북 y와 같은 밴드 규칙이지만, 정합성 확인을 위해 노트북 y를 쓴다.
    labels["close_to_close"] = ns["y"].astype(float)

    frames, coverage, timings = [], {}, {}
    import time
    for leg in TARGET_LEGS:
        started = time.time()
        y_leg = labels[leg]
        valid = np.isfinite(y_leg)
        y_int = np.where(valid, y_leg, 1).astype(int)
        kept = 0
        for fold in folds:
            tr = fold["train_idx"][valid[fold["train_idx"]]]
            te = fold["test_idx"][valid[fold["test_idx"]]]
            if len(tr) < MIN_TRAIN_ROWS or len(te) < 20 or len(np.unique(y_int[tr])) < 3:
                continue
            probs = _ensemble_probabilities(ns, features, y_int, tr, te)
            frames.append(ns["prediction_frame"](f"leg {leg}", dates[te], y_int[te], probs, fold["fold"],
                                                 extra={"session_ret": legs["session"].to_numpy()[te]}))
            kept += len(te)
        coverage[leg] = {"days": kept, "missing_labels": int((~valid).sum())}
        timings[leg] = round(time.time() - started, 1)
        print(f"  {leg}: 평가 {kept}일 · 라벨 결측 {coverage[leg]['missing_labels']} · {timings[leg]}초")

    predictions = pd.concat(frames, ignore_index=True)
    metrics = ns["summarize_predictions"](predictions, with_ci=False)
    rows = []
    for model, row in metrics.iterrows():
        leg = model.replace("leg ", "")
        rec = {"target": target, "model": model, "leg": leg, "target_mode": leg, "fold": "all",
               **{k: row.get(k, "") for k in ("n", "accuracy", "balanced_accuracy",
                                              "log_loss", "brier", "auc_gap", "auc_session")}}
        sub = predictions[predictions["model"] == model]
        if leg == "session":
            gross, net = session_pnl_bp(sub["session_ret"].to_numpy(), sub["y_pred"].to_numpy(), cost_bp)
            rec["session_bp_gross"], rec["session_bp_net"] = gross, net
        rows.append(rec)
    # 각 타깃을 그 타깃의 '항상 보합'과 비교(타깃마다 사전확률이 다르므로 서로 비교하지 않는다)
    comparisons = []
    for leg in TARGET_LEGS:
        model = f"leg {leg}"
        if model not in metrics.index:
            continue
        sub = predictions[predictions["model"] == model]
        prior_frames = []
        for fold_id, g in sub.groupby("fold"):
            counts = np.bincount(g["y_true"].to_numpy(), minlength=3).astype(float) + 1
            prior = counts / counts.sum()
            prior_frames.append(ns["prediction_frame"](f"prior {leg}", g["date"], g["y_true"],
                                                       np.tile(prior, (len(g), 1)), fold_id,
                                                       y_pred=np.ones(len(g), dtype=int)))
        both = pd.concat([sub, pd.concat(prior_frames, ignore_index=True)], ignore_index=True)
        for metric in ("balanced_accuracy", "log_loss"):
            d = ns["paired_delta_ci"](both, model, f"prior {leg}", metric)
            comparisons.append({"target": target, "comparison": f"{leg} 모델 − 사전확률(보합)", "metric": metric,
                                "delta": d["delta"], "ci_low": d["lo"], "ci_high": d["hi"],
                                "common_n": d["n"], "verdict": verdict_for(metric, d["lo"], d["hi"])})
    write_json(state.run_dir / f"legs_{target}.json",
               {"coverage": coverage, "timings": timings, "cost_bp": cost_bp, "comparisons": comparisons,
                "note": "특징은 셋 다 전일까지의 시세만. 세션 정답에만 당일 시가·종가 사용. 갭·세션 확률을 합쳐 종가→종가를 만들지 않는다."})
    write_metrics_named(state, comparisons, "comparisons.csv")
    state.mark(unit, {"legs": list(TARGET_LEGS), "coverage": coverage})
    return rows


# ---------------------------------------------------------------------------
# P10 — 공동 학습용 국내 종목 패널 + pooled 기준선
# ---------------------------------------------------------------------------
def _load_panel_bars(cache_dir, tickers, start="2015-01-01"):
    """yfinance로 패널 종목 봉을 받고 CSV로 캐시한다. 결측은 그대로 둔다(보간 없음)."""
    import yfinance as yf
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out, first = {}, {}
    for ticker in tickers:
        path = cache_dir / f"{ticker.replace('.', '_')}.csv"
        frame = None
        if path.is_file():
            frame = pd.read_csv(path, index_col=0, parse_dates=True)
        else:
            try:
                h = yf.Ticker(ticker).history(start="2010-01-01", auto_adjust=True)
                if not h.empty:
                    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
                    h.columns = [str(c).strip().lower() for c in h.columns]
                    frame = h[["open", "close", "volume"]].dropna(subset=["close"])
                    frame = frame[frame["volume"] > 0]          # 거래 없는 유령봉 제거
                    frame.to_csv(path)
            except Exception as exc:
                print(f"  ⚠️ {ticker}: {type(exc).__name__}")
        if frame is not None and len(frame):
            first[ticker] = frame.index[0]
            out[ticker] = frame[frame.index >= pd.Timestamp(start)]
    return out, first


def run_p10(target, mode, storage, state, run_notebook_fn=None):
    """패널을 만들고 pooled LightGBM을 학습해 대상 종목 단독 학습과 같은 날짜에서 비교한다.

    pooled 모델의 입력은 종목 간 비교 가능한 값(수익률·변동성·거래대금 비율)과 종목 식별값뿐이다.
    대상 종목 단독 모델도 **같은 입력·같은 폴드**로 학습해 공정하게 비교한다(노트북 대표 모델과는
    입력이 달라 직접 비교하지 않고 참고로만 적는다).
    """
    import time
    sys.path.insert(0, str(ROOT / "experiments" / "model_improvement"))
    import panel_data as pdm
    from sklearn.dummy import DummyClassifier

    unit = f"{target}:pooled"
    if state.is_done(unit):
        print(f"  이미 완료된 단위 건너뜀: {unit} (metrics.csv 유지)")
        return None
    if run_notebook_fn is None:
        sys.path.insert(0, str(ROOT / "tools"))
        from run_notebook import run_notebook as run_notebook_fn

    # 대표 모델의 폴드 경계·평가 날짜를 그대로 쓴다.
    snapshot = snapshot_paths(storage, target)
    ns = run_notebook_fn(Path(storage) / target, targets=target,
                         quick=(mode == "quick"), use_cache=bool(snapshot))[target]
    folds, dates = ns["folds"], pd.DatetimeIndex(ns["dates"])
    target_ticker = ns["TARGET_SPEC"]["ticker"]

    bars, first = _load_panel_bars(Path(storage) / "panel_cache", [t for t, _, _ in pdm.PANEL_UNIVERSE])
    kept, excluded = pdm.eligible_universe(first)
    for reason in excluded.values():
        print(f"  제외: {reason}")
    bars = {t: bars[t] for t, _, _ in kept if t in bars}
    panel = pdm.build_panel(bars)
    fabricated = pdm.check_no_interpolation(panel, bars)
    feature_cols = [c for c in panel.columns if c not in ("date", "instrument", "target_ret", "band", "y")]
    instruments = sorted(bars)
    panel["inst_id"] = panel["instrument"].map({t: i for i, t in enumerate(instruments)}).astype(float)
    cols = feature_cols + ["inst_id"]
    valid = panel[cols + ["y"]].notna().all(axis=1)
    panel = panel[valid].reset_index(drop=True)
    print(f"  패널: 종목 {len(instruments)} · 행 {len(panel):,} · 특징 {len(cols)} · 보간 위반 {fabricated}")

    X = panel[cols].to_numpy(dtype=np.float32)
    y = panel["y"].to_numpy(dtype=int)
    pdates = pd.DatetimeIndex(panel["date"])
    is_target = (panel["instrument"] == target_ticker).to_numpy()

    def lgbm():
        from lightgbm import LGBMClassifier
        return LGBMClassifier(objective="multiclass", num_class=3, n_estimators=120, learning_rate=0.03,
                              num_leaves=15, min_child_samples=100, colsample_bytree=0.85,
                              reg_alpha=0.5, reg_lambda=2.0, class_weight="balanced",
                              random_state=ns.get("SEED", 42), n_jobs=-1, verbosity=-1)

    frames, timing = [], {"pooled": 0., "single": 0.}
    for fold in folds:
        t0, t1 = dates[fold["test_idx"][0]], dates[fold["test_idx"][-1]]
        train_start = t0 - pd.DateOffset(years=5)
        tr_all = np.flatnonzero((pdates >= train_start) & (pdates < t0))
        te = np.flatnonzero((pdates >= t0) & (pdates <= t1) & is_target)
        tr_single = tr_all[is_target[tr_all]]
        if len(te) < 20 or len(tr_single) < MIN_TRAIN_ROWS:
            continue
        started = time.time()
        pooled = lgbm().fit(X[tr_all], y[tr_all]) if len(np.unique(y[tr_all])) > 1 else DummyClassifier(strategy="prior").fit(X[tr_all], y[tr_all])
        p_pool = ns["aligned_probabilities"](pooled, X[te])
        timing["pooled"] += time.time() - started
        started = time.time()
        single = lgbm().fit(X[tr_single], y[tr_single]) if len(np.unique(y[tr_single])) > 1 else DummyClassifier(strategy="prior").fit(X[tr_single], y[tr_single])
        p_single = ns["aligned_probabilities"](single, X[te])
        timing["single"] += time.time() - started
        prior = np.bincount(y[tr_single], minlength=3).astype(float) + 1
        frames.append(ns["prediction_frame"]("panel pooled", pdates[te], y[te], p_pool, fold["fold"]))
        frames.append(ns["prediction_frame"]("panel single", pdates[te], y[te], p_single, fold["fold"]))
        frames.append(ns["prediction_frame"]("panel prior", pdates[te], y[te], np.tile(prior / prior.sum(), (len(te), 1)),
                                             fold["fold"], y_pred=np.ones(len(te), dtype=int)))
    predictions = pd.concat(frames, ignore_index=True)
    metrics = ns["summarize_predictions"](predictions, with_ci=False)
    rows = [{"target": target, "model": m, "target_mode": "close_to_close", "fold": "all",
             **{k: r.get(k, "") for k in ("n", "accuracy", "balanced_accuracy", "log_loss", "brier", "auc_gap", "auc_session")},
             "seconds": round(timing["pooled"] if "pooled" in m else timing["single"] if "single" in m else 0., 1)}
            for m, r in metrics.iterrows()]
    comparisons = []
    for a, b, label in (("panel pooled", "panel single", "pooled − 단독(같은 입력)"),
                        ("panel pooled", "panel prior", "pooled − 사전확률"),
                        ("panel single", "panel prior", "단독 − 사전확률")):
        for metric in ("balanced_accuracy", "log_loss"):
            d = ns["paired_delta_ci"](predictions, a, b, metric)
            comparisons.append({"target": target, "comparison": label, "metric": metric, "delta": d["delta"],
                                "ci_low": d["lo"], "ci_high": d["hi"], "common_n": d["n"],
                                "verdict": verdict_for(metric, d["lo"], d["hi"])})
    write_json(state.run_dir / f"panel_{target}.json", {
        "selection_date": pdm.SELECTION_DATE, "selection_rule": pdm.SELECTION_RULE,
        "instruments": instruments, "excluded": excluded, "rows": int(len(panel)),
        "features": cols, "fabricated_rows": fabricated, "timing_seconds": timing,
        "survivorship_note": "목록은 2026-09-10 상장 종목이므로 과거로 적용하면 생존 편향이 있다. 종목 수 증가는 독립 날짜 표본 증가가 아니다.",
        "comparisons": comparisons})
    write_metrics_named(state, comparisons, "comparisons.csv")
    state.mark(unit, {"instruments": len(instruments), "panel_rows": int(len(panel)),
                      "evaluation_days": int(metrics["n"].max())})
    return rows


# ---------------------------------------------------------------------------
# P10b — 해외 자산(종목 공통) 특징을 넣은 pooled 패널 vs 대표 모델
# ---------------------------------------------------------------------------
PANEL_FAMILIES = ("Logistic", "LightGBM")
PANEL_INNER_BLOCK_DATES = 126     # 노트북 fit_direction_model의 내부 검증 크기(126행 = 126일)를 날짜로 옮긴 것


def panel_foreign_module():
    sys.path.insert(0, str(ROOT / "experiments" / "model_improvement"))
    import panel_foreign
    return panel_foreign


def issue_min_prob():
    """보고서가 종가 방향을 내는 최소 확률. forecast_utils 값을 그대로 쓴다(없으면 0.5)."""
    sys.path.insert(0, str(ROOT))
    try:
        from forecast_utils import DIRECTION_ISSUE_MIN_PROB
        return float(DIRECTION_ISSUE_MIN_PROB)
    except Exception:
        return 0.5


def fit_panel_family(ns, X, y, train_idx, row_dates, family, seed=42, selection="log_loss",
                     block_dates=PANEL_INNER_BLOCK_DATES):
    """노트북 fit_direction_model과 같은 후보·온도·선택 규칙. 내부 분할만 날짜 블록으로 한다.

    패널은 같은 날짜에 여러 종목 행이 있으므로 행 수 기준 분할은 한 날짜를 반으로 가른다.
    날짜 블록(126거래일)으로 나눠 같은 날짜의 행이 같은 쪽에 가게 한다. 단독(대상 종목만)
    학습에서는 행 = 날짜이므로 노트북과 같은 분할이 된다.
    """
    sys.path.insert(0, str(ROOT / "experiments" / "model_improvement"))
    import panel_foreign as pf
    from sklearn.dummy import DummyClassifier

    train_idx = np.asarray(train_idx, dtype=int)
    xt, yt = X[train_idx], y[train_idx]
    if family == "Logistic":
        candidates = [{"C": c, "class_weight": w} for c in (.003, .01, .03) for w in (None, "balanced")]
    elif family == "LightGBM":
        candidates = [{"n_estimators": n, "class_weight": w} for n in (60, 120) for w in (None, "balanced")]
    else:
        raise ValueError(family)
    splits = pf.date_block_splits(np.asarray(row_dates)[train_idx], n_splits=3, block_dates=block_dates)
    if not splits:
        raise ValueError("내부 검증 블록을 만들 수 없습니다(학습 날짜가 너무 적음)")
    labels = np.concatenate([yt[va] for _, va in splits])
    trials = []
    for params in candidates:
        parts = []
        for tr, va in splits:
            est = (ns["direction_estimator"](family, params, seed) if len(np.unique(yt[tr])) > 1
                   else DummyClassifier(strategy="prior"))
            est.fit(xt[tr], yt[tr])
            parts.append(ns["aligned_probabilities"](est, xt[va]))
        probs = np.vstack(parts)
        trials.append((float(np.mean(probs.argmax(axis=1) == labels)), ns["probability_loss"](labels, probs),
                       params, probs))
    best = (min(trials, key=lambda t: (t[1], -t[0])) if selection == "log_loss"
            else min(trials, key=lambda t: (-t[0], t[1])))
    temperature = min((1., .75, 1.5, 2.),
                      key=lambda t: ns["probability_loss"](labels, ns["temperature_probabilities"](best[3], t)))
    est = (ns["direction_estimator"](family, best[2], seed) if len(np.unique(yt)) > 1
           else DummyClassifier(strategy="prior"))
    est.fit(xt, yt)
    return {"estimator": est, "temperature": temperature,
            "selection": {"family": family, "params": best[2], "temperature": temperature,
                          "inner_accuracy": best[0], "inner_log_loss": best[1],
                          "inner_rows": int(len(labels)), "training_rows": int(len(train_idx))}}


def panel_ensemble(ns, X, y, train_idx, test_idx, row_dates, seed=42, selection="log_loss"):
    """Logistic·LightGBM을 각각 적합해 확률을 평균한다(대표 모델과 같은 결합). 선택 기록도 돌려준다."""
    probs, chosen = [], []
    for family in PANEL_FAMILIES:
        fitted = fit_panel_family(ns, X, y, train_idx, row_dates, family, seed=seed, selection=selection)
        probs.append(ns["temperature_probabilities"](ns["aligned_probabilities"](fitted["estimator"], X[test_idx]),
                                                    fitted["temperature"]))
        chosen.append(fitted["selection"])
    return np.mean(probs, axis=0), chosen


def selective_rows(target, model, probs, y):
    """P08 abstention_table을 계약 열로 옮긴다. 임계치 0.5 행이 보고서의 발행 규칙이다."""
    return [{"target": target, "model": model, **row} for row in abstention_table(probs, y)]


def run_p10b(target, mode, storage, state, run_notebook_fn=None):
    """P10 패널 + 대표 모델의 종목 공통 특징(해외 자산·KOSPI·달력)으로 pooled를 학습해
    **같은 평가 날짜·같은 정답**에서 대표 모델(No macro ensemble)과 쌍체 비교한다.

    모델 후보는 셋뿐이다: pooled+해외(9종목), 단독+해외(대상 종목만, 같은 입력), 대표 모델(P00 OOF 확률
    재사용). 대표 모델은 다시 학습하지 않는다 — 노트북이 같은 폴드에서 낸 확률을 그대로 쓴다.
    보류 진단(최대 확률 ≥ 발행 기준)은 세 모델 모두 같은 날짜에서 낸다.
    """
    import time
    sys.path.insert(0, str(ROOT / "experiments" / "model_improvement"))
    import panel_data as pdm
    import panel_foreign as pf

    unit = f"{target}:pooled_foreign"
    if state.is_done(unit):
        print(f"  이미 완료된 단위 건너뜀: {unit} (metrics.csv 유지)")
        return None
    if run_notebook_fn is None:
        sys.path.insert(0, str(ROOT / "tools"))
        from run_notebook import run_notebook as run_notebook_fn

    snapshot = snapshot_paths(storage, target)
    ns = run_notebook_fn(Path(storage) / target, targets=target,
                         quick=(mode == "quick"), use_cache=bool(snapshot))[target]
    folds, dates = ns["folds"], pd.DatetimeIndex(ns["dates"])
    seed, selection = ns.get("SEED", 42), ns.get("SELECTION_METRIC", "log_loss")
    headline = ns.get("HEADLINE_MODEL", "No macro ensemble")
    nb_predictions = ns["predictions"]
    if headline not in set(nb_predictions["model"]):
        raise RuntimeError(f"노트북 예측 프레임에 대표 모델이 없습니다: {headline}")
    official_y = pd.Series(np.asarray(ns["y"], dtype=int), index=dates)
    min_prob = issue_min_prob()

    # 종목 공통 특징: 대표 모델 입력(시세만)에서 종목 고유 열을 뺀 것. 노트북 특징 프레임에서 한 번 만든다.
    common_cols = pf.common_feature_columns(list(ns["feature_cols"]), list(ns["market_feature_idx"]))
    common = ns["feat"][common_cols].copy()
    common.index = pd.DatetimeIndex(common.index).tz_localize(None).normalize()

    bars, first = _load_panel_bars(Path(storage) / "panel_cache", [t for t, _, _ in pdm.PANEL_UNIVERSE])
    kept, excluded = pdm.eligible_universe(first)
    bars = {t: bars[t] for t, _, _ in kept if t in bars}
    panel = pdm.build_panel(bars)
    fabricated = pdm.check_no_interpolation(panel, bars)
    panel = panel.sort_values(["date", "instrument"]).reset_index(drop=True)
    panel["label_date"] = pf.label_dates(panel)
    panel = pf.attach_common_features(panel, common)
    panel, leaky_rows = pf.drop_rows_with_features_after_label(panel)
    instruments = sorted(bars)
    panel_cols = [c for c in pdm.instrument_features(bars[instruments[0]]["close"], bars[instruments[0]].get("volume")).columns]
    dummies = pf.instrument_dummies(panel, instruments)
    panel = pd.concat([panel, dummies], axis=1)
    cols = panel_cols + list(dummies.columns) + common_cols
    rows_before = len(panel)
    valid = panel[cols + ["y"]].notna().all(axis=1) & panel["pred_date"].notna() & panel["label_date"].notna()
    panel = panel[valid].reset_index(drop=True)
    target_ticker = ns["TARGET_SPEC"]["ticker"]
    is_target = (panel["instrument"] == target_ticker).to_numpy()
    print(f"  패널: 종목 {len(instruments)} · 행 {len(panel):,}(결측 제거 전 {rows_before:,}, 누수 후보 제거 {leaky_rows})"
          f" · 특징 {len(cols)}(패널 {len(panel_cols)} + 종목 {len(dummies.columns)} + 공통 {len(common_cols)}) · 보간 위반 {fabricated}")

    X = panel[cols].to_numpy(dtype=np.float32)
    y = panel["y"].to_numpy(dtype=int)
    pred_date = pd.DatetimeIndex(panel["pred_date"])
    label_date = pd.DatetimeIndex(panel["label_date"])
    row_dates = panel["date"].to_numpy()
    # 대상 종목 행의 패널 라벨과 노트북 공식 라벨의 일치율(같은 규칙·조정 종가라 거의 같아야 한다).
    official_for_rows = official_y.reindex(pred_date).to_numpy()
    have_official = np.isfinite(official_for_rows.astype(float)) & is_target
    label_agreement = float(np.mean(official_for_rows[have_official].astype(int) == y[have_official])) if have_official.any() else float("nan")

    frames, timing, choices = [], {"pooled": 0., "single": 0.}, []
    for fold in folds:
        t0, t1 = dates[fold["test_idx"][0]], dates[fold["test_idx"][-1]]
        tr_all, te = pf.fold_rows(pred_date, label_date, is_target, t0, t1, window_years=5)
        tr_single = tr_all[is_target[tr_all]]
        if len(te) < 20 or len(tr_single) < MIN_TRAIN_ROWS or len(np.unique(y[tr_all])) < 3:
            continue
        # 정답은 노트북 공식 라벨(대표 모델과 같은 y_true). 없는 날은 뒤의 공통 날짜 교집합에서 빠진다.
        y_te = official_y.reindex(pred_date[te]).to_numpy()
        keep = np.isfinite(y_te.astype(float))
        te, y_te = te[keep], y_te[keep].astype(int)
        started = time.time()
        p_pool, chosen_pool = panel_ensemble(ns, X, y, tr_all, te, row_dates, seed=seed, selection=selection)
        timing["pooled"] += time.time() - started
        started = time.time()
        p_single, chosen_single = panel_ensemble(ns, X, y, tr_single, te, row_dates, seed=seed, selection=selection)
        timing["single"] += time.time() - started
        choices.append({"fold": fold["fold"], "train_rows_pooled": int(len(tr_all)), "train_rows_single": int(len(tr_single)),
                        "test_rows": int(len(te)), "pooled": chosen_pool, "single": chosen_single})
        frames.append(ns["prediction_frame"]("panel pooled + foreign", pred_date[te], y_te, p_pool, fold["fold"]))
        frames.append(ns["prediction_frame"]("panel single + foreign", pred_date[te], y_te, p_single, fold["fold"]))
        print(f"  폴드 {fold['fold']}: 학습 pooled {len(tr_all):,} / 단독 {len(tr_single):,} · 시험 {len(te)}")

    candidates = pd.concat(frames, ignore_index=True)
    reference = nb_predictions[nb_predictions["model"].isin([headline, "Always flat"])].copy()
    reference["date"] = pd.DatetimeIndex(reference["date"]).tz_localize(None).normalize()
    predictions = pd.concat([candidates, reference], ignore_index=True)
    # 모든 모델을 같은 날짜 집합으로 자른다 — 지표표의 숫자도 서로 비교 가능해야 한다.
    common_dates = None
    for model_name, g in predictions.groupby("model"):
        common_dates = set(g["date"]) if common_dates is None else common_dates & set(g["date"])
    predictions = predictions[predictions["date"].isin(common_dates)].reset_index(drop=True)
    metrics = ns["summarize_predictions"](predictions, with_ci=False)

    rows = []
    for model_name, r in metrics.iterrows():
        seconds = timing["pooled"] if "pooled" in model_name else timing["single"] if "single" in model_name else 0.
        rows.append({"target": target, "model": model_name, "target_mode": "close_to_close", "fold": "all",
                     **{k: r.get(k, "") for k in ("n", "accuracy", "balanced_accuracy", "log_loss", "brier", "auc_gap", "auc_session")},
                     "seconds": round(seconds, 1)})
    comparisons = []
    for a, b, label in (("panel pooled + foreign", headline, f"pooled+해외 − 대표({headline})"),
                        ("panel single + foreign", headline, f"단독+해외 − 대표({headline})"),
                        ("panel pooled + foreign", "panel single + foreign", "pooled+해외 − 단독+해외(같은 입력)"),
                        ("panel pooled + foreign", "Always flat", "pooled+해외 − 사전확률")):
        for metric in ("balanced_accuracy", "log_loss", "accuracy"):
            d = ns["paired_delta_ci"](predictions, a, b, metric)
            comparisons.append({"target": target, "comparison": label, "metric": metric, "delta": d["delta"],
                                "ci_low": d["lo"], "ci_high": d["hi"], "common_n": d["n"],
                                "verdict": verdict_for(metric, d["lo"], d["hi"])})
    selective = []
    for model_name in ("panel pooled + foreign", "panel single + foreign", headline):
        g = predictions[predictions["model"] == model_name].sort_values("date")
        selective += selective_rows(target, model_name, g[["p_down", "p_flat", "p_up"]].to_numpy(), g["y_true"].to_numpy())

    write_json(state.run_dir / f"panel_foreign_{target}.json", {
        "selection_date": pdm.SELECTION_DATE, "instruments": instruments, "excluded": excluded,
        "rows": int(len(panel)), "rows_before_dropna": rows_before, "leaky_rows_dropped": leaky_rows,
        "fabricated_rows": fabricated, "features": cols, "common_features": common_cols,
        "panel_features": panel_cols, "headline_model": headline, "issue_min_prob": min_prob,
        "common_evaluation_days": int(len(common_dates)), "label_agreement_target_rows": label_agreement,
        "timing_seconds": timing, "fold_choices": choices, "comparisons": comparisons, "selective": selective,
        "survivorship_note": "목록은 2026-09-10 상장 종목이므로 과거로 적용하면 생존 편향이 있다. 종목 수 증가는 독립 날짜 표본 증가가 아니다.",
        "note": "대표 모델은 재학습하지 않고 노트북 OOF 확률을 재사용. 세 모델 모두 같은 날짜·같은 공식 라벨로 채점."})
    write_metrics_named(state, comparisons, "comparisons.csv")
    write_metrics_named(state, selective, "selective.csv")
    state.mark(unit, {"instruments": len(instruments), "panel_rows": int(len(panel)),
                      "evaluation_days": int(len(common_dates)), "label_agreement": label_agreement})
    return rows


# ---------------------------------------------------------------------------
# R02c — 그룹 D(누적 야간/장중, guides/research-candidates-plan.md R02)를 다음 날 방향 대표 모델에 더했을 때
# ---------------------------------------------------------------------------
# P03 계약: 같은 폴드·같은 날짜·같은 정답에서 특징군을 넣은 모델 − 뺀 모델의 log_loss·balanced_accuracy 쌍체
# 월 블록 CI. 여기에 P09 의 갭·세션 AUC 를 모델별로 내고 그 차이도 같은 방식으로 CI 를 낸다 — 그룹 D 가
# 세션(시가→종가) 쪽에 무엇이든 더하는지가 질문이기 때문이다. 후보는 하나뿐이다(후보를 늘리면 낙관 편향).
R02C_CURRENT = "current (market only)"
R02C_CANDIDATE = "current + D"
R02C_ASSETS = {"sam": "target", "kospi": "kospi"}


def _month_block_ci(date_index, stat_fn, b=2000, seed=42, alpha=.05):
    """노트북 block_bootstrap_ci 와 같은 달력 월 블록 부트스트랩. NaN 이 나온 표본은 버린다."""
    key = pd.PeriodIndex(pd.DatetimeIndex(date_index), freq="M")
    blocks = [np.where(key == m)[0] for m in key.unique()]
    if not blocks:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(b):
        pick = rng.integers(0, len(blocks), len(blocks))
        idx = np.concatenate([blocks[i] for i in pick])
        try:
            value = float(stat_fn(idx))
        except Exception:
            continue
        if np.isfinite(value):
            draws.append(value)
    if not draws:
        return (float("nan"), float("nan"))
    return tuple(float(v) for v in np.percentile(draws, [100 * alpha / 2, 100 * (1 - alpha / 2)]))


def leg_sign_auc(score, leg):
    """방향 점수(p_up − p_down)가 구간(갭 또는 세션) 수익률의 부호를 맞히는 AUC. 노트북 _auc_vs_sign 과 같은 규칙:
    0 과 결측은 빼고, 30개 미만이거나 부호가 한쪽뿐이면 NaN."""
    from sklearn.metrics import roc_auc_score
    score, leg = np.asarray(score, dtype=float), np.asarray(leg, dtype=float)
    ok = np.isfinite(leg) & (leg != 0) & np.isfinite(score)
    if ok.sum() < 30 or len(np.unique(leg[ok] > 0)) < 2:
        return float("nan")
    return float(roc_auc_score((leg[ok] > 0).astype(int), score[ok]))


def paired_leg_auc_delta(dates, score_a, score_b, leg, b=2000, seed=42):
    """같은 날짜에서 두 모델의 구간 AUC 차이(a − b)와 95% 월 블록 CI."""
    dates = pd.DatetimeIndex(dates)
    sa, sb, lg = (np.asarray(v, dtype=float) for v in (score_a, score_b, leg))

    def stat(idx):
        return leg_sign_auc(sa[idx], lg[idx]) - leg_sign_auc(sb[idx], lg[idx])
    delta = stat(np.arange(len(dates)))
    lo, hi = _month_block_ci(dates, stat, b=b, seed=seed)
    return {"delta": float(delta), "lo": lo, "hi": hi, "n": int(len(dates))}


def r02c_group_d(ns):
    """노트북 원시 봉(대상 종목·KOSPI 의 원본 시가·종가)으로 그룹 D 를 만들어 노트북 행(dates)에 맞춘다.

    특징 정의는 중기 러너(run_medium_horizon.group_d_features)와 같은 함수다 — 한 정의, 두 실험.
    calendar 는 노트북 feat 의 인덱스(봉 ∪ 예측일)이므로 행 d 는 d−1 세션까지만 본다.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    from run_medium_horizon import group_d_features       # 지연 import(그 모듈이 이 모듈을 import 한다)
    calendar = pd.DatetimeIndex(ns["feat"].index).tz_localize(None).normalize()
    bars = {}
    for prefix, name in R02C_ASSETS.items():
        frame = ns["raw"].get(name)
        if frame is None or len(frame) == 0 or "open" not in frame.columns or "close" not in frame.columns:
            continue
        frame = frame[["open", "close"]].astype(float).copy()
        frame.index = pd.DatetimeIndex(frame.index).tz_localize(None).normalize()
        bars[prefix] = frame.dropna().sort_index()
    if "sam" not in bars:
        raise RuntimeError("대상 종목 봉에 시가·종가가 없어 그룹 D 를 만들 수 없습니다.")
    return group_d_features(bars, calendar), sorted(bars)


def run_r02c(target, mode, storage, state, run_notebook_fn=None):
    """대표 모델의 입력(시세만) vs 같은 입력 + 그룹 D 를 같은 12폴드·같은 날짜·같은 정답에서 다시 학습해 쌍체 비교한다.

    두 후보 모두 이 실행에서 같은 코드(_ensemble_probabilities)로 학습한다. 노트북이 같은 폴드로 낸 대표 모델
    OOF 확률은 재현 확인용 참조 행으로만 둔다(다시 학습한 current 와 argmax 일치율·log_loss 차이를 기록).
    그룹 D 가 결측인 날짜(첫 60세션·KOSPI 결측)는 두 후보 모두에서 뺀다 — 쉬운 날짜만 남는 문제를 막는 공통 행 규칙.
    """
    import time

    unit = f"{target}:group_d"
    if state.is_done(unit):
        print(f"  이미 완료된 단위 건너뜀: {unit} (metrics.csv 유지)")
        return None
    if run_notebook_fn is None:
        sys.path.insert(0, str(ROOT / "tools"))
        from run_notebook import run_notebook as run_notebook_fn

    snapshot = snapshot_paths(storage, target)
    ns = run_notebook_fn(Path(storage) / target, targets=target,
                         quick=(mode == "quick"), use_cache=bool(snapshot))[target]
    features, y, dates, folds = ns["market_X"], np.asarray(ns["y"], dtype=int), pd.DatetimeIndex(ns["dates"]), ns["folds"]
    dates = dates.tz_localize(None).normalize() if dates.tz is not None else dates.normalize()
    headline = ns.get("HEADLINE_MODEL", "No macro ensemble")
    min_prob = issue_min_prob()
    b = 400 if mode == "quick" else 2000

    d_all, assets = r02c_group_d(ns)
    d_frame = d_all.reindex(dates)
    d_cols = list(d_frame.columns)
    valid = d_frame.notna().all(axis=1).to_numpy()
    X_cur = np.asarray(features, dtype=np.float32)
    X_d = np.hstack([X_cur, d_frame.to_numpy(dtype=np.float32)])
    bars = ns["raw"]["target"][["open", "close"]].astype(float).copy()
    bars.index = pd.DatetimeIndex(bars.index).tz_localize(None).normalize()
    legs = decompose_returns(bars).reindex(dates)          # 갭·세션은 평가에만 쓴다(특징에 넣지 않는다)

    frames, fold_info, skipped = [], [], []
    timing = {R02C_CURRENT: 0., R02C_CANDIDATE: 0.}
    for fold in folds:
        tr = fold["train_idx"][valid[fold["train_idx"]]]
        te = fold["test_idx"][valid[fold["test_idx"]]]
        if len(tr) < MIN_TRAIN_ROWS or len(te) < 20 or len(np.unique(y[tr])) < 3:
            skipped.append({"fold": fold["fold"], "train_rows": int(len(tr)), "test_rows": int(len(te))})
            continue
        for name, X in ((R02C_CURRENT, X_cur), (R02C_CANDIDATE, X_d)):
            started = time.time()
            probs = _ensemble_probabilities(ns, X, y, tr, te)
            timing[name] += time.time() - started
            frames.append(ns["prediction_frame"](name, dates[te], y[te], probs, fold["fold"]))
        fold_info.append({"fold": fold["fold"], "train_rows": int(len(tr)), "test_rows": int(len(te)),
                          "train_rows_dropped_by_d": int(len(fold["train_idx"]) - len(tr)),
                          "test_rows_dropped_by_d": int(len(fold["test_idx"]) - len(te))})
        print(f"  폴드 {fold['fold']}: 학습 {len(tr):,} · 시험 {len(te)} (그룹 D 결측 제외 학습 {fold_info[-1]['train_rows_dropped_by_d']} / 시험 {fold_info[-1]['test_rows_dropped_by_d']})")
    if not frames:
        raise RuntimeError("그룹 D 를 붙인 뒤 학습 가능한 폴드가 없습니다.")
    predictions = pd.concat(frames, ignore_index=True)
    # 참조 행: 노트북이 같은 폴드로 낸 대표 모델 OOF 와 사전확률(재학습하지 않는다)
    nb = ns.get("predictions")
    reference_models = []
    if nb is not None and len(nb):
        keep = [m for m in (headline, "Always flat") if m in set(nb["model"])]
        if keep:
            reference = nb[nb["model"].isin(keep)].copy()
            reference["date"] = pd.DatetimeIndex(reference["date"]).tz_localize(None).normalize()
            reference["model"] = reference["model"].map({headline: f"notebook OOF ({headline})", "Always flat": "Always flat"})
            predictions = pd.concat([predictions, reference], ignore_index=True)
            reference_models = list(reference["model"].unique())
    common_dates = None
    for _, g in predictions.groupby("model"):
        common_dates = set(g["date"]) if common_dates is None else common_dates & set(g["date"])
    predictions = predictions[predictions["date"].isin(common_dates)].reset_index(drop=True)
    metrics = ns["summarize_predictions"](predictions, with_ci=False)

    scores, leg_auc = {}, {}
    for model_name, g in predictions.groupby("model"):
        g = g.sort_values("date")
        score = (g["p_up"] - g["p_down"]).to_numpy()
        scores[model_name] = pd.Series(score, index=pd.DatetimeIndex(g["date"]))
        leg_auc[model_name] = {"auc_gap": leg_sign_auc(score, legs["gap"].reindex(g["date"]).to_numpy()),
                               "auc_session": leg_sign_auc(score, legs["session"].reindex(g["date"]).to_numpy())}
    rows = []
    for model_name, r in metrics.iterrows():
        rows.append({"target": target, "model": model_name, "target_mode": "close_to_close", "fold": "all",
                     **{k: r.get(k, "") for k in ("n", "accuracy", "balanced_accuracy", "log_loss", "brier")},
                     **leg_auc.get(model_name, {}), "seconds": round(timing.get(model_name, 0.), 1),
                     "n_features": (X_d.shape[1] if model_name == R02C_CANDIDATE else X_cur.shape[1]
                                    if model_name == R02C_CURRENT else "")})

    comparisons = []
    pairs = [(R02C_CANDIDATE, R02C_CURRENT, f"{R02C_CANDIDATE} − {R02C_CURRENT}")]
    if "Always flat" in reference_models:
        pairs.append((R02C_CANDIDATE, "Always flat", f"{R02C_CANDIDATE} − 사전확률"))
    nb_name = f"notebook OOF ({headline})"
    if nb_name in reference_models:
        pairs.append((R02C_CURRENT, nb_name, f"{R02C_CURRENT} − 노트북 OOF(재현 확인)"))
    for a, b_, label in pairs:
        for metric in ("log_loss", "balanced_accuracy", "accuracy"):
            d = ns["paired_delta_ci"](predictions, a, b_, metric)
            comparisons.append({"target": target, "comparison": label, "metric": metric, "delta": d["delta"],
                                "ci_low": d["lo"], "ci_high": d["hi"], "common_n": d["n"],
                                "verdict": verdict_for(metric, d["lo"], d["hi"])})
        if a == R02C_CANDIDATE and b_ == R02C_CURRENT:
            common = scores[a].index.intersection(scores[b_].index)
            for leg in ("gap", "session"):
                d = paired_leg_auc_delta(common, scores[a].reindex(common).to_numpy(), scores[b_].reindex(common).to_numpy(),
                                         legs[leg].reindex(common).to_numpy(), b=b)
                comparisons.append({"target": target, "comparison": label, "metric": f"auc_{leg}", "delta": d["delta"],
                                    "ci_low": d["lo"], "ci_high": d["hi"], "common_n": d["n"],
                                    "verdict": verdict_for(f"auc_{leg}", d["lo"], d["hi"])})
    selective = []
    for model_name in [R02C_CURRENT, R02C_CANDIDATE] + [m for m in reference_models if m != "Always flat"]:
        g = predictions[predictions["model"] == model_name].sort_values("date")
        selective += selective_rows(target, model_name, g[["p_down", "p_flat", "p_up"]].to_numpy(), g["y_true"].to_numpy())
    argmax_agreement = float("nan")
    if nb_name in reference_models:
        a = predictions[predictions["model"] == R02C_CURRENT].sort_values("date")["y_pred"].to_numpy()
        b2 = predictions[predictions["model"] == nb_name].sort_values("date")["y_pred"].to_numpy()
        argmax_agreement = float(np.mean(a == b2))

    write_json(state.run_dir / f"r02c_{target}.json", {
        "headline_model": headline, "assets": assets, "group_d_columns": d_cols, "n_group_d_columns": len(d_cols),
        "n_current_features": int(X_cur.shape[1]), "n_candidate_features": int(X_d.shape[1]),
        "rows_missing_group_d": int((~valid).sum()), "common_evaluation_days": int(len(common_dates)),
        "folds": fold_info, "skipped_folds": skipped, "timing_seconds": {k: round(v, 1) for k, v in timing.items()},
        "issue_min_prob": min_prob, "argmax_agreement_current_vs_notebook": argmax_agreement,
        "leg_auc": leg_auc, "comparisons": comparisons, "selective": selective,
        "note": "두 후보는 같은 코드로 재학습(대표 모델 결합·후보·온도·선택 규칙). 갭·세션은 평가에만 쓰고 특징에 넣지 않는다. "
                "그룹 D 결측 날짜는 두 후보 모두에서 제외(공통 행)."})
    write_metrics_named(state, comparisons, "comparisons.csv")
    write_metrics_named(state, selective, "selective.csv")
    state.mark(unit, {"group_d_columns": len(d_cols), "evaluation_days": int(len(common_dates)),
                      "argmax_agreement_current_vs_notebook": argmax_agreement})
    return rows


# ---------------------------------------------------------------------------
# P16 — 시가 확정 후(09:37) 종가 방향 재예측
# ---------------------------------------------------------------------------
# P09·P10b·R02c 는 모두 "07:00 에 무엇을 더 넣을까"를 물었고 전부 동률이었다. P16 은 **모델이 아니라
# 정보 마감 시각**을 바꾼다: 09:00 시가가 확정된 뒤 같은 타깃(전일 종가→당일 종가)을 다시 묻는다.
# 그러면 라벨의 갭 성분이 예측 대상이 아니라 **관측값**이 된다. 얻는 것이 있어도 그것은 더 나은
# 모델이 아니라 늦은 정보 시점의 결과이고, decision.md 는 그 사실을 먼저 적어야 한다.
P16_T0700 = "t0700"
P16_T0900 = "t0900"
P16_GAP_RULE = "gap_rule"
P16_LABEL_TARGETS = ("close_to_close", "session")
# 그룹 G 정의와 갭 규칙은 forecast_utils 의 것 하나뿐이다(2026-09-20 운영 반영). 노트북(아침 격자)과 러너
# (P16 실험)가 같은 함수를 쓰므로, 실험이 잰 것과 운영이 내는 것이 어긋날 수 없다. 이름은 호환용 별칭이다.
sys.path.insert(0, str(ROOT))
from forecast_utils import (  # noqa: E402
    POST_OPEN_GAP_COLUMNS as P16_GAP_COLUMNS, POST_OPEN_GAP_Z_WINDOW as P16_GAP_Z_WINDOW,
    gap_rule_labels, post_open_gap_features,
)


def p16_gap_features(bars, band, calendar, window=P16_GAP_Z_WINDOW):
    """그룹 G — forecast_utils.post_open_gap_features 에 위임한다(정의는 그쪽 docstring)."""
    return post_open_gap_features(bars, band, calendar, window=window)


def gap_rule_probabilities(rule_train, y_train, rule_test, alpha=1., min_bucket=20):
    """규칙 버킷별 **학습 구간** 조건부 분포(라플라스 평활). 외부 라벨은 보지 않는다.

    규칙 자체는 확률이 없어 log_loss 를 낼 수 없다. 버킷이 세 개뿐인 범주형 분류기로 보고
    학습 구간의 P(y | 버킷)을 확률로 쓴다. 표본이 적은 버킷은 학습 구간 사전확률로 되돌린다.
    y_pred 는 이 확률의 argmax 가 아니라 **규칙이 말한 방향** 그대로다(정의에 충실하게).
    """
    rule_train, rule_test = np.asarray(rule_train, dtype=int), np.asarray(rule_test, dtype=int)
    y_train = np.asarray(y_train, dtype=int)
    prior = np.bincount(y_train, minlength=3) + alpha
    prior = prior / prior.sum()
    table = {}
    for bucket in (0, 1, 2):
        mask = rule_train == bucket
        if int(mask.sum()) < min_bucket:
            table[bucket] = prior
            continue
        counts = np.bincount(y_train[mask], minlength=3) + alpha
        table[bucket] = counts / counts.sum()
    return np.vstack([table[int(b)] for b in rule_test]), {str(k): [float(v) for v in table[k]] for k in table}


def run_p16(target, mode, storage, state, run_notebook_fn=None):
    """같은 12폴드·같은 날짜에서 t0700(현행 07:00 특징) vs t0900(+ 실현 갭 그룹 G)을 재학습해 쌍체 비교한다.

    타깃은 보고서와 같은 종가→종가가 1순위이고, 세션(시가→종가)은 진단용 2순위다. 세션에서
    이득이 없고 종가→종가에서만 이득이 나면 그 이득은 **관측된 갭**에서 온 것이지 더 나은 모델이
    아니다. 모델 없는 `gap_rule`(갭 부호만 읽는 규칙)을 같은 날짜에 함께 채점해 그 사실을 드러낸다.
    """
    import time

    unit = f"{target}:post_open"
    if state.is_done(unit):
        print(f"  이미 완료된 단위 건너뜀: {unit} (metrics.csv 유지)")
        return None
    if run_notebook_fn is None:
        sys.path.insert(0, str(ROOT / "tools"))
        from run_notebook import run_notebook as run_notebook_fn

    snapshot = snapshot_paths(storage, target)
    ns = run_notebook_fn(Path(storage) / target, targets=target,
                         quick=(mode == "quick"), use_cache=bool(snapshot))[target]
    features, dates, folds = ns["market_X"], pd.DatetimeIndex(ns["dates"]), ns["folds"]
    dates = dates.tz_localize(None).normalize() if dates.tz is not None else dates.normalize()
    headline = ns.get("HEADLINE_MODEL", "No macro ensemble")
    vol_mult = float(ns.get("VOL_BAND_MULT", .3))
    min_prob = issue_min_prob()
    boot = 400 if mode == "quick" else 2000

    bars = ns["raw"]["target"][["open", "close"]].astype(float).copy()
    bars.index = pd.DatetimeIndex(bars.index).tz_localize(None).normalize()
    bars = bars.sort_index()
    feat = ns["feat"]
    feat_index = pd.DatetimeIndex(feat.index)
    band_all = pd.Series(np.asarray(feat["band"], dtype=float),
                         index=feat_index.tz_localize(None).normalize() if feat_index.tz is not None
                         else feat_index.normalize())
    band_all = band_all[~band_all.index.duplicated(keep="last")]

    gap_frame = p16_gap_features(bars, band_all.reindex(bars.index), bars.index)
    G = gap_frame.reindex(dates)
    valid_g = G.notna().all(axis=1).to_numpy()
    has_bar = pd.Index(dates).isin(bars.index)
    missing_bar = int((~has_bar).sum())
    no_open = int(bars["open"].reindex(dates).isna().to_numpy()[has_bar].sum())
    X_0700 = np.asarray(features, dtype=np.float32)
    X_0900 = np.hstack([X_0700, G.to_numpy(dtype=np.float32)])

    legs = decompose_returns(bars).reindex(dates)          # 갭·세션은 평가에, 갭만 특징으로(그룹 G)
    band_cc = band_all.reindex(dates)
    rule = gap_rule_labels(legs["gap"].to_numpy(), band_cc.to_numpy())
    session_band = legs["session"].rolling(20).std().shift(1) * vol_mult
    labels = {"close_to_close": np.asarray(ns["y"], dtype=float),
              "session": leg_labels(legs["session"].to_numpy(), session_band.to_numpy())}

    rows, comparisons, selective, detail = [], [], [], {}
    for leg_target in P16_LABEL_TARGETS:
        y_leg = labels[leg_target]
        valid = valid_g & np.isfinite(y_leg)
        y_int = np.where(valid, y_leg, 1).astype(int)
        frames, fold_info, skipped = [], [], []
        timing = {P16_T0700: 0., P16_T0900: 0.}
        for fold in folds:
            tr = fold["train_idx"][valid[fold["train_idx"]]]
            te = fold["test_idx"][valid[fold["test_idx"]]]
            if len(tr) < MIN_TRAIN_ROWS or len(te) < 20 or len(np.unique(y_int[tr])) < 3:
                skipped.append({"fold": fold["fold"], "train_rows": int(len(tr)), "test_rows": int(len(te))})
                continue
            for name, X in ((P16_T0700, X_0700), (P16_T0900, X_0900)):
                started = time.time()
                probs = _ensemble_probabilities(ns, X, y_int, tr, te)
                timing[name] += time.time() - started
                frames.append(ns["prediction_frame"](name, dates[te], y_int[te], probs, fold["fold"]))
            if leg_target == "close_to_close":
                probs, _ = gap_rule_probabilities(rule[tr], y_int[tr], rule[te])
                frames.append(ns["prediction_frame"](P16_GAP_RULE, dates[te], y_int[te], probs, fold["fold"],
                                                     y_pred=rule[te].astype(int)))
            fold_info.append({"fold": fold["fold"], "train_rows": int(len(tr)), "test_rows": int(len(te)),
                              "train_rows_dropped": int(len(fold["train_idx"]) - len(tr)),
                              "test_rows_dropped": int(len(fold["test_idx"]) - len(te))})
            print(f"  [{leg_target}] 폴드 {fold['fold']}: 학습 {len(tr):,} · 시험 {len(te)}"
                  f" (제외 학습 {fold_info[-1]['train_rows_dropped']} / 시험 {fold_info[-1]['test_rows_dropped']})")
        if not frames:
            raise RuntimeError(f"{leg_target}: 학습 가능한 폴드가 없습니다.")
        predictions = pd.concat(frames, ignore_index=True)

        reference_models = []
        if leg_target == "close_to_close":
            nb = ns.get("predictions")
            if nb is not None and len(nb):
                keep = [m for m in (headline, "Always flat") if m in set(nb["model"])]
                if keep:
                    reference = nb[nb["model"].isin(keep)].copy()
                    reference["date"] = pd.DatetimeIndex(reference["date"]).tz_localize(None).normalize()
                    reference["model"] = reference["model"].map(
                        {headline: f"notebook OOF ({headline})", "Always flat": "Always flat"})
                    predictions = pd.concat([predictions, reference], ignore_index=True)
                    reference_models = list(reference["model"].unique())
        common_dates = None
        for _, g in predictions.groupby("model"):
            common_dates = set(g["date"]) if common_dates is None else common_dates & set(g["date"])
        predictions = predictions[predictions["date"].isin(common_dates)].reset_index(drop=True)
        metrics = ns["summarize_predictions"](predictions, with_ci=False)

        scores, leg_auc = {}, {}
        for model_name, g in predictions.groupby("model"):
            g = g.sort_values("date")
            score = (g["p_up"] - g["p_down"]).to_numpy()
            scores[model_name] = pd.Series(score, index=pd.DatetimeIndex(g["date"]))
            leg_auc[model_name] = {
                "auc_gap": leg_sign_auc(score, legs["gap"].reindex(g["date"]).to_numpy()),
                "auc_session": leg_sign_auc(score, legs["session"].reindex(g["date"]).to_numpy())}
        for model_name, r in metrics.iterrows():
            rows.append({"target": target, "model": model_name, "target_mode": leg_target, "fold": "all",
                         **{k: r.get(k, "") for k in ("n", "accuracy", "balanced_accuracy", "log_loss", "brier")},
                         **leg_auc.get(model_name, {}), "seconds": round(timing.get(model_name, 0.), 1),
                         "n_features": (X_0900.shape[1] if model_name == P16_T0900 else
                                        X_0700.shape[1] if model_name == P16_T0700 else "")})

        pairs = [(P16_T0900, P16_T0700, f"{P16_T0900} − {P16_T0700}")]
        if P16_GAP_RULE in set(predictions["model"]):
            pairs.append((P16_T0900, P16_GAP_RULE, f"{P16_T0900} − {P16_GAP_RULE}"))
            pairs.append((P16_T0700, P16_GAP_RULE, f"{P16_T0700} − {P16_GAP_RULE}"))
        for name in (P16_T0700, P16_T0900, P16_GAP_RULE):
            if "Always flat" in reference_models and name in set(predictions["model"]):
                pairs.append((name, "Always flat", f"{name} − 사전확률"))
        nb_name = f"notebook OOF ({headline})"
        if nb_name in reference_models:
            pairs.append((P16_T0700, nb_name, f"{P16_T0700} − 노트북 OOF(재현 확인)"))
        for a, b_, label in pairs:
            for metric in ("log_loss", "balanced_accuracy", "accuracy"):
                d = ns["paired_delta_ci"](predictions, a, b_, metric)
                comparisons.append({"target": target, "target_mode": leg_target, "comparison": label,
                                    "metric": metric, "delta": d["delta"], "ci_low": d["lo"], "ci_high": d["hi"],
                                    "common_n": d["n"], "verdict": verdict_for(metric, d["lo"], d["hi"])})
            if b_ in (P16_T0700, P16_GAP_RULE):
                common = scores[a].index.intersection(scores[b_].index)
                for leg in ("gap", "session"):
                    d = paired_leg_auc_delta(common, scores[a].reindex(common).to_numpy(),
                                             scores[b_].reindex(common).to_numpy(),
                                             legs[leg].reindex(common).to_numpy(), b=boot)
                    comparisons.append({"target": target, "target_mode": leg_target, "comparison": label,
                                        "metric": f"auc_{leg}", "delta": d["delta"], "ci_low": d["lo"],
                                        "ci_high": d["hi"], "common_n": d["n"],
                                        "verdict": verdict_for(f"auc_{leg}", d["lo"], d["hi"])})
        for model_name in [m for m in (P16_T0700, P16_T0900, P16_GAP_RULE) if m in set(predictions["model"])] \
                + [m for m in reference_models if m != "Always flat"]:
            g = predictions[predictions["model"] == model_name].sort_values("date")
            selective += [{"target_mode": leg_target, **row} for row in
                          selective_rows(target, model_name, g[["p_down", "p_flat", "p_up"]].to_numpy(),
                                         g["y_true"].to_numpy())]
        argmax_agreement, logloss_gap = float("nan"), float("nan")
        if nb_name in reference_models:
            a = predictions[predictions["model"] == P16_T0700].sort_values("date")
            b2 = predictions[predictions["model"] == nb_name].sort_values("date")
            argmax_agreement = float(np.mean(a["y_pred"].to_numpy() == b2["y_pred"].to_numpy()))
            logloss_gap = float(metrics.loc[P16_T0700, "log_loss"] - metrics.loc[nb_name, "log_loss"])
        rule_agreement = float("nan")
        if P16_GAP_RULE in set(predictions["model"]):
            g = predictions[predictions["model"] == P16_GAP_RULE]
            rule_agreement = float(np.mean(g["y_pred"].to_numpy()
                                           == g[["p_down", "p_flat", "p_up"]].to_numpy().argmax(axis=1)))
        detail[leg_target] = {
            "common_evaluation_days": int(len(common_dates)), "folds": fold_info, "skipped_folds": skipped,
            "timing_seconds": {k: round(v, 1) for k, v in timing.items()}, "leg_auc": leg_auc,
            "rows_missing_group_g": int((~valid_g).sum()), "rows_missing_label": int((~np.isfinite(y_leg)).sum()),
            "rows_excluded_total": int((~valid).sum()),
            "argmax_agreement_t0700_vs_notebook": argmax_agreement,
            "log_loss_gap_t0700_minus_notebook": logloss_gap,
            "gap_rule_argmax_agreement": rule_agreement}

    write_json(state.run_dir / f"p16_{target}.json", {
        "headline_model": headline, "group_g_columns": list(P16_GAP_COLUMNS), "gap_z_window": P16_GAP_Z_WINDOW,
        "n_t0700_features": int(X_0700.shape[1]), "n_t0900_features": int(X_0900.shape[1]),
        "dates_without_bar": missing_bar, "dates_without_open": no_open,
        "issue_min_prob": min_prob, "vol_band_mult": vol_mult, "by_label_target": detail,
        "comparisons": comparisons, "selective": selective,
        "note": "t0900 은 모델이 아니라 정보 마감 시각을 09:00(시가 확정) 이후로 옮긴 것이다. 행 d 가 쓰는 당일 정보는 "
                "시가 하나뿐이고 종가·고가·저가·거래량은 들어가지 않는다. 그룹 G 결측 날짜는 두 후보 모두에서 제외(공통 행). "
                "gap_rule 은 모델 없는 규칙이며 확률은 학습 구간 버킷별 조건부 분포다."})
    write_metrics_named(state, comparisons, "comparisons.csv")
    write_metrics_named(state, selective, "selective.csv")
    state.mark(unit, {"label_targets": list(P16_LABEL_TARGETS),
                      "evaluation_days": {k: v["common_evaluation_days"] for k, v in detail.items()},
                      "argmax_agreement_t0700_vs_notebook":
                          detail["close_to_close"]["argmax_agreement_t0700_vs_notebook"]})
    return rows


TASK_RUNNERS = {"P00": run_p00, "P03": run_p03, "P04": run_p04, "P05": run_p05,
                "P06": run_p06, "P07": run_p07, "P08": run_p08, "P09": run_p09, "P10": run_p10,
                "P10b": run_p10b, "R02c": run_r02c, "P16": run_p16}


# ---------------------------------------------------------------------------
def write_metrics(state, rows):
    if not rows:
        return
    import csv
    import io
    # 행마다 열이 다를 수 있다(예: 세션 타깃에만 있는 session_bp_*). 첫 행의 열만 쓰면
    # 나머지 행이 거부되므로 열 합집합을 쓴다. 없는 값은 빈 칸으로 남긴다.
    fieldnames = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
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
    elif task == "P07":
        config = {"task": "P07", "mode": mode, "candidates": list(ENSEMBLE_CANDIDATES),
                  "grid": list(ENSEMBLE_WEIGHT_GRID)}
    elif task == "P08":
        config = {"task": "P08", "mode": mode,
                  "thresholds": ["none" if t is None else t for t in ABSTAIN_THRESHOLDS]}
    elif task == "P09":
        config = {"task": "P09", "mode": mode, "legs": list(TARGET_LEGS)}
    elif task == "P10":
        config = {"task": "P10", "mode": mode, "selection_date": "2026-09-10", "window": BASELINE_WINDOW}
    elif task == "P10b":
        config = {"task": "P10b", "mode": mode, "selection_date": "2026-09-10", "window": BASELINE_WINDOW,
                  "families": list(PANEL_FAMILIES), "inner_block_dates": PANEL_INNER_BLOCK_DATES,
                  "common_feature_rule": "headline market inputs minus " + "/".join(
                      panel_foreign_module().TARGET_SPECIFIC_PREFIXES),
                  "issue_min_prob": issue_min_prob()}
    elif task == "R02c":
        config = {"task": "R02c", "mode": mode, "candidate": R02C_CANDIDATE, "reference": R02C_CURRENT,
                  "group_d": {"assets": sorted(R02C_ASSETS), "windows": [5, 20, 60],
                              "definition": "누적 야간 Π(open_t/close_{t-1})-1, 누적 장중 Π(close_t/open_t)-1, 둘의 차. 행 d 는 d-1 세션까지"},
                  "headline": "No macro ensemble", "window": BASELINE_WINDOW, "issue_min_prob": issue_min_prob()}
    elif task == "P16":
        config = {"task": "P16", "mode": mode, "candidate": P16_T0900, "reference": P16_T0700,
                  "trivial_baseline": P16_GAP_RULE, "label_targets": list(P16_LABEL_TARGETS),
                  "group_g": {"columns": list(P16_GAP_COLUMNS), "z_window": P16_GAP_Z_WINDOW,
                              "definition": "gap = open_d/close_{d-1} − 1. 행 d 의 당일 정보는 시가 하나뿐이다"},
                  "headline": "No macro ensemble", "window": BASELINE_WINDOW, "issue_min_prob": issue_min_prob()}
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
        # 단위는 계산 끝에 완료로 표시되고 metrics.csv는 그 뒤에 쓰인다. 그 사이에 죽으면
        # "완료인데 산출물 없음"이 남고 재개가 그것을 건너뛴다. 산출물이 없으면 다시 돌린다.
        if rows is None and not (state.run_dir / "metrics.csv").is_file():
            print("  완료 표시는 있는데 metrics.csv가 없습니다. 단위를 다시 계산합니다.")
            state.reset_units()
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

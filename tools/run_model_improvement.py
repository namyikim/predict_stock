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

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_TARGETS = ("samsung", "sk_hynix")
MODES = ("quick", "full")

# 등록된 작업만 실행한다. 계획에 있어도 구현 전이면 여기 없고, 그때는 명확히 거절한다.
# 각 작업을 구현할 때 이 표에 한 줄씩 추가한다.
TASKS = {
    "P00": "현행 기준선과 평가 계약 고정",
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


TASK_RUNNERS = {"P00": run_p00}


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
    config = p00_config(mode) if task == "P00" else {"mode": mode}
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
        state.fail(f"{target}:baseline", f"{type(exc).__name__}: {exc}")
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

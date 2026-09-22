# -*- coding: utf-8 -*-
"""S02 Task 3 — 재개 가능한 실험 CLI(tools/run_weekly_sequence.py).

합성 스냅샷으로 러너의 동작(산출물, 재개, 훼손 복구, 설정 격리, 비밀 유출 없음)만 검증한다.
여기서 나온 수치는 기준선의 기준값이지 개선 증거가 아니다.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from test_weekly_sequence import KST, long_fixture, write_snapshot  # noqa: E402

RUNNER = ROOT / "tools" / "run_weekly_sequence.py"
ARTIFACTS = ("manifest.json", "metrics.csv", "comparisons.csv", "folds.csv", "decision.md", "checkpoint.json")


def make_storage(root, years=None):
    """runs/<target>/data_cache 에 합성 스냅샷을 둔다. years 를 주면 그만큼 긴 자료(S03 은 내부 검증이 필요)."""
    cache = Path(root) / "storage" / "samsung" / "data_cache"
    cache.mkdir(parents=True)
    if years:
        import exchange_calendars as xc
        sessions = pd.DatetimeIndex(xc.get_calendar("XKRX").sessions_in_range(f"{years[0]}-01-01", f"{years[1]}-12-31")).tz_localize(None)
        rng = np.random.default_rng(0)
        close = pd.Series(60000 * np.exp(np.cumsum(rng.normal(0, 0.015, len(sessions)))), index=sessions)
        bars = pd.DataFrame({"open": close * (1 + rng.normal(0, 0.003, len(sessions))), "close": close,
                             "volume": rng.integers(500, 2000, len(sessions)).astype(float)}, index=sessions)
        bars["high"] = bars[["open", "close"]].max(axis=1) * 1.01
        bars["low"] = bars[["open", "close"]].min(axis=1) * 0.99
        write_snapshot(cache, bars)
    else:
        write_snapshot(cache, long_fixture()["bars"])
    return Path(root) / "storage", Path(root) / "results"


def run_cli(storage, results, *extra, env_extra=None):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(RUNNER), "--task", "S02", "--target", "samsung",
                          "--mode", "quick", "--storage", str(storage), "--results", str(results),
                          "--resume", *extra], capture_output=True, text=True, env=env, cwd=ROOT, timeout=600)


def only_run_dir(results):
    runs = sorted((Path(results) / "S02").iterdir())
    assert len(runs) == 1, runs
    return runs[0]


class RunnerTests(unittest.TestCase):
    def test_missing_snapshot_exits_2_without_downloading(self):
        root = Path(tempfile.mkdtemp())
        storage, results = root / "storage", root / "results"
        (storage / "samsung" / "data_cache").mkdir(parents=True)
        out = run_cli(storage, results)
        self.assertEqual(out.returncode, 2, out.stderr[-500:])
        self.assertIn("내려받지 않습니다", out.stderr + out.stdout)
        self.assertFalse((results / "S02").exists())

    def test_quick_run_produces_all_artifacts(self):
        storage, results = make_storage(tempfile.mkdtemp())
        out = run_cli(storage, results)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        run_dir = only_run_dir(results)
        for name in ARTIFACTS:
            self.assertTrue((run_dir / name).is_file(), name)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "completed")
        for key in ("code_commit", "data_hash", "config_hash", "versions", "seed", "availability_policy",
                    "corporate_actions", "date_range"):
            self.assertIn(key, manifest, key)
        self.assertEqual(manifest["availability_policy"], "assumed")
        self.assertEqual(manifest["corporate_actions"], "heuristic")
        metrics = pd.read_csv(run_dir / "metrics.csv")
        self.assertEqual(set(metrics["model"]), {"persistence", "ohlcv_ridge"})
        self.assertEqual(metrics.groupby("model")["n"].sum().nunique(), 1)       # 공통 표본
        comparisons = pd.read_csv(run_dir / "comparisons.csv")
        self.assertIn("ohlcv_ridge_vs_persistence", set(comparisons["comparison"]))
        self.assertTrue({"mae_diff", "ci_low", "ci_high"} <= set(comparisons.columns))
        decision = (run_dir / "decision.md").read_text(encoding="utf-8")
        self.assertIn("채택 판단 없음", decision)
        self.assertIn("탐색 평가", decision)
        self.assertIn("operational_ridge", decision)                               # skipped 사유가 적힌다

    def test_interrupt_then_resume_skips_completed_units(self):
        storage, results = make_storage(tempfile.mkdtemp())
        first = run_cli(storage, results, env_extra={"WEEKLY_SEQ_FAIL_AFTER": "baseline:ohlcv_ridge"})
        self.assertNotEqual(first.returncode, 0)
        run_dir = only_run_dir(results)
        checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
        done = checkpoint["completed_units"]
        self.assertIn("baseline:ohlcv_ridge", done)
        self.assertNotIn("comparison", done)
        second = run_cli(storage, results)
        self.assertEqual(second.returncode, 0, second.stderr[-800:])
        self.assertEqual(only_run_dir(results), run_dir)                          # 같은 run 을 이어 간다
        self.assertIn("건너뜀", second.stdout)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "completed")
        self.assertIn("resumed_at_utc", manifest)

    def test_damaged_artifact_is_recomputed_despite_completion_mark(self):
        storage, results = make_storage(tempfile.mkdtemp())
        self.assertEqual(run_cli(storage, results).returncode, 0)
        run_dir = only_run_dir(results)
        (run_dir / "metrics.csv").unlink()
        out = run_cli(storage, results)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        self.assertTrue((run_dir / "metrics.csv").is_file())
        self.assertIn("다시 계산", out.stdout)

    def test_config_change_creates_a_separate_run(self):
        storage, results = make_storage(tempfile.mkdtemp())
        self.assertEqual(run_cli(storage, results).returncode, 0)
        first = only_run_dir(results)
        out = run_cli(storage, results, "--alpha", "100")
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        runs = sorted((results / "S02").iterdir())
        self.assertEqual(len(runs), 2)
        self.assertTrue((first / "metrics.csv").is_file())                         # 이전 산출물 보존
        hashes = {json.loads((r / "manifest.json").read_text(encoding="utf-8"))["config_hash"] for r in runs}
        self.assertEqual(len(hashes), 2)

    def test_manifest_never_contains_secrets(self):
        storage, results = make_storage(tempfile.mkdtemp())
        secret = "github_pat_THIS_MUST_NOT_LEAK_0123456789"
        out = run_cli(storage, results, env_extra={"GITHUB_TOKEN": secret, "ECOS_API_KEY": "ecos-secret-xyz"})
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        run_dir = only_run_dir(results)
        for name in ARTIFACTS:
            text = (run_dir / name).read_text(encoding="utf-8")
            self.assertNotIn(secret, text, name)
            self.assertNotIn("ecos-secret-xyz", text, name)


if __name__ == "__main__":
    unittest.main()


# ----------------------------------------------------------------------------------------------
# S03 — TCN 단위
# ----------------------------------------------------------------------------------------------
try:
    import torch  # noqa: F401
    HAVE_TORCH = True
except ImportError:
    HAVE_TORCH = False


def run_s03(storage, results, *extra, env_extra=None):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(RUNNER), "--task", "S03", "--target", "samsung",
                          "--mode", "quick", "--storage", str(storage), "--results", str(results),
                          "--resume", *extra], capture_output=True, text=True, env=env, cwd=ROOT, timeout=900)


def only_s03_dir(results):
    runs = sorted((Path(results) / "S03").iterdir())
    assert len(runs) == 1, runs
    return runs[0]


class S03RunnerTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_TORCH, "torch 없음")
    def test_three_seeds_are_all_reported(self):
        storage, results = make_storage(tempfile.mkdtemp(), years=(2019, 2025))
        out = run_s03(storage, results)
        self.assertEqual(out.returncode, 0, out.stderr[-1200:])
        run_dir = only_s03_dir(results)
        for seed in (42, 43, 44):
            self.assertTrue((run_dir / f"predictions_tcn_seed{seed}.csv").is_file(), seed)
        metrics = pd.read_csv(run_dir / "metrics.csv")
        tcn_rows = metrics[metrics["model"].str.startswith("tcn")]
        self.assertEqual(set(tcn_rows["model"]), {"tcn_seed42", "tcn_seed43", "tcn_seed44", "tcn_mean"})
        self.assertIn("seed", metrics.columns)
        comps = set(pd.read_csv(run_dir / "comparisons.csv")["comparison"])
        self.assertTrue({"tcn_mean_vs_persistence", "tcn_mean_vs_ohlcv_ridge"} <= comps)
        decision = (run_dir / "decision.md").read_text(encoding="utf-8")
        self.assertIn("최고 seed 선택 없음", decision)
        self.assertIn("채택 판단 없음", decision)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        ck = manifest["checkpoints"]
        self.assertEqual(set(ck), {"42", "43", "44"})
        for entry in ck.values():
            self.assertTrue(Path(entry["path"]).is_file())
            self.assertFalse(str(Path(entry["path"]).resolve()).startswith(str(Path(results).resolve())))
            self.assertEqual(len(entry["sha256"]), 64)

    @unittest.skipUnless(HAVE_TORCH, "torch 없음")
    def test_interrupt_after_seed43_resumes_from_seed44(self):
        storage, results = make_storage(tempfile.mkdtemp(), years=(2019, 2025))
        first = run_s03(storage, results, env_extra={"WEEKLY_SEQ_FAIL_AFTER": "tcn:seed43"})
        self.assertNotEqual(first.returncode, 0)
        run_dir = only_s03_dir(results)
        done = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))["completed_units"]
        self.assertIn("tcn:seed43", done)
        self.assertNotIn("tcn:seed44", done)
        second = run_s03(storage, results)
        self.assertEqual(second.returncode, 0, second.stderr[-1200:])
        self.assertEqual(only_s03_dir(results), run_dir)
        self.assertIn("[tcn:seed42] 건너뜀", second.stdout)
        self.assertIn("[tcn:seed43] 건너뜀", second.stdout)

    @unittest.skipUnless(HAVE_TORCH, "torch 없음")
    def test_deleted_seed_predictions_recompute_only_that_seed(self):
        storage, results = make_storage(tempfile.mkdtemp(), years=(2019, 2025))
        self.assertEqual(run_s03(storage, results).returncode, 0)
        run_dir = only_s03_dir(results)
        (run_dir / "predictions_tcn_seed43.csv").unlink()
        out = run_s03(storage, results)
        self.assertEqual(out.returncode, 0, out.stderr[-1200:])
        self.assertIn("[tcn:seed42] 건너뜀", out.stdout)
        self.assertIn("[tcn:seed43] 완료 표시가 있지만 산출물이 없어 다시 계산", out.stdout)
        self.assertTrue((run_dir / "predictions_tcn_seed43.csv").is_file())

    def test_missing_torch_is_skipped_explicitly(self):
        storage, results = make_storage(tempfile.mkdtemp(), years=(2019, 2025))
        out = run_s03(storage, results, env_extra={"WEEKLY_SEQ_NO_TORCH": "1"})
        self.assertEqual(out.returncode, 0, out.stderr[-1200:])
        run_dir = only_s03_dir(results)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "completed")
        results_map = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))["results"]
        for unit in ("tcn:seed42", "tcn:seed43", "tcn:seed44", "tcn:summary"):
            self.assertEqual(results_map[unit]["status"], "skipped", unit)
            self.assertIn("torch", results_map[unit]["note"])
        self.assertIn("skipped: torch missing", (run_dir / "decision.md").read_text(encoding="utf-8"))

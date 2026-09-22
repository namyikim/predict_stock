# -*- coding: utf-8 -*-
"""S04 — 실제 스냅샷 full 비교(운영 Ridge 연결, 잠금 1회 열기, 기계적 판정).

합성 데이터로 코드 동작만 검증한다. 실제 수치는 스냅샷이 있는 환경(Colab)에서 나온다.
"""
import json
import os
import pickle
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

from test_weekly_sequence_runner import make_storage, RUNNER  # noqa: E402

try:
    import torch  # noqa: F401
    HAVE_TORCH = True
except ImportError:
    HAVE_TORCH = False


def fake_operational_inputs(bars, feature_cols=("f1", "f2", "f3"), seed=0):
    """run_medium_horizon.extract_inputs 형식의 최소 pkl. 특징 행은 종목 봉 날짜(원본 봉 d-1 기준)."""
    rng = np.random.default_rng(seed)
    sam = bars[["open", "high", "low", "close", "volume"]].copy()
    feat = pd.DataFrame(rng.normal(0, 1, (len(sam), len(feature_cols))), index=sam.index, columns=list(feature_cols))
    feat["sam_vol_20"] = sam["close"].pct_change().rolling(20).std().bfill().fillna(0.01) + 1e-4
    return {"feat": feat, "sam": sam, "feature_cols": list(feature_cols), "sam_raw_close": sam["close"],
            "live_row": feat.iloc[[-1]], "prediction_date": sam.index[-1], "last_bar": sam.index[-1],
            "versions": {}, "data_snapshot_hash": "x", "macro_active": False, "bootstrap_b": 200,
            "quick_mode": True, "vol_model": "simple", "band_coverage": 0.8, "price_forecast_stats": {}}


def write_operational_pkl(storage, target, mode, bars, data_hash="synthetic"):
    path = Path(storage) / target / f"inputs_{mode}_{data_hash}.pkl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = fake_operational_inputs(bars)
    payload.update(data_hash=data_hash, mode=mode, target=target)
    with open(path, "wb") as handle:
        pickle.dump(payload, handle)
    return path


def run_s04(storage, results, *extra, env_extra=None):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(RUNNER), "--task", "S04", "--target", "samsung",
                          "--mode", "quick", "--storage", str(storage), "--results", str(results),
                          "--resume", *extra], capture_output=True, text=True, env=env, cwd=ROOT, timeout=1500)


def only_s04_dir(results):
    runs = sorted(p for p in (Path(results) / "S04").iterdir() if p.is_dir())
    assert len(runs) == 1, runs
    return runs[0]


def snapshot_bars(storage):
    from weekly_sequence_utils import load_ohlcv_snapshot
    return load_ohlcv_snapshot(Path(storage) / "samsung" / "data_cache", "samsung").bars


class OperationalRidgeTests(unittest.TestCase):
    def test_missing_operational_inputs_exits_2(self):
        storage, results = make_storage(tempfile.mkdtemp(), years=(2019, 2025))
        out = run_s04(storage, results)
        self.assertEqual(out.returncode, 2, out.stderr[-600:])
        self.assertIn("운영 입력", out.stderr + out.stdout)

    def test_last_bar_mismatch_is_refused(self):
        storage, results = make_storage(tempfile.mkdtemp(), years=(2019, 2025))
        bars = snapshot_bars(storage).iloc[:-5]                              # 5일 짧은 운영 입력
        write_operational_pkl(storage, "samsung", "quick", bars)
        out = run_s04(storage, results)
        self.assertEqual(out.returncode, 2, out.stderr[-600:])
        self.assertIn("마지막 봉", out.stderr + out.stdout)

    def test_operational_predictions_align_to_prediction_dates(self):
        import run_weekly_sequence as rws
        storage, _ = make_storage(tempfile.mkdtemp(), years=(2019, 2025))
        bars = snapshot_bars(storage)
        inputs = fake_operational_inputs(bars)
        preds = rws.operational_ridge_predictions(inputs, horizon=5)
        # 특징 행 d-1 → 예측일 = 다음 세션. 결과 인덱스는 예측일이어야 한다.
        self.assertIsInstance(preds.index, pd.DatetimeIndex)
        self.assertTrue((preds.index > bars.index[0]).all())
        self.assertTrue(preds.index.is_monotonic_increasing)
        self.assertGreater(len(preds), 100)


@unittest.skipUnless(HAVE_TORCH, "torch 없음")
class LockAndVerdictTests(unittest.TestCase):
    def prepared(self):
        root = tempfile.mkdtemp()
        storage, results = make_storage(root, years=(2019, 2025))
        write_operational_pkl(storage, "samsung", "quick", snapshot_bars(storage))
        return storage, results

    def test_dev_then_lock_then_verdict_with_all_four_candidates(self):
        storage, results = self.prepared()
        out = run_s04(storage, results)
        self.assertEqual(out.returncode, 0, out.stderr[-1500:])
        run_dir = only_s04_dir(results)
        for name in ("metrics_dev.csv", "comparisons_dev.csv", "metrics_lock.csv", "comparisons_lock.csv", "decision.md"):
            self.assertTrue((run_dir / name).is_file(), name)
        dev = pd.read_csv(run_dir / "metrics_dev.csv")
        models = set(dev[dev["fold"] == "all_used"]["model"])
        self.assertTrue({"persistence", "operational_ridge", "ohlcv_ridge", "tcn_mean"} <= models, models)
        self.assertEqual(dev[dev["fold"] == "all_used"].groupby("model")["n"].first().nunique(), 1)   # 공통 표본
        lock = pd.read_csv(run_dir / "metrics_lock.csv")
        self.assertTrue({"persistence", "operational_ridge", "ohlcv_ridge", "tcn_mean"} <= set(lock["model"]))
        marker = Path(results) / "S04" / "LOCK_OPENED_samsung.json"
        self.assertTrue(marker.is_file())
        payload = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(payload["run_id"], run_dir.name)
        decision = (run_dir / "decision.md").read_text(encoding="utf-8")
        self.assertRegex(decision, r"판정: \*\*(채택 후보|관찰 후보|미채택)\*\*")
        self.assertIn("운영 채택은 별도 결정", decision)
        self.assertIn("공통 예측일", decision)

    def test_second_lock_opening_is_refused_and_first_preserved(self):
        storage, results = self.prepared()
        self.assertEqual(run_s04(storage, results).returncode, 0)
        run_dir = only_s04_dir(results)
        first = (run_dir / "metrics_lock.csv").read_bytes()
        # 다른 설정(alpha)으로 새 run 을 시도해도 잠금은 다시 열리지 않는다.
        out = run_s04(storage, results, "--alpha", "100")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("이미 열렸습니다", out.stderr + out.stdout)
        self.assertEqual((run_dir / "metrics_lock.csv").read_bytes(), first)

    def test_lock_training_rows_mature_before_lock_start(self):
        storage, results = self.prepared()
        self.assertEqual(run_s04(storage, results).returncode, 0)
        run_dir = only_s04_dir(results)
        folds = pd.read_csv(run_dir / "folds.csv")
        lock_row = folds[folds["name"] == "lock"].iloc[0]
        self.assertLess(pd.Timestamp(lock_row["train_maturity_max"]), pd.Timestamp(lock_row["test_start"]))


class VerdictRuleTests(unittest.TestCase):
    def verdict(self, dev_ci_high, lock_ci_high, seed_worse_than_operational):
        import run_weekly_sequence as rws
        dev = {f"tcn_mean_vs_{b}": {"mae_diff": -0.001, "ci_high": dev_ci_high} for b in ("persistence", "operational_ridge", "ohlcv_ridge")}
        lock = {f"tcn_mean_vs_{b}": {"mae_diff": -0.001, "ci_high": lock_ci_high} for b in ("persistence", "operational_ridge", "ohlcv_ridge")}
        seeds = {42: 0.010, 43: 0.010, 44: 0.020 if seed_worse_than_operational else 0.010}
        return rws.verdict(dev, lock, seeds, operational_mae=0.015)

    def test_both_pass_is_candidate(self):
        self.assertEqual(self.verdict(-0.0001, -0.0001, False)[0], "채택 후보")

    def test_dev_only_is_watch(self):
        self.assertEqual(self.verdict(-0.0001, +0.0005, False)[0], "관찰 후보")

    def test_dev_fail_is_reject(self):
        self.assertEqual(self.verdict(+0.0005, -0.0001, False)[0], "미채택")

    def test_any_seed_worse_than_operational_is_reject(self):
        self.assertEqual(self.verdict(-0.0001, -0.0001, True)[0], "미채택")


if __name__ == "__main__":
    unittest.main()

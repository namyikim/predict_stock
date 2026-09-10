# -*- coding: utf-8 -*-
"""실험 러너의 재개·격리 동작.

노트북을 실제로 돌리지 않는다. 러너가 보장해야 할 것은 "무엇을 다시 돌리고 무엇을
건너뛰는가"이므로, 실행 횟수를 세는 가짜 실행기를 넣고 그 횟수를 확인한다.
"""
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_model_improvement as runner  # noqa: E402


class FakeNotebook:
    """호출 횟수를 세는 run_notebook 대역. 최소한의 native_metrics만 돌려준다."""

    def __init__(self):
        self.calls = []

    def __call__(self, storage, targets=None, quick=False, use_cache=False, no_macro=False):
        import pandas as pd
        self.calls.append({"storage": str(storage), "targets": targets, "quick": quick})
        frame = pd.DataFrame(
            {"n": [120, 120], "accuracy": [.45, .44], "balanced_accuracy": [.43, .42],
             "log_loss": [1.03, 1.04], "brier": [.62, .63],
             "auc_gap": [.80, .79], "auc_session": [.50, .49]},
            index=["Mean ensemble", "No macro ensemble"])
        return {targets: {"native_metrics": frame, "TARGET_MODE": "close_to_close"}}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._publish = os.environ.get("PREDICT_STOCK_PUBLISH")
        self.addCleanup(self._restore_publish)

    def _restore_publish(self):
        if self._publish is None:
            os.environ.pop("PREDICT_STOCK_PUBLISH", None)
        else:
            os.environ["PREDICT_STOCK_PUBLISH"] = self._publish

    def _snapshot(self, target, content=b"snapshot-v1"):
        """고정 스냅샷 파일을 만든다. data_hash가 이 내용에서 나온다."""
        cache = self.storage / target / "data_cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "target.parquet").write_bytes(content)
        return cache

    def _run(self, fake, *, target="samsung", mode="quick", resume=False):
        return runner.execute("P00", target, mode, self.storage, resume, run_notebook_fn=fake)

    # ---- 재개 -------------------------------------------------------------
    def test_resume_with_same_inputs_does_not_retrain(self):
        """같은 task·target·데이터·설정이면 완료 단위를 다시 학습하지 않는다."""
        self._snapshot("samsung")
        fake = FakeNotebook()
        first = self._run(fake)
        self.assertEqual(len(fake.calls), 1)

        second = self._run(fake, resume=True)
        self.assertEqual(len(fake.calls), 1, "재개가 완료 단위를 다시 실행했다")
        self.assertEqual(first.run_dir, second.run_dir, "재개는 같은 run_id를 이어야 한다")
        self.assertEqual(second.completed(), ["samsung:baseline"])

    def test_resuming_twice_does_not_duplicate_units(self):
        self._snapshot("samsung")
        fake = FakeNotebook()
        self._run(fake)
        self._run(fake, resume=True)
        state = self._run(fake, resume=True)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(state.completed(), ["samsung:baseline"], "완료 단위가 중복 기록됐다")

    def test_changed_data_starts_a_new_run(self):
        """스냅샷이 바뀌면 옛 체크포인트를 재사용하지 않는다."""
        self._snapshot("samsung")
        fake = FakeNotebook()
        first = self._run(fake)

        self._snapshot("samsung", content=b"snapshot-v2")     # 다른 자료
        second = self._run(fake, resume=True)
        self.assertEqual(len(fake.calls), 2, "자료가 바뀌었는데 재실행하지 않았다")
        self.assertNotEqual(first.run_dir, second.run_dir)
        self.assertNotEqual(first.manifest["data_hash"], second.manifest["data_hash"])

    def test_changed_config_starts_a_new_run(self):
        """설정(mode)이 바뀌면 별도 실행이 된다."""
        self._snapshot("samsung")
        fake = FakeNotebook()
        first = self._run(fake, mode="quick")
        second = self._run(fake, mode="full", resume=True)
        self.assertEqual(len(fake.calls), 2)
        self.assertNotEqual(first.run_dir, second.run_dir)
        self.assertNotEqual(first.manifest["config_hash"], second.manifest["config_hash"])

    def test_targets_are_isolated(self):
        """한 종목의 완료가 다른 종목을 건너뛰게 하지 않는다."""
        self._snapshot("samsung")
        self._snapshot("sk_hynix")
        fake = FakeNotebook()
        self._run(fake, target="samsung")
        self._run(fake, target="sk_hynix", resume=True)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual([c["targets"] for c in fake.calls], ["samsung", "sk_hynix"])

    # ---- 거절 -------------------------------------------------------------
    def test_unregistered_task_is_rejected(self):
        with self.assertRaises(SystemExit) as ctx:
            runner.execute("P99", "samsung", "quick", self.storage, False,
                           run_notebook_fn=FakeNotebook())
        self.assertIn("P99", str(ctx.exception))

    def test_unsupported_target_is_rejected(self):
        with self.assertRaises(SystemExit):
            runner.execute("P00", "apple", "quick", self.storage, False,
                           run_notebook_fn=FakeNotebook())

    # ---- 실패와 손상 -------------------------------------------------------
    def test_failure_is_recorded_and_unit_stays_incomplete(self):
        self._snapshot("samsung")

        def boom(*args, **kwargs):
            raise RuntimeError("셀 27 실패")

        with self.assertRaises(RuntimeError):
            self._run(boom)
        task_dir = self.storage / "P00"
        run_dir = next(task_dir.iterdir())
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(checkpoint["completed_units"], [], "실패한 단위가 완료로 남았다")
        self.assertTrue(checkpoint["failed_units"])

        # 실패 뒤 재개하면 그 단위를 다시 돌린다.
        fake = FakeNotebook()
        state = self._run(fake, resume=True)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(state.manifest["status"], "completed")

    def test_corrupt_checkpoint_is_treated_as_missing(self):
        """반쯤 쓰인 체크포인트를 완료로 착각하지 않는다."""
        self._snapshot("samsung")
        fake = FakeNotebook()
        state = self._run(fake)
        (state.run_dir / "checkpoint.json").write_text("{ 부서진", encoding="utf-8")
        again = self._run(fake, resume=True)
        self.assertEqual(len(fake.calls), 2, "손상된 체크포인트를 완료로 읽었다")
        self.assertEqual(again.completed(), ["samsung:baseline"])

    # ---- 계약 -------------------------------------------------------------
    def test_publishing_is_always_disabled(self):
        self._snapshot("samsung")
        os.environ["PREDICT_STOCK_PUBLISH"] = "true"
        self._run(FakeNotebook())
        self.assertEqual(os.environ["PREDICT_STOCK_PUBLISH"], "false",
                         "실험 러너가 발행을 끄지 않았다")

    def test_manifest_has_required_keys(self):
        self._snapshot("samsung")
        state = self._run(FakeNotebook())
        manifest = json.loads((state.run_dir / "manifest.json").read_text(encoding="utf-8"))
        for key in ("task_id", "run_id", "target", "code_commit", "data_hash", "config_hash",
                    "seed", "mode", "status", "completed_units", "failed_units", "artifact_paths"):
            self.assertIn(key, manifest)
        self.assertEqual(manifest["status"], "completed")

    def test_metrics_csv_uses_contract_fields(self):
        self._snapshot("samsung")
        state = self._run(FakeNotebook())
        import csv
        with open(state.run_dir / "metrics.csv", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2)
        for field in ("target", "model", "target_mode", "fold", "n", "accuracy",
                      "balanced_accuracy", "log_loss", "brier", "auc_gap", "auc_session"):
            self.assertIn(field, rows[0])
        self.assertEqual(rows[0]["target"], "samsung")

    def test_missing_snapshot_hashes_as_nodata(self):
        """스냅샷이 없으면 해시가 nodata다. 자료가 생기면 별도 실행이 된다."""
        fake = FakeNotebook()
        first = self._run(fake)
        self.assertEqual(first.manifest["data_hash"], "nodata")
        self._snapshot("samsung")
        second = self._run(fake, resume=True)
        self.assertEqual(len(fake.calls), 2)
        self.assertNotEqual(first.run_dir, second.run_dir)

    def test_atomic_write_leaves_no_partial_file(self):
        path = self.storage / "sub" / "x.json"
        runner.write_json(path, {"a": 1})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1})
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_config_hash_ignores_key_order(self):
        self.assertEqual(runner.config_hash({"a": 1, "b": 2}), runner.config_hash({"b": 2, "a": 1}))
        self.assertNotEqual(runner.config_hash({"a": 1}), runner.config_hash({"a": 2}))


if __name__ == "__main__":
    unittest.main()

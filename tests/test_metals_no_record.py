# -*- coding: utf-8 -*-
"""금·은 보고서의 --no-record: 3시간 간격 회차는 예측을 원장에 더하지 않고 보고서·채점만 갱신한다(2026-09-16)."""
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import build_metals_report as mr  # noqa: E402


def bars():
    index = pd.to_datetime(["2026-09-14", "2026-09-15", "2026-09-16"])
    return pd.DataFrame({"open": [100., 101., 102.], "high": [101., 102., 103.], "low": [99., 100., 101.],
                         "close": [100.5, 101.5, 102.5], "adj_close": [100.5, 101.5, 102.5], "volume": [1e5] * 3},
                        index=index)


def common(run_id):
    return {"schema_version": 3, "run_id": run_id, "created_at_utc": "2026-09-16T21:30:00+00:00",
            "prediction_date": "2026-09-17", "as_of_date": "2026-09-16", "target_mode": "close_to_close",
            "band": .01, "current_close": 102.5, "config_hash": "x", "data_snapshot_hash": "y",
            "imputed_features": "[]", "macro_snapshot_hash": "", "macro_history_mode": "disabled", "event_flags": ""}


LIVE = {"p_down": .2, "p_flat": .3, "p_up": .5}


class NoRecordTests(unittest.TestCase):
    def test_no_record_keeps_the_ledger_unchanged_but_still_rescores(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            mr.ledger_update(storage, "gold", bars(), "run1", common("run1"), LIVE, [], record=True)
            before = pd.read_csv(storage / "forecast_log.csv")
            self.assertEqual(len(before), 1)
            evaluated, daily, scored = mr.ledger_update(storage, "gold", bars(), "run2", common("run2"), LIVE, [],
                                                        record=False)
            after = pd.read_csv(storage / "forecast_log.csv")
            self.assertEqual(len(after), 1)                       # 두 번째 실행의 예측은 더하지 않았다
            self.assertEqual(after["run_id"].tolist(), ["run1"])
            self.assertEqual(len(evaluated), 1)
            self.assertTrue((storage / "daily_forecast_comparison.csv").exists())
            self.assertTrue((storage / "forecast_accuracy_summary.csv").exists())

    def test_no_record_with_no_ledger_yet_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            evaluated, daily, scored = mr.ledger_update(Path(tmp), "gold", bars(), "run1", common("run1"), LIVE, [],
                                                        record=False)
            self.assertEqual(len(evaluated), 0)
            self.assertEqual(len(scored), 0)

    def test_main_wires_the_flag(self):
        source = (ROOT / "tools" / "build_metals_report.py").read_text(encoding="utf-8")
        self.assertIn('"--no-record"', source)
        self.assertIn("record=not args.no_record", source)


if __name__ == "__main__":
    unittest.main()

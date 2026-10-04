"""기간 분리 검증은 평가 구간의 결과로 전략을 다시 선택하지 않는다."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TradingValidationTests(unittest.TestCase):
    def test_selection_coverage_cost_and_future_isolation(self):
        done = subprocess.run(["node", str(ROOT / "tests/trading_validation_cases.cjs")], cwd=ROOT,
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_cli_records_evidence_fingerprints_without_changing_ledger(self):
        source = ROOT / "forecast_history/samsung/forecast_log.csv"
        before = source.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            done = subprocess.run([sys.executable, str(ROOT / "tools/evaluate_trading.py"),
                                   "--target", "samsung", "--config", str(ROOT / "macro_inputs/trading_validation.json"),
                                   "--out", str(output)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            result = json.loads(output.read_text())
            self.assertFalse(result["automatic_promotion"])
            self.assertEqual(result["method"], "retrospective_split")
            self.assertEqual(len(result["input_sha256"]), 64)
            self.assertEqual(len(result["config_sha256"]), 64)
            self.assertEqual(len(result["engine_sha256"]), 64)
        self.assertEqual(source.read_bytes(), before)

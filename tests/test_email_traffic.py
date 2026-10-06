"""이메일 유입 집계의 Worker/SQLite 통합 계약."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EmailTrafficTests(unittest.TestCase):
    def test_actual_worker_with_sqlite(self):
        result = subprocess.run(['node', 'tests/email_traffic_cases.mjs'], cwd=ROOT,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr[-6000:])

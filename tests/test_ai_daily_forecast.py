import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import ai_daily_forecast as ai


def sample_document(day="2026-09-17"):
    return {
        "schema_version": 1,
        "target_date": day,
        "created_at_kst": f"{day}T08:00:00+09:00",
        "status": "predicted",
        "stocks": {
            "samsung": {
                "ticker": "005930",
                "name": "삼성전자",
                "previous_close": 70000,
                "predicted_open": 70500,
                "predicted_close_direction": "상승",
                "predicted_close": 71500,
                "rationale": ["전일 미국 반도체주 강세"],
                "sources": [{"title": "공개 자료", "url": "https://example.com/source"}],
            },
            "sk_hynix": {
                "ticker": "000660",
                "name": "SK하이닉스",
                "previous_close": 190000,
                "predicted_open": 192000,
                "predicted_close_direction": "하락",
                "predicted_close": 188000,
                "rationale": ["환율 변동성 확대"],
                "sources": [{"title": "공개 자료", "url": "https://example.com/source"}],
            },
        },
    }


class MetricTests(unittest.TestCase):
    def test_direction_is_measured_against_previous_close(self):
        self.assertEqual(ai.actual_direction(71000, 72000), "상승")
        self.assertEqual(ai.actual_direction(71000, 71000), "보합")
        self.assertEqual(ai.actual_direction(71000, 70000), "하락")

    def test_absolute_percentage_error_is_percent(self):
        self.assertAlmostEqual(ai.absolute_percentage_error(102, 100), 2.0)

    def test_zero_actual_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "actual must be positive"):
            ai.absolute_percentage_error(100, 0)


class ValidationTests(unittest.TestCase):
    def test_only_https_sources_are_allowed(self):
        document = sample_document()
        document["stocks"]["samsung"]["sources"][0]["url"] = "javascript:alert(1)"
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            ai.validate_prediction(document)

    def test_both_stocks_are_required(self):
        document = sample_document()
        del document["stocks"]["sk_hynix"]
        with self.assertRaisesRegex(ValueError, "exactly"):
            ai.validate_prediction(document)

    def test_prices_must_be_positive(self):
        document = sample_document()
        document["stocks"]["samsung"]["predicted_close"] = 0
        with self.assertRaisesRegex(ValueError, "positive"):
            ai.validate_prediction(document)


class LedgerTests(unittest.TestCase):
    def test_prediction_must_be_created_before_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "before 09:00 KST"):
                ai.record_prediction(
                    sample_document(),
                    Path(tmp),
                    datetime(2026, 9, 17, 9, 0, tzinfo=ai.KST),
                )

    def test_target_date_must_be_today(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "today"):
                ai.record_prediction(
                    sample_document(),
                    Path(tmp),
                    datetime(2026, 9, 18, 8, 0, tzinfo=ai.KST),
                )

    def test_existing_prediction_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 9, 17, 8, 0, tzinfo=ai.KST)
            ai.record_prediction(sample_document(), root, now)
            with self.assertRaises(FileExistsError):
                ai.record_prediction(sample_document(), root, now)

    def test_scoring_preserves_prediction_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = sample_document()
            ai.record_prediction(
                before, root, datetime(2026, 9, 17, 8, 0, tzinfo=ai.KST)
            )
            ai.score_prediction(
                "2026-09-17",
                {
                    "samsung": {"actual_open": 70400, "actual_close": 71600},
                    "sk_hynix": {"actual_open": 191000, "actual_close": 187000},
                },
                root,
                datetime(2026, 9, 17, 16, 10, tzinfo=ai.KST),
            )
            after = json.loads((root / "2026-09-17.json").read_text(encoding="utf-8"))
            self.assertEqual(after["stocks"]["samsung"]["predicted_open"], 70500)
            self.assertEqual(
                after["stocks"]["samsung"]["rationale"],
                before["stocks"]["samsung"]["rationale"],
            )
            self.assertTrue(after["stocks"]["samsung"]["direction_correct"])
            self.assertTrue(after["stocks"]["sk_hynix"]["direction_correct"])

    def test_index_reports_metrics_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai.record_prediction(
                sample_document(), root, datetime(2026, 9, 17, 8, 0, tzinfo=ai.KST)
            )
            ai.score_prediction(
                "2026-09-17",
                {
                    "samsung": {"actual_open": 70400, "actual_close": 71600},
                    "sk_hynix": {"actual_open": 191000, "actual_close": 187000},
                },
                root,
                datetime(2026, 9, 17, 16, 10, tzinfo=ai.KST),
            )
            index = ai.build_index(
                root, datetime(2026, 9, 17, 16, 11, tzinfo=ai.KST)
            )
            summary = index["summary"]["samsung"]
            self.assertEqual(summary["scored_days"], 1)
            self.assertEqual(summary["direction_accuracy_pct"], 100.0)
            self.assertIn("open_mape_pct", summary)
            self.assertIn("close_mape_pct", summary)
            self.assertNotIn("total_score", summary)

    def test_atomic_write_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai.record_prediction(
                sample_document(), root, datetime(2026, 9, 17, 8, 0, tzinfo=ai.KST)
            )
            self.assertEqual(list(root.glob("*.tmp")), [])


class CliTests(unittest.TestCase):
    def test_empty_index_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = ai.build_index(
                Path(tmp), datetime(2026, 9, 17, 8, 0, tzinfo=ai.KST)
            )
            self.assertEqual(index["schema_version"], 1)
            self.assertEqual(index["records"], [])
            self.assertEqual(index["summary"], {})

    def test_help_exposes_record_score_and_rebuild(self):
        done = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "ai_daily_forecast.py"), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        for command in ("record", "score", "rebuild-index"):
            self.assertIn(command, done.stdout)


class OperationsDocTests(unittest.TestCase):
    def test_runbook_names_times_boundaries_and_commands(self):
        text = (ROOT / "docs" / "ai-daily-forecast-operations.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "08:00 KST",
            "16:10 KST",
            "09:00 KST 이후",
            "전일 종가 대비",
            "record --input",
            "score --date",
            "rebuild-index",
            "forecast_history",
            "거시경제",
            "소급 예측하지",
        ):
            self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()

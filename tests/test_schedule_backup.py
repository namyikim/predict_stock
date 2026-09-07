"""아침 스케줄 이중화: 붐비는 시각을 피하고, 백업은 이미 기록됐으면 건너뛴다."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import should_run_today as srt  # noqa: E402

WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/daily-report.yml").read_text(encoding="utf-8"))
MAIN_CRON = "22 21 * * 0-4"


class ScheduleTests(unittest.TestCase):
    def test_two_morning_schedules_off_the_busy_minutes(self):
        crons = [item["cron"] for item in WORKFLOW[True]["schedule"]]
        self.assertEqual(crons, [MAIN_CRON, "25 22 * * 0-4"])
        for cron in crons:
            minute = int(cron.split()[0])
            self.assertNotIn(minute, (0, 30), "정각·30분은 GitHub cron이 가장 많이 밀리는 지점")

    def test_both_schedules_finish_before_the_market_opens(self):
        # 원장은 09:00 KST 이후 예측을 사전 예측으로 세지 않는다.
        for item in WORKFLOW[True]["schedule"]:
            minute, hour = int(item["cron"].split()[0]), int(item["cron"].split()[1])
            kst_hour = (hour + 9) % 24
            self.assertLess(kst_hour + minute / 60, 8.0, item["cron"])

    def test_only_the_stock_job_runs_on_the_backup_schedule(self):
        jobs = WORKFLOW["jobs"]
        self.assertNotIn("if", jobs["report"])          # 종목 잡은 백업에서도 돈다
        for name in ("trends", "metals", "china", "interest"):
            self.assertIn(MAIN_CRON, jobs[name]["if"], name)

    def test_backup_run_is_gated_on_the_ledger(self):
        steps = {s.get("name"): s for s in WORKFLOW["jobs"]["report"]["steps"]}
        gate = steps["오늘 예측이 이미 기록됐는지 확인"]
        self.assertIn("should_run_today.py", gate["run"])
        self.assertIn("github.event_name == 'schedule'", gate["if"])   # 수동 실행은 막지 않는다
        build = steps["보고서 생성·발행"]
        self.assertIn("steps.recorded.outputs.run != 'false'", build["if"])


class LedgerGateTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "samsung").mkdir()
        self.path = self.dir / "samsung" / "forecast_log.csv"
        self.today = datetime.now(timezone(timedelta(hours=9))).date().isoformat()

    def write(self, rows):
        pd.DataFrame(rows).to_csv(self.path, index=False)

    def test_skips_when_todays_prospective_forecast_exists(self):
        self.write([{"prediction_date": self.today, "is_prospective": True,
                     "kind": "direction", "run_id": "r1"}])
        recorded, reason = srt.already_recorded(self.path, self.today)
        self.assertTrue(recorded)
        self.assertIn("r1", reason)

    def test_runs_when_todays_record_is_not_prospective(self):
        # 09:00 이후에 만들어진 예측은 집계되지 않으므로 다시 돌아야 한다.
        self.write([{"prediction_date": self.today, "is_prospective": False,
                     "kind": "direction", "run_id": "late"}])
        self.assertFalse(srt.already_recorded(self.path, self.today)[0])

    def test_runs_when_only_older_dates_are_recorded(self):
        self.write([{"prediction_date": "2020-01-02", "is_prospective": True,
                     "kind": "direction", "run_id": "old"}])
        self.assertFalse(srt.already_recorded(self.path, self.today)[0])

    def test_runs_when_the_ledger_is_missing(self):
        recorded, reason = srt.already_recorded(self.dir / "nope.csv", self.today)
        self.assertFalse(recorded)
        self.assertIn("없습니다", reason)

    def test_writes_the_github_output(self):
        self.write([{"prediction_date": self.today, "is_prospective": True,
                     "kind": "direction", "run_id": "r1"}])
        out = self.dir / "out.txt"
        saved = (os.environ.get("GITHUB_OUTPUT"), sys.argv)
        os.environ["GITHUB_OUTPUT"] = str(out)
        sys.argv = ["x", "--target", "samsung", "--ledger-root", str(self.dir)]
        try:
            srt.main()
        finally:
            sys.argv = saved[1]
            if saved[0] is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = saved[0]
        self.assertIn("run=false", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

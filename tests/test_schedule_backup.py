"""아침 스케줄 이중화: 붐비는 시각을 피하고, 백업은 이미 기록됐으면 건너뛴다."""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import should_run_today as srt  # noqa: E402
import should_run_trends as trends_gate  # noqa: E402

WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/daily-report.yml").read_text(encoding="utf-8"))
MAIN_CRON = "22 21 * * 0-4"
THREE_HOUR_CRON = "22 0,3,6,9,12,15,18 * * *"
RETRY_CRONS = [
    "37 0,3,6,9,12,15,18 * * *",
    "52 0,3,6,9,12,15,18 * * *",
]


class ScheduleTests(unittest.TestCase):
    def test_schedules_cover_the_morning_twice_and_every_three_hours(self):
        crons = [item["cron"] for item in WORKFLOW[True]["schedule"]]
        self.assertEqual(crons, [MAIN_CRON, "25 22 * * 0-4", THREE_HOUR_CRON, *RETRY_CRONS])
        for cron in crons:
            minute = int(cron.split()[0])
            self.assertNotIn(minute, (0, 30), "정각·30분은 GitHub cron이 가장 많이 밀리는 지점")
        # 3시간 간격 회차가 하루를 고르게 덮는지
        for cron in crons[2:]:
            hours = sorted(int(h) for h in cron.split()[1].split(","))
            self.assertEqual(hours, [0, 3, 6, 9, 12, 15, 18])

    def test_run_name_distinguishes_automatic_retry_and_manual_runs(self):
        run_name = WORKFLOW["run-name"]
        for label in ("자동 정규", "자동 재시도", "수동", "코드 반영"):
            self.assertIn(label, run_name)
        self.assertIn(THREE_HOUR_CRON, run_name)

    def test_duplicate_gate_runs_before_python_setup_and_heavy_install(self):
        steps = WORKFLOW["jobs"]["report"]["steps"]
        by_name = {step.get("name", step.get("uses")): step for step in steps}
        gate_index = next(i for i, step in enumerate(steps)
                          if step.get("name") == "오늘 예측이 이미 기록됐는지 확인")
        setup_index = next(i for i, step in enumerate(steps)
                           if step.get("uses") == "actions/setup-python@v5")
        install_index = next(i for i, step in enumerate(steps)
                             if step.get("name") == "의존성 설치")
        self.assertLess(gate_index, setup_index)
        self.assertLess(gate_index, install_index)
        for step in (steps[setup_index], by_name["의존성 설치"]):
            self.assertIn("steps.recorded.outputs.run != 'false'", step["if"])

    def test_duplicate_gate_uses_only_python_standard_library(self):
        source = (ROOT / "tools/should_run_today.py").read_text(encoding="utf-8")
        self.assertNotIn("import pandas", source)

    def test_morning_schedules_finish_before_the_market_opens(self):
        # 원장은 09:00 KST 이후 예측을 사전 예측으로 세지 않는다. 아침 두 회차가 그 전이어야 한다.
        for item in WORKFLOW[True]["schedule"][:2]:
            minute, hour = int(item["cron"].split()[0]), int(item["cron"].split()[1])
            kst_hour = (hour + 9) % 24
            self.assertLess(kst_hour + minute / 60, 8.0, item["cron"])

    def test_heavy_side_reports_run_once_a_day(self):
        jobs = WORKFLOW["jobs"]
        self.assertNotIn("if", jobs["report"])          # 종목 잡은 모든 회차에서 게이트가 판단한다
        for name in ("metals", "china", "interest", "earnings"):
            self.assertIn(f"== '{MAIN_CRON}'", jobs[name]["if"], name)
        # 검색어만 3시간 간격 회차에도 돈다(하루 사이에 실제로 바뀌는 유일한 보고서).
        self.assertIn("!= '25 22 * * 0-4'", jobs["trends"]["if"])

    def test_backup_run_is_gated_on_the_ledger(self):
        steps = {s.get("name"): s for s in WORKFLOW["jobs"]["report"]["steps"]}
        gate = steps["오늘 예측이 이미 기록됐는지 확인"]
        self.assertIn("should_run_today.py", gate["run"])
        self.assertIn("github.event_name == 'schedule'", gate["if"])   # 수동 실행은 막지 않는다
        build = steps["보고서 생성·발행"]
        self.assertIn("steps.recorded.outputs.run != 'false'", build["if"])


class PushTriggerTests(unittest.TestCase):
    """코드가 바뀌면 바로 다시 만들되, 원장은 건드리지 않는다."""

    def test_push_is_limited_to_code_paths(self):
        push = WORKFLOW[True]["push"]
        self.assertEqual(push["branches"], ["main"])
        for path in ("samsung_direction_model_colab.ipynb", "tools/**", "forecast_utils.py"):
            self.assertIn(path, push["paths"])
        # 자기 자신이 만든 커밋(보고서·원장)이 다시 실행을 부르면 안 된다.
        for path in push["paths"]:
            self.assertFalse(path.startswith(("docs/", "forecast_history/", "runs/")), path)

    def test_push_runs_do_not_record_a_forecast(self):
        steps = {s.get("name"): s for s in WORKFLOW["jobs"]["report"]["steps"]}
        record = steps["보고서 생성·발행"]["env"]["PREDICT_STOCK_RECORD_FORECAST"]
        self.assertIn("github.event_name == 'push'", record)
        self.assertIn("'false'", record)


class ScoringScheduleTests(unittest.TestCase):
    """개장 후·마감 후 두 번 채점한다."""

    def setUp(self):
        self.wf = yaml.safe_load(
            (ROOT / ".github/workflows/afternoon-report.yml").read_text(encoding="utf-8"))

    def test_two_scoring_schedules(self):
        crons = [item["cron"] for item in self.wf[True]["schedule"]]
        self.assertEqual(crons, ["37 0 * * 1-5", "10 7 * * 1-5"])   # 09:37 / 16:10 KST

    def test_morning_run_scores_only_the_open(self):
        step = {s.get("name"): s for s in self.wf["jobs"]["report"]["steps"]}["채점·보고서 절 갱신"]
        self.assertIn("37 0 * * 1-5", step["env"]["SCOPE"])
        self.assertIn("'open'", step["env"]["SCOPE"])
        self.assertIn("--scope", step["run"])


class LedgerGateTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "samsung").mkdir()
        self.path = self.dir / "samsung" / "forecast_log.csv"
        # 시각을 고정한다. 게이트는 09:00 전후로 동작이 다르고 예측 대상 거래일도 달라지므로,
        # 실제 시계를 쓰면 CI 가 언제 도느냐에 따라 결과가 뒤집힌다(실제로 그랬다).
        self.now = datetime(2026, 9, 9, 6, 30, tzinfo=timezone(timedelta(hours=9)))   # 수요일 아침
        self.today = str(srt.next_trading_day(self.now))

    def write(self, rows):
        pd.DataFrame(rows).to_csv(self.path, index=False)

    def test_skips_when_todays_prospective_forecast_exists(self):
        self.write([{"prediction_date": self.today, "is_prospective": True,
                     "kind": "direction", "run_id": "r1"}])
        recorded, reason = srt.already_recorded(self.path, self.today, now=self.now)
        self.assertTrue(recorded)
        self.assertIn("r1", reason)

    def test_runs_when_todays_record_is_not_prospective(self):
        # 아침에는 09:00 이후에 만들어진 옛 기록을 인정하지 않는다 — 아직 제대로 만들 시간이 있다.
        morning = datetime(2026, 9, 9, 6, 30, tzinfo=timezone(timedelta(hours=9)))
        self.write([{"prediction_date": str(srt.next_trading_day(morning)), "is_prospective": False,
                     "kind": "direction", "run_id": "late"}])
        self.assertFalse(srt.already_recorded(self.path, "무시됨", now=morning)[0])

    def test_runs_when_only_older_dates_are_recorded(self):
        self.write([{"prediction_date": "2020-01-02", "is_prospective": True,
                     "kind": "direction", "run_id": "old"}])
        self.assertFalse(srt.already_recorded(self.path, self.today, now=self.now)[0])

    def test_runs_when_the_ledger_is_missing(self):
        recorded, reason = srt.already_recorded(self.dir / "nope.csv", self.today, now=self.now)
        self.assertFalse(recorded)
        self.assertIn("없습니다", reason)

    def test_after_the_open_any_record_counts_as_done(self):
        # 09:00 이후에는 무엇을 만들어도 사전 예측이 될 수 없다. 그 거래일 기록이 하나라도 있으면
        # 넘어가야 3시간마다 전체 재계산이 반복되지 않는다.
        # 수요일 09:00 이후의 대상 거래일은 목요일이다.
        morning = datetime(2026, 9, 9, 6, 30, tzinfo=timezone(timedelta(hours=9)))
        afternoon = datetime(2026, 9, 9, 15, 30, tzinfo=timezone(timedelta(hours=9)))
        self.write([{"prediction_date": str(srt.next_trading_day(afternoon)),
                     "is_prospective": False, "kind": "direction", "run_id": "late"}])
        self.assertTrue(srt.already_recorded(self.path, "무시됨", now=afternoon)[0])
        # 아침에는 그 거래일의 '사전' 예측이어야 인정한다.
        self.write([{"prediction_date": str(srt.next_trading_day(morning)),
                     "is_prospective": False, "kind": "direction", "run_id": "late"}])
        self.assertFalse(srt.already_recorded(self.path, "무시됨", now=morning)[0])

    def test_weekend_runs_do_not_duplicate_mondays_forecast(self):
        # 주말 실행의 예측일은 다음 개장일이다. 오늘 날짜로 비교하면 못 찾아 계속 다시 만든다
        # (2026-09-07 예측일에 8건이 쌓였다).
        saturday = datetime(2026, 9, 12, 12, 0, tzinfo=timezone(timedelta(hours=9)))
        monday = srt.next_trading_day(saturday)
        self.assertEqual(str(monday), "2026-09-14")
        self.write([{"prediction_date": str(monday), "is_prospective": True,
                     "kind": "direction", "run_id": "friday"}])
        self.assertTrue(srt.already_recorded(self.path, "무시됨", now=saturday)[0])

    def test_writes_the_github_output(self):
        self.write([{"prediction_date": self.today, "is_prospective": True,
                     "kind": "direction", "run_id": "r1"}])
        out = self.dir / "out.txt"
        saved = (os.environ.get("GITHUB_OUTPUT"), sys.argv, srt.datetime)
        os.environ["GITHUB_OUTPUT"] = str(out)
        sys.argv = ["x", "--target", "samsung", "--ledger-root", str(self.dir)]

        fixed = self.now

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed
        srt.datetime = FrozenDatetime      # main() 이 실제 시계를 보지 않게 한다
        try:
            srt.main()
        finally:
            srt.datetime = saved[2]
            sys.argv = saved[1]
            if saved[0] is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = saved[0]
        self.assertIn("run=false", out.read_text(encoding="utf-8"))


class TrendsRetryGateTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "index.html"
        self.kst = timezone(timedelta(hours=9))

    def write_generated_at(self, value):
        self.path.write_text(
            f'<div>생성 {value:%Y-%m-%d %H:%M} KST · 출처</div>', encoding="utf-8")

    def test_retry_runs_when_last_report_is_three_hours_old(self):
        now = datetime(2026, 9, 8, 18, 37, tzinfo=self.kst)
        self.write_generated_at(now - timedelta(hours=3, minutes=15))
        due, _ = trends_gate.should_run(self.path, now=now)
        self.assertTrue(due)

    def test_retry_skips_when_primary_just_published(self):
        now = datetime(2026, 9, 8, 18, 37, tzinfo=self.kst)
        self.write_generated_at(now - timedelta(minutes=15))
        due, _ = trends_gate.should_run(self.path, now=now)
        self.assertFalse(due)

    def test_missing_report_runs(self):
        due, reason = trends_gate.should_run(self.dir / "missing.html")
        self.assertTrue(due)
        self.assertIn("없습니다", reason)

    def test_workflow_gates_trends_publication_and_pages(self):
        steps = WORKFLOW["jobs"]["trends"]["steps"]
        gate = next(step for step in steps if step.get("id") == "trends_due")
        self.assertIn("should_run_trends.py", gate["run"])
        for name in ("인기 급상승 검색어 보고서", "Pages 재빌드 요청"):
            step = next(step for step in steps if step.get("name") == name)
            self.assertIn("steps.trends_due.outputs.run == 'true'", step["if"])


if __name__ == "__main__":
    unittest.main()


class WatchdogTests(unittest.TestCase):
    """자동 실행이 조용히 멈추면 알려야 한다. 결과(원장)만 보고 판단한다."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import check_ledger_health
        self.check = check_ledger_health
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "samsung").mkdir()
        self.path = self.dir / "samsung" / "forecast_log.csv"

    def write(self, prediction_date, prospective=True):
        pd.DataFrame([{"prediction_date": prediction_date, "is_prospective": prospective,
                       "kind": "direction", "run_id": "r"}]).to_csv(self.path, index=False)

    def test_latest_trading_day_is_healthy(self):
        # 금요일 예측이 있고 토요일에 점검하면 정상이다(마지막 거래일 = 금요일).
        self.write("2026-09-11")
        ok, message = self.check.check_target("samsung", date(2026, 9, 12), self.dir)
        self.assertTrue(ok)
        self.assertIn("2026-09-11", message)

    def test_a_weekend_or_holiday_is_not_a_failure(self):
        self.write("2026-09-11")
        for day in (date(2026, 9, 12), date(2026, 9, 13)):    # 토·일
            self.assertTrue(self.check.check_target("samsung", day, self.dir)[0], day)

    def test_missing_a_trading_day_fails_immediately(self):
        # 하루만 밀려도 잡아야 한다. '4일 허용'은 평일 연속 실패를 며칠 놓쳤다.
        self.write("2026-09-10")
        ok, message = self.check.check_target("samsung", date(2026, 9, 11), self.dir)
        self.assertFalse(ok)
        self.assertIn("거래일 1회 누락", message)

    def test_missing_ledger_fails(self):
        self.assertFalse(self.check.check_target("sk_hynix", date(2026, 9, 11), self.dir)[0])

    def test_watchdog_runs_after_the_market_opens(self):
        import yaml
        workflow = yaml.safe_load((ROOT / ".github/workflows/watchdog.yml").read_text(encoding="utf-8"))
        cron = workflow[True]["schedule"][0]["cron"]
        minute, hour = int(cron.split()[0]), int(cron.split()[1])
        kst = (hour + 9) % 24 + minute / 60
        self.assertGreater(kst, 9.0, "09:00 이후여야 그날 사전 예측 기회가 끝난 뒤다")
        self.assertLess(kst, 12.0)


class CiAndPathTests(unittest.TestCase):
    """코드가 바뀌면 테스트가 먼저 돌아야 하고, 새로 나눈 파일도 재생성 대상이어야 한다."""

    def test_ci_workflow_runs_the_suite(self):
        import yaml
        workflow = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8"))
        self.assertIn("push", workflow[True])
        self.assertIn("pull_request", workflow[True])
        run = "\n".join(str(s.get("run", "")) for s in workflow["jobs"]["test"]["steps"])
        self.assertIn("unittest discover -s tests", run)

    def test_push_paths_cover_the_split_modules(self):
        paths = WORKFLOW[True]["push"]["paths"]
        for path in ("data_sources/**", "report_html.py", "forecast_utils.py", "tools/**",
                     "samsung_direction_model_colab.ipynb"):
            self.assertIn(path, paths, f"{path} 를 고쳐도 보고서가 다시 만들어지지 않습니다")


class GateDependencyTests(unittest.TestCase):
    """게이트는 3시간마다 두 종목에서 돈다. 무거운 의존성 없이 몇 초에 끝나야 한다."""

    def test_next_trading_day_needs_no_pandas(self):
        source = (ROOT / "tools" / "should_run_today.py").read_text(encoding="utf-8")
        self.assertNotIn("import pandas", source)
        # 달력은 선택이다 — 없으면 주말 규칙으로 넘어간다.
        self.assertIn("except Exception:", source)

    def test_falls_back_to_weekday_rule_without_the_calendar(self):
        import builtins
        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "exchange_calendars":
                raise ImportError("차단")
            return real_import(name, *args, **kwargs)
        builtins.__import__ = blocked
        try:
            saturday = datetime(2026, 9, 12, 12, 0, tzinfo=timezone(timedelta(hours=9)))
            self.assertEqual(str(srt.next_trading_day(saturday)), "2026-09-14")
        finally:
            builtins.__import__ = real_import

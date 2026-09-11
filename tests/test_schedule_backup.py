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

    def test_duplicate_gate_installs_calendar_before_check_and_heavy_deps_after(self):
        steps = WORKFLOW["jobs"]["report"]["steps"]
        by_name = {step.get("name", step.get("uses")): step for step in steps}
        gate_index = next(i for i, step in enumerate(steps)
                          if step.get("name") == "오늘 예측이 이미 기록됐는지 확인")
        setup_index = next(i for i, step in enumerate(steps)
                           if step.get("uses") == "actions/setup-python@v5")
        calendar_index = next(i for i, step in enumerate(steps)
                              if step.get("name") == "거래일 달력 설치")
        install_index = next(i for i, step in enumerate(steps)
                             if step.get("name") == "의존성 설치")
        self.assertLess(setup_index, gate_index)
        self.assertLess(calendar_index, gate_index)
        self.assertLess(gate_index, install_index)
        self.assertIn("github.event_name == 'schedule'", steps[calendar_index]["if"])
        self.assertIn("exchange_calendars==4.13.2", steps[calendar_index]["run"])
        self.assertIn("steps.recorded.outputs.run != 'false'", by_name["의존성 설치"]["if"])

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
        self.assertIn("needs.validation.result", jobs["report"]["if"])
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
    """개장 후·마감 후 채점. 범위는 cron 문자열이 아니라 실행 시각으로 정한다.

    GitHub cron이 이 저장소에서 4~5시간 밀린다(09:37 회차가 14:10, 16:10 회차가 21:10 — 2026-09-08~10).
    그래서 정시 호출은 Cloudflare Worker가 맡고(counter/worker.js), GitHub cron은 백업으로 여러 개 건다.
    """

    def setUp(self):
        self.wf = yaml.safe_load(
            (ROOT / ".github/workflows/afternoon-report.yml").read_text(encoding="utf-8"))
        self.steps = {s.get("name", s.get("uses")): s for s in self.wf["jobs"]["report"]["steps"]}

    def test_schedules_cover_open_and_close_with_after_close_backups(self):
        crons = [item["cron"] for item in self.wf[True]["schedule"]]
        self.assertEqual(crons[:2], ["37 0 * * 1-5", "10 7 * * 1-5"])   # 09:37 / 16:10 KST 정시 회차
        self.assertGreaterEqual(len(crons), 4, "마감 후 백업 회차가 있어야 한다")
        for cron in crons:
            minute, hour, _, _, dow = cron.split()
            self.assertNotIn(int(minute), (0, 30), "정각·30분은 GitHub cron이 가장 많이 밀리는 지점")
            self.assertEqual(dow, "1-5")
        for cron in crons[2:]:
            minute, hour = int(cron.split()[0]), int(cron.split()[1])
            kst = (hour + 9) % 24 + minute / 60
            self.assertGreaterEqual(kst, 15 + 40 / 60, f"{cron}: 백업은 당일 봉이 확정되는 15:40 KST 뒤여야 한다")

    def test_scope_is_decided_by_the_gate_not_the_cron_string(self):
        gate = self.steps["이번 실행이 할 일 결정"]
        self.assertEqual(gate["id"], "gate")
        self.assertIn("should_score_now.py", gate["run"])
        self.assertIn("--event", gate["run"])
        score = self.steps["채점·보고서 절 갱신"]
        self.assertEqual(score["env"]["SCOPE"], "${{ steps.gate.outputs.scope }}")
        self.assertIn("steps.gate.outputs.score == 'true'", score["if"])
        self.assertNotIn("github.event.schedule", str(score))   # cron 문자열로 범위를 정하지 않는다
        review = self.steps["장 마감 회고"]
        self.assertIn("steps.gate.outputs.review == 'true'", review["if"])
        self.assertNotIn("github.event.schedule", str(review))
        self.assertIn('--date "$SESSION"', review["run"])
        self.assertEqual(review["env"]["SESSION"], "${{ steps.gate.outputs.session }}")

    def test_gate_runs_before_heavy_dependencies(self):
        names = [s.get("name") for s in self.wf["jobs"]["report"]["steps"]]
        self.assertLess(names.index("거래일 달력 설치"), names.index("이번 실행이 할 일 결정"))
        self.assertLess(names.index("이번 실행이 할 일 결정"), names.index("의존성 설치"))
        self.assertIn("steps.gate.outputs.run == 'true'", self.steps["의존성 설치"]["if"])

    def test_dispatch_inputs_default_to_auto_scope_and_name_the_caller(self):
        inputs = self.wf[True]["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["scope"]["default"], "auto")
        self.assertIn("auto", inputs["scope"]["options"])
        self.assertIn("caller", inputs)          # Cloudflare cron이 'cloudflare-cron' 을 넘긴다
        self.assertIn("inputs.caller", self.wf["run-name"])

    def test_gate_uses_only_the_standard_library(self):
        source = (ROOT / "tools/should_score_now.py").read_text(encoding="utf-8")
        self.assertNotIn("import pandas", source)
        self.assertIn("except Exception:", source)   # 달력은 선택 — 없으면 주말 규칙


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

    def test_intraday_run_still_checks_the_current_trading_day(self):
        # 모델은 15:40 전에는 미완성인 당일 봉을 버리고 오늘을 예측한다. 게이트도 같은 날짜를
        # 찾아야 아침 예측이 있는데 09:22·12:22·15:22에 다시 계산하지 않는다.
        morning = datetime(2026, 9, 9, 6, 30, tzinfo=timezone(timedelta(hours=9)))
        afternoon = datetime(2026, 9, 9, 15, 30, tzinfo=timezone(timedelta(hours=9)))
        self.assertEqual(srt.next_trading_day(afternoon), date(2026, 9, 9))
        self.write([{"prediction_date": "2026-09-09",
                     "is_prospective": True, "kind": "direction", "run_id": "morning"}])
        self.assertTrue(srt.already_recorded(self.path, "무시됨", now=afternoon)[0])
        # 다음 거래일은 당일 봉이 확정된 뒤에만 대상이 된다.
        after_close = datetime(2026, 9, 9, 15, 40, tzinfo=timezone(timedelta(hours=9)))
        self.assertEqual(srt.next_trading_day(after_close), date(2026, 9, 10))
        # 마감 전에는 그 거래일의 '사전' 예측이어야 인정한다.
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

    def test_krx_holiday_uses_the_next_open_session(self):
        holiday = datetime(2026, 5, 5, 6, 30, tzinfo=timezone(timedelta(hours=9)))
        self.assertEqual(srt.next_trading_day(holiday), date(2026, 5, 6))

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

    def test_krx_weekday_holiday_uses_previous_session(self):
        self.write("2026-05-04")
        ok, message = self.check.check_target("samsung", date(2026, 5, 5), self.dir)
        self.assertTrue(ok, message)
        self.assertIn("2026-05-04", message)

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

    def test_watchdog_installs_the_krx_calendar(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/watchdog.yml").read_text(encoding="utf-8"))
        commands = "\n".join(str(step.get("run", "")) for step in workflow["jobs"]["check"]["steps"])
        self.assertIn("exchange_calendars==4.13.2", commands)


class CiAndPathTests(unittest.TestCase):
    """코드가 바뀌면 테스트가 먼저 돌아야 하고, 새로 나눈 파일도 재생성 대상이어야 한다."""

    def test_ci_workflow_runs_the_suite(self):
        import yaml
        workflow = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8"))
        self.assertIn("push", workflow[True])
        self.assertIn("pull_request", workflow[True])
        self.assertIn("workflow_call", workflow[True])
        run = "\n".join(str(s.get("run", "")) for s in workflow["jobs"]["test"]["steps"])
        self.assertIn("unittest discover -s tests", run)

    def test_code_push_publication_needs_successful_tests(self):
        jobs = WORKFLOW["jobs"]
        validation = jobs["validation"]
        self.assertEqual(validation["uses"], "./.github/workflows/tests.yml")
        self.assertIn("github.event_name == 'push'", validation["if"])
        self.assertEqual(jobs["report"]["needs"], "validation")
        self.assertIn("needs.validation.result == 'success'", jobs["report"]["if"])
        self.assertIn("needs.validation.result == 'skipped'", jobs["report"]["if"])

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


class WorkflowSecretTests(unittest.TestCase):
    """노트북이 읽는 키가 워크플로에서 실제로 전달되는지.

    2026-09-09에 DART_API_KEY 가 earnings 잡에만 있고 보고서를 만드는 report 잡에는 없어,
    키가 등록돼 있는데도 보고서에 '공시 목록 없음 — DART_API_KEY 없음'이 떴다. 키를 새로 쓰기
    시작할 때 워크플로에 넣는 것을 잊기 쉬우므로 테스트로 묶는다.
    """

    NOTEBOOK_SECRETS = {"KOSIS_API_KEY", "ECOS_API_KEY", "DART_API_KEY", "KRX_ID", "KRX_PW"}

    def notebook_calls(self):
        import json
        import re
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        return "\n".join("".join(c["source"]) for c in nb["cells"])

    def test_notebook_still_uses_these_sources(self):
        source = self.notebook_calls()
        for name in ("load_macro_data(", "load_nsi(", "load_investor_flows(", "fetch_dart_disclosures("):
            self.assertIn(name, source, f"{name} 호출이 사라졌다면 이 테스트의 키 목록도 줄여야 합니다")

    def test_every_notebook_workflow_passes_them(self):
        import yaml
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
            for job_name, job in (workflow.get("jobs") or {}).items():
                for step in job.get("steps", []):
                    if "run_notebook.py" not in str(step.get("run", "")):
                        continue
                    given = set(step.get("env", {}) or {})
                    missing = sorted(self.NOTEBOOK_SECRETS - given)
                    self.assertEqual(missing, [], f"{path.name}:{job_name} 에 {missing} 가 없습니다")


class StaleCheckoutTests(unittest.TestCase):
    """게이트는 작업 트리가 아니라 원격의 현재 내용을 봐야 한다.

    :22·:37·:52 재시도 실행은 거의 같은 시각에 만들어져 각자 '서로가 발행하기 전'의 커밋을
    체크아웃한다. 작업 트리를 보면 셋 다 게이트를 통과해 같은 일을 세 번 한다
    (2026-09-09: 종목 보고서 23회, 검색어 23회를 24시간에 다시 만들었다).
    """

    def test_ledger_gate_reads_the_remote_ref(self):
        source = (ROOT / "tools" / "should_run_today.py").read_text(encoding="utf-8")
        self.assertIn("def published_ledger(", source)
        self.assertIn('"git", "show", f"{ref}:{path}"', source)
        self.assertIn('parser.add_argument("--ref", default="origin/main"', source)

    def test_trends_gate_reads_the_remote_ref(self):
        source = (ROOT / "tools" / "should_run_trends.py").read_text(encoding="utf-8")
        self.assertIn("def published_text(", source)
        self.assertIn('"git", "show", f"{ref}:{path}"', source)

    def test_both_fall_back_to_the_working_tree(self):
        # 원격을 못 읽어도 게이트가 죽으면 안 된다(로컬 실행·첫 실행).
        import csv
        import should_run_today as gate
        import should_run_trends as trends_gate
        root = Path(tempfile.mkdtemp())
        (root / "samsung").mkdir()
        path = root / "samsung" / "forecast_log.csv"
        now = datetime(2026, 9, 9, 6, 30, tzinfo=timezone(timedelta(hours=9)))
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["prediction_date", "is_prospective",
                                                        "kind", "run_id"])
            writer.writeheader()
            writer.writerow({"prediction_date": str(gate.next_trading_day(now)),
                             "is_prospective": "True", "kind": "direction", "run_id": "r1"})
        # 존재하지 않는 ref → 작업 트리로 물러서서 정상 판정
        self.assertTrue(gate.already_recorded(path, "x", now=now, ref="origin/no-such-branch")[0])

        page = root / "index.html"
        page.write_text("생성 2026-09-09 12:59 KST", encoding="utf-8")
        recent = datetime(2026, 9, 9, 13, 25, tzinfo=timezone(timedelta(hours=9)))
        self.assertFalse(trends_gate.should_run(page, now=recent, ref="origin/no-such-branch")[0])


class GateFetchSafetyTests(unittest.TestCase):
    """게이트의 git fetch 에 --depth 를 주면 전체 복제본을 얕은 이력으로 만들어 버린다."""

    def test_no_shallow_fetch_in_gates(self):
        import ast
        for name in ("should_run_today.py", "should_run_trends.py"):
            tree = ast.parse((ROOT / "tools" / name).read_text(encoding="utf-8"))
            fetch_calls = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.List):
                    literals = [e.value for e in node.args[0].elts if isinstance(e, ast.Constant)]
                    if literals[:2] == ["git", "fetch"]:
                        fetch_calls.append(literals)
            self.assertTrue(fetch_calls, f"{name}: git fetch 호출이 없습니다")
            for call in fetch_calls:
                self.assertFalse(any(str(a).startswith("--depth") for a in call),
                                 f"{name}: {call} — --depth 는 전체 복제본을 얕게 만듭니다")


class RecordWindowTests(unittest.TestCase):
    """사전 예측은 밤사이 미국 시장 정보를 포함해야 한다.

    2026-09 실제 사고: 3시간 회차 중 한국 저녁 회차가 다음 거래일 예측을 대표 모델로 먼저 기록해
    버려, 정보가 많은 아침 06:22 회차가 게이트에 걸려 건너뛰어졌다. 예측일 4건이 모두 저녁에
    기록됐다 — 갭 AUC 0.80 인 모델에서 갭 정보를 뺀 예측이 원장에 박힌 것이다.
    """

    def setUp(self):
        import csv
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "s").mkdir()
        self.path = self.dir / "s" / "forecast_log.csv"
        self.csv = csv
        self.write([])

    def write(self, rows):
        with open(self.path, "w", newline="", encoding="utf-8") as handle:
            writer = self.csv.DictWriter(handle, fieldnames=["prediction_date", "is_prospective",
                                                             "kind", "model", "run_id"])
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def at(self, hour, minute=22):
        return datetime(2026, 9, 14, hour, minute, tzinfo=timezone(timedelta(hours=9)))

    def evening_row(self, date="2026-09-15"):
        return {"prediction_date": date, "is_prospective": "True", "kind": "direction",
                "model": srt.EVENING_MODEL, "run_id": "e"}

    def morning_row(self, date="2026-09-14"):
        return {"prediction_date": date, "is_prospective": "True", "kind": "direction",
                "model": "No macro ensemble", "run_id": "m"}

    def test_runs_in_the_morning_window(self):
        for hour in (6, 7, 8):
            self.assertFalse(srt.already_recorded(self.path, "x", now=self.at(hour), ref=None)[0], hour)

    def test_skips_outside_both_windows(self):
        for hour in (3, 9, 12, 15):
            skip, why = srt.already_recorded(self.path, "x", now=self.at(hour), ref=None)
            self.assertTrue(skip, hour)
            self.assertIn("기록 시간대가 아닙니다", why)

    def test_evening_candidate_does_not_block_the_morning_run(self):
        # 이것이 실제 사고의 핵심이다. 저녁 기록이 있어도 아침은 반드시 돌아야 한다.
        self.write([self.evening_row("2026-09-14")])
        self.assertFalse(srt.already_recorded(self.path, "x", now=self.at(6), ref=None)[0])

    def test_morning_record_blocks_the_morning_rerun(self):
        self.write([self.morning_row()])
        skip, why = srt.already_recorded(self.path, "x", now=self.at(6), ref=None)
        self.assertTrue(skip)
        self.assertIn("사전 예측이 이미 있습니다", why)

    def test_second_evening_slot_skips_once_the_candidate_exists(self):
        self.write([self.evening_row()])
        self.assertTrue(srt.already_recorded(self.path, "x", now=self.at(18), ref=None)[0])
        self.assertTrue(srt.already_recorded(self.path, "x", now=self.at(21), ref=None)[0])

    def test_morning_record_does_not_block_the_evening_run(self):
        self.write([self.morning_row()])
        self.assertFalse(srt.already_recorded(self.path, "x", now=self.at(18), ref=None)[0])

    def test_notebook_windows_match_the_gate(self):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        source = "\n".join("".join(c["source"]) for c in nb["cells"])
        self.assertIn(f"RECORD_WINDOW_KST = {srt.RECORD_WINDOW_KST}", source)
        self.assertIn(f"EVENING_WINDOW_KST = {srt.EVENING_WINDOW_KST}", source)
        self.assertIn(f'EVENING_MODEL = "{srt.EVENING_MODEL}"', source)

    def test_evening_run_records_only_the_candidate(self):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        source = "\n".join("".join(c["source"]) for c in nb["cells"])
        self.assertIn("if RECORD_EVENING_ONLY:", source)
        self.assertIn('"model": EVENING_MODEL', source)
        # 저녁 실행은 가격·시초가·후보 구간을 기록하지 않는다(대표 경로와 섞이면 집계가 흐려진다).
        self.assertIn("if not RECORD_EVENING_ONLY else []", source)
        self.assertIn("if not RECORD_EVENING_ONLY:\n    log_records.append", source)

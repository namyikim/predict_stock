# -*- coding: utf-8 -*-
"""관세청 10일 단위 잠정치 발표를 감지해 장기 전망·실적 예상을 다시 만들지 정하는 게이트.

1~10일치는 11일, 1~20일치는 21일, 1~말일치는 익월 1일에 나온다(휴일이면 다음 영업일). 발표 뒤
보관본(macro_history/customs_flash.csv)에 아직 없는 회차가 있으면 '갱신 필요'다. 월간 워크플로는
원래 일요일·매달 7일에만 돌아 발표와 최대 열흘 어긋났다(2026-09-23 요청).
"""
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import customs_release_gate as gate  # noqa: E402

KST = timezone(timedelta(hours=9))


def write_flash(path, rows):
    pd.DataFrame(rows, columns=["month", "days", "value", "total"]).to_csv(path, index=False)


class ExpectedReleaseTests(unittest.TestCase):
    def test_release_days_by_calendar_position(self):
        # 11일 이후면 그 달 10일치, 21일 이후면 20일치, 1일 이후면 전달 말일치를 기대한다.
        self.assertEqual(gate.expected_releases(date(2026, 9, 5)), [("2026-08", 31)])
        self.assertEqual(gate.expected_releases(date(2026, 9, 11)), [("2026-08", 31), ("2026-09", 10)])
        self.assertEqual(gate.expected_releases(date(2026, 9, 21)),
                         [("2026-08", 31), ("2026-09", 10), ("2026-09", 20)])
        self.assertEqual(gate.expected_releases(date(2026, 10, 1)), [("2026-09", 31)])

    def test_release_is_not_expected_before_its_day(self):
        self.assertNotIn(("2026-09", 10), gate.expected_releases(date(2026, 9, 10)))
        self.assertNotIn(("2026-09", 20), gate.expected_releases(date(2026, 9, 20)))

    def test_year_boundary(self):
        self.assertEqual(gate.expected_releases(date(2027, 1, 1)), [("2026-12", 31)])
        self.assertEqual(gate.expected_releases(date(2027, 1, 12)), [("2026-12", 31), ("2027-01", 10)])


class MissingReleaseTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "customs_flash.csv"

    def test_reports_releases_not_yet_in_the_cache(self):
        write_flash(self.path, [("2026-08", 31, 1.0, 2.0), ("2026-09", 10, 1.0, 2.0)])
        missing = gate.missing_releases(self.path, date(2026, 9, 23))
        self.assertEqual(missing, [("2026-09", 20)])

    def test_nothing_missing_when_cache_is_current(self):
        write_flash(self.path, [("2026-08", 31, 1.0, 2.0), ("2026-09", 10, 1.0, 2.0), ("2026-09", 20, 1.0, 2.0)])
        self.assertEqual(gate.missing_releases(self.path, date(2026, 9, 23)), [])

    def test_month_end_release_is_stored_as_31(self):
        # 관세청 응답의 28·30·31 을 보관본은 31 로 모은다(exports.py 규칙). 기대치도 31 이어야 짝이 맞는다.
        write_flash(self.path, [("2026-02", 31, 1.0, 2.0)])
        self.assertEqual(gate.missing_releases(self.path, date(2026, 3, 3)), [])

    def test_missing_cache_means_everything_is_missing(self):
        self.assertEqual(gate.missing_releases(self.path, date(2026, 9, 23)),
                         [("2026-08", 31), ("2026-09", 10), ("2026-09", 20)])

    def test_holiday_delay_is_retried_not_marked_done(self):
        # 발표가 휴일로 미뤄져 21일에 못 받았다면 22일·23일에도 계속 '누락'으로 남아 다음 회차가 다시 본다.
        write_flash(self.path, [("2026-09", 10, 1.0, 2.0)])
        for day in (21, 22, 23):
            self.assertIn(("2026-09", 20), gate.missing_releases(self.path, date(2026, 9, day)))


class DecisionTests(unittest.TestCase):
    def test_should_rebuild_only_when_a_release_is_missing(self):
        path = Path(tempfile.mkdtemp()) / "customs_flash.csv"
        write_flash(path, [("2026-09", 10, 1.0, 2.0)])
        rebuild, why = gate.should_rebuild(path, now=datetime(2026, 9, 23, 9, 0, tzinfo=KST))
        self.assertTrue(rebuild)
        self.assertIn("2026-09 20일치", why)
        write_flash(path, [("2026-08", 31, 1, 2), ("2026-09", 10, 1, 2), ("2026-09", 20, 1, 2)])
        rebuild, why = gate.should_rebuild(path, now=datetime(2026, 9, 23, 9, 0, tzinfo=KST))
        self.assertFalse(rebuild)

    def test_does_not_fire_on_the_release_day_before_publication_hour(self):
        # 관세청은 오전에 낸다. 11일 00:22 회차가 '누락'으로 헛돌지 않게 09:00 KST 전에는 기대하지 않는다.
        path = Path(tempfile.mkdtemp()) / "customs_flash.csv"
        write_flash(path, [("2026-08", 31, 1, 2)])
        rebuild, _ = gate.should_rebuild(path, now=datetime(2026, 9, 11, 3, 22, tzinfo=KST))
        self.assertFalse(rebuild)
        rebuild, _ = gate.should_rebuild(path, now=datetime(2026, 9, 11, 9, 22, tzinfo=KST))
        self.assertTrue(rebuild)


if __name__ == "__main__":
    unittest.main()


class LongtermFlashNoteTests(unittest.TestCase):
    """장기 전망 조각의 '최신 반도체 수출 잠정치' 블록. 표시만 하고 모델 입력에 넣지 않는다."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import build_longterm_report as lt
        self.lt = lt
        self.path = Path(tempfile.mkdtemp()) / "customs_flash.csv"
        write_flash(self.path, [("2025-09", 10, 100.0, 300.0), ("2025-09", 20, 200.0, 600.0),
                                ("2026-08", 31, 900.0, 2000.0), ("2026-09", 10, 150.0, 400.0),
                                ("2026-09", 20, 260.0, 700.0)])

    def test_shows_releases_after_the_model_month_with_same_day_yoy(self):
        note = self.lt.latest_flash_note("2026-08-01", flash_path=self.path)
        self.assertEqual([(r["month"], r["days"]) for r in note["rows"]], [("2026-09", 10), ("2026-09", 20)])
        self.assertAlmostEqual(note["rows"][0]["yoy"], 0.5)          # 150/100 - 1
        self.assertAlmostEqual(note["rows"][1]["yoy"], 0.3)          # 260/200 - 1
        self.assertEqual(note["model_as_of"], "2026-08")

    def test_nothing_when_the_model_already_covers_the_month(self):
        self.assertIsNone(self.lt.latest_flash_note("2026-09-01", flash_path=self.path))

    def test_html_states_that_inputs_were_not_changed(self):
        note = self.lt.latest_flash_note("2026-08-01", flash_path=self.path)
        html = self.lt.flash_note_html(note)
        self.assertIn("입력에 넣지 않았습니다", html)
        self.assertIn("2026-08", html)
        self.assertIn("+50.0%", html)
        self.assertEqual(self.lt.flash_note_html(None), "")

    def test_missing_or_broken_cache_is_silent(self):
        self.assertIsNone(self.lt.latest_flash_note("2026-08-01", flash_path=self.path.parent / "nope.csv"))
        self.path.write_text("garbage", encoding="utf-8")
        self.assertIsNone(self.lt.latest_flash_note("2026-08-01", flash_path=self.path))


class WorkflowWiringTests(unittest.TestCase):
    def test_release_day_crons_and_gate(self):
        import yaml
        wf = yaml.safe_load((ROOT / ".github" / "workflows" / "monthly-longterm.yml").read_text(encoding="utf-8"))
        crons = [x["cron"] for x in wf[True]["schedule"]]
        self.assertEqual(crons, ["17 1 * * *"])  # 매일 10:17 KST 한 번
        steps = wf["jobs"]["longterm"]["steps"]
        names = [s.get("name", "") for s in steps]
        self.assertLess(names.index("이번 분기 영업이익 추정·발행"), names.index("장기 전망 계산·발행"))
        self.assertLess(names.index("장기 전망 계산·발행"), names.index("종합 보고서의 장기 전망 탭 갱신"))
        self.assertFalse(any("customs_release_gate.py" in step.get("run", "") for step in steps))

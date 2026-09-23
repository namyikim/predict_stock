# -*- coding: utf-8 -*-
"""관세청 10일 단위 잠정치 — 장기 전망의 표시 블록과 일일 갱신 워크플로 배선.

2026-09-23 처음엔 발표일 게이트(tools/customs_release_gate.py)를 만들었으나, 같은 날 매일 10:17 KST
무조건 갱신으로 대체됐다(더 단순하고 계산 비용이 작다). 게이트는 지웠고 아래는 여전히 쓰이는 부분만 검사한다.

원래 설명:

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

KST = timezone(timedelta(hours=9))


def write_flash(path, rows):
    pd.DataFrame(rows, columns=["month", "days", "value", "total"]).to_csv(path, index=False)


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

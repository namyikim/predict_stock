# -*- coding: utf-8 -*-
"""가상 매매 시뮬레이션 페이지.

이 페이지의 위험은 '검증 안 된 수익 곡선을 결론처럼 읽는 것'이다. 그래서 백테스트를 쓰지 않고
원장의 사전 예측만 쓰는지, 비용을 끌 수 없는지, 보유 전략과 나란히 보여 주는지를 테스트로 고정한다.
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "lab" / "index.html"


class LabPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")
        cls.script = re.search(r"<script>(.*?)</script>", cls.html, re.S).group(1)

    def test_javascript_parses(self):
        tmp = Path("/tmp/_lab_check.js")
        tmp.write_text(self.script, encoding="utf-8")
        done = subprocess.run(["node", "--check", str(tmp)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_uses_only_prospective_scored_ledger_rows(self):
        # 백테스트가 아니라 실제로 미리 낸 예측만 쓴다.
        self.assertIn('r.kind === "direction"', self.script)
        self.assertIn('String(r.is_prospective).toLowerCase() === "true"', self.script)
        self.assertIn('r.status === "scored"', self.script)
        self.assertIn("forecast_history/", self.script)

    def test_first_record_per_day_wins(self):
        # 원장 집계 규칙과 같아야 한다(같은 날 여러 번 돌린 것 중 첫 기록만).
        self.assertIn('var key = r.target_date + "|" + r.model;', self.script)

    def test_entry_is_at_the_open_not_the_previous_close(self):
        # 예측을 볼 수 있는 가장 이른 실행 시점은 09:00 시가다. 전일 종가로 사면 갭을 공짜로 먹는다.
        self.assertIn("var buy = open * (1 + cost.slip)", self.script)
        self.assertIn("sell = close * (1 - cost.slip)", self.script)
        self.assertIn("var gross = sell / buy - 1", self.script)
        # 전일 종가(current_close)로 진입하면 밤사이 갭을 공짜로 먹는다. 쓰지 않는다.
        self.assertNotIn("current_close", self.script)

    def test_costs_are_always_applied(self):
        self.assertIn("cost.fee * 2 + cost.tax", self.script)
        self.assertIn("거래비용은 끌 수 없습니다", self.html)

    def test_buy_and_hold_is_shown_alongside(self):
        self.assertIn('simulate(picked, "always", cost)', self.script)
        self.assertIn("보유 대비", self.script)

    def test_small_sample_warning(self):
        self.assertIn("picked.length < 60", self.script)
        self.assertIn("이 결과는 잡음입니다", self.script)

    def test_page_states_the_session_limitation(self):
        self.assertIn("AUC 0.80", self.html)
        self.assertIn("AUC 0.50", self.html)
        self.assertIn("실제 거래는 하지 않았습니다", self.html)
        self.assertIn('name="robots" content="noindex,nofollow"', self.html)

    def test_not_linked_from_the_landing_page(self):
        landing = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("lab/", landing)

    def test_admin_links_to_it(self):
        admin = (ROOT / "docs" / "admin" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="../lab/"', admin)

    def test_all_rules_are_computed_side_by_side(self):
        # 규칙을 하나씩 바꿔가며 보면 비교가 어렵다. 같은 자료·같은 비용으로 동시에 돌린다.
        self.assertIn("var RULES = [", self.script)
        for rule in ("up_over_flat", "up_over_third", "predicted_up", "always"):
            self.assertIn(f'id: "{rule}"', self.script)
        self.assertIn("function compareTable(", self.script)
        self.assertIn('$("compare").innerHTML = compareTable(picked, cost, rule)', self.script)

    def test_comparison_warns_against_picking_the_winner(self):
        # 표에서 제일 좋은 규칙을 고르면 그 표본에 맞춘 것이다. 화면에 그 함정을 적는다.
        self.assertIn("이 표에서 제일 좋은 규칙을 고르지 마세요", self.script)
        self.assertIn("과적합", self.script)
        self.assertIn("규칙은 미리 정해 두고", self.script)

    def test_comparison_shows_relative_to_buy_and_hold(self):
        self.assertIn("보유 대비", self.script)
        self.assertIn('simulate(picked, "always", cost)', self.script)

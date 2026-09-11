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


class PageSource(unittest.TestCase):
    """페이지 원문을 읽는 공통 베이스."""

    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")
        cls.script = re.search(r"<script>(.*?)</script>", cls.html, re.S).group(1)


class LabPageTests(PageSource):
    pass

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


class AttributionTabTests(PageSource):
    """무엇이 예측을 밀었는지 — 기록은 남기되 검증 전 해석을 강요하지 않는다."""

    def test_tab_exists_and_is_not_the_default(self):
        self.assertIn('id="tab-attr"', self.html)
        self.assertIn('id="panel-attr" hidden', self.html)   # 가상 매매가 기본

    def test_reads_attribution_and_ledger(self):
        self.assertIn("attribution.csv", self.script)
        self.assertIn("forecast_log.csv", self.script)
        self.assertIn("function outcomeByDate()", self.script)

    def test_only_scored_prospective_rows_decide_hit_or_miss(self):
        self.assertIn('r.kind !== "direction" || r.status !== "scored"', self.script)
        self.assertIn('String(r.is_prospective).toLowerCase() !== "true"', self.script)

    def test_warns_that_large_contribution_is_not_usefulness(self):
        self.assertIn("기여도가 큰 특징이 도움이 된 특징은 아닙니다", self.html)
        self.assertIn("틀린 날에 더 크게 반응한 특징은 해로울 수 있습니다", self.script)

    def test_small_sample_warning_on_the_split_view(self):
        self.assertIn("total < 60", self.script)
        self.assertIn("이 표는 아직 잡음입니다", self.script)

    def test_public_report_does_not_show_attribution(self):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        report = next("".join(c["source"]) for c in nb["cells"]
                      if "def build_summary():" in "".join(c.get("source", [])))
        self.assertNotIn("live_contributions", report)
        self.assertNotIn("attribution", report)


class AttributionRecordingTests(unittest.TestCase):
    """기여도는 대표 모델과 같은 특징 집합에서 뽑아야 한다."""

    @classmethod
    def setUpClass(cls):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        cls.source = "\n".join("".join(c["source"]) for c in nb["cells"])

    def test_uses_the_headline_models_feature_set(self):
        # 전체 특징 모델에서 뽑으면 대표가 쓰지도 않는 macro_*·nsi_* 가 1위로 찍힌다.
        self.assertIn('if HEADLINE_MODEL == "No macro ensemble" and market_live_models:', self.source)
        self.assertIn("live_X[:, market_feature_idx]", self.source)

    def test_only_recorded_for_prospective_runs(self):
        self.assertIn("if RECORD_FORECAST and live_contributions:", self.source)

    def test_first_record_per_day_wins(self):
        self.assertIn('drop_duplicates(["prediction_date", "model", "rank"], keep="first")', self.source)

    def test_file_is_synced_with_the_ledger(self):
        self.assertIn('"attribution.csv"', self.source)

    def test_no_new_dependency_for_contributions(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("shap", requirements.lower())
        forecast = (ROOT / "forecast_utils.py").read_text(encoding="utf-8")
        self.assertIn("pred_contrib=True", forecast)     # LightGBM 내장
        self.assertIn('hasattr(model, "coef_")', forecast)   # 로지스틱은 계수×표준화값

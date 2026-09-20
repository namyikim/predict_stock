# -*- coding: utf-8 -*-
"""가상 매매 시뮬레이션 페이지.

이 페이지의 위험은 '검증 안 된 수익 곡선을 결론처럼 읽는 것'이다. 그래서 백테스트를 쓰지 않고
원장의 사전 예측만 쓰는지, 비용을 끌 수 없는지, 보유 전략과 나란히 보여 주는지를 테스트로 고정한다.
"""
import json
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
        import tempfile
        with tempfile.TemporaryDirectory() as d:          # 고정 /tmp 는 Windows 에서 쓸 수 없다
            tmp = Path(d) / "_lab_check.js"
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




class AiDailyForecastTabTests(PageSource):
    """기존 모델·거시경제와 분리된 사람이 읽을 수 있는 AI 판단 원장."""

    def test_tab_is_separate_and_not_default(self):
        self.assertIn('id="tab-ai"', self.html)
        self.assertIn('id="panel-ai" hidden', self.html)
        self.assertIn("AI 일일예측", self.html)

    def test_reads_only_the_separate_ai_ledger(self):
        self.assertIn("ai_daily_forecast/index.json", self.script)
        ai_block = self.script[self.script.index("function loadAiForecasts") :]
        self.assertNotIn("forecast_log.csv", ai_block)
        self.assertNotIn("macro", ai_block.lower())

    def test_explains_baseline_and_separate_metrics(self):
        for text in ("전일 종가 대비", "방향 적중률", "시초가 평균 오차", "종가 평균 오차"):
            self.assertIn(text, self.html + self.script)

    def test_warns_on_small_samples_and_disclaims_advice(self):
        self.assertIn("scored_days", self.script)
        self.assertIn("< 20", self.script)
        self.assertIn("투자 자문이 아닙니다", self.html)

    def test_missing_metrics_render_as_unknown_not_zero(self):
        self.assertIn(
            'if (value === null || value === undefined || value === "") return "—";',
            self.script,
        )

    def test_all_ledger_text_is_escaped_and_links_are_https_only(self):
        self.assertIn("function esc(value)", self.script)
        self.assertIn('url.indexOf("https://") === 0', self.script)
        self.assertIn('rel="noopener noreferrer nofollow"', self.script)

    def test_reopening_ai_tab_fetches_fresh_data(self):
        # 예측이 없을 때 탭을 먼저 열어도, 나중에 다시 열면 새 원장을 받아야 한다.
        harness = f"""
const listeners = {{}};
const elements = {{}};
function element(id) {{
  if (!elements[id]) elements[id] = {{
    value: id === "target" ? "samsung" : "",
    className: "", hidden: false, innerHTML: "", textContent: "",
    addEventListener: function (event, handler) {{
      if (event === "click") listeners[id] = handler;
    }}
  }};
  return elements[id];
}}
global.document = {{ getElementById: element }};
let aiFetches = 0;
global.fetch = function (url) {{
  if (String(url).includes("ai_daily_forecast/index.json")) aiFetches += 1;
  return Promise.resolve({{
    ok: true,
    text: function () {{ return Promise.resolve(""); }},
    json: function () {{ return Promise.resolve({{records: [], summary: {{}}}}); }}
  }});
}};
eval({json.dumps(self.script)});
async function settle() {{
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
}}
(async function () {{
  listeners["tab-ai"](); await settle();
  listeners["tab-ai"](); await settle();
  if (aiFetches !== 2) {{
    console.error("expected 2 AI fetches, got " + aiFetches);
    process.exit(1);
  }}
}})();
"""
        done = subprocess.run(["node"], input=harness, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

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


class AiForecastDateLabelTests(PageSource):
    """날짜 하나가 '작성일이자 예측 대상일'임을 화면이 밝혀야 한다(2026-09-20 질문).

    모델 예측은 전날 06:22에 다음 거래일을 맞히고, 이쪽은 당일 아침에 당일을 맞힌다. 같은 잣대로 비교하면 안 된다.
    """

    def test_intro_contrasts_the_two_forecast_times(self):
        self.assertIn("날짜는 예측을 쓴 날이자 맞히려는 날입니다", self.html)
        self.assertIn("그날</b> 종가를 예측합니다", self.html)
        self.assertIn("전날</b> 06:22", self.html)
        self.assertIn("같은 잣대로 비교하지 마세요", self.html)

    def test_latest_heading_says_what_the_date_means(self):
        self.assertIn('"<h2>최근 판단 · " + esc(latest.target_date) + " 종가 예측</h2>"', self.script)
        self.assertIn("같은 날 장 마감 종가를 맞히려는 예측입니다", self.script)
        self.assertIn("aiWrittenAt(latest.created_at_kst)", self.script)

    def test_history_table_names_the_column_and_shows_the_writing_time(self):
        self.assertIn("<th>예측일 = 대상일</th>", self.script)
        self.assertIn("aiWrittenAt(record.created_at_kst)", self.script)
        self.assertNotIn("<th>날짜</th><th>상태</th>", self.script)

    def test_writing_time_helper_is_defensive(self):
        body = self.script[self.script.index("function aiWrittenAt"):]
        body = body[:body.index("function aiPct")]
        self.assertIn('String(iso || "")', body)              # null 이어도 제목이 깨지지 않는다
        self.assertIn("/T(" + chr(92) + "d{2}:" + chr(92) + "d{2})/", body)   # ISO 에서 시:분만 꺼낸다
        self.assertIn('return match ? " " + match[1] : "";', body)

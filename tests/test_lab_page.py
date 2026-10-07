# -*- coding: utf-8 -*-
"""가상 매매 시뮬레이션 페이지.

개장 시점에 이용 가능한 원장 예측, 정수 수량·비용, 보유 기준선 및 민감도 모드의 계약을 확인한다.
실제 계좌 계산은 Node에서 브라우저와 동일한 엔진을 실행해 검증한다.
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

    def test_account_contracts_in_node(self):
        done = subprocess.run(["node", str(ROOT / "tests/trading_sim_cases.cjs")], cwd=ROOT,
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_page_uses_shared_engine_and_prepares_rows_before_trading(self):
        self.assertIn('src="trading_sim.js"', self.html)
        self.assertIn('TradingSim.prepare(rows, model)', self.script)
        self.assertIn('TradingSim.simulate(picked, rule, cost, capital)', self.script)
        self.assertIn('개장 시점 이용 불가', self.script)

    def test_zero_cost_is_only_allowed_as_sensitivity_experiment(self):
        self.assertIn('$("cost-mode").value === "standard"', self.script)
        self.assertIn('v<=0', self.script)
        self.assertIn('비용 민감도 실험', self.html)

    def test_buy_and_hold_and_daily_session_are_distinct(self):
        self.assertIn('simulate(picked, "buy_hold", cost, capital)', self.script)
        self.assertIn('매일 장중 보유', self.html)
        self.assertIn('매수 후 보유', self.html)

    def test_small_sample_warning_does_not_claim_60_days_prove_profit(self):
        self.assertIn('picked.length < 60', self.script)
        self.assertIn('60일이 넘어도 다른 기간과 장세에서 별도 검증', self.script)

    def test_page_states_execution_and_observation_limitations(self):
        self.assertIn('실제 거래는 하지 않았습니다', self.html)
        self.assertIn('관측일 종가 기준', self.html)
        self.assertIn('장 시작 후 예측은 시가에 거래할 수 없어 제외', self.html)
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
        self.assertIn('$("compare").innerHTML = compareTable(picked, cost, rule, capital)', self.script)

    def test_comparison_warns_against_picking_the_winner(self):
        # 표에서 제일 좋은 규칙을 고르면 그 표본에 맞춘 것이다. 화면에 그 함정을 적는다.
        self.assertIn("이 표에서 제일 좋은 규칙을 고르지 마세요", self.script)
        self.assertIn("과적합", self.script)
        self.assertIn("규칙은 미리 정해 두고", self.script)

    def test_comparison_shows_relative_to_buy_and_hold(self):
        self.assertIn("보유 대비", self.script)
        self.assertIn('simulate(picked, "buy_hold", cost, capital)', self.script)


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
        # 선택한 예측(방향·1·5·20일 종가)의 채점된 행만 판정한다(2026-09-28 예측별 탭).
        self.assertIn('if (!inScope(r) || r.status !== "scored") return;', self.script)
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
        # 이메일·카카오 유입을 집계하는 traffic_attribution.js는 모델 기여도 원장이 아니다.
        self.assertNotIn("attribution.csv", report)




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
global.document = {{ getElementById: element, querySelectorAll: function () {{ return []; }} }};
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
        # 기록 창의 실행만 남긴다. 방향 기여도가 없어도 가격 기여도는 남기므로 live_contributions 는 조건이 아니다
        # (2026-09-28). 저녁 실행은 원장에 가격 예측을 기록하지 않으니 가격 기여도도 남기지 않는다.
        self.assertIn("if RECORD_FORECAST:\n    _attr_path", self.source)
        self.assertIn("if not RECORD_EVENING_ONLY:", self.source)

    def test_every_run_is_kept(self):
        # 2026-09-28 부터 실행마다 남긴다. '예측일마다 첫 기록만'이던 옛 규칙은 전날 저녁 실행이 원장의 대표
        # 예측을 만든 아침 실행의 기여도를 밀어내, /lab/ 요약이 원장과 한 건도 짝을 짓지 못했다.
        # 어느 실행을 셀지는 요약(correct_attribution.js)이 원장의 '최초 사전 예측' 규칙으로 고른다.
        self.assertIn('drop_duplicates(["run_id", "model", "kind", "horizon_days", "rank"], keep="first")', self.source)
        self.assertNotIn('drop_duplicates(["prediction_date", "model", "rank"]', self.source)

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


class AttributionStockLabelTests(unittest.TestCase):
    """기여도 탭 결과에 종목 이름이 보여야 한다(2026-09-28: 삼성인지 하이닉스인지 표시가 없었다)."""

    def setUp(self):
        self.page = (ROOT / "docs" / "lab" / "index.html").read_text(encoding="utf-8")

    def test_headings_carry_the_stock_name(self):
        self.assertIn("function stockName()", self.page)
        self.assertIn("'<h2>' + esc(stockName()) + ' · ' + esc(modelLabel(g.model))", self.page)
        self.assertIn('$("attr-status").textContent = stockName() + " 기록";', self.page)

    def test_model_names_are_readable(self):
        self.assertIn('"Candidate evening forecast": "전날 저녁 예측 (관찰 후보)"', self.page)
        self.assertIn('"No macro ensemble": "대표 예측 (아침 07:00)"', self.page)


class AttributionStockSelectorTests(unittest.TestCase):
    """종목은 버튼 탭으로 고른다(2026-09-28). 가상 매매·기여도 두 탭 모두에 있고, 둘 다 숨은 #target 하나를 바꾼다.

    처음엔 종목 드롭다운이 '가상 매매' 탭 안에만 있어 기여도 탭에서는 고를 수 없었다.
    """

    def setUp(self):
        self.page = (ROOT / "docs" / "lab" / "index.html").read_text(encoding="utf-8")

    def panel(self, name, until):
        return self.page[self.page.index(f'<div id="panel-{name}"'):self.page.index(until)]

    def test_both_panels_have_stock_button_tabs(self):
        for panel in (self.panel("sim", '<div id="panel-attr"'), self.panel("attr", '<div id="panel-ai"')):
            self.assertIn('class="stock-tabs"', panel)
            self.assertIn('data-stock="samsung">삼성전자</button>', panel)
            self.assertIn('data-stock="sk_hynix">SK하이닉스</button>', panel)

    def test_hidden_select_keeps_the_value_and_buttons_follow_it(self):
        self.assertIn('<select id="target" hidden>', self.page)
        self.assertIn("function paintStockTabs()", self.page)
        self.assertIn('$("target").dispatchEvent(new Event("change"));', self.page)
        self.assertNotIn("attr-target", self.page)

    def test_buttons_are_easy_to_tap(self):
        self.assertIn("min-height:40px", self.page)


class AttributionScopeTabTests(unittest.TestCase):
    """기여도 탭을 예측별(다음 거래일 방향·1·5·20일 종가)로 나눠 본다(2026-09-28)."""

    def setUp(self):
        self.page = (ROOT / "docs" / "lab" / "index.html").read_text(encoding="utf-8")

    def test_four_scope_tabs(self):
        for scope, label in (("direction:1", "다음 거래일 방향"), ("price:1", "1일 종가"),
                             ("price:5", "5일 종가"), ("price:20", "20일 종가")):
            self.assertIn(f'data-scope="{scope}">{label}</button>', self.page)

    def test_every_view_uses_the_selected_scope(self):
        self.assertIn("var rows = scopedAttr();", self.page)
        self.assertIn("summarizeCorrectAttribution(attrRows, ledgerRows, attrScope)", self.page)
        body = self.page[self.page.index("function renderAttribution()"):]
        body = body[:body.index("\n  }\n")]
        self.assertNotIn("attrRows.forEach", body)


class PaperStatusTests(PageSource):
    def test_saved_paper_book_renders_and_escapes_collection_errors(self):
        data=json.loads((ROOT/'docs/lab/paper_status.json').read_text())
        data.update(collection_errors=['<script>잘못된 응답</script>'],last_tick='2026-10-06T01:00:00+00:00',
                    orders=[{'decision_at':'2026-10-06T00:45:00+00:00','target':'samsung','side':'buy','qty':10,
                             'status':'rejected','reason':'price_moved'}])
        script=self.script.replace('  loadPaper();','  global.renderPaperForTest=renderPaper;')
        harness=f'''
const elements={{}};
global.document={{getElementById:(id)=>elements[id]||(elements[id]={{value:'samsung',addEventListener:()=>{{}}}}),querySelectorAll:()=>[]}};
global.fetch=()=>new Promise(()=>{{}});
eval({json.dumps(script)});
renderPaperForTest({json.dumps(data)});
const html=elements['paper-status'].innerHTML;
const assert=require('node:assert/strict');
assert.ok(html.includes('10,000,000원'));
assert.ok(html.includes('실주문 없는 관찰용 계좌'));
assert.ok(html.includes('결정 이후 가격 변동 초과'));
assert.ok(html.includes('&lt;script&gt;'));
assert.ok(!html.includes('<script>잘못된 응답'));
'''
        done=subprocess.run(['node'],input=harness,text=True,capture_output=True)
        self.assertEqual(done.returncode,0,done.stderr)


class PaperDiagnosticsViewTests(PageSource):
    def test_diagnostics_and_legacy_state_render(self):
        data=json.loads((ROOT/'docs/lab/paper_status.json').read_text())
        data['diagnostics']={'schema_version':1,'last_success_at':None,'targets':{'samsung':{
            'quote_timestamp':'2026-10-06T03:38:00+00:00','observed_at':'2026-10-06T03:57:57+00:00',
            'quote_age_seconds':1197,'signal_count':4,'eligible_signal_count':1,
            'reason_counts':{'stale_quote':1,'<img src=x>':1}}}}
        script=self.script.replace('  loadPaper();','  global.renderPaperForTest=renderPaper;')
        harness=f'''const elements={{}};
global.document={{getElementById:(id)=>elements[id]||(elements[id]={{value:'samsung',addEventListener:()=>{{}}}}),querySelectorAll:()=>[]}};
global.fetch=()=>new Promise(()=>{{}});
eval({json.dumps(script)});
const data={json.dumps(data)};
renderPaperForTest(data);
const assert=require('node:assert/strict'),html=elements['paper-status'].innerHTML;
assert.ok(html.includes('시세 지연으로 거래 제외'));
assert.ok(html.includes('1197초'));
assert.ok(html.includes('유효 후보 1건'));
assert.ok(html.includes('&lt;img src=x&gt;'));
assert.ok(!html.includes('<img src=x>'));
delete data.diagnostics;renderPaperForTest(data);
assert.ok(elements['paper-status'].innerHTML.includes('진단 정보가 없는 이전 저장본'));
'''
        done=subprocess.run(['node'],input=harness,text=True,capture_output=True)
        self.assertEqual(done.returncode,0,done.stderr)

class PerformanceSummaryTests(PageSource):
    def test_performance_summary_period_money_units_and_empty_data(self):
        self.assertIn('function performanceSummary(',self.script)
        script=self.script.replace('  loadPaper();','  global.summaryForTest=performanceSummary;')
        harness=f'''const elements={{}};
global.document={{getElementById:(id)=>elements[id]||(elements[id]={{value:'samsung',addEventListener:()=>{{}}}}),querySelectorAll:()=>[]}};
global.fetch=()=>new Promise(()=>{{}});
eval({json.dumps(script)});
const assert=require('node:assert/strict');
let html=summaryForTest({{dates:['2026-10-01','2026-10-06'],capital:10000000,total:0.025,benchmark:0.01,trades:2}});
for(const text of ['2026-10-01','2026-10-06','2일','2.50%','250,000원','10,250,000원','1.50%p','2건'])assert.ok(html.includes(text),text);
html=summaryForTest({{dates:[],capital:10000000,total:0,trades:0}});
assert.ok(html.includes('평가일 없음'));
assert.ok(html.includes('거래 없음'));
assert.ok(!html.includes('0.00%'));
assert.ok(!html.includes('NaN'));
html=summaryForTest({{dates:['2026-10-06'],capital:10000000,total:-0.02,trades:1}});
assert.ok(html.includes('-200,000원'));
assert.ok(html.includes('-2.00%'));
'''
        done=subprocess.run(['node'],input=harness,text=True,capture_output=True)
        self.assertEqual(done.returncode,0,done.stderr)

    def test_saved_validation_displays_observed_dates_and_net_profit(self):
        data=json.loads((ROOT/'docs/lab/validation_samsung.json').read_text())
        harness=f'''const elements={{}};
global.document={{getElementById:(id)=>elements[id]||(elements[id]={{value:'samsung',addEventListener:()=>{{}}}}),querySelectorAll:()=>[]}};
const data={json.dumps(data)};
global.fetch=(url)=>url==='validation_samsung.json'?Promise.resolve({{ok:true,json:()=>Promise.resolve(data)}}):new Promise(()=>{{}});
eval({json.dumps(self.script)});
setImmediate(()=>{{
 const assert=require('node:assert/strict'), html=elements['validation-result'].innerHTML;
 assert.ok(html.includes('실제 자료 평가 기간: '+data.evaluation.dates[0]));
 assert.ok(html.includes(data.evaluation.dates.length+'일 관측'));
 assert.ok(html.includes((data.evaluation.strategy.total*100).toFixed(2)+'%'));
 assert.ok(html.includes('과거') || html.includes('사후'));
 assert.ok(html.includes('순손익(원)'));
 assert.ok(html.includes('자료 부족'));
}});
'''
        done=subprocess.run(['node'],input=harness,text=True,capture_output=True)
        self.assertEqual(done.returncode,0,done.stderr)

    def test_empty_saved_evaluation_does_not_display_zero_performance(self):
        data=json.loads((ROOT/'docs/lab/validation_samsung.json').read_text())
        data['evaluation']['dates']=[]
        for key in ('strategy','buy_hold','daily_session'):
            data['evaluation'][key].update(total=0,trades=0)
        harness=f'''const elements={{}};
global.document={{getElementById:(id)=>elements[id]||(elements[id]={{value:'samsung',addEventListener:()=>{{}}}}),querySelectorAll:()=>[]}};
const data={json.dumps(data)};
global.fetch=(url)=>url==='validation_samsung.json'?Promise.resolve({{ok:true,json:()=>Promise.resolve(data)}}):new Promise(()=>{{}});
eval({json.dumps(self.script)});
setImmediate(()=>{{
 const assert=require('node:assert/strict'), html=elements['validation-result'].innerHTML;
 assert.ok(html.includes('평가일 없음'));
 assert.ok(html.includes('평가 자료 없음'));
 assert.ok(!html.includes('0.00%'));
}});
'''
        done=subprocess.run(['node'],input=harness,text=True,capture_output=True)
        self.assertEqual(done.returncode,0,done.stderr)

class AllModelPerformanceTests(PageSource):
    def test_comparison_engine_and_rendered_models(self):
        done=subprocess.run(['node',str(ROOT/'tests/model_comparison_cases.cjs')],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(done.returncode,0,done.stderr)
        self.assertIn('function modelComparisonTable(',self.script)
        script=self.script.replace('  loadPaper();','  global.tableForTest=modelComparisonTable;')
        harness=f'''const elements={{}};
global.document={{getElementById:(id)=>elements[id]||(elements[id]={{value:'samsung',addEventListener:()=>{{}}}}),querySelectorAll:()=>[]}};
global.fetch=()=>new Promise(()=>{{}});
eval({json.dumps(script)});
const assert=require('node:assert/strict');
const html=tableForTest({{scope:'available',eligibleModels:1,priceConflictDays:0,models:[
{{model:'A<script>',dates:['2026-10-01','2026-10-02'],simulation:{{total:.02,mdd:-.01,trades:[{{}}]}},hold:{{total:.01}}}},
{{model:'B',dates:[],simulation:null,hold:null}}]}},10000000,'A<script>');
for(const text of ['A&lt;script&gt;','B','2.00%','200,000원','1.00%p','2026-10-01','자료 없음'])assert.ok(html.includes(text),text);
assert.ok(!html.includes('<script>'));
'''
        done=subprocess.run(['node'],input=harness,text=True,capture_output=True)
        self.assertEqual(done.returncode,0,done.stderr)

    def test_all_models_render_even_when_selected_model_has_no_scores(self):
        script=self.script.replace('  loadPaper();','  global.runForTest=run;global.rowsForTest=function(x){rows=x;};')
        harness=f'''const elements={{}};
global.document={{getElementById:(id)=>elements[id]||(elements[id]={{value:'samsung',addEventListener:()=>{{}}}}),querySelectorAll:()=>[]}};
global.fetch=()=>new Promise(()=>{{}});
global.TradingSim=require({json.dumps(str(ROOT/'docs/lab/trading_sim.js'))});
eval({json.dumps(script)});
const values={{model:'未採点',rule:'up_over_flat',capital:'10000000',fee:'0.015',tax:'0.18',slip:'0.05','cost-mode':'standard','model-period':'available'}};
for(const [key,value]of Object.entries(values))elements[key].value=value;
const base={{kind:'direction',target_date:'2026-10-01',created_at_utc:'2026-10-01T08:00:00+09:00',is_prospective:'true',status:'scored',actual_open:'100',actual_close:'110',p_up:'.8',p_down:'.1',prediction:'상승'}};
rowsForTest([{{...base,model:'평가완료'}},{{...base,model:'未採点',status:'pending'}}]);runForTest();
const assert=require('node:assert/strict'), html=elements['model-compare'].innerHTML;
assert.ok(html.includes('평가완료'));
assert.ok(html.includes('未採点'));
assert.ok(html.includes('2026-10-01'));
assert.ok(html.includes('자료 없음'));
assert.ok(elements.result.innerHTML.includes('채점 예측이 없습니다'));
'''.replace('未採点','미채점')
        done=subprocess.run(['node'],input=harness,text=True,capture_output=True)
        self.assertEqual(done.returncode,0,done.stderr)

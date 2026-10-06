import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import fs from 'node:fs';
const require=createRequire(import.meta.url);
assert.ok(fs.existsSync(new URL('../docs/lab/wave_view.js',import.meta.url)),'파동 화면 모듈 필요');
const {renderWaveComparison}=require('../docs/lab/wave_view.js');
const now=Date.parse('2026-10-06T08:00:00Z');
// 자동 생성 자료가 쌓여도 테스트의 '자료 없음' 조건은 바뀌지 않는다.
const unavailable={status:'unavailable',metrics:{total:null},equity_curve:[],fills:[],orders:[],limitations:['missing_common_prices']};
const results=Object.fromEntries(['range_rebound','trend_pullback','buy_hold','cash','legacy'].map(k=>[k,structuredClone(unavailable)]));
let real={schema_version:1,target:'samsung',method:'observed_time_retrospective_split',automatic_promotion:false,
  input_sha256:'a'.repeat(64),engine_sha256:'b'.repeat(64),config_sha256:'c'.repeat(64),
  evidence:'insufficient',generated_at_utc:'2026-10-06T07:59:00Z',selected:null,
  config:{strategies:{range_rebound:{version:'wave-v1'},trend_pullback:{version:'wave-v1'}},
    paper:{capital:10000000,max_symbol_weight:.3},validation:{selection:{start:'2026-07-01',end:'2026-08-31'}}},
  evaluation:{dates:['2026-09-08','2026-10-06'],status:'unavailable',reasons:['missing_common_prices'],
    standard:structuredClone(results),double_cost:structuredClone(results)}};
const render=(v=real,p=null,options={})=>renderWaveComparison(v,p,{now,target:'samsung',...options});
assert.match(render(null),/평가 자료를 불러오지 못했습니다/);
assert.match(render(),/공통 평가 가격 자료 부족/);
assert.ok(!render().includes('>0.00%</td>'));
assert.match(render(),/관측 기록 없음/);
let v=structuredClone(real);v.generated_at_utc='2026-09-01T00:00:00Z';assert.match(render(v),/오래된 평가 자료/);
v=structuredClone(real);v.schema_version=99;assert.match(render(v),/지원하지 않는/);
v=structuredClone(real);v.config.strategies.range_rebound.version='wave-v2';assert.match(render(v),/전략 버전 불일치/);
v=structuredClone(real);v.target='sk_hynix';assert.match(render(v),/종목 불일치/);
v=structuredClone(real);v.input_sha256='';assert.match(render(v),/출처 확인 불가/);
v=structuredClone(real);v.generated_at_utc='2026-10-06T07:59:00Z';v.evaluation.status='available';v.evaluation.reasons=[];
v.evaluation.dates=['2026-10-01','2026-10-02'];v.evaluation.prices=[{session:'2026-10-01',close:100},{session:'2026-10-02',close:98}];
const result={metrics:{total:-.02,closed_trades:1,cost_won:3000,mdd:-.02,average_net_pnl:-200000,exposure_fraction:1,mean_holding_sessions:2,unfilled_rate:0},equity_curve:[{session:'2026-10-01',equity:10000000},{session:'2026-10-02',equity:9800000}],fills:[{side:'buy',session:'2026-10-01',qty:100,price:100,fee:10,tax:0,timing:'open',reason:'next_open'},{side:'sell',session:'2026-10-02',qty:100,price:98,fee:10,tax:10,timing:'close',reason:'max_holding'}],decisions:[{decision_at:'2026-10-01T06:31:00Z',signal_bar_end:'2026-10-01T06:30:00Z',action:'wait',reason_codes:['<script>alert(1)</script>']}],orders:[],uncertainty:{interval:[-.05,.02]}};
v.evaluation.standard.range_rebound=result;
v.evaluation.double_cost.range_rebound={...result,metrics:{...result.metrics,total:-.03}};
let html=render(v);
for(const text of ['-2.00%','-200,000원','관측 전용','누적 모의 수익률','최대 하락폭','가격과 판단·체결','기록 시각','&lt;script&gt;','장 시작','왕복 거래'])assert.ok(html.includes(text),text);
assert.ok(!html.includes('<script>alert'));
assert.match(html,/role="img"/);
assert.match(html,/<details>/);
assert.match(render(v,null,{scenario:'double_cost'}),/-3.00%/);
const noTrade=structuredClone(v);noTrade.evaluation.standard.range_rebound.metrics.closed_trades=0;assert.match(render(noTrade),/거래 없음/);
v.paper_config_hashes={range_rebound:'1'.repeat(64)};
const paper={schema_version:1,mode:'observe_only',generated_at:'2026-10-06T07:59:00Z',collection_errors:[],strategies:[{strategy:'range_rebound',strategy_version:'wave-v1',config_hash:'1'.repeat(64),mode:'observe_only',order_count:0,fill_count:0,decision_count:2,observations:[{target:'samsung',status:'stale_daily'}]}]};
assert.match(render(v,paper),/최신 완료 일봉 부족/);
paper.strategies[0].strategy_version='wrong';assert.match(render(v,paper),/관측 전략 버전 불일치/);
console.log('파동 화면 상태·버전·그래프·이스케이프 검증 통과');
const evil=structuredClone(v);evil.evaluation.standard.range_rebound.equity_curve[0].session='</title><script>alert(1)</script>';
assert.ok(!render(evil).includes('<script>alert(1)</script>'),'SVG 날짜도 이스케이프해야 함');
const unknown=structuredClone(v);unknown.evidence='unknown';assert.ok(!render(unknown).includes('최소 표본 충족'));assert.match(render(unknown),/평가 근거 확인 불가/);
const trust=structuredClone(v);trust.paper_config_hashes={range_rebound:'1'.repeat(64),trend_pullback:'2'.repeat(64)};
const matching={schema_version:1,mode:'observe_only',generated_at:'2026-10-06T07:59:00Z',strategies:[{strategy:'range_rebound',strategy_version:'wave-v1',config_hash:'1'.repeat(64),mode:'observe_only',order_count:0,fill_count:0,decision_count:2,observations:[]}]};
matching.strategies[0].config_hash='3'.repeat(64);assert.match(render(trust,matching),/관측 설정·출처 불일치/);

/* 사전 예측 원장의 기간 분리 검증. 실험실과 동일한 계좌 엔진을 사용한다(2026-10-04).
 * 전략 선택은 selection 기간만 사용한다. evaluation 성과로 재선택하거나 실거래를 활성화하지 않는다.
 */
'use strict';
const sim=require('../docs/lab/trading_sim.js');
function evaluate(rows, config) {
  const c=config;
  function date(value) {
    if(typeof value!=='string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) ||
       !Number.isFinite(Date.parse(value)) || new Date(value).toISOString().slice(0,10)!==value) throw Error('날짜 형식이 올바르지 않습니다.');
  }
  for(const part of ['selection','evaluation']) {
    if(!c[part]) throw Error('선택·평가 기간이 필요합니다.');
    date(c[part].start); date(c[part].end);
    if(c[part].start>c[part].end) throw Error('기간 시작이 종료보다 늦습니다.');
  }
  if(c.selection.end>=c.evaluation.start) throw Error('전략 선택 기간과 평가 기간은 순서대로 분리해야 합니다.');
  if(!Array.isArray(c.candidates) || !c.candidates.length) throw Error('전략 후보가 필요합니다.');
  const ids=new Set();
  for(const x of c.candidates) {
    if(!x.id || !x.model || ids.has(x.id)) throw Error('후보 ID와 모델을 확인하세요.');
    ids.add(x.id);
    sim.simulate([],x.rule,c.cost,c.capital);
  }
  if(['fee','tax','slip'].some(k=>c.cost[k]<=0)) throw Error('검증은 양수 비용을 적용해야 합니다.');
  for(const k of ['min_days','min_trades']) if(!Number.isInteger(c[k]) || c[k]<1) throw Error('최소 표본 기준은 양의 정수여야 합니다.');
  const prepared=c.candidates.map(x=>({candidate:x,...sim.prepare(rows,x.model)}));
  const maps=prepared.map(p=>new Map(p.rows.map(r=>[r.target_date,r])));
  const common=prepared[0].rows.map(r=>r.target_date).filter(d=>maps.every(m=>m.has(d)));
  const used=common.filter(d=>d>=c.selection.start && d<=c.evaluation.end);
  // 같은 날짜의 실제 시세가 모델마다 다르면 하나를 임의로 고르지 않는다.
  for(const d of used) for(const m of maps.slice(1)) {
    const a=maps[0].get(d),b=m.get(d);
    if(Number(a.actual_open)!==Number(b.actual_open) || Number(a.actual_close)!==Number(b.actual_close))
      throw Error('모델 간 실제 시세 불일치: '+d);
  }
  const selectedDates=common.filter(d=>d>=c.selection.start && d<=c.selection.end);
  const evaluationDates=common.filter(d=>d>=c.evaluation.start && d<=c.evaluation.end);
  function metrics(records,rule) {
    const out=sim.simulate(records,rule,c.cost,c.capital);
    return {total:out.total,mdd:out.mdd,cost_won:out.costWon,final_cash:out.finalCash,
      trades:out.trades.length,held_days:out.heldDays,observed_days:out.days,
      exposure:out.days?out.heldDays/out.days:0,unfilled:out.unfilled,curve:out.curve};
  }
  const selection=prepared.map((p,i)=>({id:p.candidate.id,...metrics(selectedDates.map(d=>maps[i].get(d)),p.candidate.rule)}));
  const ranking=selection.slice().sort((a,b)=>b.total-a.total || a.id.localeCompare(b.id));
  const picked=selectedDates.length ? ranking[0] : null;
  const index=picked?prepared.findIndex(p=>p.candidate.id===picked.id):-1;
  const selected=index<0?null:prepared[index].candidate;
  const evaluationRows=selected?evaluationDates.map(d=>maps[index].get(d)):[];
  const strategy=selected?metrics(evaluationRows,selected.rule):null;
  const enough=selectedDates.length>=c.min_days && evaluationDates.length>=c.min_days && picked &&
    picked.trades>=c.min_trades && strategy.trades>=c.min_trades;
  return {schema_version:1,scope:'pre_open_intraday',method:'retrospective_split',automatic_promotion:false,
    evidence:enough?'minimum_sample_met':'insufficient',selected,selection:{dates:selectedDates,metrics:selection},
    evaluation:{dates:evaluationDates,strategy,
      buy_hold:selected?metrics(evaluationRows,'buy_hold'):null,
      daily_session:selected?metrics(evaluationRows,'always'):null},
    coverage:prepared.map(p=>({id:p.candidate.id,eligible_days:p.rows.length,excluded:p.excluded,
      selection_days:p.rows.filter(r=>r.target_date>=c.selection.start && r.target_date<=c.selection.end).length,
      evaluation_days:p.rows.filter(r=>r.target_date>=c.evaluation.start && r.target_date<=c.evaluation.end).length})),
    limitations:['설정은 사후 선택입니다. 사전에 고정한 미사용 기간의 운영 실적이 아닙니다.',
      '예측이 있는 공통 관측일만 비교합니다. 기업행사·배당과 실제 체결은 반영하지 않습니다.']};
}
module.exports={evaluate};
if(require.main===module) {
  try { const input=JSON.parse(require('node:fs').readFileSync(0,'utf8')); process.stdout.write(JSON.stringify(evaluate(input.rows,input.config))); }
  catch(e) { console.error(e.message); process.exitCode=1; }
}

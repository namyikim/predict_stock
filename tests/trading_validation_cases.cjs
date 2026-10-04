const assert=require('node:assert/strict');
const {evaluate}=require(process.cwd()+'/tools/trading_validation.cjs');
function row(day,model,up,close) { return {kind:'direction',horizon_days:1,model,target_date:day,prediction_date:day,
 created_at_utc:day+'T07:00:00+09:00',is_prospective:'true',status:'scored',actual_open:100,actual_close:close,
 p_up:up,p_down:1-up,prediction:up>.5?'상승':'하락'}; }
const cfg={capital:10000,cost:{fee:.00015,tax:.0018,slip:.0005},selection:{start:'2026-09-01',end:'2026-09-02'},
 evaluation:{start:'2026-09-03',end:'2026-09-04'},min_days:2,min_trades:1,
 candidates:[{id:'a',model:'a',rule:'predicted_up'},{id:'b',model:'b',rule:'predicted_up'}]};
const rows=[row('2026-09-01','a',.8,110),row('2026-09-02','a',.2,90),row('2026-09-03','a',.8,90),row('2026-09-04','a',.8,90),
 row('2026-09-01','b',.2,110),row('2026-09-02','b',.8,90),row('2026-09-03','b',.2,90),row('2026-09-04','b',.2,90)];
const result=evaluate(rows,cfg);
assert.equal(result.selected.id,'a');
assert.ok(result.evaluation.strategy.total<0);
assert.equal(result.automatic_promotion,false);
assert.equal(result.evaluation.dates.length,2);
assert.equal(evaluate(rows.filter(r=>!(r.target_date==='2026-09-04' && r.model==='b')),cfg).evaluation.dates.length,1);
// 평가 구간의 미래 이익 변화로 선택 모델이 바뀌지 않는다.
assert.equal(evaluate(rows.map(r=>r.target_date>='2026-09-03'?{...r,p_up:.99,p_down:.01,prediction:'상승'}:r),cfg).selected.id,'a');
assert.throws(()=>evaluate(rows,{...cfg,evaluation:{start:'2026-09-02',end:'2026-09-04'}}));
assert.throws(()=>evaluate(rows.map((r,i)=>i===4?{...r,actual_close:112}:r),cfg),/시세/);
assert.equal(evaluate(rows,{...cfg,min_days:60}).evidence,'insufficient');
assert.throws(()=>evaluate(rows,{...cfg,cost:{fee:0,tax:0,slip:0}}));
assert.equal(evaluate(rows.map(r=>r.target_date>='2026-09-03'?{...r,actual_close:150}:r),cfg).selected.id,'a');
console.log('기간 분리 검증 계약 통과');

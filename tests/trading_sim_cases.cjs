const assert = require('node:assert/strict');
const sim = require(process.cwd() + '/docs/lab/trading_sim.js');
const row = (day, open=100, close=110, extra={}) => ({kind:'direction', horizon_days:'1', model:'m', target_date:day, prediction_date:day, created_at_utc:day+'T00:00:00Z', information_cutoff:'pre_open', is_prospective:'True', status:'scored', actual_open:open, actual_close:close, prediction:'상승', p_up:.7,p_down:.1,...extra});
// 정확히 개장 시각에 작성된 예측과 장후 정보는 개장가로 거래할 수 없다.
const early = row('2026-09-01',100,110,{created_at_utc:'2026-08-31T22:00:00Z'});
assert.equal(sim.prepare([early], 'm').rows.length, 1);
assert.equal(sim.prepare([row('2026-09-01')], 'm').rows.length, 0);
assert.equal(sim.prepare([{...early,information_cutoff:'post_open'}], 'm').rows.length, 0);
assert.equal(sim.prepare([{...early,created_at_utc:'bad'}], 'm').rows.length, 0);
assert.equal(sim.prepare([{...early,horizon_days:5}], 'm').rows.length, 0);
// 첫 기록의 결측·미채점을 나중 기록으로 바꾸지 않는다.
assert.equal(sim.prepare([{...early,status:'pending'}, {...early,created_at_utc:'2026-08-31T23:00:00Z'}], 'm').rows.length, 0);
const a={...early}, b={...early,target_date:'2026-09-02',prediction_date:'2026-09-02',created_at_utc:'2026-09-01T22:00:00Z',actual_open:120,actual_close:120};
const zero={fee:0,tax:0,slip:0};
const hold=sim.simulate([a,b],'buy_hold',zero,1000);
assert.equal(hold.finalCash,1200); assert.equal(hold.trades.length,1); assert.equal(hold.trades[0].shares,10);
assert.equal(sim.simulate([a,b],'always',zero,1000).finalCash,1100);
assert.equal(sim.simulate([a],'always',zero,50).trades.length,0);
// 9주 매수: 매수 수수료 9원, 매도 수수료 9.9원, 거래세 19.8원.
const fee=sim.simulate([a],'always',{fee:.01,tax:.02,slip:0},1000);
assert.equal(fee.trades[0].shares,9); assert.ok(Math.abs(fee.finalCash-1051.3)<1e-8);
assert.ok(Math.abs(fee.costWon-38.7)<1e-8); assert.ok(Math.abs(fee.trades[0].pnl-51.3)<1e-8);
assert.throws(()=>sim.simulate([a],'always',{fee:-.1,tax:0,slip:0},1000));
assert.throws(()=>sim.simulate([a],'always',zero,0));
assert.throws(()=>sim.simulate([a,a],'always',zero,1000));
assert.equal(sim.simulate([a],'predicted_up',zero,1000).finalCash,1100);
// 9/28부터 당일 KST 아침 예측 우선. 저녁 후보와 이전 날짜는 최초 기록 유지.
const holiday={...early,target_date:'2026-09-28',prediction_date:'2026-09-28',created_at_utc:'2026-09-24T22:00:00Z'};
const morning={...holiday,created_at_utc:'2026-09-27T22:00:00Z',prediction:'하락'};
assert.equal(sim.prepare([holiday,morning],'m').rows[0].prediction,'하락');
assert.equal(sim.prepare([{...holiday,model:'Candidate evening forecast'},{...morning,model:'Candidate evening forecast'}],'Candidate evening forecast').rows[0].prediction,'상승');
assert.equal(sim.prepare([holiday,{...morning,status:'pending'}],'m').rows.length,0);
console.log('trading simulation contracts passed');

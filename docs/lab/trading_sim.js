/* 실험실 계좌 시뮬레이터. 주문·실거래를 수행하지 않는다(2026-10-04 자동매매 검증 개선).
 * 브라우저와 Node 검증에서 같은 계산을 사용한다. 비용은 각 체결 금액 기준이다.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.TradingSim = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  function number(v) {
    if (v === null || v === undefined || String(v).trim() === '') return NaN;
    return Number(v);
  }
  function stamp(v) {
    var s = String(v || '').trim().replace(' ', 'T');
    if (!s) return NaN;
    if (!/(Z|[+-]\d\d:?\d\d)$/i.test(s)) s += 'Z'; // created_at_utc 열의 무시간대 값도 UTC다.
    return Date.parse(s);
  }
  function sameDayRank(r) {
    // forecast_utils.same_day_rank와 같은 적용일·저녁 후보 예외. 시가 이후 기록은 우선하지 않는다.
    var day=String(r.prediction_date || r.target_date || ''), t=stamp(r.created_at_utc);
    if (day<'2026-09-28' || String(r.model).indexOf('Candidate evening')===0 || !Number.isFinite(t) ||
        String(r.information_cutoff || 'pre_open')!=='pre_open' || t>=Date.parse(day+'T09:00:00+09:00')) return 1;
    return new Date(t+9*3600*1000).toISOString().slice(0,10)===day ? 0 : 1;
  }
  function prepare(all, model) {
    var excluded = {}, first = new Map();
    function reject(reason) { excluded[reason] = (excluded[reason] || 0) + 1; }
    all.filter(function (r) { return r.kind === 'direction' && r.model === model &&
      (r.horizon_days === undefined || r.horizon_days === '' || number(r.horizon_days) === 1); })
      .slice().sort(function (a,b) {
        // 시각이 잘못된 최초 후보가 있으면 뒤의 정상 행으로 몰래 대체하지 않는다.
        var rank=sameDayRank(a)-sameDayRank(b);
        if (rank) return rank;
        var x=stamp(a.created_at_utc), y=stamp(b.created_at_utc);
        return (Number.isFinite(x) ? x : -Infinity) - (Number.isFinite(y) ? y : -Infinity);
      }).forEach(function (r) {
        if (!first.has(r.target_date)) first.set(r.target_date, r);
        else reject('duplicate');
      });
    var rows=[];
    first.forEach(function (r) {
      var day=String(r.target_date || ''), t=stamp(r.created_at_utc);
      var cutoff=String(r.information_cutoff || 'pre_open');
      var openTime=Date.parse(day+'T09:00:00+09:00');
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !Number.isFinite(t) || !Number.isFinite(openTime)) return reject('invalid_time');
      if (cutoff !== 'pre_open' || !(t < openTime)) return reject('unavailable_at_open');
      if (String(r.is_prospective).toLowerCase() !== 'true' || r.status !== 'scored') return reject('not_scored_prospective');
      if (!(number(r.actual_open)>0) || !Number.isFinite(number(r.actual_open)) ||
          !(number(r.actual_close)>0) || !Number.isFinite(number(r.actual_close))) return reject('missing_price');
      rows.push(r);
    });
    rows.sort(function(a,b) { return a.target_date.localeCompare(b.target_date); });
    return {rows:rows, excluded:excluded};
  }
  function decide(rule, r) {
    if (rule === 'always' || rule === 'buy_hold') return true;
    if (rule === 'predicted_up') return r.prediction === '상승';
    var up=number(r.p_up), down=number(r.p_down);
    if (!Number.isFinite(up) || up<0 || up>1) return false;
    if (rule === 'up_over_third') return up > 1/3;
    return Number.isFinite(down) && down>=0 && down<=1 && up>down;
  }
  function simulate(picked, rule, cost, capital) {
    if (!Number.isFinite(capital) || capital<=0) throw new Error('초기 자금은 0보다 커야 합니다.');
    ['fee','tax','slip'].forEach(function(k) {
      if (!Number.isFinite(cost[k]) || cost[k]<0 || cost[k]>=1) throw new Error('비용은 0% 이상 100% 미만이어야 합니다.');
    });
    if (cost.fee+cost.tax>=1) throw new Error('매도 비용의 합은 100% 미만이어야 합니다.');
    if (['always','buy_hold','predicted_up','up_over_flat','up_over_third'].indexOf(rule)<0) throw new Error('알 수 없는 매매 규칙입니다.');
    var cash=capital, shares=0, curve=[], trades=[], entry=null, costs=0, explicitCosts=0, slipCosts=0;
    var heldDays=0, unfilled=0, previous='', peak=capital, mdd=0;
    function buy(r) {
      var price=number(r.actual_open)*(1+cost.slip);
      var quantity=Math.floor(cash/(price*(1+cost.fee)));
      if (!quantity) { unfilled++; return; }
      var amount=quantity*price, fee=amount*cost.fee;
      var slip=quantity*(price-number(r.actual_open));
      entry={date:r.target_date, open:number(r.actual_open), equity:cash, debit:amount+fee,
        costs:fee+slip, p_up:number(r.p_up), p_down:number(r.p_down), prediction:r.prediction};
      cash-=amount+fee; shares=quantity; explicitCosts+=fee; slipCosts+=slip; costs+=fee+slip;
    }
    function sell(r) {
      var price=number(r.actual_close)*(1-cost.slip), amount=shares*price;
      var charge=amount*(cost.fee+cost.tax), slip=shares*(number(r.actual_close)-price);
      var proceeds=amount-charge, pnl=proceeds-entry.debit;
      cash+=proceeds; explicitCosts+=charge; slipCosts+=slip; costs+=charge+slip;
      trades.push({date:r.target_date, entry_date:entry.date, open:entry.open, close:number(r.actual_close),
        shares:shares, pnl:pnl, gross:shares*(number(r.actual_close)-entry.open)/entry.equity,
        net:pnl/entry.equity, costWon:entry.costs+charge+slip,
        p_up:entry.p_up, p_down:entry.p_down, prediction:entry.prediction});
      shares=0; entry=null;
    }
    picked.forEach(function(r,i) {
      if (r.target_date<=previous || !Number.isFinite(number(r.actual_open)) || number(r.actual_open)<=0 ||
          !Number.isFinite(number(r.actual_close)) || number(r.actual_close)<=0) throw new Error('시세는 날짜순·중복 없이 유효해야 합니다.');
      previous=r.target_date;
      if (rule === 'buy_hold' ? i===0 : decide(rule,r)) buy(r);
      var held=shares>0;
      if (held) heldDays++;
      if (held && (rule!=='buy_hold' || i===picked.length-1)) sell(r);
      var equity=cash+shares*number(r.actual_close);
      peak=Math.max(peak,equity); mdd=Math.min(mdd,equity/peak-1);
      curve.push({date:r.target_date,value:equity/capital,equity:equity,cash:cash,shares:shares,held:held});
    });
    return {curve:curve,trades:trades,total:cash/capital-1,mdd:mdd,fees:costs/capital,costWon:costs,
      explicitCostWon:explicitCosts,slippageWon:slipCosts,finalCash:cash,days:picked.length,heldDays:heldDays,unfilled:unfilled};
  }
  function compareModels(all, rule, cost, capital, scope) {
    if (['available','common'].indexOf(scope)<0) throw new Error('알 수 없는 비교 기간');
    var names=Array.from(new Set(all.filter(function(r){return r.kind==='direction';}).map(function(r){return r.model;}))).sort();
    var prepared=names.map(function(name){return {name:name,rows:prepare(all,name).rows};});
    var active=prepared.filter(function(p){return p.rows.length>0;}), common=[], conflicts=0;
    if(active.length) {
      var maps=active.map(function(p){return new Map(p.rows.map(function(r){return [r.target_date,r];}));});
      active[0].rows.forEach(function(r){
        if(!maps.every(function(m){return m.has(r.target_date);}))return;
        var same=maps.every(function(m){var x=m.get(r.target_date);return number(x.actual_open)===number(r.actual_open)&&number(x.actual_close)===number(r.actual_close);});
        if(same)common.push(r.target_date);else conflicts++;
      });
    }
    var shared=new Set(common);
    return {scope:scope,commonDates:common,priceConflictDays:conflicts,eligibleModels:active.length,
      models:prepared.map(function(p){
        var rows=scope==='common'?p.rows.filter(function(r){return shared.has(r.target_date);}):p.rows;
        return {model:p.name,dates:rows.map(function(r){return r.target_date;}),availableDays:p.rows.length,
                simulation:rows.length?simulate(rows,rule,cost,capital):null,
                hold:rows.length?simulate(rows,'buy_hold',cost,capital):null};
      })};
  }
  return {prepare:prepare,simulate:simulate,decide:decide,compareModels:compareModels};

});

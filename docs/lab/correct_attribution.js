/* 채점된 사전 방향 예측의 기여도 요약. 날짜만으로 결과를 연결하지 않는다. */
(function (root) {
  'use strict';
  function summarize(attrs, ledger) {
    var first = new Map(), groups = new Map(), byRun = new Map();
    var key = function (r, date) { return JSON.stringify([r.run_id, r.model, date]); };
    ledger.filter(function (r) {
      return r.kind === 'direction' && String(r.is_prospective).toLowerCase() === 'true' &&
        r.run_id && r.model && r.target_date;
    }).sort(function (a, b) {
      return String(a.created_at_utc || a.run_id).localeCompare(String(b.created_at_utc || b.run_id)) ||
        String(a.run_id).localeCompare(String(b.run_id));
    }).forEach(function (r) {
      var k = JSON.stringify([r.model, r.target_date]);
      if (!first.has(k)) first.set(k, r);
    });
    attrs.forEach(function (r) {
      if (!r.feature || r.contribution === '' || r.contribution == null) return;
      var c = Number(r.contribution);
      if (!Number.isFinite(c)) return;
      var k = key(r, r.prediction_date);
      if (!byRun.has(k)) byRun.set(k, new Map());
      // 동일 특징의 중복 행은 한 번만 센다.
      if (!byRun.get(k).has(r.feature)) byRun.get(k).set(r.feature, Math.abs(c));
    });
    first.forEach(function (r) {
      if (!groups.has(r.model)) groups.set(r.model, {model:r.model, selected:0, scored:0,
        matched:0, correct:0, dates:[], stats:new Map()});
      var g = groups.get(r.model); g.selected++;
      if (r.status !== 'scored' || !['0','0.0','1','1.0'].includes(String(r.direction_correct))) return;
      g.scored++;
      var features = byRun.get(key(r, r.target_date));
      if (!features || !features.size) return;
      var total = Array.from(features.values()).reduce(function (a,b) {return a+b;},0);
      if (!Number.isFinite(total) || total <= 0) return;
      g.matched++;
      if (Number(r.direction_correct) !== 1) return;
      g.correct++; g.dates.push(r.target_date);
      var max = Math.max.apply(null,Array.from(features.values()));
      features.forEach(function (v,feature) {
        if (!g.stats.has(feature)) g.stats.set(feature,{feature:feature,count:0,topCount:0,sum:0});
        var s=g.stats.get(feature); s.count++; s.sum+=v/total;
        if (v===max) s.topCount++;
      });
    });
    return Array.from(groups.values()).filter(function(g) {
      return attrs.some(function(a){return a.model===g.model;});
    }).sort(function(a,b){return a.model.localeCompare(b.model);}).map(function(g) {
      g.dates.sort(); g.rows=Array.from(g.stats.values()).map(function(s) {
        return {feature:s.feature,count:s.count,topCount:s.topCount,share:s.sum/g.correct};
      }).sort(function(a,b){return b.share-a.share || b.topCount-a.topCount || a.feature.localeCompare(b.feature);});
      delete g.stats; return g;
    });
  }
  if (typeof module !== 'undefined' && module.exports) module.exports=summarize;
  else root.summarizeCorrectAttribution=summarize;
})(typeof window !== 'undefined' ? window : globalThis);

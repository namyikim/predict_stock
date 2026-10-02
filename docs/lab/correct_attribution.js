/* 채점된 사전 예측의 기여도 요약. 날짜만으로 결과를 연결하지 않는다.
 *
 * opts.kind: "direction"(다음 거래일 방향) 또는 "price"(가격), opts.horizon: 거래일 수(1·5·20).
 * 기여도는 (실행 ID, 모델, 예측일)로 원장과 짝을 짓는다. 가격 예측의 원장 target_date 는 만기일이라
 * 예측일(prediction_date)로 맞춘다. 방향 예측은 두 날짜가 같다.
 *
 * '맞힘'의 정의
 *   방향: direction_correct == 1.
 *   가격: 축소 전 원시 예측(raw_predicted_return)의 부호가 실제 수익률의 부호와 같다. 점 예측을 보류한
 *         날도 모델은 값을 냈으므로 그 값으로 잰다. 구간 적중(95~100%)은 거의 모든 날이 맞음이라 쓰지 않는다.
 */
(function (root) {
  'use strict';
  function kindOf(r) { return r.kind ? String(r.kind) : 'direction'; }
  function horizonOf(r) {
    var h = parseInt(r.horizon_days, 10);
    return Number.isFinite(h) ? h : 1;
  }
  // 원장의 예측일. 예전 기록이나 방향 예측처럼 prediction_date 가 없으면 target_date 로 대신한다(방향은 둘이 같다).
  function predictionDate(r) { return String(r.prediction_date || r.target_date || '').slice(0, 10); }
  function correctness(r, kind) {
    if (r.status !== 'scored') return null;
    if (kind === 'direction') {
      if (!['0', '0.0', '1', '1.0'].includes(String(r.direction_correct))) return null;
      return Number(r.direction_correct) === 1;
    }
    var raw = Number(r.raw_predicted_return), actual = Number(r.actual_return);
    if (r.raw_predicted_return === '' || r.actual_return === '' ||
        !Number.isFinite(raw) || !Number.isFinite(actual) || raw === 0 || actual === 0) return null;
    return (raw > 0) === (actual > 0);
  }
  function summarize(attrs, ledger, opts) {
    opts = opts || {};
    var kind = opts.kind || 'direction', horizon = opts.horizon || 1;
    var first = new Map(), groups = new Map(), byRun = new Map();
    var key = function (r, date) { return JSON.stringify([r.run_id, r.model, date]); };
    var wanted = function (r) { return kindOf(r) === kind && horizonOf(r) === horizon; };
    ledger.filter(function (r) {
      return wanted(r) && String(r.is_prospective).toLowerCase() === 'true' &&
        r.run_id && r.model && predictionDate(r);
    }).sort(function (a, b) {
      return String(a.created_at_utc || a.run_id).localeCompare(String(b.created_at_utc || b.run_id)) ||
        String(a.run_id).localeCompare(String(b.run_id));
    }).forEach(function (r) {
      var k = JSON.stringify([r.model, predictionDate(r)]);
      if (!first.has(k)) first.set(k, r);
    });
    var picked = attrs.filter(wanted);
    picked.forEach(function (r) {
      if (!r.feature || r.contribution === '' || r.contribution == null) return;
      var c = Number(r.contribution);
      if (!Number.isFinite(c)) return;
      var k = key(r, predictionDate(r));
      if (!byRun.has(k)) byRun.set(k, new Map());
      // 동일 특징의 중복 행은 한 번만 센다.
      if (!byRun.get(k).has(r.feature)) byRun.get(k).set(r.feature, Math.abs(c));
    });
    first.forEach(function (r) {
      if (!groups.has(r.model)) groups.set(r.model, {model: r.model, kind: kind, horizon: horizon,
        selected: 0, scored: 0, matched: 0, correct: 0, pending: 0, pendingFrom: '', dates: [], stats: new Map()});
      var g = groups.get(r.model); g.selected++;
      var ok = correctness(r, kind);
      if (ok === null) {
        // 기여도는 남아 있지만 아직 채점 전인 예측(2026-10-02): 비어 있는 이유와 언제 채워지는지 알리려고 센다.
        // 5·20거래일 종가는 만기가 지나야 채점되므로 한동안 '기록은 있는데 요약은 빈' 상태가 이어진다.
        var waiting = byRun.get(key(r, predictionDate(r)));
        if (r.status !== 'scored' && waiting && waiting.size) {
          g.pending++;
          if (!g.pendingFrom || predictionDate(r) < g.pendingFrom) g.pendingFrom = predictionDate(r);
        }
        return;
      }
      g.scored++;
      var features = byRun.get(key(r, predictionDate(r)));
      if (!features || !features.size) return;
      var total = Array.from(features.values()).reduce(function (a, b) { return a + b; }, 0);
      if (!Number.isFinite(total) || total <= 0) return;
      g.matched++;
      if (!ok) return;
      g.correct++; g.dates.push(predictionDate(r));
      var max = Math.max.apply(null, Array.from(features.values()));
      features.forEach(function (v, feature) {
        if (!g.stats.has(feature)) g.stats.set(feature, {feature: feature, count: 0, topCount: 0, sum: 0});
        var s = g.stats.get(feature); s.count++; s.sum += v / total;
        if (v === max) s.topCount++;
      });
    });
    return Array.from(groups.values()).filter(function (g) {
      return picked.some(function (a) { return a.model === g.model; });
    }).sort(function (a, b) { return a.model.localeCompare(b.model); }).map(function (g) {
      g.dates.sort(); g.rows = Array.from(g.stats.values()).map(function (s) {
        return {feature: s.feature, count: s.count, topCount: s.topCount, share: s.sum / g.correct};
      }).sort(function (a, b) { return b.share - a.share || b.topCount - a.topCount || a.feature.localeCompare(b.feature); });
      delete g.stats; return g;
    });
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = summarize;
  else root.summarizeCorrectAttribution = summarize;
})(typeof window !== 'undefined' ? window : globalThis);

const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
assert.ok(fs.existsSync('docs/traffic_attribution.js'),'도착 페이지 유입 모듈 필요');
const api=require('../docs/traffic_attribution.js');
const endpoint='https://counter.example', q='?utm_source=email&utm_medium=report&edition_day=2026-10-07&edition_phase=pre_open';
const plain=endpoint+'/hit?page=samsung';
const good=api.hitUrl(endpoint,'samsung',q+'&email=private%40example.com&token=secret');
const u=new URL(good);assert.equal(u.searchParams.get('page'),'samsung');
assert.equal(u.searchParams.get('utm_source'),'email');assert.equal(u.searchParams.get('edition_day'),'2026-10-07');
assert.equal(u.searchParams.size,5);assert.ok(!good.includes('private')&&!good.includes('secret'));
for(const bad of ['',q+'&utm_source=email',q+'&edition_day=2026-10-08',q.replace('2026-10-07','2026-02-30'),q.replace('2026-10-07','0000-01-01'),q.replace('pre_open','<script>'),q.replace('report','z'.repeat(2000))])assert.equal(api.hitUrl(endpoint,'samsung',bad),plain);
assert.equal(api.hitUrl(endpoint,'삼성 & /',''),endpoint+'/hit?page='+encodeURIComponent('삼성 & /'));
const fixtures=JSON.parse(fs.readFileSync(0,'utf8'));
for(const fixture of fixtures){
 assert.ok(fixture.html.includes('<script src="/predict_stock/traffic_attribution.js"></script>'),fixture.name);
 assert.ok(fixture.html.indexOf('traffic_attribution.js')<fixture.html.indexOf('fetch('),fixture.name);
 for(const mode of ['loaded','missing','direct','no-element']){
  const calls=[],ctx={URL,URLSearchParams,Date,location:{search:mode==='direct'?'':q},
    document:{getElementById:()=>mode==='no-element'?null:{textContent:''}},
    fetch:url=>{calls.push(url);return Promise.resolve({ok:true,json:()=>Promise.resolve({total:1})});}};
  if(mode!=='missing')ctx.TrafficAttribution=api;
  vm.createContext(ctx);
  for(const s of fixture.html.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(s[1],ctx);
  assert.equal(calls.length,mode==='no-element'?0:1,fixture.name+'/'+mode);
  if(calls.length){const out=new URL(calls[0]);assert.equal(out.searchParams.get('page'),fixture.page);assert.equal(out.searchParams.get('utm_source'),['direct','missing'].includes(mode)?null:'email');}
 }
}
console.log('전체 생성 경로·한 번 요청·모듈 실패 시 기존 집계·개인 쿼리 제외 검증 통과');

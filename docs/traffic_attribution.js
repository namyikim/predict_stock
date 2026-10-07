/* 도착 URL의 이메일 회차만 기존 방문 요청에 전달한다. 저장·메뉴 전파는 하지 않는다. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.TrafficAttribution=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  function hitUrl(endpoint,page,search){
    const original=endpoint+'/hit?page='+encodeURIComponent(page);
    try {
      const p=new URLSearchParams(search);
      const keys=['utm_source','utm_medium','edition_day','edition_phase'];
      if(keys.some(k=>p.getAll(k).length!==1))return original;
      const day=p.get('edition_day'),phase=p.get('edition_phase');
      if(p.get('utm_source')!=='email'||p.get('utm_medium')!=='report'||
         !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(day)||day.startsWith('0000')||
         !['pre_open','post_close'].includes(phase))return original;
      const stamp=Date.parse(day+'T00:00:00Z');
      if(!Number.isFinite(stamp)||new Date(stamp).toISOString().slice(0,10)!==day)return original;
      const result=new URL(original);
      keys.forEach(k=>result.searchParams.set(k,p.get(k)));
      return result.toString();
    } catch {return original;}
  }
  return {hitUrl};
});

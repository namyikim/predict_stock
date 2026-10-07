/* 이메일 유입은 집계치만 표시한다. 개인 계정·정확한 위치를 추정하지 않는다. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.EmailTraffic=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const pages={main:'메인',samsung:'삼성전자',sk_hynix:'SK하이닉스',metals:'금·은',china:'중국 주식',macro:'거시 경제',ai_news:'AI 뉴스',robot_news:'로봇 뉴스',trends:'급상승 검색어',interest:'장기 관심도'};
  const devices={mobile:'모바일',tablet:'태블릿',desktop:'PC',unknown:'알 수 없음'};
  const countries={KR:'대한민국',US:'미국',JP:'일본',CN:'중국',unknown:'알 수 없음'};
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const count=x=>Number.isFinite(Number(x))&&Number(x)>0?Math.floor(Number(x)):0;
  const num=x=>count(x).toLocaleString('ko-KR');
  const label=(map,key)=>Object.prototype.hasOwnProperty.call(map,key)?map[key]:key;
  function distribution(title,rows,key,names){
    if(!Array.isArray(rows))return '<h3>'+esc(title)+'</h3><p class="hint">추가 통계 미적용 — Worker를 업데이트하세요.</p>';
    const total=rows.reduce((n,r)=>n+count(r.views),0);
    return '<h3>'+esc(title)+'</h3>'+rows.map(r=>{
      const ratio=total?count(r.views)/total*100:0;
      return '<div class="email-bar"><div><span>'+esc(label(names,r[key]))+'</span><b>'+num(r.views)+'회 · '+ratio.toFixed(1)+'%</b></div><div class="email-track"><span style="width:'+ratio.toFixed(2)+'%"></span></div></div>';
    }).join('');
  }
  function table(headers,rows){
    return '<div class="email-table"><table><thead><tr>'+headers.map(h=>'<th>'+esc(h)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(v=>'<td>'+esc(v)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
  }
  function render(data,periodViews){
    if(!data||data.available!==true)return '<p class="empty">이메일 유입 집계 미적용 — D1 이행과 Worker 배포를 확인하세요.</p>';
    if(!Array.isArray(data.daily)||!Array.isArray(data.editions))return '<p class="empty">이메일 유입 응답이 불완전합니다. Worker를 업데이트하세요.</p>';
    const total=data.daily.reduce((n,r)=>n+count(r.views),0);
    let html='<div class="tiles"><div class="tile"><div class="k">이메일 유입 방문수</div><div class="v">'+num(total)+'회</div><div class="n">선택한 기간 기준</div></div><div class="tile"><div class="k">전체 조회 중 비중</div><div class="v">'+(count(periodViews)?(total/count(periodViews)*100).toFixed(1)+'%':'—')+'</div><div class="n">메일 발송 대비 클릭률이 아닙니다</div></div></div>';
    html+='<p class="hint">방문일은 UTC 기준, 발행 회차일은 메일에 표시된 한국 날짜입니다. 같은 방문자·페이지의 10분 내 방문은 한 건으로 셉니다. 과거 출처 미분류 방문은 이메일 유입으로 복원하지 않습니다. 링크 공유·자동 검사로 실제 클릭 수와 다를 수 있습니다.</p>';
    if(!total)return html+'<p class="empty">선택한 기간에 이메일 유입 기록이 없습니다.</p>';
    const byPage=new Map();data.daily.forEach(r=>byPage.set(r.page,(byPage.get(r.page)||0)+count(r.views)));
    html+=distribution('보고서별 방문',Array.from(byPage,([page,views])=>({page,views})).sort((a,b)=>b.views-a.views),'page',pages);
    html+='<div class="email-grid"><section>'+distribution('국가별 방문',data.countries,'country',countries)+'</section><section>'+distribution('기기별 방문',data.devices,'device',devices)+'</section></div>';
    html+='<p class="hint">국가는 IP 기반 추정, 기기는 브라우저 정보 기반 추정입니다. VPN·브라우저 설정 등에 따라 실제와 다를 수 있습니다. 구독자 계정·정확한 위치와 연결하지 않습니다.</p>';
    html+='<details><summary>방문일·보고서별 내역 (UTC)</summary>'+table(['방문일 (UTC)','보고서','방문수'],data.daily.map(r=>[r.day,label(pages,r.page),num(r.views)]))+'</details>';
    html+='<details><summary>발행 회차별 내역</summary><p class="hint">선택한 방문 기간에 들어온 방문을 메일 발행 회차별로 묶었습니다. 발행일이 조회 기간보다 앞설 수 있습니다.</p>'+table(['발행 회차일 (한국)','구분','보고서','방문수'],data.editions.map(r=>[r.edition_day,label({pre_open:'개장 전',post_close:'마감 후'},r.edition_phase),label(pages,r.page),num(r.views)]))+'</details>';
    return html;
  }
  return {render};
});

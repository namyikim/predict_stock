/* 파동 연구 평가와 관측 장부 표시. 미확정 자료를 0% 성과로 바꾸지 않는다. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WaveView=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const names={range_rebound:'박스권 반등',trend_pullback:'상승 추세 눌림목',buy_hold:'매수 후 보유',cash:'현금 유지',legacy:'기존 사전 예측 규칙'};
  const colors=['#1a5490','#985b19','#567965','#808080','#8b5f9e'];
  const reasons={missing_common_prices:'공통 평가 가격 자료 부족',unverified_cash_price_basis:'현금 체결용 가격 기준 미검증',corporate_action_unresolved:'분할·배당 조정 미확인',untradeable_session:'거래 불가 또는 거래량 없음',missing_original_forecasts:'기존 사전 예측 원장 부족',original_forecast_price_mismatch:'원장과 가격 자료 불일치',no_completed_daily:'완료 일봉 없음',stale_daily:'최신 완료 일봉 부족',archive_error:'시세 보관 자료 읽기 오류',observe_only:'관측 전용',insufficient_bars:'지표를 계산할 일봉 부족',zero_range:'가격 범위 없음',not_sideways:'박스권 조건 아님',outside_lower_range:'범위 하단 조건 아님',not_uptrend:'상승 추세 조건 아님',no_pullback:'눌림목 미확인',average_not_recovered:'평균 가격 미회복',rebound_unconfirmed:'반등 미확인',range_rebound_confirmed:'박스권 반등 조건 충족',trend_pullback_confirmed:'눌림목 회복 조건 충족',next_open:'다음 거래일 시가',max_holding:'최대 보유 기간 종료',stop:'손절 기준 도달',stop_gap:'시가가 손절 기준 아래',stop_before_target_assumption:'목표·손절 동시 도달 → 손절 우선 가정',target:'목표 기준 도달',target_gap_conservative:'상승 갭 → 목표 가격 적용',delayed_exit:'시세 확보 후 지연 청산',decision_after_open:'시가 이후 관측으로 진입 제외',reward_below_cost_buffer:'비용 대비 예상 보상 부족',ending_position_not_liquidated:'기간 말 미청산 보유',stale_quote:'분봉 지연',missing_quote:'분봉 없음',cash_or_exposure_limit:'현금·투자 한도 제한',reentry_cooldown:'재진입 대기',entry_at_or_above_target:'시가가 목표 이상',stop_not_below_entry:'손절 기준과 진입가 역전'};
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  const pct=v=>finite(v)?(v*100).toFixed(2)+'%':'—';
  const won=v=>finite(v)?Math.round(v).toLocaleString('ko-KR')+'원':'—';
  const reason=v=>esc(reasons[v]||v||'미기록');
  const warning=text=>'<p class="warn">'+esc(text)+'</p>';
  function time(value){if(!value)return '시각 미확인';const d=new Date(value);return Number.isFinite(d.getTime())?esc(d.toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}))+' KST':'시각 미확인';}
  function old(value,now){return !Number.isFinite(Date.parse(value))||Date.parse(value)>now+300000||now-Date.parse(value)>48*3600000;}
  function lineChart(series,title,unit,markers=[],includeZero=true){
    series=series.filter(s=>s.points.length);
    const dates=Array.from(new Set(series.flatMap(s=>s.points.map(p=>p.date)))).sort();
    if(dates.length<2)return '<p class="muted">'+esc(title)+': 표시할 자료가 2일 미만입니다.</p>';
    const values=series.flatMap(s=>s.points.map(p=>p.value)).concat(markers.map(p=>p.value));
    if(includeZero)values.push(0);
    let lo=Math.min(...values),hi=Math.max(...values);if(hi===lo){lo-=1;hi+=1;}
    const W=760,H=200,L=76,R=16,T=16,B=32;
    const x=date=>L+(W-L-R)*dates.indexOf(date)/(dates.length-1), y=v=>T+(H-T-B)*(hi-v)/(hi-lo);
    const display=v=>v.toLocaleString('ko-KR',{maximumFractionDigits:2})+unit;
    const lines=series.map(s=>'<polyline fill="none" stroke="'+s.color+'" stroke-width="2" points="'+s.points.map(p=>x(p.date).toFixed(2)+','+y(p.value).toFixed(2)).join(' ')+'"/>').join('');
    const marks=markers.filter(m=>dates.includes(m.date)).map(m=>'<circle cx="'+x(m.date).toFixed(2)+'" cy="'+y(m.value).toFixed(2)+'" r="4" fill="'+m.color+'"><title>'+esc(m.label)+'</title></circle>').join('');
    return '<figure class="wave-chart"><figcaption>'+esc(title)+'</figcaption><svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="'+esc(title)+'"><title>'+esc(title)+' · '+esc(dates[0])+' ~ '+esc(dates.at(-1))+'</title>'+[lo,(lo+hi)/2,hi].map(v=>'<line x1="'+L+'" x2="'+(W-R)+'" y1="'+y(v)+'" y2="'+y(v)+'" stroke="#e4e9ee"/><text x="'+(L-5)+'" y="'+(y(v)+4)+'" text-anchor="end" font-size="12">'+esc(display(v))+'</text>').join('')+lines+marks+
      '<text x="'+L+'" y="'+(H-6)+'" font-size="12">'+esc(dates[0])+'</text><text x="'+(W-R)+'" y="'+(H-6)+'" text-anchor="end" font-size="12">'+esc(dates.at(-1))+'</text></svg><p class="muted">'+series.map(s=>'<span style="color:'+s.color+'">● '+esc(s.name)+'</span>').join(' · ')+'</p></figure>';
  }
  function paperView(paper,validation,target,now){
    if(!paper)return '<h3>신호 관측 전용 기록</h3>'+warning('관측 기록 없음 · 다음 정상 자동 실행 뒤 생성됩니다.');
    if(paper.schema_version!==1||paper.mode!=='observe_only'||!Array.isArray(paper.strategies))return warning('지원하지 않는 관측 자료 형식');
    let html='<h3>신호 관측 전용 기록</h3><p>기록 시각: '+time(paper.generated_at)+' · 주문·체결을 만들지 않는 단계입니다.</p>';
    if(old(paper.generated_at,now))html+=warning('오래된 관측 자료 또는 자료 시각 미확인 · 최신 실행을 확인하세요.');
    html+=paper.strategies.map(s=>{
      const expected=validation?.config?.strategies?.[s.strategy]?.version;
      if(!expected||s.strategy_version!==expected)return warning('관측 전략 버전 불일치 또는 버전 미기록: '+(names[s.strategy]||s.strategy));
      if(!/^[a-f0-9]{64}$/.test(s.config_hash||'')||s.config_hash!==validation?.paper_config_hashes?.[s.strategy])return warning('관측 설정·출처 불일치 또는 설정 해시 미기록: '+(names[s.strategy]||s.strategy));
      if(s.mode!=='observe_only'||s.order_count!==0||s.fill_count!==0)return warning('관측 전용 계약과 다른 자료: '+(names[s.strategy]||s.strategy));
      const observations=(s.observations||[]).filter(o=>o.target===target);
      return '<div class="tile"><b>'+esc(names[s.strategy]||s.strategy)+'</b><p>관측 '+(Number.isInteger(s.observed_days)?esc(s.observed_days)+'일':'일수 미기록')+' · 최초 '+time(s.first_observed_at)+'</p><p>누적 판단(두 종목) '+esc(s.decision_count)+'건 · 체결 0건 · 수익 평가 전</p>'+observations.map(o=>'<p>'+reason(o.status)+(o.signal?' · '+(o.signal.reason_codes||[]).map(reason).join(' / '):'')+'</p>'+(o.risk_preview?'<p class="muted">한도 점검: '+(o.risk_preview.reasons.length?o.risk_preview.reasons.map(reason).join(' / '):'조건 내 · 주문 아님')+'</p>':'')).join('')+'</div>';
    }).join('');
    if(paper.collection_errors?.length)html+=warning('수집 오류: '+paper.collection_errors.join(' · '));
    return html;
  }
  function renderWaveComparison(validation,paperStatus,options={}){
    const now=options.now??Date.now(),target=options.target??validation?.target,scenario=options.scenario==='double_cost'?'double_cost':'standard';
    if(!validation)return warning('평가 자료를 불러오지 못했습니다. 자동 평가 파일 생성 여부를 확인하세요.')+paperView(paperStatus,null,target,now);
    if(validation.schema_version!==1||validation.method!=='observed_time_retrospective_split'||validation.automatic_promotion!==false)return warning('지원하지 않는 평가 자료 형식');
    if(validation.target!==target)return warning('평가 자료 종목 불일치');
    if(['input_sha256','engine_sha256','config_sha256'].some(k=>!/^[a-f0-9]{64}$/.test(validation[k]||'')))return warning('평가 자료 출처 확인 불가');
    if(['range_rebound','trend_pullback'].some(k=>validation.config?.strategies?.[k]?.version!=='wave-v1'))return warning('평가 전략 버전 불일치');
    if(!['insufficient','minimum_sample_met_not_profit_proof'].includes(validation.evidence))return warning('평가 근거 확인 불가 · 자료 갱신이 필요합니다.');
    const ev=validation.evaluation,c=validation.config,capital=c.paper.capital;
    if(!ev||!Array.isArray(ev.dates)||!ev[scenario])return warning('지원하지 않는 평가 항목');
    let html='<div class="wave-view"><p class="warn"><b>과거자료 연구 평가 · 실제 계좌 수익 아님</b><br>자동 승격 없음. 아래 신호 관측 기록과 별도로 계산합니다.</p>';
    if(old(validation.generated_at_utc,now))html+=warning('오래된 평가 자료 또는 자료 시각 미확인 · 현재 가격으로 해석하지 마세요.');
    html+='<p><b>평가 기간: '+esc(ev.dates[0]||'없음')+' ~ '+esc(ev.dates.at(-1)||'없음')+'</b> · '+ev.dates.length+'거래일</p><p class="muted">생성 '+time(validation.generated_at_utc)+' · 초기 자금 '+won(capital)+' · 종목 한도 '+pct(c.paper.max_symbol_weight)+' · '+(scenario==='double_cost'?'거래 비용 2배':'기준 거래 비용')+'</p>';
    html+=warning(validation.evidence==='insufficient'?'자료 부족 · 평가 60거래일·완료 거래 20건 기준 미충족 또는 비교 자료 없음':'최소 표본 충족 · 수익성 입증을 뜻하지 않습니다.');
    if(ev.status!=='available')html+='<p class="empty">평가 보류: '+(ev.reasons||[]).map(reason).join(' · ')+'</p>';
    const ids=Object.keys(names),results=ev[scenario];
    html+='<div class="wave-table-scroll" tabindex="0" role="region" aria-label="전략 성과 비교표"><table><thead><tr><th>전략</th><th>기간 순수익률</th><th>순손익</th><th>최대 하락폭</th><th>왕복 거래</th><th>총 비용</th></tr></thead><tbody>'+ids.map(id=>{
      const r=results[id],m=r?.metrics;
      if(ev.status!=='available'||!finite(m?.total))return '<tr><th scope="row">'+names[id]+'</th><td colspan="5">미확정 · '+(r?.limitations||['자료 없음']).map(reason).join(' / ')+'</td></tr>';
      return '<tr><th scope="row">'+names[id]+(validation.selected===id?' · 선택 기간 후보':'')+'</th><td>'+pct(m.total)+'</td><td>'+won(m.total*capital)+'</td><td>'+pct(m.mdd)+'</td><td>'+esc(m.closed_trades)+'건'+(m.closed_trades===0?' · 거래 없음':'')+'</td><td>'+won(m.cost_won)+'</td></tr>';
    }).join('')+'</tbody></table></div>';
    if(ev.status==='available'){
      const series=ids.filter(id=>finite(results[id]?.metrics?.total)).map((id,i)=>({name:names[id],color:colors[i],points:(results[id].equity_curve||[]).filter(p=>finite(p.equity)).map(p=>({date:p.session,value:(p.equity/capital-1)*100}))}));
      html+=lineChart(series,'누적 모의 수익률','%');
      const falls=ids.filter(id=>finite(results[id]?.metrics?.total)).map((id,i)=>{let peak=capital;return {name:names[id],color:colors[i],points:(results[id].equity_curve||[]).filter(p=>finite(p.equity)).map(p=>{peak=Math.max(peak,p.equity);return {date:p.session,value:(p.equity/peak-1)*100};})};});
      html+=lineChart(falls,'이전 최고 평가액 대비 하락폭','%');
      const chosen=names[options.strategy]?options.strategy:(validation.selected||'range_rebound'),r=results[chosen];
      html+='<h3>가격과 판단·체결 · '+names[chosen]+'</h3>';
      const prices=(ev.prices||[]).filter(p=>finite(p.close)),priceMap=new Map(prices.map(p=>[p.session,p.close]));
      const timing={open:'장 시작',close:'장 마감',intrabar_unknown:'봉 내부 · 정확한 시각 미상'};
      const markers=(r?.fills||[]).filter(f=>finite(f.price)).map(f=>({date:f.session,value:f.price,color:f.side==='buy'?'#087d51':'#b34040',label:f.session+' '+(f.side==='buy'?'모의 진입':'모의 청산')+' · '+(timing[f.timing]||'시각 미확인')}));
      for(const d of r?.decisions||[]){const date=(d.signal_bar_end||'').slice(0,10);if(priceMap.has(date))markers.push({date,value:priceMap.get(date),color:'#8b5f9e',label:date+' 판단 · '+(d.action==='enter'?'조건 충족':'관망')});}
      html+=prices.length?lineChart([{name:'종가',color:'#1a5490',points:prices.map(p=>({date:p.session,value:p.close}))}],'종가와 모의 거래 기록','원',markers,false):'<p class="muted">검증된 가격 차트 자료가 없습니다.</p>';
      html+='<p class="muted">초록: 모의 진입 · 빨강: 모의 청산 · 보라: 판단. 정확한 봉 내부 체결 시각은 알 수 없습니다. 아래 기록에서 시각·이유를 확인하세요.</p>';
      const records=(r?.fills||[]).map(f=>({date:f.session,type:f.side==='buy'?'모의 진입':'모의 청산',time:timing[f.timing]||'시각 미확인',detail:won(f.price)+' × '+esc(f.qty)+'주 · 수수료·세금 '+won((f.fee||0)+(f.tax||0)),why:reason(f.reason)}))
        .concat((r?.decisions||[]).map(d=>({date:(d.signal_bar_end||d.decision_at||'').slice(0,10),type:d.action==='enter'?'조건 충족':'관망',time:time(d.decision_at),detail:'체결 아님',why:(d.reason_codes||[]).map(reason).join(' / ')})))
        .concat((r?.orders||[]).filter(o=>o.status!=='filled').map(o=>({date:o.session,type:'미체결·보류',time:o.decision_at?time(o.decision_at):'일 단위 기록',detail:esc(o.status),why:reason(o.reason)}))).sort((a,b)=>a.date.localeCompare(b.date));
      html+='<details><summary>판단·체결 근거 기록 '+records.length+'건 (최근 100건)</summary><div class="wave-table-scroll" tabindex="0"><table><tr><th>날짜</th><th>구분</th><th>기록 시각</th><th>가격·비용</th><th>이유</th></tr>'+records.slice(-100).map(x=>'<tr><td>'+esc(x.date)+'</td><td>'+x.type+'</td><td>'+x.time+'</td><td>'+x.detail+'</td><td>'+x.why+'</td></tr>').join('')+'</table></div></details>';
      const m=r?.metrics||{},uncertainty=r?.uncertainty?.interval;
      html+='<details><summary>선택 전략의 비용·위험 지표 설명</summary><p>평균 순손익 '+won(m.average_net_pnl)+' · 투자한 날짜 비율 '+pct(m.exposure_fraction)+' · 평균 보유 '+(finite(m.mean_holding_sessions)?m.mean_holding_sessions.toFixed(1)+'거래일':'—')+' · 미체결 비율 '+pct(m.unfilled_rate)+'</p><p>날짜 블록 재표본 탐색 구간: '+(uncertainty?pct(uncertainty[0])+' ~ '+pct(uncertainty[1]):'산출 자료 부족')+' · 미래 수익 보장이나 전략 선택 불확실성을 반영한 구간이 아닙니다.</p></details>';
    }
    html+=paperView(paperStatus,validation,target,now)+'<details><summary>계산 기준·출처 확인</summary><p>파동 하단은 미래 저점 예측이 아니라 과거 가격 범위 안의 상대 위치입니다. 하락폭은 관측일 종가 기준이며 실제 손실 한도를 보장하지 않습니다.</p><p>선택 '+esc(c.validation.selection.start)+' ~ '+esc(c.validation.selection.end)+' · 평가 전에 규칙 고정 · 비용은 가정값입니다.</p><p>기존 사전 예측 규칙은 시가~종가 재계산이며 자동 모의계좌의 체결 실적이 아닙니다.</p><p>입력 '+esc(validation.input_sha256)+'<br>설정 '+esc(validation.config_sha256)+'<br>코드 '+esc(validation.engine_sha256)+'</p></details></div>';
    return html;
  }
  return {renderWaveComparison};
});

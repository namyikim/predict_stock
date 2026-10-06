"""일봉 OHLC 연구 재생. 운영 원장·실주문과 분리하며 관측 시각을 소급하지 않는다."""
import math
from .paper_trading import instant
from .paper_market_data import _session_bounds
from .wave_signals import wave_signal


def simulate_wave(bars, *, config, capital):
    """관측된 신호 다음 세션 시가 진입을 가정한다. 실제 체결·수익을 보증하지 않는다."""
    if isinstance(capital,bool) or not math.isfinite(capital) or capital<=0:
        raise ValueError('양의 초기 자금 필요')
    execution=config['execution']
    fee,tax,slip=(execution[k] for k in ('fee','tax','slip'))
    if any(isinstance(v,bool) or not math.isfinite(v) or v<0 or v>=1 for v in (fee,tax,slip)) or fee+tax>=1:
        raise ValueError('비용 범위 오류')
    if execution.get('max_holding_sessions')!=5:
        raise ValueError('wave-v1은 최대 5거래일 보유')
    weight=execution.get('position_weight',1.0)
    if isinstance(weight,bool) or not isinstance(weight,(int,float)) or not 0<weight<=1:raise ValueError('배분 한도 오류')
    trade_start=execution.get('trade_start','0001-01-01')
    entry_deadline=execution.get('entry_deadline','9999-12-31')
    wave_signal([],decision_at='2000-01-01T00:00:00+00:00',config=config)
    rows=list(bars)
    if len({(r['target'],r['timeframe']) for r in rows})>1:
        raise ValueError('종목·시간대 혼합 금지')
    first={}
    for r in rows:
        if r['timeframe']!='1d' or r['adjustment']!='unadjusted':
            raise ValueError('현금 체결용 비조정 일봉이 검증되어야 한다')
        start,end,observed=(instant(r[k]) for k in ('bar_start','bar_end','observed_at'))
        bounds=_session_bounds(r['session'])
        if bounds!=(start,end) or observed<end:
            raise ValueError('XKRX 완료 일봉·수집 시각 오류')
        values=[r[k] for k in ('open','high','low','close','volume')]
        if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in values):
            raise ValueError('유효한 OHLCV 필요')
        o,h,l,c,v=values
        if min(o,h,l,c)<=0 or v<0 or not l<=min(o,c)<=max(o,c)<=h:
            raise ValueError('OHLCV 범위 오류')
        old=first.get(r['session'])
        if old is None or observed<instant(old['observed_at']):first[r['session']]=r
        elif observed==instant(old['observed_at']) and old!=r:
            raise ValueError('동일 관측의 상충 가격')
    import exchange_calendars as xcals
    sessions=[] if not first else [str(d.date()) for d in xcals.get_calendar('XKRX').sessions_in_range(min(first),max(first))]
    decisions,orders,fills,curve=[],[],[],[]
    limitations=['research_ohlc_execution_not_live','intrabar_order_unknown_stop_first',
                 'first_observed_price_snapshot','no_backdated_observations',
                 'no_partial_take_profit_or_trailing_stop']
    cash=float(capital);qty=0;position=None;pending=None;history=[]
    costs=0.;peak=float(capital);mdd=0.;last_price=None;invalid=False;stale=False;closed=0;wins=0

    def sell(bar,reference,reason,timing):
        nonlocal cash,qty,position,costs,closed,wins
        price=reference*(1-slip);amount=qty*price
        charge,levy=amount*fee,amount*tax
        pnl=amount-charge-levy-position['debit']
        fills.append(dict(side='sell',session=bar['session'],bar_start=bar['bar_start'],bar_end=bar['bar_end'],
                          timing=timing,reference_price=reference,price=price,qty=qty,fee=charge,tax=levy,
                          reason=reason,pnl=pnl))
        orders.append(dict(side='sell',session=bar['session'],status='filled',reason=reason))
        costs+=charge+levy+qty*(reference-price);cash+=amount-charge-levy
        closed+=1;wins+=int(pnl>0);qty=0;position=None

    for i,day in enumerate(sessions):
        bar=first.get(day)
        active=day>=trade_start
        if not active or day>entry_deadline:pending=None
        if bar is not None and bar['requires_corporate_action_adjustment']:
            history=[];pending=None
            if position:
                invalid=True;limitations.append('corporate_action_unresolved')
                orders.append(dict(side='sell',session=day,status='blocked',reason='corporate_action_unresolved'))
                curve.append(dict(session=day,equity=None,stale=True));break
            continue
        unavailable='missing_bar' if bar is None else ('zero_volume' if bar['volume']==0 else None)
        if unavailable:
            history=[]
            if pending:orders.append(dict(side='buy',session=day,status='cancelled',reason=unavailable));pending=None
            if position:
                orders.append(dict(side='sell',session=day,status='delayed',reason=unavailable))
                position['delayed']=True
            stale=bool(position)
            if active:curve.append(dict(session=day,equity=cash+qty*(last_price or 0),stale=stale))
            continue
        stale=False
        if pending:
            signal=pending;pending=None
            reference=bar['open'];price=reference*(1+slip);stop=signal['reference_stop']
            target=((signal['metrics']['range_low']+signal['metrics']['range_high'])/2
                    if config['strategy']=='range_rebound' else price+2*(price-stop))
            rejection=None
            if instant(signal['decision_at'])>=instant(bar['bar_start']):rejection='decision_after_open'
            elif stop is None or stop>=reference:rejection='stop_not_below_entry'
            elif reference>=target or price>=target:rejection='entry_at_or_above_target'
            # 원시 목표 차익과 왕복 비용을 같은 1주 단위로 비교한다.
            round_cost=(price-reference)+price*fee+target*slip+target*(1-slip)*(fee+tax)
            if rejection is None and target-reference<=2*round_cost:rejection='reward_below_cost_buffer'
            quantity=math.floor(cash*weight/(price*(1+fee)))
            if rejection is None and quantity<1:rejection='insufficient_cash'
            orders.append(dict(side='buy',session=day,decision_at=signal['decision_at'],
                               status='rejected' if rejection else 'filled',reason=rejection or 'next_open'))
            if rejection is None:
                amount=quantity*price;charge=amount*fee;cash-=amount+charge;qty=quantity
                costs+=charge+qty*(price-reference)
                position=dict(stop=stop,target=target,entry_index=i,debit=amount+charge,delayed=False)
                fills.append(dict(side='buy',session=day,timing='open',bar_start=bar['bar_start'],bar_end=bar['bar_end'],
                                  reference_price=reference,price=price,qty=qty,fee=charge,tax=0,reason='next_open'))
        sold=False
        if position:
            stop,target=position['stop'],position['target']
            if position['delayed']:
                sell(bar,bar['open'],'delayed_exit','open');sold=True
            elif bar['open']<=stop:
                sell(bar,bar['open'],'stop_gap','open');sold=True
            elif bar['open']>=target:
                sell(bar,target,'target_gap_conservative','open');sold=True
            elif bar['low']<=stop:
                reason='stop_before_target_assumption' if bar['high']>=target else 'stop'
                sell(bar,stop,reason,'intrabar_unknown');sold=True
            elif bar['high']>=target:
                sell(bar,target,'target','intrabar_unknown');sold=True
            elif i-position['entry_index']+1>=5:
                sell(bar,bar['close'],'max_holding','close');sold=True
        last_price=bar['close'];equity=cash+qty*last_price
        peak=max(peak,equity);mdd=min(mdd,equity/peak-1)
        if active:curve.append(dict(session=day,equity=equity,stale=False))
        history.append(bar)
        if not position and not sold:
            # 마지막 날 신호도 기록하지만 미래 봉이 없으면 체결하지 않는다.
            signal=wave_signal(history,decision_at=bar['observed_at'],config=config)
            if active:decisions.append(signal)
            if signal['action']=='enter':pending=signal
    if position:limitations.append('ending_position_not_liquidated')
    if stale:limitations.append('ending_valuation_stale')
    final=cash+qty*(last_price or 0)
    valid=bool(curve) and not invalid and not stale
    return dict(mode='research_only',decisions=decisions,orders=orders,fills=fills,equity_curve=curve,
                metrics=dict(total=final/capital-1 if valid else None,final_equity=final if valid else None,
                             cash=cash,open_qty=qty,closed_trades=closed,winning_trades=wins,
                             cost_won=costs,mdd=mdd if valid else None,observed_sessions=len(first),
                             start=sessions[0] if sessions else None,end=sessions[-1] if sessions else None),
                limitations=limitations)

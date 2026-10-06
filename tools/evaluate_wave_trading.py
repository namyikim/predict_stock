"""기간 분리 파동 전략 연구 평가. 가격·원장 검증이 안 되면 성과를 만들지 않는다."""
import argparse
import copy
import csv
import hashlib
import json
import random
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from tools.paper_trading import instant
from tools.paper_market_data import read_archive
from tools.wave_backtest import simulate_wave
from tools.run_wave_paper import paper_config_hash

ROOT=Path(__file__).resolve().parents[1]
NAMES=('range_rebound','trend_pullback','buy_hold','cash','legacy')


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()


def unavailable(reason):
    return dict(status='unavailable',metrics={'total':None},equity_curve=[],fills=[],orders=[],limitations=[reason])


def summarize(result,capital,bootstrap):
    curve=result['equity_curve'];fills=result['fills'];orders=result['orders'];metrics=result['metrics']
    sells=[f for f in fills if f['side']=='sell'];buys=[o for o in orders if o['side']=='buy']
    days=[p['session'] for p in curve];position=0;exposed=0;entry=None;durations=[]
    by_day={}
    for fill in fills:by_day.setdefault(fill['session'],[]).append(fill)
    for i,day in enumerate(days):
        held=position>0
        for fill in by_day.get(day,[]):
            if fill['side']=='buy':position=fill['qty'];entry=i;held=True
            else:
                position=0
                if entry is not None:durations.append(i-entry+1)
                entry=None
        exposed+=int(held)
    result['status']='available' if metrics['total'] is not None else 'unavailable'
    metrics.update(start=days[0] if days else None,end=days[-1] if days else None,observed_sessions=len(days),average_net_pnl=sum(f['pnl'] for f in sells)/len(sells) if sells else None,
                   exposure_fraction=exposed/len(days) if days else None,
                   mean_holding_sessions=sum(durations)/len(durations) if durations else None,
                   unfilled_rate=sum(o['status']!='filled' for o in buys)/len(buys) if buys else None)
    interval=None
    if metrics['total'] is not None and len(curve)>=bootstrap['block_sessions']*2:
        previous=capital;returns=[]
        for point in curve:
            returns.append(point['equity']/previous-1);previous=point['equity']
        rng=random.Random(bootstrap['seed']);distribution=[];block=bootstrap['block_sessions']
        for _ in range(bootstrap['samples']):
            sampled=[]
            while len(sampled)<len(returns):
                start=rng.randrange(len(returns)-block+1);sampled.extend(returns[start:start+block])
            wealth=1.
            for r in sampled[:len(returns)]:wealth*=1+r
            distribution.append(wealth-1)
        distribution.sort();n=len(distribution)
        interval=[distribution[int((n-1)*.025)],distribution[int((n-1)*.975)]]
    result['uncertainty']=dict(method='moving_day_blocks',interval=interval,level=.95,**bootstrap,
                              notice='탐색적 구간. 전략 선택 불확실성·미래 장세 변화는 포함하지 않음')
    return result


def baseline(rows, *, strategy, forecasts, capital, weight, cost):
    cash=capital;qty=0;entry=None;fills=[];orders=[];curve=[];costs=0.;peak=capital;mdd=0.
    for i,row in enumerate(rows):
        day=row['session'];buy=(strategy=='buy_hold' and i==0)
        if strategy=='legacy':
            forecast=forecasts[day]
            try:up,down=float(forecast['p_up']),float(forecast['p_down'])
            except (KeyError,TypeError,ValueError):return unavailable('invalid_original_forecast')
            if not 0<=up<=1 or not 0<=down<=1:return unavailable('invalid_original_forecast')
            buy=up>down
        if buy:
            price=row['open']*(1+cost['slip']);qty=int(cash*weight/(price*(1+cost['fee'])))
            orders.append(dict(side='buy',session=day,status='filled' if qty else 'rejected'))
            if qty:
                fee=qty*price*cost['fee'];entry=qty*price+fee;cash-=entry
                costs+=fee+qty*(price-row['open'])
                fills.append(dict(side='buy',session=day,price=price,qty=qty,fee=fee,tax=0,timing='open'))
        if qty and (strategy=='legacy' or i==len(rows)-1):
            price=row['close']*(1-cost['slip']);fee=qty*price*cost['fee'];tax=qty*price*cost['tax']
            proceeds=qty*price-fee-tax;cash+=proceeds;costs+=fee+tax+qty*(row['close']-price)
            fills.append(dict(side='sell',session=day,price=price,qty=qty,fee=fee,tax=tax,pnl=proceeds-entry,timing='close'))
            qty=0
        equity=cash+qty*row['close'];peak=max(peak,equity);mdd=min(mdd,equity/peak-1)
        curve.append(dict(session=day,equity=equity,stale=False))
    return dict(status='available',equity_curve=curve,fills=fills,orders=orders,limitations=['retrospective_not_live'],
                metrics=dict(total=cash/capital-1,final_equity=cash,open_qty=qty,closed_trades=sum(f['side']=='sell' for f in fills),cost_won=costs,mdd=mdd))


def prepare_forecasts(data,model):
    if not data.get('forecasts'):return {}
    # 화면과 동일하게 최초 사전 예측만 사용한다.
    script="const fs=require('fs'),s=require('./docs/lab/trading_sim.js'),d=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(s.prepare(d.rows,d.model).rows));"
    r=subprocess.run(['node','-e',script],input=json.dumps({'rows':data['forecasts'],'model':model}),text=True,
                     capture_output=True,check=True,cwd=ROOT)
    return {row['target_date']:row for row in json.loads(r.stdout)}


def evaluate_wave(data, *, config):
    import exchange_calendars as xcals
    calendar=xcals.get_calendar('XKRX');settings=config['validation'];capital=config['paper']['capital']
    weight=min(config['paper']['max_symbol_weight'],config['paper']['max_sector_weight'])
    if not 0<weight<=.3 or capital<=0:raise ValueError('비교 자금·한도 오류')
    ranges={}
    for name in ('selection','evaluation'):
        spec=settings[name]
        if spec['start']>spec['end']:raise ValueError('기간 순서 오류')
        ranges[name]=[str(d.date()) for d in calendar.sessions_in_range(spec['start'],spec['end'])]
        if not ranges[name]:raise ValueError('평가 거래일 없음')
    gap=calendar.sessions_in_range(ranges['selection'][-1],ranges['evaluation'][0]) if ranges['selection'][-1]<ranges['evaluation'][0] else []
    if settings['purge_sessions']<5 or len(gap)-2<settings['purge_sessions']:raise ValueError('기간 중복 또는 5거래일 분리 부족')
    if settings['min_days']<60 or settings['min_trades']<20:raise ValueError('최소 검증 기준을 낮출 수 없음')
    boot=settings['bootstrap']
    if not 1<=boot['block_sessions']<=20 or not 100<=boot['samples']<=5000:raise ValueError('재표본 설정 오류')
    first={}
    for row in data['bars']:
        if row.get('target')!=data['target'] or row.get('timeframe')!='1d':raise ValueError('종목·봉 혼합')
        old=first.get(row['session'])
        if old is None or instant(row['observed_at'])<instant(old['observed_at']):first[row['session']]=row
        elif instant(row['observed_at'])==instant(old['observed_at']) and row!=old:raise ValueError('가격 버전 충돌')
    forecasts=prepare_forecasts(data,settings['legacy_model'])

    def period(dates):
        reasons=set()
        if any(r.get('adjustment')!='unadjusted' for d,r in first.items() if d<=dates[-1]):reasons.add('unverified_cash_price_basis')
        if any(day not in first for day in dates):reasons.add('missing_common_prices')
        if any(first[d].get('requires_corporate_action_adjustment') for d in dates if d in first):reasons.add('corporate_action_unresolved')
        if any(first[d]['volume']<=0 for d in dates if d in first):reasons.add('untradeable_session')
        result=dict(dates=dates,status='unavailable' if reasons else 'available',reasons=sorted(reasons),standard={},double_cost={})
        if reasons:
            for scenario in ('standard','double_cost'):
                result[scenario]={name:unavailable(','.join(sorted(reasons))) for name in NAMES}
            return result
        result['prices']=[{key:first[d][key] for key in ('session','open','high','low','close')} for d in dates]
        current=[first[d] for d in dates];history=[first[d] for d in sorted(first) if d<=dates[-1]]
        for multiplier,scenario in [(1,'standard'),(2,'double_cost')]:
            cost=copy.deepcopy(config['execution'])
            for key in ('fee','tax','slip'):cost[key]*=multiplier
            if any(not 0<=cost[k]<1 for k in ('fee','tax','slip')) or cost['fee']+cost['tax']>=1:raise ValueError('비용 범위 오류')
            cost.update(position_weight=weight,trade_start=dates[0],entry_deadline=dates[-5] if len(dates)>=5 else '0001-01-01')
            for name in NAMES:
                if name in config['strategies']:
                    simulation=simulate_wave(history,config=dict(config['strategies'][name],execution=cost),capital=capital)
                else:
                    if name=='legacy' and any(d not in forecasts for d in dates):
                        result[scenario][name]=unavailable('missing_original_forecasts');continue
                    if name=='legacy' and any(float(forecasts[d]['actual_open'])!=first[d]['open'] or float(forecasts[d]['actual_close'])!=first[d]['close'] for d in dates):
                        result[scenario][name]=unavailable('original_forecast_price_mismatch');continue
                    simulation=baseline(current,strategy=name,forecasts=forecasts,capital=capital,weight=weight,cost=cost)
                if simulation['metrics']['total'] is None:result[scenario][name]=simulation
                else:result[scenario][name]=summarize(simulation,capital,boot)
        return result

    selection=period(ranges['selection'])
    candidates=[name for name in ('range_rebound','trend_pullback') if selection['standard'][name]['metrics'].get('total') is not None and selection['standard'][name]['metrics'].get('closed_trades',0)>0]
    selected=sorted(candidates,key=lambda n:(-selection['standard'][n]['metrics']['total'],n))[0] if candidates else None
    evaluation=period(ranges['evaluation'])
    enough=selected is not None and selection['status']=='available' and evaluation['status']=='available'
    if enough:
        for part in (selection,evaluation):
            enough=bool(enough and len(part['dates'])>=settings['min_days'] and part['standard'][selected]['metrics'].get('closed_trades',0)>=settings['min_trades'])
    files=['tools/evaluate_wave_trading.py','tools/wave_backtest.py','tools/wave_signals.py','tools/paper_market_data.py','tools/collect_cash_prices.py','tools/paper_trading.py','tools/run_wave_paper.py','docs/lab/trading_sim.js']
    engine=b'\n'.join((ROOT/f).read_bytes() for f in files)
    return dict(schema_version=1,target=data['target'],method='observed_time_retrospective_split',automatic_promotion=False,
                selection=selection,evaluation=evaluation,selected=selected,candidate_count=2,
                strategy_labels=dict(range_rebound='박스권 반등',trend_pullback='상승 추세 눌림목',buy_hold='매수 후 보유',cash='현금 유지',legacy='기존 사전 예측 규칙 · 시가~종가 재계산'),
                selection_rule='선택 기간의 비용 차감 수익률 최대, 완료 거래가 있는 후보만, 동률은 이름순',
                evidence='minimum_sample_met_not_profit_proof' if enough else 'insufficient',
                input_sha256=digest(data),config_sha256=digest(config),engine_sha256=hashlib.sha256(engine).hexdigest(),
                calendar_version=xcals.__version__,config=config,paper_config_hashes={name:paper_config_hash(config,name) for name in config['strategies']},
                limitations=['관측 시각을 소급하지 않은 연구 재생이며 실시간 모의운용 실적이 아님',
                             '단일 종목별 동일 배분 한도 비교이며 두 종목 수익을 합산하지 않음',
                             '파동 신규 진입은 구간 마지막 4거래일 제외, 각 기간 현금에서 시작',
                             '기간 경계는 5거래일 분리, 학습 모델 추가 없음, 기존 예측은 사전 원장만 사용',
                             '평가를 본 뒤 규칙을 바꾸면 새 버전·새 평가 구간이 필요'])


def validation_bars(root, target):
    # 검증된 별도 보관본만 읽으며 기존 Yahoo 계열로 대체하지 않는다.
    return read_archive(Path(root)/'paper_history/market_cash', target=target, timeframe='1d')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',required=True,choices=['samsung','sk_hynix'])
    parser.add_argument('--config',type=Path,default=ROOT/'macro_inputs/wave_strategies.json')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();config=json.loads(args.config.read_text())
    path=ROOT/'forecast_history'/args.target/'forecast_log.csv'
    forecasts=list(csv.DictReader(path.open(encoding='utf-8-sig'))) if path.exists() else []
    data=dict(target=args.target,bars=validation_bars(ROOT,args.target),forecasts=forecasts)
    result=evaluate_wave(data,config=config)
    result['generated_at_utc']=datetime.now(timezone.utc).isoformat()
    result['source_revision']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(args.target,result['evidence'],result['evaluation']['reasons'])

if __name__=='__main__':main()

"""파동 후보의 분리된 관측 장부. 실주문·모의 체결·과거 체결 이식 기능은 없다."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta

try:
    from .paper_trading import instant, KST
    from .paper_market_data import available_bars, read_archive, _session_bounds
    from .wave_signals import wave_signal
except ImportError:
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from tools.paper_trading import instant, KST
    from tools.paper_market_data import available_bars, read_archive, _session_bounds
    from tools.wave_signals import wave_signal

ROOT=Path(__file__).resolve().parents[1]
TARGETS=('samsung','sk_hynix')


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def paper_config_hash(config,strategy):
    meaningful=dict(signal=config['strategies'][strategy],execution=config['execution'],paper=config['paper'],mode='observe_only',targets=TARGETS)
    return hashlib.sha256(encoded(meaningful).encode()).hexdigest()


def risk_preview(state, *, target, quotes, now, completed_bar_ends, paper, execution):
    """현재 시세로 한도만 점검한다. suggested_qty는 주문이나 예약이 아니다."""
    stamp=instant(now);reasons=[];prices={}
    held=state['positions'];reserved=state['reservations']
    if target not in TARGETS or any(t not in TARGETS for t in set(held)|set(reserved)):
        raise ValueError('지원하지 않는 종목')
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0
           for v in [state['cash'],*held.values(),*reserved.values()]):
        raise ValueError('잔고·예약 값 오류')
    needed={target}|{t for t,q in held.items() if q>0}
    for t in needed:
        q=quotes.get(t)
        if q is None:
            reasons.append('missing_quote')
            if held.get(t,0)>0:reasons.append('exit_deferred_missing_quote')
            continue
        age=(stamp-instant(q['bar_end'])).total_seconds()
        valid=(isinstance(q['close'],(int,float)) and not isinstance(q['close'],bool) and math.isfinite(q['close']) and q['close']>0)
        bounds=_session_bounds(stamp.astimezone(KST).date().isoformat())
        if not valid or instant(q['observed_at'])>stamp or age<0 or q.get('adjustment')!='unadjusted':
            reasons.append('invalid_quote')
        elif not bounds or not bounds[0]<instant(q['bar_end'])<=bounds[1] or age>paper['max_quote_age_seconds']:
            reasons.append('stale_quote')
        else:prices[t]=q['close']
        if t not in prices and held.get(t,0)>0:reasons.append('exit_deferred_stale_quote')
    last_exit=state['last_exits'].get(target)
    if last_exit and len({instant(t) for t in completed_bar_ends if instant(last_exit)<instant(t)<=stamp})<=1:
        reasons.append('reentry_cooldown')
    if held.get(target,0)>0:reasons.append('already_holding')
    if reasons:return dict(budget=0,suggested_qty=0,reasons=sorted(set(reasons)))
    equity=state['cash']+sum(q*prices[t] for t,q in held.items() if q>0)
    exposure=sum(q*prices[t] for t,q in held.items() if q>0)
    reserve=sum(reserved.values())
    budget=max(0,min(state['cash']-reserve,
                     equity*paper['max_symbol_weight']-held.get(target,0)*prices[target]-reserved.get(target,0),
                     equity*paper['max_sector_weight']-exposure-reserve))
    unit=prices[target]*(1+execution['slip'])*(1+execution['fee'])
    quantity=math.floor(budget/unit)
    return dict(budget=budget,suggested_qty=quantity,reservation_preview=quantity*unit,
                reasons=[] if quantity else ['cash_or_exposure_limit'])


def run_wave_paper(config, *, now, dry_run=True, root=ROOT):
    if dry_run is not True:raise ValueError('현재 버전은 관측 전용이며 주문 활성화를 지원하지 않습니다')
    stamp=instant(now);now=stamp.isoformat();root=Path(root);paper=config['paper']
    if config.get('mode')!='research_only' or config.get('automatic_promotion') is not False:
        raise ValueError('연구 전용 설정 필요')
    for k,limit in [('max_symbol_weight',0.3),('max_sector_weight',0.5)]:
        if not isinstance(paper[k],(int,float)) or not 0<paper[k]<=limit:raise ValueError('투자 한도 오류')
    if not isinstance(paper['capital'],(int,float)) or not math.isfinite(paper['capital']) or paper['capital']<=0:
        raise ValueError('초기 자금 오류')
    if not 0<paper['max_quote_age_seconds']<=120:raise ValueError('시세 유효시간 오류')
    for key in ('fee','tax','slip'):
        value=config['execution'][key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<1:
            raise ValueError('비용 설정 오류')
    strategies=config['strategies']
    if not strategies:raise ValueError('연구 전략 필요')
    for name,signal_config in strategies.items():
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',name) or name!=signal_config['strategy']:
            raise ValueError('전략 저장 경로 오류')
        wave_signal([],decision_at=now,config=signal_config)
    day=stamp.astimezone(KST).date()
    while True:
        bounds=_session_bounds(day.isoformat())
        if bounds and bounds[1]<=stamp:break
        day-=timedelta(days=1)
    expected_daily_end=bounds[1]
    data={};errors=[]
    for target in TARGETS:
        try:
            daily=available_bars(read_archive(root/'paper_history/market',target=target,timeframe='1d'),decision_at=now)
            minute=available_bars(read_archive(root/'paper_history/market',target=target,timeframe='1m'),decision_at=now)
            data[target]=(daily,minute[-1] if minute else None)
        except (ValueError,KeyError,sqlite3.Error,TypeError) as exc:
            errors.append(target+': '+type(exc).__name__)
            data[target]=None
    connections=[];summaries=[]
    try:
        # 모든 장부를 먼저 잠그고 설정을 확인한다. 충돌이면 기존 기록을 고치지 않는다.
        for name,signal_config in sorted(strategies.items()):
            path=root/'paper_history/strategies'/name/'account.sqlite';path.parent.mkdir(parents=True,exist_ok=True)
            db=sqlite3.connect(path,timeout=0.2);connections.append((name,db))
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS decisions (target TEXT, bar_end TEXT, payload TEXT NOT NULL, PRIMARY KEY(target,bar_end))')
            db.execute('CREATE TABLE IF NOT EXISTS runs (observed_at TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            digest=paper_config_hash(config,name)
            meta=dict(db.execute('SELECT key,value FROM meta'))
            if meta and meta.get('config_hash')!=digest:raise ValueError(name+': 설정 변경으로 기존 장부 사용 불가')
            if meta.get('last_observed_at') and instant(meta['last_observed_at'])>stamp:raise ValueError('관측 시각 역행')
            if not meta:
                db.executemany('INSERT INTO meta VALUES (?,?)',[('config_hash',digest),('cash',str(paper['capital']))])
        for name,db in connections:
            previous=db.execute('SELECT payload FROM runs WHERE observed_at=?',(now,)).fetchone()
            if previous:
                summaries.append(json.loads(previous[0]));continue
            cash=float(db.execute("SELECT value FROM meta WHERE key='cash'").fetchone()[0])
            state=dict(cash=cash,positions={},reservations={},last_exits={})
            quotes={t:value[1] for t,value in data.items() if value and value[1]}
            observations=[]
            for target in TARGETS:
                if data[target] is None:
                    observations.append(dict(target=target,status='archive_error'));continue
                daily,_=data[target]
                if not daily:
                    observations.append(dict(target=target,status='no_completed_daily'));continue
                latest=daily[-1]['bar_end']
                if instant(latest)!=expected_daily_end:
                    observations.append(dict(target=target,status='stale_daily',latest_bar_end=latest,
                                             expected_bar_end=expected_daily_end.isoformat()))
                    continue
                old=db.execute('SELECT payload FROM decisions WHERE target=? AND bar_end=?',(target,latest)).fetchone()
                if old:signal=json.loads(old[0])
                else:
                    signal=wave_signal(daily,decision_at=now,config=strategies[name])
                    db.execute('INSERT INTO decisions VALUES (?,?,?)',(target,latest,encoded(signal)))
                risk=risk_preview(state,target=target,quotes=quotes,now=now,completed_bar_ends=[r['bar_end'] for r in daily],
                                  paper=paper,execution=config['execution'])
                if signal['action']=='enter' and risk['suggested_qty']:
                    # 두 종목의 관측상 배분도 합산 한도를 넘기지 않게 같은 실행 안에서만 예약한다.
                    state['reservations'][target]=risk['reservation_preview']
                observations.append(dict(target=target,status='observe_only',signal=signal,risk_preview=risk))
            history_times=[instant(row[0]) for row in db.execute('SELECT observed_at FROM runs')]+[stamp]
            observed_days=len({t.astimezone(KST).date() for t in history_times})
            summary=dict(observed_days=observed_days,first_observed_at=min(history_times).isoformat(),strategy=name,strategy_version=strategies[name]['version'],
                         config_hash=db.execute("SELECT value FROM meta WHERE key='config_hash'").fetchone()[0],mode='observe_only',observed_at=now,initial_capital=paper['capital'],cash=cash,
                         positions={},order_count=0,fill_count=0,total_return=None,
                         decision_count=db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0],observations=observations,
                         notice='관측용 배분은 주문이 아닙니다. 독립 계좌의 자금을 합산하지 않습니다.')
            db.execute('INSERT INTO runs VALUES (?,?)',(now,encoded(summary)))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('last_observed_at',?)",(now,))
            summaries.append(summary)
        for _,db in connections:db.commit()
    except Exception:
        for _,db in connections:db.rollback()
        raise
    finally:
        for _,db in connections:db.close()
    return dict(schema_version=1,mode='observe_only',generated_at=now,strategies=summaries,collection_errors=errors,
                notice='현재는 신호 관측만 수행합니다. 모의 체결·수익률은 아직 없습니다.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'macro_inputs/wave_strategies.json')
    args=parser.parse_args()
    result=run_wave_paper(json.loads(args.config.read_text()),now=datetime.now(timezone.utc).isoformat())
    destination=ROOT/'docs/lab/wave_paper_status.json';destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=destination.parent,delete=False) as handle:
        handle.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n');temporary=handle.name
    os.replace(temporary,destination)
    print('파동 전략 관측 전용:',len(result['strategies']),'개 · 오류:',len(result['collection_errors']))


if __name__=='__main__':
    # 저장소 루트에서 python tools/run_wave_paper.py 실행도 지원한다.
    main()

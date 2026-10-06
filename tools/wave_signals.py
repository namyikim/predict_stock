"""관측 시점에 알려진 일봉으로만 계산하는 연구 후보. 주문이나 수익률을 만들지 않는다."""
import math
from .paper_market_data import available_bars
from .paper_trading import instant


def wave_signal(bars, *, decision_at, config):
    """65개 완료 일봉으로 두 파동 후보 중 하나를 계산한다. 입력을 수정하지 않는다."""
    strategy = config.get('strategy')
    if strategy not in ('range_rebound', 'trend_pullback') or config.get('version') != 'wave-v1':
        raise ValueError('지원하지 않는 연구 전략·버전')
    if config.get('timeframe') != '1d':
        raise ValueError('첫 연구 버전은 일봉만 사용')
    rows = available_bars(bars, decision_at=decision_at)
    if len({(r['target'], r['timeframe']) for r in rows}) > 1 or any(r['timeframe'] != '1d' for r in rows):
        raise ValueError('하나의 종목·일봉 계열만 사용')
    for r in rows:
        values = [r[k] for k in ('open', 'high', 'low', 'close', 'volume')]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            raise ValueError('유효한 OHLCV 필요')
        o,h,l,c,v=values
        if min(o,h,l,c)<=0 or v<0 or not l<=min(o,c)<=max(o,c)<=h:
            raise ValueError('OHLCV 범위 오류')
    cutoff = max((instant(r['observed_at']) for r in rows), default=None)
    result = dict(strategy=strategy, version=config['version'], action='wait', regime='unknown',
                  range_position=None, reason_codes=[], information_cutoff=cutoff.isoformat() if cutoff else None,
                  reference_stop=None, decision_at=instant(decision_at).isoformat(), available_bars=len(rows))
    if config.get('requires_forecast', False):
        result['reason_codes']=['aligned_forecast_unavailable']
        return result
    if len(rows)<65:
        result['reason_codes']=['insufficient_bars']
        return result
    # 직전 봉의 위치를 판정할 범위에는 직전·최신 봉을 모두 넣지 않는다.
    previous, latest = rows[-2:]
    window=rows[-22:-2]
    lo,hi=min(r['low'] for r in window),max(r['high'] for r in window)
    if hi<=lo:
        result['reason_codes']=['zero_range']
        return result
    mean=lambda data: sum(r['close'] for r in data)/len(data)
    ma20,ma60=mean(rows[-20:]),mean(rows[-60:])
    previous_ma20=mean(rows[-21:-1])
    slope=ma20/mean(rows[-25:-5])-1
    position=(previous['close']-lo)/(hi-lo)
    rising=slope>0 and ma20>ma60
    sideways=abs(slope)<=0.01
    # 두 후보는 독립 실험이다. 완만한 상승은 양쪽 분류에 걸칠 수 있다.
    result.update(regime=('range' if sideways else 'trend') if strategy=='range_rebound' else ('uptrend' if rising else 'other'),
                  range_position=position, reference_stop=min(r['low'] for r in rows[-5:]),
                  metrics=dict(range_low=lo,range_high=hi,ma20=ma20,ma60=ma60,ma20_change_5=slope,
                               previous_ma20=previous_ma20), signal_bar_end=latest['bar_end'])
    reasons=[]
    if strategy=='range_rebound':
        if not sideways:reasons.append('not_sideways')
        if not 0<=position<=0.2:reasons.append('outside_lower_range')
    else:
        if not rising:reasons.append('not_uptrend')
        if previous['close']>previous_ma20:reasons.append('no_pullback')
        if latest['close']<=ma20:reasons.append('average_not_recovered')
    if latest['close']<=previous['high']:reasons.append('rebound_unconfirmed')
    if reasons:
        result['reason_codes']=reasons
    else:
        result.update(action='enter',reason_codes=[strategy+'_confirmed'])
    return result

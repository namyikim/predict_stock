"""회고용 가격·수급 비교. 예측 신호가 아닌 관찰값을 계산한다(2026-10-02 회고 반영)."""
import numpy as np
import pandas as pd


def _dated(frame):
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index).normalize()
    return frame.loc[~frame.index.duplicated(keep='last')].sort_index()


def _valid(values):
    return bool(np.isfinite(values).all() and (values > 0).all())


def price_context(prices, peer, session_date):
    """오늘 제외 고점과 동일 거래일 창의 수익률 차이. 누락된 날을 건너뛰지 않는다."""
    day = pd.Timestamp(session_date).normalize()
    prices, peer = _dated(prices), _dated(peer)
    prices = prices.loc[prices.index <= day]
    out = {'breakouts': {}, 'relative': {}}
    if day not in prices.index:
        return out
    for n in (5, 20, 60):
        window = prices.tail(n + 1)
        if len(window) != n + 1:
            continue
        if n in (20, 60):
            highs = pd.to_numeric(window['high'], errors='coerce')
            close = float(window['close'].iloc[-1])
            if _valid(highs) and np.isfinite(close) and close > 0:
                high = float(highs.iloc[:-1].max())
                out['breakouts'][str(n)] = {
                    'prior_high': high, 'distance': close / high - 1,
                    'close_breakout': bool(close > high),
                    'intraday_breakout': bool(highs.iloc[-1] > high)}
        if 'close' not in peer:
            continue
        own = pd.to_numeric(window['close'], errors='coerce')
        other = pd.to_numeric(peer.reindex(window.index)['close'], errors='coerce')
        if _valid(own) and _valid(other):
            ret, peer_ret = float(own.iloc[-1] / own.iloc[0] - 1), float(other.iloc[-1] / other.iloc[0] - 1)
            out['relative'][str(n)] = {'start': str(window.index[0].date()), 'end': str(day.date()),
                                      'own': ret, 'peer': peer_ret, 'excess': ret - peer_ret}
    return out


def buyback_transition(periods, flows, sessions, session_date):
    """공시상 예정 종료 전후 같은 길이의 수급(주). 실제 완료·자사주 거래량으로 해석하지 않는다."""
    day = pd.Timestamp(session_date).normalize()
    active = [p for p in periods if pd.Timestamp(p['start']) <= day <= pd.Timestamp(p['end'])]
    ended = [p for p in periods if pd.Timestamp(p['end']) < day]
    if active or not ended:
        return None
    end = max(pd.Timestamp(p['end']) for p in ended)
    sessions = pd.DatetimeIndex(sessions).normalize().unique().sort_values()
    sessions = sessions[sessions <= day]
    after = sessions[sessions > end]
    if len(after) == 0 or len(after) > 20:
        return None
    before = sessions[sessions <= end]
    frame = _dated(flows.set_index('date'))
    out = {'state': 'scheduled_end_passed', 'scheduled_end': str(end.date()),
           'sessions_after': len(after), 'windows': {}}
    for n in (5, 20):
        if len(before) < n or len(after) < n:
            continue
        pre, post = frame.reindex(before[-n:]), frame.reindex(after[:n])
        actors = {}
        for key in ('foreign_net', 'inst_net', 'indiv_net', 'other_net'):
            if key not in frame:
                continue
            a, b = pd.to_numeric(pre[key], errors='coerce'), pd.to_numeric(post[key], errors='coerce')
            if np.isfinite(a).all() and np.isfinite(b).all():
                actors[key] = {'before': float(a.sum()), 'after': float(b.sum())}
        if actors:
            out['windows'][str(n)] = actors
    return out


def foreign_pressure(flows, prices, session_date):
    """최근·직전 각 5거래일 외국인 순매수/거래량 비교(2026-10-02 회고). 결측은 보간하지 않는다."""
    day = pd.Timestamp(session_date).normalize()
    prices = _dated(prices)
    window = prices.loc[prices.index <= day].tail(10)
    if len(window) != 10 or window.index[-1] != day or 'volume' not in window or 'foreign_net' not in flows:
        return None
    net = pd.to_numeric(_dated(flows.set_index('date')).reindex(window.index)['foreign_net'], errors='coerce')
    volume = pd.to_numeric(window['volume'], errors='coerce')
    if not np.isfinite(net).all() or not _valid(volume):
        return None
    before, after = float(net.iloc[:5].sum()), float(net.iloc[5:].sum())
    previous_ratio = before / float(volume.iloc[:5].sum())
    recent_ratio = after / float(volume.iloc[5:].sum())
    state = 'other'
    if before < 0 and after < 0:
        state = ('selling_unchanged' if np.isclose(previous_ratio, recent_ratio, rtol=1e-9, atol=1e-12)
                 else 'selling_eased' if recent_ratio > previous_ratio else 'selling_intensified')
    elif before <= 0 and after > 0:
        state = 'turned_buying'
    elif before >= 0 and after < 0:
        state = 'turned_selling'
    return {'state': state, 'previous_ratio': previous_ratio, 'recent_ratio': recent_ratio,
            'previous_daily_net': before / 5, 'recent_daily_net': after / 5}

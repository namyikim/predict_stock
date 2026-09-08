"""외국인·기관 수급: KRX(pykrx) → 네이버 금융 매매동향 → 저장소 보관본."""
import hashlib
import io
import json
import os
import re
from pathlib import Path
import random
import time
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd
from data_sources._common import *  # noqa: F401,F403




# ---------------------------------------------------------------------------
# 외국인·기관 수급 (투자자별 순매수, 외국인 지분율)
# ---------------------------------------------------------------------------
# 삼성전자·SK하이닉스는 외국인 비중이 절반 안팎이라 그들의 순매수 방향이 주가와 같이 움직인다.
# 다만 '같이 움직인다'와 '앞선다'는 다르다. 전일 순매수가 오늘 방향을 맞히는지는 다른 특징들과
# 똑같이 'No flow ensemble'과의 쌍체 비교로 잰다. 투자자별 거래 실적은 그날 장 마감 뒤에 확정되므로
# 다음 날 07:00 예측에는 전일까지의 값이 들어간다(shift 1).
#
# 자료원 우선순위: 사용자 CSV > KRX(pykrx, 계정 필요) > 네이버 금융 페이지 > 저장소 보관본.
FLOW_COLUMNS = ['date', 'foreign_net', 'inst_net', 'indiv_net', 'volume', 'foreign_ratio']




def _flows_from_pykrx(ticker, start, end):
    from pykrx import stock as krx
    code = ticker.split('.')[0]
    s, e = pd.Timestamp(start).strftime('%Y%m%d'), pd.Timestamp(end).strftime('%Y%m%d')
    vol = krx.get_market_trading_volume_by_date(s, e, code)              # 순매수 주식수, 투자자별
    if vol is None or vol.empty:
        raise RuntimeError('pykrx가 빈 표를 돌려주었습니다.')
    out = pd.DataFrame({'date': pd.to_datetime(vol.index)})
    out['foreign_net'] = vol['외국인합계'].to_numpy(dtype=float) if '외국인합계' in vol else np.nan
    out['inst_net'] = vol['기관합계'].to_numpy(dtype=float) if '기관합계' in vol else np.nan
    out['indiv_net'] = vol['개인'].to_numpy(dtype=float) if '개인' in vol else np.nan
    out['volume'] = np.nan
    try:
        ratio = krx.get_exhaustion_rates_of_foreign_investment_by_date(s, e, code)
        ratio.index = pd.to_datetime(ratio.index)
        out['foreign_ratio'] = ratio['지분율'].reindex(out['date']).to_numpy(dtype=float)
    except Exception:
        out['foreign_ratio'] = np.nan
    return out




def parse_naver_frgn_html(html_text):
    """네이버 금융 '외국인·기관 매매동향' 표(item/frgn) → 일별 순매매량·보유율."""
    import io
    tables = pd.read_html(io.StringIO(html_text))
    picked = None
    for table in tables:
        cols = [' '.join(map(str, c)) if isinstance(c, tuple) else str(c) for c in table.columns]
        if any('외국인' in c for c in cols) and any('날짜' in c for c in cols):
            table.columns = cols
            picked = table
            break
    if picked is None:
        raise ValueError('네이버 페이지에서 매매동향 표를 찾지 못했습니다.')

    def col(*needles):
        """헤더가 두 줄이라 '순매매량 기관', '외국인 보유율'처럼 합쳐진다. 조건을 모두 포함하는 열을 찾는다."""
        return next((c for c in picked.columns if all(n in c for n in needles)), None)

    def num(name_cols):
        c = col(*name_cols) if isinstance(name_cols, tuple) else col(name_cols)
        if c is None:
            return np.nan
        return pd.to_numeric(picked[c].astype(str).str.replace(',', '').str.replace('+', '').str.rstrip('%'),
                             errors='coerce')

    out = pd.DataFrame({
        'date': pd.to_datetime(picked[col('날짜')].astype(str).str.replace('.', '-', regex=False), errors='coerce'),
        'volume': num('거래량'),
        'inst_net': num(('순매매량', '기관')),
        'foreign_net': num(('순매매량', '외국인')),
        'foreign_ratio': num('보유율'),
    }).dropna(subset=['date', 'foreign_net'])
    out['indiv_net'] = np.nan
    return out[FLOW_COLUMNS].sort_values('date').reset_index(drop=True)




def _flows_from_naver(ticker, start, max_pages=200):
    """finance.naver.com/item/frgn.naver?code=... 을 페이지 단위로 읽는다. 계정이 필요 없다."""
    code = ticker.split('.')[0]
    frames, start = [], pd.Timestamp(start)
    for page in range(1, max_pages + 1):
        url = f'https://finance.naver.com/item/frgn.naver?code={code}&page={page}'
        with open_url(url) as response:
            html_text = response.read().decode('euc-kr', errors='ignore')
        frame = parse_naver_frgn_html(html_text)
        if frame.empty:
            break
        frames.append(frame)
        if frame['date'].min() <= start:
            break
        time.sleep(0.4 + random.uniform(0, 0.4))
    if not frames:
        raise RuntimeError('네이버 매매동향 표가 비어 있습니다.')
    out = pd.concat(frames).drop_duplicates('date').sort_values('date')
    return out[out['date'] >= start].reset_index(drop=True)




def load_investor_flows(storage, ticker, start, end, use_cache=False, fallback_dir=None):
    """(DataFrame(FLOW_COLUMNS), info). 저장소 보관본이 있으면 최근 40거래일만 새로 받아 잇는다."""
    storage = Path(storage)
    code = ticker.split('.')[0]
    cache = storage / 'macro_cache'
    cache.mkdir(parents=True, exist_ok=True)
    local = storage / 'macro_inputs' / f'investor_flows_{code}.csv'
    cached = cache / f'investor_flows_{code}.csv'
    fallback = Path(fallback_dir) / f'investor_flows_{code}.csv' if fallback_dir else None

    def read(path):
        frame = pd.read_csv(path, dtype=str)
        frame['date'] = pd.to_datetime(frame['date']).dt.normalize()
        for col in FLOW_COLUMNS[1:]:
            frame[col] = pd.to_numeric(frame.get(col), errors='coerce')
        return frame[FLOW_COLUMNS].drop_duplicates('date').sort_values('date').reset_index(drop=True)

    error, source = None, None
    if use_cache and cached.exists():
        frame, source = read(cached), 'explicit_cache_replay'
    elif local.exists():
        frame, source = read(local), 'user_csv'
    else:
        base = read(fallback) if fallback and fallback.exists() else None
        fetch_start = (base['date'].max() - pd.Timedelta(days=60)) if base is not None and len(base) else pd.Timestamp(start)
        fresh = None
        errors = []
        if os.environ.get('KRX_ID') and os.environ.get('KRX_PW'):
            try:
                fresh, source = _flows_from_pykrx(ticker, fetch_start, end), 'KRX(pykrx)'
            except ImportError:
                errors.append('KRX: pykrx 미설치')
            except Exception as exc:
                errors.append(f'KRX: {type(exc).__name__}: {exc}')
        if fresh is None:
            try:
                fresh, source = _flows_from_naver(ticker, fetch_start), 'naver'
            except Exception as exc:
                errors.append(f'naver: {type(exc).__name__}: {exc}')
        if fresh is None:
            if base is None:
                raise RuntimeError('수급 자료를 받지 못했습니다: ' + ' / '.join(errors))
            frame, source, error = base, 'last_successful_fetch', ' / '.join(errors)
        else:
            frame = (pd.concat([base, fresh]) if base is not None else fresh)
            frame = frame.drop_duplicates('date', keep='last').sort_values('date').reset_index(drop=True)
            if base is not None:
                source += '+cache'
    frame.to_csv(cached, index=False)
    info = {'source': source, 'fresh': error is None and source not in ('explicit_cache_replay',),
            'fetch_error': error, 'first': frame['date'].min().date().isoformat(),
            'last': frame['date'].max().date().isoformat(), 'rows': int(len(frame)),
            'snapshot_hash': hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()[:20]}
    return frame, info




def flow_features(flows, dates, volume=None):
    """d일 07:00에 아는 값만으로 만든 수급 특징(전일까지). 순매수는 20일 평균 거래량으로 나눠 무차원화한다."""
    f = flows.set_index('date').sort_index()
    vol = f['volume'] if volume is None else pd.Series(volume).reindex(f.index)
    if vol.isna().all():
        raise ValueError('거래량이 없어 순매수를 정규화할 수 없습니다.')
    vol20 = vol.rolling(20, min_periods=10).mean().replace(0, np.nan)
    frgn = f['foreign_net'] / vol20
    inst = f['inst_net'] / vol20
    sign = np.sign(f['foreign_net'].fillna(0))
    streak = sign.groupby((sign != sign.shift()).cumsum()).cumcount() + 1
    streak = (streak * sign).where(sign != 0, 0)
    out = pd.DataFrame({
        'flow_frgn_1': frgn,
        'flow_frgn_5': frgn.rolling(5, min_periods=3).sum(),
        'flow_frgn_20': frgn.rolling(20, min_periods=10).sum(),
        'flow_frgn_streak': streak,
        'flow_inst_5': inst.rolling(5, min_periods=3).sum(),
        'flow_frgn_ratio_chg_20': f['foreign_ratio'].diff(20),
    })
    out = out.replace([np.inf, -np.inf], np.nan)
    # 예측일 d에는 d보다 앞선 마지막 거래일의 값을 쓴다(그날 마감 뒤 확정). 단순 shift(1) 뒤 ffill은
    # 자료가 끝난 뒤의 예측일에 이틀 전 값을 넣어 버린다.
    dates = pd.DatetimeIndex(dates)
    left = pd.DataFrame({'date': dates.as_unit('ns'), '_order': np.arange(len(dates))}).sort_values('date')
    right = out.rename_axis('date').reset_index()
    right['date'] = pd.DatetimeIndex(right['date']).as_unit('ns')
    joined = pd.merge_asof(left, right, on='date', direction='backward', allow_exact_matches=False,
                           tolerance=pd.Timedelta(days=6)).sort_values('_order')
    return joined.drop(columns='_order').set_index('date').reindex(dates)

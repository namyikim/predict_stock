"""공용: 요청 헤더(open_url), 월별·일별 정규화, 보관본 노후 판정."""
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




def normalize_monthly(frame):
    columns = ['month', 'value'] + [c for c in ('released_at', 'vintage', 'source') if c in frame]
    out = frame[columns].copy()
    months = out['month'].astype(str).str.replace(r'^(\d{4})[./](\d{1,2})$', r'\1-\2', regex=True)
    months = months.str.replace(r'^(\d{4})(\d{2})$', r'\1-\2', regex=True)
    out['month'] = pd.to_datetime(months, format='mixed', errors='raise').dt.to_period('M').dt.to_timestamp()
    out['value'] = pd.to_numeric(out['value'].astype(str).str.replace(',', ''), errors='coerce')
    keys = ['month']
    if 'released_at' in out:
        releases = []
        for value in out['released_at']:
            stamp = pd.Timestamp(value)
            if pd.isna(stamp) or stamp.tzinfo is None:
                raise ValueError('released_at에는 시간대가 포함된 정확한 발표/수정 시각이 필요합니다.')
            releases.append(stamp.tz_convert('UTC'))
        out['released_at'] = pd.to_datetime(releases, utc=True)
        earliest = (out['month'] + pd.offsets.MonthBegin(1)).dt.tz_localize('Asia/Seoul')
        if (out['released_at'] < earliest).any():
            raise ValueError('완결 월별 통계의 발표 시각이 해당 월 종료보다 빠릅니다.')
        keys.append('released_at')
    if out.duplicated(keys).any():
        raise ValueError('월별 통계에 같은 월이 중복됩니다. 하나의 지표/단위만 선택하세요.')
    out.loc[~np.isfinite(out.value) | (out.value <= 0), 'value'] = np.nan
    if out.value.notna().sum() == 0:
        raise ValueError('월별 통계에 유효한 양수 값이 없습니다.')
    return out.sort_values(keys).reset_index(drop=True)




def normalize_daily(frame):
    out = frame[['date', 'value']].copy()
    out['date'] = pd.to_datetime(out['date'].astype(str).str.replace(r'[^0-9]', '', regex=True),
                                 format='%Y%m%d', errors='raise').dt.normalize()
    out['value'] = pd.to_numeric(out['value'], errors='coerce')
    if out.duplicated('date').any():
        raise ValueError('일별 지수에 같은 날짜가 중복됩니다.')
    out = out.dropna(subset=['value']).sort_values('date').reset_index(drop=True)
    if out.empty:
        raise ValueError('일별 지수에 유효한 값이 없습니다.')
    return out




def cache_age_days(storage, names=None, now=None):
    """저장소에서 받아 둔 보관본이 며칠 된 자료인지. {이름: 경과일}.

    보관본은 한국에서 돌린 실행이 갱신한다. 그걸 잊으면 100일 만료 규칙이 걸릴 때까지 낡은 값을
    조용히 쓰게 된다. 여기서 경과일을 재어 보고서에 적을 수 있게 한다.
    """
    names = names or ('leading_cycle', 'semiconductor_exports', 'news_sentiment', 'term_spread')
    now = pd.Timestamp(now or pd.Timestamp.now(tz='Asia/Seoul').tz_localize(None)).normalize()
    out = {}
    for name in names:
        path = Path(storage) / 'macro_fallback' / f'{name}.csv'
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, dtype=str)
            column = 'month' if 'month' in frame.columns else 'date'
            last = pd.to_datetime(frame[column].astype(str), format='mixed', errors='coerce').max()
        except Exception:
            continue
        if pd.notna(last):
            out[name] = int((now - pd.Timestamp(last).normalize()).days)
    return out




def stale_cache_note(ages, warn_after=45, expire_after=100):
    """경고 문구. 경고할 것이 없으면 빈 문자열."""
    stale = {name: age for name, age in ages.items() if age >= warn_after}
    if not stale:
        return ''
    worst = max(stale.values())
    listed = ', '.join(f'{name} {age}일' for name, age in sorted(stale.items(), key=lambda x: -x[1]))
    tail = (' 만료(100일)가 가까워 곧 지표에서 빠집니다.' if worst >= expire_after - 20 else '')
    return (f'참고자료 보관본이 오래되었습니다({listed}). 한국에서 Colab으로 노트북을 한 번 '
            f'실행하면 갱신됩니다.{tail}')




# 파이썬 기본 User-Agent(Python-urllib/3.x)는 CDN이 자동으로 막는 일이 흔하다. OECD가 Actions에서
# 403을 돌려준 것도 이 때문이었다(같은 요청이 브라우저·requests로는 통과). 모든 외부 요청에 붙인다.
USER_AGENT = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/126.0 Safari/537.36 predict_stock/1.0 (research; non-commercial)')




def urllib_request_with_agent(url, accept=None):
    from urllib.request import Request
    headers = {'User-Agent': USER_AGENT, 'Accept-Language': 'ko,en;q=0.8'}
    if accept:
        headers['Accept'] = accept
    return Request(url, headers=headers)




def open_url(url, timeout=60, accept=None):
    return urlopen(urllib_request_with_agent(url, accept), timeout=timeout)

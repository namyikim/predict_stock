"""OECD G20 경기선행지수(OECD 데이터 API, 대체로 FRED)와 참조월+1개월 20일 정렬."""
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
# OECD G20 경기선행지수(CLI) — FRED 경유
# ---------------------------------------------------------------------------
# "G20 CLI가 한국 수출을 2개월 앞선다"는 차트는 최종 수정치로 사후에 그린 것이다. CLI는 추세제거·
# 평활 필터를 전체 시계열에 걸어 계산하므로 매달 소급 수정되고(OECD FAQ), 발표는 참조월로부터
# 약 5~6주 뒤다. 여기서는 참조월 M의 값을 (M+1)월 20일 이후에만 쓴다. 개정 문제는 없앨 수 없어
# 백테스트가 낙관적이라는 점을 보고서에 적는다.
# 원본은 OECD 데이터 API(키 불필요). FRED에는 G20 집계가 없다(G7·개별국만 있고 OECD 전체는 2022-11에서 끊겼다).
# FRED_CLI_SERIES_ID를 명시하면(예: G7LOLITOAASTSAM) OECD 대신 FRED를 쓴다.
CLI_REF_AREA = os.environ.get('OECD_CLI_REF_AREA', 'G20')


OECD_CLI_URLS = [
    'https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI,/{area}.M.LI...AA...H?startPeriod={start}&format=csvfilewithlabels',
    'https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI,4.1/{area}.M.LI...AA...H?startPeriod={start}&format=csvfilewithlabels',
]


FRED_CLI_SERIES_ID = os.environ.get('FRED_CLI_SERIES_ID', '')


CLI_RELEASE_DAY = 20          # 참조월 다음 달의 이 날짜부터 사용


CLI_MAX_AGE_DAYS = 75


CLI_NOTE = ('OECD G20 경기선행지수(OECD 데이터 API). 참조월+1개월 20일 이후에만 사용. '
            '매달 소급 수정되므로 백테스트는 최종 수정치 기준이라 낙관적')




def fred_key():
    key = os.environ.get('FRED_API_KEY')
    if not key:
        try:
            from google.colab import userdata
            key = userdata.get('FRED_API_KEY')
        except Exception:
            pass
    return key




def _fred_request(key, path, params, retries=3):
    # URL에 키가 들어가므로 예외 메시지에 URL을 싣지 않는다.
    query = urlencode({**params, 'api_key': key, 'file_type': 'json'})
    url = f'https://api.stlouisfed.org/fred/{path}?{query}'
    for attempt in range(retries):
        try:
            with open_url(url, accept='application/json') as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as exc:
            detail = error_detail(exc)
            if attempt == retries - 1:
                raise RuntimeError(f'FRED API 조회 실패({detail}). 키·시리즈 코드를 확인하거나 '
                                   'macro_inputs/cli_g20.csv를 사용하세요.') from None
            time.sleep(3 * (2 ** attempt) + random.uniform(0, 3))




def parse_fred_observations(payload):
    rows = payload.get('observations') or []
    if not rows:
        raise ValueError('FRED 응답에 관측치가 없습니다.')
    out = pd.DataFrame({'month': [r['date'] for r in rows],
                        'value': pd.to_numeric([r.get('value') for r in rows], errors='coerce')})
    out = out.dropna(subset=['value'])            # FRED는 결측을 '.'로 보낸다
    if out.empty:
        raise ValueError('FRED 응답에 유효한 값이 없습니다.')
    return normalize_monthly(out)




def fetch_fred_monthly(series_id, key, start):
    payload = _fred_request(key, 'series/observations',
                            {'series_id': series_id, 'observation_start': pd.Timestamp(start).strftime('%Y-%m-%d')})
    return parse_fred_observations(payload)




def parse_oecd_csv(text, ref_area=None):
    """OECD csvfilewithlabels → DataFrame(month, value). REF_AREA·TIME_PERIOD·OBS_VALUE 열을 쓴다."""
    import io
    frame = pd.read_csv(io.StringIO(text), dtype=str)
    needed = {'TIME_PERIOD', 'OBS_VALUE'}
    if not needed.issubset(frame.columns):
        raise ValueError(f'OECD 응답에 {needed} 열이 없습니다: {list(frame.columns)[:8]}')
    if ref_area and 'REF_AREA' in frame.columns:
        frame = frame[frame['REF_AREA'].astype(str) == ref_area]
    out = pd.DataFrame({'month': frame['TIME_PERIOD'].astype(str),
                        'value': pd.to_numeric(frame['OBS_VALUE'], errors='coerce')}).dropna(subset=['value'])
    if out.empty:
        raise ValueError('OECD 응답에 유효한 관측치가 없습니다.')
    out = out.drop_duplicates('month', keep='last')
    return normalize_monthly(out)




def fetch_oecd_cli(ref_area, start, retries=2):
    """OECD 데이터 API에서 진폭조정 CLI를 받는다. 키가 필요 없다."""
    start_text = pd.Timestamp(start).strftime('%Y-%m')
    last = ''
    for url in OECD_CLI_URLS:
        for attempt in range(retries):
            try:
                with open_url(url.format(area=ref_area, start=start_text), timeout=90,
                              accept='application/vnd.sdmx.data+csv; charset=utf-8, text/csv, */*') as response:
                    return parse_oecd_csv(response.read().decode('utf-8-sig'), ref_area)
            except Exception as exc:
                last = error_detail(exc)
                time.sleep(2 + random.uniform(0, 2))
    raise RuntimeError(f'OECD CLI 조회 실패({last}). 연결을 확인하거나 macro_inputs/cli_g20.csv를 사용하세요.')




def search_fred_series(key, text):
    payload = _fred_request(key, 'series/search', {'search_text': text, 'limit': 20})
    return [(s.get('id'), s.get('title'), s.get('frequency_short'), s.get('observation_end'))
            for s in payload.get('seriess', [])]




def load_cli(storage, start, end, use_cache=False, fallback_dir=None):
    """(DataFrame(month,value), info). 우선순위: 캐시 재현 > 사용자 CSV > FRED > 저장소 보관본."""
    storage = Path(storage)
    cache = storage / 'macro_cache'
    cache.mkdir(parents=True, exist_ok=True)
    local = storage / 'macro_inputs' / 'cli_g20.csv'
    cached = cache / 'cli_g20.csv'
    fallback = Path(fallback_dir) / 'cli_g20.csv' if fallback_dir else None
    source, error = None, None
    if use_cache and cached.exists():
        frame, source = normalize_monthly(pd.read_csv(cached, dtype=str)), 'explicit_cache_replay'
    elif local.exists():
        frame, source = normalize_monthly(pd.read_csv(local, dtype=str)), 'user_csv'
    else:
        try:
            if FRED_CLI_SERIES_ID:
                key = fred_key()
                if not key:
                    raise RuntimeError('FRED_API_KEY가 없습니다.')
                frame, source = fetch_fred_monthly(FRED_CLI_SERIES_ID, key, start), f'FRED_API({FRED_CLI_SERIES_ID})'
            else:
                frame, source = fetch_oecd_cli(CLI_REF_AREA, start), f'OECD_API({CLI_REF_AREA})'
        except Exception as exc:
            if not (fallback and fallback.exists()):
                raise
            error = str(exc)
            frame, source = normalize_monthly(pd.read_csv(fallback, dtype=str)), 'last_successful_fetch'
    frame.to_csv(cached, index=False)
    info = {'source': source, 'series_id': FRED_CLI_SERIES_ID or f'OECD {CLI_REF_AREA}',
            'fresh': source.startswith(('OECD_API', 'FRED_API')) or source == 'user_csv',
            'fetch_error': error, 'first': frame['month'].min().strftime('%Y-%m'),
            'last': frame['month'].max().strftime('%Y-%m'), 'rows': int(len(frame)),
            'release_day': CLI_RELEASE_DAY, 'note': CLI_NOTE,
            'snapshot_hash': hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()[:20]}
    return frame, info




def cli_features(frame, dates, release_day=CLI_RELEASE_DAY, max_age_days=CLI_MAX_AGE_DAYS):
    """참조월+1개월 release_day 이후에만 보이는 CLI 특징. 수준(100 기준)과 3·6개월 변화."""
    monthly = normalize_monthly(frame).set_index('month')['value'].asfreq('MS')
    f = pd.DataFrame({
        'cli_level': monthly - 100.0,
        'cli_change_3m': monthly.diff(3),
        'cli_change_6m': monthly.diff(6),
    })
    available = (pd.DatetimeIndex(f.index) + pd.DateOffset(months=1)) + pd.Timedelta(days=release_day - 1)
    f.index = available
    f = f.dropna(how='all')
    f['_expires'] = f.index + pd.Timedelta(days=max_age_days)
    dates = pd.DatetimeIndex(dates)
    left = pd.DataFrame({'available_date': dates.as_unit('ns'), '_order': np.arange(len(dates))}).sort_values('available_date')
    right = f.rename_axis('available_date').reset_index()
    right['available_date'] = pd.DatetimeIndex(right['available_date']).as_unit('ns')
    joined = pd.merge_asof(left, right, on='available_date', direction='backward').sort_values('_order')
    valid = joined['available_date'] <= joined['_expires']
    out = pd.DataFrame(index=dates)
    for col in ('cli_level', 'cli_change_3m', 'cli_change_6m'):
        out[col] = joined[col].where(valid).to_numpy()
    return out

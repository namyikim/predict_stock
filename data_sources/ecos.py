"""한국은행 ECOS: 뉴스심리지수(일별, 주 1회 공개)와 장단기 금리차(국고채 10년-3년)."""
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
# 한국은행 뉴스심리지수(NSI, ECOS 일별)
# ---------------------------------------------------------------------------
# 지수는 일별로 작성되지만 ECOS 공개는 주 1회(2026-09부터 매주 월요일 16:00)다. 그래서
# 지수 날짜의 값을 그날 예측에 쓰면 최대 8일치 미래 정보가 들어간다. 지수 날짜 + NSI_RELEASE_LAG_DAYS
# 이후에만 사용하는 보수적 정렬을 쓴다(관측된 공개 지연 반영). 2026-09-01 신(新)지수로 바뀌며 과거치가 새 방법으로
# 재계산되었으므로 백테스트는 '재계산된 이력' 기준이라는 한계가 있다(선행지수와 같은 주의).
NSI_STAT_CODE = os.environ.get('ECOS_NSI_STAT_CODE', '523Y001')   # 6.4. 뉴스심리지수(실험적 통계), 항목 A001 일별


NSI_ITEM_CODE = os.environ.get('ECOS_NSI_ITEM_CODE', 'A001')


# 2026-09-08(화) 조회에서 마지막 지수가 2026-08-31이었다. 즉 9/7(월) 공개분이 8/31까지였고, 한 번의 공개에
# 담긴 날짜들은 7~13일 전 값이다. 그래서 14일로 둔다. 몇 주 공개 패턴을 확인한 뒤 줄일 수 있다.
NSI_RELEASE_LAG_DAYS = 14


NSI_MAX_AGE_DAYS = 21


NSI_NOTE = ('뉴스심리지수는 일별 지수이지만 주 1회, 약 1주 지연 공개되므로 지수 날짜+14일 이후에만 사용. '
            '2026-09 신지수로 과거치가 재계산되어 백테스트는 재계산 이력 기준')




def ecos_key():
    key = os.environ.get('ECOS_API_KEY')
    if not key:
        try:
            from google.colab import userdata
            key = userdata.get('ECOS_API_KEY')
        except Exception:
            pass
    return key




def _ecos_request(key, path, retries=3):
    # URL에 키가 들어가므로 예외 메시지에 URL을 절대 싣지 않는다. KOSIS와 같은 이유로 재시도한다.
    url = f'https://ecos.bok.or.kr/api/{path.format(key=key)}'
    for attempt in range(retries):
        try:
            with open_url(url, accept='application/json') as response:
                result = json.loads(response.read().decode('utf-8-sig'))
            break
        except Exception as exc:
            detail = f'{type(exc).__name__} {getattr(exc, "code", "")}'.strip()
            if attempt == retries - 1:
                raise RuntimeError(f'ECOS API 조회 실패({detail}). 키·연결을 확인하거나 '
                                   'macro_inputs/news_sentiment.csv를 사용하세요.') from None
            time.sleep(3 * (2 ** attempt) + random.uniform(0, 3))
    if 'RESULT' in result:
        info = result['RESULT']
        raise RuntimeError(f"ECOS 응답 오류 {info.get('CODE', '')}: {info.get('MESSAGE', '')} "
                           f"(통계표 {NSI_STAT_CODE}/{NSI_ITEM_CODE}. tools/probe_ecos.py 로 코드를 확인하세요)")
    return result




def parse_ecos_daily(payload):
    rows = (payload.get('StatisticSearch') or {}).get('row') or []
    if not rows:
        raise ValueError('ECOS 응답에 일별 자료가 없습니다.')
    out = pd.DataFrame({'date': [str(r['TIME']) for r in rows],
                        'value': pd.to_numeric([r.get('DATA_VALUE') for r in rows], errors='coerce')})
    return normalize_daily(out)




def fetch_ecos_daily(stat_code, item_code, start, end, key):
    payload = _ecos_request(key, f'StatisticSearch/{{key}}/json/kr/1/100000/{stat_code}/D/'
                                 f'{pd.Timestamp(start):%Y%m%d}/{pd.Timestamp(end):%Y%m%d}/{item_code}')
    return parse_ecos_daily(payload)




def search_ecos_tables(key, keyword):
    """통계표 목록에서 이름에 keyword가 든 표와 그 항목 코드를 돌려준다(코드 확인용)."""
    tables = _ecos_request(key, 'StatisticTableList/{key}/json/kr/1/5000/')
    rows = (tables.get('StatisticTableList') or {}).get('row') or []
    hits = [r for r in rows if keyword in str(r.get('STAT_NAME', ''))]
    for r in hits:
        try:
            items = _ecos_request(key, f"StatisticItemList/{{key}}/json/kr/1/500/{r['STAT_CODE']}/")
            r['items'] = [(i.get('ITEM_CODE'), i.get('ITEM_NAME'), i.get('CYCLE')) for i in
                          (items.get('StatisticItemList') or {}).get('row') or []]
        except Exception:
            r['items'] = []
    return hits




def load_nsi(storage, start, end, use_cache=False, fallback_dir=None):
    """(DataFrame(date,value), info).

    우선순위: 명시적 캐시 재현 > macro_inputs CSV > ECOS API > 저장소 보관본.
    fallback_dir 는 다른 자료원(load_macro_data·load_cli·load_term_spread)과 같은 규약이다.
    이 인자가 없어서 장기 전망이 매번 TypeError 로 NSI 를 통째로 빠뜨리고 있었다(2026-09-08).
    """
    storage = Path(storage)
    cache = storage / 'macro_cache'
    cache.mkdir(parents=True, exist_ok=True)
    local = storage / 'macro_inputs' / 'news_sentiment.csv'
    cached = cache / 'news_sentiment.csv'
    fallback = Path(fallback_dir) / 'news_sentiment.csv' if fallback_dir else None
    error = None
    if use_cache and cached.exists():
        frame, source = normalize_daily(pd.read_csv(cached, dtype=str)), 'explicit_cache_replay'
    elif local.exists():
        frame, source = normalize_daily(pd.read_csv(local, dtype=str)), 'user_csv'
    else:
        try:
            key = ecos_key()
            if not key:
                raise RuntimeError('ECOS_API_KEY가 없습니다. Colab 보안 비밀/Secrets에 등록하거나 '
                                   'macro_inputs/news_sentiment.csv를 두세요.')
            frame, source = fetch_ecos_daily(NSI_STAT_CODE, NSI_ITEM_CODE, start, end, key), 'ECOS_API'
        except Exception as exc:
            if not (fallback and fallback.exists()):
                raise
            error = str(exc)
            frame, source = normalize_daily(pd.read_csv(fallback, dtype=str)), 'last_successful_fetch'
    frame.to_csv(cached, index=False)
    info = {'source': source, 'stat_code': NSI_STAT_CODE, 'item_code': NSI_ITEM_CODE,
            'fresh': source in ('ECOS_API', 'user_csv'), 'fetch_error': error,
            'first': frame['date'].min().date().isoformat(), 'last': frame['date'].max().date().isoformat(),
            'rows': int(len(frame)), 'release_lag_days': NSI_RELEASE_LAG_DAYS, 'note': NSI_NOTE,
            'snapshot_hash': hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()[:20]}
    return frame, info




def nsi_features(frame, dates, lag_days=NSI_RELEASE_LAG_DAYS, max_age_days=NSI_MAX_AGE_DAYS):
    """지수 날짜 + lag_days 이후에만 보이는 뉴스심리 특징. 달력일 기준 계열(주말 포함)."""
    daily = normalize_daily(frame).set_index('date')['value'].asfreq('D').ffill(limit=3)
    f = pd.DataFrame({
        'nsi_level': daily - 100.0,
        'nsi_change_5d': daily.diff(5),
        'nsi_change_20d': daily.diff(20),
        'nsi_z60': (daily - daily.rolling(60).mean()) / daily.rolling(60).std().replace(0, np.nan),
    })
    f.index = pd.DatetimeIndex(f.index) + pd.Timedelta(days=lag_days)      # 공개 이후에만 사용
    f = f.dropna(how='all')
    f['_expires'] = f.index + pd.Timedelta(days=max_age_days)
    dates = pd.DatetimeIndex(dates)
    left = pd.DataFrame({'available_date': dates.as_unit('ns'), '_order': np.arange(len(dates))}).sort_values('available_date')
    joined = pd.merge_asof(left, f.rename_axis('available_date').reset_index().assign(
        available_date=lambda d: pd.DatetimeIndex(d['available_date']).as_unit('ns')),
        on='available_date', direction='backward').sort_values('_order')
    valid = joined['available_date'] <= joined['_expires']
    out = pd.DataFrame(index=dates)
    for col in ['nsi_level', 'nsi_change_5d', 'nsi_change_20d', 'nsi_z60']:
        out[col] = joined[col].where(valid).to_numpy()
    return out




# ---------------------------------------------------------------------------
# 장단기 금리차 (ECOS 시장금리 일별: 국고채 10년 - 3년)
# ---------------------------------------------------------------------------
# 매일 나오고, 발표 지연이 없고, 소급 수정이 없다. CLI·선행지수·뉴스심리지수가 모두 갖는
# '사후에 보면 잘 맞는' 문제가 없는 유일한 후보라 합성 점수에 넣는다.
# 다만 장단기금리차는 한국 선행종합지수의 구성 항목이라 '선행지수를 앞선다'는 것은 구조적이다.
TERM_SPREAD_STAT_CODE = os.environ.get('ECOS_RATE_STAT_CODE', '817Y002')      # 시장금리(일별)


TERM_SPREAD_LONG_ITEM = os.environ.get('ECOS_RATE_LONG_ITEM', '010210000')    # 국고채(10년)


TERM_SPREAD_SHORT_ITEM = os.environ.get('ECOS_RATE_SHORT_ITEM', '010200000')  # 국고채(3년)




def load_term_spread(storage, start, end, use_cache=False, fallback_dir=None):
    """(DataFrame(date, value=10년-3년 %p), info). 우선순위: 캐시 재현 > 사용자 CSV > ECOS > 저장소 보관본."""
    storage = Path(storage)
    cache = storage / 'macro_cache'
    cache.mkdir(parents=True, exist_ok=True)
    local = storage / 'macro_inputs' / 'term_spread.csv'
    cached = cache / 'term_spread.csv'
    fallback = Path(fallback_dir) / 'term_spread.csv' if fallback_dir else None
    error = None
    if use_cache and cached.exists():
        frame, source = normalize_daily(pd.read_csv(cached, dtype=str)), 'explicit_cache_replay'
    elif local.exists():
        frame, source = normalize_daily(pd.read_csv(local, dtype=str)), 'user_csv'
    else:
        try:
            key = ecos_key()
            if not key:
                raise RuntimeError('ECOS_API_KEY가 없습니다.')
            long_ = fetch_ecos_daily(TERM_SPREAD_STAT_CODE, TERM_SPREAD_LONG_ITEM, start, end, key).set_index('date')['value']
            short = fetch_ecos_daily(TERM_SPREAD_STAT_CODE, TERM_SPREAD_SHORT_ITEM, start, end, key).set_index('date')['value']
            spread = (long_ - short).dropna()
            if spread.empty:
                raise ValueError('국고채 10년·3년 금리가 겹치는 날이 없습니다.')
            frame, source = pd.DataFrame({'date': spread.index, 'value': spread.to_numpy()}), 'ECOS_API'
        except Exception as exc:
            if not (fallback and fallback.exists()):
                raise
            error = str(exc)
            frame, source = normalize_daily(pd.read_csv(fallback, dtype=str)), 'last_successful_fetch'
    frame.to_csv(cached, index=False)
    info = {'source': source, 'fresh': source in ('ECOS_API', 'user_csv'), 'fetch_error': error,
            'first': frame['date'].min().date().isoformat(), 'last': frame['date'].max().date().isoformat(),
            'rows': int(len(frame)),
            'snapshot_hash': hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()[:20]}
    return frame, info




def monthly_mean_by_month_end(daily_frame, dates):
    """일별 계열을 '그 월말까지의 그 달 평균'으로. 금리처럼 지연 없는 자료에 쓴다(월말 인덱스용)."""
    series = normalize_daily(daily_frame).set_index('date')['value']
    monthly = series.resample('ME').mean()
    dates = pd.DatetimeIndex(dates)
    return monthly.reindex(dates, method='ffill', tolerance=pd.Timedelta(days=45))

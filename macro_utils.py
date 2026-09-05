"""KOSIS 월별 경기/반도체 지표. 최신 수정치의 지연 정렬은 과거 빈티지를 복원하지 않는다."""
import hashlib
import io
import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd


MACRO_SERIES = {
    'leading_cycle': {'orgId': '101', 'tblId': 'DT_1C8015', 'name': '선행지수 순환변동치',
                      'itmId': 'T1', 'unit': '2020=100'},
    'semiconductor_exports': {'orgId': '127', 'tblId': 'DT_092_115_2009_S023',
                              'name': '반도체', 'itmId': '13103131003T1', 'unit': 'USD'},
}
MACRO_HISTORY_NOTE = 'lagged_latest_vintage: 월+2 첫날부터 사용, 과거 수정치 누출 가능; 실시간 실적으로 재검증 필요'


def kosis_key():
    key = os.environ.get('KOSIS_API_KEY')
    if not key:
        try:
            from google.colab import userdata
            key = userdata.get('KOSIS_API_KEY')
        except Exception:
            pass
    return key


def _kosis_request(key, params):
    # HTTP exceptions contain the full URL and API key: never propagate or log them.
    query = {'method': 'getList', 'apiKey': key, 'format': 'json', 'jsonVD': 'Y',
             'prdSe': 'M', **params}
    try:
        with urlopen('https://kosis.kr/openapi/Param/statisticsParameterData.do?' + urlencode(query),
                     timeout=60) as response:
            result = json.loads(response.read().decode('utf-8-sig'))
    except Exception:
        raise RuntimeError('KOSIS API 조회 실패. 키 권한/연결을 확인하거나 macro_inputs CSV를 사용하세요.') from None
    if not isinstance(result, list) or not result:
        raise ValueError('KOSIS API에 수치가 없습니다. API 키, 통계표 접근 권한과 조회 기간을 확인하세요.')
    return result


def _series_rows(rows, series):
    if not isinstance(rows, list):
        raise ValueError('KOSIS가 통계 배열을 반환하지 않았습니다.')
    target = MACRO_SERIES[series]['name'].replace(' ', '')
    selected = [r for r in rows if str(r.get('C1_NM', '')).replace(' ', '') == target]
    if not selected:
        raise ValueError(f'KOSIS 통계표에서 정확한 {target} 합계 항목을 찾지 못했습니다.')
    return selected


def normalize_monthly(frame):
    out = frame[['month', 'value']].copy()
    months = out['month'].astype(str).str.replace(r'^(\d{4})[./](\d{1,2})$', r'\1-\2', regex=True)
    months = months.str.replace(r'^(\d{4})(\d{2})$', r'\1-\2', regex=True)
    out['month'] = pd.to_datetime(months, format='mixed', errors='raise').dt.to_period('M').dt.to_timestamp()
    out['value'] = pd.to_numeric(out['value'].astype(str).str.replace(',', ''), errors='coerce')
    if out['month'].duplicated().any():
        raise ValueError('월별 통계에 같은 월이 중복됩니다. 하나의 지표/단위만 선택하세요.')
    out.loc[~np.isfinite(out.value) | (out.value <= 0), 'value'] = np.nan
    if out.value.notna().sum() == 0:
        raise ValueError('월별 통계에 유효한 양수 값이 없습니다.')
    return out.sort_values('month').reset_index(drop=True)


def parse_kosis_rows(rows, series):
    selected = _series_rows(rows, series)
    parsed = []
    for row in selected:
        if row.get('PRD_SE', 'M') != 'M':
            raise ValueError('월별 통계가 필요합니다.')
        value = pd.to_numeric(str(row['DT']).replace(',', ''), errors='coerce')
        if series == 'semiconductor_exports':
            unit = str(row.get('UNIT_NM', '')).replace(' ', '').lower()
            multipliers = {'달러': 1, 'dollars': 1, 'us$': 1, 'usd': 1,
                           '천달러': 1000, '백만달러': 1000000, '억달러': 100000000}
            if unit not in multipliers:
                raise ValueError(f'반도체 수출액의 달러 단위를 확인할 수 없습니다: {unit}')
            value *= multipliers[unit]
        parsed.append({'month': row['PRD_DE'], 'value': value})
    return normalize_monthly(pd.DataFrame(parsed))


def fetch_kosis_monthly(series, start, end, key):
    spec = MACRO_SERIES[series]
    params = {k: spec[k] for k in ('orgId', 'tblId', 'itmId')}
    # Resolve exact aggregate by official classification name; never guess a component code.
    latest = _kosis_request(key, {**params, 'objL1': 'ALL', 'newEstPrdCnt': '1'})
    codes = {row['C1'] for row in _series_rows(latest, series)}
    if len(codes) != 1:
        raise ValueError('KOSIS 항목이 여러 코드에 대응합니다. 분류 개편 여부를 확인하세요.')
    rows = _kosis_request(key, {**params, 'objL1': codes.pop(),
                               'startPrdDe': pd.Timestamp(start).strftime('%Y%m'),
                               'endPrdDe': pd.Timestamp(end).strftime('%Y%m')})
    return parse_kosis_rows(rows, series)


def read_macro_csv(path, series):
    """Normalized month,value CSV or KOSIS time-on-columns CSV (exports: dollars)."""
    content = Path(path).read_bytes()
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = content.decode('cp949')
    frame = pd.read_csv(io.StringIO(text), dtype=str)
    if {'month', 'value'}.issubset(frame.columns):
        return normalize_monthly(frame)
    month_cols = [c for c in frame if re.fullmatch(r'\d{4}[./-]\d{1,2}|\d{6}', c.strip())]
    target = MACRO_SERIES[series]['name'].replace(' ', '')
    mask = frame.apply(lambda col: col.fillna('').str.replace(' ', '').eq(target)).any(axis=1)
    selected = frame.loc[mask]
    if len(selected) != 1 or not month_cols:
        raise ValueError(f'{path.name}: KOSIS에서 {target}만 선택하여 시점을 열로 CSV를 받거나 month,value 형식으로 저장하세요.')
    # KOSIS table's native export unit is dollars. Other CSV units must be explicit.
    factor = 1
    unit_cols = [c for c in frame if '단위' in c]
    if series == 'semiconductor_exports' and unit_cols:
        unit = str(selected.iloc[0][unit_cols[0]]).replace(' ', '')
        if unit not in {'달러', '천달러', '백만달러', '억달러'}:
            raise ValueError('CSV 수출액 단위를 확인하세요.')
        factor = {'달러': 1, '천달러': 1000, '백만달러': 1000000, '억달러': 100000000}[unit]
    result = normalize_monthly(pd.DataFrame({'month': month_cols, 'value': selected.iloc[0][month_cols].values}))
    result['value'] *= factor
    return result


def load_macro_data(storage, start, end, use_cache=False):
    storage = Path(storage)
    cache = storage / 'macro_cache'
    cache.mkdir(parents=True, exist_ok=True)
    key = kosis_key()
    data, sources = {}, {}
    for series, spec in MACRO_SERIES.items():
        local = storage / 'macro_inputs' / f'{series}.csv'
        cached = cache / f'{series}.csv'
        if use_cache and cached.exists():
            data[series] = read_macro_csv(cached, series)
            sources[series] = 'explicit_cache_replay'
        elif local.exists():
            data[series] = read_macro_csv(local, series)
            sources[series] = 'user_csv'
        elif key:
            data[series] = fetch_kosis_monthly(series, start, end, key)
            sources[series] = 'KOSIS_API'
        else:
            raise RuntimeError('월별 지표가 없습니다. Colab 보안 비밀에 KOSIS_API_KEY를 등록하거나 '
                               f'{local}에 공식 CSV를 저장하세요. 기존 모델만 실행하려면 USE_MACRO_FEATURES=False.')
        data[series].to_csv(cached, index=False)
    combined = pd.concat([f.assign(series=s) for s, f in data.items()], ignore_index=True)
    payload = combined.to_csv(index=False)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:20]
    snapshots = storage / 'macro_snapshots'
    snapshots.mkdir(parents=True, exist_ok=True)
    info = {'snapshot_hash': digest, 'retrieved_at_utc': pd.Timestamp.now(tz='UTC').isoformat(),
            'history_note': MACRO_HISTORY_NOTE, 'sources': sources, 'series': MACRO_SERIES,
            'latest_month': {s: f.loc[f.value.notna(), 'month'].max().date().isoformat() for s, f in data.items()}}
    path = snapshots / f'{digest}.csv'
    if not path.exists():
        path.write_text(payload, encoding='utf-8')
        path.with_suffix('.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    return data, info


def macro_features(data, dates, max_age_days=100):
    """Conservative MONTH+2 alignment, not an assertion about actual release dates.

    All rolling operations use calendar months (including missing months). Values expire.
    This is latest-vintage research; use immutable prospective forecasts to assess live value.
    """
    dates = pd.DatetimeIndex(dates).astype('datetime64[ns]')
    result = pd.DataFrame(index=dates)
    for series, frame in data.items():
        monthly = normalize_monthly(frame).set_index('month').asfreq('MS')['value']
        f = pd.DataFrame(index=monthly.index)
        if series == 'leading_cycle':
            f['macro_leading_cycle'] = monthly - 100
            f['macro_leading_change_1m'] = monthly.diff()
            f['macro_leading_change_3m'] = monthly.diff(3)
        elif series == 'semiconductor_exports':
            f['macro_semiconductor_log_usd'] = np.log(monthly)
            f['macro_semiconductor_yoy'] = monthly.pct_change(12, fill_method=None)
            f['macro_semiconductor_mom'] = monthly.pct_change(1, fill_method=None)
            f['macro_semiconductor_yoy_3m'] = f['macro_semiconductor_yoy'].rolling(3).mean()
        else:
            raise ValueError(f'Unknown macro series: {series}')
        f.index = (f.index + pd.offsets.MonthBegin(2)).astype('datetime64[ns]')
        joined = pd.merge_asof(pd.DataFrame({'available_date': dates}), f.rename_axis('available_date').reset_index(),
                               on='available_date', direction='backward', tolerance=pd.Timedelta(days=max_age_days))
        for col in f:
            result[col] = joined[col].to_numpy()
    return result

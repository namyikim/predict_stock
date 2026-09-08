"""KOSIS 월별 지표(선행지수·반도체 수출)와 발표 지연 정렬."""
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




MACRO_SERIES = {
    'leading_cycle': {'orgId': '101', 'tblId': 'DT_1C8015', 'name': '선행지수 순환변동치',
                      'itmId': 'T1', 'unit': '2020=100'},
    'semiconductor_exports': {'orgId': '127', 'tblId': 'DT_092_115_2009_S023',
                              'name': '반도체', 'itmId': '13103131003T1', 'unit': 'USD'},
}


OPTIONAL_MACRO_SERIES = {
    'daily_exports': {'name': '한국 일평균 수출액', 'unit': 'USD per working day'},
    'oecd_g20_cli': {'name': 'OECD Major G20 CLI (amplitude adjusted)', 'unit': 'long-term average=100'},
}


MACRO_HISTORY_NOTE = ('발표 이력 CSV는 각 발표/수정 시각 이후 사용. 이력이 없는 자료는 '
                      'lagged_latest_vintage(월+2 첫날) 가정이며 과거 수정치 누출 가능; 실시간 재검증 필요')




def kosis_key():
    key = os.environ.get('KOSIS_API_KEY')
    if not key:
        try:
            from google.colab import userdata
            key = userdata.get('KOSIS_API_KEY')
        except Exception:
            pass
    return key




def _kosis_request(key, params, retries=4):
    """KOSIS 조회. HTTP 예외에는 URL(=API 키)이 들어 있으므로 절대 그대로 올리지 않는다.

    같은 키로 여러 잡이 동시에 부르면 한쪽이 거절된다(2026-09-07: 종목 병렬 실행에서 삼성전자만 실패).
    그래서 지수 백오프로 재시도하고, 마지막에는 종류·HTTP 코드만 메시지에 남긴다 — 이것이 없으면
    '조회 실패' 한 줄만 남아 한도 초과인지 일시 장애인지 구분할 수 없다.
    """
    query = {'method': 'getList', 'apiKey': key, 'format': 'json', 'jsonVD': 'Y',
             'prdSe': 'M', **params}
    url = 'https://kosis.kr/openapi/Param/statisticsParameterData.do?' + urlencode(query)
    for attempt in range(retries):
        try:
            with open_url(url, accept='application/json') as response:
                result = json.loads(response.read().decode('utf-8-sig'))
            break
        except Exception as exc:
            detail = f'{type(exc).__name__} {getattr(exc, "code", "")}'.strip()
            if attempt == retries - 1:
                raise RuntimeError(f'KOSIS API 조회 실패({detail}). 키 권한·호출 한도·연결을 확인하거나 '
                                   'macro_inputs CSV를 사용하세요.') from None
            time.sleep(3 * (2 ** attempt) + random.uniform(0, 3))
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
    if series in OPTIONAL_MACRO_SERIES:
        raise ValueError(f'{path.name}: month,value와 선택적 released_at 형식이 필요합니다.')
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




def load_macro_data(storage, start, end, use_cache=False, fallback_dir=None):
    """월별 지표를 읽는다. 우선순위: 명시적 캐시 재현 > 사용자 CSV > KOSIS API > 최근 성공분(fallback).

    fallback_dir은 저장소에 보관해 둔 '마지막으로 조회에 성공한' CSV 폴더다. KOSIS는 해외 IP에서
    간헐적으로 연결 자체가 막히는데(URLError), 월 단위 지표라 며칠 전 값과 오늘 값이 같다. 그래서
    조회에 실패하면 그 값을 쓰고 출처를 sources에 남긴다 — 조용히 빠지거나 실행이 죽는 것보다 낫다.
    너무 오래된 값은 macro_features의 만료 규칙이 알아서 걸러낸다.
    """
    storage = Path(storage)
    cache = storage / 'macro_cache'
    cache.mkdir(parents=True, exist_ok=True)
    key = kosis_key()
    data, sources, errors = {}, {}, {}
    for series, spec in MACRO_SERIES.items():
        local = storage / 'macro_inputs' / f'{series}.csv'
        cached = cache / f'{series}.csv'
        fallback = Path(fallback_dir) / f'{series}.csv' if fallback_dir else None
        if use_cache and cached.exists():
            data[series] = read_macro_csv(cached, series)
            sources[series] = 'explicit_cache_replay'
        elif local.exists():
            data[series] = read_macro_csv(local, series)
            sources[series] = 'user_csv'
        elif key:
            try:
                data[series] = fetch_kosis_monthly(series, start, end, key)
                sources[series] = 'KOSIS_API'
            except Exception as exc:
                if not (fallback and fallback.exists()):
                    raise
                errors[series] = str(exc)
                data[series] = normalize_monthly(pd.read_csv(fallback, dtype=str))
                sources[series] = 'last_successful_fetch'
        elif fallback and fallback.exists():
            data[series] = normalize_monthly(pd.read_csv(fallback, dtype=str))
            sources[series] = 'last_successful_fetch'
        else:
            raise RuntimeError('월별 지표가 없습니다. Colab 보안 비밀에 KOSIS_API_KEY를 등록하거나 '
                               f'{local}에 공식 CSV를 저장하세요. 기존 모델만 실행하려면 USE_MACRO_FEATURES=False.')
        data[series].to_csv(cached, index=False)
    optional_status = {}
    for series in OPTIONAL_MACRO_SERIES:
        local = storage / 'macro_inputs' / f'{series}.csv'
        cached = cache / f'{series}.csv'
        path = cached if use_cache and cached.exists() else local
        if not path.exists():
            optional_status[series] = 'not_provided'
            continue
        try:
            frame = read_macro_csv(path, series)
            frame.to_csv(cached, index=False)
        except (ValueError, OSError, KeyError) as exc:
            optional_status[series] = f'invalid:{type(exc).__name__}'
            continue
        data[series] = frame
        sources[series] = 'explicit_cache_replay' if path == cached else 'user_csv'
        optional_status[series] = 'loaded'
    combined = pd.concat([f.assign(series=s) for s, f in data.items()], ignore_index=True)
    payload = combined.to_csv(index=False)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:20]
    snapshots = storage / 'macro_snapshots'
    snapshots.mkdir(parents=True, exist_ok=True)
    info = {'snapshot_hash': digest, 'retrieved_at_utc': pd.Timestamp.now(tz='UTC').isoformat(),
            'fetch_errors': errors,
            'fresh': all(v in ('KOSIS_API', 'user_csv') for v in sources.values()),
            'history_note': MACRO_HISTORY_NOTE, 'sources': sources,
            'series': {s: {**MACRO_SERIES, **OPTIONAL_MACRO_SERIES}[s] for s in data},
            'optional_status': optional_status,
            'availability_modes': {s: ('supplied_release_history' if 'released_at' in f else
                                       'lagged_latest_vintage') for s, f in data.items()},
            'latest_month': {s: f.loc[f.value.notna(), 'month'].max().date().isoformat() for s, f in data.items()}}
    path = snapshots / f'{digest}.csv'
    if not path.exists():
        path.write_text(payload, encoding='utf-8')
        path.with_suffix('.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    return data, info




def _monthly_features(series, monthly):
    f = pd.DataFrame(index=monthly.index)
    if series in ('leading_cycle', 'oecd_g20_cli'):
        prefix = 'macro_leading' if series == 'leading_cycle' else 'macro_oecd_g20_cli'
        f[prefix + ('_cycle' if series == 'leading_cycle' else '_level')] = monthly - 100
        f[prefix + '_change_1m'] = monthly.diff()
        f[prefix + '_change_3m'] = monthly.diff(3)
    elif series in ('semiconductor_exports', 'daily_exports'):
        prefix = 'macro_semiconductor' if series == 'semiconductor_exports' else 'macro_daily_exports'
        f[prefix + '_log_usd'] = np.log(monthly)
        yoy = monthly.pct_change(12, fill_method=None)
        f[prefix + '_yoy'] = yoy
        f[prefix + '_mom'] = monthly.pct_change(1, fill_method=None)
        f[prefix + '_yoy_3m'] = yoy.rolling(3).mean()
        f[prefix + '_yoy_change_1m'] = yoy.diff()
        f[prefix + '_yoy_change_3m'] = yoy.diff(3)
    else:
        raise ValueError(f'Unknown macro series: {series}')
    return f




def macro_features(data, dates, max_age_days=100, prediction_hour=7):
    """Use releases known at the specified Seoul prediction hour; never backfill revisions.

    Date-only monthly inputs retain the conservative MONTH+2 latest-vintage assumption.
    Explicit released_at rows must contain the actual values published at those instants,
    not today's revised values labelled with original publication dates.
    """
    if not 0 <= prediction_hour < 24:
        raise ValueError('prediction_hour must be in [0, 24)')
    dates = pd.DatetimeIndex(dates)
    local = dates.tz_localize('Asia/Seoul') if dates.tz is None else dates.tz_convert('Asia/Seoul')
    cutoff = (local.normalize() + pd.Timedelta(hours=prediction_hour)).tz_convert('UTC').as_unit('ns')
    left = pd.DataFrame({'available_date': cutoff, '_order': np.arange(len(dates))}).sort_values('available_date')
    result = pd.DataFrame(index=dates)
    for series, frame in data.items():
        monthly = normalize_monthly(frame)
        if 'released_at' not in monthly:
            f = _monthly_features(series, monthly.set_index('month').asfreq('MS')['value'])
            f.index = (f.index + pd.offsets.MonthBegin(2)).tz_localize('Asia/Seoul').tz_convert('UTC').as_unit('ns')
            f['_expires'] = f.index + pd.Timedelta(days=max_age_days, hours=prediction_hour)
        else:
            events = []
            known = pd.Series(dtype=float)
            first_release = {}
            for released, batch in monthly.sort_values('released_at').groupby('released_at', sort=True):
                if released > cutoff.max():
                    break
                for row in batch.itertuples():
                    known.loc[row.month] = row.value
                    first_release.setdefault(row.month, released)
                known = known.sort_index()
                values = _monthly_features(series, known.asfreq('MS')).iloc[-1].to_dict()
                values['available_date'] = released
                # Revising an old month must not make an obsolete last observation fresh.
                values['_expires'] = first_release[known.index[-1]] + pd.Timedelta(days=max_age_days)
                events.append(values)
            if not events:
                cols = _monthly_features(series, pd.Series(dtype=float)).columns
                result[cols] = np.nan
                continue
            f = pd.DataFrame(events).set_index('available_date')
            f.index = pd.DatetimeIndex(f.index).as_unit('ns')
        joined = pd.merge_asof(left, f.rename_axis('available_date').reset_index(),
                               on='available_date', direction='backward').sort_values('_order')
        valid = joined['available_date'] <= joined['_expires']
        for col in f.columns.difference(['_expires'], sort=False):
            result[col] = joined[col].where(valid).to_numpy()
    return result

"""KOSIS 월별 경기/반도체 지표. 최신 수정치의 지연 정렬은 과거 빈티지를 복원하지 않는다."""
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
            with urlopen(url, timeout=60) as response:
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
            with urlopen(url, timeout=60) as response:
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


def load_nsi(storage, start, end, use_cache=False):
    """(DataFrame(date,value), info). 우선순위: 명시적 캐시 재현 > macro_inputs CSV > ECOS API."""
    storage = Path(storage)
    cache = storage / 'macro_cache'
    cache.mkdir(parents=True, exist_ok=True)
    local = storage / 'macro_inputs' / 'news_sentiment.csv'
    cached = cache / 'news_sentiment.csv'
    if use_cache and cached.exists():
        frame, source = normalize_daily(pd.read_csv(cached, dtype=str)), 'explicit_cache_replay'
    elif local.exists():
        frame, source = normalize_daily(pd.read_csv(local, dtype=str)), 'user_csv'
    else:
        key = ecos_key()
        if not key:
            raise RuntimeError('ECOS_API_KEY가 없습니다. Colab 보안 비밀/Secrets에 등록하거나 macro_inputs/news_sentiment.csv를 두세요.')
        frame, source = fetch_ecos_daily(NSI_STAT_CODE, NSI_ITEM_CODE, start, end, key), 'ECOS_API'
    frame.to_csv(cached, index=False)
    info = {'source': source, 'stat_code': NSI_STAT_CODE, 'item_code': NSI_ITEM_CODE,
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
            with urlopen(url, timeout=60) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as exc:
            detail = f'{type(exc).__name__} {getattr(exc, "code", "")}'.strip()
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
                with urlopen(url.format(area=ref_area, start=start_text), timeout=90) as response:
                    return parse_oecd_csv(response.read().decode('utf-8-sig'), ref_area)
            except Exception as exc:
                last = f'{type(exc).__name__} {getattr(exc, "code", "")}'.strip()
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


# ---------------------------------------------------------------------------
# 일평균 수출: 월 합계 / 조업일수(주중일 - 한국 휴장일 근사)
# ---------------------------------------------------------------------------
def korean_working_days(month_start):
    """그 달의 조업일수 근사. 관세청 조업일수(토요일 0.5일)와 정확히 같지는 않지만 달력 효과의 대부분을 없앤다."""
    month_start = pd.Timestamp(month_start).normalize().replace(day=1)
    month_end = month_start + pd.offsets.MonthEnd(0)
    days = pd.bdate_range(month_start, month_end)
    try:
        import exchange_calendars as xc
        cal = xc.get_calendar('XKRX')
        sessions = cal.sessions_in_range(month_start.strftime('%Y-%m-%d'), month_end.strftime('%Y-%m-%d'))
        return float(len(sessions))
    except Exception:
        return float(len(days))


def daily_average(monthly_frame):
    """DataFrame(month, value) → DataFrame(month, value=일평균). 조업일수로 나눈다."""
    out = normalize_monthly(monthly_frame).copy()
    out['value'] = [v / max(korean_working_days(m), 1.0) for m, v in zip(out['month'], out['value'])]
    return out


# ---------------------------------------------------------------------------
# 관세청 품목별 수출입실적 (공공데이터포털 /1220000/Itemtrade/getItemtradeList)
# ---------------------------------------------------------------------------
# KOSIS 품목별 월 확정치는 관세청 원천보다 2~5주 늦다. 같은 숫자를 원천에서 바로 받으면 그만큼
# 나우캐스트를 앞당길 수 있다. 응답은 HS 10자리로 잘게 나오므로 8541(개별소자)·8542(집적회로)를
# 각각 합산한다. expDlr 단위는 달러다(2026-06 디램 111.7억 달러로 확인).
CUSTOMS_URL = 'https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList'
CUSTOMS_HS = ('8541', '8542')       # 반도체: 개별소자 + 집적회로


def data_go_kr_key():
    key = os.environ.get('DATA_GO_KR_KEY')
    if not key:
        try:
            from google.colab import userdata
            key = userdata.get('DATA_GO_KR_KEY')
        except Exception:
            pass
    if key and '%' in key:
        from urllib.parse import unquote
        key = unquote(key)          # Encoding용 키를 넣어도 동작하게 한다
    return key


def parse_customs_xml(text):
    """<item> 목록 → DataFrame(month, value=수출 달러). HS 세부 코드를 월별로 합산한다."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(text)
    message = root.findtext('.//resultMsg') or ''
    code = root.findtext('.//resultCode')
    if code not in (None, '00', '0'):
        raise RuntimeError(f'관세청 API 오류 {code}: {message}')
    rows = []
    for item in root.iter('item'):
        period = (item.findtext('year') or '').strip()
        amount = (item.findtext('expDlr') or '').strip()
        if not period or not amount:
            continue
        # 'year'에는 2026.06(월별)과 2026(연 합계)이 섞여 온다. 월별만 쓴다.
        if not re.fullmatch(r'\d{4}[.\-/]\d{2}', period):
            continue
        try:
            rows.append({'month': period.replace('.', '-').replace('/', '-'), 'value': float(amount.replace(',', ''))})
        except ValueError:
            continue
    if not rows:
        raise ValueError('관세청 응답에 월별 수출액이 없습니다.')
    frame = pd.DataFrame(rows).groupby('month', as_index=False)['value'].sum()
    return normalize_monthly(frame)


def fetch_customs_exports(start, end, key, hs_codes=CUSTOMS_HS, retries=3):
    """반도체 월별 수출액(달러). HS 대분류별로 조회해 합산한다."""
    start_text = pd.Timestamp(start).strftime('%Y%m')
    end_text = pd.Timestamp(end).strftime('%Y%m')
    total = None
    for hs in hs_codes:
        query = urlencode({'serviceKey': key, 'strtYymm': start_text, 'endYymm': end_text, 'hsSgn': hs})
        for attempt in range(retries):
            try:
                with urlopen(f'{CUSTOMS_URL}?{query}', timeout=90) as response:
                    frame = parse_customs_xml(response.read().decode('utf-8'))
                break
            except Exception as exc:
                detail = f'{type(exc).__name__} {getattr(exc, "code", "")}'.strip()
                if attempt == retries - 1:
                    raise RuntimeError(f'관세청 조회 실패({detail}, HS {hs}). 키·활용신청 상태를 확인하세요.') from None
                time.sleep(3 * (2 ** attempt) + random.uniform(0, 3))
        series = frame.set_index('month')['value']
        total = series if total is None else total.add(series, fill_value=0)
    return pd.DataFrame({'month': total.index, 'value': total.to_numpy()})


def reconcile_customs(kosis, customs, tolerance=0.15, min_overlap=6):
    """관세청 계열이 KOSIS 확정치와 같은 크기인지 확인한다.

    품목 정의나 단위가 다르면 조용히 1000배 틀린 값이 들어간다. 겹치는 달의 비율 중앙값이 1에서
    tolerance 이상 벗어나면 쓰지 않는다. 반환: (사용 가능 여부, 진단 정보)
    """
    k = normalize_monthly(kosis).set_index('month')['value']
    c = normalize_monthly(customs).set_index('month')['value']
    overlap = k.index.intersection(c.index)
    info = {'overlap': int(len(overlap)), 'ratio_median': None, 'ratio_min': None, 'ratio_max': None}
    if len(overlap) < min_overlap:
        info['reason'] = f'겹치는 달이 {len(overlap)}개뿐이라 검증할 수 없습니다.'
        return False, info
    ratio = (c.loc[overlap] / k.loc[overlap]).replace([np.inf, -np.inf], np.nan).dropna()
    info.update(ratio_median=float(ratio.median()), ratio_min=float(ratio.min()), ratio_max=float(ratio.max()))
    if not (1 - tolerance) <= info['ratio_median'] <= (1 + tolerance):
        info['reason'] = (f"KOSIS 대비 배율 중앙값이 {info['ratio_median']:.3f}로 1에서 많이 벗어납니다"
                          ' — 품목 정의나 단위가 다를 수 있어 쓰지 않습니다.')
        return False, info
    return True, info


def merge_customs_exports(kosis, customs):
    """KOSIS 확정치를 그대로 두고, KOSIS에 아직 없는 달만 관세청 값으로 채운다."""
    k = normalize_monthly(kosis).set_index('month')['value']
    c = normalize_monthly(customs).set_index('month')['value']
    added = [m for m in c.index if m not in k.index]
    merged = pd.concat([k, c.loc[added]]).sort_index()
    return (pd.DataFrame({'month': merged.index, 'value': merged.to_numpy()}),
            [pd.Timestamp(m).strftime('%Y-%m') for m in added])


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
        request = urllib_request_with_agent(url)
        with urlopen(request, timeout=60) as response:
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


def urllib_request_with_agent(url):
    from urllib.request import Request
    return Request(url, headers={'User-Agent': 'Mozilla/5.0 (predict_stock research; non-commercial)'})


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

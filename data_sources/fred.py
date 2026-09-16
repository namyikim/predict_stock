"""FRED(세인트루이스 연준) 시계열. 키 없이 CSV 로 받을 수 있다.

야후로는 닿지 않는 긴 이력에 쓴다 — 일본 10년물은 야후에 없고, 엔/달러도 야후는 1996년부터인데
FRED 는 1971년부터다. 받은 것은 보관본에 누적해 조회가 막혀도 그림이 비지 않게 한다.
"""
import io

import pandas as pd

from data_sources._common import *  # noqa: F401,F403

FRED_CSV = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}'
# 미·일 금리차 그림에 쓰는 세 계열
US10Y, JP10Y, USDJPY = 'DGS10', 'IRLTLT01JPM156N', 'EXJPUS'
US_CPI = 'CPIAUCSL'          # 미국 CPI(계절조정, 1982-84=100)
HIGH_YIELD_SPREAD = 'BAMLH0A0HYM2'  # ICE BofA 미국 하이일드 OAS(%p)
NASDAQ = 'NASDAQCOM'                 # 나스닥 종합지수


def fetch_fred_daily(series_id, retries=3):
    """FRED 원래 관측 주기를 보존한다. '.' 결측은 날짜를 남기지 않고 버린다."""
    for attempt in range(retries):
        try:
            with open_url(FRED_CSV.format(sid=series_id), timeout=60, accept='text/csv, */*') as response:
                text = response.read().decode('utf-8', errors='ignore')
            frame = pd.read_csv(io.StringIO(text))
            date_col, value_col = frame.columns[0], frame.columns[-1]
            frame[date_col] = pd.to_datetime(frame[date_col], errors='coerce')
            frame[value_col] = pd.to_numeric(frame[value_col], errors='coerce')
            series = frame.dropna(subset=[date_col, value_col]).set_index(date_col)[value_col]
            if series.empty:
                raise ValueError(f'FRED {series_id} 응답이 비어 있습니다.')
            return series[~series.index.duplicated(keep='last')].sort_index().rename(series_id)
        except Exception as exc:
            if attempt == retries - 1:
                raise RuntimeError(f'FRED {series_id} 조회 실패({error_detail(exc)}).') from None
            time.sleep(3 * (2 ** attempt) + random.uniform(0, 2))


def fetch_fred(series_id, retries=3):
    """월평균 Series. 일별 계열은 월로 줄이고, '.' 로 온 결측은 버린다."""
    return fetch_fred_daily(series_id, retries=retries).resample('MS').mean().dropna().rename(series_id)


def build_us_market_inputs(fetch=True, cache_path=None):
    """하이일드 OAS·미국 10년물·나스닥의 일별 자료와 진단을 돌려준다.

    시작일은 세 계열이 처음으로 모두 관측된 날이다. 휴장·결측일은 채우지 않는다.
    """
    base = None
    if cache_path is not None and Path(cache_path).exists():
        try:
            raw = pd.read_csv(cache_path)
            raw[raw.columns[0]] = pd.to_datetime(raw[raw.columns[0]], errors='coerce')
            base = raw.dropna(subset=[raw.columns[0]]).set_index(raw.columns[0])
        except Exception:
            base = None
    info = {'failed': {}}
    if fetch:
        fresh = {}
        for name, sid in (('high_yield_spread', HIGH_YIELD_SPREAD), ('us10y', US10Y), ('nasdaq', NASDAQ)):
            try:
                fresh[name] = fetch_fred_daily(sid)
            except Exception as exc:
                info['failed'][name] = str(exc)[:120]
        fresh = pd.DataFrame(fresh)
        if base is None:
            frame = fresh
        elif len(fresh):
            frame = fresh.combine_first(base)
            for column in fresh.columns:
                frame.loc[fresh.index, column] = fresh[column].where(
                    fresh[column].notna(), frame.loc[fresh.index, column])
        else:
            frame = base
        info['source'] = 'fred' if len(fresh) else 'cache'
    else:
        if base is None:
            raise RuntimeError('미국 금융시장 보관본이 없고 조회도 하지 않았습니다.')
        frame, info['source'] = base, 'cache'
    columns = ['high_yield_spread', 'us10y', 'nasdaq']
    present = [name for name in columns if name in frame]
    if len(present) == len(columns):
        common = frame[columns].dropna()
        if len(common):
            frame = frame.loc[common.index.min():]
    frame = frame.reindex(columns=present).dropna(how='all').sort_index()
    frame.index.name = 'date'
    info.update({'rows': int(len(frame)), 'columns': list(frame.columns),
                 'first': f'{frame.index.min():%Y-%m-%d}' if len(frame) else None,
                 'last': f'{frame.index.max():%Y-%m-%d}' if len(frame) else None})
    return frame, info


def build_us_jp_inputs(fetch=True, cache_path=None):
    """(DataFrame(month, us10y, jp10y, usdjpy, rate_gap), info). rate_gap = 미국 − 일본."""
    base = None
    if cache_path is not None and Path(cache_path).exists():
        try:
            raw = pd.read_csv(cache_path)
            raw[raw.columns[0]] = pd.to_datetime(raw[raw.columns[0]], errors='coerce')
            base = raw.dropna(subset=[raw.columns[0]]).set_index(raw.columns[0])
        except Exception:
            base = None
    info = {'failed': {}}
    if fetch:
        fresh = {}
        for name, sid in (('us10y', US10Y), ('jp10y', JP10Y), ('usdjpy', USDJPY)):
            try:
                fresh[name] = fetch_fred(sid)
            except Exception as exc:
                info['failed'][name] = str(exc)[:120]
        fresh = pd.DataFrame(fresh)
        if base is None:
            frame = fresh
        elif len(fresh):
            frame = fresh.combine_first(base)
            for column in fresh.columns:
                frame.loc[fresh.index, column] = fresh[column].where(
                    fresh[column].notna(), frame.loc[fresh.index, column])
        else:
            frame = base
        info['source'] = 'fred' if len(fresh) else 'cache'
    else:
        if base is None:
            raise RuntimeError('미·일 보관본이 없고 조회도 하지 않았습니다.')
        frame, info['source'] = base, 'cache'
    if 'us10y' in frame and 'jp10y' in frame:
        frame['rate_gap'] = frame['us10y'] - frame['jp10y']
    frame = frame.sort_index()
    frame.index.name = 'month'
    info.update({'rows': int(len(frame)), 'columns': list(frame.columns),
                 'first': f'{frame.index.min():%Y-%m}' if len(frame) else None,
                 'last': f'{frame.index.max():%Y-%m}' if len(frame) else None})
    return frame, info

"""원/달러 결정 요인 분석에 쓸 월별 계열 여섯 개.

야후(환율·달러지수·미10년물)와 한국은행 ECOS(국고채·경상수지)에서 받는다. 받은 것은
macro_history/fx_inputs.csv 에 누적해, 다음 실행이 조회에 실패해도 표가 비지 않게 한다.

한·미 금리차는 '한국 국고채 10년 − 미국 10년물'이다. 정책금리가 아니라 시장금리를 쓰는 이유는
환율이 반응하는 것이 정책 결정 자체보다 시장이 반영한 기대이기 때문이고, 두 나라 모두 같은
만기를 써야 비교가 되기 때문이다.
"""
import os
from datetime import timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd


from data_sources._common import *  # noqa: F401,F403

# 야후 티커. 월평균으로 줄여 쓴다(일별은 잡음이 크고 경상수지가 월별이라 맞춰야 한다).
YAHOO_SERIES = {"usdkrw": "KRW=X", "dxy": "DX-Y.NYB", "jpy": "JPY=X",
                "cny": "CNY=X", "us10y": "^TNX"}
# 국고채 10년(일별 시장금리)
KR10Y_STAT, KR10Y_ITEM = '817Y002', '010210000'
# 경상수지(월별, 백만 달러). 통계표·항목 코드는 환경변수로 바꿀 수 있다.
CA_STAT = os.environ.get('ECOS_CA_STAT_CODE', '301Y013')
CA_ITEM = os.environ.get('ECOS_CA_ITEM_CODE', '000000')


def fetch_yahoo_monthly(tickers=YAHOO_SERIES, start='2000-01-01'):
    """월평균. 실패한 티커는 빼고 그 사실을 함께 돌려준다."""
    import yfinance as yf
    frames, failed = {}, {}
    for name, ticker in tickers.items():
        try:
            history = yf.download(ticker, start=start, progress=False,
                                  auto_adjust=False, threads=False)
            if history is None or history.empty:
                failed[name] = '빈 응답'
                continue
            close = history['Close']
            if hasattr(close, 'columns'):
                close = close.iloc[:, 0]
            monthly = close.dropna().resample('MS').mean()
            if monthly.empty:
                failed[name] = '월평균이 비었습니다'
                continue
            frames[name] = monthly
        except Exception as exc:
            failed[name] = f'{type(exc).__name__}: {exc}'[:100]
    return pd.DataFrame(frames), failed


def build_fx_inputs(start='2000-01-01', end=None, fetch=True, cache_path=None,
                    korea_rate_fn=None, current_account_fn=None):
    """(DataFrame(month, 여섯 계열), info). 받은 것을 보관본과 합쳐 누적한다."""
    end = pd.Timestamp(end or pd.Timestamp.now(tz=KST).date())
    columns = ['usdkrw', 'dxy', 'jpy', 'cny', 'rate_gap', 'current_account']
    base = None
    if cache_path is not None and Path(cache_path).exists():
        try:
            base = pd.read_csv(cache_path, parse_dates=['month']).set_index('month')
        except Exception:
            base = None

    info = {'failed': {}, 'source': 'cache' if base is not None else 'none'}
    if not fetch:
        if base is None:
            raise RuntimeError('fx_inputs 보관본이 없고 조회도 하지 않았습니다.')
        frame = base
    else:
        market, failed = fetch_yahoo_monthly(start=start)
        info['failed'].update(failed)
        fresh = pd.DataFrame(index=market.index)
        for name in ('usdkrw', 'dxy', 'jpy', 'cny'):
            if name in market:
                fresh[name] = market[name]
        # 금리차 = 한국 10년 − 미국 10년. ^TNX 는 10배 표기라 10으로 나눈다.
        if 'us10y' in market and korea_rate_fn is not None:
            try:
                korea = korea_rate_fn(start, end)
                fresh['rate_gap'] = korea.reindex(market.index) - market['us10y'] / 10.0
            except Exception as exc:
                info['failed']['rate_gap'] = f'{type(exc).__name__}: {exc}'[:100]
        else:
            info['failed']['rate_gap'] = '미국 10년물을 받지 못했습니다.'
        if korea_rate_fn is None:
            info['failed'].setdefault('rate_gap', '한국 금리 조회 함수가 없습니다.')
        if current_account_fn is None:
            info['failed'].setdefault('current_account', '경상수지 조회 함수가 없습니다.')
        try:
            if current_account_fn is None:
                raise RuntimeError('경상수지 조회 함수가 없습니다.')
            fresh['current_account'] = current_account_fn(start, end).reindex(market.index)
        except Exception as exc:
            info['failed']['current_account'] = f'{type(exc).__name__}: {exc}'[:100]

        fresh = fresh.dropna(how='all')
        if base is None:
            frame = fresh
        else:
            frame = fresh.combine_first(base) if len(fresh) else base
        info['source'] = 'yahoo+ecos' if len(fresh) else 'cache'

    frame = frame.reindex(columns=[c for c in columns if c in frame.columns]).sort_index()
    info.update({'rows': int(len(frame)), 'columns': list(frame.columns),
                 'first': f'{frame.index.min():%Y-%m}' if len(frame) else None,
                 'last': f'{frame.index.max():%Y-%m}' if len(frame) else None})
    return frame, info

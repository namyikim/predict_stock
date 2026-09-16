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
                    korea_rate_fn=None, current_account_fn=None,
                    korea_cpi_fn=None, us_cpi_fn=None):
    """(DataFrame(month, 여섯 계열), info). 받은 것을 보관본과 합쳐 누적한다."""
    end = pd.Timestamp(end or pd.Timestamp.now(tz=KST).date())
    columns = ['usdkrw', 'dxy', 'jpy', 'cny', 'rate_gap', 'current_account',
               'kr10y', 'us10y', 'real_rate_gap']
    base = None
    if cache_path is not None and Path(cache_path).exists():
        try:
            raw = pd.read_csv(cache_path)
            # 저장할 때 색인 이름이 야후 그대로 'Date' 로 남아 'month' 를 찾던 읽기가 실패했다
            # (2026-09-16). 첫 열을 날짜로 본다 — 이름이 무엇이든.
            date_col = raw.columns[0]
            raw[date_col] = pd.to_datetime(raw[date_col], errors='coerce')
            base = raw.dropna(subset=[date_col]).set_index(date_col)
            base.index.name = 'month'
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
        # 금리차 = 한국 10년 − 미국 10년. yfinance 의 ^TNX 종가는 이미 % 단위(예: 4.25)다.
        # 처음에 10으로 나눠 미국 금리가 0.4 가 됐고, 금리차가 사실상 한국 금리 수준이 돼 버렸다
        # (2026-09-16: 2024년 평균 +2.80 으로 나와 잡았다. 실제로는 -0.9 안팎).
        if 'us10y' in market and korea_rate_fn is not None:
            try:
                korea = korea_rate_fn(start, end)
                us = market['us10y']
                # 만약 10배 표기(40 대)로 오면 그때만 나눈다.
                if us.dropna().median() > 20:
                    us = us / 10.0
                fresh['rate_gap'] = korea.reindex(market.index) - us
                fresh['kr10y'] = korea.reindex(market.index)
                fresh['us10y'] = us
                # 실질금리차 = (한국 명목 − 한국 물가상승률) − (미국 명목 − 미국 물가상승률).
                # 물가상승률은 CPI 의 12개월 전 대비. 기대인플레이션의 가장 단순한 대리다.
                if korea_cpi_fn is not None and us_cpi_fn is not None:
                    try:
                        kr_cpi = korea_cpi_fn(start, end).reindex(market.index)
                        us_cpi = us_cpi_fn().reindex(market.index)
                        kr_infl = (kr_cpi / kr_cpi.shift(12) - 1) * 100
                        us_infl = (us_cpi / us_cpi.shift(12) - 1) * 100
                        fresh['real_rate_gap'] = (fresh['kr10y'] - kr_infl) - (fresh['us10y'] - us_infl)
                    except Exception as exc:
                        info['failed']['real_rate_gap'] = f'{type(exc).__name__}: {exc}'[:100]
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
            # 새로 받은 값이 이긴다. 보관본은 '받지 못한 달'을 채우는 용도이지 새 값을 덮는 용도가
            # 아니다 — 계산 방식을 고쳤을 때 옛 값이 남으면 고친 의미가 없다.
            frame = fresh.combine_first(base) if len(fresh) else base
            for column in fresh.columns:
                frame.loc[fresh.index, column] = fresh[column].where(fresh[column].notna(),
                                                                     frame.loc[fresh.index, column])
        info['source'] = 'yahoo+ecos' if len(fresh) else 'cache'

    frame = frame.reindex(columns=[c for c in columns if c in frame.columns]).sort_index()
    frame.index.name = 'month'
    info.update({'rows': int(len(frame)), 'columns': list(frame.columns),
                 'first': f'{frame.index.min():%Y-%m}' if len(frame) else None,
                 'last': f'{frame.index.max():%Y-%m}' if len(frame) else None})
    return frame, info

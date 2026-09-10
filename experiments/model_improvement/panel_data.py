# -*- coding: utf-8 -*-
"""P10 — 공동 학습용 국내 반도체 관련 종목 패널.

선정 규칙과 기준일을 여기 고정한다. 목록은 2026-09-10 시점에 상장돼 있는 종목이므로
과거로 적용하면 **생존 편향**이 있다(그 사이 상장폐지·합병된 종목이 빠져 있다). 그 사실을
manifest와 decision에 남기고, 종목 수 증가를 독립 날짜 표본 증가로 주장하지 않는다.

한국 종목은 패널(date × instrument)로 함께 학습하고, 미국 종목은 기존 노트북처럼 한국 마감
시각에 정렬한 외부 변수로만 쓴다(여기서는 다루지 않는다).
"""
import numpy as np
import pandas as pd

SELECTION_DATE = "2026-09-10"
SELECTION_RULE = ("KRX 상장 반도체·반도체 장비·소재 대형주. 삼성전자·SK하이닉스와 같은 수요(메모리·"
                  "파운드리) 또는 공급망(장비·소재)에 묶인 종목. 2015-01-01 이전 상장, 일평균 거래대금 상위.")

# 종목 코드, 이름, 역할. 두 대상 종목을 포함한다.
PANEL_UNIVERSE = [
    ("005930.KS", "삼성전자", "target"),
    ("000660.KS", "SK하이닉스", "target"),
    ("042700.KS", "한미반도체", "equipment"),
    ("403870.KS", "HPSP", "equipment"),           # 2022 상장 → 기준(2015 이전) 미달, 제외 사유 기록용
    ("058470.KQ", "리노공업", "test"),
    ("240810.KQ", "원익IPS", "equipment"),       # 2016 분할 재상장 → 미달
    ("036930.KQ", "주성엔지니어링", "equipment"),
    ("000990.KS", "DB하이텍", "foundry"),
    ("005290.KQ", "동진쎄미켐", "material"),
    ("357780.KQ", "솔브레인", "material"),        # 2020 분할 재상장 → 미달
    ("039030.KQ", "이오테크닉스", "equipment"),
    ("095340.KQ", "ISC", "test"),
]
MIN_LISTED_BEFORE = "2015-01-01"


def eligible_universe(first_dates):
    """first_dates: {ticker: 첫 거래일}. 기준일 이전 상장 종목만 남기고 제외 사유를 돌려준다."""
    kept, excluded = [], {}
    cutoff = pd.Timestamp(MIN_LISTED_BEFORE)
    for ticker, name, role in PANEL_UNIVERSE:
        first = first_dates.get(ticker)
        if first is None:
            excluded[ticker] = f"{name}: 시세 없음"
        elif pd.Timestamp(first) > cutoff:
            excluded[ticker] = f"{name}: 첫 거래일 {pd.Timestamp(first).date()} > {MIN_LISTED_BEFORE}"
        else:
            kept.append((ticker, name, role))
    return kept, excluded


def instrument_features(close, volume, value=None):
    """종목 간 비교 가능한 입력만 만든다: 수익률·변동성·거래대금 비율. 가격 수준은 넣지 않는다.

    행 d는 d일 종가까지의 정보다. 결측(거래정지·상장 전)은 그대로 NaN으로 두고 보간하지 않는다.
    """
    close = close.astype(float)
    ret = close.pct_change()
    f = pd.DataFrame(index=close.index)
    f["ret_1"], f["ret_2"], f["ret_3"] = ret, ret.shift(1), ret.shift(2)
    for n in (5, 10, 20):
        f[f"mom_{n}"] = close / close.shift(n) - 1
    f["vol_20"] = ret.rolling(20).std()
    f["vol_ratio"] = ret.rolling(5).std() / f["vol_20"]
    f["ma50_gap"] = close / close.rolling(50).mean() - 1
    if volume is not None:
        turnover = (close * volume.astype(float)) if value is None else value.astype(float)
        f["turnover_ratio_20"] = turnover / turnover.rolling(20).mean()
    return f


def build_panel(bars_by_ticker, band_mult=0.3):
    """{ticker: bars(open/close/volume)} → 긴 형식 패널.

    열: date, instrument, 특징들, target_ret, band, y. 종목별로 특징을 만든 뒤 세로로 쌓는다.
    y는 그 종목의 다음날 수익률을 그 종목의 과거 변동성 밴드로 3클래스화한 것이다.
    """
    frames = []
    for ticker, bars in bars_by_ticker.items():
        bars = bars.sort_index()
        f = instrument_features(bars["close"], bars.get("volume"))
        close = bars["close"].astype(float)
        f["target_ret"] = close.shift(-1) / close - 1
        f["band"] = band_mult * f["vol_20"]
        f["y"] = np.where(f["target_ret"] < -f["band"], 0., np.where(f["target_ret"] > f["band"], 2., 1.))
        f.loc[f["target_ret"].isna() | f["band"].isna(), "y"] = np.nan
        f["instrument"] = ticker
        f.index.name = "date"
        frames.append(f.reset_index())
    panel = pd.concat(frames, ignore_index=True)
    return panel.replace([np.inf, -np.inf], np.nan)


def date_folds(panel_dates, fold_specs):
    """(train_start, test_start, test_end) 목록을 받아 날짜 단위로 패널 행 위치를 나눈다.

    같은 날짜의 모든 종목이 같은 쪽에 들어간다. 종목 단위로 섞지 않는다.
    """
    dates = pd.DatetimeIndex(panel_dates)
    out = []
    for i, (train_start, test_start, test_end) in enumerate(fold_specs):
        tr = np.flatnonzero((dates >= pd.Timestamp(train_start)) & (dates < pd.Timestamp(test_start)))
        te = np.flatnonzero((dates >= pd.Timestamp(test_start)) & (dates < pd.Timestamp(test_end)))
        out.append({"fold": i, "train_idx": tr, "test_idx": te})
    return out


def check_no_interpolation(panel, raw_bars_by_ticker):
    """패널의 각 (instrument, date) 행이 원본 봉에 실제로 존재하는지 확인한다.

    상장 전·거래정지 날짜를 만들어 채우지 않았다는 증거다. 위반 건수를 돌려준다.
    """
    bad = 0
    for ticker, group in panel.groupby("instrument"):
        have = pd.DatetimeIndex(raw_bars_by_ticker[ticker].index)
        bad += int((~pd.DatetimeIndex(group["date"]).isin(have)).sum())
    return bad

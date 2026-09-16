# -*- coding: utf-8 -*-
"""P10b — 패널에 해외 자산(종목 공통) 특징을 붙인다.

P10 패널(experiments/model_improvement/panel_data.py)의 행 (instrument, d)는 d일 종가까지의
종목 정보이고 라벨은 d→다음 봉 수익률이다. 대표 모델(노트북)의 행 e는 e일 07:00에 알 수 있는
정보(전일 미국 세션, 전일 한국 시장)로 e일 종가→종가 수익률을 맞힌다. 그러므로 패널 행 d에는
노트북 행 e = "d 다음 거래일"의 공통 특징을 붙인다. e ≤ d 인 행은 절대 쓰지 않는다.

공통 특징은 대표 모델 입력(시세만) 가운데 종목 고유 열(sam_·peer_·GDR)을 뺀 것이다 — 해외
자산·KOSPI·달력. 값은 종목과 무관하므로 한 번 만들어 날짜로 붙인다.

누수 방지 규칙 두 가지를 테스트로 고정한다.
  1. 특징 날짜 e는 항상 d보다 뒤의 첫 달력 날짜다(같은 날짜 금지).
  2. 라벨을 완성하는 봉(label_date)이 e보다 앞서는 행은 버린다 — 그 행의 특징은 결과가 난 뒤의
     정보를 담는다(대상 종목은 휴장, 다른 종목은 거래한 날).
"""
import numpy as np
import pandas as pd

# 대표 모델 입력 가운데 종목에 따라 값이 달라지는 열. 나머지가 종목 공통 특징이다.
TARGET_SPECIFIC_PREFIXES = ("sam_", "peer_", "target_gdr_", "gdr_")


def common_feature_columns(feature_cols, market_feature_idx):
    """대표 모델(시세만) 입력 중 종목 공통인 열 이름. 순서는 노트북 순서를 지킨다."""
    names = [feature_cols[i] for i in market_feature_idx]
    return [c for c in names if not c.startswith(TARGET_SPECIFIC_PREFIXES)]


def next_calendar_date(panel_dates, calendar):
    """각 패널 날짜 d에 대해 달력에서 d보다 **뒤인** 첫 날짜. 없으면 NaT."""
    cal = pd.DatetimeIndex(pd.to_datetime(calendar)).sort_values().unique()
    dates = pd.DatetimeIndex(pd.to_datetime(panel_dates))
    pos = cal.searchsorted(dates, side="right")
    out = np.full(len(dates), np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
    ok = pos < len(cal)
    out[ok] = cal.to_numpy(dtype="datetime64[ns]")[pos[ok]]
    return pd.DatetimeIndex(out)


def label_dates(panel):
    """종목별로 다음 봉의 날짜 — 그 봉의 종가가 라벨을 완성한다. 마지막 봉은 NaT."""
    return panel.groupby("instrument")["date"].shift(-1)


def attach_common_features(panel, common):
    """패널에 공통 특징을 붙인다. common은 노트북 예측일(그날 아침에 아는 정보)로 색인된 프레임.

    행 (instrument, d)는 common.loc[e], e = d 뒤 첫 달력 날짜를 받는다. 열 pred_date에 e를 남긴다.
    """
    pred = next_calendar_date(panel["date"], common.index)
    joined = common.reindex(pred)
    joined.index = panel.index
    out = pd.concat([panel, joined], axis=1)
    out["pred_date"] = pred
    return out


def drop_rows_with_features_after_label(panel):
    """label_date < pred_date 인 행을 버린다. (남은 프레임, 버린 행 수)"""
    label = pd.to_datetime(panel["label_date"])
    pred = pd.to_datetime(panel["pred_date"])
    leaky = label.notna() & pred.notna() & (label < pred)
    return panel[~leaky].reset_index(drop=True), int(leaky.sum())


def instrument_dummies(panel, instruments):
    """종목 식별 one-hot. 로지스틱에도 쓸 수 있게 정수 id 대신 열을 만든다."""
    out = pd.DataFrame(index=panel.index)
    for ticker in instruments:
        out[f"inst_{ticker.replace('.', '_')}"] = (panel["instrument"] == ticker).astype(float)
    return out


def fold_rows(pred_date, label_date, is_target, test_start, test_end, window_years=5):
    """한 외부 폴드의 학습·시험 행 위치.

    학습: 라벨이 test_start 전에 완성된 모든 종목 행, 예측일 기준 최근 window_years년.
    시험: 대상 종목 행 가운데 예측일이 [test_start, test_end]인 것.
    """
    pred = pd.DatetimeIndex(pd.to_datetime(pred_date))
    label = pd.DatetimeIndex(pd.to_datetime(label_date))
    t0, t1 = pd.Timestamp(test_start), pd.Timestamp(test_end)
    train = np.flatnonzero((label < t0) & (pred >= t0 - pd.DateOffset(years=window_years)))
    test = np.flatnonzero(np.asarray(is_target, dtype=bool) & (pred >= t0) & (pred <= t1))
    return train, test


def date_block_splits(row_dates, n_splits=3, block_dates=126):
    """날짜 블록 기준 시계열 분할. (학습 행, 검증 행) 목록 — 같은 날짜의 행은 같은 쪽에 간다.

    마지막 n_splits 블록(각 block_dates 거래일)을 차례로 검증으로 쓰고, 학습은 그 블록 전 전체다.
    """
    dates = pd.DatetimeIndex(pd.to_datetime(row_dates))
    uniq = dates.unique().sort_values()
    block = min(block_dates, max(len(uniq) // (n_splits + 2), 1))
    splits = []
    for k in range(n_splits, 0, -1):
        start, stop = len(uniq) - k * block, len(uniq) - (k - 1) * block
        if start <= 0:
            continue
        lo, hi = uniq[start], uniq[stop - 1]
        va = np.flatnonzero((dates >= lo) & (dates <= hi))
        tr = np.flatnonzero(dates < lo)
        if len(tr) and len(va):
            splits.append((tr, va))
    return splits

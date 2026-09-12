# -*- coding: utf-8 -*-
"""R09 3단계 — 선행지수 '전망'을 다음 분기 이익 모형에 얹으면 나아지는가.

계획의 진짜 판정이다. 지수 자체의 오차가 줄어도 이익 추정이 그대로면 채택하지 않는다.

시점 정합: 각 분기의 기준일(분기 시작 + k개월 − 1일)에 **그때 이미 공표된 달까지만** 보고
AR(2) 로 3개월 앞을 낸다. 공표는 참조월 다음 달 20일이라는 기존 규칙(oecd.CLI_RELEASE_DAY)을 쓴다.
"""
import os
import sys
from pathlib import Path

os.environ["PREDICT_STOCK_PUBLISH"] = "false"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import build_earnings_forecast as ef  # noqa: E402
import run_cli_forecast as cf  # noqa: E402
from data_sources import oecd  # noqa: E402

HORIZON = 3
LAGS = 2
B = 2000
SEED = 20260912


def available_through(asof, release_day=oecd.CLI_RELEASE_DAY):
    """그 시점에 이미 공표된 마지막 참조월."""
    asof = pd.Timestamp(asof)
    month = asof.replace(day=1) - pd.DateOffset(months=1)
    while (month + pd.DateOffset(months=1) + pd.Timedelta(days=release_day - 1)) > asof:
        month -= pd.DateOffset(months=1)
    return month


def cli_forecast_feature(cli, asof_dates, horizon=HORIZON, lags=LAGS):
    """기준일마다 '그때 보이던 값으로 낸 3개월 앞 전망 − 그때 수준'."""
    out = []
    for asof in asof_dates:
        last_month = available_through(asof)
        history = cli.loc[:last_month]
        if len(history) < 60:
            out.append(np.nan)
            continue
        values = history.to_numpy(dtype=float)
        point = cf.forecast_ar(values, horizon, lags)
        out.append(np.nan if point is None else point - float(values[-1]))
    return np.array(out, dtype=float)


def paired(a, b, seed=SEED, b_draws=B):
    a, b = np.abs(np.asarray(a)), np.abs(np.asarray(b))
    keep = np.isfinite(a) & np.isfinite(b)
    a, b = a[keep], b[keep]
    if len(a) < 8:
        return {"diff": None, "n": int(len(a))}
    delta = a - b
    rng = np.random.default_rng(seed)
    draws = np.array([rng.choice(delta, len(delta), replace=True).mean() for _ in range(b_draws)])
    lo, hi = np.quantile(draws, [0.025, 0.975])
    return {"diff": float(delta.mean()), "lo": float(lo), "hi": float(hi), "n": int(len(delta))}


cli = pd.read_csv(ROOT / "macro_history/cli_kor.csv", parse_dates=["month"])
cli = cli.set_index("month")["value"].asfreq("MS").dropna()
exports = pd.read_csv(ROOT / "macro_history/semiconductor_exports.csv", parse_dates=["month"])
exports = exports.set_index("month")["value"].asfreq("MS").dropna()

import yfinance as yf  # noqa: E402
hist = yf.Ticker("KRW=X").history(start="2005-01-01", auto_adjust=False)
hist.index = pd.to_datetime(hist.index).tz_localize(None)
usdkrw = hist["Close"].astype(float).resample("MS").mean().dropna()
usdkrw = usdkrw.reindex(usdkrw.index.union(exports.index)).ffill().reindex(exports.index)

TRILLION = 1e12
for target, name in (("samsung", "삼성전자"), ("sk_hynix", "SK하이닉스")):
    profit = pd.read_csv(ROOT / f"macro_history/operating_profit_{target}.csv")
    profit = pd.Series(profit["value"].to_numpy(),
                       index=pd.PeriodIndex(profit["quarter"], freq="Q"))
    k = 2                                   # 분기 앞 두 달까지 보는 자리(현행 발행과 같다)
    frame = ef.build_frame(profit, exports, usdkrw, k, cli.reset_index(), None)
    asof = pd.DatetimeIndex([(q.start_time + pd.DateOffset(months=k)) - pd.Timedelta(days=1)
                             for q in frame.index])
    frame["cli_fc_3m"] = cli_forecast_feature(cli, asof)

    base = ef.FEATURES_NEXT + ef.CLI_FEATURES
    plus = base + ["cli_fc_3m"]
    oof_base = ef.walk_forward(frame, target="profit_next", features=base, gap=1,
                               rw="profit_lag1", sn="profit_lag3")
    oof_plus = ef.walk_forward(frame, target="profit_next", features=plus, gap=1,
                               rw="profit_lag1", sn="profit_lag3")
    common = oof_base.index.intersection(oof_plus.index)
    print(f"\n=== {name} · 다음 분기 영업이익 (공통 분기 {len(common)}개) ===")
    if len(common) < 8:
        print("  표본 부족 — 판정하지 않는다")
        continue
    a = oof_plus.loc[common, "model"] - oof_plus.loc[common, "actual"]
    b = oof_base.loc[common, "model"] - oof_base.loc[common, "actual"]
    result = paired(a.to_numpy(), b.to_numpy())
    print(f"  기존 MAE {np.abs(b).mean() / TRILLION:7.3f}조 · 전망 추가 "
          f"{np.abs(a).mean() / TRILLION:7.3f}조")
    verdict = ("우위" if result["hi"] < 0 else "열위" if result["lo"] > 0 else "동률")
    print(f"  차이 {result['diff'] / TRILLION:+7.3f}조 "
          f"[{result['lo'] / TRILLION:+.3f}, {result['hi'] / TRILLION:+.3f}] → {verdict}")
    for label, oof in (("기존", oof_base), ("전망 추가", oof_plus)):
        ev = ef.evaluate(oof.loc[common])
        print(f"    {label:8} 기준선 이김 {ev['beats_baselines']} · "
              f"모델 {ev['mae_model'] / TRILLION:.3f}조 · "
              f"직전분기 {ev['mae_random_walk'] / TRILLION:.3f}조 · "
              f"4분기전 {ev['mae_seasonal_naive'] / TRILLION:.3f}조")
    print(f"    cli_fc_3m 값 있는 분기 {int(frame['cli_fc_3m'].notna().sum())}개 · "
          f"최근값 {frame['cli_fc_3m'].dropna().iloc[-1]:+.3f}")

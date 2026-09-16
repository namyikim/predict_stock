# -*- coding: utf-8 -*-
"""원/달러 환율의 결정 요인 — VAR 분산분해.

"원/달러가 왜 움직이나"에 답하려는 것이다. 여섯 계열로 VAR 을 세우고, 예측오차 분산분해로
각 시차에서 원/달러 변동의 몇 %가 어느 요인에서 오는지를 본다.

방법론에서 정한 것과 그 이유.
- 로그차분: 환율·지수는 단위근이 있어 수준 VAR 은 가짜 회귀가 된다. 금리차·경상수지처럼 이미
  차이·비율인 계열은 차분만 한다.
- 일반화 분산분해(Pesaran–Shin): 촐레스키는 변수 순서를 바꾸면 결과가 달라지는데 그 순서를
  정당화할 근거가 약하다. 일반화는 순서에 의존하지 않는다. 대신 합이 100%가 되지 않아
  마지막에 정규화한다(그 사실을 보고서에 적는다).
- 시차는 BIC 로 고른다(AIC 는 표본이 짧을 때 과하게 긴 시차를 고른다).

이 결과는 '무엇이 환율을 움직였나'를 과거 자료로 분해한 것이지 예측이 아니다.
"""
import numpy as np
import pandas as pd

# 표의 가로 순서. 원/달러 자신을 마지막에 두어 '나머지'로 읽히게 한다.
FX_FACTORS = ("dxy", "jpy", "cny", "rate_gap", "current_account", "usdkrw")
FX_LABELS = {"dxy": "미 달러지수", "jpy": "엔/달러", "cny": "위안/달러",
             "rate_gap": "한·미 금리차", "current_account": "경상수지", "usdkrw": "원/달러"}
HORIZONS = (1, 3, 6, 12)
# 이미 차이·비율인 계열은 로그를 씌우지 않는다(음수가 될 수 있고 로그의 뜻도 없다).
LEVEL_DIFF_ONLY = {"rate_gap", "current_account"}


def prepare(frame, factors=FX_FACTORS):
    """월별 수준 → VAR 에 넣을 정상 계열. 로그차분(또는 차분) 뒤 결측 제거."""
    out = {}
    for name in factors:
        if name not in frame:
            continue
        series = pd.to_numeric(frame[name], errors="coerce")
        if name in LEVEL_DIFF_ONLY:
            out[name] = series.diff()
        else:
            positive = series.where(series > 0)
            out[name] = np.log(positive).diff()
    return pd.DataFrame(out).dropna()


def generalized_decomposition(results, horizons=HORIZONS, target="usdkrw"):
    """일반화 예측오차 분산분해(Pesaran–Shin 1998). {시차: {요인: 비율}}.

    촐레스키와 달리 변수 순서에 의존하지 않는다. 합이 1 이 아니므로 행별로 정규화한다.
    """
    names = list(results.names)
    index = names.index(target)
    sigma = np.asarray(results.sigma_u)
    ma = results.ma_rep(maxn=max(horizons))          # 충격반응 계수 Psi_0..Psi_h
    table = {}
    for horizon in horizons:
        numer = np.zeros(len(names))
        denom = 0.0
        for step in range(horizon):
            psi = ma[step]
            row = psi[index, :] @ sigma                # (1 x k)
            numer += row ** 2 / np.diag(sigma)
            denom += float(psi[index, :] @ sigma @ psi[index, :].T)
        share = numer / denom if denom > 0 else numer
        total = share.sum()
        table[horizon] = {name: float(share[i] / total) if total > 0 else float("nan")
                          for i, name in enumerate(names)}
    return table


def fit_and_decompose(frame, factors=FX_FACTORS, horizons=HORIZONS, max_lags=6, target="usdkrw"):
    """(분산분해 표, 진단). 자료가 모자라면 (None, 사유)."""
    from statsmodels.tsa.api import VAR
    data = prepare(frame, factors)
    present = [c for c in factors if c in data.columns]
    if target not in present:
        return None, {"error": f"{target} 계열이 없습니다."}
    # 변수당 최소 표본. 시차 p, 변수 k 면 계수가 k*p 개라 그보다 넉넉해야 한다.
    if len(data) < max(40, 8 * len(present)):
        return None, {"error": f"표본이 {len(data)}개월로 부족합니다(필요 {max(40, 8 * len(present))}개월).",
                      "n": int(len(data))}
    model = VAR(data[present])
    try:
        order = model.select_order(maxlags=min(max_lags, len(data) // (2 * len(present)) or 1))
        lag = int(getattr(order, "bic", 1) or 1)
    except Exception:
        lag = 1
    lag = max(1, min(lag, max_lags))
    results = model.fit(lag)
    table = generalized_decomposition(results, horizons, target)
    info = {"n": int(len(data)), "lag": lag, "factors": present,
            "first": str(data.index[0])[:7], "last": str(data.index[-1])[:7],
            "method": "일반화 분산분해(순서 비의존) · 로그차분 · BIC 시차"}
    return table, info

# -*- coding: utf-8 -*-
"""장기 전망 — 반도체 수출액·선행지수 순환변동치와 3·6·12개월 주가 수익률.

매일 보고서의 맨 아래 '장기 전망 (월간)' 절에 들어갈 조각(HTML)과 수치(JSON)를 만든다.
월 1회 실행하고, 실패하면 이전 달 조각이 그대로 남는다.

설계 원칙
- 상관계수가 높다는 사실은 예측력의 근거가 아니다. 두 시계열이 모두 우상향하면 수준끼리는
  얼마든지 높은 상관이 나온다. 여기서는 "지금 값이 앞으로 h개월 수익률을 맞히는가"만 본다.
- HP 필터 같은 양방향 필터는 미래 자료를 쓰므로 쓰지 않는다. 모든 추세·변화율은 그 시점까지의
  자료만으로 계산한다(이동평균, 전년 동월 대비, z-score).
- 월별 지표는 발표 지연(월+2)을 반영한다(macro_utils.macro_features와 같은 규칙).
- 타깃이 h개월 겹치므로 워크포워드에서 h개월을 비워 학습·시험이 겹치지 않게 한다(purge).
- 점 예측은 워크포워드에서 '0% 기준선'을 이길 때만 낸다. 못 이기면 국면별 과거 분포만 보여 준다.

    python tools/build_longterm_report.py --target samsung --out runs/longterm --dump
    python tools/build_longterm_report.py --target samsung --out runs/longterm --publish
"""
import argparse
import hashlib
import html
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import github_pages  # noqa: E402
from macro_utils import (  # noqa: E402
    cli_features, daily_average, load_cli, load_macro_data, load_nsi, load_term_spread,
    macro_features, monthly_mean_by_month_end, nsi_features,
)

KST = timezone(timedelta(hours=9))
TARGETS = {
    "samsung": {"ticker": "005930.KS", "name": "삼성전자"},
    "sk_hynix": {"ticker": "000660.KS", "name": "SK하이닉스"},
}
START = "2000-01-01"
FIRST_TEST = "2012-01-31"       # 그 앞은 학습 전용
HORIZONS = {"3개월": 3, "6개월": 6, "12개월": 12}
FEATURES = [
    ("macro_semiconductor_yoy", "반도체 수출 전년 동월 대비"),
    ("macro_semiconductor_yoy_change_3m", "수출 YoY의 3개월 변화(가속/감속)"),
    ("macro_leading_cycle", "선행지수 순환변동치(100 기준)"),
    ("macro_leading_change_3m", "선행지수 3개월 변화"),
    ("price_to_exports_z", "주가/수출액 비율 z-score(5년)"),
    ("mom_12m", "주가 12개월 모멘텀"),
    ("drawdown_36m", "36개월 고점 대비 낙폭"),
    ("cli_level", "G20 경기선행지수(100 기준)"),
    ("cli_change_3m", "G20 선행지수 3개월 변화"),
    ("cycle_score", "합성 사이클 점수(일평균 수출 YoY·선행지수 변화·뉴스심리 변화·금리차, 등가중)"),
]
CLI_FEATURES = ["cli_level", "cli_change_3m"]
# 합성 점수의 구성 요소. 각각 확장 창 z-score로 표준화한 뒤 가중치 없이 평균한다.
# 학습되는 계수가 하나뿐이라 과적합 위험이 낮고, 개별 지표의 잡음이 서로 상쇄된다.
CYCLE_COMPONENTS = ["exports_daily_yoy", "macro_leading_change_3m", "nsi_change_20d", "term_spread"]
BOOTSTRAP_B, SEED = 1000, 42
# 판정에 필요한 최소 독립 표본 수. 겹치는 h개월 타깃은 h개월마다 하나씩만 독립이다.
# 이 하한이 없을 때 SK하이닉스 12개월이 독립 표본 6개로 'MAE 72.3% vs 73.0%'를 우위로 선언했다.
# 그 정도 표본에서는 블록 부트스트랩 신뢰구간도 믿을 수 없다. 미달이면 점 예측을 내지 않고
# 판정 불가로 적는다 — 최종 평가 구간을 따로 떼기에는 표본이 애초에 부족하다.
MIN_INDEPENDENT = 20


# ---------------------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------------------
def monthly_prices(ticker, cache_dir, fetch=True):
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{ticker.replace('.', '_')}_monthly.csv"
    if fetch:
        import yfinance as yf
        h = yf.Ticker(ticker).history(start=START, auto_adjust=False)
        if h is None or h.empty:
            raise RuntimeError(f"{ticker} 시세를 받지 못했습니다.")
        h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
        # Yahoo의 옛 한국 종목 수정종가는 배당 조정 계산 탓에 0이나 음수가 섞인다(SK하이닉스 2000년대
        # 초반). 0 이하는 값이 아니라 오류이므로 버리고, 그 비율이 크면 원종가로 대체한다.
        adj = h["Adj Close"].astype(float) if "Adj Close" in h else None
        raw = h["Close"].astype(float)
        if adj is not None and (adj > 0).mean() >= 0.98:
            daily = adj[adj > 0]
        else:
            print("  ⚠️ 수정종가에 0 이하 값이 많아 원종가를 씁니다.", flush=True)
            daily = raw[raw > 0]
        daily = daily.dropna()
        # 진행 중인 달은 월말 종가가 아니므로 뺀다(이번 달 1일이어도 전월까지만 남는다).
        last_full = pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None).normalize().replace(day=1) - pd.Timedelta(days=1)
        daily = daily[daily.index <= last_full]
        monthly = daily.resample("ME").last().dropna()
        monthly = prepend_history(monthly, cache_dir.parent)
        monthly.to_csv(path, header=["adj_close"])
        return monthly
    if not path.exists():
        raise RuntimeError(f"캐시가 없습니다: {path}")
    return pd.read_csv(path, index_col=0, parse_dates=True)["adj_close"]


def prepend_history(monthly, storage):
    """Yahoo는 2000년부터다. 그 이전 월말 종가를 CSV(date,close)로 두면 앞에 이어 붙인다.
    겹치는 첫 달로 스케일을 맞춰(수정계수) 하나의 연속 계열로 만든다. 파일: macro_inputs/price_history_monthly.csv"""
    path = Path(storage) / "macro_inputs" / "price_history_monthly.csv"
    if not path.exists():
        return monthly
    old = pd.read_csv(path)
    old["date"] = pd.to_datetime(old["date"]).dt.normalize()
    old = old.set_index("date")["close"].astype(float).resample("ME").last().dropna()
    old = old[old.index < monthly.index[0]]
    if old.empty:
        return monthly
    # 스케일 맞춤: 옛 계열의 마지막 값과 새 계열의 첫 값 사이 수익률이 보존되도록 비율을 곱한다.
    bridge = old.index[-1]
    ratio = float(monthly.iloc[0]) / float(old.iloc[-1])
    return pd.concat([old * ratio, monthly])


def expanding_z(series, min_periods=60):
    """확장 창 z-score. 그 시점까지의 평균·표준편차만 쓴다(전체 표본 표준화는 미래를 본다)."""
    mean = series.expanding(min_periods).mean()
    std = series.expanding(min_periods).std().replace(0, np.nan)
    return (series - mean) / std


def build_frame(monthly, macro, cli=None, nsi=None, spread=None):
    """월말 인덱스의 특징·타깃 프레임. 모든 특징은 그 월말까지의 자료만 쓴다."""
    monthly = monthly[monthly > 0].dropna()
    idx = monthly.index
    f = pd.DataFrame(index=idx)
    f["price"] = monthly.to_numpy()
    logp = np.log(monthly)
    f["mom_12m"] = logp - logp.shift(12)
    f["drawdown_36m"] = monthly / monthly.rolling(36, min_periods=12).max() - 1
    # 월+2 지연을 반영한 월별 지표(macro_features는 발표 지연을 넣어 준다)
    mf = macro_features(macro, idx, prediction_hour=23)
    for col in mf.columns:
        f[col] = mf[col].to_numpy()
    # 주가/수출액: 수출액(달러)이 이익의 대리변수라고 보고, 그 대비 주가가 앞서 달렸는지를 본다.
    exports = np.exp(f["macro_semiconductor_log_usd"]) if "macro_semiconductor_log_usd" in f else None
    if exports is not None:
        ratio = logp - np.log(exports.rolling(12, min_periods=6).mean())
        f["price_to_exports_z"] = (ratio - ratio.rolling(60, min_periods=24).mean()) / ratio.rolling(60, min_periods=24).std()
    # 국면 판정은 3개월 평균 YoY와 그 3개월 변화로 한다(월별 YoY는 달마다 부호가 뒤집혀 국면이 깜빡인다).
    if "macro_semiconductor_yoy_3m" in f:
        f["exports_cycle"] = f["macro_semiconductor_yoy_3m"]
        f["exports_accel"] = f["macro_semiconductor_yoy_3m"].diff(3)
        f["phase"] = [phase_of(a, b) for a, b in zip(f["exports_cycle"], f["exports_accel"])]
    if cli is not None and len(cli):
        cf = cli_features(cli, idx)          # 참조월+1개월 20일 이후에만 보인다
        for col in CLI_FEATURES:
            f[col] = cf[col].to_numpy()

    # ---- 합성 사이클 점수의 구성 요소 -----------------------------------------
    # 일평균 수출 YoY: 월 합계를 조업일수로 나눠 달력 효과를 없앤 뒤 전년 동월 대비. 월+2 지연은
    # macro_features가 이미 반영한 원달러 수출 로그값을 쓰지 않고, 같은 지연을 직접 건다.
    if "semiconductor_exports" in macro:
        da = daily_average(macro["semiconductor_exports"]).set_index("month")["value"]
        yoy = (da / da.shift(12) - 1)
        yoy.index = pd.DatetimeIndex(yoy.index) + pd.DateOffset(months=2)   # 월+2 발표 지연
        f["exports_daily_yoy"] = yoy.reindex(idx, method="ffill", tolerance=pd.Timedelta(days=45)).to_numpy()
    if nsi is not None and len(nsi):
        nf = nsi_features(nsi, idx)            # 지수 날짜+14일 이후에만 보인다
        f["nsi_change_20d"] = nf["nsi_change_20d"].to_numpy()
    if spread is not None and len(spread):
        f["term_spread"] = monthly_mean_by_month_end(spread, idx).to_numpy()
    have = [c for c in CYCLE_COMPONENTS if c in f.columns]
    if len(have) >= 3:
        z = pd.concat([expanding_z(f[c]) for c in have], axis=1)
        f["cycle_score"] = z.mean(axis=1, skipna=False)
        f["cycle_components"] = len(have)
    for label, h in HORIZONS.items():
        f[f"fwd_{h}m"] = logp.shift(-h) - logp
    return f


# ---------------------------------------------------------------------------
# 검증
# ---------------------------------------------------------------------------
def block_bootstrap_ci(n, stat_fn, block, b=BOOTSTRAP_B, seed=SEED):
    rng = np.random.default_rng(seed)
    starts = np.arange(0, n, block)
    draws = []
    for _ in range(b):
        pick = rng.integers(0, len(starts), len(starts))
        idx = np.concatenate([np.arange(s, min(s + block, n)) for s in starts[pick]])
        try:
            draws.append(stat_fn(idx))
        except Exception:
            continue
    if not draws:
        return (np.nan, np.nan)
    return tuple(np.percentile(draws, [2.5, 97.5]))


def spearman(x, y):
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 24:
        return np.nan
    return float(pd.Series(x[ok]).rank().corr(pd.Series(y[ok]).rank()))


def walk_forward(f, cols, h):
    """확장 창, 매달 재적합, h개월 purge. Ridge(표준화) 점 예측의 OOF."""
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    y = f[f"fwd_{h}m"]
    X = f[cols]
    oof = pd.Series(np.nan, index=f.index)
    for t in f.index[f.index >= pd.Timestamp(FIRST_TEST)]:
        train_end = t - pd.DateOffset(months=h)          # 타깃이 t 이전에 실현된 행만
        tr = f.index[(f.index <= train_end)]
        Xt, yt = X.loc[tr], y.loc[tr]
        ok = Xt.notna().all(axis=1) & yt.notna()
        if ok.sum() < 60 or X.loc[t].isna().any():
            continue
        model = make_pipeline(StandardScaler(), Ridge(alpha=10.0)).fit(Xt[ok], yt[ok])
        oof.loc[t] = float(model.predict(X.loc[[t]])[0])
    return oof


def evaluate(f, cols, h):
    oof = walk_forward(f, cols, h)
    y = f[f"fwd_{h}m"]
    ok = oof.notna() & y.notna()
    yy, pp = y[ok].to_numpy(), oof[ok].to_numpy()
    n = len(yy)
    out = {"horizon_months": h, "n_oof": int(n), "n_independent": int(n // h),
           "first": ok[ok].index[0].date().isoformat() if n else None,
           "last": ok[ok].index[-1].date().isoformat() if n else None}
    if n < 3 * h:
        out.update(beats_zero=False, note="표본 부족")
        return out, oof
    # 축소: OOF 예측에 실제를 회귀한 기울기(0~1). 첫 절반에서 추정해 둘째 절반에 적용.
    half = n // 2
    denom = float(np.sum(pp[:half] ** 2))
    slope = float(np.clip(np.sum(pp[:half] * yy[:half]) / denom, 0., 1.)) if denom > 0 else 0.
    err_model = np.abs(yy[half:] - slope * pp[half:])
    err_zero = np.abs(yy[half:])
    diff = err_model - err_zero
    lo, hi = block_bootstrap_ci(len(diff), lambda i: float(diff[i].mean()), block=h)
    enough = (n // h) >= MIN_INDEPENDENT
    out.update(
        corr_spearman=spearman(pp, yy),
        sign_hit=float(np.mean(np.sign(pp) == np.sign(yy))),
        shrink_slope=slope,
        mae_model=float(err_model.mean()), mae_zero=float(err_zero.mean()),
        mae_diff=float(diff.mean()), mae_diff_lo=float(lo), mae_diff_hi=float(hi),
        beats_zero=bool(enough and np.isfinite(hi) and hi < 0),
        enough_samples=bool(enough),
        n_evaluation=int(len(diff)),
    )
    if not enough:
        out["note"] = (f"독립 표본 {n // h}개로 판정에 필요한 {MIN_INDEPENDENT}개에 못 미칩니다 "
                       "— 우위 여부를 말하지 않습니다")
    return out, oof


def feature_ic(f, cols, h):
    """특징별 스피어만 IC와 블록 부트스트랩 CI. |IC|가 CI로 0을 배제해야 '있다'고 본다."""
    y = f[f"fwd_{h}m"].to_numpy()
    rows = []
    for c, label in FEATURES:
        if c not in cols:
            continue
        x = f[c].to_numpy()
        ok = np.isfinite(x) & np.isfinite(y)
        ic = spearman(x, y)
        xs, ys = x[ok], y[ok]
        lo, hi = block_bootstrap_ci(len(xs), lambda i: spearman(xs[i], ys[i]), block=h)
        rows.append({"feature": c, "label": label, "n": int(ok.sum()), "ic": ic, "ic_lo": lo, "ic_hi": hi,
                     "significant": bool(np.isfinite(lo) and (lo > 0) == (hi > 0))})
    return rows


# ---------------------------------------------------------------------------
# 국면과 유사 시기
# ---------------------------------------------------------------------------
PHASES = ["회복(수출 YoY<0, 가속)", "확장(YoY>0, 가속)", "둔화(YoY>0, 감속)", "침체(YoY<0, 감속)"]


def phase_of(yoy, change):
    if not (np.isfinite(yoy) and np.isfinite(change)):
        return None
    if yoy < 0:
        return PHASES[0] if change > 0 else PHASES[3]
    return PHASES[1] if change > 0 else PHASES[2]


def phase_table(f, h):
    d = pd.DataFrame({"phase": f.get("phase", pd.Series(None, index=f.index)), "fwd": f[f"fwd_{h}m"]}).dropna()
    rows = []
    for p in PHASES:
        g = d.loc[d.phase == p, "fwd"]
        if len(g) < 6:
            rows.append({"phase": p, "n": int(len(g))})
            continue
        rows.append({"phase": p, "n": int(len(g)), "median": float(g.median()),
                     "q25": float(g.quantile(.25)), "q75": float(g.quantile(.75)),
                     "positive_share": float((g > 0).mean())})
    return rows


def merge_short_runs(phases, min_len=3):
    """한두 달 깜빡이는 국면 전환은 국면이 아니다. min_len 미만 구간은 앞 구간에 흡수시킨다."""
    values = list(phases)
    runs = []
    for value in values:
        if runs and runs[-1][0] == value:
            runs[-1][1] += 1
        else:
            runs.append([value, 1])
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for i, (value, length) in enumerate(runs):
            if length >= min_len or value is None:
                continue
            target = i - 1 if i > 0 else i + 1        # 앞 구간에 붙이되, 첫 구간이면 뒤로
            runs[target][1] += length
            runs.pop(i)
            # 흡수 뒤 같은 국면이 이웃하면 합친다
            merged = []
            for value2, length2 in runs:
                if merged and merged[-1][0] == value2:
                    merged[-1][1] += length2
                else:
                    merged.append([value2, length2])
            runs = merged
            changed = True
            break
    out = []
    for value, length in runs:
        out.extend([value] * length)
    return out


def phase_episodes(f, min_len=3):
    """국면 구간 목록. 기간은 행(달) 수로 센다. 마지막 구간은 진행 중이라 기간이 확정되지 않았다."""
    if "phase" not in f:
        return []
    series = f["phase"].dropna()
    if len(series) < min_len * 2:
        return []
    smoothed = merge_short_runs(series.tolist(), min_len)
    index = series.index
    episodes, start_pos = [], 0
    for pos in range(1, len(smoothed) + 1):
        if pos == len(smoothed) or smoothed[pos] != smoothed[start_pos]:
            episodes.append({"phase": smoothed[start_pos],
                             "start": index[start_pos].strftime("%Y-%m"),
                             "end": index[pos - 1].strftime("%Y-%m"),
                             "end_period": pd.Period(index[pos - 1], freq="M"),
                             "months": pos - start_pos,
                             "ongoing": pos == len(smoothed)})
            start_pos = pos
    return episodes


def phase_duration_outlook(f, min_len=3, min_sample=3):
    """지금 국면이 얼마나 더 갈까 — 과거 같은 국면의 조건부 잔여 기간.

    '이미 k개월 지속된 국면이 앞으로 몇 달 더 가는가'는 전체 기간 분포가 아니라 k개월을 넘긴
    구간들만 놓고 봐야 한다(생존분석의 조건부 잔여수명). 표본이 열 개 남짓이라 점 예측이 아니라
    중앙값과 사분위로만 말한다.
    """
    episodes = phase_episodes(f, min_len)
    if not episodes:
        return None
    current = episodes[-1]
    same = [e for e in episodes[:-1] if e["phase"] == current["phase"]]
    out = {
        "phase": current["phase"],
        "since": current["start"], "as_of": current["end"],
        "months_so_far": current["months"],
        "episodes": [{"start": e["start"], "end": e["end"], "months": e["months"]} for e in same],
        "n_past": len(same),
        "median_total": float(np.median([e["months"] for e in same])) if same else None,
    }
    survived = [e["months"] - current["months"] for e in same if e["months"] > current["months"]]
    out["n_conditional"] = len(survived)
    out["n_shorter"] = len(same) - len(survived)
    if len(survived) >= min_sample:
        out["remaining_median"] = float(np.median(survived))
        out["remaining_q25"] = float(np.percentile(survived, 25))
        out["remaining_q75"] = float(np.percentile(survived, 75))
        out["expected_end"] = str(current["end_period"] + int(round(out["remaining_median"])))
    else:
        out["reason"] = (f"지금까지 {current['months']}개월 이어졌는데, 과거 같은 국면 {len(same)}번 중 "
                         f"이보다 길었던 것이 {len(survived)}번뿐이라 잔여 기간을 말할 표본이 없습니다.")
    return out


def phase_duration_by_spread(f, min_len=3):
    """같은 국면에서 '같은 개월째'의 장단기 금리차가 역전(≤0)이었는지로 나눠 잔여 기간을 본다.

    모델이 아니라 서술 통계다. 표본이 몇 개 안 되므로 숫자보다 '역전이었던 구간이 더 빨리 끝났나'만
    읽어야 한다. 금리차는 매일 나오고 소급 수정이 없어 이런 조건으로 쓰기에 가장 깨끗하다.
    """
    if "term_spread" not in f or "phase" not in f:
        return None
    episodes = phase_episodes(f, min_len)
    if len(episodes) < 2:
        return None
    current = episodes[-1]
    k = current["months_so_far"] if "months_so_far" in current else current["months"]
    spread = f["term_spread"]
    now_spread = spread.iloc[-1] if pd.notna(spread.iloc[-1]) else None
    rows = []
    for ep in episodes[:-1]:
        if ep["phase"] != current["phase"] or ep["months"] <= k:
            continue
        start = pd.Timestamp(ep["start"] + "-01") + pd.offsets.MonthEnd(0)
        at_k = start + pd.DateOffset(months=k - 1) + pd.offsets.MonthEnd(0)
        if at_k not in spread.index or pd.isna(spread.loc[at_k]):
            continue
        rows.append({"start": ep["start"], "end": ep["end"], "months": ep["months"],
                     "remaining": ep["months"] - k, "spread_at_k": float(spread.loc[at_k]),
                     "inverted": bool(spread.loc[at_k] <= 0)})
    if not rows:
        return None
    inverted = [r["remaining"] for r in rows if r["inverted"]]
    normal = [r["remaining"] for r in rows if not r["inverted"]]
    return {"months_so_far": k, "now_spread": now_spread, "now_inverted": (now_spread is not None and now_spread <= 0),
            "rows": rows,
            "inverted_median": float(np.median(inverted)) if inverted else None, "n_inverted": len(inverted),
            "normal_median": float(np.median(normal)) if normal else None, "n_normal": len(normal)}


def render_duration_chart(outlook, current_months):
    """과거 같은 국면의 기간을 가로 막대로. 지금 국면은 진행 중임을 화살표로 표시한다."""
    episodes = outlook.get("episodes") or []
    if not episodes:
        return ""
    rows = episodes + [{"start": outlook["since"], "end": "진행 중", "months": current_months}]
    W, L, R, ROW, TOP = 680, 118, 46, 22, 22
    H = TOP + ROW * len(rows) + 16
    longest = max(r["months"] for r in rows)
    scale = (W - L - R) / max(longest, 1)
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;'
           f'font-family:-apple-system,\'Malgun Gothic\',sans-serif;font-size:11px">']
    for i, row in enumerate(rows):
        y = TOP + ROW * i
        ongoing = i == len(rows) - 1
        color = "#c8952a" if ongoing else "#4c78a8"
        width = max(row["months"] * scale, 2)
        out.append(f'<rect x="{L}" y="{y}" width="{width:.1f}" height="{ROW - 8}" fill="{color}" '
                   f'opacity="{0.95 if ongoing else 0.75}"/>')
        label = f'{row["start"]} ~ {row["end"]}'
        out.append(f'<text x="{L - 8}" y="{y + ROW - 13}" text-anchor="end" fill="#6b7178">{html.escape(label)}</text>')
        out.append(f'<text x="{L + width + 6:.1f}" y="{y + ROW - 13}" fill="{color}">'
                   f'{row["months"]}개월{"(진행 중)" if ongoing else ""}</text>')
    if outlook.get("remaining_median") is not None:
        x = L + (current_months + outlook["remaining_median"]) * scale
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{TOP - 6}" y2="{H - 14}" stroke="#a8322a" stroke-dasharray="4,3"/>')
        out.append(f'<text x="{x + 5:.1f}" y="{TOP - 9}" fill="#a8322a">중앙값 종료 지점</text>')
    out.append("</svg>")
    return "".join(out)


def similar_episodes(f, cols, h, k=3, min_gap_months=12):
    """표준화한 특징 공간에서 지금과 가장 가까운 과거 월말 k개와 그 뒤 h개월 수익률."""
    X = f[cols].dropna()
    y = f[f"fwd_{h}m"]
    if len(X) < 36 or f.index[-1] not in X.index:
        return []
    z = (X - X.mean()) / X.std().replace(0, np.nan)
    now = z.loc[f.index[-1]]
    hist = z[z.index <= f.index[-1] - pd.DateOffset(months=h)]     # 결과가 이미 실현된 시점만
    dist = np.sqrt(((hist - now) ** 2).sum(axis=1)).sort_values()
    picked = []
    for t in dist.index:
        if any(abs((t - p).days) < min_gap_months * 30 for p in picked):
            continue
        picked.append(t)
        if len(picked) == k:
            break
    return [{"date": t.date().isoformat(), "distance": float(dist[t]), "fwd": float(y.loc[t])} for t in picked]


# ---------------------------------------------------------------------------
# 렌더링
# ---------------------------------------------------------------------------
TD = 'style="padding:6px 10px;border-top:1px solid #eee"'
TDR = TD[:-1] + ';text-align:right;font-variant-numeric:tabular-nums"'
TH = 'style="padding:8px 10px;text-align:left;font-size:11px;color:#6b7178;letter-spacing:.5px;background:#fafafa"'
THR = TH.replace("text-align:left", "text-align:right")


def pct(x):
    return "—" if x is None or not np.isfinite(x) else f"{(math.exp(x) - 1) * 100:+.1f}%"


def table(head, body, min_width=560):
    return ('<div style="overflow-x:auto;-webkit-overflow-scrolling:touch">'
            f'<table style="width:100%;min-width:{min_width}px;border-collapse:collapse;font-size:13px;border:1px solid #e5e5e5">'
            f'<tr>{head}</tr>{body}</table></div>')


PHASE_COLOR = {PHASES[0]: "#dbe9f6", PHASES[1]: "#dff0e3", PHASES[2]: "#fbeed6", PHASES[3]: "#f6dcd9"}


def text_width(text, size=11):
    """SVG 텍스트의 대략적인 픽셀 폭. 한글·한자는 라틴 문자의 두 배 가까이 넓다.

    글자 수에 고정 폭을 곱하면 한글 범례가 서로 겹친다(2026-09-08에 실제로 겹쳐 보였다).
    """
    width = 0.0
    for ch in text:
        code = ord(ch)
        wide = (0x1100 <= code <= 0x115F or 0x2E80 <= code <= 0xA4CF
                or 0xAC00 <= code <= 0xD7A3 or 0xF900 <= code <= 0xFAFF
                or 0xFF00 <= code <= 0xFF60)
        width += size * (1.0 if wide else 0.55)
    return width


def log_ticks(lo, hi):
    """1·2·5 × 10^k 중 [lo, hi]에 드는 눈금. 종목마다 가격대가 달라(수백 원~수백만 원) 고정 목록은 못 쓴다."""
    if not (np.isfinite(lo) and np.isfinite(hi)) or lo <= 0 or hi <= lo:
        return []
    out = []
    k = math.floor(math.log10(lo))
    while 10 ** k <= hi:
        for m in (1, 2, 5):
            v = m * 10 ** k
            if lo <= v <= hi:
                out.append(int(v) if v >= 1 else v)
        k += 1
    return out


def lead_lag(f, a="mom_12m", b="macro_semiconductor_yoy", span=15):
    """a가 b를 몇 개월 앞서는가. a(t)와 b(t+k)의 상관이 가장 큰 k를 찾는다(k>0이면 a가 선행)."""
    if a not in f or b not in f:
        return None
    best = None
    for k in range(-span, span + 1):
        x, y = f[a], f[b].shift(-k)
        ok = x.notna() & y.notna()
        if ok.sum() < 60:
            continue
        r = float(x[ok].corr(y[ok]))
        # 같이 움직이는 관계(양의 상관)만 본다. 역위상 정렬에서 |r|이 커지는 것은 선행이 아니다.
        if np.isfinite(r) and (best is None or r > best[1]):
            best = (k, r)
    return None if best is None else {"lead_months": best[0], "corr": best[1]}


def render_chart(f, name):
    """추세를 제거하고 사이클끼리 겹쳐 그린다.

    주가는 장기 우상향이라 '수준'으로는 사이클이 보이지 않는다. 그래서 위 칸에는 주가의
    12개월 수익률(= 그 시점까지의 자료만 쓰는 추세 제거)을 지표들과 함께 표준화해 겹치고,
    아래 얇은 칸에 실제 주가 수준(로그)을 참고로 남긴다. 표준화는 보기 위한 것이고 모형은 원값을 쓴다.
    """
    d = f.dropna(subset=["price"]).copy()
    d = d[d["price"] > 0]
    if len(d) < 24:
        return ""
    W, L, R, TOP, GAP, BOT = 900, 62, 58, 40, 26, 34
    PH, PS = 280, 86                      # 사이클 칸, 주가 수준 칸
    H = TOP + PH + GAP + PS + BOT
    x0, x1 = d.index[0].value, d.index[-1].value

    def X(t):
        return L + (t.value - x0) / (x1 - x0) * (W - L - R)

    ZMAX = 2.8

    def YZ(z):
        return TOP + PH / 2 - max(-ZMAX, min(ZMAX, z)) / ZMAX * (PH / 2 - 6)

    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;'
           f'font-family:-apple-system,\'Malgun Gothic\',sans-serif;font-size:11px">']

    # 국면 배경 — 3개월 이상 이어질 때만(한두 달 전환은 깜빡이기만 한다)
    if "phase" in d:
        runs, prev, start_t, count = [], None, None, 0
        for t, phv in list(d["phase"].items()) + [(d.index[-1], None)]:
            if phv != prev:
                if prev is not None and count >= 3:
                    runs.append((start_t, t, prev))
                prev, start_t, count = phv, t, 1
            else:
                count += 1
        for a, b, phv in runs:
            out.append(f'<rect x="{X(a):.1f}" y="{TOP}" width="{max(X(b) - X(a), 1):.1f}" '
                       f'height="{PH}" fill="{PHASE_COLOR.get(phv, "#fff")}"/>')

    for z in (-2, -1, 0, 1, 2):
        if z == 0:
            out.append(f'<line x1="{L}" x2="{W - R}" y1="{YZ(z):.1f}" y2="{YZ(z):.1f}" stroke="#999" stroke-dasharray="3,3"/>')
        out.append(f'<text x="{W - R + 6}" y="{YZ(z) + 4:.1f}" fill="#8a9199">{z:+d}σ</text>')

    missing = []
    for key, label, color, width in (("mom_12m", f"{name} 12개월 수익률", "#1a5490", 2.0),
                                     ("macro_semiconductor_yoy", "반도체 수출 전년 동월 대비", "#b5453c", 1.5),
                                     ("macro_leading_cycle", "선행지수 순환변동치", "#2e7d32", 1.5)):
        vals = d[key].replace([np.inf, -np.inf], np.nan).dropna() if key in d else pd.Series(dtype=float)
        if len(vals) < 12 or not np.isfinite(vals.std()) or vals.std() == 0:
            missing.append(label)
            continue
        z = (vals - vals.mean()) / vals.std()
        out.append(f'<polyline points="{" ".join(f"{X(t):.1f},{YZ(v):.1f}" for t, v in z.items())}" '
                   f'fill="none" stroke="{color}" stroke-width="{width}" opacity="0.92"/>')
    out.append(f'<rect x="{L}" y="{TOP}" width="{W - L - R}" height="{PH}" fill="none" stroke="#ddd"/>')

    # 아래 칸: 실제 주가 수준(로그) — 사이클 칸이 '수익률'이라 수준을 함께 봐야 해석이 된다.
    price = d["price"]
    y_top2 = TOP + PH + GAP
    plo, phi = math.log(float(price.min())), math.log(float(price.max()))

    def YP(v):
        return y_top2 + PS - (math.log(v) - plo) / (phi - plo) * PS

    for v in log_ticks(float(price.min()), float(price.max())):
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{YP(v):.1f}" y2="{YP(v):.1f}" stroke="#f0f0f0"/>'
                   f'<text x="{L - 6}" y="{YP(v) + 4:.1f}" text-anchor="end" fill="#8a9199">{v:,}</text>')
    out.append(f'<polyline points="{" ".join(f"{X(t):.1f},{YP(v):.1f}" for t, v in price.items())}" '
               'fill="none" stroke="#1a5490" stroke-width="1.4" opacity="0.75"/>')
    out.append(f'<rect x="{L}" y="{y_top2}" width="{W - L - R}" height="{PS}" fill="none" stroke="#ddd"/>')
    out.append(f'<text x="{L}" y="{y_top2 - 5}" fill="#6b7178">참고: 실제 주가 수준 (로그축, 원)</text>')

    for t in [t for t in d.index if t.month == 12 and t.year % 2 == 1]:
        out.append(f'<line x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{TOP}" y2="{TOP + PH}" stroke="#f2f2f2"/>'
                   f'<text x="{X(t):.1f}" y="{y_top2 + PS + 15:.1f}" text-anchor="middle" fill="#8a9199">{t.year + 1}</text>')

    out.append(f'<text x="{L}" y="14" fill="#1a1a1a" font-weight="600">'
               f'{html.escape(name)} 주가와 반도체 사이클 — 추세를 뺀 뒤 겹쳐 그림 (모두 표준화)</text>')
    x = L
    for text, color in ((f"{name} 12개월 수익률", "#1a5490"),
                        ("반도체 수출 YoY", "#b5453c"), ("선행지수 순환변동치", "#2e7d32")):
        out.append(f'<line x1="{x:.0f}" x2="{x + 16:.0f}" y1="30" y2="30" stroke="{color}" stroke-width="2"/>'
                   f'<text x="{x + 21:.0f}" y="34" fill="#6b7178">{html.escape(text)}</text>')
        x += 21 + text_width(text) + 18
    phases_legend = " ".join(f'<tspan fill="{c}">■</tspan> {html.escape(p.split("(")[0])}'
                             for p, c in PHASE_COLOR.items())
    out.append(f'<text x="{L}" y="{H - 6}" fill="#6b7178">배경 = 사이클 국면: {phases_legend}</text>')
    if missing:
        out.append(f'<text x="{W - R}" y="{H - 6}" text-anchor="end" fill="#a8322a">'
                   f'{html.escape(" · ".join(missing))}: 자료 없음</text>')
    out.append("</svg>")
    return "".join(out)


def render_fragment(result):
    e = html.escape
    r = result
    parts = ['<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
             '7. 장기 전망 (월간) <span style="font-weight:400;color:#8a9199;font-size:12px">'
             f'&nbsp;반도체 수출액·선행지수 순환변동치와 3·6·12개월 수익률 · 기준 {e(r["as_of"])}</span></h3>']
    parts.append('<div style="background:#fdf8ec;border-left:4px solid #c8952a;padding:12px 16px;border-radius:0 5px 5px 0;font-size:13px">'
                 '수출액 추세와 주가의 <b>수준</b>이 상관이 높은 것은 둘 다 우상향하기 때문이며 예측력의 근거가 아닙니다. '
                 'HP 필터처럼 미래 자료를 쓰는 양방향 추세도 쓰지 않았습니다. 아래는 그 시점까지의 자료로 계산한 지표가 '
                 '<b>앞으로</b> h개월 수익률을 맞히는지를 2012년 이후 워크포워드로 잰 결과입니다. 12개월 지평은 독립 표본이 '
                 f'{r["evaluation"]["12"]["n_independent"]}개뿐이라 결론은 잠정적입니다.</div>')

    # 그림: 실제 통계치와 주가
    if r.get("chart_svg"):
        first = r.get("chart_first", "")
        parts.append('<h4 style="font-size:14px;margin:18px 0 6px">실제 통계치와 주가 — 추세를 빼고 겹쳐 보기</h4>')
        parts.append(f'<div style="border:1px solid #e5e5e5;border-radius:6px;padding:8px">{r["chart_svg"]}</div>')
        parts.append(f'<div style="font-size:11px;color:#8a9199;margin-top:4px">시세는 Yahoo Finance 월말 수정종가({e(first)}부터 제공). '
                     '수출액·선행지수는 KOSIS 원자료이며 발표 지연(월+2)을 반영해 "그 시점에 알 수 있던 값"으로 그렸습니다. '
                     '주가는 장기 우상향이라 <b>수준</b>으로는 사이클이 보이지 않습니다. 그래서 위 칸은 주가를 <b>12개월 수익률</b>로 바꿔(그 시점까지의 자료만 쓰는 추세 제거) '
                     '세 계열을 모두 표준화해 겹쳤고, 아래 칸에 실제 주가 수준을 참고로 두었습니다. 표준화는 보기 위한 것이고 모형은 원값을 씁니다. '
                     '주가 봉우리가 수출 봉우리보다 <b>왼쪽</b>에 있으면 주가가 사이클을 앞선 것이고, 그때 수출은 설명 변수이지 예측 변수가 아닙니다.</div>')
        ll = r.get("lead_lag")
        if ll:
            direction = ("주가가 수출을 <b>{}개월 앞섰습니다</b>".format(ll["lead_months"]) if ll["lead_months"] > 0
                         else ("주가가 수출을 <b>{}개월 뒤따랐습니다</b>".format(-ll["lead_months"]) if ll["lead_months"] < 0
                               else "주가와 수출이 <b>같은 달에</b> 움직였습니다"))
            parts.append(f'<div style="font-size:13px;margin-top:6px">시차 상관이 가장 큰 지점: {direction} '
                         f'(상관 {ll["corr"]:+.2f}). 앞선다면 수출 지표로 주가를 <b>예측</b>하기는 어렵고, 사이클의 위치를 '
                         '가늠하는 용도로 읽어야 합니다.</div>')

    # 현재 값·국면
    cur = r["current"]
    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">지금 위치</h4>')
    body = ""
    for c, label in FEATURES:
        v = cur.get(c)
        if v is None or not np.isfinite(v):
            continue
        shown = (f"{v * 100:+.1f}%" if c in ("macro_semiconductor_yoy", "macro_semiconductor_yoy_change_3m", "mom_12m", "drawdown_36m")
                 else f"{v:+.2f}")
        body += f'<tr><td {TD}>{e(label)}</td><td {TDR}>{shown}</td><td {TDR}>{cur.get(c + "_pct", float("nan")) * 100:.0f}번째 백분위</td></tr>'
    parts.append(table(f'<th {TH}>지표</th><th {THR}>현재</th><th {THR}>2000년 이후 위치</th>', body, 420))
    parts.append(f'<div style="font-size:13px;margin-top:8px">반도체 사이클 국면: <b>{e(cur.get("phase") or "판정 불가")}</b> '
                 f'<span style="color:#8a9199;font-size:12px">(3개월 평균 수출 YoY의 부호 × 3개월 가속/감속, 월+2 발표 지연 반영)</span></div>')

    # 이 국면이 얼마나 더 갈까
    outlook = r.get("duration")
    if outlook:
        parts.append('<h4 style="font-size:14px;margin:18px 0 6px">'
                     f'이 국면이 얼마나 더 갈까 — {e(outlook["phase"].split("(")[0])} 국면 {outlook["months_so_far"]}개월째</h4>')
        if outlook.get("remaining_median") is not None:
            end_text = e(outlook["expected_end"])
            parts.append('<div style="font-size:13px">'
                         f'과거에 같은 국면이 {outlook["months_so_far"]}개월을 넘긴 경우는 {outlook["n_conditional"]}번이었고, '
                         f'그때 <b>{outlook["remaining_median"]:.0f}개월</b> 더 이어졌습니다'
                         f'(사분위 {outlook["remaining_q25"]:.0f}~{outlook["remaining_q75"]:.0f}개월). '
                         f'중앙값대로면 <b>{end_text}</b>쯤 국면이 바뀝니다. '
                         f'{outlook["as_of"]} 수출 자료 기준입니다.</div>')
        else:
            parts.append(f'<div style="font-size:13px;color:#6b7178">{e(outlook.get("reason", ""))}</div>')
        chart = render_duration_chart(outlook, outlook["months_so_far"])
        if chart:
            parts.append(f'<div style="border:1px solid #e5e5e5;border-radius:6px;padding:8px;margin-top:8px">{chart}</div>')
        parts.append('<div style="font-size:11px;color:#8a9199;margin-top:4px">'
                     f'과거 같은 국면 {outlook["n_past"]}번의 기간(중앙값 '
                     f'{outlook["median_total"]:.0f}개월)에서 <b>이미 지난 {outlook["months_so_far"]}개월을 뺀</b> 값입니다 — '
                     '전체 기간 분포를 그대로 쓰면 이미 지난 기간을 두 번 세게 됩니다. '
                     '표본이 열 개 남짓이라 점 예측이 아니라 범위로만 읽어야 하고, 국면 판정 자체가 '
                     '월+2 발표 지연을 반영한 것이라 실제 전환보다 늦게 인지됩니다. '
                     '한두 달짜리 깜빡임은 국면으로 세지 않았습니다(3개월 이상).</div>')

    # 국면별 과거 분포
    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">이 국면에서 과거에는 어땠나 — 12개월 뒤 수익률 분포</h4>')
    body = ""
    for row in r["phases"]["12"]:
        mark = ' style="background:#f4f8fc;font-weight:600"' if row["phase"] == cur.get("phase") else ""
        if "median" not in row:
            body += f'<tr{mark}><td {TD}>{e(row["phase"])}</td><td {TDR}>{row["n"]}</td><td {TDR} colspan="3">표본 부족</td></tr>'
            continue
        body += (f'<tr{mark}><td {TD}>{e(row["phase"])}</td><td {TDR}>{row["n"]}</td>'
                 f'<td {TDR}>{pct(row["median"])}</td><td {TDR}>{pct(row["q25"])} ~ {pct(row["q75"])}</td>'
                 f'<td {TDR}>{row["positive_share"] * 100:.0f}%</td></tr>')
    parts.append(table(f'<th {TH}>국면</th><th {THR}>월 수</th><th {THR}>중앙값</th><th {THR}>25~75%</th><th {THR}>상승 비율</th>', body))
    parts.append('<div style="font-size:11px;color:#8a9199;margin-top:4px">월 단위 표본이라 인접한 달은 서로 거의 같은 결과를 공유합니다. '
                 '월 수를 12로 나눈 것이 대략의 독립 표본 수입니다.</div>')

    # 유사 시기
    if r["similar"]:
        parts.append('<h4 style="font-size:14px;margin:18px 0 6px">지금과 가장 비슷했던 시기 (특징 공간 거리)</h4>')
        body = "".join(f'<tr><td {TD}>{e(s["date"])}</td><td {TDR}>{s["distance"]:.2f}</td><td {TDR}>{pct(s["fwd"])}</td></tr>'
                       for s in r["similar"])
        parts.append(table(f'<th {TH}>월말</th><th {THR}>거리</th><th {THR}>그 뒤 12개월 수익률</th>', body, 420))

    # 검증
    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">검증 — 이 지표들이 앞으로의 수익률을 맞혔는가 (2012년 이후 워크포워드)</h4>')
    body = ""
    for label, h in HORIZONS.items():
        ev = r["evaluation"][str(h)]
        if "mae_model" not in ev:
            body += f'<tr><td {TD}>{label}</td><td {TDR} colspan="6">{e(ev.get("note", "표본 부족"))}</td></tr>'
            continue
        verdict = ("<b style='color:#1e6b34'>0% 기준선을 이김</b>" if ev["beats_zero"]
                   else ("판정 불가(표본 부족)" if not ev.get("enough_samples", True) else "동률(CI가 0 포함)"))
        _corr = ev.get("corr_spearman")
        body += (f'<tr><td {TD}>{label}</td><td {TDR}>{ev["n_evaluation"]}({ev["n_independent"]})</td>'
                 f'<td {TDR}>{"—" if _corr is None or not np.isfinite(_corr) else format(_corr, ".2f")}</td><td {TDR}>{ev["sign_hit"] * 100:.0f}%</td>'
                 f'<td {TDR}>{ev["mae_model"] * 100:.1f}% / {ev["mae_zero"] * 100:.1f}%</td>'
                 f'<td {TDR}>[{ev["mae_diff_lo"] * 100:+.1f}, {ev["mae_diff_hi"] * 100:+.1f}]</td><td {TDR}>{verdict}</td></tr>')
    parts.append(table(f'<th {TH}>지평</th><th {THR}>평가 월(독립)</th><th {THR}>순위상관</th><th {THR}>부호 적중</th>'
                       f'<th {THR}>MAE 모델/0%</th><th {THR}>차이 95% CI</th><th {THR}>판정</th>', body, 640))
    body = ""
    def _f2(v):
        return "—" if v is None or not np.isfinite(v) else f"{v:+.2f}"
    for row in r["ic"]["12"]:
        sig = "유의" if row["significant"] else "동률"
        body += (f'<tr><td {TD}>{e(row["label"])}</td><td {TDR}>{_f2(row["ic"])}</td>'
                 f'<td {TDR}>[{_f2(row["ic_lo"])}, {_f2(row["ic_hi"])}]</td><td {TDR}>{sig}</td></tr>')
    parts.append('<div style="font-size:12px;color:#6b7178;margin:10px 0 4px">지표별 12개월 수익률과의 순위상관(IC). CI가 0을 포함하면 동률.</div>')
    parts.append(table(f'<th {TH}>지표</th><th {THR}>IC</th><th {THR}>95% CI</th><th {THR}>판정</th>', body, 420))

    # G20 CLI 효과
    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">G20 경기선행지수(OECD CLI)를 넣으면 나아지는가</h4>')
    if r.get("cli_active"):
        body = ""
        for label, h in HORIZONS.items():
            ab = r["cli_ablation"].get(str(h))
            if not ab or ab["mae_with"] is None or ab["mae_without"] is None:
                continue
            better = ab["mae_with"] < ab["mae_without"]
            body += (f'<tr><td {TD}>{label}</td>'
                     f'<td {TDR}>{ab["mae_with"] * 100:.1f}%</td><td {TDR}>{ab["mae_without"] * 100:.1f}%</td>'
                     f'<td {TDR}>{(ab["mae_with"] - ab["mae_without"]) * 100:+.2f}%p</td>'
                     f'<td {TDR}>{"조금 낫다" if better else "낫지 않다"}</td></tr>')
        parts.append(table(f'<th {TH}>지평</th><th {THR}>MAE (CLI 포함)</th><th {THR}>MAE (CLI 제외)</th>'
                           f'<th {THR}>차이</th><th {THR}>판정</th>', body, 520))
        cll = r.get("cli_lead_lag")
        lead_text = ""
        if cll:
            lead_text = (f' CLI 3개월 변화는 수출 YoY를 <b>{cll["lead_months"]}개월</b> '
                         f'{"앞섰습니다" if cll["lead_months"] > 0 else "뒤따랐습니다" if cll["lead_months"] < 0 else "같이 움직였습니다"}'
                         f'(상관 {cll["corr"]:+.2f}).')
        parts.append('<div style="font-size:11px;color:#8a9199;margin-top:4px">'
                     '"G20 CLI가 한국 수출을 2개월 앞선다"는 차트는 최종 수정치로 사후에 그린 것입니다. CLI는 추세제거·평활 '
                     '필터를 전체 시계열에 걸어 계산하므로 매달 소급 수정되고, 발표는 참조월로부터 5~6주 뒤입니다. 여기서는 '
                     '참조월+1개월 20일 이후에만 썼지만 개정 문제는 남아 있어 <b>이 표도 낙관적</b>입니다. 수출을 앞서는 것과 '
                     f'주가를 앞서는 것은 다른 문제이고, 주가는 수출을 앞섭니다(위 시차 상관).{lead_text}</div>')
    else:
        parts.append(f'<div style="font-size:13px;color:#6b7178">미포함 — {e(str(r.get("cli_info", {}).get("reason", "")))}</div>')

    # 합성 사이클 점수 효과
    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">합성 사이클 점수를 넣으면 나아지는가</h4>')
    if r.get("cycle_active"):
        names = {"exports_daily_yoy": "일평균 수출 YoY", "macro_leading_change_3m": "선행지수 3개월 변화",
                 "nsi_change_20d": "뉴스심리 20일 변화", "term_spread": "장단기 금리차(10년-3년)"}
        parts.append('<div style="font-size:12px;color:#6b7178;margin-bottom:6px">'
                     + " · ".join(names.get(c, c) for c in r["cycle_components"])
                     + '를 각각 확장 창 z-score로 표준화해 <b>가중치 없이 평균</b>한 지표 하나입니다. '
                     '지표를 하나씩 더하면 표본(12개월 지평 독립 표본 약 13개)에 비해 계수가 너무 많아지므로, '
                     '학습되는 계수가 하나뿐인 합성 점수로 묶었습니다. 같은 날짜에서 점수를 뺀 모델과 비교합니다.</div>')
        body = ""
        for label, h in HORIZONS.items():
            ab = r["cycle_ablation"].get(str(h))
            if not ab or ab["mae_with"] is None or ab["mae_without"] is None:
                continue
            better = ab["mae_with"] < ab["mae_without"]
            body += (f'<tr><td {TD}>{label}</td>'
                     f'<td {TDR}>{ab["mae_with"] * 100:.1f}%</td><td {TDR}>{ab["mae_without"] * 100:.1f}%</td>'
                     f'<td {TDR}>{(ab["mae_with"] - ab["mae_without"]) * 100:+.2f}%p</td>'
                     f'<td {TDR}>{"조금 낫다" if better else "낫지 않다"}</td></tr>')
        parts.append(table(f'<th {TH}>지평</th><th {THR}>MAE (점수 포함)</th><th {THR}>MAE (점수 제외)</th>'
                           f'<th {THR}>차이</th><th {THR}>판정</th>', body, 520))
        parts.append('<div style="font-size:11px;color:#8a9199;margin-top:4px">차이가 1%p 안팎이면 동률로 읽으세요. '
                     '일평균 수출은 월+2, 뉴스심리는 지수 날짜+14일 지연을 반영했고 금리차는 지연이 없습니다. '
                     '선행지수·뉴스심리는 소급 수정되므로 이 표도 낙관적입니다.</div>')
    else:
        missing = [k for k, v in r.get("extra_info", {}).items() if not v.get("enabled")]
        parts.append(f'<div style="font-size:13px;color:#6b7178">구성 요소가 부족해 만들지 않았습니다'
                     f'{" — 빠진 자료: " + ", ".join(missing) if missing else ""}.</div>')

    # 금리차 조건부 잔여 기간
    ds = r.get("duration_by_spread")
    if ds:
        parts.append('<h4 style="font-size:14px;margin:18px 0 6px">같은 국면, 같은 개월째에 금리차가 역전됐던 경우와 아니었던 경우</h4>')
        now_text = ("지금 금리차 " + (f'{ds["now_spread"]:+.2f}%p' if ds["now_spread"] is not None else "—")
                    + (" (역전)" if ds["now_inverted"] else " (정상)"))
        parts.append(f'<div style="font-size:13px">{now_text}. 과거 같은 국면 {ds["months_so_far"]}개월째에 '
                     f'금리차가 역전이었던 {ds["n_inverted"]}번은 그 뒤 중앙값 '
                     f'{"—" if ds["inverted_median"] is None else format(ds["inverted_median"], ".0f") + "개월"}, '
                     f'정상이었던 {ds["n_normal"]}번은 '
                     f'{"—" if ds["normal_median"] is None else format(ds["normal_median"], ".0f") + "개월"} 더 갔습니다.</div>')
        body = "".join(f'<tr><td {TD}>{e(row["start"])} ~ {e(row["end"])}</td><td {TDR}>{row["months"]}</td>'
                       f'<td {TDR}>{row["spread_at_k"]:+.2f}%p</td><td {TDR}>{"역전" if row["inverted"] else "정상"}</td>'
                       f'<td {TDR}>{row["remaining"]}개월</td></tr>' for row in ds["rows"])
        parts.append(table(f'<th {TH}>구간</th><th {THR}>총 기간</th><th {THR}>{ds["months_so_far"]}개월째 금리차</th>'
                           f'<th {THR}>상태</th><th {THR}>그 뒤 잔여</th>', body, 520))
        parts.append('<div style="font-size:11px;color:#8a9199;margin-top:4px">모델이 아니라 서술 통계입니다. '
                     '표본이 몇 개 안 되므로 숫자보다 "역전이었던 구간이 더 빨리 끝났는가"만 읽으세요.</div>')

    # 점 예측
    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">전망</h4>')
    lines = []
    for label, h in HORIZONS.items():
        ev, fc = r["evaluation"][str(h)], r["forecast"][str(h)]
        if ev.get("beats_zero") and fc.get("point") is not None:
            lines.append(f'<li><b>{label}</b>: {pct(fc["point"])} (축소계수 {ev["shrink_slope"]:.2f} 적용, 원시 {pct(fc["raw"])})</li>')
        else:
            reason = (ev.get("note") if not ev.get("enough_samples", True)
                      else "워크포워드에서 0% 기준선 대비 우위가 확인되지 않았습니다")
            lines.append(f'<li><b>{label}</b>: 점 예측하지 않음 — {e(str(reason))}. '
                         f'위 국면별 분포와 유사 시기를 참고하세요.</li>')
    parts.append('<ul style="font-size:13px;margin:4px 0 0;padding-left:20px">' + "".join(lines) + '</ul>')
    parts.append(f'<div style="font-size:11px;color:#8a9199;margin-top:8px">월 1회 갱신 · 시세 {e(r["price_last"])}까지 · '
                 f'수출액 최신월 {e(r["macro_last"].get("semiconductor_exports", "?"))} · 선행지수 최신월 {e(r["macro_last"].get("leading_cycle", "?"))} · '
                 f'생성 {e(r["generated_at"])} · 이 절은 연구·교육용이며 투자 자문이 아닙니다.</div>')
    return "".join(parts)


# ---------------------------------------------------------------------------
def analyse(target, out_dir, fetch=True):
    spec = TARGETS[target]
    cache = out_dir / "cache"
    monthly = monthly_prices(spec["ticker"], cache, fetch=fetch)
    # 외부 API가 막히면 저장소에 보관된 마지막 성공분을 쓴다(다른 실행이 갱신해 둔다).
    # 파일마다 따로 받는다 — 하나가 없거나 실패해도 나머지는 들어와야 한다.
    fallback_dir = out_dir / "macro_fallback"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    try:
        tok = github_pages.token()
    except Exception:
        tok = None
    loaded = []
    for name in ("leading_cycle.csv", "semiconductor_exports.csv", "cli_g20.csv",
                 "news_sentiment.csv", "term_spread.csv"):
        try:
            text = github_pages.fetch(f"macro_history/{name}", tok)
            if text:
                (fallback_dir / name).write_text(text, encoding="utf-8")
                loaded.append(name)
        except Exception:
            continue
    print("  보관본:", ", ".join(loaded) if loaded else "(없음)", flush=True)
    macro, macro_info = load_macro_data(out_dir, pd.Timestamp(START) - pd.DateOffset(years=2),
                                        pd.Timestamp.now(tz="Asia/Seoul").date(), use_cache=not fetch,
                                        fallback_dir=fallback_dir)
    if not macro_info.get("fresh", True):
        print("  ⚠️ KOSIS 조회 실패 → 저장소 보관본 사용:", macro_info.get("fetch_errors", {}), flush=True)
    macro = {k: v for k, v in macro.items() if k in ("semiconductor_exports", "leading_cycle")}
    cli, cli_info = None, {"enabled": False, "reason": "USE_CLI=False"}
    if os.environ.get("USE_CLI", "true").strip().lower() not in ("0", "false", "no"):
        try:
            cli, cli_info = load_cli(out_dir, "1998-01-01", datetime.now(KST).date(),
                                     use_cache=not fetch, fallback_dir=fallback_dir)
            cli_info["enabled"] = True
            print(f"  G20 CLI: {cli_info['source']} · {cli_info['first']}~{cli_info['last']}", flush=True)
        except Exception as exc:
            cli, cli_info = None, {"enabled": False, "reason": f"{type(exc).__name__}: {exc}"}
            print("  ⚠️ G20 CLI를 쓸 수 없어 빼고 진행합니다:", cli_info["reason"], flush=True)
    nsi, spread = None, None
    extra_info = {}
    for name, loader in (("nsi", load_nsi), ("term_spread", load_term_spread)):
        try:
            frame_, info_ = loader(out_dir, "1998-01-01", datetime.now(KST).date(),
                                   use_cache=not fetch, fallback_dir=fallback_dir)
            extra_info[name] = {**info_, "enabled": True}
            if name == "nsi":
                nsi = frame_
            else:
                spread = frame_
            print(f"  {name}: {info_['source']} · ~{info_['last']}", flush=True)
        except Exception as exc:
            extra_info[name] = {"enabled": False, "reason": f"{type(exc).__name__}: {exc}"}
            print(f"  ⚠️ {name}을(를) 쓸 수 없어 빼고 진행합니다: {exc}", flush=True)
    f = build_frame(monthly, macro, cli, nsi, spread)
    cols = [c for c, _ in FEATURES if c in f.columns and f[c].notna().mean() > 0.5]
    cli_active = all(c in cols for c in CLI_FEATURES)
    cols_no_cli = [c for c in cols if c not in CLI_FEATURES]
    cycle_active = "cycle_score" in cols
    cols_no_cycle = [c for c in cols if c != "cycle_score"]

    evaluation, forecast, phases, ic, cli_ablation, cycle_ablation = {}, {}, {}, {}, {}, {}
    for label, h in HORIZONS.items():
        ev, oof = evaluate(f, cols, h)
        evaluation[str(h)] = ev
        if cli_active:
            # 같은 방법·같은 날짜에서 CLI만 뺀 결과. 두 MAE의 차이가 CLI의 추가 효과다.
            ev_no, _ = evaluate(f, cols_no_cli, h)
            cli_ablation[str(h)] = {"mae_with": ev.get("mae_model"), "mae_without": ev_no.get("mae_model"),
                                    "corr_with": ev.get("corr_spearman"), "corr_without": ev_no.get("corr_spearman")}
        if cycle_active:
            ev_no, _ = evaluate(f, cols_no_cycle, h)
            cycle_ablation[str(h)] = {"mae_with": ev.get("mae_model"), "mae_without": ev_no.get("mae_model"),
                                      "corr_with": ev.get("corr_spearman"), "corr_without": ev_no.get("corr_spearman")}
        phases[str(h)] = phase_table(f, h)
        ic[str(h)] = feature_ic(f, cols, h)
        raw = float(oof.iloc[-1]) if pd.notna(oof.iloc[-1]) else None
        # 라이브: 마지막 월말 행으로 예측. walk_forward가 마지막 행도 OOF로 채운다(타깃은 미래라 NaN).
        forecast[str(h)] = {"raw": raw, "point": (ev.get("shrink_slope", 0.) * raw) if (raw is not None and "shrink_slope" in ev) else None}

    last = f.index[-1]
    current = {}
    for c, _ in FEATURES:
        if c in f.columns:
            v = f[c].iloc[-1]
            current[c] = float(v) if pd.notna(v) else None
            hist = f[c].dropna()
            current[c + "_pct"] = float((hist < v).mean()) if pd.notna(v) and len(hist) else float("nan")
    current["phase"] = f["phase"].iloc[-1] if "phase" in f else None
    result = {
        "target": target, "name": spec["name"], "as_of": last.date().isoformat(),
        "price_last": last.date().isoformat(), "macro_last": macro_info.get("latest_month", {}),
        "features": cols, "current": current, "phases": phases, "ic": ic,
        "evaluation": evaluation, "forecast": forecast,
        "similar": similar_episodes(f, cols, 12),
        "chart_svg": render_chart(f, spec["name"]),
        "lead_lag": lead_lag(f),
        "duration": phase_duration_outlook(f),
        "cli_info": cli_info, "cli_active": cli_active, "cli_ablation": cli_ablation,
        "cycle_active": cycle_active, "cycle_ablation": cycle_ablation,
        "cycle_components": [c for c in CYCLE_COMPONENTS if c in f.columns],
        "extra_info": extra_info,
        "duration_by_spread": phase_duration_by_spread(f),
        "cli_lead_lag": lead_lag(f, a="cli_change_3m", b="macro_semiconductor_yoy") if cli_active else None,
        "chart_first": f.index[0].date().isoformat(),
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "macro_snapshot_hash": macro_info.get("snapshot_hash", ""),
        "macro_sources": macro_info.get("sources", {}),
        "frame_hash": hashlib.sha256(f.to_csv().encode()).hexdigest()[:16],
    }
    return result, f


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="samsung", choices=list(TARGETS))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--dump", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    out_dir = args.out / args.target
    out_dir.mkdir(parents=True, exist_ok=True)
    result, frame = analyse(args.target, out_dir, fetch=not args.no_fetch)
    fragment = render_fragment(result)
    (out_dir / "longterm.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "longterm.html").write_text(fragment, encoding="utf-8")
    frame.to_csv(out_dir / "longterm_frame.csv")
    if args.dump:
        for label, h in HORIZONS.items():
            ev = result["evaluation"][str(h)]
            print(label, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in ev.items()})
        print("phase:", result["current"]["phase"], "| similar:", result["similar"])
    if args.publish:
        tok = github_pages.token()
        # 이번에 새로 받은 보조 자료를 '마지막 성공분'으로 남긴다(다음에 API가 막혀도 계속 돌 수 있게).
        for name, key in (("cli_g20.csv", "cli_info"), ("news_sentiment.csv", None), ("term_spread.csv", None)):
            cache = out_dir / "macro_cache" / name
            info = result.get(key) if key else result.get("extra_info", {}).get(
                "nsi" if name.startswith("news") else "term_spread", {})
            if cache.exists() and (info or {}).get("fresh"):
                try:
                    github_pages.publish(f"macro_history/{name}", cache.read_text(encoding="utf-8"), tok,
                                         f"macro: {name} ({(info or {}).get('last', '')})")
                except Exception as exc:
                    print(f"  사본 업로드 실패({name}):", exc, flush=True)
        for name in ("longterm.html", "longterm.json"):
            sha = github_pages.publish(f"docs/{args.target}/{name}", (out_dir / name).read_text(encoding="utf-8"),
                                       tok, f"longterm: {args.target} {result['as_of']}")
            print(f"발행 docs/{args.target}/{name} @ {sha}")


if __name__ == "__main__":
    main()

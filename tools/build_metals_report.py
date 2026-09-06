# -*- coding: utf-8 -*-
"""금·은 예측 보고서 — 다음 거래일 방향, 1주일·1개월 예상 구간, 예측 원장.

삼성전자·SK하이닉스 노트북과 같은 산출물을 낸다. 노트북의 검증 장치(워크포워드,
'가격 유지' 대비 신호 선택, 변동성 구간 보정, 불변 원장과 사후 채점)를 forecast_utils에서
그대로 가져다 쓴다. 노트북 자체를 금에 쓰지 않는 이유는 그 파이프라인이 KOSPI·반도체
피처, 한국 거래일, 야간 갭 분해, KOSIS 수출 지표까지 한국 반도체주에 맞춰져 있어서다.

시세는 COMEX 선물 연속물(GC=F, SI=F, 달러/트로이온스). 미국 세션 자산(달러지수, 10년물,
TIPS, 구리, WTI, VIX, S&P 500)이 같은 날 마감하므로 "d일 종가까지의 정보로 d+1일
종가 방향"을 예측한다. 삼성 노트북과 달리 야간 갭이 없어 '갭 나우캐스트' 효과가 없고,
그래서 방향 예측력은 낮게 나올 것을 각오해야 한다. 그 수치를 그대로 보고서에 적는다.

    python tools/build_metals_report.py --out runs/metals --dump
    python tools/build_metals_report.py --out runs/metals --publish
"""
import argparse
import hashlib
import html
import json
import math
import os
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import github_pages  # noqa: E402
from forecast_utils import (  # noqa: E402
    append_forecasts, atomic_csv, calibrate_price_forecast, daily_comparison, evaluate_forecasts,
    fit_direction_model, predict_direction_model, probability_loss, summarize_daily,
)

warnings.filterwarnings("ignore")
KST = timezone(timedelta(hours=9))
PAGES_DIR = "docs/metals"
LEDGER_ROOT = "forecast_history"
COUNTER_ENDPOINT = "https://predict-stock-counter.kimname1.workers.dev"

ASSETS = {
    "gold": dict(ticker="GC=F", name="금", other="SI=F"),
    "silver": dict(ticker="SI=F", name="은", other="GC=F"),
}
AUX = {"DX-Y.NYB": "dxy", "^TNX": "us10y", "TIP": "tip", "HG=F": "copper", "CL=F": "wti",
       "^VIX": "vix", "^GSPC": "sp500", "PL=F": "platinum", "USDKRW=X": "usdkrw"}
AUX_LABEL = {"dxy": "달러지수", "us10y": "미국 10년물 금리", "tip": "TIPS ETF(실질금리 대리)", "copper": "구리 선물",
             "wti": "WTI", "vix": "VIX", "sp500": "S&P 500", "platinum": "백금 선물", "usdkrw": "원/달러(표시용)"}
START = "2004-01-01"
FIRST_TEST = "2012-01-01"
TEST_MONTHS, TRAIN_YEARS = 6, 5
VOL_BAND_MULT, BAND_COVERAGE = 0.3, 0.8
HORIZONS = {"1거래일": 1, "1주일": 5, "1개월": 20}
BOOTSTRAP_B, SEED = 500, 42
OZ_PER_GRAM = 1 / 31.1034768
PROB_COLS = ["p_down", "p_flat", "p_up"]


# ---------------------------------------------------------------------------
# 시세
# ---------------------------------------------------------------------------
def load_bars(ticker, cache_dir, fetch=True):
    import yfinance as yf
    path = cache_dir / f"{ticker.replace('^', '_').replace('=', '_')}.csv"
    frame = None
    if fetch or not path.exists():
        try:
            h = yf.Ticker(ticker).history(start=START, auto_adjust=False)
            if not h.empty:
                h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
                h.columns = [str(c).strip().lower().replace(" ", "_") for c in h.columns]
                frame = h[["open", "high", "low", "close"]].copy()
                frame["adj_close"] = h["adj_close"] if "adj_close" in h else h["close"]
                frame = frame[~frame.index.duplicated()].dropna(subset=["close"]).sort_index()
                frame.to_csv(path)
        except Exception as exc:
            print(f"  ⚠️ {ticker}: {type(exc).__name__}", flush=True)
    if frame is None and path.exists():
        frame = pd.read_csv(path, index_col=0, parse_dates=True)
    return frame


def drop_unclosed(frame):
    """미국 선물 전자세션은 17:00 ET에 끝난다. 그 전이면 당일 봉은 미완성이다."""
    if frame is None or frame.empty:
        return frame
    now = pd.Timestamp.now(tz="America/New_York")
    if frame.index[-1].date() >= now.date() and (now.hour, now.minute) < (17, 15):
        return frame.iloc[:-1]
    return frame


def trading_days_ahead(date, n):
    try:
        import exchange_calendars as xc
        cal = xc.get_calendar("CMES")
        sessions = cal.sessions_in_range(pd.Timestamp(date), pd.Timestamp(date) + pd.Timedelta(days=60))
        after = [s for s in sessions if s > pd.Timestamp(date)]
        return pd.Timestamp(after[n - 1]).normalize()
    except Exception:
        d, k = pd.Timestamp(date), 0
        while k < n:
            d += pd.Timedelta(days=1)
            if d.weekday() < 5:
                k += 1
        return d


# ---------------------------------------------------------------------------
# 특징
# ---------------------------------------------------------------------------
def build_features(key, bars, aux):
    """행 d는 d일 종가까지의 정보만 담는다. 목표는 d+1 종가 수익률."""
    close = bars["close"]
    ret = close.pct_change()
    f = pd.DataFrame(index=close.index)
    f["ret_1"], f["ret_2"], f["ret_3"] = ret, ret.shift(1), ret.shift(2)
    for n in (5, 10, 20, 60):
        f[f"mom_{n}"] = close / close.shift(n) - 1
    f["vol_5"] = ret.rolling(5).std()
    f["vol_20"] = ret.rolling(20).std()
    f["vol_ratio"] = f["vol_5"] / f["vol_20"]
    f["ma50_gap"] = close / close.rolling(50).mean() - 1
    f["ma200_gap"] = close / close.rolling(200).mean() - 1
    f["high252_gap"] = close / close.rolling(252).max() - 1
    other = aux[ASSETS[key]["other"]]["close"].reindex(close.index).ffill()
    f["other_ret_1"] = other.pct_change()
    f["other_mom_5"] = other / other.shift(5) - 1
    ratio = np.log(close / other) if key == "gold" else np.log(other / close)
    f["ratio_z60"] = (ratio - ratio.rolling(60).mean()) / ratio.rolling(60).std()
    for ticker, name in AUX.items():
        if name == "usdkrw":
            continue
        s = aux[ticker]["close"].reindex(close.index).ffill()
        if name in ("us10y", "vix"):
            f[f"{name}_chg_1"] = s.diff()
            f[f"{name}_chg_5"] = s.diff(5)
            if name == "vix":
                f["vix_log"] = np.log(s)
        else:
            f[f"{name}_ret_1"] = s.pct_change()
            f[f"{name}_ret_5"] = s / s.shift(5) - 1
    f["band"] = VOL_BAND_MULT * f["vol_20"]
    f["target_ret"] = close.shift(-1) / close - 1
    f["y"] = np.where(f["target_ret"] < -f["band"], 0, np.where(f["target_ret"] > f["band"], 2, 1))
    f.loc[f["target_ret"].isna(), "y"] = np.nan
    return f.replace([np.inf, -np.inf], np.nan)


def feature_cols(f):
    return [c for c in f.columns if c not in ("band", "target_ret", "y")]


# ---------------------------------------------------------------------------
# 방향: 워크포워드
# ---------------------------------------------------------------------------
def month_blocks(date_index):
    key = pd.PeriodIndex(pd.DatetimeIndex(date_index), freq="M")
    return [np.where(key == m)[0] for m in key.unique()]


def block_bootstrap_ci(date_index, stat_fn, b=BOOTSTRAP_B, seed=SEED, alpha=0.05):
    blocks = month_blocks(date_index)
    if not blocks:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(b):
        pick = rng.integers(0, len(blocks), len(blocks))
        idx = np.concatenate([blocks[i] for i in pick])
        try:
            draws.append(stat_fn(idx))
        except Exception:
            continue
    return tuple(np.percentile(draws, [100 * alpha / 2, 100 * (1 - alpha / 2)])) if draws else (np.nan, np.nan)


def walk_forward(f, cols):
    from sklearn.metrics import balanced_accuracy_score, roc_auc_score
    data = f.dropna(subset=cols + ["y"])
    X, y, dates = data[cols].to_numpy(dtype=np.float32), data["y"].to_numpy(dtype=int), data.index
    oof = pd.DataFrame(index=dates, columns=PROB_COLS + ["prior_down", "prior_flat", "prior_up"], dtype=float)
    start = pd.Timestamp(FIRST_TEST)
    folds = 0
    while start <= dates[-1]:
        end = start + pd.DateOffset(months=TEST_MONTHS)
        train = np.flatnonzero((dates >= start - pd.DateOffset(years=TRAIN_YEARS)) & (dates < start))
        test = np.flatnonzero((dates >= start) & (dates < end))
        if len(train) >= 100 and len(test):
            fitted = fit_direction_model(X, y, train, "Logistic", seed=SEED)
            oof.iloc[test, :3] = predict_direction_model(fitted, X[test])
            prior = np.bincount(y[train], minlength=3) / len(train)
            oof.iloc[test, 3:] = prior
            folds += 1
        start = end
    scored = oof.dropna()
    yy = y[dates.get_indexer(scored.index)]
    p = scored[PROB_COLS].to_numpy()
    prior = scored[["prior_down", "prior_flat", "prior_up"]].to_numpy()
    non_flat = yy != 1
    metrics = dict(
        n=len(scored), folds=folds, first=scored.index[0].date(), last=scored.index[-1].date(),
        balanced_accuracy=float(balanced_accuracy_score(yy, p.argmax(axis=1))),
        prior_balanced_accuracy=float(balanced_accuracy_score(yy, prior.argmax(axis=1))),
        log_loss=probability_loss(yy, p), prior_log_loss=probability_loss(yy, prior),
        auc_up=float(roc_auc_score(yy == 2, p[:, 2])),
        auc_up_vs_down=float(roc_auc_score(yy[non_flat] == 2, (p[non_flat, 2] - p[non_flat, 0]))),
        class_share=np.bincount(yy, minlength=3) / len(yy),
    )
    diff = -np.log(np.clip(p[np.arange(len(yy)), yy], 1e-7, 1)) + np.log(np.clip(prior[np.arange(len(yy)), yy], 1e-7, 1))
    metrics["log_loss_diff_lo"], metrics["log_loss_diff_hi"] = block_bootstrap_ci(scored.index, lambda i: float(diff[i].mean()))
    return metrics


def live_direction(f, cols):
    data = f.dropna(subset=cols)
    live_row = data.iloc[[-1]]
    hist = data.dropna(subset=["y"])
    train = np.flatnonzero(hist.index >= hist.index[-1] - pd.DateOffset(years=TRAIN_YEARS))
    fitted = fit_direction_model(hist[cols].to_numpy(dtype=np.float32), hist["y"].to_numpy(dtype=int), train, "Logistic", seed=SEED)
    p = predict_direction_model(fitted, live_row[cols].to_numpy(dtype=np.float32))[0]
    return dict(p_down=float(p[0]), p_flat=float(p[1]), p_up=float(p[2]), band=float(live_row["band"].iloc[0]),
                as_of=live_row.index[0], selection=fitted["selection"], training_rows=int(len(train)))


# ---------------------------------------------------------------------------
# 가격: 1주일·1개월 구간
# ---------------------------------------------------------------------------
def price_forecasts(f, cols, close, as_of):
    from sklearn.base import clone
    from sklearn.linear_model import Ridge
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    template = Pipeline([("scaler", StandardScaler()), ("model", Ridge(alpha=1e4))])
    current = float(close.loc[as_of])
    rows, stats_all = [], {}
    live_X = f.loc[[as_of], cols].to_numpy(dtype=np.float32)
    for label, h in HORIZONS.items():
        reg = f[cols].copy()
        reg["future"] = close.shift(-h) / close - 1
        reg["sigma"] = f["vol_20"] * math.sqrt(h)
        reg = reg.dropna()
        X = reg[cols].to_numpy(dtype=np.float32)
        y = reg["future"].to_numpy(dtype=float)
        sigma = reg["sigma"].to_numpy(dtype=float)
        z = y / np.maximum(sigma, 1e-6)
        oof = np.full(len(z), np.nan)
        for tr, va in TimeSeriesSplit(n_splits=5, gap=h - 1).split(X):
            oof[va] = clone(template).fit(X[tr], z[tr]).predict(X[va]) * sigma[va]
        mask = ~np.isnan(oof)
        st = calibrate_price_forecast(y[mask], oof[mask], sigma[mask], reg.index[mask], h, block_bootstrap_ci, coverage=BAND_COVERAGE)
        fitted = clone(template).fit(X, z)
        sigma_live = float(f.loc[as_of, "vol_20"]) * math.sqrt(h)
        point = float(st["oof_slope"] * fitted.predict(live_X)[0] * sigma_live)
        half = sigma_live * st["band_q"]
        center = current * (1 + point)
        rows.append(dict(horizon=label, trading_days=h, as_of_date=as_of.date().isoformat(),
                         target_date=trading_days_ahead(as_of, h).date().isoformat(), current_close=current,
                         signal="있음" if st["beats_baseline"] else "없음",
                         predicted_return=point if st["beats_baseline"] else np.nan,
                         predicted_close=center if st["beats_baseline"] else np.nan,
                         center_close=center, low_close=current * (1 + point - half), high_close=current * (1 + point + half),
                         band_coverage=st["band_coverage_realized"], vol_model="simple",
                         model_mae=st["raw_model_mae"], zero_baseline_mae=st["zero_baseline_mae"],
                         mae_diff_lo=st["mae_diff_lo"], mae_diff_hi=st["mae_diff_hi"], oof_slope=st["oof_slope"]))
        stats_all[label] = st
    return rows, stats_all


# ---------------------------------------------------------------------------
# 원장
# ---------------------------------------------------------------------------
def ledger_update(storage, key, bars, run_id, common, live, price_rows):
    storage.mkdir(parents=True, exist_ok=True)
    label = ["하락", "보합", "상승"][int(np.argmax([live["p_down"], live["p_flat"], live["p_up"]]))]
    records = [{**common, "prediction": label, **{k: live[k] for k in PROB_COLS}, "model": "Logistic",
                "record_id": f"{run_id}:direction:Logistic", "kind": "direction", "horizon_days": 1,
                "target_date": common["prediction_date"]}]
    for row in price_rows:
        records.append({**common, **row, "model": "Ridge", "kind": "price", "horizon_days": row["trading_days"],
                        "record_id": f"{run_id}:price:{row['trading_days']}"})
    log_path = storage / "forecast_log.csv"
    all_log = append_forecasts(log_path, pd.DataFrame(records))
    evaluated = evaluate_forecasts(all_log, bars)
    atomic_csv(evaluated, log_path)
    daily = daily_comparison(evaluated)
    atomic_csv(daily, storage / "daily_forecast_comparison.csv")
    scored = summarize_daily(daily)
    atomic_csv(scored, storage / "forecast_accuracy_summary.csv")
    return evaluated, daily, scored


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
def pct(x, d=1):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.{d}f}%"


def num(x, d=2):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:,.{d}f}"


TD = 'style="padding:6px 10px;border-top:1px solid #eee"'
TDR = TD[:-1] + ';text-align:right;font-variant-numeric:tabular-nums"'
TH = 'style="padding:8px 10px;text-align:left;font-size:11px;color:#6b7178;letter-spacing:.5px;background:#fafafa"'
THR = TH.replace("text-align:left", "text-align:right")


def table(head, body, min_width=560):
    return ('<div style="overflow-x:auto;-webkit-overflow-scrolling:touch">'
            f'<table style="width:100%;min-width:{min_width}px;border-collapse:collapse;font-size:13px;border:1px solid #e5e5e5">'
            f'<tr>{head}</tr>{body}</table></div>')


def note(text, warn=False):
    bg = "#fff4e5;border:1px solid #f0c58a" if warn else "#f5f6f8"
    return f'<div style="margin:10px 0 0;padding:12px 16px;background:{bg};border-radius:6px;font-size:13px;color:#4a4f55;line-height:1.65">{text}</div>'


def prob_bar(p, label, color):
    return (f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:13px">'
            f'<span style="width:34px">{label}</span>'
            f'<span style="flex:1;background:#eef0f2;border-radius:3px;height:12px;overflow:hidden">'
            f'<span style="display:block;width:{p * 100:.1f}%;height:100%;background:{color}"></span></span>'
            f'<span style="width:48px;text-align:right;font-variant-numeric:tabular-nums">{p * 100:.1f}%</span></div>')


def render_asset(key, res, usdkrw):
    e = html.escape
    a = ASSETS[key]
    live, wf, rows, st = res["live"], res["wf"], res["price_rows"], res["price_stats"]
    current = rows[0]["current_close"]
    krw_g = current * usdkrw * OZ_PER_GRAM if usdkrw else float("nan")
    parts = [f'<h3 style="font-size:18px;margin:34px 0 10px;padding-bottom:6px;border-bottom:1px solid #ddd">{e(a["name"])} <span style="font-size:12px;color:#8a9199;font-weight:400">COMEX 선물 {a["ticker"]}</span></h3>']
    parts.append(f'<div style="font-size:13px;color:#6b7178">기준 봉 {live["as_of"].date()} · 종가 <b style="color:#1a1a1a">${current:,.2f}/온스</b>'
                 + (f' · 약 <b style="color:#1a1a1a">{krw_g:,.0f}원/g</b> (원/달러 {usdkrw:,.0f} 환산, 국내 KRX 금시장 가격과는 다를 수 있음)' if usdkrw else "") + '</div>')

    # 다음 거래일 방향
    skill = wf["log_loss_diff_hi"] < 0
    parts.append(f'<h4 style="font-size:14px;margin:18px 0 6px">다음 거래일({res["prediction_date"].date()}) 방향 · 보합 밴드 ±{live["band"] * 100:.2f}%</h4>')
    parts.append('<div style="max-width:420px">' + prob_bar(live["p_down"], "하락", "#a8322a") + prob_bar(live["p_flat"], "보합", "#8a9199") + prob_bar(live["p_up"], "상승", "#1a7f37") + "</div>")
    top = max(PROB_COLS, key=lambda k: live[k])
    label = {"p_down": "하락", "p_flat": "보합", "p_up": "상승"}[top]
    verdict = (f"모델의 최빈 판정은 <b>{label}</b>({live[top] * 100:.0f}%)입니다. "
               + (f"워크포워드 {wf['n']:,}일에서 이 모델의 log loss는 기저 확률보다 낮았고 95% 구간이 0을 배제합니다"
                  f"([{wf['log_loss_diff_lo']:+.4f}, {wf['log_loss_diff_hi']:+.4f}]) — 확률에 작지만 실제 정보가 있습니다."
                  if skill else
                  f"그러나 워크포워드 {wf['n']:,}일에서 이 모델의 log loss가 기저 확률(과거 빈도)보다 낫다는 증거가 없습니다"
                  f"(차이 95% 구간 [{wf['log_loss_diff_lo']:+.4f}, {wf['log_loss_diff_hi']:+.4f}]가 0을 포함). "
                  "<b>이 확률은 과거 빈도와 다를 바 없다고 보고 참고만 하세요.</b>"))
    parts.append(note(verdict, warn=not skill))

    # 1주·1개월
    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">1주일·1개월 뒤 예상 가격 (달러/온스)</h4>')
    body = ""
    for r in rows:
        sig = r["signal"] == "있음"
        body += (f'<tr><td {TD}>{e(r["horizon"])}<br><span style="font-size:11px;color:#8a9199">{r["target_date"]}</span></td>'
                 f'<td {TDR}>{"있음" if sig else "없음"}</td>'
                 f'<td {TDR}>{num(r["predicted_close"]) if sig else "—"}</td>'
                 f'<td {TDR}>{pct(r["predicted_return"], 2) if sig else "—"}</td>'
                 f'<td {TDR}>{num(r["center_close"])}</td>'
                 f'<td {TDR}><b>{num(r["low_close"])} ~ {num(r["high_close"])}</b></td>'
                 f'<td {TDR}>{r["band_coverage"] * 100:.0f}%</td></tr>')
    parts.append(table(f'<th {TH}>기간</th><th {THR}>신호</th><th {THR}>예상 종가</th><th {THR}>예상 변화</th><th {THR}>중심</th>'
                       f'<th {THR}>명목 {BAND_COVERAGE:.0%} 구간</th><th {THR}>실제 적중률</th>', body))
    no_sig = [r["horizon"] for r in rows if r["signal"] == "없음"]
    if no_sig:
        parts.append(note("신호 '없음'은 출력 오류가 아닙니다. 그 기간의 점 예측이 독립 구간에서 '현재가 유지'보다 낫다는 것을 보이지 못해 "
                          "예측을 내지 않은 것이고, 중심은 현재가입니다. 구간은 변동성 밴드이며 오른쪽 열이 독립 평가 구간에서의 실제 적중률입니다."))

    # 모델 품질
    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">이 모델의 과거 성적 (워크포워드, 5년 학습 → 6개월 시험)</h4>')
    body = (f'<tr><td {TD}>시험 구간</td><td {TDR}>{wf["first"]} ~ {wf["last"]} · {wf["n"]:,}일 · {wf["folds"]}구간</td></tr>'
            f'<tr><td {TD}>균형 정확도 (모델 / 기저)</td><td {TDR}>{wf["balanced_accuracy"] * 100:.1f}% / {wf["prior_balanced_accuracy"] * 100:.1f}%</td></tr>'
            f'<tr><td {TD}>log loss (모델 / 기저)</td><td {TDR}>{wf["log_loss"]:.4f} / {wf["prior_log_loss"]:.4f}</td></tr>'
            f'<tr><td {TD}>AUC — 상승 vs 나머지 / 상승 vs 하락</td><td {TDR}>{wf["auc_up"]:.3f} / {wf["auc_up_vs_down"]:.3f}</td></tr>'
            f'<tr><td {TD}>실제 클래스 비율 (하락/보합/상승)</td><td {TDR}>{" / ".join(f"{s * 100:.0f}%" for s in wf["class_share"])}</td></tr>')
    parts.append(table(f'<th {TH}>지표</th><th {THR}></th>', body, 420))

    # 원장 성적
    scored = res.get("scored")
    if scored is not None and len(scored):
        parts.append('<h4 style="font-size:14px;margin:18px 0 6px">지금까지의 실제 예측 성적 (원장, 날짜별 최초 사전 예측만)</h4>')
        body = ""
        for _, s in scored.iterrows():
            body += (f'<tr><td {TD}>{e(str(s["kind"]))} · {int(s["horizon_days"])}일 · {e(str(s["model"]))}</td><td {TDR}>{int(s["n"])}</td>'
                     f'<td {TDR}>{num(s["accuracy"] * 100, 0) + "%" if pd.notna(s["accuracy"]) else "—"}</td>'
                     f'<td {TDR}>{num(s["mean_log_loss"], 3)}</td>'
                     f'<td {TDR}>{num(s["price_mape"] * 100, 2) + "%" if pd.notna(s["price_mape"]) else "—"}</td>'
                     f'<td {TDR}>{num(s["interval_coverage"] * 100, 0) + "%" if pd.notna(s["interval_coverage"]) else "—"}</td></tr>')
        parts.append(table(f'<th {TH}>예측</th><th {THR}>채점 n</th><th {THR}>방향 정확도</th><th {THR}>log loss</th><th {THR}>가격 MAPE</th><th {THR}>구간 적중</th>', body))
    else:
        parts.append(note(f"원장에 아직 채점된 예측이 없습니다. 매일 실행이 쌓이면 여기에 실제 성적이 표시됩니다(원장: <code>{LEDGER_ROOT}/{key}/</code>)."))
    return "".join(parts)


def render(results, usdkrw, today, quality):
    e = html.escape
    pred = max(r["prediction_date"] for r in results.values())
    parts = ['<div style="max-width:980px;margin:0 auto;font-family:-apple-system,\'Malgun Gothic\',sans-serif;line-height:1.65;color:#1a1a1a">'
             '<style>code{font-family:ui-monospace,monospace;font-size:12px;word-break:break-all}a{color:#1a5490}</style>'
             '<div style="font-size:11px;letter-spacing:2px;color:#8a9199">GOLD &amp; SILVER · DIRECTION &amp; PRICE FORECAST</div>'
             '<h1 style="font-size:24px;margin:6px 0 4px">금·은 예측 보고서</h1>'
             f'<div style="font-size:13px;color:#6b7178">예측일 {pred.date()} · 시세 기준일 {today.date()} · 삼성전자·SK하이닉스 보고서와 같은 검증 장치를 씁니다</div>']
    parts.append(note("<b>먼저 읽을 것.</b> 금·은은 거의 24시간 거래되는 시장이라 삼성전자 보고서의 예측력을 만들던 '야간 갭'이 없습니다. "
                      "따라서 다음 거래일 방향 확률은 과거 빈도와 크게 다르지 않을 가능성이 높고, 아래 각 금속의 '과거 성적' 표가 그것을 "
                      "그대로 보여 줍니다. 쓸모가 있는 쪽은 <b>변동성으로 보정한 1주일·1개월 구간</b>입니다 — 그 구간의 실제 적중률을 함께 적었습니다. "
                      "연구·교육용이며 투자 자문이 아닙니다."))
    for key in ASSETS:
        parts.append(render_asset(key, results[key], usdkrw))
    parts.append('<h3 style="font-size:16px;margin:34px 0 10px;padding-bottom:6px;border-bottom:1px solid #ddd">이 보고서의 데이터와 방법</h3>')
    body = ""
    for q in quality:
        td_last = TDR if q["stale"] <= 5 else TDR[:-1] + ';color:#a8322a"'
        body += (f'<tr><td {TD}>{e(q["label"])}</td><td {TD}><code>{e(q["ticker"])}</code></td><td {TDR}>{q["rows"]:,}</td>'
                 f'<td {td_last}>{q["last"]}</td></tr>')
    parts.append(table(f'<th {TH}>자산</th><th {TH}>티커</th><th {THR}>행 수</th><th {THR}>마지막 봉</th>', body, 480))
    parts.append('<div style="font-size:13px;line-height:1.7;margin-top:10px">'
                 f'<b>시세</b> Yahoo Finance, {START}부터. 금·은은 COMEX 선물 연속물(달러/트로이온스). 미국 17:00 ET 이전이면 당일 봉을 미완성으로 보고 제외.<br>'
                 '<b>목표</b> d일 종가 대비 d+1일 종가. 보합 밴드는 20일 변동성의 0.3배(삼성 노트북과 같음).<br>'
                 f'<b>방향 모델</b> 로지스틱 회귀(정규화 강도·클래스 가중·온도를 학습 구간 안에서만 선택). 워크포워드는 {FIRST_TEST}부터 5년 학습 → 6개월 시험. '
                 '기저 확률은 각 학습 구간의 클래스 빈도. 차이의 95% 구간은 월 블록 부트스트랩.<br>'
                 '<b>가격 구간</b> 릿지 회귀 점 예측을 OOF 절반으로 기울기 보정, 다음 1/4에서 "현재가 유지" 대비 우위가 없으면 점 예측을 내지 않음, '
                 f'마지막 1/4에서 구간 적중률 측정. 구간 폭 = q × 20일 변동성 × √h, 목표 적중률 {BAND_COVERAGE:.0%}.<br>'
                 f'<b>원장</b> 예측은 불변으로 <code>{LEDGER_ROOT}/gold/</code>, <code>{LEDGER_ROOT}/silver/</code>에 쌓이고, 매 실행에서 확정된 봉으로 채점.<br>'
                 '<b>생성</b> <code>tools/build_metals_report.py</code>. 매일 07:00 KST 자동 실행.</div>')
    parts.append("</div>")
    return "".join(parts), pred


def page(inner, pred, today):
    counter = ('<div style="margin-top:10px;font-variant-numeric:tabular-nums">조회 <span id="view-count">—</span></div>'
               '<script>(function(){' f'var E="{COUNTER_ENDPOINT}",P="metals";'
               'var el=document.getElementById("view-count");if(!el)return;fetch(E+"/hit?page="+encodeURIComponent(P))'
               '.then(function(r){return r.ok?r.json():null;}).then(function(d){if(d&&typeof d.total==="number")el.textContent=d.total.toLocaleString("ko-KR");})'
               '.catch(function(){});})();</script>') if COUNTER_ENDPOINT else ""
    stale = ('<div id="stale-note" hidden style="max-width:980px;margin:0 auto 18px;padding:12px 16px;background:#fff4e5;border:1px solid #f0c58a;'
             'border-radius:6px;font-family:-apple-system,\'Malgun Gothic\',sans-serif;font-size:13px;color:#7a4b00"></div>'
             '<script>(function(){var d=new Date(Date.now()+9*3600e3);' f'var p="{pred.date().isoformat()}";'
             'var t=d.toISOString().slice(0,10),w=d.getUTCDay();if(t>p&&w>=1&&w<=5){var n=Math.round((Date.parse(t)-Date.parse(p))/864e5);'
             'var el=document.getElementById("stale-note");if(!el)return;el.hidden=false;'
             'el.textContent="이 보고서는 "+p+" 예측분입니다("+n+"일 지남). 오늘 자 보고서가 아직 발행되지 않았습니다. 휴장일이면 정상이고, 아니면 자동 실행(Actions)이 실패했을 수 있습니다.";}})();</script>')
    return ('<!doctype html>\n<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>금·은 예측 보고서 {pred.date()}</title>'
            '<style>html,body{overflow-x:hidden}body{margin:0;padding:24px 20px 48px;background:#fff;max-width:100%;-webkit-font-smoothing:antialiased}'
            'img{max-width:100%}@media(max-width:640px){body{padding:16px 12px 32px}}</style></head><body>'
            f'{stale}{inner}'
            '<div style="max-width:980px;margin:28px auto 0;padding-top:14px;border-top:1px solid #e5e5e5;font-family:-apple-system,\'Malgun Gothic\',sans-serif;font-size:12px;color:#8a9199">'
            f'생성 {datetime.now(KST).strftime("%Y-%m-%d %H:%M")} KST · <a href="https://github.com/{github_pages.GITHUB_REPO}" style="color:#1a5490">저장소</a> · '
            f'<a href="https://github.com/{github_pages.GITHUB_REPO}/tree/{github_pages.GITHUB_BRANCH}/{LEDGER_ROOT}" style="color:#1a5490">예측 원장</a> · '
            '<a href="../" style="color:#1a5490">보고서 목록</a> · 연구·교육용입니다. 투자 자문이 아닙니다.'
            f'{counter}</div></body></html>')


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--publish", action="store_true", help="GITHUB_TOKEN으로 원장과 보고서를 저장소에 발행")
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--dump", action="store_true")
    args = parser.parse_args()
    cache = args.out / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + hashlib.sha256(os.urandom(8)).hexdigest()[:12]
    tok = github_pages.token() if args.publish else None

    tickers = [a["ticker"] for a in ASSETS.values()] + list(AUX)
    print(f"시세 {len(tickers)}종목 수집", flush=True)
    data = {t: drop_unclosed(load_bars(t, cache, fetch=not args.no_fetch)) for t in tickers}
    missing = [t for t, f in data.items() if f is None or f.empty]
    if missing:
        raise SystemExit(f"데이터 없음: {missing}")
    today = max(f.index[-1] for f in data.values())
    usdkrw = float(data["USDKRW=X"]["close"].iloc[-1])
    quality = [dict(label=ASSETS[k]["name"] + " 선물", ticker=a["ticker"], rows=len(data[a["ticker"]]), last=data[a["ticker"]].index[-1].date(),
                    stale=(today - data[a["ticker"]].index[-1]).days) for k, a in ASSETS.items()]
    quality += [dict(label=AUX_LABEL[n], ticker=t, rows=len(data[t]), last=data[t].index[-1].date(), stale=(today - data[t].index[-1]).days) for t, n in AUX.items()]

    import sklearn
    versions_hash = hashlib.sha256(f"pandas={pd.__version__};sklearn={sklearn.__version__};numpy={np.__version__}".encode()).hexdigest()[:12]
    runtime = "github-actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local"
    results = {}
    for key, a in ASSETS.items():
        bars = data[a["ticker"]]
        f = build_features(key, bars, data)
        cols = feature_cols(f)
        print(f"■ {a['name']}: 행 {len(f.dropna(subset=cols)):,} · 특징 {len(cols)}", flush=True)
        wf = walk_forward(f, cols)
        live = live_direction(f, cols)
        as_of = live["as_of"]
        prediction_date = trading_days_ahead(as_of, 1)
        price_rows, price_stats = price_forecasts(f, cols, bars["close"], as_of)
        snapshot = hashlib.sha256(pd.util.hash_pandas_object(bars["close"]).values.tobytes()).hexdigest()[:20]
        config = dict(schema_version=3, model_version="metals-logistic-ridge-v1", target_mode="close_to_close", band_mode="vol_scaled",
                      vol_band_mult=VOL_BAND_MULT, training_years=TRAIN_YEARS, feature_cols=cols, seed=SEED)
        config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:20]
        common = dict(schema_version=3, run_id=run_id, created_at_utc=datetime.now(timezone.utc).isoformat(), runtime=runtime,
                      versions_hash=versions_hash, prediction_date=prediction_date.date().isoformat(), as_of_date=as_of.date().isoformat(),
                      data_snapshot_hash=snapshot, config_hash=config_hash, target_mode="close_to_close", band=live["band"],
                      imputed_features="{}", macro_snapshot_hash="", macro_history_mode="disabled")

        storage = args.out / key
        storage.mkdir(parents=True, exist_ok=True)
        ledger_path = f"{LEDGER_ROOT}/{key}/forecast_log.csv"
        if tok:
            remote = github_pages.fetch(ledger_path, tok)
            if remote:
                (storage / "forecast_log.csv").write_text(remote, encoding="utf-8-sig")
                print(f"  원장 불러옴: {ledger_path}")
        evaluated, daily, scored = ledger_update(storage, key, bars, run_id, common, live, price_rows)
        print(f"  원장 {len(evaluated)}건 · 채점 {int((evaluated.status == 'scored').sum())}건")
        results[key] = dict(live=live, wf=wf, price_rows=price_rows, price_stats=price_stats, prediction_date=prediction_date, scored=scored)
        if args.dump:
            print(f"  방향: 하락 {live['p_down']:.3f} 보합 {live['p_flat']:.3f} 상승 {live['p_up']:.3f} · 밴드 ±{live['band']:.4f}")
            print(f"  WF n={wf['n']} bal.acc {wf['balanced_accuracy']:.3f}/{wf['prior_balanced_accuracy']:.3f} logloss {wf['log_loss']:.4f}/{wf['prior_log_loss']:.4f} "
                  f"diff CI [{wf['log_loss_diff_lo']:+.4f},{wf['log_loss_diff_hi']:+.4f}] AUC up {wf['auc_up']:.3f} up/down {wf['auc_up_vs_down']:.3f}")
            for r in price_rows:
                print(f"  {r['horizon']:<5} {r['target_date']} 신호 {r['signal']} 중심 {r['center_close']:,.2f} 구간 {r['low_close']:,.2f}~{r['high_close']:,.2f} 적중 {r['band_coverage']:.0%} "
                      f"slope {r['oof_slope']:.3f} sel[{price_stats[r['horizon']]['selection_mae_diff_lo']:+.4f},{price_stats[r['horizon']]['selection_mae_diff_hi']:+.4f}]")
        if tok:
            for name in ["forecast_log.csv", "daily_forecast_comparison.csv", "forecast_accuracy_summary.csv"]:
                sha = github_pages.publish(f"{LEDGER_ROOT}/{key}/{name}", (storage / name).read_text(encoding="utf-8-sig"), tok, f"data: {key}/{name} ({run_id})")
                print(f"  GitHub 저장: {LEDGER_ROOT}/{key}/{name} @ {sha}")

    inner, pred = render(results, usdkrw, today, quality)
    doc = page(inner, pred, today)
    out = args.out / "index.html"
    out.write_text(doc, encoding="utf-8")
    print("보고서:", out, f"{len(doc):,} bytes")
    if tok:
        sha = github_pages.publish(f"{PAGES_DIR}/index.html", doc, tok, f"report: metals {pred.date()} ({run_id})")
        print(f"GitHub Pages 발행: {PAGES_DIR}/index.html @ {sha}")
        print("→ https://namyikim.github.io/predict_stock/metals/")


if __name__ == "__main__":
    main()

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
from macro_utils import load_macro_data, macro_features  # noqa: E402

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
]
BOOTSTRAP_B, SEED = 1000, 42


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


def build_frame(monthly, macro):
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
    out.update(
        corr_spearman=spearman(pp, yy),
        sign_hit=float(np.mean(np.sign(pp) == np.sign(yy))),
        shrink_slope=slope,
        mae_model=float(err_model.mean()), mae_zero=float(err_zero.mean()),
        mae_diff=float(diff.mean()), mae_diff_lo=float(lo), mae_diff_hi=float(hi),
        beats_zero=bool(np.isfinite(hi) and hi < 0),
        n_evaluation=int(len(diff)),
    )
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


def render_chart(f, name):
    """실제 통계치와 주가를 한 그림에. 외부 라이브러리 없이 SVG를 직접 그린다(보고서 HTML에 인라인).

    위: 주가(로그축) + 사이클 국면 배경. 가운데: 반도체 수출 전년 동월 대비(월+2 지연 반영, 즉 그 시점에
    알 수 있던 값). 아래: 선행지수 순환변동치(100 기준). 모두 같은 시간축이라 선후 관계를 눈으로 볼 수 있다.
    """
    d = f.dropna(subset=["price"]).copy()
    d = d[d["price"] > 0]
    if len(d) < 24:
        return ""
    W, L, R = 900, 60, 24
    panels = [("price", 230), ("macro_semiconductor_yoy", 130), ("macro_leading_cycle", 130)]
    top, gap, bottom = 16, 28, 30
    H = top + sum(h for _, h in panels) + gap * (len(panels) - 1) + bottom
    x0, x1 = d.index[0].value, d.index[-1].value
    def X(t):
        return L + (t.value - x0) / (x1 - x0) * (W - L - R)
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;font-family:-apple-system,\'Malgun Gothic\',sans-serif;font-size:11px">']
    y = top
    years = [t for t in d.index if t.month == 12 and t.year % 2 == 1]
    for key, ph in panels:
        y_top, y_bot = y, y + ph
        series = d[key] if key in d else pd.Series(np.nan, index=d.index)
        vals = series.replace([np.inf, -np.inf], np.nan).dropna()
        if not len(vals):
            # 지표를 못 받은 경우. 빈 칸에 사유만 적고 넘어간다(라벨에 nan이 찍히지 않게).
            label = {"macro_semiconductor_yoy": "반도체 수출액 전년 동월 대비",
                     "macro_leading_cycle": "선행지수 순환변동치 − 100"}.get(key, key)
            out.append(f'<text x="{L}" y="{y_top - 4}" fill="#1a1a1a" font-weight="600">{html.escape(label)}</text>')
            out.append(f'<rect x="{L}" y="{y_top}" width="{W - L - R}" height="{ph}" fill="none" stroke="#ddd"/>')
            out.append(f'<text x="{(L + W - R) / 2:.0f}" y="{(y_top + y_bot) / 2:.0f}" text-anchor="middle" fill="#8a9199">자료 없음</text>')
            y = y_bot + gap
            continue
        if key == "price":
            lo, hi = float(np.log(vals.min())), float(np.log(vals.max()))
            def Y(v, lo=lo, hi=hi, y_top=y_top, y_bot=y_bot):
                return y_bot - (math.log(v) - lo) / (hi - lo) * (y_bot - y_top)
            # 국면 배경
            if "phase" in d:
                prev, start = None, None
                for t, phv in list(d["phase"].items()) + [(d.index[-1], None)]:
                    if phv != prev:
                        if prev is not None and start is not None:
                            out.append(f'<rect x="{X(start):.1f}" y="{y_top}" width="{max(X(t) - X(start), 1):.1f}" height="{ph}" fill="{PHASE_COLOR.get(prev, "#fff")}"/>')
                        prev, start = phv, t
            ticks = log_ticks(float(vals.min()), float(vals.max()))
            for v in ticks:
                out.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#eee"/>'
                           f'<text x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end" fill="#8a9199">{v:,}</text>')
            title = f"{name} 월말 수정종가 (로그축) · 배경 = 반도체 사이클 국면"
        else:
            lo, hi = float(min(vals.min(), 0)), float(max(vals.max(), 0))
            pad = (hi - lo) * .08 or 1
            lo, hi = lo - pad, hi + pad
            def Y(v, lo=lo, hi=hi, y_top=y_top, y_bot=y_bot):
                return y_bot - (v - lo) / (hi - lo) * (y_bot - y_top)
            out.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="#999" stroke-dasharray="3,3"/>')
            for v in (lo + pad, hi - pad):
                lab = f"{v * 100:+.0f}%" if key == "macro_semiconductor_yoy" else f"{v:+.1f}"
                out.append(f'<text x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end" fill="#8a9199">{lab}</text>')
            title = ("반도체 수출액 전년 동월 대비 (그 시점에 알 수 있던 값, 월+2 지연)" if key == "macro_semiconductor_yoy"
                     else "선행지수 순환변동치 − 100 (월+2 지연)")
        if len(vals):
            pts = " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in vals.items())
            color = "#1a5490" if key == "price" else ("#b5453c" if key == "macro_semiconductor_yoy" else "#2e7d32")
            out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.6"/>')
        out.append(f'<text x="{L}" y="{y_top - 4}" fill="#1a1a1a" font-weight="600">{html.escape(title)}</text>')
        out.append(f'<rect x="{L}" y="{y_top}" width="{W - L - R}" height="{ph}" fill="none" stroke="#ddd"/>')
        y = y_bot + gap
    for t in years:
        out.append(f'<line x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{top}" y2="{H - bottom}" stroke="#f0f0f0"/>'
                   f'<text x="{X(t):.1f}" y="{H - bottom + 14}" text-anchor="middle" fill="#8a9199">{t.year + 1}</text>')
    legend = " ".join(f'<tspan fill="{c}">■</tspan> {html.escape(p.split("(")[0])}' for p, c in PHASE_COLOR.items())
    out.append(f'<text x="{W - R}" y="{H - 4}" text-anchor="end" fill="#6b7178">{legend}</text>')
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
        parts.append('<h4 style="font-size:14px;margin:18px 0 6px">실제 통계치와 주가 — 같은 시간축</h4>')
        parts.append(f'<div style="border:1px solid #e5e5e5;border-radius:6px;padding:8px">{r["chart_svg"]}</div>')
        parts.append(f'<div style="font-size:11px;color:#8a9199;margin-top:4px">시세는 Yahoo Finance 월말 수정종가({e(first)}부터 제공). '
                     '수출액·선행지수는 KOSIS 원자료이며 발표 지연(월+2)을 반영해 "그 시점에 알 수 있던 값"으로 그렸습니다. '
                     '주가가 수출 사이클을 앞서는지, 뒤따르는지 눈으로 확인하세요 — 주가가 앞서면 수출은 설명 변수이지 예측 변수가 아닙니다.</div>')

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
        verdict = ("<b style='color:#1e6b34'>0% 기준선을 이김</b>" if ev["beats_zero"] else "동률(CI가 0 포함)")
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

    # 점 예측
    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">전망</h4>')
    lines = []
    for label, h in HORIZONS.items():
        ev, fc = r["evaluation"][str(h)], r["forecast"][str(h)]
        if ev.get("beats_zero") and fc.get("point") is not None:
            lines.append(f'<li><b>{label}</b>: {pct(fc["point"])} (축소계수 {ev["shrink_slope"]:.2f} 적용, 원시 {pct(fc["raw"])})</li>')
        else:
            lines.append(f'<li><b>{label}</b>: 점 예측하지 않음 — 워크포워드에서 0% 기준선 대비 우위가 확인되지 않았습니다. '
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
    # KOSIS가 막히면 저장소에 보관된 마지막 성공분을 쓴다(일일 보고서가 매일 갱신해 둔다).
    fallback_dir = out_dir / "macro_fallback"
    try:
        tok = github_pages.token()
        fallback_dir.mkdir(parents=True, exist_ok=True)
        for series in ("leading_cycle", "semiconductor_exports"):
            text = github_pages.fetch(f"macro_history/{series}.csv", tok)
            if text:
                (fallback_dir / f"{series}.csv").write_text(text, encoding="utf-8")
    except Exception as exc:
        print("  월별 지표 사본을 받지 못했습니다(계속 진행):", exc, flush=True)
    macro, macro_info = load_macro_data(out_dir, pd.Timestamp(START) - pd.DateOffset(years=2),
                                        pd.Timestamp.now(tz="Asia/Seoul").date(), use_cache=not fetch,
                                        fallback_dir=fallback_dir)
    if not macro_info.get("fresh", True):
        print("  ⚠️ KOSIS 조회 실패 → 저장소 보관본 사용:", macro_info.get("fetch_errors", {}), flush=True)
    macro = {k: v for k, v in macro.items() if k in ("semiconductor_exports", "leading_cycle")}
    f = build_frame(monthly, macro)
    cols = [c for c, _ in FEATURES if c in f.columns and f[c].notna().mean() > 0.5]

    evaluation, forecast, phases, ic = {}, {}, {}, {}
    for label, h in HORIZONS.items():
        ev, oof = evaluate(f, cols, h)
        evaluation[str(h)] = ev
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
        for name in ("longterm.html", "longterm.json"):
            sha = github_pages.publish(f"docs/{args.target}/{name}", (out_dir / name).read_text(encoding="utf-8"),
                                       tok, f"longterm: {args.target} {result['as_of']}")
            print(f"발행 docs/{args.target}/{name} @ {sha}")


if __name__ == "__main__":
    main()

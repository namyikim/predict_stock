# -*- coding: utf-8 -*-
"""분기 영업이익 나우캐스트 — 월별 반도체 수출액으로 삼성전자·SK하이닉스의 이번 분기 영업이익을 추정한다.

주가 예측과 성격이 다르다. 주가는 미래를 맞히는 일이지만 이것은 **이미 벌어지고 있는 분기의
결과를 아직 발표되지 않았을 뿐인 상태에서 추정**하는 일(나우캐스트)이다. 메모리 두 회사의
영업이익은 D램·낸드 가격과 물량으로 결정되고, 한국 반도체 수출액은 같은 것을 매달 집계한다.
그래서 여기서는 상관이 예측력으로 이어질 여지가 실제로 있다 — 다만 그 여지를 검증으로 확인한다.

설계
- **학습과 서빙의 시점을 맞춘다.** 분기가 끝나기 전에 추정하므로, 그 분기의 앞 k개월 수출만 쓴다.
  과거 분기도 똑같이 앞 k개월로 특징을 만들어 학습한다(k는 이번 분기에 실제로 확보된 월 수).
- **기준선을 이겨야 숫자를 낸다.** 직전 분기 영업이익(랜덤워크)과 4분기 전(계절 나이브) 둘 다를
  워크포워드 MAE에서 이겨야 점 추정을 발표한다. 못 이기면 구간과 기준선만 보여 준다.
- 영업이익은 음수가 될 수 있다(2023년 두 회사 모두 적자). 로그를 쓰지 않고 원 단위로 다룬다.
- 환율을 넣는다. 수출은 달러, 영업이익은 원이다.

영업이익 이력은 DART 공시에서 받거나(권장) CSV로 넣는다.
    macro_inputs/operating_profit_<종목>.csv : quarter,value  (예: 2026-Q2,4210000000000)

    python tools/build_earnings_forecast.py --target samsung --out runs/earnings --dump
    python tools/build_earnings_forecast.py --target samsung --out runs/earnings --publish
"""
import argparse
import html
import json
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
from macro_utils import cli_features, load_cli, load_macro_data  # noqa: E402

KST = timezone(timedelta(hours=9))
TARGETS = {
    "samsung": {"name": "삼성전자", "corp_code": "00126380", "ticker": "005930.KS"},
    "sk_hynix": {"name": "SK하이닉스", "corp_code": "00164779", "ticker": "000660.KS"},
}
TRILLION = 1e12
MIN_TRAIN_QUARTERS = 16     # 이보다 적으면 학습하지 않는다(계절성 4분기 × 4주기)
FIRST_TEST_QUARTER = "2016Q1"


# ---------------------------------------------------------------------------
# 영업이익 이력
# ---------------------------------------------------------------------------
def read_operating_profit_csv(path):
    """quarter,value CSV. quarter는 2026-Q3 / 2026Q3 / 2026-09 형식을 받는다. value 단위는 원."""
    frame = pd.read_csv(path, dtype=str)
    if not {"quarter", "value"}.issubset(frame.columns):
        raise ValueError(f"{Path(path).name}: quarter,value 두 열이 필요합니다.")

    def to_period(text):
        text = str(text).upper().replace(" ", "").replace("-", "")
        if "Q" in text:
            return pd.Period(text, freq="Q")
        return pd.Period(pd.Timestamp(str(text)), freq="Q")

    periods = pd.PeriodIndex([to_period(t) for t in frame["quarter"]], freq="Q")
    value = pd.to_numeric(frame["value"].astype(str).str.replace(",", ""), errors="coerce")
    out = pd.Series(value.to_numpy(), index=periods, name="operating_profit").dropna().sort_index()
    if out.index.has_duplicates:
        raise ValueError("영업이익 CSV에 같은 분기가 중복됩니다.")
    if out.empty:
        raise ValueError("영업이익 CSV에 유효한 값이 없습니다.")
    return out


def dart_key():
    key = os.environ.get("DART_API_KEY")
    if not key:
        try:
            from google.colab import userdata
            key = userdata.get("DART_API_KEY")
        except Exception:
            pass
    return key


def _dart_request(key, params, retries=3):
    """DART OpenAPI. URL에 키가 들어가므로 예외에는 종류·코드만 남긴다."""
    import time
    import random
    from urllib.parse import urlencode
    from urllib.request import urlopen
    url = ("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json?"
           + urlencode({"crtfc_key": key, **params}))
    for attempt in range(retries):
        try:
            with urlopen(url, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except Exception as exc:
            detail = f'{type(exc).__name__} {getattr(exc, "code", "")}'.strip()
            if attempt == retries - 1:
                raise RuntimeError(f"DART 조회 실패({detail}). 키·연결을 확인하거나 "
                                   "macro_inputs/operating_profit_<종목>.csv를 사용하세요.") from None
            time.sleep(3 * (2 ** attempt) + random.uniform(0, 3))
    status = payload.get("status")
    if status == "013":          # 조회된 데이터 없음(아직 미공시 분기)
        return []
    if status != "000":
        raise RuntimeError(f"DART 응답 오류 {status}: {payload.get('message', '')}")
    return payload.get("list", [])


# 보고서 코드: 1분기·반기·3분기·사업보고서. 누적 금액이 오므로 차분해서 분기 값을 만든다.
DART_REPORTS = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}


def _pick_operating_profit(rows):
    """연결 손익계산서의 영업이익 누적 금액(원)."""
    best = None
    for row in rows:
        name = str(row.get("account_nm", "")).replace(" ", "")
        if name not in ("영업이익", "영업이익(손실)"):
            continue
        if str(row.get("fs_div", "")) not in ("CFS", ""):     # 연결 우선
            continue
        raw = str(row.get("thstrm_add_amount") or row.get("thstrm_amount") or "").replace(",", "")
        if raw in ("", "-"):
            continue
        try:
            best = float(raw)
        except ValueError:
            continue
        break
    return best


def fetch_operating_profit_dart(corp_code, key, start_year=2010, end_year=None):
    """분기 영업이익(원). 누적 공시를 차분해 3개월 값으로 만든다."""
    end_year = end_year or datetime.now(KST).year
    cumulative = {}
    for year in range(start_year, end_year + 1):
        for quarter, report in DART_REPORTS.items():
            rows = _dart_request(key, {"corp_code": corp_code, "bsns_year": str(year),
                                       "reprt_code": report, "fs_div": "CFS"})
            value = _pick_operating_profit(rows)
            if value is not None:
                cumulative[pd.Period(f"{year}Q{quarter}", freq="Q")] = value
    if not cumulative:
        raise RuntimeError("DART에서 영업이익을 찾지 못했습니다. corp_code와 공시 여부를 확인하세요.")
    series = pd.Series(cumulative).sort_index()
    quarterly = {}
    for period, value in series.items():
        if period.quarter == 1:
            quarterly[period] = value
            continue
        previous = period - 1
        if previous in series.index:            # 누적 - 직전 누적
            quarterly[period] = value - series[previous]
    return pd.Series(quarterly, name="operating_profit").sort_index()


def load_operating_profit(storage, target):
    """(분기 시계열, 출처). CSV가 있으면 그것을 쓰고, 없으면 DART에서 받는다."""
    path = Path(storage) / "macro_inputs" / f"operating_profit_{target}.csv"
    if path.exists():
        return read_operating_profit_csv(path), "user_csv"
    key = dart_key()
    if not key:
        raise RuntimeError("영업이익 이력이 없습니다. DART_API_KEY를 등록하거나 "
                           f"{path}에 quarter,value CSV를 저장하세요.")
    return fetch_operating_profit_dart(TARGETS[target]["corp_code"], key), "DART_API"


# ---------------------------------------------------------------------------
# 특징: 분기의 '앞 k개월'만 쓴다
# ---------------------------------------------------------------------------
def monthly_usdkrw(cache_dir, fetch=True):
    path = Path(cache_dir) / "usdkrw_monthly.csv"
    if fetch:
        import yfinance as yf
        history = yf.Ticker("KRW=X").history(start="2005-01-01", auto_adjust=False)
        if history is None or history.empty:
            raise RuntimeError("원/달러 환율을 받지 못했습니다.")
        history.index = pd.to_datetime(history.index).tz_localize(None)
        monthly = history["Close"].astype(float).resample("MS").mean().dropna()
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        monthly.to_csv(path, header=["usdkrw"])
        return monthly
    if not path.exists():
        raise RuntimeError(f"환율 캐시가 없습니다: {path}")
    return pd.read_csv(path, index_col=0, parse_dates=True)["usdkrw"]


def first_k_months(monthly, k):
    """분기별로 앞 k개월만 평균낸다. 분기 안에 k개월이 다 없으면 NaN."""
    frame = pd.DataFrame({"value": monthly})
    frame["quarter"] = pd.PeriodIndex(frame.index, freq="Q")
    frame["rank"] = frame.groupby("quarter").cumcount() + 1
    picked = frame[frame["rank"] <= k]
    counts = picked.groupby("quarter")["value"].count()
    means = picked.groupby("quarter")["value"].mean()
    return means.where(counts == k)


def build_frame(profit, exports, usdkrw, k, cli=None):
    """분기 표. 특징은 모두 그 분기의 앞 k개월 또는 그 이전 자료만 쓴다.

    다음 분기 전망을 위해 profit_next(=t+1 분기 영업이익)와, 분기 t의 k번째 달 말 시점에 보이는
    G20 CLI를 함께 둔다. CLI는 참조월+1개월 20일 이후에만 보이므로 k번째 달 말에는 (k-1)번째 달
    값까지 들어온다.
    """
    exp_k = first_k_months(exports, k)
    fx_k = first_k_months(usdkrw, k)
    f = pd.DataFrame({"exports_k": exp_k, "usdkrw_k": fx_k})
    f["exports_krw_k"] = f["exports_k"] * f["usdkrw_k"]        # 원화 환산 수출 규모
    f["exports_yoy"] = f["exports_k"] / f["exports_k"].shift(4) - 1
    f["exports_qoq"] = f["exports_k"] / f["exports_k"].shift(1) - 1
    f["profit"] = profit.reindex(f.index)
    f["profit_lag1"] = f["profit"].shift(1)                    # 직전 분기(이번 분기 중에 이미 발표됨)
    f["profit_lag3"] = f["profit"].shift(3)                    # 다음 분기의 '4분기 전'
    f["profit_lag4"] = f["profit"].shift(4)
    f["profit_next"] = f["profit"].shift(-1)                   # 다음 분기 타깃
    if cli is not None and len(cli):
        asof = pd.DatetimeIndex([(q.start_time + pd.DateOffset(months=k)) - pd.Timedelta(days=1) for q in f.index])
        cf = cli_features(cli, asof)
        f["cli_level"] = cf["cli_level"].to_numpy()
        f["cli_change_3m"] = cf["cli_change_3m"].to_numpy()
    return f


FEATURES = ["exports_krw_k", "exports_yoy", "exports_qoq", "profit_lag1", "profit_lag4"]
# 다음 분기: 직전 분기 영업이익은 profit_lag1(t-1)이 마지막으로 아는 값이고, 계절 기준선은 t-3이다.
FEATURES_NEXT = ["exports_krw_k", "exports_yoy", "exports_qoq", "profit_lag1", "profit_lag3"]
CLI_FEATURES = ["cli_level", "cli_change_3m"]


def walk_forward(f, target="profit", features=None, gap=0, rw="profit_lag1", sn="profit_lag4",
                 first_test=FIRST_TEST_QUARTER, min_train=MIN_TRAIN_QUARTERS):
    """확장 창. 분기 t는 타깃이 이미 알려진 분기들로만 학습한다.

    gap=0: 이번 분기 나우캐스트. t 이전 분기의 영업이익은 다 발표됐다.
    gap=1: 다음 분기 전망. 행 s의 타깃은 profit(s+1)이라, 분기 t 중에는 s+1<=t-1, 즉 s<=t-2까지만 안다.
    """
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    features = features or FEATURES
    usable = f.dropna(subset=features + [target, rw, sn])
    rows = []
    for t in usable.index[usable.index >= pd.Period(first_test, freq="Q")]:
        train = usable[usable.index < t - gap]
        if len(train) < min_train:
            continue
        model = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
        model.fit(train[features], train[target])
        rows.append({
            "quarter": t,
            "actual": float(usable.loc[t, target]),
            "model": float(model.predict(usable.loc[[t], features])[0]),
            "random_walk": float(usable.loc[t, rw]),
            "seasonal_naive": float(usable.loc[t, sn]),
        })
    return pd.DataFrame(rows).set_index("quarter") if rows else pd.DataFrame()


def evaluate(oof):
    if oof.empty:
        return {"n": 0, "beats_baselines": False, "note": "표본 부족"}
    out = {"n": int(len(oof)), "first": str(oof.index[0]), "last": str(oof.index[-1])}
    for name in ("model", "random_walk", "seasonal_naive"):
        out[f"mae_{name}"] = float(np.mean(np.abs(oof["actual"] - oof[name])))
    out["corr"] = float(np.corrcoef(oof["actual"], oof["model"])[0, 1]) if len(oof) > 2 else np.nan
    out["beats_baselines"] = bool(out["mae_model"] < min(out["mae_random_walk"], out["mae_seasonal_naive"]))
    residual = (oof["actual"] - oof["model"]).to_numpy()
    out["residual_q10"] = float(np.quantile(residual, .10))
    out["residual_q90"] = float(np.quantile(residual, .90))
    out["mape_model"] = float(np.mean(np.abs((oof["actual"] - oof["model"]) /
                                             np.where(np.abs(oof["actual"]) < 1e-9, np.nan, oof["actual"]))))
    return out


def fit_live(f, live_quarter, target="profit", features=None, gap=0):
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    features = features or FEATURES
    usable = f.dropna(subset=features + [target])
    train = usable[usable.index < live_quarter - gap]
    live = f.loc[[live_quarter], features] if live_quarter in f.index else None
    if len(train) < MIN_TRAIN_QUARTERS or live is None or live.isna().any(axis=1).iloc[0]:
        return None, len(train)
    model = make_pipeline(StandardScaler(), Ridge(alpha=1.0)).fit(train[features], train[target])
    return float(model.predict(live)[0]), len(train)


# ---------------------------------------------------------------------------
# 렌더링
# ---------------------------------------------------------------------------
TD = 'style="padding:6px 10px;border-top:1px solid #eee"'
TDR = TD[:-1] + ';text-align:right;font-variant-numeric:tabular-nums"'
TH = 'style="padding:8px 10px;text-align:left;font-size:11px;color:#6b7178;letter-spacing:.5px;background:#fafafa"'
THR = TH.replace("text-align:left", "text-align:right")


def jo(value):
    return "—" if value is None or not np.isfinite(value) else f"{value / TRILLION:,.2f}조원"


def render_chart(f, oof, name):
    """분기 영업이익(막대)과 원화 환산 반도체 수출(선). 사이클이 같이 도는지 눈으로 본다."""
    d = f.dropna(subset=["exports_krw_k"]).copy()
    d = d[d.index >= pd.Period("2011Q1", freq="Q")]
    if len(d) < 12:
        return ""
    W, L, R, TOP, BOT = 900, 66, 58, 28, 34
    PH = 240
    H = TOP + PH + BOT
    n = len(d)
    step = (W - L - R) / n

    def X(i):
        return L + step * (i + .5)

    profit = d["profit"]
    pmax = float(np.nanmax(np.abs(profit))) if profit.notna().any() else 1.
    zero_y = TOP + PH * .72

    def YP(v):
        return zero_y - v / pmax * (PH * .62)

    ex = d["exports_krw_k"]
    elo, ehi = float(ex.min()), float(ex.max())

    def YE(v):
        return TOP + PH - (v - elo) / max(ehi - elo, 1e-9) * (PH - 12)

    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;'
           f'font-family:-apple-system,\'Malgun Gothic\',sans-serif;font-size:11px">']
    for i, (period, row) in enumerate(d.iterrows()):
        v = row["profit"]
        if not np.isfinite(v):
            continue
        top, height = (YP(v), zero_y - YP(v)) if v >= 0 else (zero_y, YP(v) - zero_y)
        color = "#4c78a8" if v >= 0 else "#b5453c"
        out.append(f'<rect x="{X(i) - step * .34:.1f}" y="{top:.1f}" width="{step * .68:.1f}" '
                   f'height="{max(height, 0.6):.1f}" fill="{color}" opacity="0.85"/>')
    if oof is not None and len(oof):
        pts = [(i, oof.loc[p, "model"]) for i, p in enumerate(d.index) if p in oof.index]
        if pts:
            out.append('<g>' + "".join(
                f'<circle cx="{X(i):.1f}" cy="{YP(v):.1f}" r="2.6" fill="none" stroke="#c8952a" stroke-width="1.4"/>'
                for i, v in pts) + '</g>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{zero_y:.1f}" y2="{zero_y:.1f}" stroke="#999"/>')
    out.append(f'<polyline points="{" ".join(f"{X(i):.1f},{YE(v):.1f}" for i, v in enumerate(ex))}" '
               'fill="none" stroke="#2e7d32" stroke-width="1.6"/>')
    for v in (pmax, pmax / 2, 0, -pmax / 2):
        if abs(v) > pmax:
            continue
        out.append(f'<text x="{L - 6}" y="{YP(v) + 4:.1f}" text-anchor="end" fill="#4c78a8">{v / TRILLION:,.0f}조</text>')
    out.append(f'<text x="{W - R + 6}" y="{YE(ehi) + 4:.1f}" fill="#2e7d32">수출↑</text>')
    out.append(f'<text x="{W - R + 6}" y="{YE(elo) + 4:.1f}" fill="#2e7d32">수출↓</text>')
    for i, period in enumerate(d.index):
        if period.quarter == 1 and period.year % 2 == 1:
            out.append(f'<text x="{X(i):.1f}" y="{TOP + PH + 15:.1f}" text-anchor="middle" fill="#8a9199">{period.year}</text>')
    out.append(f'<text x="{L}" y="14" fill="#1a1a1a" font-weight="600">'
               f'{html.escape(name)} 분기 영업이익(막대)과 원화 환산 반도체 수출(선)</text>')
    out.append(f'<text x="{W - R}" y="14" text-anchor="end" fill="#c8952a">○ 워크포워드 추정치</text>')
    out.append(f'<rect x="{L}" y="{TOP}" width="{W - L - R}" height="{PH}" fill="none" stroke="#ddd"/>')
    out.append("</svg>")
    return "".join(out)


def render_fragment(result):
    e = html.escape
    r = result
    ev = r["evaluation"]
    parts = ['<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
             f'8. 이번 분기 영업이익 추정 <span style="font-weight:400;color:#8a9199;font-size:12px">'
             f'&nbsp;{e(r["quarter"])} · 월별 반도체 수출액 기준 · '
             f'{e(r.get("months_included") or "")} 반영</span></h3>']
    parts.append('<div style="background:#fdf8ec;border-left:4px solid #c8952a;padding:12px 16px;'
                 'border-radius:0 5px 5px 0;font-size:13px">'
                 '주가 예측과 성격이 다릅니다. 이것은 <b>이미 진행 중인 분기의 결과를 발표 전에 추정</b>하는 '
                 '나우캐스트입니다. 메모리 영업이익과 한국 반도체 수출액은 같은 것(D램·낸드 가격과 물량)을 재고 있어 '
                 f'여지가 실제로 있습니다. 분기가 끝나기 전이므로 이번 분기의 <b>{e(r.get("months_included") or "")}</b> 수출만 썼고, '
                 f'과거 분기도 똑같이 <b>각 분기의 앞 {r["months_used"]}개월</b>로 특징을 만들어 학습했습니다 — '
                 '3개월 평균으로 학습한 계수를 2개월 평균에 적용하면 성질이 다른 값을 넣는 셈이 되기 때문입니다.'
                 + (f' <b>{e(r["months_missing"])} 수출은 아직 KOSIS에 올라오지 않았습니다.</b> '
                    '그 달이 들어오면 추정이 더 단단해집니다.' if r.get("months_missing") else "")
                 + '</div>')

    if r.get("chart_svg"):
        parts.append(f'<div style="border:1px solid #e5e5e5;border-radius:6px;padding:8px;margin-top:10px">{r["chart_svg"]}</div>')

    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">추정</h4>')
    if r["point"] is not None:
        parts.append(f'<div style="font-size:22px;font-weight:700">{jo(r["point"])}'
                     f'<span style="font-size:13px;font-weight:400;color:#6b7178"> · 80% 구간 '
                     f'{jo(r["low"])} ~ {jo(r["high"])}</span></div>')
        parts.append(f'<div style="font-size:12px;color:#6b7178;margin-top:4px">직전 분기 {jo(r["last_actual"])}'
                     f'({e(r["last_actual_quarter"])}) 대비 {r["change_vs_last"]:+.1%}</div>')
    else:
        parts.append('<div style="font-size:16px;font-weight:600;color:#6b7178">예측하지 않음</div>'
                     f'<div style="font-size:12px;color:#8a9199;margin-top:4px">{e(r["no_point_reason"])} '
                     f'참고로 직전 분기는 {jo(r["last_actual"])}({e(r["last_actual_quarter"])})였습니다.</div>')

    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">검증 — 기준선을 이기는가 (워크포워드)</h4>')
    if ev.get("n"):
        body = ""
        for key, label in (("model", "수출 기반 모델"), ("random_walk", "직전 분기 그대로"),
                           ("seasonal_naive", "4분기 전 그대로")):
            body += (f'<tr><td {TD}>{label}</td><td {TDR}>{ev[f"mae_{key}"] / TRILLION:,.2f}조원</td></tr>')
        parts.append(f'<div style="font-size:12px;color:#6b7178;margin-bottom:6px">'
                     f'{e(ev["first"])} ~ {e(ev["last"])} · {ev["n"]}개 분기 · 평균절대오차(MAE)가 작을수록 좋습니다.</div>')
        parts.append('<div style="overflow-x:auto"><table style="width:100%;min-width:420px;border-collapse:collapse;'
                     f'font-size:13px;border:1px solid #e5e5e5"><tr><th {TH}>방법</th><th {THR}>MAE</th></tr>{body}</table></div>')
        verdict = ("<b style='color:#1e6b34'>두 기준선을 모두 이겼습니다.</b>" if ev["beats_baselines"]
                   else "<b style='color:#a8322a'>기준선을 이기지 못했습니다.</b> 점 추정을 내지 않습니다.")
        parts.append(f'<div style="font-size:13px;margin-top:8px">{verdict} '
                     f'실제값과의 상관 {ev.get("corr", float("nan")):.2f} · 평균 오차율 {ev.get("mape_model", float("nan")):.1%}</div>')
    else:
        parts.append(f'<div style="font-size:13px;color:#6b7178">{e(ev.get("note", "표본 부족"))}</div>')

    # ---- 다음 분기 전망
    nq = r.get("next_quarter")
    if nq:
        parts.append('<h4 style="font-size:14px;margin:22px 0 6px">'
                     f'다음 분기({e(nq["quarter"])}) 전망 — G20 경기선행지수를 넣어 보다</h4>')
        parts.append('<div style="font-size:12px;color:#6b7178;margin-bottom:6px">이번 분기 나우캐스트와 달리 '
                     '아직 시작하지 않은 분기를 내다보는 것이라 훨씬 어렵습니다. 선행지수가 쓸모 있다면 여기서 '
                     '나타나야 합니다. 같은 날짜에서 CLI를 넣은 모델과 뺀 모델을 나란히 쟀습니다.</div>')
        if nq["point"] is not None:
            parts.append(f'<div style="font-size:20px;font-weight:700">{jo(nq["point"])}'
                         f'<span style="font-size:13px;font-weight:400;color:#6b7178"> · 80% 구간 '
                         f'{jo(nq["low"])} ~ {jo(nq["high"])} · {"CLI 포함" if nq["chosen"] == "with_cli" else "CLI 제외"} 모델</span></div>')
        else:
            parts.append('<div style="font-size:16px;font-weight:600;color:#6b7178">예측하지 않음</div>'
                         f'<div style="font-size:12px;color:#8a9199;margin-top:4px">{e(nq["no_point_reason"])}</div>')
        body = ""
        for label, evx in (("CLI 제외", nq["evaluation_without_cli"]), ("CLI 포함", nq.get("evaluation_with_cli"))):
            if not evx or not evx.get("n"):
                body += f'<tr><td {TD}>{label}</td><td {TDR} colspan="4">{e((evx or {}).get("note", "미포함"))}</td></tr>'
                continue
            body += (f'<tr><td {TD}>{label}</td><td {TDR}>{evx["n"]}</td>'
                     f'<td {TDR}>{evx["mae_model"] / TRILLION:,.2f}조원</td>'
                     f'<td {TDR}>{evx["mae_random_walk"] / TRILLION:,.2f} / {evx["mae_seasonal_naive"] / TRILLION:,.2f}조원</td>'
                     f'<td {TDR}>{"이김" if evx["beats_baselines"] else "못 이김"}</td></tr>')
        parts.append('<div style="overflow-x:auto"><table style="width:100%;min-width:520px;border-collapse:collapse;'
                     f'font-size:13px;border:1px solid #e5e5e5"><tr><th {TH}>모델</th><th {THR}>분기 수</th>'
                     f'<th {THR}>MAE</th><th {THR}>기준선 MAE (직전/4분기 전)</th><th {THR}>판정</th></tr>{body}</table></div>')
        if r.get("cli_active"):
            parts.append('<div style="font-size:11px;color:#8a9199;margin-top:4px">G20 CLI는 참조월+1개월 20일 이후 값만 썼습니다. '
                         'CLI는 매달 소급 수정되므로 이 표는 최종 수정치 기준이라 낙관적입니다.</div>')
        else:
            parts.append(f'<div style="font-size:11px;color:#8a9199;margin-top:4px">G20 CLI 미포함 — '
                         f'{e(str(r.get("cli_info", {}).get("reason", "")))}</div>')

    parts.append(f'<div style="font-size:11px;color:#8a9199;margin-top:10px">'
                 f'영업이익 출처: {e(r["profit_source"])} · 이력 {e(r["profit_first"])}~{e(r["profit_last"])} '
                 f'({r["profit_n"]}개 분기) · 수출액 최신월 {e(r["exports_last_month"])} · '
                 f'생성 {e(r["generated_at"])}<br>'
                 '분기 영업이익은 발표 전까지 확정값이 아니며, 이 추정은 연구·교육용입니다. '
                 '회사 가이던스·증권사 추정치와 다를 수 있습니다.</div>')
    return "".join(parts)


# ---------------------------------------------------------------------------
def analyse(target, out_dir, fetch=True):
    spec = TARGETS[target]
    out_dir.mkdir(parents=True, exist_ok=True)
    profit, profit_source = load_operating_profit(out_dir, target)

    fallback_dir = out_dir / "macro_fallback"
    try:
        token = github_pages.token()
        fallback_dir.mkdir(parents=True, exist_ok=True)
        text = github_pages.fetch("macro_history/semiconductor_exports.csv", token)
        if text:
            (fallback_dir / "semiconductor_exports.csv").write_text(text, encoding="utf-8")
        text = github_pages.fetch("macro_history/leading_cycle.csv", token)
        if text:
            (fallback_dir / "leading_cycle.csv").write_text(text, encoding="utf-8")
    except Exception as exc:
        print("  월별 지표 사본을 받지 못했습니다(계속 진행):", exc, flush=True)
    macro, macro_info = load_macro_data(out_dir, "2005-01-01",
                                        datetime.now(KST).date(), use_cache=not fetch,
                                        fallback_dir=fallback_dir)
    exports = (macro["semiconductor_exports"].set_index("month")["value"]
               .asfreq("MS").dropna())
    usdkrw = monthly_usdkrw(out_dir / "cache", fetch=fetch).reindex(exports.index).ffill()

    # 이번 분기에 실제로 확보된 월 수 = 나우캐스트에 쓸 k
    live_quarter = pd.Period(exports.index[-1], freq="Q")
    in_quarter = exports.index[pd.PeriodIndex(exports.index, freq="Q") == live_quarter]
    months_used = len(in_quarter)
    month_names = ", ".join(f"{d.month}월" for d in in_quarter)
    missing_months = ", ".join(
        f"{m}월" for m in range(live_quarter.start_time.month, live_quarter.end_time.month + 1)
        if m not in {d.month for d in in_quarter})

    cli, cli_info = None, {"enabled": False, "reason": "USE_CLI=False"}
    if os.environ.get("USE_CLI", "true").strip().lower() not in ("0", "false", "no"):
        try:
            cli, cli_info = load_cli(out_dir, "2000-01-01", datetime.now(KST).date(),
                                     use_cache=not fetch, fallback_dir=fallback_dir)
            cli_info["enabled"] = True
        except Exception as exc:
            cli, cli_info = None, {"enabled": False, "reason": f"{type(exc).__name__}: {exc}"}
            print("  ⚠️ G20 CLI를 쓸 수 없어 빼고 진행합니다:", cli_info["reason"], flush=True)

    f = build_frame(profit, exports, usdkrw, months_used, cli)
    oof = walk_forward(f)
    ev = evaluate(oof)
    point, n_train = fit_live(f, live_quarter)

    # ---- 다음 분기 전망 (CLI가 이론적으로 맞는 자리) --------------------------------
    # 분기 t 중간에 t+1의 영업이익을 내다본다. 같은 날짜에서 CLI를 넣은 것과 뺀 것을 나란히 재고,
    # 기준선(마지막으로 아는 영업이익 t-1, 4분기 전 t-3)을 이길 때만 숫자를 낸다.
    cli_active = cli is not None and all(c in f.columns and f[c].notna().mean() > 0.5 for c in CLI_FEATURES)
    next_quarter = live_quarter + 1
    next_variants = {"without_cli": FEATURES_NEXT}
    if cli_active:
        next_variants["with_cli"] = FEATURES_NEXT + CLI_FEATURES
    next_results = {}
    for name, feats in next_variants.items():
        oof_n = walk_forward(f, target="profit_next", features=feats, gap=1, rw="profit_lag1", sn="profit_lag3")
        ev_n = evaluate(oof_n)
        pt_n, ntr = fit_live(f, live_quarter, target="profit_next", features=feats, gap=1)
        next_results[name] = {"evaluation": ev_n, "raw_point": pt_n, "n_train": ntr}
    chosen = ("with_cli" if cli_active and next_results["with_cli"]["evaluation"].get("mae_model", np.inf)
              < next_results["without_cli"]["evaluation"].get("mae_model", np.inf) else "without_cli")
    nr = next_results[chosen]
    next_point = nr["raw_point"] if nr["evaluation"].get("beats_baselines") and nr["raw_point"] is not None else None
    next_block = {
        "quarter": f"{next_quarter.year}년 {next_quarter.quarter}분기", "quarter_code": str(next_quarter),
        "chosen": chosen, "point": next_point,
        "low": (next_point + nr["evaluation"]["residual_q10"]) if next_point is not None else None,
        "high": (next_point + nr["evaluation"]["residual_q90"]) if next_point is not None else None,
        "raw_point": nr["raw_point"], "evaluation": nr["evaluation"],
        "evaluation_without_cli": next_results["without_cli"]["evaluation"],
        "evaluation_with_cli": next_results.get("with_cli", {}).get("evaluation"),
        "no_point_reason": ("" if next_point is not None else
                            "워크포워드에서 '마지막으로 아는 영업이익 그대로'·'4분기 전 그대로'보다 오차가 작다는 것을 보이지 못했습니다."),
    }
    no_point_reason = ""
    if point is None:
        no_point_reason = f"학습 분기가 {n_train}개로 부족하거나 이번 분기 특징이 비어 있습니다."
    elif not ev.get("beats_baselines"):
        no_point_reason = ("워크포워드에서 '직전 분기 그대로' 또는 '4분기 전 그대로'보다 "
                           "오차가 작다는 것을 보이지 못했습니다.")
        point = None

    last_actual_quarter = profit.index[-1]
    last_actual = float(profit.iloc[-1])
    result = {
        "target": target, "name": spec["name"],
        "quarter": f"{live_quarter.year}년 {live_quarter.quarter}분기",
        "quarter_code": str(live_quarter), "months_used": months_used,
        "months_included": month_names, "months_missing": missing_months,
        "point": point,
        "low": (point + ev["residual_q10"]) if point is not None else None,
        "high": (point + ev["residual_q90"]) if point is not None else None,
        "change_vs_last": (point / last_actual - 1) if (point is not None and last_actual) else float("nan"),
        "no_point_reason": no_point_reason,
        "last_actual": last_actual, "last_actual_quarter": str(last_actual_quarter),
        "evaluation": ev, "n_train": n_train,
        "profit_source": profit_source, "profit_n": int(len(profit)),
        "profit_first": str(profit.index[0]), "profit_last": str(profit.index[-1]),
        "exports_last_month": exports.index[-1].date().isoformat(),
        "macro_sources": macro_info.get("sources", {}),
        "cli_info": cli_info, "cli_active": cli_active,
        "next_quarter": next_block,
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
    }
    result["chart_svg"] = render_chart(f, oof, spec["name"])
    return result, f, oof, profit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="samsung", choices=list(TARGETS))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--dump", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    out_dir = args.out / args.target
    result, frame, oof, profit_series = analyse(args.target, out_dir, fetch=not args.no_fetch)
    fragment = render_fragment(result)
    (out_dir / "earnings.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "earnings.html").write_text(fragment, encoding="utf-8")
    frame.to_csv(out_dir / "earnings_frame.csv")
    profit_series.rename_axis("quarter").rename("value").reset_index().assign(
        quarter=lambda d: d["quarter"].astype(str)).to_csv(out_dir / "earnings_profit.csv", index=False)
    oof.to_csv(out_dir / "earnings_oof.csv")
    print(f"{result['name']} {result['quarter']} ({result.get('months_included')} 반영): "
          f"{jo(result['point']) if result['point'] is not None else '예측하지 않음'}"
          f" · 직전 {jo(result['last_actual'])} ({result['last_actual_quarter']})", flush=True)
    if args.dump:
        print(json.dumps(result["evaluation"], ensure_ascii=False, indent=2, default=str))
        print(oof.tail(8).to_string())
    if args.publish:
        token = github_pages.token()
        # 다음 실행이 최근 2년만 다시 받으면 되도록 이력을 저장소에 남긴다.
        if result["profit_source"].startswith("DART"):
            series = pd.read_csv(out_dir / "earnings_profit.csv") if (out_dir / "earnings_profit.csv").exists() else None
            if series is not None:
                github_pages.publish(f"macro_history/operating_profit_{args.target}.csv",
                                     series.to_csv(index=False), token,
                                     f"earnings: {args.target} 영업이익 이력 ({result['profit_last']})")
        cli_cache = out_dir / "macro_cache" / "cli_g20.csv"
        if result.get("cli_info", {}).get("fresh") and cli_cache.exists():
            github_pages.publish("macro_history/cli_g20.csv", cli_cache.read_text(encoding="utf-8"),
                                 token, f"macro: cli_g20 ({result['cli_info'].get('last')})")
        for name in ("earnings.html", "earnings.json"):
            sha = github_pages.publish(f"docs/{args.target}/{name}",
                                       (out_dir / name).read_text(encoding="utf-8"),
                                       token, f"earnings: {args.target} {result['quarter_code']}")
            print(f"발행 docs/{args.target}/{name} @ {sha}")


if __name__ == "__main__":
    main()

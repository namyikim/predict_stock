# -*- coding: utf-8 -*-
"""금값 결정 요인과 평가 — 회귀식이 말하는 '적정 가격'과 실제 금값의 괴리(2026-09-27 요청).

    ln(금값) = -6.74 + 3.69·ln(미국 CPI) − 1.37·ln(달러인덱스) − 0.06·(미국 10년물 금리, %)
    분석기간 2000.1~2026.8, R² 0.96 (사용자 제공 식)

같은 자료(월평균 금값·CPI-U 계절조정·달러인덱스·10년물 금리)로 다시 맞추면 계수가 -6.80 · 3.70 · −1.37 · −0.06,
R² 0.956 으로 식이 재현된다(2026-09-27 확인). 그림은 금값(좌, 달러/온스)·적정 가격(좌)·괴리(우, %) 세 선이다.

자료(모두 월평균):
- 금값: 2000-01~2024-12 세계은행 원자재 가격(Pink Sheet, LBMA 기준 월평균, macro_history/gold_worldbank_monthly.csv)
  → 그 뒤는 COMEX 금 선물 연속물(GC=F) 월평균. 겹치는 293개월에서 두 계열 차이는 평균 0.09%.
- 미국 CPI: CPI-U 계절조정(FRED CPIAUCSL = BLS CUSR0000SA0). FRED(키) → BLS(키 없음) → 저장소 보관본 순으로 받는다.
  아직 발표되지 않은 최근 달은 직전 발표치를 쓰고 그렇게 표시한다.
- 달러인덱스(DX-Y.NYB)·10년물 금리(^TNX, %): Yahoo 일별 → 월평균.

적정 가격은 식이 2000~2026 전체 자료로 맞춘 것이라 과거 구간의 괴리는 사후적이다. 목표가가 아니다.
"""
import html
import io
import json
import math
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODEL = {"const": -6.74, "cpi": 3.69, "dxy": -1.37, "y10": -0.06, "r2": 0.96, "period": "2000.1~2026.8"}
START = "2000-01-01"
GAP_AXIS = (-0.30, 0.70)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) predict_stock/1.0 (research; non-commercial)"}
GOLD_COLOR, FAIR_COLOR, GAP_COLOR = "#b8860b", "#1a5490", "#c0392b"


# ---------------------------------------------------------------------------
# 자료
# ---------------------------------------------------------------------------
def _monthly_csv(path):
    path = Path(path)
    if not path.exists():
        return None
    frame = pd.read_csv(path)
    return pd.Series(pd.to_numeric(frame["value"], errors="coerce").to_numpy(),
                     index=pd.PeriodIndex(pd.to_datetime(frame["month"]), freq="M")).dropna()


def _yahoo_monthly(ticker, cache_dir, fetch=True):
    """Yahoo 일별 종가의 월평균. 받지 못하면 지난 캐시."""
    path = Path(cache_dir) / f"valuation_{ticker.replace('^', '_').replace('=', '_')}.csv"
    daily = None
    if fetch:
        try:
            import yfinance as yf
            h = yf.Ticker(ticker).history(start=START, auto_adjust=False)
            if not h.empty:
                daily = h["Close"].copy()
                daily.index = pd.to_datetime(daily.index).tz_localize(None).normalize()
                daily = daily[~daily.index.duplicated()].dropna()
                path.parent.mkdir(parents=True, exist_ok=True)
                daily.rename("close").to_csv(path)
        except Exception as exc:
            print(f"  ⚠️ {ticker} 조회 실패: {type(exc).__name__}", flush=True)
    if daily is None and path.exists():
        daily = pd.read_csv(path, index_col=0, parse_dates=True)["close"]
    if daily is None or daily.empty:
        return None
    # 주말 봉(Globex 일요일 세션)은 버린다 — 금속 보고서의 drop_unclosed 와 같은 이유
    daily = daily[daily.index.weekday < 5]
    return daily.resample("ME").mean().to_period("M").dropna()


def _fetch_cpi_fred():
    sys.path.insert(0, str(ROOT))
    from data_sources import oecd
    key = oecd.fred_key()
    if not key:
        raise RuntimeError("FRED_API_KEY 없음")
    frame = oecd.fetch_fred_monthly("CPIAUCSL", key, START)
    return pd.Series(frame["value"].to_numpy(), index=pd.PeriodIndex(pd.to_datetime(frame["month"]), freq="M"))


def _fetch_cpi_bls():
    """BLS 공개 API(키 없음, 한 번에 10년). CUSR0000SA0 = CPI-U 계절조정 = FRED CPIAUCSL."""
    rows = {}
    this_year = pd.Timestamp.now().year
    for start in range(2000, this_year + 1, 10):
        payload = json.dumps({"seriesid": ["CUSR0000SA0"], "startyear": str(start),
                              "endyear": str(min(start + 9, this_year))}).encode()
        request = urllib.request.Request("https://api.bls.gov/publicAPI/v2/timeseries/data/", data=payload,
                                         headers={**UA, "Content-type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode())
        if data.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError(f"BLS 응답 {data.get('status')}")
        for r in data["Results"]["series"][0]["data"]:
            try:
                value = float(r["value"])
            except ValueError:
                continue                         # 발표되지 않은 달은 '-'
            if r["period"].startswith("M") and r["period"] != "M13":
                rows[pd.Period(f"{r['year']}-{r['period'][1:]}", freq="M")] = value
    return pd.Series(rows).sort_index()


def load_cpi(fallback_path=None):
    """(Series, 출처). FRED → BLS → 보관본."""
    for name, fetch in (("FRED CPIAUCSL", _fetch_cpi_fred), ("BLS CUSR0000SA0", _fetch_cpi_bls)):
        try:
            series = fetch()
            if series is not None and len(series) > 100:
                return series.sort_index(), name
        except Exception as exc:
            print(f"  미국 CPI {name} 조회 실패 → 다음: {type(exc).__name__}", flush=True)
    series = _monthly_csv(fallback_path or ROOT / "macro_history" / "us_cpi.csv")
    return series, "저장소 보관본(us_cpi.csv)"


def load_inputs(cache_dir, fetch=True):
    """월별 입력 틀(gold, cpi, dxy, y10)과 출처 정보."""
    wb = _monthly_csv(ROOT / "macro_history" / "gold_worldbank_monthly.csv")
    futures = _yahoo_monthly("GC=F", cache_dir, fetch)
    dxy = _yahoo_monthly("DX-Y.NYB", cache_dir, fetch)
    y10 = _yahoo_monthly("^TNX", cache_dir, fetch)
    cpi, cpi_source = load_cpi() if fetch else (_monthly_csv(ROOT / "macro_history" / "us_cpi.csv"), "저장소 보관본")
    if futures is None or dxy is None or y10 is None or cpi is None:
        return None, {"reason": "금값·달러인덱스·금리·CPI 중 받지 못한 자료가 있습니다."}
    gold = futures.copy()
    if wb is not None:
        gold = pd.concat([wb, futures[futures.index > wb.index.max()]])     # 세계은행이 끝난 뒤는 선물
    frame = pd.DataFrame({"gold": gold, "dxy": dxy, "y10": y10}).loc[START[:7]:]
    frame = frame.dropna(subset=["gold", "dxy", "y10"])
    cpi_full = cpi.reindex(pd.period_range(cpi.index.min(), cpi.index.max(), freq="M")).interpolate()
    frame["cpi"] = cpi_full.reindex(frame.index)
    last_cpi = cpi.index.max()
    frame["cpi_carried"] = frame.index > last_cpi
    frame["cpi"] = frame["cpi"].ffill()
    frame = frame.dropna(subset=["cpi"])
    return frame, {"cpi_source": cpi_source, "cpi_last": str(last_cpi), "cpi_raw": cpi,
                   "gold_source": ("세계은행 월평균 ~" + str(wb.index.max()) + " · 이후 COMEX 선물 월평균")
                   if wb is not None else "COMEX 선물 월평균"}


# ---------------------------------------------------------------------------
# 계산
# ---------------------------------------------------------------------------
def fair_value(frame, model=MODEL):
    return np.exp(model["const"] + model["cpi"] * np.log(frame["cpi"]) + model["dxy"] * np.log(frame["dxy"])
                  + model["y10"] * frame["y10"])


def evaluate(frame, model=MODEL):
    """적정 가격·괴리와 이 자료에서 잰 적합도(R², 같은 자료 OLS 계수)."""
    out = frame.copy()
    out["fair"] = fair_value(out, model)
    out["gap"] = out["gold"] / out["fair"] - 1
    y, yhat = np.log(out["gold"]), np.log(out["fair"])
    r2 = float(1 - ((y - yhat) ** 2).sum() / ((y - y.mean()) ** 2).sum())
    X = np.column_stack([np.ones(len(out)), np.log(out["cpi"]), np.log(out["dxy"]), out["y10"]])
    coef = np.linalg.lstsq(X, y.to_numpy(), rcond=None)[0]
    return out, {"r2": r2, "coef": [float(c) for c in coef], "n": int(len(out)),
                 "first": str(out.index.min()), "last": str(out.index.max())}


# ---------------------------------------------------------------------------
# 그림: 금값·적정 가격(좌, 달러/온스) · 괴리(우, −30%~70%)
# ---------------------------------------------------------------------------
def _nice_step(span):
    raw = span / 6
    magnitude = 10 ** math.floor(math.log10(raw))
    return next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)


def chart_svg(out, width=900, height=420):
    left, right, top, bottom = 70, 62, 52, 44
    n = len(out)
    months = out.index
    first_year, last = months[0].year, months[-1]
    t0 = pd.Period(f"{first_year}-01", freq="M")
    span_months = (last - t0).n + 1

    def x_of(period):
        return left + (width - left - right) * ((period - t0).n) / max(span_months - 1, 1)

    high = float(max(out["gold"].max(), out["fair"].max()))
    step = _nice_step(high)
    y_max = math.ceil(high * 1.05 / step) * step

    def y_left(v):
        return top + (height - top - bottom) * (1 - v / y_max)

    g_lo, g_hi = GAP_AXIS

    def y_right(g):
        g = min(max(g, g_lo), g_hi)
        return top + (height - top - bottom) * (g_hi - g) / (g_hi - g_lo)

    grid = ""
    v = 0.0
    while v <= y_max + 1e-9:
        grid += (f'<line x1="{left}" x2="{width - right}" y1="{y_left(v):.1f}" y2="{y_left(v):.1f}" stroke="#eee"/>'
                 f'<text x="{left - 8}" y="{y_left(v) + 4:.1f}" text-anchor="end" font-size="11" fill="#8a9199">'
                 f'${v:,.0f}</text>')
        v += step
    right_ticks = ""
    for g in np.arange(g_lo, g_hi + 1e-9, 0.10):
        right_ticks += (f'<text x="{width - right + 8}" y="{y_right(g) + 4:.1f}" font-size="11" '
                        f'fill="{GAP_COLOR}">{g * 100:+.0f}%</text>')
    zero = (f'<line x1="{left}" x2="{width - right}" y1="{y_right(0):.1f}" y2="{y_right(0):.1f}" '
            f'stroke="{GAP_COLOR}" stroke-width="0.8" stroke-dasharray="2,3" opacity="0.7"/>')
    years = ""
    for year in range(first_year, last.year + 1, 2):
        x = x_of(pd.Period(f"{year}-01", freq="M"))
        years += (f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{height - bottom}" y2="{height - bottom + 4}" stroke="#bbb"/>'
                  f'<text x="{x:.1f}" y="{height - bottom + 17}" text-anchor="middle" font-size="10" fill="#8a9199">'
                  f'{year}</text>')

    def poly(values, y_fn):
        return " ".join(f"{x_of(p):.1f},{y_fn(v):.1f}" for p, v in zip(months, values) if np.isfinite(v))

    gap_points = poly(out["gap"].to_numpy(), y_right)
    zero_y = y_right(0)
    gap_area = f"{x_of(months[0]):.1f},{zero_y:.1f} {gap_points} {x_of(months[-1]):.1f},{zero_y:.1f}"
    lines = (f'<polygon points="{gap_area}" fill="{GAP_COLOR}" opacity="0.10"/>'
             f'<polyline points="{gap_points}" fill="none" stroke="{GAP_COLOR}" stroke-width="1.2" opacity="0.85"/>'
             f'<polyline points="{poly(out["fair"].to_numpy(), y_left)}" fill="none" stroke="{FAIR_COLOR}" '
             'stroke-width="1.8" stroke-dasharray="6,3"/>'
             f'<polyline points="{poly(out["gold"].to_numpy(), y_left)}" fill="none" stroke="{GOLD_COLOR}" stroke-width="2.2"/>')
    end = out.iloc[-1]
    ex = x_of(months[-1])
    marks = (f'<circle cx="{ex:.1f}" cy="{y_left(end["gold"]):.1f}" r="3.5" fill="{GOLD_COLOR}"/>'
             f'<circle cx="{ex:.1f}" cy="{y_left(end["fair"]):.1f}" r="3.5" fill="{FAIR_COLOR}"/>'
             f'<circle cx="{ex:.1f}" cy="{y_right(end["gap"]):.1f}" r="3" fill="{GAP_COLOR}"/>')
    legend_items = [(GOLD_COLOR, "", "금 가격(좌, 달러/온스)"), (FAIR_COLOR, "6,3", "적정 가격(좌, 회귀식)"),
                    (GAP_COLOR, "", "적정 가격 대비 괴리(우, %)")]
    legend, lx = "", left
    for color, dash, label in legend_items:
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        legend += (f'<line x1="{lx}" x2="{lx + 22}" y1="36" y2="36" stroke="{color}" stroke-width="2.2"{dash_attr}/>'
                   f'<text x="{lx + 28}" y="40" font-size="11" fill="#3a4652">{html.escape(label)}</text>')
        lx += 28 + 11 * len(label) * 0.78 + 26
    title = (f'<text x="{left}" y="20" font-size="13" font-weight="600" fill="#1a1a1a">금 가격과 적정 가격 괴리 '
             f'{months[0].year}.{months[0].month:02d}~{last.year}.{last.month:02d} (월평균)</text>'
             f'<text x="{width - right + 54}" y="20" text-anchor="end" font-size="11" fill="#8a9199">'
             '좌: 달러/온스 · 우: %</text>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" xmlns="http://www.w3.org/2000/svg" '
            f'style="max-width:{width}px;font-family:-apple-system,\'Malgun Gothic\',sans-serif">'
            f'<rect width="{width}" height="{height}" fill="#fff"/>{title}{legend}{grid}{zero}{right_ticks}'
            f'<line x1="{left}" x2="{width - right}" y1="{height - bottom}" y2="{height - bottom}" stroke="#bbb"/>'
            f'{years}{lines}{marks}</svg>')


# ---------------------------------------------------------------------------
# 절
# ---------------------------------------------------------------------------
def render(out, fit, info, table, TD, TDR, TH, THR, note):
    """금 절 안에 들어갈 '금값 결정 요인과 평가' 블록(h4 제목 + 그림 + 표 + 주석)."""
    e = html.escape
    end, last = out.iloc[-1], out.index[-1]
    parts = ['<h4 style="font-size:14px;margin:18px 0 6px">금값 결정 요인과 평가 '
             '<span style="font-size:11px;color:#8a9199;font-weight:400">미국 CPI · 달러인덱스 · 10년물 금리로 본 적정 가격</span></h4>',
             f'<div style="border:1px solid #e5e5e5;border-radius:6px;padding:8px">{chart_svg(out)}</div>']
    state = ("적정 가격보다 비쌉니다" if end["gap"] > 0.05 else "적정 가격보다 쌉니다" if end["gap"] < -0.05
             else "적정 가격 부근입니다")
    past = out["gap"]
    higher_share = float((past >= end["gap"]).mean())
    carried = " (CPI 미발표 달 — 직전 발표치 사용)" if bool(end.get("cpi_carried")) else ""
    body = (f'<tr><td {TD}>{last.year}년 {last.month}월 금 가격(월평균)</td><td {TDR}>${end["gold"]:,.0f}</td></tr>'
            f'<tr><td {TD}>적정 가격{e(carried)}</td><td {TDR}>${end["fair"]:,.0f}</td></tr>'
            f'<tr><td {TD}>괴리</td><td {TDR}><b>{end["gap"] * 100:+.1f}%</b> · {state}</td></tr>'
            f'<tr><td {TD}>{out.index[0].year}년 이후 괴리 범위</td><td {TDR}>{past.min() * 100:+.0f}% '
            f'({past.idxmin()}) ~ {past.max() * 100:+.0f}% ({past.idxmax()})</td></tr>'
            f'<tr><td {TD}>지금보다 괴리가 컸던 달</td><td {TDR}>{higher_share:.0%} ({int((past >= end["gap"]).sum())}개월)</td></tr>'
            f'<tr><td {TD}>입력값 ({last.year}.{last.month:02d})</td><td {TDR}>CPI {end["cpi"]:.1f} · 달러인덱스 '
            f'{end["dxy"]:.1f} · 10년물 {end["y10"]:.2f}%</td></tr>')
    parts.append(table(f'<th {TH}>항목</th><th {THR}>값</th>', body, 460))
    c = fit["coef"]
    parts.append(note(
        f'식: ln(금값) = {MODEL["const"]} + {MODEL["cpi"]}·ln(미국 CPI) − {abs(MODEL["dxy"])}·ln(달러인덱스) − '
        f'{abs(MODEL["y10"])}·(미국 10년물 금리, %) · 분석기간 {MODEL["period"]}, R² {MODEL["r2"]}. '
        f'같은 자료({fit["first"]}~{fit["last"]}, {fit["n"]}개월)에서 이 식의 설명력은 R² {fit["r2"]:.3f}이고, 다시 맞춘 계수는 '
        f'{c[0]:.2f} · {c[1]:.2f} · {c[2]:.2f} · {c[3]:.3f}로 거의 같습니다. '
        '물가가 오르면 금값의 적정 수준이 올라가고, 달러가 강하거나 금리가 높으면 내려갑니다. '
        '<b>식은 2000~2026년 전체 자료로 맞춘 것이라 과거 구간의 괴리는 사후적으로 본 것</b>이고, 괴리가 크다고 곧 되돌아온다는 '
        '보장은 없습니다(2011년·2020년처럼 +40~50%까지 벌어진 적이 있습니다). 목표가나 매수·매도 의견이 아닙니다. '
        f'자료: 금 {e(info.get("gold_source", ""))}, 미국 CPI-U 계절조정({e(info.get("cpi_source", ""))}, 최신 '
        f'{e(info.get("cpi_last", ""))}), 달러인덱스(DX-Y.NYB)·10년물 금리(^TNX) 월평균.'))
    return parts

# -*- coding: utf-8 -*-
"""거시 경제 보고서 — 환율·금리·채권 등 거시 지표를 모아 보는 페이지.

2026-09-15 시작. 지금은 뼈대만 있고 지표는 하나씩 붙인다. 뼈대를 먼저 두는 이유는, 지표를
추가할 때마다 페이지 구조를 다시 정하지 않고 SECTIONS 에 함수 하나만 더하면 되게 하기 위해서다.

이 저장소의 규칙을 그대로 따른다.
- 받은 자료를 그대로 보여 준다. 예측하거나 매수·매도 의견을 내지 않는다.
- 조회에 실패한 지표는 그 사실과 사유를 적고, 나머지는 정상 표시한다.
- 값이 없으면 '—'로 두고 추측해 채우지 않는다.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from html import escape
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KST = timezone(timedelta(hours=9))
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "namyikim/predict_stock")
TITLE = "거시 경제"
OUT = ROOT / "docs" / "macro" / "index.html"

_STYLE = """<style>
html,body{overflow-x:hidden;overflow-x:clip}
body{margin:0;padding:24px 20px 48px;background:#fff;font-family:-apple-system,'Malgun Gothic',sans-serif;
     line-height:1.65;color:#1a1a1a;-webkit-font-smoothing:antialiased}
.wrap{max-width:980px;margin:0 auto}
a{color:#1a5490}
table{width:100%;border-collapse:collapse;font-size:13px;border:1px solid #e5e5e5}
th{background:#fafafa;font-size:11px;color:#6b7178;padding:8px 10px;text-align:left}
td{padding:7px 10px;border-top:1px solid #eee}
.num{text-align:right;white-space:nowrap}
.muted{color:#8a9199;font-size:11px}
.empty{border:1px dashed #d8dce1;border-radius:6px;padding:18px;color:#6b7178;font-size:13px;
       background:#fafbfc}
@media(max-width:640px){body{padding:16px 12px 32px}}
</style>"""

BACK_BUTTON = ('<div class="back-to-index" style="margin-bottom:10px">'
               '<a href="../" style="display:inline-block;font-size:12px;color:#1a5490;'
               'text-decoration:none;border:1px solid #cedff0;border-radius:5px;padding:5px 11px;'
               'background:#f0f6fc">← 보고서 목록</a></div>')


def planned_section(title, note, items):
    """아직 자료를 붙이지 않은 절. 무엇을 넣을지 적어 두어 다음 작업의 목록이 되게 한다."""
    rows = "".join(f"<li style='margin:3px 0'>{escape(item)}</li>" for item in items)
    return (f'<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;'
            f'border-bottom:1px solid #ddd">{escape(title)}</h3>'
            f'<div class="empty">{escape(note)}'
            f'<ul style="margin:8px 0 0;padding-left:18px;font-size:12px">{rows}</ul></div>')


def fx_decomposition_section(frame, info):
    """원/달러 결정 요인 표. 시차별로 무엇이 몇 %를 설명했는지."""
    sys.path.insert(0, str(ROOT / "tools"))
    from fx_variance import FX_FACTORS, FX_LABELS, HORIZONS, fit_and_decompose

    head = ('<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;'
            'border-bottom:1px solid #ddd">원/달러 결정 요인 '
            '<span style="font-weight:400;color:#8a9199;font-size:12px">'
            '&nbsp;VAR 분산분해 · 과거 자료의 분해이지 예측이 아닙니다</span></h3>')
    if frame is None or frame.empty:
        return head + ('<div class="empty">자료를 받지 못했습니다 — '
                       + escape(", ".join(f"{k}: {v}" for k, v in (info or {}).get("failed", {}).items())
                                or "원인 미상") + '</div>')
    table, diag = fit_and_decompose(frame)
    if table is None:
        return head + f'<div class="empty">{escape(diag.get("error", "계산할 수 없습니다."))}</div>'

    factors = [c for c in FX_FACTORS if c in diag["factors"]]
    rows = ""
    for horizon in HORIZONS:
        cells = ""
        for name in factors:
            share = table[horizon].get(name)
            # 자기 자신(원/달러)은 '다른 요인으로 설명되지 않은 몫'이라 회색으로 눌러 둔다.
            style = "color:#8a9199" if name == "usdkrw" else ""
            cells += (f'<td class="num" style="{style}">—</td>' if share is None or not np.isfinite(share)
                      else f'<td class="num" style="{style}">{share:.0%}</td>')
        rows += f'<tr><td>{horizon}개월</td>{cells}</tr>'
    header = "".join(f'<th class="num">{escape(FX_LABELS[c])}</th>' for c in factors)
    note = (f'{escape(diag["first"])}~{escape(diag["last"])} 월별 {diag["n"]}개 · 시차 {diag["lag"]}개월 · '
            f'{escape(diag["method"])}')
    return (head +
            '<div style="overflow-x:auto"><table style="min-width:620px">'
            f'<tr><th>시차</th>{header}</tr>{rows}</table></div>'
            f'<div class="muted" style="margin-top:6px">{note}</div>'
            '<div style="font-size:12px;color:#6b7178;line-height:1.7;margin-top:8px">'
            '각 행은 그 시차에서 원/달러 변동의 예측오차 분산을 100%로 놓고 나눈 것입니다. '
            '<b>원/달러</b> 칸은 다른 요인으로 설명되지 않은 몫이라, 그 값이 클수록 "밖에서 온 것으로는 '
            '설명이 잘 안 된다"는 뜻입니다. 일반화 분산분해는 합이 100%가 되지 않아 행별로 정규화했습니다. '
            '인과가 아니라 과거 자료에서의 동행·선행 관계입니다.</div>')


def dual_axis_chart(frame, left, right, left_label, right_label, title, start, note,
                    left_fmt="{:,.0f}", right_fmt="{:.2f}", left_color="#1a5490", right_color="#c8952a"):
    """단위가 다른 두 계열을 좌우 축에 놓고 겹친 꺾은선. 연도 눈금과 월간 변화율 상관을 함께 적는다.

    한쪽 계열이 늦게 시작하면(예: 일본 10년물 1989년) 그 선은 자료가 있는 구간만 그린다.
    x 축은 두 계열 중 먼저 시작하는 쪽에 맞춘다.
    """
    if frame is None or frame.empty or left not in frame or right not in frame:
        return ""
    data = frame[[left, right]]
    data = data[data.index >= pd.Timestamp(start)].dropna(how="all")
    if len(data.dropna()) < 24:
        return ""
    W, H, L, R, T, B = 900, 360, 66, 66, 34, 40
    PH = H - T - B
    xs = pd.Series(np.linspace(L, W - R, len(data)), index=data.index)

    def scale(series):
        series = series.dropna()
        lo, hi = float(series.min()), float(series.max())
        pad = (hi - lo) * 0.06 or 1.0
        lo, hi = lo - pad, hi + pad
        return (lambda v: T + PH * (1 - (v - lo) / (hi - lo))), lo, hi

    yl, llo, lhi = scale(data[left])
    yr, rlo, rhi = scale(data[right])

    def line(series, fn, color):
        # 결측 구간에서 선을 끊는다 — 이어 그리면 없는 자료를 있는 것처럼 보인다.
        segments, current = [], []
        for stamp, value in series.items():
            if pd.isna(value):
                if current:
                    segments.append(current); current = []
                continue
            current.append(f"{xs[stamp]:.1f},{fn(value):.1f}")
        if current:
            segments.append(current)
        return "".join(f'<polyline fill="none" stroke="{color}" stroke-width="1.8" points="{" ".join(seg)}"/>'
                       for seg in segments if len(seg) > 1)

    svg = [f'<svg viewBox="0 0 {W} {H}" width="100%" '
           f'style="max-width:{W}px;font-family:-apple-system,Malgun Gothic,sans-serif;font-size:11px">']
    years = sorted({d.year for d in data.index})
    step = 1 if len(years) <= 10 else (2 if len(years) <= 24 else 5)
    for year in years:
        first = data.index[data.index.year == year][0]
        x = xs[first]
        svg.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{T}" y2="{T + PH}" stroke="#eee"/>')
        # 간격에 걸리는 해 + 첫 해는 항상 적는다(그림이 어디서 시작하는지 바로 보이게).
        if year % step == 0 or year == years[0]:
            svg.append(f'<text x="{x:.1f}" y="{H - 14}" text-anchor="middle" fill="#8a9199">{year}</text>')
    for frac in (0.0, 0.5, 1.0):
        lv = llo + (lhi - llo) * frac
        rv = rlo + (rhi - rlo) * frac
        svg.append(f'<text x="{L - 8}" y="{yl(lv):.1f}" text-anchor="end" fill="{left_color}">'
                   f'{left_fmt.format(lv)}</text>')
        svg.append(f'<text x="{W - R + 8}" y="{yr(rv):.1f}" fill="{right_color}">{right_fmt.format(rv)}</text>')
    # 0 선(금리차처럼 부호가 뜻을 갖는 계열)
    if rlo < 0 < rhi:
        svg.append(f'<line x1="{L}" x2="{W - R}" y1="{yr(0):.1f}" y2="{yr(0):.1f}" '
                   f'stroke="{right_color}" stroke-dasharray="3,3" opacity="0.5"/>')
    svg.append(line(data[left], yl, left_color))
    svg.append(line(data[right], yr, right_color))
    svg.append(f'<text x="{L}" y="18" fill="#1a1a1a" font-weight="600">{escape(left_label)}(파랑, 왼쪽 축) · '
               f'{escape(right_label)}(주황, 오른쪽 축) · {years[0]}년~{years[-1]}년 월평균</text>')
    svg.append("</svg>")
    both = data.dropna()
    corr = float(both[left].diff().corr(both[right].diff())) if len(both) > 24 else float("nan")
    corr_text = f"월간 변화의 상관 <b>{corr:+.2f}</b> ({len(both)}개월)" if np.isfinite(corr) else ""
    return ('<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
            f'{escape(title)} <span style="font-weight:400;color:#8a9199;font-size:12px">'
            f'&nbsp;{years[0]}년부터 · 월평균</span></h3>'
            + "".join(svg) +
            f'<div style="font-size:12px;color:#6b7178;margin-top:6px">{corr_text}. {escape(note)}</div>')


def fx_overlay_chart(frame, start="2009-01-01"):
    return dual_axis_chart(frame, "usdkrw", "cny", "원/달러", "위안/달러", "원/달러와 위안/달러", start,
                           "단위가 다르므로 축을 따로 두었고, 폭이 아니라 방향을 보는 그림입니다. "
                           "위안은 관리변동환율이라 움직임이 작습니다.")


def real_rate_chart(frame, start="2001-01-01"):
    """원/달러와 한·미 실질금리차. 실질금리차가 벌어지면 원화가 강해진다는 관계를 보려는 것이다."""
    return dual_axis_chart(frame, "usdkrw", "real_rate_gap", "원/달러", "한·미 실질금리차(%p)",
                           "원/달러와 한·미 실질금리차", start,
                           "실질금리 = 10년물 명목금리 − 최근 12개월 소비자물가 상승률. 금리차 = 한국 − 미국. "
                           "점선은 실질금리차 0. 물가상승률은 기대인플레이션의 가장 단순한 대리이며, "
                           "다른 정의(기대치 조사·물가연동채)를 쓰면 값이 달라집니다.",
                           left_fmt="{:,.0f}", right_fmt="{:+.1f}")


def us_jp_chart(frame, start="1980-01-01"):
    """미·일 10년물 금리차와 엔/달러. 금리차가 벌어지면 엔이 약해진다는 관계를 보려는 것이다."""
    return dual_axis_chart(frame, "usdjpy", "rate_gap", "엔/달러", "미·일 10년물 금리차(%p)",
                           "미·일 금리차와 엔/달러", start,
                           "금리차 = 미국 10년 − 일본 10년. 일본 10년물 자료는 1989년부터라 그 전 구간은 "
                           "금리차 선이 없습니다. 점선은 금리차 0.",
                           left_fmt="{:,.0f}", right_fmt="{:+.1f}")


def us_market_chart(frame):
    """미국 하이일드 OAS·10년물 금리(왼쪽 %)와 나스닥(오른쪽)을 일별로 겹친다."""
    columns = ["high_yield_spread", "us10y", "nasdaq"]
    if frame is None or frame.empty or any(name not in frame for name in columns):
        return ""
    data = frame[columns].dropna()
    if len(data) < 24:
        return ""
    W, H, L, R, T, B = 900, 390, 66, 72, 48, 40
    PH = H - T - B
    first, last = data.index.min(), data.index.max()
    span = max((last - first).days, 1)
    xs = pd.Series([L + (W - L - R) * (stamp - first).days / span for stamp in data.index], index=data.index)

    def scale(series):
        lo, hi = float(series.min()), float(series.max())
        pad = (hi - lo) * .06 or 1.0
        lo, hi = lo - pad, hi + pad
        return (lambda value: T + PH * (1 - (value - lo) / (hi - lo))), lo, hi

    rate_values = pd.concat([data["high_yield_spread"], data["us10y"]])
    yr, rlo, rhi = scale(rate_values)
    yn, nlo, nhi = scale(data["nasdaq"])

    def polyline(series, fn, color, width="1.7"):
        points = " ".join(f"{xs[stamp]:.1f},{fn(value):.1f}" for stamp, value in series.items())
        return f'<polyline fill="none" stroke="{color}" stroke-width="{width}" points="{points}"/>'

    colors = {"high_yield_spread": "#b33a3a", "us10y": "#1a5490", "nasdaq": "#2a8b57"}
    svg = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;'
           'font-family:-apple-system,Malgun Gothic,sans-serif;font-size:11px">']
    years = list(range(first.year, last.year + 1))
    step = 1 if len(years) <= 10 else (2 if len(years) <= 24 else 5)
    for year in years:
        stamp = max(first, pd.Timestamp(year=year, month=1, day=1))
        if stamp > last:
            continue
        x = L + (W - L - R) * (stamp - first).days / span
        svg.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{T}" y2="{T + PH}" stroke="#eee"/>')
        if year == first.year or year % step == 0:
            svg.append(f'<text x="{x:.1f}" y="{H - 14}" text-anchor="middle" fill="#8a9199">{year}</text>')
    for frac in (0.0, .5, 1.0):
        rv = rlo + (rhi - rlo) * frac
        nv = nlo + (nhi - nlo) * frac
        svg.append(f'<text x="{L - 8}" y="{yr(rv):.1f}" text-anchor="end" fill="#48525c">{rv:.1f}</text>')
        svg.append(f'<text x="{W - R + 8}" y="{yn(nv):.1f}" fill="{colors["nasdaq"]}">{nv:,.0f}</text>')
    svg.append(polyline(data["high_yield_spread"], yr, colors["high_yield_spread"]))
    svg.append(polyline(data["us10y"], yr, colors["us10y"]))
    svg.append(polyline(data["nasdaq"], yn, colors["nasdaq"]))
    svg.append(f'<text x="{L}" y="18" fill="{colors["high_yield_spread"]}" font-weight="600">'
               '하이일드 채권 스프레드</text>')
    svg.append(f'<text x="{L + 180}" y="18" fill="{colors["us10y"]}" font-weight="600">'
               '미국 국채 10년</text>')
    svg.append(f'<text x="{L + 310}" y="18" fill="{colors["nasdaq"]}" font-weight="600">'
               '나스닥 종합지수</text>')
    svg.append(f'<text x="{L}" y="35" fill="#6b7178">금리·스프레드(%, 왼쪽 축) · '
               '나스닥(오른쪽 축)</text>')
    svg.append("</svg>")
    return ('<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
            '미국 신용위험·국채금리와 나스닥 '
            f'<span style="font-weight:400;color:#8a9199;font-size:12px">&nbsp;{first.year}년~{last.year}년 · 일별</span></h3>'
            + "".join(svg) +
            '<div style="font-size:12px;color:#6b7178;margin-top:6px">'
            '하이일드 채권 스프레드는 ICE BofA 미국 하이일드 지수의 옵션조정 스프레드(OAS)입니다. '
            '세 계열이 모두 관측되는 날짜만 그렸으며 결측값을 보간하지 않았습니다. '
            'FRED는 ICE 라이선스에 따라 2026년 4월부터 이 스프레드의 최근 3년만 제공하므로, '
            '현재 공식 공개 경로에서 받을 수 있는 최대 기간을 표시합니다. '
            '자료: FRED(BAMLH0A0HYM2, DGS10, NASDAQCOM).</div>')


# 절 목록. 지표를 붙일 때 여기에 함수를 더하면 페이지 구조는 건드리지 않아도 된다.
SECTIONS = (
    ("환율", "원/달러 결정 요인 표는 위에 있습니다. 아래는 앞으로 더할 것입니다.",
     ("원/달러 종가와 이동평균", "엔/달러·달러지수와 함께 본 상대 강도", "최근 변동성")),
    ("금리", "한국·미국 정책금리와 시장금리를 나란히 놓습니다.",
     ("한국은행 기준금리", "미국 연방기금금리 목표", "한·미 금리차")),
    ("채권", "국채 수익률 곡선과 장단기 금리차를 봅니다.",
     ("한국 3년·10년 국고채", "미국 2년·10년 국채", "장단기 금리차(경기 신호로 읽히는 값)")),
    ("경기 지표", "이미 이 저장소가 받고 있는 지표를 한곳에 모읍니다.",
     ("선행지수 순환변동치", "G20 경기선행지수", "뉴스심리지수", "반도체 수출")),
)


def build_page(now=None, fx_frame=None, fx_info=None, us_jp_frame=None, us_jp_info=None,
               us_market_frame=None, us_market_info=None):
    now = now or datetime.now(KST)
    body = ""
    if fx_frame is not None:
        body += fx_decomposition_section(fx_frame, fx_info)
        body += fx_overlay_chart(fx_frame)
        body += real_rate_chart(fx_frame) or (
            '<div class="empty">한·미 실질금리차를 만들지 못했습니다 — '
            + escape(str((fx_info or {}).get("failed", {}).get("real_rate_gap", "자료 부족"))) + '</div>')
    if us_jp_frame is not None:
        body += us_jp_chart(us_jp_frame)
    elif us_jp_info and us_jp_info.get("failed"):
        body += ('<div class="empty">미·일 금리차 자료를 받지 못했습니다 — '
                 + escape("; ".join(f"{k}: {v}" for k, v in us_jp_info["failed"].items())) + '</div>')
    if us_market_frame is not None:
        body += us_market_chart(us_market_frame)
    elif us_market_info and us_market_info.get("failed"):
        body += ('<div class="empty">미국 금융시장 자료를 받지 못했습니다 — '
                 + escape("; ".join(f"{k}: {v}" for k, v in us_market_info["failed"].items())) + '</div>')
    body += "".join(planned_section(title, note, items) for title, note, items in SECTIONS)
    return (
        '<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(TITLE)}</title>{_STYLE}</head><body>'
        '<div class="wrap">'
        + BACK_BUTTON +
        '<div style="border-bottom:3px solid #1a1a1a;padding-bottom:11px;margin-bottom:18px">'
        '<div style="font-size:11px;letter-spacing:2px;color:#8a9199">MACRO ECONOMY</div>'
        f'<h2 style="margin:6px 0 5px;font-size:27px">{escape(TITLE)}</h2>'
        f'<div style="font-size:12px;color:#8a9199">{now:%Y-%m-%d %H:%M} KST 기준 · '
        '환율·금리·채권 등 거시 지표를 모아 봅니다. 예측이 아니라 자료 정리입니다.</div></div>'
        '<div style="background:#fdf8ec;border-left:4px solid #c8952a;padding:11px 15px;'
        'border-radius:0 5px 5px 0;font-size:12px;line-height:1.7;margin-bottom:6px">'
        '<b>준비 중입니다.</b> 아래는 앞으로 넣을 항목이고, 자료가 붙는 대로 하나씩 채웁니다. '
        '값이 없는 절은 비어 있는 그대로 두고 추측해 채우지 않습니다.</div>'
        + body +
        '<div style="margin-top:24px;padding-top:14px;border-top:1px solid #e5e5e5;'
        'font-size:12px;color:#8a9199">'
        f'생성 {now:%Y-%m-%d %H:%M} KST · '
        f'<a href="https://github.com/{GITHUB_REPO}">저장소</a><br>'
        '연구·교육용입니다. 투자 자문이 아닙니다.</div>'
        '</div></body></html>')


FX_CACHE = ROOT / "macro_history" / "fx_inputs.csv"


def load_fx(fetch=True):
    """(자료, 진단). 받은 것은 보관본에 누적해 다음 실행이 실패해도 표가 비지 않게 한다."""
    # 자료원 모듈끼리는 서로 참조하지 않는다(그 규칙을 테스트가 지킨다). 야후와 ECOS 를 여기서
    # 엮는다 — 결합은 도구의 몫이다.
    from data_sources.fx_inputs import build_fx_inputs
    from data_sources.ecos import (fetch_korea_rate_monthly, fetch_current_account_monthly,
                                   fetch_korea_cpi_monthly)
    from data_sources.fred import fetch_fred, US_CPI
    try:
        frame, info = build_fx_inputs(fetch=fetch, cache_path=FX_CACHE,
                                      korea_rate_fn=fetch_korea_rate_monthly,
                                      current_account_fn=fetch_current_account_monthly,
                                      korea_cpi_fn=fetch_korea_cpi_monthly,
                                      us_cpi_fn=lambda: fetch_fred(US_CPI))
    except Exception as exc:
        return None, {"failed": {"전체": f"{type(exc).__name__}: {exc}"[:160]}}
    if len(frame):
        FX_CACHE.parent.mkdir(parents=True, exist_ok=True)
        frame.reset_index().to_csv(FX_CACHE, index=False)
    return frame, info


US_JP_CACHE = ROOT / "macro_history" / "us_jp_rates.csv"
US_MARKET_CACHE = ROOT / "macro_history" / "us_market_daily.csv"


def load_us_jp(fetch=True):
    from data_sources.fred import build_us_jp_inputs
    try:
        frame, info = build_us_jp_inputs(fetch=fetch, cache_path=US_JP_CACHE)
    except Exception as exc:
        return None, {"failed": {"전체": f"{type(exc).__name__}: {exc}"[:160]}}
    if len(frame):
        US_JP_CACHE.parent.mkdir(parents=True, exist_ok=True)
        frame.reset_index().to_csv(US_JP_CACHE, index=False)
    return frame, info


def load_us_market(fetch=True):
    from data_sources.fred import build_us_market_inputs
    try:
        frame, info = build_us_market_inputs(fetch=fetch, cache_path=US_MARKET_CACHE)
    except Exception as exc:
        return None, {"failed": {"전체": f"{type(exc).__name__}: {exc}"[:160]}}
    if len(frame):
        US_MARKET_CACHE.parent.mkdir(parents=True, exist_ok=True)
        frame.reset_index().to_csv(US_MARKET_CACHE, index=False)
    return frame, info


def main():
    parser = argparse.ArgumentParser(description="거시 경제 보고서를 만든다")
    parser.add_argument("--write", action="store_true", help="docs/macro/index.html 에 저장")
    parser.add_argument("--no-fetch", action="store_true", help="보관본만 쓰고 조회하지 않는다")
    args = parser.parse_args()
    frame, info = load_fx(fetch=not args.no_fetch)
    if info.get("failed"):
        for name, reason in info["failed"].items():
            print(f"  ⚠️ {name}: {reason}", flush=True)
    if frame is not None and len(frame):
        print(f"  환율 자료: {info.get('source')} · {info.get('first')}~{info.get('last')} "
              f"({info.get('rows')}개월, {len(info.get('columns', []))}계열)", flush=True)
    us_jp, us_jp_info = load_us_jp(fetch=not args.no_fetch)
    if us_jp_info.get("failed"):
        for name, reason in us_jp_info["failed"].items():
            print(f"  ⚠️ 미·일 {name}: {reason}", flush=True)
    if us_jp is not None and len(us_jp):
        print(f"  미·일 자료: {us_jp_info.get('source')} · {us_jp_info.get('first')}~{us_jp_info.get('last')} "
              f"({us_jp_info.get('rows')}개월)", flush=True)
    us_market, us_market_info = load_us_market(fetch=not args.no_fetch)
    if us_market_info.get("failed"):
        for name, reason in us_market_info["failed"].items():
            print(f"  ⚠️ 미국 금융시장 {name}: {reason}", flush=True)
    if us_market is not None and len(us_market):
        print(f"  미국 금융시장 자료: {us_market_info.get('source')} · "
              f"{us_market_info.get('first')}~{us_market_info.get('last')} "
              f"({us_market_info.get('rows')}일)", flush=True)
    page = build_page(fx_frame=frame, fx_info=info, us_jp_frame=us_jp, us_jp_info=us_jp_info,
                      us_market_frame=us_market, us_market_info=us_market_info)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(page, encoding="utf-8")
        print(f"저장: {OUT.relative_to(ROOT)} ({len(page):,} bytes)")
    else:
        print(page)


if __name__ == "__main__":
    main()

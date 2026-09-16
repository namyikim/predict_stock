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


# ---------------------------------------------------------------------------
# 그림 아래 해석(2026-09-16 요청)
# ---------------------------------------------------------------------------
# 사용자가 준 전문가 코멘트에서 '판단 틀'만 가져와 지금 자료에 적용한다. 그림이 바뀌면 결론도 바뀐다 —
# 코멘트를 그대로 인용해 두면 시간이 지나 틀린 말이 남는다(2026-09-16 지적). 원문은 작성 시점 기록으로만
# 접어 둔다. 규칙은 아래 코드에 있고 같은 자료면 같은 문장이 나온다. 예측이나 매매 판단이 아니다.
FX_PAIR_EXPERT_NOTES = (
    "원/달러와 위안/달러는 거의 같은 방향으로 움직입니다. 우리 수출에서 중국이 차지하는 비중이 22%로 가장 큽니다.",
    "중장기적으로도 같은 방향으로 움직일 수밖에 없습니다.",
    "중국의 경상수지 흑자는 너무 커졌고, 미국의 경상수지 적자는 반대로 너무 커졌습니다.",
    "이 불균형을 해소하려면 달러 가치는 떨어지고 위안화 가치는 올라야 합니다. 중국은 위안화 가치 상승을 유도할 것입니다.",
)
REAL_RATE_EXPERT_NOTES = (
    "금리차도 원/달러 환율에 많은 영향을 줍니다. 명목금리보다 실질금리가 원/달러 환율에 더 영향을 미칩니다.",
    "실질금리는 10년 국채수익률에서 소비자물가 상승률을 뺀 것입니다.",
    "명목금리는 미국이 더 높습니다. 미국 10년 국채수익률은 5%, 우리나라는 4.5% 안팎입니다.",
    "명목금리는 미국이 높지만 우리 물가상승률이 미국보다 낮습니다. 실질금리는 우리가 더 높기 때문에 "
    "원화 가치는 더 오를 수 있습니다.",
)
US_JP_EXPERT_NOTES = (
    "엔화 가치가 너무 저평가되어 있습니다.",
    "엔/달러 환율을 결정하는 가장 중요한 요소는 미국과 일본의 10년 국채수익률 차이인데, 이 차이가 많이 "
    "축소되고 있습니다.",
    "이런 점을 볼 때 엔화 가치가 오를 수 있고, 우리나라 원화 가치도 오를 수 있습니다.",
)


def _comove_phrase(corr):
    return ("강하게 같이 움직였습니다" if corr >= .6 else "뚜렷하게 같이 움직였습니다" if corr >= .3 else
            "약하게만 같이 움직였습니다" if corr >= .1 else "거의 따로 움직였습니다")


def _change_over(series, months=12):
    """months 개월 전 대비 변화율. 자료가 모자라면 None."""
    series = series.dropna()
    return float(series.iloc[-1] / series.iloc[-1 - months] - 1) if len(series) > months else None


def _commentary_box(facts, view, original_notes, basis, reading):
    """그림 아래 해석 상자.

    facts: 자료로 다시 계산한 사실(HTML). view: 전문가 판단 틀을 지금 자료에 적용한 결론(HTML).
    original_notes: 판단 틀을 가져온 코멘트 원문(작성 시점 기록이라 접어 둔다).
    """
    def items(rows, as_html=True):
        return "".join(f'<li style="margin:4px 0">{row if as_html else escape(row)}</li>' for row in rows)

    return ('<div style="border:1px solid #e5e5e5;border-radius:6px;padding:12px 14px;margin-top:10px;'
            'background:#fbfcfd;font-size:13px;line-height:1.7">'
            '<div style="font-weight:700">해석 — 자료로 본 지금 '
            f'<span style="font-weight:400;color:#8a9199;font-size:12px">{basis:%Y-%m} 기준 · '
            '그림을 만들 때마다 다시 계산</span></div>'
            f'<ul style="margin:4px 0 0;padding-left:18px">{items(facts)}</ul>'
            '<div style="font-weight:700;margin-top:10px">전문가 시각 '
            '<span style="font-weight:400;color:#8a9199;font-size:12px">전문가 코멘트의 판단 틀을 지금 자료에 적용한 것 · '
            '자료가 바뀌면 결론도 바뀝니다 · 예측이나 매매 판단이 아닙니다</span></div>'
            f'<ul style="margin:4px 0 0;padding-left:18px">{items(view)}</ul>'
            '<details style="margin-top:8px"><summary style="font-size:12px;color:#6b7178;cursor:pointer">'
            '판단 틀을 가져온 전문가 코멘트 원문 (2026-09 작성 · 수치는 작성 시점 기준)</summary>'
            '<ul style="margin:4px 0 0;padding-left:18px;font-size:12px;color:#6b7178">'
            f'{items(original_notes, as_html=False)}</ul></details>'
            f'<div style="font-size:11px;color:#8a9199;margin-top:8px">{reading} 과거 자료의 관계를 정해진 규칙으로 '
            '읽은 것이며 인과나 예측이 아닙니다.</div>'
            '</div>')


FX_PAIR_READING = ('읽는 법: 이 그림에서 선이 내려가면 그 통화가 달러보다 강해진 것입니다(위안/달러가 내려가면 위안화 강세, '
                   '원/달러가 내려가면 원화 강세). 위안화는 관리변동환율이라 움직임 자체가 작습니다.')


def fx_pair_insight(frame, start="2009-01-01", recent_months=36, cross_months=60):
    """원/달러·위안/달러 그림의 해석 재료. 없으면 None.

    판단 틀(2026-09 코멘트): 중국이 최대 수출 상대국이라 원화와 위안화는 같이 움직인다. 미·중 경상수지
    불균형이 줄려면 달러 약세·위안 강세여야 하고, 위안화가 강해지면 원화도 강해질 수 있다.
    이 틀을 지금의 동행 강도·위안화 12개월 방향·원/위안 교차환율 위치에 적용한다.
    반환: {"facts", "view", "basis", "summary"(한 문장), "won"(원화 함의 +1 강세·−1 약세·0 없음)}.
    """
    if frame is None or len(frame) == 0 or "usdkrw" not in frame or "cny" not in frame:
        return None
    data = frame[["usdkrw", "cny"]]
    data = data[data.index >= pd.Timestamp(start)].dropna()
    data = data[(data > 0).all(axis=1)]
    if len(data) < recent_months + 1:
        return None
    change = np.log(data).diff().dropna()
    corr_all = float(change["usdkrw"].corr(change["cny"]))
    recent = change.iloc[-recent_months:]
    corr_recent = float(recent["usdkrw"].corr(recent["cny"]))
    beta = float(np.cov(change["usdkrw"], change["cny"])[0, 1] / change["cny"].var())
    ratio = float(change["usdkrw"].std() / change["cny"].std())
    krw_12, cny_12 = _change_over(data["usdkrw"]), _change_over(data["cny"])
    cross = (data["usdkrw"] / data["cny"]).iloc[-cross_months:]
    z = (float((cross.iloc[-1] - cross.mean()) / cross.std())
         if len(cross) >= 24 and float(cross.std()) > 0 else None)

    facts = [
        f'2009년 이후 두 환율의 월간 변화는 {_comove_phrase(corr_all)}(상관 <b>{corr_all:+.2f}</b>, '
        f'{len(change)}개월). 최근 {recent_months // 12}년은 {_comove_phrase(corr_recent)}'
        f'(상관 <b>{corr_recent:+.2f}</b>).',
        f'위안/달러가 1% 움직일 때 원/달러는 {"같은" if beta >= 0 else "반대"} 방향으로 평균 '
        f'<b>{abs(beta):.1f}%</b> 따라 움직였습니다. 월간 변동폭은 원/달러가 위안/달러의 <b>{ratio:.1f}배</b>입니다.',
    ]
    if krw_12 is not None and cny_12 is not None:
        facts.append(f'최근 12개월 원/달러 <b>{krw_12:+.1%}</b>, 위안/달러 <b>{cny_12:+.1%}</b>.')
    if z is not None:
        facts.append(f'원/위안 환율(원/달러 ÷ 위안/달러)은 <b>{cross.iloc[-1]:,.1f}원</b>으로 최근 5년 평균 '
                     f'{cross.mean():,.1f}원 대비 {z:+.1f}σ입니다.')

    together = corr_recent >= .3
    view = [("원/달러와 위안/달러는 같은 방향으로 움직이고 있습니다. 중국이 한국의 최대 수출 상대국이라 "
             "위안화 흐름이 원화로 옮겨 오는 구조입니다.") if together else
            "최근 3년은 원/달러와 위안/달러의 동행이 약해, 위안화 흐름만으로 원화를 읽기는 어렵습니다."]
    if cny_12 is not None and cny_12 <= -.01:
        view.append(f'위안화 가치가 최근 1년 {abs(cny_12):.1%} 올랐습니다. 미국의 경상적자와 중국의 경상흑자가 '
                    '커진 불균형을 줄이는 방향(달러 약세·위안 강세)과 맞는 흐름입니다.')
        view.append("이 흐름이 이어진다면 두 통화의 동행으로 볼 때 원화 가치도 함께 오를 수 있습니다." if together
                    else "다만 원화와의 동행이 약해, 원화가 함께 강해진다고 보기는 어렵습니다.")
    elif cny_12 is not None and cny_12 >= .01:
        view.append(f'위안화 가치가 최근 1년 {cny_12:.1%} 떨어졌습니다. 불균형 해소 방향(위안 강세)과 반대로 가고 '
                    '있고, ' + ("동행이 유지되면 원화에도 약세 압력입니다." if together
                              else "원화와의 동행은 약해 원화에 주는 영향은 제한적입니다."))
    else:
        view.append("위안화는 최근 1년 뚜렷한 방향이 없어, 위안화 쪽에서 오는 원화 방향성도 약합니다.")
    if z is not None and z > 1:
        view.append("원화가 위안화보다 평소보다 약한 상태라, 동행 관계로 보면 원화가 따라잡을(강세) 여지가 있습니다.")
    elif z is not None and z < -1:
        view.append("원화가 위안화보다 평소보다 강한 상태라, 원화의 추가 강세 여지는 상대적으로 작습니다.")
    # 페이지 맨 위 요약에 올릴 한 문장과 원화 함의
    if not together:
        summary, won = "원화와 위안화의 동행이 약해져 위안화 흐름만으로 원화를 읽기는 어렵습니다.", 0
        short = "원화와 위안화의 동행이 약해 위안화로 원화를 읽기 어렵고"
    elif cny_12 is not None and cny_12 <= -.01:
        summary, won = (f"위안화가 최근 1년 {abs(cny_12):.1%} 올랐고 원화는 위안화를 따라 움직이는 편이라, "
                        "원화도 강세 쪽으로 끌리는 국면입니다."), 1
        short = f"위안화가 1년간 {abs(cny_12):.1%} 올라 원화도 강세 쪽으로 끌리고"
    elif cny_12 is not None and cny_12 >= .01:
        summary, won = (f"위안화가 최근 1년 {cny_12:.1%} 떨어졌고 원화는 위안화를 따라 움직이는 편이라, "
                        "원화에도 약세 압력이 있는 국면입니다."), -1
        short = f"위안화가 1년간 {cny_12:.1%} 내려 원화에도 약세 압력이 있고"
    else:
        summary, won = "위안화가 최근 1년 뚜렷한 방향이 없어 위안화 쪽에서 오는 원화 방향성은 약합니다.", 0
        short = "위안화는 뚜렷한 방향이 없고"
    return {"facts": facts, "view": view, "basis": data.index[-1], "summary": summary, "short": short, "won": won}


def fx_pair_commentary(frame, start="2009-01-01"):
    insight = fx_pair_insight(frame, start)
    return _commentary_box(insight["facts"], insight["view"], FX_PAIR_EXPERT_NOTES, insight["basis"],
                           FX_PAIR_READING) if insight else ""


def fx_overlay_chart(frame, start="2009-01-01"):
    chart = dual_axis_chart(frame, "usdkrw", "cny", "원/달러", "위안/달러", "원/달러와 위안/달러", start,
                            "단위가 다르므로 축을 따로 두었고, 폭이 아니라 방향을 보는 그림입니다. "
                            "위안은 관리변동환율이라 움직임이 작습니다.")
    return chart + fx_pair_commentary(frame, start) if chart else ""


REAL_RATE_READING = ('읽는 법: 이 그림에서 주황 선(실질금리차)이 올라가면 한국의 실질금리가 미국보다 상대적으로 높아진 것이고, '
                     '파랑 선(원/달러)이 내려가면 원화 강세입니다.')


def real_rate_insight(frame, info=None, start="2001-01-01", stale_months=3):
    """원/달러·한·미 실질금리차 그림의 해석 재료. 없으면 None.

    판단 틀(2026-09 코멘트): 원/달러에는 명목보다 실질금리 차이가 더 영향을 준다. 명목금리가 미국이 높아도
    한국 물가상승률이 더 낮으면 실질금리는 한국이 높고, 그러면 원화 가치가 오를 수 있다.
    이 틀을 실질·명목 금리차의 연결 강도, 지금 명목금리, 최신 실질금리차(끊겼으면 판단 보류)에 적용한다.
    """
    columns = ["usdkrw", "rate_gap", "real_rate_gap"]
    if frame is None or len(frame) == 0 or any(name not in frame for name in columns):
        return None
    data = frame[frame.index >= pd.Timestamp(start)]
    both = data[columns].dropna()
    both = both[both["usdkrw"] > 0]
    if len(both) < 36:
        return None
    fx_change = np.log(both["usdkrw"]).diff()
    corr_real = float(fx_change.corr(both["real_rate_gap"].diff()))
    corr_nom = float(fx_change.corr(both["rate_gap"].diff()))
    facts = [f'{both.index[0]:%Y-%m}~{both.index[-1]:%Y-%m} 월간 변화로 보면 원/달러와 실질금리차의 상관은 '
             f'<b>{corr_real:+.2f}</b>, 같은 기간 명목금리차와는 <b>{corr_nom:+.2f}</b>입니다(음(−)이면 한국 금리가 '
             '상대적으로 오를 때 원/달러가 내리는 관계).']

    gap = None
    if {"kr10y", "us10y"}.issubset(data.columns):
        nominal = data[["kr10y", "us10y"]].dropna()
        if len(nominal):
            kr, us = (float(value) for value in nominal.iloc[-1])
            gap = kr - us
            facts.append(f'{nominal.index[-1]:%Y-%m} 명목 10년물은 한국 <b>{kr:.2f}%</b>, 미국 <b>{us:.2f}%</b>'
                         f'(명목금리차 <b>{gap:+.2f}%p</b>).')

    real = data["real_rate_gap"].dropna()
    real_value, real_month = float(real.iloc[-1]), real.index[-1]
    fx_last = data["usdkrw"].dropna().index[-1]
    lag = (fx_last.to_period("M") - real_month.to_period("M")).n
    stale = lag > stale_months
    text = (f'실질금리차(10년물 − 소비자물가 상승률, 한국 − 미국) 최신값은 <b>{real_value:+.2f}%p</b>'
            f'({real_month:%Y-%m})입니다.')
    if stale:
        reason = ((info or {}).get("notes") or {}).get("korea_cpi")
        text += (f' 그 뒤 {lag}개월은 한국 물가 자료가 끊겨 계산하지 못했습니다'
                 + (f'({escape(str(reason))})' if reason else '') + '.')
    facts.append(text)
    real_12 = (real_value - float(real.iloc[-13])) if not stale and len(real) >= 13 else None

    if corr_real < 0 and abs(corr_real) > abs(corr_nom) + .02:
        view = ["원/달러에는 명목금리보다 물가를 뺀 실질금리 차이가 더 크게 작용해 왔습니다"
                + (" — 다만 관계 자체는 약한 편입니다." if abs(corr_real) < .3 else ".")]
    elif abs(corr_nom) > abs(corr_real) + .02:
        view = ["이 기간 원/달러에는 실질금리보다 명목금리 차이가 더 뚜렷하게 연결됐습니다. 실질금리만으로 "
                "원화를 읽기는 어렵습니다."]
    else:
        view = ["원/달러와 실질·명목 금리차의 연결 강도는 비슷합니다."]
    if gap is not None:
        view.append(f'명목금리는 {"미국" if gap < 0 else "한국"}이 {abs(gap):.2f}%p 높습니다 — '
                    + ("명목만 보면 원화에 불리한 조건입니다." if gap < 0 else "명목으로도 원화에 우호적인 조건입니다."))
    if stale:
        text = "최신 물가 자료가 없어 지금 어느 나라의 실질금리가 더 높은지는 판단하지 못합니다."
        if gap is not None and gap < 0:
            text += (f' 한국 물가상승률이 미국보다 {abs(gap):.2f}%p 넘게 낮다면 한국의 실질금리가 더 높아지고, '
                     '그 경우 원화 가치가 오를 수 있는 조건이 됩니다.')
        elif gap is not None:
            text += (f' 명목금리가 한국이 높으므로, 한국 물가상승률이 미국보다 {gap:.2f}%p 넘게 높지 않다면 '
                     '실질금리도 한국이 높아 원화에 우호적입니다.')
        view.append(text)
    elif real_value > 0:
        view.append(f'물가를 빼면 한국의 실질금리가 {real_value:.2f}%p 높습니다. '
                    + ("명목금리는 미국이 높지만 " if gap is not None and gap < 0 else "")
                    + "실질 기준으로는 원화 가치가 오를 수 있는 조건입니다.")
    else:
        view.append(f'물가를 빼도 미국의 실질금리가 {abs(real_value):.2f}%p 높아, 금리 면에서는 원화에 부담입니다.')
    if real_12 is not None and abs(real_12) >= .3:
        view.append(f'최근 1년 실질금리차가 {"한국" if real_12 > 0 else "미국"} 쪽으로 {abs(real_12):.2f}%p 움직여 '
                    f'원화에 {"우호적인" if real_12 > 0 else "불리한"} 방향입니다.')
    if stale:
        nominal = f"(명목은 미국이 {abs(gap):.2f}%p 높음)" if gap is not None and gap < 0 else ""
        summary, won = ("최신 물가 자료가 없어 지금 어느 나라의 실질금리가 더 높은지는 판단하지 못합니다"
                        + (f"(명목금리는 미국이 {abs(gap):.2f}%p 높음)." if gap is not None and gap < 0 else "."), 0)
        short = f"실질금리 우위는 물가 자료가 끊겨 판단 보류{nominal}이며"
    elif real_value > 0:
        summary, won = (f"물가를 뺀 실질금리는 한국이 {real_value:.2f}%p 높아, 실질 기준으로는 원화 가치가 오를 수 "
                        "있는 조건입니다."), 1
        short = f"실질금리는 한국이 {real_value:.2f}%p 높아 원화에 우호적이며"
    else:
        summary, won = f"물가를 빼도 미국의 실질금리가 {abs(real_value):.2f}%p 높아, 금리 면에서는 원화에 부담입니다.", -1
        short = f"실질금리는 미국이 {abs(real_value):.2f}%p 높아 원화에 부담이며"
    return {"facts": facts, "view": view, "basis": fx_last, "summary": summary, "short": short, "won": won}


def real_rate_commentary(frame, info=None, start="2001-01-01"):
    insight = real_rate_insight(frame, info, start)
    return _commentary_box(insight["facts"], insight["view"], REAL_RATE_EXPERT_NOTES, insight["basis"],
                           REAL_RATE_READING) if insight else ""


def real_rate_chart(frame, start="2001-01-01", info=None):
    """원/달러와 한·미 실질금리차. 실질금리차가 벌어지면 원화가 강해진다는 관계를 보려는 것이다."""
    chart = dual_axis_chart(frame, "usdkrw", "real_rate_gap", "원/달러", "한·미 실질금리차(%p)",
                            "원/달러와 한·미 실질금리차", start,
                            "실질금리 = 10년물 명목금리 − 최근 12개월 소비자물가 상승률. 금리차 = 한국 − 미국. "
                            "점선은 실질금리차 0. 물가상승률은 기대인플레이션의 가장 단순한 대리이며, "
                            "다른 정의(기대치 조사·물가연동채)를 쓰면 값이 달라집니다.",
                            left_fmt="{:,.0f}", right_fmt="{:+.1f}")
    return chart + real_rate_commentary(frame, info, start) if chart else ""


US_JP_READING = ('읽는 법: 이 그림에서 주황 선(미·일 금리차)이 내려가면 달러를 들고 있을 때의 금리 이점이 줄어든 것이고, '
                 '파랑 선(엔/달러)이 내려가면 엔화 강세입니다.')


def us_jp_insight(frame, fx=None, start="1989-01-01", fit_months=120):
    """미·일 금리차·엔/달러 그림의 해석 재료. 없으면 None.

    판단 틀(2026-09 코멘트): 엔/달러를 결정하는 가장 중요한 요소는 미·일 10년물 금리차다. 금리차가 줄면 엔화
    가치가 오를 수 있고, 금리차에 비해 엔화가 약하면 저평가다. 엔화가 오르면 원화도 오를 수 있다.
    이 틀을 금리차의 방향, '금리차에 맞는 엔/달러'(최근 10년 수준 관계, 참고치), 원화와의 동행에 적용한다.
    """
    if frame is None or len(frame) == 0 or "usdjpy" not in frame or "rate_gap" not in frame:
        return None
    data = frame[["usdjpy", "rate_gap"]]
    data = data[data.index >= pd.Timestamp(start)].dropna()
    data = data[data["usdjpy"] > 0]
    if len(data) < 48:
        return None
    corr = float(np.log(data["usdjpy"]).diff().corr(data["rate_gap"].diff()))
    last = data.index[-1]
    gap, yen = float(data["rate_gap"].iloc[-1]), float(data["usdjpy"].iloc[-1])
    recent = data["rate_gap"].iloc[-36:]
    peak, peak_month = float(recent.max()), recent.idxmax()
    change_12 = gap - float(data["rate_gap"].iloc[-13])
    shrinking = change_12 <= -.15 or (peak - gap) >= .5
    widening = not shrinking and change_12 >= .15
    yen_12 = _change_over(data["usdjpy"])
    rank = float((data["usdjpy"].iloc[-120:] < yen).mean())
    facts = [
        f'{data.index[0]:%Y-%m}~{last:%Y-%m} 월간 변화로 보면 미·일 금리차와 엔/달러의 상관은 <b>{corr:+.2f}</b>입니다'
        f'(양(+)이면 금리차가 벌어질 때 엔화가 약해지는 관계). 두 선은 {_comove_phrase(corr)}.',
        f'금리차는 {last:%Y-%m} <b>{gap:+.2f}%p</b> — 최근 3년 고점 {peak:+.2f}%p({peak_month:%Y-%m})보다 '
        f'{peak - gap:.2f}%p 낮고, 최근 12개월 {change_12:+.2f}%p 움직였습니다.',
        f'엔/달러는 <b>{yen:,.1f}엔</b> — 최근 12개월 {yen_12:+.1%}, 최근 10년 중 {rank:.0%} 지점'
        '(높을수록 엔화 약세)입니다.',
    ]
    off = None
    fit = data.iloc[-fit_months:]
    if len(fit) >= 60 and float(fit["rate_gap"].std()) > 0:
        slope, intercept = np.polyfit(fit["rate_gap"], np.log(fit["usdjpy"]), 1)
        if slope > 0:
            implied = float(np.exp(intercept + slope * gap))
            off = yen / implied - 1
            facts.append(f'최근 {len(fit) // 12}년 금리차와 엔/달러의 수준 관계로 보면 지금 금리차에 맞는 엔/달러는 '
                         f'약 <b>{implied:,.0f}엔</b>, 실제는 {yen:,.0f}엔({off:+.0%})입니다. 수준끼리의 단순 회귀라 '
                         '참고치입니다.')
    won_corr = None
    if fx is not None and len(fx) and {"usdkrw", "jpy"}.issubset(fx.columns):
        pair = fx[["usdkrw", "jpy"]].dropna()
        pair = pair[(pair > 0).all(axis=1)]
        if len(pair) >= 36:
            won_corr = float(np.log(pair["usdkrw"]).diff().corr(np.log(pair["jpy"]).diff()))
            facts.append(f'원/달러와 엔/달러의 월간 변화는 {_comove_phrase(won_corr)}(상관 <b>{won_corr:+.2f}</b>, '
                         f'{pair.index[0]:%Y}년부터 {len(pair)}개월).')

    view = ["엔/달러를 움직이는 핵심 변수는 미·일 10년물 금리차입니다"
            + ("." if corr >= .3 else
               " — 다만 월간 변화로 본 연결은 약한 편이라, 금리차만으로 방향을 단정하기는 어렵습니다.")]
    if shrinking:
        view.append(f'금리차가 최근 3년 고점 {peak:+.2f}%p에서 {gap:+.2f}%p로 줄어, 엔화 강세 요인이 쌓이고 있습니다.')
        if yen_12 is not None and yen_12 > .02:
            view.append("그런데도 엔화는 최근 1년 더 약해져, 금리차와 엔화 가치가 벌어져 있습니다.")
    elif widening:
        view.append(f'금리차가 최근 1년 {change_12:+.2f}%p 벌어져 엔화에는 약세 요인입니다.')
    else:
        view.append("금리차에 뚜렷한 방향이 없어 금리 쪽 압력은 중립입니다.")
    if off is not None:
        view.append(f'금리차로 설명되는 수준에 비해 엔화가 {off:.0%}가량 저평가된 상태로 보입니다.' if off > .05 else
                    f'금리차로 설명되는 수준에 비해 엔화가 {abs(off):.0%}가량 고평가된 상태로 보입니다.' if off < -.05
                    else "금리차에 비춰 엔화 가치는 적정 범위입니다.")
    if shrinking and off is not None and off > .05:
        text = "금리차 축소와 저평가가 겹쳐 엔화 가치가 오를 여지가 있습니다."
        if won_corr is not None:
            text += (" 원화도 엔화와 같은 방향으로 움직이는 경향이 있어 원화 가치도 오를 수 있습니다." if won_corr >= .3
                     else " 다만 원화와 엔화의 동행이 약해, 원화까지 함께 오른다고 보기는 어렵습니다.")
        view.append(text)
        follows = won_corr is not None and won_corr >= .3
        summary = (f"미·일 금리차가 줄고 엔화는 금리차로 설명되는 수준보다 {off:.0%} 약해, 엔화 가치가 오를 여지가 있습니다"
                   + (" — 원화도 함께 강해질 수 있습니다." if follows else " — 다만 원화가 따라간다고 보기는 어렵습니다."))
        short = ("미·일 금리차 축소로 엔화가 오를 여지가 있어 원화도 따라 강해질 수 있고" if follows
                 else "미·일 금리차 축소로 엔화가 오를 여지가 있지만 원화가 따라간다고 보기는 어렵고")
        won = 1 if follows else 0
    elif widening and off is not None and off < -.05:
        view.append("금리차 확대와 고평가가 겹쳐 엔화 가치가 떨어질 여지가 있습니다.")
        follows = won_corr is not None and won_corr >= .3
        summary = "미·일 금리차가 벌어지고 엔화는 고평가 상태라 엔화 가치가 떨어질 여지가 있습니다"
        summary += " — 원화에도 약세 압력입니다." if follows else "."
        short = ("미·일 금리차 확대로 엔화가 약해질 여지가 있어 원화에도 약세 압력이 있고" if follows
                 else "미·일 금리차 확대로 엔화가 약해질 여지가 있고")
        won = -1 if follows else 0
    else:
        view.append("금리차와 엔화 가치가 한 방향을 가리키지 않아, 엔화 방향에 대한 판단은 보류합니다.")
        summary, won = "미·일 금리차와 엔화 가치가 한 방향을 가리키지 않아 엔화 방향은 판단 보류입니다.", 0
        short = "엔화는 금리차와 가치가 엇갈려 판단 보류이고"
    return {"facts": facts, "view": view, "basis": last, "summary": summary, "short": short, "won": won}


def us_jp_commentary(frame, fx=None, start="1989-01-01"):
    insight = us_jp_insight(frame, fx, start)
    return _commentary_box(insight["facts"], insight["view"], US_JP_EXPERT_NOTES, insight["basis"],
                           US_JP_READING) if insight else ""


def saving_insight(frame):
    """총저축률·투자율·경상수지 그림의 한 문장. 자료가 없으면 None.

    저축 − 투자 ≈ 경상수지(회계상 항등식). 흑자가 크면 달러가 들어오는 구조라 원화에 우호적인 배경이지만
    연간 자료라 느리게 움직인다.
    """
    columns = ["saving_rate", "investment_rate", "current_account"]
    if frame is None or len(frame) == 0 or any(name not in frame for name in columns):
        return None
    data = frame[columns].dropna()
    if data.empty:
        return None
    year, row = int(data.index[-1]), data.iloc[-1]
    gap = float(row["saving_rate"] - row["investment_rate"])
    ca = float(row["current_account"])
    trend = ""
    if len(data) >= 2:
        prior = float(data["current_account"].iloc[-2])
        trend = f" 전년({int(data.index[-2])}년 {prior:,.0f}억 달러)보다 {'늘었습니다' if ca > prior else '줄었습니다'}."
    if ca >= 0:
        summary = (f"{year}년 총저축률 {row['saving_rate']:.1f}%가 투자율 {row['investment_rate']:.1f}%보다 {gap:+.1f}%p "
                   f"높아 경상수지 흑자 {ca:,.0f}억 달러가 나는 구조입니다.{trend} 흑자는 달러가 들어오는 배경이라 "
                   "원화에 우호적이지만 연간 자료라 느리게 움직입니다.")
        short = f"{year}년 경상수지는 저축이 투자보다 {gap:.1f}%p 많아 {ca:,.0f}억 달러 흑자로 원화에 우호적입니다"
        won = 1
    else:
        summary = (f"{year}년 투자율 {row['investment_rate']:.1f}%가 저축률 {row['saving_rate']:.1f}%보다 높아 경상수지 적자 "
                   f"{abs(ca):,.0f}억 달러가 나는 구조입니다.{trend} 적자는 달러가 나가는 배경이라 원화에 부담입니다.")
        short = f"{year}년 경상수지는 투자가 저축보다 {abs(gap):.1f}%p 많아 {abs(ca):,.0f}억 달러 적자로 원화에 부담입니다"
        won = -1
    return {"summary": summary, "short": short, "won": won}


def us_jp_chart(frame, start="1980-01-01", fx=None):
    """미·일 10년물 금리차와 엔/달러. 금리차가 벌어지면 엔이 약해진다는 관계를 보려는 것이다."""
    chart = dual_axis_chart(frame, "usdjpy", "rate_gap", "엔/달러", "미·일 10년물 금리차(%p)",
                            "미·일 금리차와 엔/달러", start,
                            "금리차 = 미국 10년 − 일본 10년. 일본 10년물 자료는 1989년부터라 그 전 구간은 "
                            "금리차 선이 없습니다. 점선은 금리차 0.",
                            left_fmt="{:,.0f}", right_fmt="{:+.1f}")
    return chart + us_jp_commentary(frame, fx) if chart else ""


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


# 국내총투자율이 파랑, 총저축률이 주황(2026-09-16 요청).
SAVING_COLORS = {"saving_rate": "#c8952a", "investment_rate": "#1a5490",
                 "surplus": "#8cc39f", "deficit": "#e7a3a0"}
# 꺾은선의 최저점을 막대 0 선보다 이만큼(그림 높이 비율) 위에 둔다. 선이 적자 막대 영역으로 내려오면
# 저축률·투자율이 음수인 것처럼 읽힌다(2026-09-16 지적).
LINE_FLOOR_GAP = .06


def nice_ticks(lo, hi, steps, most=6):
    """lo~hi 안의 딱 떨어지는 눈금. steps 가운데 눈금이 most 개 이하가 되는 가장 촘촘한 간격을 쓴다."""
    for step in steps:
        ticks = np.arange(np.ceil(lo / step) * step, hi + step * 1e-9, step)
        if len(ticks) <= most or step == steps[-1]:
            return [float(tick) + 0.0 for tick in ticks]      # -0.0 이 '-0' 으로 찍히지 않게


def saving_investment_chart(frame, start_year=1990):
    """총저축률·국내총투자율(꺾은선, 왼쪽 %)과 경상수지(막대, 오른쪽 억 달러)를 연도별로 겹친다.

    국민계정 항등식(저축 − 투자 ≈ 경상수지)을 눈으로 보려는 그림이다. 두 선의 간격이 벌어진 해에
    막대가 크게 나온다. 인과가 아니라 회계상 같은 것을 두 방향에서 잰 것이다.
    두 축은 눈금 위치(격자)를 함께 쓰고, 막대에는 0 기준선을 긋는다.
    """
    columns = ["saving_rate", "investment_rate", "current_account"]
    if frame is None or len(frame) == 0 or any(name not in frame for name in columns):
        return ""
    data = frame[columns]
    data = data[data.index >= start_year].dropna(how="all")
    if len(data.dropna(subset=["saving_rate", "investment_rate"])) < 5:
        return ""
    years = [int(year) for year in data.index]
    W, H, L, R, T, B = 900, 410, 58, 84, 62, 40
    PH = H - T - B
    band = (W - L - R) / len(years)
    xs = {year: L + band * (i + .5) for i, year in enumerate(years)}

    ca = data["current_account"].dropna()
    clo = min(0.0, float(ca.min())) if len(ca) else -1.0
    chi = max(0.0, float(ca.max())) if len(ca) else 1.0
    cpad = (chi - clo) * .06 or 1.0
    clo, chi = clo - cpad, chi + cpad

    def y_ca(value):
        return T + PH * (1 - (value - clo) / (chi - clo))

    # 왼쪽(%) 축은 막대 축에 맞춰 정한다. 선의 최저값을 막대 0 선보다 LINE_FLOOR_GAP 만큼 위에, 최고값을
    # 위 끝 조금 아래에 놓고 거꾸로 축 범위를 구한다 — 선이 적자 막대 영역으로 내려오지 않는다.
    rates = pd.concat([data["saving_rate"], data["investment_rate"]]).dropna()
    rmin, rmax = float(rates.min()), float(rates.max())
    low_frac = min((0.0 - clo) / (chi - clo) + LINE_FLOOR_GAP, .8)
    high_frac = .96
    span = max(rmax - rmin, 1.0) / (high_frac - low_frac)
    rlo = rmin - low_frac * span
    rhi = rlo + span

    def y_rate(value):
        return T + PH * (1 - (value - rlo) / (rhi - rlo))

    svg = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;'
           'font-family:-apple-system,Malgun Gothic,sans-serif;font-size:11px">']
    # 격자는 왼쪽 % 눈금에 맞추고, 오른쪽 억 달러는 축 끝의 짧은 눈금으로만 표시한다(두 축의 딱 떨어지는
    # 값이 같은 높이에 오지 않으므로 격자를 둘 다 그리면 어느 선이 어느 눈금인지 헷갈린다).
    for rate in nice_ticks(rlo, rhi, (1, 2, 5, 10)):
        y = y_rate(rate)
        svg.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" stroke="#eee"/>')
        svg.append(f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end" fill="#48525c">{rate:.0f}%</text>')
    for amount in nice_ticks(clo, chi, (100, 200, 250, 500, 1000, 2000)):
        y = y_ca(amount)
        svg.append(f'<line x1="{W - R}" x2="{W - R + 4}" y1="{y:.1f}" y2="{y:.1f}" stroke="#8fb39a"/>')
        svg.append(f'<text x="{W - R + 8}" y="{y + 4:.1f}" fill="#4f7a5c">{amount:,.0f}</text>')
    zero = y_ca(0.0)
    svg.append(f'<line x1="{L}" x2="{W - R}" y1="{zero:.1f}" y2="{zero:.1f}" stroke="#9aa3ab" '
               'stroke-dasharray="3,3"/>')
    last = years[-1]
    for year in years:
        x = xs[year]
        svg.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{T + PH}" y2="{T + PH + 4}" stroke="#c9ced6"/>')
        # 5년마다 + 처음·마지막 해. 마지막 해 바로 앞의 5년 눈금은 글자가 겹치므로 뺀다.
        if year in (years[0], last) or (year % 5 == 0 and last - year > 1):
            svg.append(f'<text x="{x:.1f}" y="{H - 14}" text-anchor="middle" fill="#8a9199">{year}</text>')
        value = data.at[year, "current_account"]
        if pd.isna(value):
            continue
        top, bottom = sorted((y_ca(float(value)), zero))
        color = SAVING_COLORS["surplus" if value >= 0 else "deficit"]
        svg.append(f'<rect x="{x - band * .32:.1f}" y="{top:.1f}" width="{band * .64:.1f}" '
                   f'height="{max(bottom - top, .6):.1f}" fill="{color}">'
                   f'<title>{year}년 경상수지 {value:,.0f}억 달러</title></rect>')
    for name, label in (("saving_rate", "총저축률"), ("investment_rate", "국내총투자율")):
        segments, current = [], []
        for year in years:
            value = data.at[year, name]
            if pd.isna(value):
                if current:
                    segments.append(current)
                    current = []
                continue
            current.append((xs[year], y_rate(float(value)), year, float(value)))
        if current:
            segments.append(current)
        color = SAVING_COLORS[name]
        for segment in segments:
            points = " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in segment)
            svg.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{points}"/>')
            svg.extend(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="{color}">'
                       f'<title>{year}년 {label} {value:.1f}%</title></circle>' for x, y, year, value in segment)
    # 범례: 윗줄은 선, 아랫줄은 막대와 축 설명
    svg.append(f'<line x1="{L}" x2="{L + 22}" y1="14" y2="14" stroke="{SAVING_COLORS["saving_rate"]}" stroke-width="2.5"/>'
               f'<text x="{L + 28}" y="18" fill="#1a1a1a" font-weight="600">총저축률</text>'
               f'<line x1="{L + 100}" x2="{L + 122}" y1="14" y2="14" stroke="{SAVING_COLORS["investment_rate"]}" '
               f'stroke-width="2.5"/><text x="{L + 128}" y="18" fill="#1a1a1a" font-weight="600">국내총투자율</text>'
               f'<text x="{L + 230}" y="18" fill="#6b7178">선: 국민총처분가능소득 대비 %(왼쪽 축)</text>')
    svg.append(f'<rect x="{L}" y="28" width="22" height="10" fill="{SAVING_COLORS["surplus"]}"/>'
               f'<text x="{L + 28}" y="37" fill="#1a1a1a" font-weight="600">경상수지 흑자</text>'
               f'<rect x="{L + 110}" y="28" width="22" height="10" fill="{SAVING_COLORS["deficit"]}"/>'
               f'<text x="{L + 138}" y="37" fill="#1a1a1a" font-weight="600">적자</text>'
               f'<text x="{L + 230}" y="37" fill="#6b7178">막대: 억 달러(오른쪽 축) · 점선은 경상수지 0</text>')
    svg.append("</svg>")

    latest = data.loc[last]
    facts = []
    if pd.notna(latest["saving_rate"]) and pd.notna(latest["investment_rate"]):
        facts.append(f'{last}년 총저축률 <b>{latest["saving_rate"]:.1f}%</b> · 국내총투자율 '
                     f'<b>{latest["investment_rate"]:.1f}%</b> (차이 {latest["saving_rate"] - latest["investment_rate"]:+.1f}%p)')
    if pd.notna(latest["current_account"]):
        facts.append(f'경상수지 <b>{latest["current_account"]:,.0f}억 달러</b>')
    both = data.dropna()
    if len(both) >= 10:
        corr = float((both["saving_rate"] - both["investment_rate"]).corr(both["current_account"]))
        if np.isfinite(corr):
            facts.append(f'저축률−투자율 차이와 경상수지의 상관 <b>{corr:+.2f}</b> ({len(both)}년)')
    return ('<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
            '총저축률·국내총투자율과 경상수지 '
            f'<span style="font-weight:400;color:#8a9199;font-size:12px">&nbsp;{years[0]}년~{last}년 · 연간</span></h3>'
            + "".join(svg) +
            f'<div style="font-size:13px;margin-top:6px">{" · ".join(facts)}</div>'
            '<div style="font-size:12px;color:#6b7178;margin-top:6px;line-height:1.7">'
            '총저축률 = 총저축 ÷ 국민총처분가능소득, 국내총투자율 = 국내총투자 ÷ 국민총처분가능소득(한국은행 '
            '국민계정 주요지표, 연간). 경상수지는 국제수지 기준 연간 합계입니다. 국민계정에서는 저축과 투자의 '
            '차이가 대체로 경상수지와 같아지므로(회계상 항등식), 저축률이 투자율보다 높은 해에 흑자 막대가 '
            '나옵니다. 인과가 아니라 같은 것을 두 방향에서 잰 관계입니다. 최근 연도는 잠정치라 바뀔 수 있습니다. '
            '자료: 한국은행 ECOS(2.1.1.1 주요지표 연간지표, 2.5.1.1 국제수지).</div>')


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
               us_market_frame=None, us_market_info=None, saving_frame=None, saving_info=None):
    now = now or datetime.now(KST)
    from macro_summary import summary_html
    # 판정 아래 '이 페이지를 한 문단으로': 그림마다 자료로 다시 쓴 결론 한 문장(2026-09-16 요청).
    insights = []
    for title, insight in (("원/달러와 위안/달러", fx_pair_insight(fx_frame)),
                           ("원/달러와 한·미 실질금리차", real_rate_insight(fx_frame, fx_info)),
                           ("미·일 금리차와 엔/달러", us_jp_insight(us_jp_frame, fx=fx_frame)),
                           ("총저축률·투자율과 경상수지", saving_insight(saving_frame))):
        if insight:
            insights.append({"title": title, "short": insight.get("short") or insight["summary"],
                             "summary": insight["summary"], "won": insight.get("won")})
    body = summary_html(fx_frame, us_jp_frame, us_market_frame, now, insights=insights)
    if fx_frame is not None:
        body += fx_decomposition_section(fx_frame, fx_info)
        body += fx_overlay_chart(fx_frame)
        body += real_rate_chart(fx_frame, info=fx_info) or (
            '<div class="empty">한·미 실질금리차를 만들지 못했습니다 — '
            + escape(str((fx_info or {}).get("failed", {}).get("real_rate_gap", "자료 부족"))) + '</div>')
    # 한국 그림끼리 모은다: 환율 그림 다음, 미·일·미국 그림 앞(2026-09-16 요청).
    if saving_frame is not None and len(saving_frame):
        body += saving_investment_chart(saving_frame)
    elif saving_info and saving_info.get("failed"):
        body += ('<div class="empty">총저축률·투자율·경상수지 자료를 받지 못했습니다 — '
                 + escape("; ".join(f"{k}: {v}" for k, v in saving_info["failed"].items())) + '</div>')
    if us_jp_frame is not None:
        body += us_jp_chart(us_jp_frame, fx=fx_frame)
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
    from data_sources.fred import fetch_fred, US_CPI, KOREA_CPI

    # ECOS 한국 물가가 실패해 FRED(OECD 계열, 2023-11까지)로 넘어가면 실질금리차가 그 달에서 멈춘다.
    # 예전에는 ECOS 오류를 버려 왜 멈췄는지 알 수 없었다(2026-09-16). 사유를 남겨 페이지에 적는다.
    # 오류 문구는 _ecos_request 가 키를 뺀 형태로 만든다.
    notes = {}

    def korea_cpi_with_fallback(start, end):
        try:
            return fetch_korea_cpi_monthly(start, end)
        except Exception as ecos_exc:
            notes["korea_cpi"] = (f"ECOS {type(ecos_exc).__name__}: {str(ecos_exc)[:120]} → "
                                  "FRED OECD 한국 CPI(2023-11까지)로 대체")
            print(f"  ⚠️ 한국 소비자물가: {notes['korea_cpi']}", flush=True)
            try:
                return fetch_fred(KOREA_CPI)
            except Exception as fred_exc:
                raise RuntimeError(f'ECOS {type(ecos_exc).__name__}; FRED {type(fred_exc).__name__}') from None

    try:
        frame, info = build_fx_inputs(fetch=fetch, cache_path=FX_CACHE,
                                      korea_rate_fn=fetch_korea_rate_monthly,
                                      current_account_fn=fetch_current_account_monthly,
                                      korea_cpi_fn=korea_cpi_with_fallback,
                                      us_cpi_fn=lambda: fetch_fred(US_CPI))
    except Exception as exc:
        return None, {"failed": {"전체": f"{type(exc).__name__}: {exc}"[:160]}, "notes": notes}
    if notes:
        info.setdefault("notes", {}).update(notes)
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


SAVING_CACHE = ROOT / "macro_history" / "korea_saving_investment.csv"


def load_saving_investment(fetch=True):
    """총저축률·국내총투자율·경상수지(연간). 받은 것은 보관본에 누적한다."""
    from data_sources.ecos import build_saving_investment
    try:
        frame, info = build_saving_investment(fetch=fetch, cache_path=SAVING_CACHE)
    except Exception as exc:
        return None, {"failed": {"전체": f"{type(exc).__name__}: {exc}"[:160]}}
    if fetch and len(frame) and info.get("source", "").startswith("ECOS_API"):
        SAVING_CACHE.parent.mkdir(parents=True, exist_ok=True)
        frame.reset_index().to_csv(SAVING_CACHE, index=False)
    return (frame if len(frame) else None), info


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
    saving, saving_info = load_saving_investment(fetch=not args.no_fetch)
    if saving_info.get("failed"):
        for name, reason in saving_info["failed"].items():
            print(f"  ⚠️ 저축·투자·경상수지 {name}: {reason}", flush=True)
    if saving is not None:
        print(f"  저축·투자·경상수지: {saving_info.get('source')} · {saving_info.get('first')}~"
              f"{saving_info.get('last')} ({saving_info.get('rows')}년)", flush=True)
    page = build_page(fx_frame=frame, fx_info=info, us_jp_frame=us_jp, us_jp_info=us_jp_info,
                      us_market_frame=us_market, us_market_info=us_market_info,
                      saving_frame=saving, saving_info=saving_info)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(page, encoding="utf-8")
        print(f"저장: {OUT.relative_to(ROOT)} ({len(page):,} bytes)")
    else:
        print(page)


if __name__ == "__main__":
    main()

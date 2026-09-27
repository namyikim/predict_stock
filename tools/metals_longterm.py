# -*- coding: utf-8 -*-
"""금·은 장기 전망(2026-09-27 요청) — 금속마다 '장기 전망' 탭 하나.

단기 절(다음 거래일 방향·1주일·1개월 구간)은 모델이지만, 장기 전망은 모델이 아니라 **과거의 비슷한 상황**과
**변동성 모의실험**으로 말한다. 금·은의 몇 달~몇 년 수익률을 이긴다고 검증된 모델이 이 저장소에 없기 때문이다.

- 금: 적정 가격 대비 괴리(CPI·달러인덱스·10년물 회귀, gold_valuation)가 지금과 같은 구간이었던 달의 1년 뒤 금값.
- 은: 금·은 가격 비율(금값 ÷ 은값)이 지금과 같은 구간이었던 달의 1년 뒤 은값. 비율이 높으면 금에 비해 은이 싸다.
- 둘 다: 딱 떨어지는 가격에 6개월·1년·2년 안에 닿을 확률(주식 장기 전망 탭과 같은 모의실험, forecast_utils).
- 둘 다: 위 숫자를 처음 값 그대로 원장(forecast_history/<금속>/outlook_log.csv)에 남기고 발표된 값으로 채점한다
  (outlook_ledger — 주식 장기 전망과 같은 규칙).

구간 통계는 겹치는 12개월 창이라 독립 표본이 적다(한 번의 긴 상승이 여러 달로 세어진다). 그래서 '같은 구간의 개월 수'와
'전체 평균'을 함께 적고, 표본이 적으면 그렇게 말한다.
"""
import html
import math

import numpy as np
import pandas as pd

import gold_valuation
import outlook_ledger

SPECS = {
    "gold": {"signal": "적정 가격 대비 괴리", "edges": [-np.inf, -0.10, 0.0, 0.10, 0.20, 0.30, np.inf],
             "labels": ["−10% 미만", "−10~0%", "0~+10%", "+10~20%", "+20~30%", "+30% 이상"],
             "fmt": lambda v: f"{v * 100:+.1f}%"},
    "silver": {"signal": "금·은 가격 비율(금값 ÷ 은값)", "edges": [-np.inf, 50, 65, 80, 95, np.inf],
               "labels": ["50 미만", "50~65", "65~80", "80~95", "95 이상"], "fmt": lambda v: f"{v:.1f}"},
}
MIN_BUCKET = 12          # 구간 안 개월 수가 이보다 적으면 '표본이 적다'고 적는다
LEVEL_WINDOWS = (("6개월", 126), ("1년", 252), ("2년", 504))


def forward_log_return(monthly, months=12):
    return np.log(monthly.shift(-months) / monthly)


def last_complete(series, today=None):
    """이번 달(월중)을 뺀 마지막 달까지. 월평균이 아직 바뀌는 달로 구간을 정하지 않는다."""
    today = pd.Timestamp.now() if today is None else pd.Timestamp(today)
    return series[series.index < pd.Period(today, freq="M")]


def bucket_stats(signal, price, edges, labels):
    """구간별 1년 뒤 로그수익률 통계와 전체 평균."""
    fwd = forward_log_return(price)
    frame = pd.DataFrame({"signal": signal, "fwd": fwd}).dropna()
    frame["bucket"] = pd.cut(frame["signal"], edges, labels=labels, right=False)
    rows = []
    for label in labels:
        part = frame.loc[frame["bucket"] == label, "fwd"]
        rows.append({"bucket": label, "n": int(len(part)),
                     "median": float(part.median()) if len(part) else None,
                     "q25": float(part.quantile(.25)) if len(part) else None,
                     "q75": float(part.quantile(.75)) if len(part) else None,
                     "up": float((part > 0).mean()) if len(part) else None})
    overall = {"n": int(len(frame)), "median": float(frame["fwd"].median()), "up": float((frame["fwd"] > 0).mean())}
    return rows, overall


def analyse(key, daily_close, cache_dir, fetch=True, valuation=None, today=None):
    """장기 전망 계산. 자료가 모자라면 해당 항목만 None."""
    spec = SPECS[key]
    out = {"key": key}
    gold_m = valuation[0]["gold"] if valuation else gold_valuation._yahoo_monthly("GC=F", cache_dir, fetch)
    if key == "gold":
        price = gold_m
        signal = valuation[0]["gap"] if valuation else None
    else:
        price = gold_valuation._yahoo_monthly("SI=F", cache_dir, fetch)
        signal = (gold_m / price).dropna() if price is not None and gold_m is not None else None
    out["price_monthly"] = price
    if signal is not None and price is not None and len(signal) > 36:
        signal = signal.dropna()
        done = last_complete(signal, today)
        rows, overall = bucket_stats(done, price, spec["edges"], spec["labels"])
        now_value = float(done.iloc[-1])
        now_bucket = str(pd.cut([now_value], spec["edges"], labels=spec["labels"], right=False)[0])
        out.update(signal=signal, rows=rows, overall=overall, as_of=str(done.index[-1]),
                   now_value=now_value, now_bucket=now_bucket, latest_value=float(signal.iloc[-1]),
                   latest_month=str(signal.index[-1]))
    from forecast_utils import summary_level_odds
    close = pd.Series(daily_close).dropna() if daily_close is not None else None
    out["odds"] = summary_level_odds(close) if close is not None and len(close) > 300 else None
    out["price_date"] = close.index[-1] if close is not None and len(close) else None
    return out


# ---------------------------------------------------------------------------
# 그림: 금·은 가격 비율
# ---------------------------------------------------------------------------
def ratio_svg(ratio, width=900, height=260):
    values = ratio.dropna()
    if len(values) < 24:
        return ""
    left, right, top, bottom = 56, 24, 32, 30
    lo, hi = math.floor(values.min() / 10) * 10, math.ceil(values.max() / 10) * 10
    n = len(values)

    def x(i):
        return left + (width - left - right) * i / (n - 1)

    def y(v):
        return top + (height - top - bottom) * (hi - v) / (hi - lo)

    grid = "".join(f'<line x1="{left}" x2="{width - right}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="#eee"/>'
                   f'<text x="{left - 8}" y="{y(t) + 4:.1f}" text-anchor="end" font-size="11" fill="#8a9199">{t:.0f}</text>'
                   for t in range(int(lo), int(hi) + 1, 10))
    median = float(values.median())
    line = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    years = ""
    for i, p in enumerate(values.index):
        if p.month == 1 and p.year % 2 == 0:
            years += f'<text x="{x(i):.1f}" y="{height - 10}" text-anchor="middle" font-size="10" fill="#8a9199">{p.year}</text>'
    last = float(values.iloc[-1])
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" xmlns="http://www.w3.org/2000/svg" '
            f'style="max-width:{width}px;font-family:-apple-system,\'Malgun Gothic\',sans-serif">'
            f'<rect width="{width}" height="{height}" fill="#fff"/>'
            f'<text x="{left}" y="18" font-size="13" font-weight="600" fill="#1a1a1a">금·은 가격 비율(금값 ÷ 은값) '
            f'{values.index[0]}~{values.index[-1]} (월평균)</text>{grid}'
            f'<line x1="{left}" x2="{width - right}" y1="{y(median):.1f}" y2="{y(median):.1f}" stroke="#8a9199" '
            f'stroke-dasharray="4,3"/><text x="{width - right}" y="{y(median) - 4:.1f}" text-anchor="end" font-size="10" '
            f'fill="#6b7178">중앙값 {median:.0f}</text>'
            f'<polyline points="{line}" fill="none" stroke="#6b7c93" stroke-width="1.8"/>'
            f'<circle cx="{x(n - 1):.1f}" cy="{y(last):.1f}" r="3.5" fill="#6b7c93"/>'
            f'<text x="{x(n - 1) - 6:.1f}" y="{y(last) - 8:.1f}" text-anchor="end" font-size="11" font-weight="600" '
            f'fill="#3a4652">지금 {last:.1f}</text>{years}</svg>')


# ---------------------------------------------------------------------------
# 원장: 처음 값만 남기고 발표된 값으로 채점(outlook_ledger 와 같은 규칙)
# ---------------------------------------------------------------------------
def collect(lt):
    rows = []
    label_r, unit_r, _ = outlook_ledger.SERIES["bucket_return_12m"]
    label_u, _, _ = outlook_ledger.SERIES["bucket_up_12m"]
    row = next((r for r in lt.get("rows") or [] if r["bucket"] == lt.get("now_bucket")), None)
    if row and row["n"] and lt.get("as_of"):
        target = (pd.Period(lt["as_of"], freq="M") + 12).strftime("%Y-%m")
        detail = f"{lt['now_bucket']}"
        rows.append({"record_id": f"bucket_return_12m|{lt['as_of']}|{target}|{detail}", "series": "bucket_return_12m",
                     "label": label_r, "unit": unit_r, "kind": "value", "info_as_of": lt["as_of"],
                     "target_period": target, "horizon": "12개월", "detail": detail, "point": row["median"],
                     "low": row["q25"], "high": row["q75"], "baseline": 0.0,
                     "model": f"{SPECS[lt['key']]['signal']} {detail} · {row['n']}개월"})
        rows.append({"record_id": f"bucket_up_12m|{lt['as_of']}|{target}|{detail}", "series": "bucket_up_12m",
                     "label": label_u, "unit": "probability", "kind": "probability", "info_as_of": lt["as_of"],
                     "target_period": target, "horizon": "12개월", "detail": detail, "probability": row["up"],
                     "baseline_probability": 0.5, "model": f"{SPECS[lt['key']]['signal']} {detail} · {row['n']}개월"})
    for r in outlook_ledger.collect_forecasts({}, None, lt.get("odds"), lt.get("price_date")):
        rows.append(r)
    return rows


def actuals(lt, daily_close, today=None):
    """월평균 수익률(구간 통계와 같은 정의)과 가격 도달 여부. 대상 달이 끝나야 값이 생긴다."""
    price = lt.get("price_monthly")
    close = pd.Series(daily_close).dropna().sort_index()
    this_month = pd.Period(pd.Timestamp.now() if today is None else today, freq="M")

    def ret(row):
        start, end = pd.Period(row["info_as_of"], freq="M"), pd.Period(row["target_period"], freq="M")
        if price is None or end >= this_month or start not in price.index or end not in price.index:
            return None
        return math.log(float(price[end]) / float(price[start]))
    return {"bucket_return_12m": ret,
            "bucket_up_12m": lambda row: None if ret(row) is None else float(ret(row) > 0),
            "level_reach": lambda row: outlook_ledger.level_reach_outcome(close, row)}


# ---------------------------------------------------------------------------
# 절
# ---------------------------------------------------------------------------
def render(key, name, lt, long_term_parts, valuation_parts, ledger, table, TD, TDR, TH, THR, note):
    """'<금속> · 장기 전망' h3 절."""
    e = html.escape
    spec = SPECS[key]
    parts = [f'<h3 style="font-size:18px;margin:34px 0 10px;padding-bottom:6px;border-bottom:1px solid #ddd">{e(name)} · 장기 전망 '
             '<span style="font-size:12px;color:#8a9199;font-weight:400">몇 달~몇 년 · 모델이 아니라 과거의 비슷한 상황과 모의실험</span></h3>']
    parts.append(note("장기 수익률을 '변화 없음'보다 잘 맞힌다고 검증된 금·은 모델이 없습니다. 그래서 여기서는 방향을 예측하지 않고, "
                      "<b>과거에 지금과 비슷했던 달에 1년 뒤 어땠는지</b>와 <b>지금의 변동성으로 본 가격 도달 확률</b>을 보여 줍니다. "
                      "모든 숫자는 처음 낸 값 그대로 기록해 발표된 값으로 채점합니다(맨 아래)."))
    parts.extend(long_term_parts)
    if key == "silver" and lt.get("signal") is not None:
        svg = ratio_svg(lt["signal"])
        if svg:
            parts.append('<h4 style="font-size:14px;margin:18px 0 6px">금·은 가격 비율 <span style="font-size:11px;color:#8a9199;'
                         'font-weight:400">높을수록 금에 비해 은이 싸다</span></h4>'
                         f'<div style="border:1px solid #e5e5e5;border-radius:6px;padding:8px">{svg}</div>')
    parts.extend(valuation_parts)

    if lt.get("rows"):
        subject = spec["signal"] + ("가" if key == "gold" else "이")      # 괴리가 · 비율(…)이
        parts.append(f'<h4 style="font-size:14px;margin:18px 0 6px">{e(subject)} 지금과 비슷했던 달의 1년 뒤 '
                     f'<span style="font-size:11px;color:#8a9199;font-weight:400">{e(lt["as_of"])} 기준 {spec["fmt"](lt["now_value"])} · '
                     f'구간 {e(lt["now_bucket"])}</span></h4>')
        body = ""
        for r in lt["rows"]:
            mark = ' style="background:#fff8e1"' if r["bucket"] == lt["now_bucket"] else ""
            if not r["n"]:
                cells = f'<td {TDR}>0</td><td {TDR}>—</td><td {TDR}>—</td><td {TDR}>—</td>'
            else:
                cells = (f'<td {TDR}>{r["n"]}</td><td {TDR}><b>{math.expm1(r["median"]) * 100:+.0f}%</b></td>'
                         f'<td {TDR}>{math.expm1(r["q25"]) * 100:+.0f}% ~ {math.expm1(r["q75"]) * 100:+.0f}%</td>'
                         f'<td {TDR}>{r["up"]:.0%}</td>')
            body += f'<tr{mark}><td {TD}>{e(r["bucket"])}{" ← 지금" if mark else ""}</td>{cells}</tr>'
        o = lt["overall"]
        body += (f'<tr><td {TD}><b>전체</b></td><td {TDR}>{o["n"]}</td><td {TDR}>{math.expm1(o["median"]) * 100:+.0f}%</td>'
                 f'<td {TDR}>—</td><td {TDR}>{o["up"]:.0%}</td></tr>')
        parts.append(table(f'<th {TH}>{e(spec["signal"])}</th><th {THR}>개월</th><th {THR}>1년 뒤 중앙값</th>'
                           f'<th {THR}>가운데 절반</th><th {THR}>오른 비율</th>', body, 560))
        now = next(r for r in lt["rows"] if r["bucket"] == lt["now_bucket"])
        few = now["n"] < MIN_BUCKET
        what = ("금값이 적정 가격보다 비싸던 때" if key == "gold" and lt["now_value"] > 0.1 else
                "금값이 적정 가격보다 싸던 때" if key == "gold" and lt["now_value"] < -0.1 else
                "은이 금에 비해 싸던 때" if key == "silver" and lt["now_value"] >= 80 else
                "은이 금에 비해 비싸던 때" if key == "silver" and lt["now_value"] < 65 else "평소와 비슷하던 때")
        text = (f'지금과 같은 구간({e(lt["now_bucket"])}, {what})은 {now["n"]}개월 있었고, 1년 뒤 {e(name)} 가격의 중앙값은 '
                + (f'<b>{math.expm1(now["median"]) * 100:+.0f}%</b>, 오른 비율은 {now["up"]:.0%}였습니다. '
                   f'전체 평균(중앙값 {math.expm1(lt["overall"]["median"]) * 100:+.0f}%, 오른 비율 {lt["overall"]["up"]:.0%})과 견주어 보세요.'
                   if now["n"] else "계산할 수 없습니다."))
        if few:
            text += f' <b>표본이 {now["n"]}개월뿐이라</b> 한두 번의 흐름이 숫자를 좌우합니다.'
        parts.append(f'<div style="font-size:13px;margin-top:8px">{text}</div>')
        parts.append(note("1년 뒤 수익률은 월평균 가격 기준입니다. 12개월 창이 겹치므로 한 번의 긴 상승·하락이 여러 달로 세어집니다 — "
                          "개월 수보다 독립적인 사례는 훨씬 적습니다. 과거의 빈도이지 예측 모델이 아닙니다."))

    odds = lt.get("odds")
    if odds and odds.get("levels"):
        body = ""
        for level in odds["levels"]:
            curve = np.asarray(level["curve"], dtype=float)
            cells = "".join(f'<td {TDR}>{curve[days - 1]:.0%}</td>' if curve.size >= days else f'<td {TDR}>—</td>'
                            for _, days in LEVEL_WINDOWS)
            body += (f'<tr><td {TD}>${level["level"]:,.0f} (지금보다 {level["change"] * 100:+.0f}%)</td>{cells}</tr>')
        parts.append(f'<h4 style="font-size:14px;margin:18px 0 6px">딱 떨어지는 가격에는 언제쯤? '
                     f'<span style="font-size:11px;color:#8a9199;font-weight:400">종가가 한 번이라도 닿을 확률 · 추세 없음</span></h4>')
        parts.append(table(f'<th {TH}>가격</th>' + "".join(f'<th {THR}>{label} 안</th>' for label, _ in LEVEL_WINDOWS), body, 480))
        parts.append(note(f'최근 3년 일간 등락(연 변동성 {odds["annual_vol"]:.0%})에서 평균을 빼고 다시 뽑아 2년을 모의실험했습니다. '
                          '추세를 넣지 않았으므로 오르내림의 크기만 반영한 확률이고, 그 가격을 지킨다는 뜻이 아닙니다. 목표가가 아닙니다.'))

    parts.append(outlook_ledger.render(ledger, heading="h4", title="지난 장기 전망은 맞았나",
                                       footnote=f"{name} 장기 전망 탭에 숫자로 나온 전망을"))
    return parts

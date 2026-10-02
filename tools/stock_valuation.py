# -*- coding: utf-8 -*-
"""과거 수출·환율 관계와 주가 — 반도체 수출액·원/달러 회귀의 '회귀 기준값'와 실제 주가의 괴리(2026-09-28 요청).

금 적정 가격(gold_valuation)과 같은 방식이다. 금은 사용자가 준 식을 그대로 썼지만 주식은 식이 없어, 같은 자료로
후보를 비교해 골랐다(2026-09-28, 2006~2026 월평균):

    ln(주가) = a + b·ln(반도체 수출액 12개월 합) + c·ln(원/달러)

- 삼성전자: R² 0.912. 앞 절반(2006~2016)으로 맞춘 식이 뒤 절반을 R² +0.43 으로 설명 — 후보 중 가장 안정적.
  수출만 쓰면 R² 0.907 · 뒤 절반 +0.33. 미 10년물 금리를 더하면 R² 는 0.914 로 거의 같고 뒤 절반이 −3.5 로 무너졌다.
- SK하이닉스: 같은 식이 R² 0.84 이지만 뒤 절반은 −1.1 로 설명하지 못한다(2023~2026 급등이 이전 관계를 벗어났다).
  그래서 식은 같게 두고 **안정성 판정을 함께 적는다** — 불안정하면 회귀 기준값를 참고로만 읽으라고 쓴다.

수출은 달러로 벌고 원화가 약하면 원화 이익이 커진다 — 두 변수 모두 경제적으로 읽힌다. 계수는 매 실행 2006년~지난달
자료로 다시 맞춘다(금과 달리 고정된 식이 아니다). 두 계열 모두 우상향하는 수준끼리의 회귀라 높은 R² 가 곧 예측력은
아니다 — 앞 절반→뒤 절반 점검이 그 한계를 드러낸다. 목표가가 아니다.
"""
import html
import math

import numpy as np
import pandas as pd

import gold_valuation

START = "2006-01"
PRICE_COLOR = "#1a1a1a"


def monthly_series(frame):
    """macro 틀(month, value) → 월 Period Series."""
    if frame is None or len(frame) == 0:
        return None
    return pd.Series(pd.to_numeric(frame["value"], errors="coerce").to_numpy(),
                     index=pd.PeriodIndex(pd.to_datetime(frame["month"]), freq="M")).dropna()


def build(price, exports, krw, today=None):
    """월평균 주가·수출 12개월 합·원/달러. 이번 달(월중)은 뺀다 — 수출 통계가 아직 없는 달이다."""
    exp12 = exports.sort_index().rolling(12).sum()
    frame = pd.DataFrame({"price": price, "exp12": exp12, "krw": krw}).loc[START:]
    this_month = pd.Period(pd.Timestamp.now() if today is None else today, freq="M")
    return frame[frame.index < this_month].dropna()


def fit(frame):
    y = np.log(frame["price"]).to_numpy()
    X = np.column_stack([np.ones(len(frame)), np.log(frame["exp12"]), np.log(frame["krw"])])
    coef = np.linalg.lstsq(X, y, rcond=None)[0]
    fitted = X @ coef
    r2 = float(1 - ((y - fitted) ** 2).sum() / ((y - y.mean()) ** 2).sum())
    half = len(frame) // 2
    early = np.linalg.lstsq(X[:half], y[:half], rcond=None)[0]
    resid = y[half:] - X[half:] @ early
    oos_r2 = float(1 - (resid ** 2).sum() / ((y[half:] - y[half:].mean()) ** 2).sum())
    out = frame.copy()
    out["fair"] = np.exp(fitted)
    out["gap"] = out["price"] / out["fair"] - 1
    return out, {"coef": [float(c) for c in coef], "r2": r2, "oos_r2": oos_r2, "n": int(len(frame)),
                 "first": str(frame.index.min()), "last": str(frame.index.max()),
                 "split": str(frame.index[half]), "stable": oos_r2 > 0}


def analyse(ticker, macro, cache_dir, fetch=True):
    exports = monthly_series(macro.get("semiconductor_exports"))
    price = gold_valuation._yahoo_monthly(ticker, cache_dir, fetch)
    krw = gold_valuation._yahoo_monthly("KRW=X", cache_dir, fetch)
    if exports is None or price is None or krw is None:
        return None
    frame = build(price, exports, krw)
    if len(frame) < 60:
        return None
    out, info = fit(frame)
    return {"out": out, "fit": info}


def gap_axis(gap):
    lo = math.floor(min(-0.3, float(gap.min())) * 10) / 10
    hi = math.ceil(max(0.7, float(gap.max())) * 10) / 10
    return (lo, hi)


def render(result, name, table, TD, TDR, TH, THR):
    if not result:
        return []
    e = html.escape
    out, f = result["out"], result["fit"]
    end, last = out.iloc[-1], out.index[-1]
    svg = gold_valuation.chart_svg(
        out, price_col="price", title=f"{name} 주가와 회귀 기준값 괴리", price_label="주가(좌, 원, 월평균)",
        fair_label="회귀 기준값(좌, 회귀식)", money=lambda v: f"{v:,.0f}원", gap_axis=gap_axis(out["gap"]),
        axis_note="좌: 원 · 우: %", price_color=PRICE_COLOR,
        gap_label="회귀 기준값 대비 괴리(우, %)")
    parts = ['<h4 style="font-size:14px;margin:18px 0 6px">과거 수출·환율 관계와 주가 '
             '<span style="font-size:11px;color:#8a9199;font-weight:400">과거 수출·환율 관계에 따른 기준값</span></h4>',
             f'<div style="border:1px solid #e5e5e5;border-radius:6px;padding:8px">{svg}</div>']
    state = ("과거 관계의 기준값보다 높습니다" if end["gap"] > 0.05 else "과거 관계의 기준값보다 낮습니다" if end["gap"] < -0.05
             else "과거 관계의 기준값 부근입니다")
    gap = out["gap"]
    body = (f'<tr><td {TD}>{last.year}년 {last.month}월 주가(월평균)</td><td {TDR}>{end["price"]:,.0f}원</td></tr>'
            f'<tr><td {TD}>회귀 기준값</td><td {TDR}>{end["fair"]:,.0f}원</td></tr>'
            f'<tr><td {TD}>괴리</td><td {TDR}><b>{end["gap"] * 100:+.1f}%</b> · {state}</td></tr>'
            f'<tr><td {TD}>{out.index[0].year}년 이후 괴리 범위</td><td {TDR}>{gap.min() * 100:+.0f}% ({gap.idxmin()}) ~ '
            f'{gap.max() * 100:+.0f}% ({gap.idxmax()})</td></tr>'
            f'<tr><td {TD}>지금보다 괴리가 컸던 달</td><td {TDR}>{(gap >= end["gap"]).mean():.0%}</td></tr>'
            f'<tr><td {TD}>입력값 ({last})</td><td {TDR}>반도체 수출 12개월 합 {end["exp12"] / 1e8:,.0f}억 달러 · '
            f'원/달러 {end["krw"]:,.0f}</td></tr>')
    parts.append(table(f'<th {TH}>항목</th><th {THR}>값</th>', body, 460))
    c = f["coef"]

    def term(coef, label):
        return f'{"+" if coef >= 0 else "−"} {abs(coef):.3f}·{label}'

    # 방향 문장은 실제 계수의 부호로 만든다(SK하이닉스는 원/달러 계수가 음수로 나왔다).
    effects = [("반도체 수출이 늘수록" if c[1] > 0 else "반도체 수출이 줄수록")]
    if abs(c[2]) >= 0.05:
        effects.append("원화가 약할수록(원/달러가 높을수록)" if c[2] > 0 else "원화가 강할수록(원/달러가 낮을수록)")
    direction = (" · ".join(effects) + " 회귀 기준값가 올라갑니다."
                 + ("" if abs(c[2]) >= 0.05 else " 이 식의 원/달러 계수는 ±0.05 안입니다."))
    stability = (f'앞 절반({f["first"]}~{f["split"]})으로 맞춘 식이 뒤 절반을 R² {f["oos_r2"]:+.2f}로 설명해, 관계가 기간에 걸쳐 '
                 '어느 정도 유지됐습니다.' if f["stable"] else
                 f'<b>다만 앞 절반({f["first"]}~{f["split"]})으로 맞춘 식은 뒤 절반을 설명하지 못합니다(R² {f["oos_r2"]:+.2f}).</b> '
                 '최근 주가가 과거의 수출·환율 관계를 크게 벗어났다는 뜻이라, 이 회귀 기준값는 참고로만 읽어 주세요.')
    parts.append(
        '<div style="margin:10px 0 0;padding:12px 16px;background:#f5f6f8;border-radius:6px;font-size:13px;color:#4a4f55;line-height:1.65">'
        f'식: ln(주가) = {c[0]:.2f} {term(c[1], "ln(반도체 수출액 12개월 합, 달러)")} {term(c[2], "ln(원/달러)")} · '
        f'{f["first"]}~{f["last"]} {f["n"]}개월 월평균, R² {f["r2"]:.3f}. 계수는 매 실행 최신 자료로 다시 맞춥니다. '
        f'{stability} {direction} '
        '두 계열 모두 우상향하는 수준끼리의 회귀라 높은 R²가 곧 예측력은 아니고, 괴리가 크다고 곧 되돌아온다는 보장도 없습니다. '
        '식은 전체 기간으로 맞춘 것이라 과거 구간의 괴리는 사후적으로 본 것입니다. 기업의 내재가치를 추정한 값은 아니며, 시점별 순차 검증과 단순 기준 모델 비교로 예측력을 확인한 결과도 아닙니다. 목표가나 매수·매도 의견이 아닙니다. '
        '자료: 주가 Yahoo Finance 월평균, 반도체 수출액 KOSIS(최근 달은 관세청 자료로 보완), 원/달러 Yahoo 월평균.</div>')
    return parts

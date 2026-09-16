# -*- coding: utf-8 -*-
"""거시 경제 페이지 맨 위 '한눈에 보는 쉬운 요약'.

아래 그림들이 지금 무엇을 보여 주는지를 일반 사용자 말로 적는다. 이 저장소의 다른 요약과 같은
태도를 지킨다.
- 판정(좋음/개선/보통/주의/나쁨)은 정해진 규칙으로만 낸다(2026-09-16 요청 — 종목 보고서처럼 지금이
  좋은지 나쁜지 한눈에 보이게). 규칙·관점·근거 개수를 판정 옆에 함께 적는다. 예측이 아니다.
- 문장은 템플릿이다. 계산된 값을 정해진 문장에 채우므로 같은 자료면 같은 문장이 나온다.
- 인과를 단정하지 않는다. "~하는 동안 ~했다"로 관찰을 적고, 해석은 "흔히 ~로 읽힙니다"로 한정한다.
- 매수·매도·보유 같은 말을 만들지 않는다(테스트로 막는다).
- 자료가 오래됐으면 그 사실을 적고 판단 재료에서 뺀다. 갱신이 멈춘 계열로 '지금'을 말하면 안 된다.

'위치'는 최근 12개월 범위 안에서 어디인가(하위/중간/상위)로, '방향'은 최근 3개월 변화로 말한다.
둘 다 임의의 문턱이 아니라 자료 자체의 범위에서 나오는 값이라, 다른 자료를 넣어도 같은 규칙이 선다.

판정 규칙(지표 하나):
  유리한 정도 = 위치(오를수록 유리한 지표) 또는 1 − 위치(내릴수록 유리한 지표)
  유리한 편(≥0.6)  : 나빠지고 있으면 주의, 아니면 좋음
  불리한 편(≤0.4)  : 나아지고 있으면 개선, 아니면 나쁨
  가운데           : 나아지면 개선, 나빠지면 주의, 아니면 보통
  나아짐/나빠짐은 최근 석 달 변화가 그 지표의 '거의 그대로' 폭을 넘었는지로 본다.
종합은 판정에 쓴 지표들의 위치(유리 +1·가운데 0·불리 −1)와 방향(나아짐 +1·그대로 0·나빠짐 −1)을
각각 평균해 같은 규칙(문턱 ±0.25)으로 나눈다.
"""
from html import escape

import numpy as np
import pandas as pd

STALE_MONTHS = 3          # 이보다 오래된 계열은 '업데이트 지연'으로 표시하고 판단에서 뺀다
FAVORABLE_SIDE = 0.6      # 1년 범위에서 유리한 쪽 40% 안이면 '유리한 편'
OVERALL_CUT = 0.25        # 종합 판정에서 위치·방향 평균을 나누는 문턱
MIN_JUDGED = 3            # 판정에 쓸 지표가 이보다 적으면 종합 판정을 내지 않는다
PERSPECTIVE = "한국 금융시장·위험자산 기준"
# 단계: (배경색, 글자색, 뜻)
STAGES = {
    "좋음": ("#e6f2ea", "#1e6b34", "유리한 편이고 나빠지지 않고 있습니다"),
    "개선": ("#e7f0fb", "#1a5490", "아직 유리한 편은 아니지만 나아지고 있습니다"),
    "보통": ("#f1f2f4", "#5b6570", "1년 범위의 가운데이고 뚜렷한 움직임이 없습니다"),
    "주의": ("#fdf3e3", "#9a5b00", "나빠지는 쪽으로 움직이고 있습니다"),
    "나쁨": ("#fbeaea", "#a8322a", "불리한 편이고 나아지지 않고 있습니다"),
}
_NOT_JUDGED = ("#f1f2f4", "#6b7178")


def _as_series(series):
    """열이 없거나 스칼라면 빈 Series. 아래 규칙 함수들이 항상 Series 를 받게 한다."""
    if series is None or not isinstance(series, pd.Series):
        return pd.Series(dtype=float)
    return pd.to_numeric(series, errors="coerce").dropna()


def _last(series):
    series = _as_series(series)
    return (series.index[-1], float(series.iloc[-1])) if len(series) else (None, None)


def position(series, window=12):
    """최근 window 개월 범위 안에서 마지막 값의 위치 0~1. 자료가 모자라면 None."""
    series = _as_series(series)
    if len(series) < window:
        return None
    recent = series.iloc[-window:]
    lo, hi = float(recent.min()), float(recent.max())
    return (float(series.iloc[-1]) - lo) / (hi - lo) if hi > lo else 0.5


def direction(series, months=3):
    """최근 months 개월 변화(마지막 값 − months 개월 전). 자료가 모자라면 None."""
    series = _as_series(series)
    if len(series) <= months:
        return None
    return float(series.iloc[-1] - series.iloc[-1 - months])


def is_stale(series, now, months=STALE_MONTHS):
    stamp, _ = _last(series)
    if stamp is None:
        return True
    return (pd.Timestamp(now).to_period("M") - pd.Timestamp(stamp).to_period("M")).n > months


def where_word(pos):
    if pos is None:
        return "위치 판단 불가"
    return ("1년 중 가장 낮은 편" if pos < 0.2 else "1년 중 낮은 편" if pos < 0.4 else
            "1년 중 가운데" if pos <= 0.6 else "1년 중 높은 편" if pos <= 0.8 else "1년 중 가장 높은 편")


def trend_word(change, unit, flat, decimals=None):
    """flat 보다 작은 변화는 '거의 그대로'. 단위별 임계값은 호출부가 정한다.

    변화량은 값 자체가 아니라 차이이므로 %·%p 는 소수 둘째 자리, 지수는 첫째 자리, 원·포인트는
    정수로 적는다. '0% 올랐습니다' 같은 문장이 나오지 않게 한다.
    """
    if change is None:
        return "최근 흐름 판단 불가"
    if abs(change) < flat:
        return "최근 석 달 거의 그대로"
    sign = "올랐습니다" if change > 0 else "내렸습니다"
    if decimals is None:
        decimals = 2 if unit in ("%p", "%") else (1 if unit == "" and abs(change) < 50 else 0)
    shown_unit = "%p" if unit == "%" else unit          # 금리의 변화는 %p 로 적는다
    return f"최근 석 달 {abs(change):,.{decimals}f}{shown_unit} {sign}"


def sides(pos, chg, favorable, flat):
    """(위치 쪽, 방향 쪽). 위치: 유리 +1·가운데 0·불리 −1. 방향: 나아짐 +1·그대로 0·나빠짐 −1.

    favorable 은 유리한 방향이다(+1: 오를수록 유리, −1: 내릴수록 유리, 0: 판정하지 않음).
    판정할 수 없으면 None.
    """
    if not favorable or pos is None or chg is None:
        return None
    goodness = pos if favorable > 0 else 1 - pos
    level = 1 if goodness >= FAVORABLE_SIDE else (-1 if goodness <= 1 - FAVORABLE_SIDE else 0)
    move = favorable * chg
    return level, (1 if move >= flat else -1 if move <= -flat else 0)


def stage_from(level, move):
    """위치 쪽·방향 쪽 → 단계. 지표 하나와 종합 판정이 같은 규칙을 쓴다."""
    if level > 0:
        return "주의" if move < 0 else "좋음"
    if level < 0:
        return "개선" if move > 0 else "나쁨"
    return "개선" if move > 0 else "주의" if move < 0 else "보통"


def stage_of(pos, chg, favorable, flat):
    """위치·방향 → 단계(좋음/개선/보통/주의/나쁨). 판정하지 않는 지표면 None."""
    found = sides(pos, chg, favorable, flat)
    return stage_from(*found) if found else None


def item(label, series, now, unit, flat, fmt="{:,.0f}", reading="", favorable=0):
    """항목 하나: 이름 · 최근값(기준일) · 위치 · 방향 · (해석). 오래됐으면 지연 표시."""
    stamp, value = _last(series)
    if value is None:
        return {"label": label, "text": "자료 없음", "stale": True, "stamp": None, "favorable": favorable}
    stale = is_stale(series, now)
    head = f"<b>{escape(label)}</b> {fmt.format(value)}{escape(unit)} <span class='muted'>({stamp:%Y-%m} 기준)</span>"
    if stale:
        return {"label": label, "stamp": stamp, "stale": True, "favorable": favorable,
                "text": head + " — <span style='color:#a8322a'>업데이트 지연</span>. 이 값은 위 판정에 쓰지 않습니다."}
    pos, chg = position(series), direction(series)
    text = f"{head} · {where_word(pos)} · {trend_word(chg, unit, flat)}"
    if reading:
        text += f". {escape(reading)}"
    found = sides(pos, chg, favorable, flat)
    return {"label": label, "stamp": stamp, "stale": False, "text": text, "pos": pos, "chg": chg,
            "favorable": favorable, "sides": found, "stage": stage_from(*found) if found else None}


def monthly(frame):
    """일별 자료면 월평균으로 줄인다. 월별이면 그대로. 위치·방향 규칙이 월 단위이기 때문이다."""
    if frame is None or len(frame) == 0:
        return frame
    index = pd.DatetimeIndex(frame.index)
    if len(index) > 1 and (index[1:] - index[:-1]).median() < pd.Timedelta(days=20):
        return frame.resample("MS").mean()
    return frame


def build_items(fx, us_jp, us_market, now):
    """자료 → 항목 목록. 각 항목의 해석 문장은 관찰에 붙는 일반 지식이지 지금의 인과 주장이 아니다.

    favorable 은 한국 금융시장·위험자산 기준의 유리한 방향이다. 원화 약세가 수출 기업에 유리하듯
    관점이 바뀌면 방향도 바뀐다 — 그래서 요약에 관점을 함께 적는다. 미·일 금리차는 한국 입장에서
    유리·불리가 한쪽으로 정해지지 않아 판정하지 않는다(0).
    """
    items = []
    fx, us_jp, us_market = monthly(fx), monthly(us_jp), monthly(us_market)
    if us_market is not None and "high_yield_spread" in us_market and "hy_spread" not in us_market:
        us_market = us_market.rename(columns={"high_yield_spread": "hy_spread"})
    if fx is not None:
        items.append(item("원/달러", fx.get("usdkrw"), now, "원", 15, favorable=-1,
                          reading="원/달러가 오르면 원화가 약해진 것입니다."))
        items.append(item("미 달러지수", fx.get("dxy"), now, "", 1.0, fmt="{:.1f}", favorable=-1,
                          reading="여러 통화 대비 달러의 힘입니다. 원/달러 결정 요인 표에서 가장 큰 몫을 차지해 왔습니다."))
        items.append(item("한·미 10년물 금리차", fx.get("rate_gap"), now, "%p", 0.15, fmt="{:+.2f}", favorable=1,
                          reading="한국 − 미국. 흔히 이 값이 낮을수록 원화가 약해지는 쪽으로 읽힙니다."))
        items.append(item("한·미 실질금리차", fx.get("real_rate_gap"), now, "%p", 0.15, fmt="{:+.2f}", favorable=1,
                          reading="물가를 뺀 금리차. 명목보다 환율과의 관계가 안정적이라고 흔히 봅니다."))
    if us_jp is not None:
        items.append(item("미·일 10년물 금리차", us_jp.get("rate_gap"), now, "%p", 0.15, fmt="{:+.2f}",
                          reading="미국 − 일본. 흔히 이 값이 커지면 엔이 약해지는 쪽으로 읽힙니다."))
    if us_market is not None:
        items.append(item("미국 하이일드 스프레드", us_market.get("hy_spread"), now, "%p", 0.25, fmt="{:.2f}",
                          favorable=-1,
                          reading="회사채가 국채보다 얼마나 더 높은 금리를 요구받는지. 흔히 신용 불안의 온도계로 읽힙니다."))
        items.append(item("미국 10년물", us_market.get("us10y"), now, "%", 0.2, fmt="{:.2f}", favorable=-1,
                          reading="장기 금리. 오르면 주식·장기 자산에 흔히 부담으로 읽힙니다."))
        items.append(item("나스닥", us_market.get("nasdaq"), now, "", 400, fmt="{:,.0f}", favorable=1))
    return items


def overall(items):
    """판정에 쓴 지표들의 종합. 지표가 MIN_JUDGED 개보다 적으면 단계는 None."""
    judged = [it for it in items if not it.get("stale") and it.get("sides")]
    levels = [it["sides"][0] for it in judged]
    moves = [it["sides"][1] for it in judged]
    counts = {"n": len(judged),
              "good": levels.count(1), "bad": levels.count(-1), "middle": levels.count(0),
              "better": moves.count(1), "worse": moves.count(-1), "flat": moves.count(0)}
    if len(judged) < MIN_JUDGED:
        return None, counts
    level, move = float(np.mean(levels)), float(np.mean(moves))
    level_side = 1 if level >= OVERALL_CUT else (-1 if level <= -OVERALL_CUT else 0)
    move_side = 1 if move >= OVERALL_CUT else (-1 if move <= -OVERALL_CUT else 0)
    return stage_from(level_side, move_side), counts


def chip(label, big=False):
    bg, fg = STAGES[label][:2] if label in STAGES else _NOT_JUDGED
    size = "font-size:20px;padding:3px 16px;border-radius:16px" if big else \
        "font-size:11px;padding:1px 0;border-radius:10px;min-width:56px;text-align:center"
    return (f'<span style="display:inline-block;{size};background:{bg};color:{fg};font-weight:700;'
            f'margin-right:6px">{escape(label)}</span>')


def watch_points(items):
    """조건부 관찰점. 예측이 아니라 '함께 움직이면 이렇게 읽힌다'는 조합이다."""
    by = {it["label"]: it for it in items if not it.get("stale")}
    notes = []
    hy, us10, nq = by.get("미국 하이일드 스프레드"), by.get("미국 10년물"), by.get("나스닥")
    if hy and us10 and nq:
        hy_up = (hy.get("chg") or 0) > 0.25
        rate_up = (us10.get("chg") or 0) > 0.2
        nq_down = (nq.get("chg") or 0) < -400
        if rate_up and nq_down and not hy_up:
            notes.append("최근 석 달 미국 장기금리가 오르는 동안 나스닥이 내렸고, 하이일드 스프레드는 크게 오르지 "
                         "않았습니다. 흔히 '금리 부담은 있지만 신용 불안까지는 아닌 국면'으로 읽힙니다. "
                         "스프레드까지 함께 오르기 시작하면 흔히 경기 불안 신호로 읽힙니다.")
        elif hy_up and nq_down:
            notes.append("최근 석 달 하이일드 스프레드가 오르는 동안 나스닥이 내렸습니다. 흔히 신용 불안이 주식에 "
                         "옮겨가는 국면으로 읽힙니다.")
        elif hy_up:
            notes.append("최근 석 달 하이일드 스프레드가 올랐습니다. 주식이 아직 반응하지 않았다면, 흔히 먼저 보는 "
                         "경고 신호로 읽힙니다.")
        else:
            notes.append("최근 석 달 미국 신용 스프레드·장기금리·나스닥 세 지표에서 함께 움직이는 경고 조합은 "
                         "보이지 않습니다.")
    usd, gap = by.get("원/달러"), by.get("한·미 10년물 금리차")
    if usd and gap:
        if (usd.get("chg") or 0) > 15 and (gap.get("chg") or 0) < -0.15:
            notes.append("최근 석 달 한·미 금리차가 줄어드는 동안 원/달러가 올랐습니다. 두 지표가 같은 방향을 가리키는 "
                         "국면입니다.")
        elif (usd.get("chg") or 0) < -15 and (gap.get("chg") or 0) > 0.15:
            notes.append("최근 석 달 한·미 금리차가 벌어지는 동안 원/달러가 내렸습니다. 두 지표가 같은 방향을 가리키는 "
                         "국면입니다.")
    return notes


def verdict_html(items):
    """맨 위 종합 판정 상자: 단계 · 뜻 · 근거 개수 · 눈여겨볼 지표 · 단계 설명 · 관점."""
    stage, counts = overall(items)
    if stage is None:
        head = (f'{chip("판단 보류", big=True)}<span style="font-size:14px">판정에 쓸 수 있는 지표가 '
                f'{counts["n"]}개뿐이라({MIN_JUDGED}개 이상 필요) 종합 판정을 내지 않습니다.</span>')
    else:
        head = f'{chip(stage, big=True)}<span style="font-size:14px;font-weight:600">{escape(STAGES[stage][2])}</span>'
    basis = (f'판정에 쓴 지표 {counts["n"]}개 · 1년 범위로 보면 유리한 쪽 {counts["good"]}개 · '
             f'불리한 쪽 {counts["bad"]}개 · 가운데 {counts["middle"]}개 / 최근 석 달은 나아진 것 '
             f'{counts["better"]}개 · 나빠진 것 {counts["worse"]}개 · 그대로 {counts["flat"]}개')
    watch = [f'{it["stage"]}: {it["label"]}' for it in items
             if not it.get("stale") and it.get("stage") in ("나쁨", "주의")]
    watch_html = (f'<div style="font-size:12px;margin-top:4px">눈여겨볼 지표 — {escape(" · ".join(watch))}</div>'
                  if watch else "")
    legend = " ".join(f'{chip(name)}<span style="font-size:11px;color:#6b7178;margin-right:10px">'
                      f'{escape(meaning)}</span>' for name, (_, _, meaning) in STAGES.items())
    return ('<div style="background:#fff;border:1px solid #cedff0;border-radius:6px;padding:12px 14px;margin:12px 0 0">'
            f'<div style="font-size:11px;color:#7a8797;margin-bottom:6px">지금 거시 환경 · {escape(PERSPECTIVE)}</div>'
            f'<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">{head}</div>'
            f'<div style="font-size:12px;color:#3a4652;margin-top:8px;line-height:1.6">{escape(basis)}</div>'
            + watch_html +
            f'<div style="margin-top:8px;line-height:2">{legend}</div>'
            '<div style="font-size:11px;color:#7a8797;margin-top:4px;line-height:1.6">'
            '유리한 방향: 원/달러·미 달러지수·하이일드 스프레드·미국 10년물은 내릴수록, 한·미 금리차·나스닥은 '
            '오를수록 유리한 것으로 봅니다. 미·일 금리차는 한국에 유리·불리가 한쪽으로 정해지지 않아 판정에 넣지 '
            '않습니다. 원화 약세가 수출 기업에는 유리할 수 있듯 관점이 바뀌면 판정도 바뀝니다. 1년 범위 안의 '
            '위치와 최근 석 달 방향을 정해진 규칙으로 나눈 것이며 예측이 아닙니다.</div></div>')


def summary_html(fx, us_jp, us_market, now):
    items = build_items(fx, us_jp, us_market, now)
    if not items:
        return ""
    stale = [it["label"] for it in items if it.get("stale")]
    stamps = [it["stamp"] for it in items if it.get("stamp") is not None and not it.get("stale")]
    basis = f"{min(stamps):%Y-%m}~{max(stamps):%Y-%m}" if stamps else "—"

    def label_for(it):
        if it.get("stale"):
            return "판단 제외"
        return it.get("stage") or "참고"

    rows = "".join(
        '<li style="margin:7px 0;line-height:1.7;display:flex;gap:4px;align-items:baseline">'
        f'<span style="flex:0 0 auto">{chip(label_for(it))}</span><span>{it["text"]}</span></li>'
        for it in items)
    notes = watch_points(items)
    notes_html = ("".join(f'<li style="margin:6px 0;line-height:1.7">{escape(n)}</li>' for n in notes)
                  if notes else '<li style="margin:6px 0">판단에 쓸 수 있는 지표가 부족합니다.</li>')
    stale_html = (f'<div style="font-size:12px;color:#a8322a;margin-top:8px">업데이트 지연: '
                  f'{escape(", ".join(stale))} — 이 지표는 위 판정과 관찰점에 쓰지 않았습니다.</div>' if stale else "")
    return (
        '<section id="easy-summary" aria-label="한눈에 보는 쉬운 요약" '
        'style="background:#f0f6fc;border:1px solid #cedff0;border-radius:8px;padding:16px 20px;margin:0 0 20px">'
        '<h3 style="margin:0 0 6px;font-size:19px">한눈에 보는 쉬운 요약</h3>'
        f'<div style="font-size:12px;color:#586575">자료 기준 {escape(basis)} · 아래 그림의 최근값을 정해진 규칙으로 '
        '읽은 것입니다. 예측이나 매매 판단이 아닙니다.</div>'
        + verdict_html(items) +
        '<div style="font-size:11px;color:#7a8797;margin-top:10px">위치 = 최근 12개월 범위 안에서 어디인가 · '
        '방향 = 최근 3개월 변화 · 해석은 흔히 쓰이는 읽는 법이지 지금의 인과 주장이 아닙니다.</div>'
        '<div style="font-size:13px;font-weight:700;margin-top:12px">지표별로 보면</div>'
        f'<ul style="list-style:none;padding:0;margin:4px 0 0;font-size:13px">{rows}</ul>'
        '<div style="font-size:13px;font-weight:700;margin-top:12px">함께 보면 읽히는 것</div>'
        f'<ul style="margin:4px 0 0;padding-left:18px;font-size:13px">{notes_html}</ul>'
        + stale_html +
        '</section>')

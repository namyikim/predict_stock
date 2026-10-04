"""시간순 예측 모델 선택과 누적 예측 원장. 노트북에도 동일 소스를 포함한다."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


def decision_inputs_html(*, name, cards, unknowns, caveat=""):
    """판단 재료 요약 — 이미 계산된 값을 한곳에 모은다. 새 주장을 만들지 않는다.

    매수·매도·보유 의견을 내지 않는 이유는 이 저장소가 스스로 측정한 결과 때문이다. 갭 AUC 0.80
    vs 세션 AUC 0.50 — 예측력이 있는 구간은 09:00 시가까지이고, 의견은 09:00 이후에 실행된다.
    그래서 '무엇을 아는가'와 '무엇을 모르는가'를 나란히 적고 판단은 사람에게 남긴다.

    cards: [{"label", "value", "detail", "source", "tone"}] — tone 은 'up'/'down'/'' 중 하나.
    unknowns: 이 보고서가 답하지 못하는 것들(문자열 목록).
    """
    from html import escape
    if not cards:
        return ""
    colors = {"up": "#1e6b34", "down": "#a8322a", "": "#1a1a1a"}
    rows = ""
    for card in cards:
        tone = colors.get(card.get("tone", ""), "#1a1a1a")
        rows += (f'<tr><td style="padding:8px 11px;border-top:1px solid #eee;white-space:nowrap">'
                 f'{escape(card["label"])}</td>'
                 f'<td style="padding:8px 11px;border-top:1px solid #eee;text-align:right;'
                 f'font-size:15px;font-weight:700;color:{tone}">{escape(str(card["value"]))}</td>'
                 # 값이 눈에 띄게, 읽는 법·출처는 옅은 회색으로 내린다(2026-09-28 요청). 크기는 표 본문 13px 그대로
                 # (2026-09-27 통일 요청) — 색으로만 단계를 준다.
                 f'<td style="padding:8px 11px;border-top:1px solid #eee;color:#8a9199;font-size:13px">'
                 f'{escape(card.get("detail", ""))}</td>'
                 f'<td style="padding:8px 11px;border-top:1px solid #eee;color:#a3a9b0;font-size:13px;'
                 f'white-space:nowrap">{escape(card.get("source", ""))}</td></tr>')
    unknown_html = ""
    if unknowns:
        # 한계 목록은 늘 같은 내용이라 매번 펼쳐 둘 필요가 없다. 접어 두고 제목만 보인다(2026-09-28 요청).
        unknown_html = ('<details style="margin-top:10px;font-size:12px;color:#6b7178">'
                        '<summary style="cursor:pointer;color:#7a8797">이 보고서가 답하지 못하는 것 '
                        f'({len(unknowns)}가지)</summary><ul style="margin:6px 0 0;padding-left:18px;line-height:1.6">'
                        + "".join(f"<li>{escape(u)}</li>" for u in unknowns) + "</ul></details>")
    return ('<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
            f'그 밖에 지금 알 수 있는 것 <span style="font-weight:400;color:#8a9199;font-size:12px">'
            f'&nbsp;{escape(name)} · 흩어진 값을 모은 것이며 매수·매도 의견이 아닙니다</span></h3>'
            '<div style="overflow-x:auto"><table style="width:100%;min-width:420px;border-collapse:collapse;'
            'font-size:13px;border:1px solid #e5e5e5">'
            '<tr style="background:#fafafa;font-size:11px;color:#6b7178">'
            '<th style="padding:8px 11px;text-align:left">항목</th>'
            '<th style="padding:8px 11px;text-align:right">지금</th>'
            '<th style="padding:8px 11px;text-align:left">읽는 법</th>'
            '<th style="padding:8px 11px;text-align:left">출처</th></tr>'
            f'{rows}</table></div>{unknown_html}'
            + (f'<div style="font-size:11px;color:#8a9199;margin:6px 0 20px;line-height:1.6">'
               f'{escape(caveat)}</div>' if caveat else ""))


# 쉬운 요약 맨 위의 두 블록. 읽는 사람이 가장 먼저 찾는 것은 '다음 거래일에 시초가·방향·종가가
# 어떻게 되나'와 '지난 예측은 맞았나, 지금까지 얼마나 맞히나'다(2026-09-13 지적). 둘을 요약 맨 위에
# 크게 두고 나머지 요약은 그 아래에 그대로 둔다.
# 지난 예측 결과는 장 마감 후 갱신(tools/build_afternoon_update.py)이 아래 표시 사이만 다시 그려 넣는다.
# 아침 보고서의 결과가 오후의 '예측 vs 실제' 절과 어긋나지 않게 하려는 것이다. 지우지 말 것.
SCORECARD_START, SCORECARD_END = "<!--SCORECARD_START-->", "<!--SCORECARD_END-->"
# 시가 반영 갱신(P16 운영 반영, 2026-09-20). 아침 보고서는 이 표시 사이에 자리 표시만 두고, 09:37 회차
# (tools/build_afternoon_update.py --scope open)가 실제 시가로 낸 종가 방향 카드로 바꿔 끼운다.
POSTOPEN_START, POSTOPEN_END = "<!--POSTOPEN_START-->", "<!--POSTOPEN_END-->"
# 09:37 행의 모델명과 정보 마감 표시. 07:00 사전 예측 행(대표 모델·Candidate …)은 절대 덮어쓰지 않고
# 같은 target_date 에 별도 모델명으로 한 행만 더한다. 이름이 대표 집계(is_headline_model)에서 빠진다.
POST_OPEN_MODEL = "Post-open"
POST_OPEN_INFORMATION_CUTOFF = "post_open"
PRE_OPEN_INFORMATION_CUTOFF = "pre_open"
# P16 결정 문서의 과거 검증 정확도(같은 날짜·같은 정답, 정보 마감만 다름). 카드에 "모델이 좋아진 것이
# 아니라 정보가 늘어난 것"을 숫자로 적기 위한 값이다. experiments/model_improvement/P16/…/decision.md.
POST_OPEN_TRACK_RECORD = {
    "samsung": {"pre_open": .454, "post_open": .564, "gap_rule": .544},
    "sk_hynix": {"pre_open": .499, "post_open": .576, "gap_rule": .544},
}
_WEEKDAYS_KO = "월화수목금토일"


def is_headline_model(model):
    """대표 행인가. 관찰 후보('Candidate …')와 시가 반영 갱신('Post-open')은 대표 집계에 넣지 않는다.

    Series 를 받으면 같은 인덱스의 bool Series, 문자열을 받으면 bool 을 돌려준다.
    """
    if isinstance(model, pd.Series):
        text = model.astype(str)
        return ~(text.str.startswith("Candidate") | text.str.startswith(POST_OPEN_MODEL))
    text = str(model)
    return not (text.startswith("Candidate") or text.startswith(POST_OPEN_MODEL))
# 방향 낱말은 보고서 전체에서 상승·보합·하락으로 통일한다(2026-09-17). 쉬운 요약만 "오름·큰 변화
# 없음·내림"을 써서 1절·판단 재료 표와 어긋났고, 같은 예측이 다른 말로 보였다.
_PLAIN_DIRECTION = {"하락": "하락", "보합": "보합", "상승": "상승"}
_VERDICT_COLORS = {"맞음": ("#e6f2ea", "#1e6b34"), "틀림": ("#fbeaea", "#a8322a"),
                   "채점 전": ("#f1f2f4", "#6b7178"), "유보": ("#eef1f5", "#5b6570")}
_CARD = ('flex:1 1 92px;min-width:0;border:1px solid #e3e8ee;border-radius:6px;'
         'padding:9px 11px;background:#fbfdff')
_BOX = 'background:#fff;border:1px solid #cedff0;border-radius:6px;padding:12px 14px;margin:12px 0 0'


def _finite(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def _day_label(value):
    """'2026-09-14 (월)'. 날짜로 읽을 수 없으면 None."""
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(stamp):
        return None
    return f"{stamp.date().isoformat()} ({_WEEKDAYS_KO[stamp.weekday()]})"


# 종가 방향 발행 기준. 최대 확률이 이 값 이상일 때만 방향을 내고 그 아래는 '판단 유보'로 표시하는 장치다.
# 2026-09-16 오전에 0.50 으로 켰다가(P08: 0.5 이상인 날만 고르면 외부 정확도 0.46→0.67·0.50→0.64, 대신
# 전체의 4분의 1만 발행) 같은 날 사용자 결정으로 껐다 — "판단을 안 하는 것은 비겁하다. 정확도를 높이는
# 노력을 해야지". 그래서 0.0: 세 확률 중 가장 높은 방향을 매일 낸다(예전과 같다).
# 장치는 남겨 둔다. 원장에는 argmax 라벨과 세 확률이 그대로 남으므로, 값을 올리면 과거 기록에도 소급된다.
DIRECTION_ISSUE_MIN_PROB = 0.0
_DIRECTION_WORDS = {0: "▼ 하락", 1: "보합", 2: "▲ 상승"}


def direction_call(row, min_prob=DIRECTION_ISSUE_MIN_PROB):
    """세 확률에서 종가 방향 판정을 만든다.

    반환: {"valid", "issued", "label", "max_prob", "argmax"}.
      valid  — 세 확률이 모두 있고 합이 1이며 최댓값이 하나뿐인가.
      issued — valid 이고 최대 확률이 min_prob 이상인가. 아니면 '판단 유보'.
      label  — issued 면 '▼ 하락'·'보합'·'▲ 상승', 유보면 '판단 유보', valid 가 아니면 '판단 어려움'.
    """
    row = row if hasattr(row, "get") else {}
    probabilities = [_finite(row.get(k)) for k in ("p_down", "p_flat", "p_up")]
    valid = (all(p is not None and 0 <= p <= 1 for p in probabilities)
             and abs(sum(probabilities) - 1) < .01
             and sum(abs(p - max(probabilities)) < 1e-9 for p in probabilities) == 1)
    if not valid:
        return {"valid": False, "issued": False, "label": "판단 어려움", "max_prob": None, "argmax": None}
    best = max(probabilities)
    argmax = probabilities.index(best)
    issued = best >= min_prob
    return {"valid": True, "issued": issued, "max_prob": float(best), "argmax": argmax,
            "label": _DIRECTION_WORDS[argmax] if issued else "판단 유보"}


def direction_hold_note(call, min_prob=DIRECTION_ISSUE_MIN_PROB):
    """유보·발행 사유 한 줄."""
    if not call.get("valid"):
        return "세 확률이 비슷하거나 값이 없습니다"
    if call["issued"]:
        return (f"계산상 가능성 {call['max_prob']:.0%}"
                + (f" (기준 {min_prob:.0%} 이상)" if min_prob > 0 else ""))
    return (f"가장 높은 확률이 {call['max_prob']:.0%}로 기준 {min_prob:.0%}에 못 미쳐 방향을 내지 않습니다"
            f"(계산상 기울기: {_DIRECTION_WORDS[call['argmax']].strip('▼▲ ')})")


def next_day_forecast_html(*, prediction_date, summary, open_forecast, price_forecasts,
                           target_mode="close_to_close"):
    """다음 거래일의 시초가·방향·종가를 카드 셋으로. 하루의 시간 순서(09:00 → 15:30)대로 놓는다.

    시초가 카드를 크게 둔다 — 이 모델의 예측력은 거의 전부 갭(전일 종가→시가)에 있고(갭 AUC 0.8,
    장중 AUC 0.5), 종가 방향은 확률이 기준 이상인 날만 낸다(direction_call).
    가격은 아래 요약과 같은 문(門)을 지난 것만 숫자로 보인다. 검증을 통과하지 못한 가격은 원시값도
    중심값도 내지 않는다 — 숫자가 보이면 예측으로 읽힌다.
    """
    from html import escape
    summary = summary if hasattr(summary, "get") else {}
    open_forecast = open_forecast if hasattr(open_forecast, "get") else {}
    live = summary.get("live")
    call = direction_call(live if hasattr(live, "get") else {})
    basis = "당일 시초가 대비" if target_mode == "open_to_close" else "전일 종가 대비"
    direction_note = (f"{basis} · " if call["valid"] else "") + direction_hold_note(call)

    def price(row, field):
        row = row if hasattr(row, "get") else {}
        point = _finite(row.get(field))
        if row.get("signal") != "있음" or point is None or point <= 0:
            return "예측 안 함", "검증을 통과하지 못해 숫자를 내지 않습니다"
        change = _finite(row.get("predicted_return"))
        return f"{point:,.0f}원", (f"전일 종가 대비 {change:+.2%}" if change is not None else "모델 예상")

    by_days = {r.get("trading_days"): r for r in (price_forecasts or []) if hasattr(r, "get")}
    cards = [("시초가 · 09:00",) + price(open_forecast, "predicted_open") + (True,),
             ("종가 방향", call["label"], direction_note, False),
             ("종가 · 15:30",) + price(by_days.get(1, {}), "predicted_close") + (False,)]
    body = ""
    for label, value, note, big in cards:
        muted = value in ("예측 안 함", "판단 어려움", "판단 유보")
        size = 16 if muted else (24 if big else 19)
        body += (f'<div style="{_CARD}{";flex:2 1 150px" if big else ""}">'
                 f'<div style="font-size:11px;color:#7a8797">{escape(label)}'
                 f'{" · 밤사이 미국 시장을 반영한 값 · 09:00 전에만 의미" if big else ""}</div>'
                 f'<div style="font-size:{size}px;font-weight:700;line-height:1.35;'
                 f'color:{"#8a9199" if muted else "#1a1a1a"}">{escape(value)}</div>'
                 f'<div style="font-size:11px;color:#7a8797;line-height:1.45">{escape(note)}</div></div>')
    when = _day_label(open_forecast.get("target_date", prediction_date)) or _day_label(prediction_date)
    return (f'<div style="{_BOX}">'
            f'<div style="font-size:14px;font-weight:700;margin-bottom:8px">다음 거래일 '
            f'{escape(when or "날짜 미확인")} 예측</div>'
            f'<div style="display:flex;gap:8px;flex-wrap:wrap">{body}</div></div>')


def scorecard_html(review, ensemble_name="Mean ensemble", note=""):
    """지난 예측이 맞았는지와 지금까지의 성적. 실제로 미리 낸 예측만 센다(백테스트 아님).

    판정은 아래 '예측 vs 실제' 절(ledger_section_html)과 같은 행을 같은 기준으로 고른다 —
    시초가·종가는 실제 값이 예측 구간 안이면 맞음, 방향은 상승·보합·하락이 같으면 맞음.
    성적은 review_ledger 의 가장 긴 창이다. 표본이 20일 미만이면 흐리게 하고 이르다고 적는다.
    """
    from html import escape
    review = review if isinstance(review, dict) else {}
    latest = review.get("latest")

    def won(value):
        value = _finite(value)
        return "—" if value is None else f"{value:,.0f}원"

    def verdict(flag):
        value = _finite(flag)
        return "채점 전" if value is None else ("맞음" if value == 1 else "틀림")

    results = []
    if isinstance(latest, pd.DataFrame) and len(latest) and "kind" in latest:
        kind = latest["kind"]
        model = latest["model"] if "model" in latest else pd.Series("", index=latest.index)
        horizon = latest["horizon_days"] if "horizon_days" in latest else pd.Series(1, index=latest.index)

        # 관찰 후보('Candidate …': 저녁 시초가, HAR·IV 구간, strict gate)는 대표 행이 아니다. 같은 날 같은
        # kind 의 행이 여럿이므로 이름으로 걸러야 한다(2026-09-16: 저녁 시초가 후보를 추가하며 확인).
        headline = is_headline_model(model)

        def first(mask):
            picked = latest[mask & headline]
            return picked.iloc[0] if len(picked) else None

        def price_result(label, row, point, actual, low, high):
            predicted = won(row.get(point)) if _finite(row.get(point)) is not None else "숫자 없음(구간만)"
            band = (f" · 구간 {won(row.get(low))}~{won(row.get(high))}"
                    if _finite(row.get(low)) is not None and _finite(row.get(high)) is not None else "")
            return label, verdict(row.get("interval_hit")), f"예측 {predicted} → 실제 {won(row.get(actual))}{band}"

        row = first(kind == "open")
        if row is not None:
            results.append(price_result("시초가", row, "predicted_open", "actual_open", "low_open", "high_open"))
        row = first((kind == "direction") & (model == ensemble_name))
        if row is not None:
            predicted = str(row.get("prediction"))
            actual = _finite(row.get("actual_class"))
            actual_text = {0: "하락", 1: "보합", 2: "상승"}.get(int(actual), "—") if actual is not None else "—"
            change = _finite(row.get("actual_return"))
            if change is not None:
                actual_text += f" ({change:+.2%})"
            call = direction_call(row)
            if call["valid"] and not call["issued"]:
                # 그날은 방향을 내지 않았다. 맞음·틀림으로 세지 않고, 계산상 기울기가 어땠는지만 참고로 적는다.
                results.append(("방향", "유보",
                                f"판단 유보(가장 높은 확률 {call['max_prob']:.0%}, 기준 {DIRECTION_ISSUE_MIN_PROB:.0%} 미만) "
                                f"→ 실제 {actual_text} · 참고: 계산상 기울기 {_PLAIN_DIRECTION.get(predicted, predicted)}, "
                                f"{verdict(row.get('direction_correct'))}"))
            else:
                results.append(("방향", verdict(row.get("direction_correct")),
                                f"예측 {_PLAIN_DIRECTION.get(predicted, predicted)} → 실제 {actual_text}"))
        # 시가 반영 갱신(Post-open)은 대표 행이 아니므로 headline 마스크 밖에서 따로 찾는다. 07:00 결과와
        # 같은 줄에 합치지 않는다 — 정보 마감이 다른 두 예측은 다른 질문의 답이다.
        post = latest[(kind == "direction") & (model.astype(str) == POST_OPEN_MODEL)]
        if len(post):
            row = post.iloc[0]
            actual = _finite(row.get("actual_class"))
            actual_text = {0: "하락", 1: "보합", 2: "상승"}.get(int(actual), "—") if actual is not None else "—"
            created = pd.to_datetime(row.get("created_at_utc"), utc=True, errors="coerce")
            when = f"{created.tz_convert('Asia/Seoul'):%H:%M}" if pd.notna(created) else "시각 미상"
            gap = _finite(row.get("gap"))
            results.append(("시가반영", verdict(row.get("direction_correct")),
                            f"시가 반영 갱신(실제 실행 {when}) 예측 {_PLAIN_DIRECTION.get(str(row.get('prediction')), str(row.get('prediction')))}"
                            + (f"(갭 {gap:+.2%})" if gap is not None else "") + f" → 실제 {actual_text}"
                            " · 정보 마감 15:30 이전 · 07:00 예측과 별개로 셉니다"))
        row = first((kind == "price") & (horizon == 1))
        if row is not None:
            results.append(price_result("종가", row, "predicted_close", "actual_close", "low_close", "high_close"))

    if results:
        result_html = "".join(
            '<div style="display:flex;gap:8px;align-items:baseline;padding:6px 0;border-top:1px solid #eef1f4">'
            f'<span style="flex:0 0 auto;background:{_VERDICT_COLORS[mark][0]};color:{_VERDICT_COLORS[mark][1]};'
            f'font-size:12px;font-weight:700;padding:1px 9px;border-radius:10px">{mark}</span>'
            f'<span style="flex:0 0 42px;font-size:13px;font-weight:700">{escape(label)}</span>'
            f'<span style="font-size:12px;color:#3a4652;line-height:1.5">{escape(detail)}</span></div>'
            for label, mark, detail in results)
    else:
        result_html = ('<div style="font-size:13px;color:#6b7178">아직 채점된 예측이 없습니다. 오늘 예측은 '
                       '다음 거래일에 실제 시가·종가와 대조됩니다.</div>')
    scored_day = _day_label(review.get("latest_date")) if results else None

    rolling = review.get("rolling")
    n_days = int(_finite(review.get("n_scored_days")) or 0)
    stats, held_note = [], ""
    span_label = f"채점한 {n_days}거래일 전체" if n_days else ""
    if isinstance(rolling, pd.DataFrame) and len(rolling) and {"window", "kind", "n"}.issubset(rolling.columns):
        window = int(rolling["window"].max())
        span = rolling[rolling["window"] == window]
        if n_days > window:
            span_label = f"최근 {window}거래일"

        def pick(kind, horizon=None):
            part = span[span["kind"] == kind]
            if horizon is not None and "horizon_days" in part:
                part = part[part["horizon_days"] == horizon]
            return part.iloc[0] if len(part) else None

        # 방향 성적은 실제로 방향을 낸 날(최대 확률이 기준 이상)만 센다. 유보한 날 수는 옆에 적는다.
        # direction_issued 행이 없는 옛 review 면 전체 행으로 물러선다.
        row = pick("direction_issued")
        issued_only = row is not None
        if row is None:
            row = pick("direction")
        if row is not None and _finite(row.get("n")) and int(row["n"]) > 0 and _finite(row.get("hit_rate")) is not None:
            base = _finite(row.get("prior_hit_rate"))
            held = _finite(row.get("held"))
            parts = ([f"방향을 낸 날만 · 유보 {held:.0f}일"] if issued_only and held is not None else []) + \
                    ([f"늘 같은 답이면 {base:.0%}"] if base is not None else [])
            stats.append(("종가 방향 적중률", float(row["hit_rate"]), int(row["n"]), " · ".join(parts)))
        elif row is not None and issued_only:
            held = _finite(row.get("held")) or 0
            held_note = f"종가 방향은 최근 {held:.0f}일 모두 판단 유보였습니다(가장 높은 확률이 {DIRECTION_ISSUE_MIN_PROB:.0%} 미만). "
        # 1주일(5거래일)·1개월(20거래일) 종가 구간도 원장에서 매일 채점되지만 카드에는 1일만 보였다
        # (2026-09-23). review_ledger 의 rolling 표는 가격 예측을 기간별로 이미 묶으므로 고르기만 하면 된다.
        # 만기가 아직 없는 기간(1개월은 첫 만기 10월 7일)은 행이 없어 카드가 나오지 않는다.
        for label, kind, horizon in (("시초가 구간 적중", "open", None), ("종가 구간 적중", "price", 1),
                                     ("1주일 종가 구간 적중", "price", 5),
                                     ("1개월 종가 구간 적중", "price", 20)):
            row = pick(kind, horizon)
            if row is not None and _finite(row.get("interval_coverage")) is not None and _finite(row.get("n")):
                target = _finite(row.get("nominal_coverage"))
                extra = f"목표 {target:.0%}" if target is not None else ""
                if horizon in (5, 20):
                    # 긴 기간은 예측끼리 기간이 겹쳐 독립 표본이 아니다. 적중률이 목표보다 한참 높으면 구간이
                    # 넓어 정보가 적다는 뜻이기도 하다 — 그 사실을 옆에 적는다.
                    extra = " · ".join(x for x in (extra, "기간이 겹쳐 독립 표본 아님") if x)
                stats.append((label, float(row["interval_coverage"]), int(row["n"]), extra))
        # 시가 반영 갱신 적중률은 별도 카드다. 대표 적중률과 합치지 않고 정보 마감을 적는다.
        row = pick("direction_post_open")
        if row is not None and _finite(row.get("n")) and int(row["n"]) > 0 and _finite(row.get("hit_rate")) is not None:
            stats.append(("시가 반영 갱신 적중률", float(row["hit_rate"]), int(row["n"]),
                          "정보 마감 15:30 이전 · 07:00 과 다른 질문"))
    if stats:
        stats_html = '<div style="display:flex;gap:8px;flex-wrap:wrap">' + "".join(
            f'<div style="{_CARD}">'
            f'<div style="font-size:11px;color:#7a8797">{escape(label)}</div>'
            f'<div style="font-size:19px;font-weight:700;line-height:1.35;'
            f'color:{"#8a9199" if n < 20 else "#1a1a1a"}">{value:.0%}</div>'
            f'<div style="font-size:11px;color:#7a8797">{n}{"건" if label.startswith(("1주일", "1개월")) else "일"} 중'
            f'{" · " + escape(extra) if extra else ""}</div></div>'
            for label, value, n, extra in stats) + '</div>'
    else:
        stats_html = '<div style="font-size:13px;color:#6b7178">아직 성적을 낼 만큼 채점된 예측이 없습니다.</div>'
    caution = ("표본이 20일이 안 되어 아직 판단하기 이릅니다. "
               if stats and min(n for _, _, n, _ in stats) < 20 else "")
    return (f'<div style="{_BOX};margin-top:8px">'
            '<div style="font-size:14px;font-weight:700;margin-bottom:4px">지난 예측은 맞았나'
            + (f' <span style="font-weight:400;color:#7a8797;font-size:12px">{escape(scored_day)} 예측</span>'
               if scored_day else '') + '</div>'
            f'{result_html}'
            '<div style="font-size:14px;font-weight:700;margin:12px 0 6px">지금까지 성적 '
            f'<span style="font-weight:400;color:#7a8797;font-size:12px">'
            f'{escape(" · ".join(part for part in (span_label, "미리 낸 예측만") if part))}</span></div>'
            f'{stats_html}'
            '<div style="font-size:11px;color:#8a9199;margin-top:6px;line-height:1.5">'
            f'{caution}{held_note}백테스트가 아니라 실제로 미리 낸 예측을 채점한 결과입니다. 가격은 실제 값이 예측 구간 안에 '
            '들어오면 맞음으로 셉니다. 자세한 수치는 아래 ‘예측 vs 실제’에 있습니다.'
            + (f' {escape(note)}' if note else '') + '</div></div>')


# ---------------------------------------------------------------------------
# 기록하지 않는 재실행이 보여 줄 예측
# ---------------------------------------------------------------------------
# 2026-09-16: SK하이닉스 보고서가 코드 push·조각 갱신으로 하루에 여덟 번 다시 만들어졌다. 매번 모델을 새로
# 학습해 15:25 판에는 '하락'이 맨 위에 떴지만, 원장에 기록되고 채점된 그날 아침 예측은 '보합'이었다.
# 화면의 예측과 채점하는 예측이 달라서는 안 된다. 기록하지 않는 실행은 원장의 공식 사전 예측을 보여 준다.
_COMMON_FORECAST_FIELDS = ("signal", "current_close", "as_of_date", "target_date", "predicted_return",
                           "raw_predicted_return", "band_coverage", "model_mae", "zero_baseline_mae",
                           "mae_diff_lo", "mae_diff_hi", "oof_slope")
_OPEN_FORECAST_FIELDS = _COMMON_FORECAST_FIELDS + ("predicted_open", "center_open", "low_open", "high_open",
                                                   "gap_sign_auc", "gap_corr")
_PRICE_FORECAST_FIELDS = _COMMON_FORECAST_FIELDS + ("predicted_close", "center_close", "low_close",
                                                    "high_close", "vol_model")
# 비어 있는 것 자체가 뜻인 칸(검증을 통과하지 못해 점 예측을 내지 않음). 나머지 칸은 원장이 비어 있으면
# 새로 계산한 값을 둔다 — 옛 원장에 없던 통계 칸이 'nan%' 로 찍히지 않게.
_NULL_MEANS_NO_FORECAST = ("predicted_return", "predicted_open", "predicted_close")


def official_forecast(ledger, prediction_date, evening_model="Candidate evening forecast"):
    """원장에 기록된 그날의 공식 사전 예측 — 예측일 당일 아침 실행(없으면 가장 먼저 기록된 사전 예측 실행)의 행들.

    저녁 후보(evening_model)와 사전 예측이 아닌 행은 뺀다. 원장의 채점 규칙(날짜별 최초 사전 예측)과 같다.
    반환: 없으면 None, 있으면 {"run_id", "created_at_utc", "direction": {모델: 행},
    "open": 행 또는 None, "price": {거래일 수: 행}}. 가격·시초가는 'Candidate …' 관찰 후보를 뺀 대표 행이다.
    """
    needed = {"prediction_date", "run_id", "kind", "model"}
    if not isinstance(ledger, pd.DataFrame) or ledger.empty or not needed.issubset(ledger.columns):
        return None
    prospective = (ledger["is_prospective"].astype(str).str.strip().str.lower().isin(["true", "1"])
                   if "is_prospective" in ledger else pd.Series(True, index=ledger.index))
    rows = ledger[(ledger["prediction_date"].astype(str).str[:10] == str(pd.Timestamp(prediction_date).date()))
                  & prospective & (ledger["model"].astype(str) != evening_model)]
    direction = rows[rows["kind"] == "direction"]
    if direction.empty:
        return None
    if "created_at_utc" in direction:
        # 채점 규칙(daily_comparison)과 같다: 예측일 당일 아침 예측이 먼저, 그다음 가장 먼저 기록된 것.
        direction = direction.assign(_created=pd.to_datetime(direction["created_at_utc"], utc=True, errors="coerce"),
                                     _rank=same_day_rank(direction))
        direction = direction.sort_values(["_rank", "_created"], kind="stable")
    first = direction.iloc[0]
    run = rows[rows["run_id"] == first["run_id"]]
    headline = is_headline_model(run["model"])
    opens = run[(run["kind"] == "open") & headline]
    prices = run[(run["kind"] == "price") & headline & run.get("horizon_days", pd.Series(np.nan, index=run.index)).notna()]
    return {"run_id": str(first["run_id"]), "created_at_utc": first.get("created_at_utc"),
            "direction": {str(r["model"]): r.to_dict() for _, r in run[run["kind"] == "direction"].iterrows()},
            "open": opens.iloc[0].to_dict() if len(opens) else None,
            "price": {int(r["horizon_days"]): r.to_dict() for _, r in prices.iterrows()}}


def apply_official_forecast(official, live_table, open_row, price_rows, band):
    """official_forecast 의 값으로 화면에 보일 예측을 바꾼다. 받은 객체는 건드리지 않고 새 객체를 돌려준다.

    방향은 원장에 있는 모델만, 가격은 같은 거래일 수의 행만 바꾼다. 원장에 없는 것은 새로 계산한 값을 둔다.
    반환: (live_table, open_row, price_rows, band)
    """
    table = live_table.copy()
    for model, row in official["direction"].items():
        if model not in table.index:
            continue
        for column in ("prediction", "p_down", "p_flat", "p_up"):
            if column in table.columns and pd.notna(row.get(column)):
                table.loc[model, column] = row[column]

    def merge(fresh, recorded, fields):
        merged = dict(fresh)
        for field in fields:
            if field not in recorded:
                continue
            value = recorded[field]
            if field in _NULL_MEANS_NO_FORECAST:
                merged[field] = np.nan if pd.isna(value) else value
            elif not pd.isna(value):
                merged[field] = value
        return merged

    opened = merge(open_row, official["open"], _OPEN_FORECAST_FIELDS) if official.get("open") else dict(open_row)
    prices = [merge(row, official["price"][row.get("trading_days")], _PRICE_FORECAST_FIELDS)
              if row.get("trading_days") in official["price"] else dict(row) for row in price_rows]
    bands = [row.get("band") for row in official["direction"].values() if _finite(row.get("band")) is not None]
    return table, opened, prices, (float(bands[0]) if bands else band)


def official_forecast_note(official):
    """다시 만든 보고서가 어떤 예측을 보여 주는지 한 문장으로."""
    created = pd.to_datetime(official.get("created_at_utc"), utc=True, errors="coerce")
    when = f"{created.tz_convert('Asia/Seoul'):%m-%d %H:%M} KST" if pd.notna(created) else "그날 아침"
    return (f"보고서는 다시 만들었지만 예측은 원장에 기록된 공식 사전 예측({when} 실행 {official['run_id']})을 "
            "그대로 보여 줍니다. 채점도 이 예측으로 합니다. 이번에 새로 학습한 모델의 값은 원장에 기록되지 않으므로 "
            "보여 주지 않습니다.")


# ---------------------------------------------------------------------------
# 장기 전망 탭의 쉬운 요약
# ---------------------------------------------------------------------------
# 오늘의 장 예측 탭처럼 장기 전망 탭에도 맨 위 요약을 둔다(2026-09-13 요청). 이번 분기 영업이익 추정을 크게,
# 앞으로의 흐름을 짧게, 30만·40만 원 같은 딱 떨어지는 가격에 언제쯤 닿을 수 있는지를 확률로 보인다.
# 새 모델을 만들지 않는다. 월간 도구가 계산해 둔 값(longterm.json·earnings.json)과 오늘까지의 종가만 쓴다.
# 가격 도달 시점은 예측이 아니라 변동성 모의실험이다. 장기 주가 모델이 '변화 없음'을 이긴다는 근거가 없으므로
# 추세를 넣지 않은 경우를 기본으로, 과거 같은 반도체 국면의 1년 중앙값을 추세로 넣은 경우를 참고로 둔다.
MONTH_TRADING_DAYS = 21
YEAR_TRADING_DAYS = 252
_TONE_CHIP = {"up": ("좋은 신호", "#e6f2ea", "#1e6b34"), "down": ("조심할 신호", "#fbeaea", "#a8322a"),
              "": ("참고", "#f1f2f4", "#6b7178")}


def round_price_levels(current, count=2):
    """현재가 위의 딱 떨어지는 가격들. 25.95만 원이면 30만·40만 원, 181.2만 원이면 200만·300만 원."""
    current = _finite(current)
    if current is None or current <= 0:
        return []
    step = 10.0 ** np.floor(np.log10(current))
    first = (np.floor(current / step) + 1) * step
    return [float(first + step * i) for i in range(count)]


def level_reach_odds(close, levels, *, daily_drift=0.0, horizon_days=2 * YEAR_TRADING_DAYS,
                     lookback_days=YEAR_TRADING_DAYS, n_paths=20000, seed=20260913):
    """가격마다 '종가가 한 번이라도 닿을' 확률이 시간이 지나며 어떻게 오르는지 모의실험한다.

    최근 lookback_days 거래일의 일간 로그수익률에서 평균을 뺀 값을 복원추출해 경로를 만든다. 지금의 변동성과
    두꺼운 꼬리는 그대로 쓰되, 지난 1년의 추세가 앞으로도 이어진다고 가정하지 않는다(평균을 빼는 이유).
    추세는 daily_drift(하루 로그수익률 — 스칼라 또는 길이 horizon_days 배열)로만 넣는다.
    같은 seed 면 같은 결과이고, 시나리오끼리 같은 seed 를 쓰면 추세 차이만 비교된다.

    반환: 종가가 61개 미만이거나 가격이 없으면 None. 아니면
      {"current", "annual_vol", "horizon_days",
       "levels": [{"level", "change", "curve"(1~horizon 거래일 누적 도달 확률), "half_day"(처음 50% 이상이
                   되는 거래일 수, 기간 안에 없으면 None)}]}
    이미 넘은 가격은 첫날부터 도달한 것으로 센다.
    """
    prices = pd.Series(close).astype(float)
    prices = prices[np.isfinite(prices) & (prices > 0)]
    levels = [float(level) for level in (levels or []) if _finite(level) is not None and float(level) > 0]
    if len(prices) < 61 or not levels:
        return None
    horizon = int(horizon_days)
    returns = np.diff(np.log(prices.to_numpy()))[-int(lookback_days):]
    shocks = returns - returns.mean()
    drift = np.broadcast_to(np.asarray(daily_drift, dtype=float), (horizon,))
    rng = np.random.default_rng(seed)
    targets = np.log(np.asarray(levels))[:, None]
    position = np.full(int(n_paths), np.log(prices.iloc[-1]))
    first_hit = np.where(position[None, :] >= targets, 0, horizon + 1)
    # 날짜별 가격 분포(2026-09-30: 가로 시간·세로 주가 그래프용). 5거래일마다 분위수만 적는다 — 난수를 더 쓰지
    # 않으므로 도달 확률은 전과 같다.
    band_days, band_rows = [0], [np.full(5, float(prices.iloc[-1]))]
    for day in range(1, horizon + 1):
        position += drift[day - 1] + shocks[rng.integers(0, shocks.size, position.size)]
        first_hit[(first_hit > horizon) & (position[None, :] >= targets)] = day
        if day % 5 == 0 or day == horizon:
            band_days.append(day)
            band_rows.append(np.exp(np.quantile(position, [.1, .25, .5, .75, .9])))
    days = np.arange(1, horizon + 1)
    current = float(prices.iloc[-1])
    rows = []
    for level, hits in zip(levels, first_hit):
        curve = np.searchsorted(np.sort(hits), days, side="right") / hits.size
        rows.append({"level": level, "change": level / current - 1, "curve": curve,
                     "half_day": int(days[np.argmax(curve >= .5)]) if curve[-1] >= .5 else None})
    bands = np.vstack(band_rows)
    return {"current": current, "annual_vol": float(shocks.std(ddof=1) * np.sqrt(YEAR_TRADING_DAYS)),
            "horizon_days": horizon, "levels": rows,
            "bands": {"days": band_days, **{name: bands[:, i].tolist()
                                             for i, name in enumerate(("p10", "p25", "p50", "p75", "p90"))}}}


def _trillion(value):
    value = _finite(value)
    return "—" if value is None else f"{value / 1e12:,.1f}조 원"


def _man_won(value):
    """300000 → '30만원'. 만 원 단위로 떨어지지 않으면 원 단위 그대로."""
    man = float(value) / 1e4
    return f"{man:,.0f}만원" if abs(man - round(man)) < 1e-6 else f"{float(value):,.0f}원"


def _when(price_date, trading_days):
    """거래일 수를 '약 N개월 뒤 (YYYY년 M월경)'로. 달력 날짜는 1년 252거래일로 환산한 대략값이다."""
    months = trading_days / MONTH_TRADING_DAYS
    text = "한 달 안" if months < 1 else f"약 {months:.0f}개월 뒤"
    try:
        day = pd.Timestamp(price_date) + pd.Timedelta(days=round(trading_days * 365.25 / YEAR_TRADING_DAYS))
    except (TypeError, ValueError):
        return text
    return text if pd.isna(day) else f"{text} ({day.year}년 {day.month}월경)"


LEVEL_ODDS_HORIZON = 2 * YEAR_TRADING_DAYS



def level_fan_svg(base, price_date=None):
    """가로 시간·세로 주가인 예측 부채꼴(2026-09-30 요청: 30만·40만 원을 한 그래프에).

    모의실험 경로의 날짜별 분포를 띠로 그린다 — 옅은 띠 10~90%, 진한 띠 25~75%, 선은 가운데 값. 30만·40만 원은
    가로 점선이고, 선 옆에 '한 번이라도 닿을 확률'(2년 안)을 적는다. 띠는 '그 날의 가격'이고 닿을 확률은
    '그때까지 한 번이라도'라서 닿을 확률이 더 높다. base 는 level_reach_odds 결과(bands 포함).
    """
    import math
    from html import escape
    bands = (base or {}).get("bands")
    if not bands or len(bands.get("days", [])) < 2:
        return ""
    days = [int(d) for d in bands["days"]]
    horizon = max(days)
    levels = [row for row in base.get("levels", []) if row.get("change", 0) > 0]
    top_values = bands["p90"] + [row["level"] for row in levels]
    lo_raw, hi_raw = min(bands["p10"]) * .95, max(top_values) * 1.04
    span = hi_raw - lo_raw
    step = next(s for s in (10_000, 20_000, 50_000, 100_000, 200_000, 500_000, 1_000_000, 2_000_000, 5_000_000)
                if span / s <= 6)
    lo, hi = step * math.floor(lo_raw / step), step * math.ceil(hi_raw / step)
    left, right, top, bottom = 70, 600, 30, 226
    def x(day):
        return left + day / horizon * (right - left)
    def y(price):
        return bottom - (price - lo) / (hi - lo) * (bottom - top)
    grid = ""
    tick = lo
    while tick <= hi + 1:
        grid += (f'<line x1="{left}" y1="{y(tick):.1f}" x2="{right}" y2="{y(tick):.1f}" stroke="#eceef1"/>'
                 f'<text x="{left - 8}" y="{y(tick) + 4:.1f}" font-size="11" text-anchor="end" fill="#6b7178">'
                 f'{escape(_man_won(tick))}</text>')
        tick += step
    for month, name in ((0, "지금"), (6, "6개월"), (12, "1년"), (18, "18개월"), (24, "2년")):
        day = min(month * MONTH_TRADING_DAYS, horizon)
        when = ""
        if price_date is not None and month:
            try:
                stamp = pd.Timestamp(price_date) + pd.Timedelta(days=round(day * 365.25 / YEAR_TRADING_DAYS))
                when = f"{stamp.year % 100:02d}.{stamp.month}"
            except (TypeError, ValueError):
                when = ""
        grid += (f'<text x="{x(day):.1f}" y="{bottom + 16}" font-size="11" text-anchor="middle" fill="#6b7178">{name}</text>'
                 + (f'<text x="{x(day):.1f}" y="{bottom + 29}" font-size="10" text-anchor="middle" fill="#a3a9b0">{when}</text>'
                    if when else ""))
    def band(upper, lower, color, opacity):
        points = [f"{x(d):.1f},{y(v):.1f}" for d, v in zip(days, bands[upper])]
        points += [f"{x(d):.1f},{y(v):.1f}" for d, v in zip(reversed(days), reversed(bands[lower]))]
        return f'<polygon points="{" ".join(points)}" fill="{color}" opacity="{opacity}"/>'
    median = " ".join(f"{x(d):.1f},{y(v):.1f}" for d, v in zip(days, bands["p50"]))
    shapes = (band("p90", "p10", "#b5d4f4", .55) + band("p75", "p25", "#7fb0e0", .6)
              + f'<polyline points="{median}" fill="none" stroke="#1a5490" stroke-width="2"/>'
              + f'<circle cx="{x(0):.1f}" cy="{y(base["current"]):.1f}" r="4.5" fill="#1a1a1a"/>'
              + f'<text x="{x(0) + 8:.1f}" y="{y(base["current"]) + 18:.1f}" font-size="11" fill="#1a1a1a">'
              f'지금 {base["current"]:,.0f}원</text>')
    colors = ("#b3541e", "#7a3f9d", "#2f7d4f")
    for index, row in enumerate(levels):
        color = colors[index % len(colors)]
        line_y = y(row["level"])
        reach = float(row["curve"][-1]) if len(row.get("curve", [])) else None
        shapes += (f'<line x1="{left}" y1="{line_y:.1f}" x2="{right}" y2="{line_y:.1f}" stroke="{color}" '
                   'stroke-width="1.6" stroke-dasharray="6 4"/>'
                   f'<text x="{right + 6}" y="{line_y + 4:.1f}" font-size="12" font-weight="700" fill="{color}">'
                   f'{escape(_man_won(row["level"]))}</text>')
        if reach is not None:
            shapes += (f'<text x="{right - 4}" y="{line_y - 6:.1f}" font-size="11" text-anchor="end" fill="{color}">'
                       f'2년 안 한 번이라도 닿을 확률 {reach:.0%}</text>')
        if row.get("half_day"):
            shapes += (f'<circle cx="{x(int(row["half_day"])):.1f}" cy="{line_y:.1f}" r="4.5" fill="{color}" '
                       'stroke="#fff" stroke-width="1.5"/>')
    legend = ('<text x="70" y="14" font-size="11" fill="#3a4652">'
              '<tspan fill="#7fb0e0">■</tspan> 50% 범위  <tspan fill="#b5d4f4">■</tspan> 80% 범위  '
              '<tspan fill="#1a5490">━</tspan> 가운데 값  <tspan fill="#8a9199">●</tspan> 닿을 확률이 절반을 넘는 때</text>')
    return (f'<svg viewBox="0 0 660 {bottom + 36}" width="100%" style="max-width:660px;min-width:380px;display:block;margin:4px 0 10px" '
            f'role="img" aria-label="주가 예측 범위와 목표 가격">{legend}{grid}'
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="#c3c8cf"/>'
            f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#c3c8cf"/>{shapes}</svg>')

def level_odds_svg(base, months_per_tick=6):
    """가격 도달 확률 곡선(2026-09-30 요청: '30만·40만 원은 언제쯤?'을 그림으로).

    가로축은 지금부터 2년, 세로축은 '그때까지 한 번이라도 닿았을 확률'. 50% 선을 점선으로 긋고 곡선이 그 선을
    넘는 때에 점을 찍는다. 이미 넘은 가격은 그리지 않는다. base 는 level_reach_odds 결과.
    """
    from html import escape
    rows = [row for row in (base or {}).get("levels", []) if row.get("change", 0) > 0 and len(row.get("curve", [])) > 1]
    if not rows:
        return ""
    colors = ("#1a5490", "#b3541e", "#2f7d4f")
    left, right, top, bottom = 58, 610, 34, 214
    days = max(len(row["curve"]) for row in rows)
    def x(day):
        return left + day / days * (right - left)
    def y(prob):
        return bottom - prob * (bottom - top)
    grid = ""
    for prob in (0, .25, .5, .75, 1):
        dash = ' stroke-dasharray="5 4"' if prob == .5 else ""
        color = "#8a9199" if prob == .5 else "#e5e7eb"
        grid += (f'<line x1="{left}" y1="{y(prob):.1f}" x2="{right}" y2="{y(prob):.1f}" stroke="{color}"{dash}/>'
                 f'<text x="{left - 8}" y="{y(prob) + 4:.1f}" font-size="11" text-anchor="end" fill="#6b7178">{prob:.0%}</text>')
    labels = {0: "지금", 6: "6개월", 12: "1년", 18: "18개월", 24: "2년"}
    months = round(days / MONTH_TRADING_DAYS)
    for month in range(0, months + 1, months_per_tick):
        day = min(month * MONTH_TRADING_DAYS, days)
        grid += (f'<line x1="{x(day):.1f}" y1="{bottom}" x2="{x(day):.1f}" y2="{bottom + 4}" stroke="#c3c8cf"/>'
                 f'<text x="{x(day):.1f}" y="{bottom + 18}" font-size="11" text-anchor="middle" fill="#6b7178">'
                 f'{labels.get(month, f"{month}개월")}</text>')
    lines, legend = "", ""
    for index, row in enumerate(rows):
        color = colors[index % len(colors)]
        curve = np.asarray(row["curve"], dtype=float)
        step = max(1, len(curve) // 120)
        points = [(0, 0.0)] + [(d + 1, float(curve[d])) for d in range(0, len(curve), step)]
        if points[-1][0] != len(curve):
            points.append((len(curve), float(curve[-1])))
        path = " ".join(f"{x(d):.1f},{y(p):.1f}" for d, p in points)
        lines += f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2.5"/>'
        end = float(curve[-1])
        lines += (f'<text x="{right + 6}" y="{y(end) + 4:.1f}" font-size="12" font-weight="700" fill="{color}">'
                  f'{end:.0%}</text>')
        if row.get("half_day"):
            half = int(row["half_day"])
            lines += (f'<circle cx="{x(half):.1f}" cy="{y(.5):.1f}" r="5" fill="{color}" stroke="#fff" stroke-width="1.5"/>'
                      f'<text x="{x(half):.1f}" y="{y(.5) - 9:.1f}" font-size="11" text-anchor="middle" fill="{color}">'
                      f'약 {max(1, round(half / MONTH_TRADING_DAYS))}개월</text>')
        legend += (f'<rect x="{left + index * 190}" y="6" width="14" height="4" rx="2" fill="{color}" transform="translate(0,4)"/>'
                   f'<text x="{left + index * 190 + 20}" y="14" font-size="12" fill="#1a1a1a">'
                   f'{escape(_man_won(row["level"]))} (지금보다 {row["change"]:+.0%})</text>')
    return (f'<svg viewBox="0 0 660 {bottom + 30}" width="100%" style="max-width:660px;min-width:360px;display:block;margin:4px 0 10px" '
            'role="img" aria-label="가격 도달 확률 곡선">'
            f'{legend}{grid}<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="#c3c8cf"/>{lines}</svg>')
LEVEL_ODDS_LOOKBACK = 3 * YEAR_TRADING_DAYS


def summary_prices(close):
    """요약의 가격 도달 확률이 쓰는 종가(유효한 양수만)."""
    prices = pd.Series(close if close is not None else [], dtype=float)
    return prices[np.isfinite(prices) & (prices > 0)]


def summary_level_odds(close, levels=None, n_paths=20000, seed=20260913):
    """요약 카드의 '추세 없음' 가격 도달 확률과 같은 값(같은 입력·같은 seed). 전망 원장이 기록에 쓴다."""
    prices = summary_prices(close)
    current = float(prices.iloc[-1]) if len(prices) else None
    levels = [float(level) for level in levels] if levels else round_price_levels(current)
    return level_reach_odds(prices, levels, daily_drift=0.0, horizon_days=LEVEL_ODDS_HORIZON,
                            lookback_days=LEVEL_ODDS_LOOKBACK, n_paths=n_paths, seed=seed)


def leading_cycle_phase_line(path="macro_history/leading_cycle.csv", window=24):
    """통계청 선행지수 순환변동치의 국면 한 줄(2026-10-01). 규칙은 tools/cycle_phase.py 와 같다:
    최근 24개월 고점 뒤 1개월 하락이면 '정점 가능성', 2개월 이상이면 '하락 국면 전환'(저점 쪽도 같음). 없으면 ''."""
    import os
    if not os.path.exists(path):
        return ""
    try:
        frame = pd.read_csv(path)
        stamps = pd.to_datetime(frame.iloc[:, 0], errors="coerce")
        cycle = pd.Series(pd.to_numeric(frame.iloc[:, -1], errors="coerce").to_numpy(dtype=float), index=stamps)
        cycle = cycle[cycle.index.notna()].dropna().sort_index()
    except (OSError, ValueError):
        return ""
    if len(cycle) < 6:
        return ""
    recent = cycle.tail(window)
    last_month, last = cycle.index[-1], float(cycle.iloc[-1])
    peak_month, trough_month = recent.idxmax(), recent.idxmin()
    down = int((cycle.index > peak_month).sum())
    up = int((cycle.index > trough_month).sum())
    if peak_month > trough_month:
        state = ("상승 중(최근 고점 경신)" if down == 0 else "정점 가능성(고점 뒤 1개월 하락)" if down == 1
                 else f"하락 국면 전환(고점 뒤 {down}개월 연속)")
    else:
        state = ("하락 중(최근 저점 경신)" if up == 0 else "저점 가능성(저점 뒤 1개월 상승)" if up == 1
                 else f"상승 국면 전환(저점 뒤 {up}개월 연속)")
    return (f"경기 국면(통계청 선행지수 순환변동치) — {last_month.year}년 {last_month.month}월 {last:.1f}, {state}. "
            f"최근 고점 {float(recent.max()):.1f}({peak_month.year}년 {peak_month.month}월). "
            "자세한 판정과 국면별 코스피 성과는 거시 경제 보고서의 '경기 국면' 절에 있습니다.")


def longterm_easy_summary_html(*, name, price_date, close, longterm=None, earnings=None, levels=None,
                               n_paths=20000, seed=20260913):
    """장기 전망 탭 맨 위의 쉬운 요약: 이번 분기 영업이익 추정(강조), 앞으로의 흐름, 가격 도달 시점.

    검증 문(門)을 그대로 따른다 — 영업이익은 기준선을 이긴 추정만 숫자로, 장기 주가 모델은 '변화 없음'을
    이긴 지평만 수익률로 적는다. 가격 도달 시점은 예측이 아니라 변동성 모의실험(level_reach_odds)이다.
    제목은 h3 하나뿐이다 — 탭 나누기가 h3 로 절을 자르므로 안쪽 블록은 div 로만 만든다.
    """
    from html import escape

    def mapping(value):
        return value if isinstance(value, dict) else {}

    longterm, earnings = mapping(longterm), mapping(earnings)

    def passes(item):
        return bool(mapping(item.get("evaluation")).get("beats_baselines")
                    and _finite(item.get("point")) is not None and not item.get("no_point_reason"))

    def band(item):
        if item.get("estimate_basis") == "partial_month_scenario":
            return "속보 기반 시나리오 · 구간 미검증"
        point, low, high = (_finite(item.get(key)) for key in ("point", "low", "high"))
        if low is None or high is None:
            return "구간 없음"
        text = f"80% 구간 {low / 1e12:,.1f}~{high / 1e12:,.1f}조 원"
        if point is not None and low > point:
            text += " · 과거에 실제가 추정보다 컸던 경향이 있어 구간이 추정보다 위에 있습니다"
        elif point is not None and high < point:
            text += " · 과거에 실제가 추정보다 작았던 경향이 있어 구간이 추정보다 아래에 있습니다"
        return text

    def card(label, value, note, big=False, muted=False):
        size = 16 if muted else (26 if big else 19)
        return (f'<div style="{_CARD};flex:{"2 1 220px" if big else "1 1 150px"}">'
                f'<div style="font-size:11px;color:#7a8797">{escape(label)}</div>'
                f'<div style="font-size:{size}px;font-weight:700;line-height:1.3;'
                f'color:{"#8a9199" if muted else "#1a1a1a"}">{escape(value)}</div>'
                f'<div style="font-size:11px;color:#7a8797;line-height:1.45">{escape(note)}</div></div>')

    def box(title, subtitle, body):
        return (f'<div style="{_BOX}">'
                f'<div style="font-size:14px;font-weight:700;margin-bottom:8px">{escape(title)}'
                + (f' <span style="font-weight:400;color:#7a8797;font-size:12px">{escape(subtitle)}</span>'
                   if subtitle else '') + f'</div>{body}</div>')

    # ---- 1) 이번 분기 영업이익 ----------------------------------------------------
    quarter = str(earnings.get("quarter") or "이번 분기")
    point, last = _finite(earnings.get("point")), _finite(earnings.get("last_actual"))
    next_q = mapping(earnings.get("next_quarter"))
    next_label = str(next_q.get("quarter") or "다음 분기")
    change = point / last - 1 if passes(earnings) and last else None
    next_change = (_finite(next_q.get("point")) / point - 1) if passes(earnings) and passes(next_q) else None
    if not earnings:
        profit_html = '<div style="font-size:13px;color:#6b7178">영업이익 추정 자료를 확인하지 못했습니다.</div>'
    else:
        cards = [card(f"{quarter} 영업이익 추정", _trillion(point), band(earnings), big=True)
                 if passes(earnings) else
                 card(f"{quarter} 영업이익 추정", "예측하기 어렵습니다",
                      str(earnings.get("no_point_reason") or "검증에서 기준선을 이기지 못했습니다"),
                      big=True, muted=True)]
        if last is not None:
            cards.append(card(f"직전 분기 실제 · {earnings.get('last_actual_quarter') or ''}", _trillion(last),
                              f"이번 분기 추정은 이보다 {change:+.1%}" if change is not None else "비교할 추정 없음"))
        # 부문 분리 추정(삼성전자, 2026-09-29 사용자 결정). 검증 전이라 기존 추정 옆에 따로 둔다.
        segment = mapping(earnings.get("segment_split"))
        if _finite(segment.get("low")) is not None and _finite(segment.get("high")) is not None:
            cards.append(card(f"{quarter} 부문 분리 추정", f"{segment['low'] / 1e12:,.1f}~{segment['high'] / 1e12:,.1f}조 원",
                              "DS 이익을 따로 늘려 잡은 값 · 검증 전 시나리오"))
        if next_q:
            cards.append(card(f"{next_label} 추정", _trillion(next_q.get("point")),
                              (f"이번 분기 추정 대비 {next_change:+.1%} · " if next_change is not None else "")
                              + band(next_q))
                         if passes(next_q) else
                         card(f"{next_label} 추정", "예측하기 어렵습니다", "검증에서 기준선을 이기지 못했습니다",
                              muted=True))
        basis = [str(earnings["interval_note"])] if earnings.get("interval_note") else []
        if earnings.get("months_included"):
            basis.append(f"{earnings['months_included']} 반도체 수출 반영")
        for flash in earnings.get("flash_applied") or []:
            if isinstance(flash, dict) and flash.get("month"):
                basis.append(f"{str(flash['month'])[-2:].lstrip('0')}월은 1~{flash.get('days')}일 관세청 속보")
        reason = mapping(mapping(earnings.get("provisional_info")).get(earnings.get("quarter_code"))).get("reason")
        if reason:
            basis.append(str(reason))
        basis.append("회사 발표나 증권사 전망 평균이 아닌 자체 모델 추정")
        profit_html = (f'<div style="display:flex;gap:8px;flex-wrap:wrap">{"".join(cards)}</div>'
                       '<div style="font-size:11px;color:#8a9199;margin-top:6px;line-height:1.5">'
                       f'{escape(" · ".join(basis))}</div>')

    # ---- 2) 앞으로 어떻게 될까 -------------------------------------------------------
    signals = []
    if change is not None:
        text = f"이익 — {quarter} 추정이 직전 분기보다 {change:+.0%}"
        if next_change is not None:
            text += f", {next_label} 추정은 그보다 {next_change:+.0%}"
        tone = ("up" if change > 0 and (next_change is None or next_change > 0) else
                "down" if change < 0 and (next_change is None or next_change < 0) else "")
        signals.append((tone, text + "입니다."))
    evaluation, forecast = mapping(longterm.get("evaluation")), mapping(longterm.get("forecast"))
    passed, pending = [], []
    for months in ("3", "6", "12"):
        value = _finite(mapping(forecast.get(months)).get("point"))
        if mapping(evaluation.get(months)).get("beats_zero") and value is not None:
            passed.append((months, float(np.expm1(value))))     # 모델 타깃은 로그수익률
        else:
            pending.append(months)
    if longterm:
        if passed:
            text = ("장기 주가 모델 — " + " · ".join(f"{m}개월 뒤 {r:+.1%}" for m, r in passed)
                    + " 예상(검증 통과)")
            if pending:
                text += f", {'·'.join(pending)}개월은 판단 근거 부족"
            signals.append(("up" if passed[0][1] > 0 else "down", text + "."))
        else:
            signals.append(("", "장기 주가 모델 — 3·6·12개월 모두 '변화 없음'보다 낫다는 근거가 없어 "
                                "방향을 말하지 않습니다."))
    state, duration = mapping(longterm.get("current")), mapping(longterm.get("duration"))
    live = mapping(longterm.get("live_exports"))
    if live:
        state = {**state, **mapping(live.get("current")), "phase": live.get("phase")}
        duration = {}  # 속보 국면에 월간 검증의 지속기간을 붙이지 않는다.
        period = mapping(live.get("latest_period"))
        period_label = ("월말 잠정치" if period.get("basis") == "full_month_preliminary" else
                        f"1~{period['days']}일 잠정치" if period.get("days") else "월간 자료")
        signals.append(("", f"최신 수출 반영 — {live.get('exports_last_month')} {period_label}, "
                        f"{live.get('generated_at')} 확인. 월말 가격을 고정한 수출 민감도이며 "
                        "속보 반영 전망은 별도 검증 전입니다."))
    phase = duration.get("phase") or state.get("phase")
    phase_row = next((row for row in (mapping(longterm.get("phases")).get("12") or [])
                      if isinstance(row, dict) and row.get("phase") == phase
                      and _finite(row.get("median")) is not None), None)
    if live:
        phase_row = None
    if phase:
        text, tone = f"반도체 수출 사이클 — {str(phase).split('(')[0]} 국면", ""
        months_so_far = _finite(duration.get("months_so_far"))
        if months_so_far is not None:
            text += f" {months_so_far:.0f}개월째"
        remaining = _finite(duration.get("remaining_median"))
        n_past, n_longer = _finite(duration.get("n_past")), _finite(duration.get("n_conditional"))
        if remaining is not None:
            text += f", 과거 같은 국면은 이 시점에서 {remaining:.0f}개월쯤 더 갔습니다"
        elif n_past and n_longer is not None:
            text += f", 과거 {n_past:.0f}번 중 이보다 길었던 것은 {n_longer:.0f}번뿐이라 국면 후반일 수 있습니다"
            tone = "down" if n_longer / n_past < .25 else ""
        if phase_row:
            median = float(np.expm1(phase_row["median"]))
            text += (f". 과거 같은 국면에서 1년 뒤 주가 중앙값 {median:+.0%}, 오른 비율 "
                     f"{float(phase_row.get('positive_share') or 0):.0%}({int(phase_row.get('n') or 0)}개월)")
            tone = "down" if median < 0 else ("up" if median > .05 and tone != "down" else tone)
        signals.append((tone, text + "."))
    # 통계청 선행지수 순환변동치의 국면(2026-10-01: 거시 영상의 판단 방식). 보관본이 있을 때만.
    cycle_line = leading_cycle_phase_line() if longterm else ""   # 장기 자료가 없으면 '확인하지 못함' 안내를 남긴다
    if cycle_line:
        signals.append(("down" if ("하락" in cycle_line or "정점" in cycle_line) else "", cycle_line))
    change_6m = _finite(mapping(longterm.get("cli_outlook")).get("change_6m"))
    if change_6m is not None:
        signals.append(("up" if change_6m >= .3 else "down" if change_6m <= -.3 else "",
                        f"경기선행지수(OECD 한국) — 앞으로 6개월 동안 {abs(change_6m):.1f}포인트 "
                        f"{'오를' if change_6m > 0 else '내릴'} 것으로 전망됩니다."))
    z, momentum = _finite(state.get("price_to_exports_z")), _finite(state.get("mom_12m"))
    if z is not None:
        text = (f"가격 부담 — 수출 대비 주가가 5년 평균보다 {z:+.1f}σ로 "
                f"{'비싼' if z > 1 else '싼' if z < -1 else '보통인'} 편")
        if momentum is not None:
            text += f"(지난 1년 주가 {np.expm1(momentum):+.0%})"
        signals.append(("down" if z > 1 else "up" if z < -1 else "", text + "입니다."))
    if signals:
        ups = sum(tone == "up" for tone, _ in signals)
        downs = sum(tone == "down" for tone, _ in signals)
        signals_html = "".join(
            '<div style="display:flex;gap:8px;align-items:baseline;padding:6px 0;border-top:1px solid #eef1f4">'
            f'<span style="flex:0 0 72px;text-align:center;background:{_TONE_CHIP[tone][1]};'
            f'color:{_TONE_CHIP[tone][2]};font-size:11px;font-weight:700;padding:1px 0;border-radius:10px">'
            f'{_TONE_CHIP[tone][0]}</span>'
            f'<span style="font-size:13px;color:#3a4652;line-height:1.55">{escape(text)}</span></div>'
            for tone, text in signals)
        signals_html += (f'<div style="font-size:12px;color:#586575;margin-top:6px">좋은 신호 {ups}개 · '
                         f'조심할 신호 {downs}개. 이미 계산된 신호를 세어 본 것이며 매수·매도 의견이 아닙니다.</div>')
    else:
        signals_html = '<div style="font-size:13px;color:#6b7178">장기 자료를 확인하지 못했습니다.</div>'

    # ---- 3) 딱 떨어지는 가격에는 언제쯤 ------------------------------------------------
    prices = summary_prices(close)
    current = float(prices.iloc[-1]) if len(prices) else None
    levels = [float(level) for level in levels] if levels else round_price_levels(current)
    horizon = LEVEL_ODDS_HORIZON
    scenarios = [("추세 없음", 0.0)]
    if phase_row and int(phase_row.get("n") or 0) >= 20:
        drift = np.zeros(horizon)
        drift[:YEAR_TRADING_DAYS] = float(phase_row["median"]) / YEAR_TRADING_DAYS   # 1년만, 그 뒤는 추세 없음
        scenarios.append((f"과거 같은 국면의 1년 흐름({np.expm1(phase_row['median']):+.0%})이 이어지면", drift))
    # 등락은 최근 3년에서 뽑는다. 2026년처럼 1년 변동성이 이전의 2배를 넘는 해만 쓰면(삼성전자 74% vs 이전
    # 2년 30%) 2년 뒤까지의 도달 시점이 크게 앞당겨진다. 지평(2년)에 맞춰 더 긴 창을 쓰고 1년 값은 함께 적는다.
    lookback = LEVEL_ODDS_LOOKBACK
    used_days = min(lookback, max(len(prices) - 1, 0))
    recent = np.diff(np.log(prices.to_numpy()))[-YEAR_TRADING_DAYS:] if len(prices) > 21 else np.array([])
    recent_vol = float(recent.std(ddof=1) * np.sqrt(YEAR_TRADING_DAYS)) if recent.size > 20 else None
    runs = [(label, level_reach_odds(prices, levels, daily_drift=drift, horizon_days=horizon,
                                     lookback_days=lookback, n_paths=n_paths, seed=seed))
            for label, drift in scenarios]
    base = runs[0][1]
    if base is None:
        level_title = "딱 떨어지는 가격에는 언제쯤?"
        level_html = ('<div style="font-size:13px;color:#6b7178">종가 이력이 짧거나 가격이 없어 '
                      '계산하지 않았습니다.</div>')
    else:
        level_title = "·".join(_man_won(row["level"]) for row in base["levels"]) + "은 언제쯤?"
        level_cards = ""
        for index, row in enumerate(base["levels"]):
            curve = row["curve"]
            if row["change"] <= 0:
                head, odds, extra = "이미 넘었습니다", f"현재가 {base['current']:,.0f}원", ""
            else:
                head = (f"가능성이 절반을 넘는 때: {_when(price_date, row['half_day'])}" if row["half_day"]
                        else "2년 안에는 가능성이 절반을 넘지 않습니다")
                odds = (" · ".join(f"{label} 안 {curve[min(days, curve.size) - 1]:.0%}"
                                   for label, days in (("6개월", 126), ("1년", 252), ("2년", 504)))
                        + " (추세 없음)")
                extra = " / ".join(
                    f"{label}: " + (f"절반 넘는 때 {_when(price_date, other['half_day'])}" if other["half_day"]
                                    else "2년 안에 절반을 넘지 않음")
                    + f" · 1년 안 {other['curve'][min(252, other['curve'].size) - 1]:.0%}"
                    for label, run in runs[1:] for other in [run["levels"][index]])
            level_cards += (f'<div style="{_CARD};flex:1 1 240px">'
                            f'<div style="font-size:12px;color:#7a8797">{escape(_man_won(row["level"]))} · '
                            f'지금보다 {row["change"]:+.1%}</div>'
                            f'<div style="font-size:17px;font-weight:700;line-height:1.4;margin:2px 0">{escape(head)}</div>'
                            f'<div style="font-size:12px;color:#3a4652">{escape(odds)}</div>'
                            + (f'<div style="font-size:11px;color:#7a8797;margin-top:3px;line-height:1.45">'
                               f'{escape(extra)}</div>' if extra else '') + '</div>')
        model_note = (f"검증을 통과한 {'·'.join(m for m, _ in passed)}개월 주가 모델이 있지만 지평이 짧아 2년 추세로 "
                      "늘려 쓰지 않았습니다. " if passed else
                      "장기 주가 모델이 '변화 없음'보다 낫다는 근거가 없어 추세로 쓰지 않았습니다. ")
        # KRX 는 1년에 245거래일 안팎이라 3년이 756거래일에 못 미친다. 두 달 안쪽 차이는 3년으로 적는다.
        window_text = ("최근 3년" if used_days >= lookback - 2 * MONTH_TRADING_DAYS
                       else f"최근 {used_days:,}거래일")
        recent_text = (f", 최근 1년만 보면 {recent_vol:.0%}"
                       if recent_vol is not None and abs(recent_vol - base["annual_vol"]) >= .05 else "")
        level_html = ('<div style="overflow-x:auto">' + level_fan_svg(base, price_date) + '</div>'
                      f'<div style="display:flex;gap:8px;flex-wrap:wrap">{level_cards}</div>'
                      '<div style="font-size:11px;color:#8a9199;margin-top:6px;line-height:1.5">'
                      f'{window_text} 일간 등락(연 변동성 {base["annual_vol"]:.0%}{recent_text})에서 평균을 빼고 다시 뽑아 '
                      f'{int(n_paths):,}개 경로로 2년을 모의실험했습니다. 종가가 한 번이라도 그 가격에 닿을 확률이며, '
                      f'그 가격을 지킨다는 뜻이 아닙니다. {escape(model_note)}목표가나 매수·매도 의견이 아닙니다.</div>')

    stamp = " · ".join(part for part in (
        f"장기 자료 {longterm['as_of']} 기준" if longterm.get("as_of") else "",
        f"영업이익 추정 {str(earnings['generated_at'])[:10]} 계산" if earnings.get("generated_at") else "",
        f"주가 {_day_label(price_date) or ''} 종가 {current:,.0f}원" if current is not None else "") if part)
    return ('<section id="longterm-summary" aria-label="한눈에 보는 장기 전망 요약" '
            'style="background:#f0f6fc;border:1px solid #cedff0;border-radius:8px;padding:16px 20px;margin:0 0 20px">'
            '<h3 style="margin:0 0 6px;font-size:19px">한눈에 보는 장기 전망 요약</h3>'
            f'<div style="font-size:12px;color:#586575">{escape(str(name))}'
            + (f' · {escape(stamp)}' if stamp else '') + '</div>'
            + box("이번 분기 영업이익 추정", "", profit_html)
            + box("앞으로 어떻게 될까", "", signals_html)
            + box(level_title, "종가 기준 · 확률", level_html)
            + '</section>')


# ---- 지금 차트 상황 (2026-09-20) --------------------------------------------------------------
# "이제 반등이 나올 때가 됐나" 같은 질문에 과거 빈도로 답한다. 전날 종가까지의 봉만 쓰므로 저녁 보고서에도
# 그대로 나온다. 예측이 아니라 '같은 상황이 과거에 몇 번 있었고 다음 날 어땠나'를 센 것이다.
# 2026-09-20 점검(2010~2026, 두 종목): 며칠 급하게 빠진 뒤에는 다음 날 상승이 평소 36%에서 43~50%로
# 잦았고, RSI 30 미만·볼린저 하단 이탈 같은 교과서 지표는 통하지 않았다(2021년 이후 하단 이탈 뒤 상승 40%).
# 대표 모델 입력에 최근 수익률·RSI·이동평균 거리가 이미 있어, 그런 날 모델도 '상승'을 더 자주 낸다.
# 임계값은 점검 때 쓴 값을 그대로 고정한다 — 맞는 값을 찾아 조정하지 않는다.
CHART_SITUATION_SINCE = "2021-01-01"      # 대표 모델의 외부 평가 구간과 같은 시작
CHART_SITUATION_MIN_N = 20
CHART_SITUATION_BAND_MULT = 0.3           # 보고서의 상승·보합·하락 밴드(0.3 × 20일 변동성)와 같은 정의
_SITUATION_ORDER = ("down_streak", "drop_5d", "drop_1d", "off_high_20d", "up_streak", "rise_5d", "rise_1d")


def _run_length(flag):
    """True 가 이어진 길이(그 날 포함). False 면 0."""
    flag = flag.astype(int)
    return flag.groupby((flag != flag.shift()).cumsum()).cumsum() * flag


def chart_situation_signals(close):
    """날짜별 신호(그 날 종가까지의 정보만)와 그 근거 값. 반환: (신호 DataFrame, 값 DataFrame)."""
    close = pd.Series(close, dtype=float).dropna()
    ret = close.pct_change()
    down, up = _run_length(ret < 0), _run_length(ret > 0)
    ret5 = close / close.shift(5) - 1
    off_high = close / close.rolling(20).max() - 1
    signals = pd.DataFrame({"down_streak": down >= 3, "drop_5d": ret5 <= -.08, "drop_1d": ret <= -.04,
                            "off_high_20d": off_high <= -.10, "up_streak": up >= 3, "rise_5d": ret5 >= .08,
                            "rise_1d": ret >= .04})
    values = pd.DataFrame({"ret_1d": ret, "ret_5d": ret5, "off_high": off_high, "down_run": down, "up_run": up})
    return signals, values


def _situation_label(key, row):
    if key == "down_streak":
        return f"{int(row['down_run'])}일 연속 하락 중", "3일 이상 연속 하락"
    if key == "up_streak":
        return f"{int(row['up_run'])}일 연속 상승 중", "3일 이상 연속 상승"
    if key == "drop_5d":
        return f"최근 5거래일 {row['ret_5d']:+.1%}", "5거래일 −8% 이상 급락"
    if key == "rise_5d":
        return f"최근 5거래일 {row['ret_5d']:+.1%}", "5거래일 +8% 이상 급등"
    if key == "drop_1d":
        return f"직전 거래일 하루 {row['ret_1d']:+.1%}", "하루 −4% 이상 급락"
    if key == "rise_1d":
        return f"직전 거래일 하루 {row['ret_1d']:+.1%}", "하루 +4% 이상 급등"
    return f"20일 고점 대비 {row['off_high']:+.1%}", "20일 고점 대비 −10% 이상"


def chart_situation(bars, since=CHART_SITUATION_SINCE, min_n=CHART_SITUATION_MIN_N,
                    band_mult=CHART_SITUATION_BAND_MULT):
    """마지막 마감 봉 기준의 차트 상황과, 같은 상황 뒤 다음 거래일이 과거에 어땠는지.

    bars: 마감된 일봉(adj_close 가 있으면 그것, 없으면 close). 결과 분류는 보고서와 같다 — 다음 날 수익률이
    밴드(0.3 × 직전 20일 변동성)보다 크면 상승, 작으면 하락, 사이면 보합. 신호 날짜 s 의 결과는 s+1 거래일이고
    마지막 봉은 결과가 없어 통계에서 빠진다. since 이후 표본이 min_n 미만이면 전체 이력으로 넓히고 그렇게 적는다.
    반환: None(자료 부족) 또는 {"as_of", "facts", "base", "active": [...], "since"}.
    """
    if bars is None or len(bars) < 80:
        return None
    column = "adj_close" if "adj_close" in bars else "close"
    close = pd.Series(bars[column], dtype=float).dropna()
    close = close[close > 0]
    if len(close) < 80:
        return None
    signals, values = chart_situation_signals(close)
    ret = close.pct_change()
    band = (band_mult * ret.rolling(20).std()).shift(1)
    cls = pd.Series(np.where(ret < -band, 0., np.where(ret > band, 2., 1.)), index=close.index).where(band.notna() & ret.notna())
    nxt_cls, nxt_ret, nxt5 = cls.shift(-1), ret.shift(-1), close.shift(-5) / close - 1
    valid = nxt_cls.notna()

    def stats(mask):
        m = mask & valid
        n = int(m.sum())
        if n == 0:
            return {"n": 0}
        c = nxt_cls[m]
        return {"n": n, "up": float((c == 2).mean()), "flat": float((c == 1).mean()), "down": float((c == 0).mean()),
                "mean_next": float(nxt_ret[m].mean()), "mean_5d": float(nxt5[m].mean()) if nxt5[m].notna().any() else float("nan")}

    recent = pd.Series(close.index >= pd.Timestamp(since), index=close.index)
    everything = pd.Series(True, index=close.index)
    base_recent, base_all = stats(recent), stats(everything)
    last = close.index[-1]
    row = values.loc[last]
    active = []
    for key in _SITUATION_ORDER:
        if not bool(signals.loc[last, key]):
            continue
        detail, rule = _situation_label(key, row)
        found = stats(signals[key] & recent)
        window, base = f"{pd.Timestamp(since).year}년 이후", base_recent
        if found["n"] < min_n:
            found, window, base = stats(signals[key]), f"{close.index[0].year}년 이후 전체", base_all
        item = {"key": key, "detail": detail, "rule": rule, "window": window, "base": base, **found,
                "enough": found["n"] >= min_n, "side": "down" if key in ("down_streak", "drop_5d", "drop_1d", "off_high_20d") else "up"}
        if item["enough"] and base.get("n"):
            se = float(np.sqrt(base["up"] * (1 - base["up"]) / found["n"]))
            item["up_differs"] = bool(abs(found["up"] - base["up"]) > 2 * se)
            se_d = float(np.sqrt(base["down"] * (1 - base["down"]) / found["n"]))
            item["down_differs"] = bool(abs(found["down"] - base["down"]) > 2 * se_d)
        active.append(item)
    facts = {"ret_1d": float(row["ret_1d"]), "ret_5d": float(row["ret_5d"]), "off_high": float(row["off_high"]),
             "down_run": int(row["down_run"]), "up_run": int(row["up_run"])}
    return {"as_of": pd.Timestamp(last), "facts": facts, "base": base_recent, "active": active, "since": since}


def chart_situation_html(situation):
    """쉬운 요약의 '지금 차트 상황' 카드. h3 를 쓰지 않는다(탭 나누기가 요약을 쪼갠다)."""
    from html import escape
    if not situation:
        return ""
    facts, base, active = situation["facts"], situation.get("base") or {}, situation.get("active") or []
    box = ('<div style="background:#fff;border:1px solid #cedff0;border-radius:6px;padding:12px 14px;margin:10px 0 0;'
           'font-size:13px;line-height:1.7">')
    head = (f'<b>지금 차트 상황</b> <span style="color:#7a8797;font-size:11px">'
            f'{_day_label(situation["as_of"])} 종가 기준 · 과거에 같은 상황이 몇 번 있었고 다음 날 어땠는지 센 것 · 예측이 아닙니다</span>')
    if not active:
        # 기준일이 어제가 아닐 수 있다(주말·휴일 뒤 보고서). '직전 거래일'이라고 적는다.
        run = (f" · {facts['down_run']}일 연속 하락" if facts["down_run"] >= 2 else
               f" · {facts['up_run']}일 연속 상승" if facts["up_run"] >= 2 else "")
        return (box + head + f'<br>특별한 신호 없음 — 직전 거래일 {facts["ret_1d"]:+.1%} · 최근 5거래일 {facts["ret_5d"]:+.1%}{run} · '
                f'20일 고점 대비 {facts["off_high"]:+.1%}. 급락·급등이나 3일 이상 연속 움직임일 때만 과거 통계를 보입니다.</div>')
    rows = ""
    for item in active:
        if not item.get("enough"):
            rows += (f'<tr><td style="padding:5px 8px;border-top:1px solid #eef1f5"><b>{escape(item["detail"])}</b>'
                     f'<div style="font-size:11px;color:#7a8797">{escape(item["rule"])}</div></td>'
                     f'<td colspan="4" style="padding:5px 8px;border-top:1px solid #eef1f5;color:#7a8797">'
                     f'과거 표본 {item["n"]}번 — 말하기에 부족합니다</td></tr>')
            continue
        b = item["base"]
        if item.get("up_differs") and item["up"] > b["up"]:
            verdict, color = "다음 날 상승이 평소보다 잦았습니다", "#1e6b34"
        elif item.get("down_differs") and item["down"] > b["down"]:
            verdict, color = "다음 날 하락이 평소보다 잦았습니다", "#a8322a"
        elif item.get("up_differs") and item["up"] < b["up"]:
            verdict, color = "다음 날 상승이 평소보다 드물었습니다", "#a8322a"
        else:
            verdict, color = "평소와 뚜렷이 다르지 않았습니다", "#5b6570"
        five = "" if not np.isfinite(item.get("mean_5d", float("nan"))) else f' · 5거래일 뒤 평균 {item["mean_5d"]:+.1%}'
        rows += (f'<tr><td style="padding:5px 8px;border-top:1px solid #eef1f5"><b>{escape(item["detail"])}</b>'
                 f'<div style="font-size:11px;color:#7a8797">{escape(item["rule"])} · {escape(item["window"])} {item["n"]}번</div></td>'
                 f'<td style="padding:5px 8px;border-top:1px solid #eef1f5;text-align:right">{item["up"]:.0%}'
                 f'<div style="font-size:11px;color:#7a8797">평소 {b["up"]:.0%}</div></td>'
                 f'<td style="padding:5px 8px;border-top:1px solid #eef1f5;text-align:right">{item["flat"]:.0%}'
                 f'<div style="font-size:11px;color:#7a8797">{b["flat"]:.0%}</div></td>'
                 f'<td style="padding:5px 8px;border-top:1px solid #eef1f5;text-align:right">{item["down"]:.0%}'
                 f'<div style="font-size:11px;color:#7a8797">{b["down"]:.0%}</div></td>'
                 f'<td style="padding:5px 8px;border-top:1px solid #eef1f5;color:{color}">{verdict}'
                 f'<div style="font-size:11px;color:#7a8797">다음 날 평균 {item["mean_next"]:+.2%}{five}</div></td></tr>')
    table = ('<div style="overflow-x:auto"><table style="width:100%;min-width:480px;border-collapse:collapse;margin-top:6px">'
             '<tr style="font-size:11px;color:#6b7178;background:#fafafa"><th style="padding:5px 8px;text-align:left">상황</th>'
             '<th style="padding:5px 8px;text-align:right">다음 날 상승</th><th style="padding:5px 8px;text-align:right">보합</th>'
             '<th style="padding:5px 8px;text-align:right">하락</th><th style="padding:5px 8px;text-align:left">과거에는</th></tr>'
             f'{rows}</table></div>')
    note = ('<div style="font-size:11px;color:#7a8797;margin-top:6px">상승·보합·하락은 위 종가 방향과 같은 기준(밴드)입니다. '
            '신호가 여럿이면 같은 날들이 겹쳐 세어집니다. 대표 모델의 입력에 최근 수익률·RSI·이동평균 거리가 이미 들어 있어 '
            '이 경향은 위 예측 확률에 반영돼 있습니다 — 따로 더해 읽지 마세요. 반등의 상당 부분은 밤사이 갭으로 왔습니다.</div>')
    return box + head + table + note + "</div>"


def easy_summary_html(*, name, prediction_date, data_date, summary, open_forecast,
                      price_forecasts, review=None, longterm=None, earnings=None,
                      target_mode="close_to_close", record_forecast=True,
                      macro_active=True, nsi_active=True, official_note="",
                      post_open=None, target=None, situation=None):
    """Summarize already-computed results; never infer news causes or bypass signal gates.

    This is a generation-time snapshot. Intraday ledger refreshes remain separate and
    must not make the original forecast look as though it used later observations.
    official_note: 기록하지 않는 재실행이 원장의 공식 사전 예측을 보여 줄 때의 설명(official_forecast_note).
    post_open: 이 예측일의 시가 반영 갱신 행(post_open_row_for). 있으면 카드, 없으면 09:37 자리 표시를 둔다.
    target: 종목 키(samsung·sk_hynix) — 카드의 과거 검증 수치(POST_OPEN_TRACK_RECORD)용.
    situation: chart_situation(bars) 결과. 있으면 세 카드 아래에 '지금 차트 상황' 카드를 둔다.
    """
    from html import escape

    def number(value):
        try:
            result = float(value)
            return result if np.isfinite(result) else None
        except (TypeError, ValueError):
            return None

    def date_text(value):
        return str(value)[:10] if value is not None else "날짜 미확인"

    def mapping(value):
        return value if isinstance(value, dict) else {}

    sections = []
    live = summary.get("live", {})
    basis = "당일 시초가 대비" if target_mode == "open_to_close" else "전일 종가 대비"

    def price_text(row, field):
        point = number(row.get(field))
        if row.get("signal") != "있음" or point is None or point <= 0:
            return "예측하기 어렵습니다(검증 근거 부족)."
        return f"약 {point:,.0f}원. 확정 가격이 아닌 모델 예상입니다."

    # 전체 결론은 시초가가 앞이다. 이 모델이 실제로 맞히는 것은 갭(전일 종가→시가)이고, 종가 방향은
    # 확률이 기준(DIRECTION_ISSUE_MIN_PROB) 이상인 날만 낸다(2026-09-16). 유보한 날은 그렇다고 적는다.
    open_point = number(open_forecast.get("predicted_open"))
    if open_forecast.get("signal") == "있음" and open_point is not None and open_point > 0:
        open_change = number(open_forecast.get("predicted_return"))
        headline = (f"시초가 약 {open_point:,.0f}원"
                    + (f"({open_change:+.2%})" if open_change is not None else "") + " 예상.")
    else:
        headline = "시초가는 예측하지 않습니다(검증 근거 부족)."
    call = direction_call(live)
    if not call["valid"]:
        direction = "종가 방향을 판단하기 어렵습니다."
    elif call["issued"]:
        direction = (f"{basis} 종가 방향은 ‘{call['label'].strip('▼▲ ')}’ 쪽의 계산상 가능성이 가장 높습니다 "
                     f"({call['max_prob']:.1%}). 이 확률은 실제 적중률이 아닙니다.")
    else:
        direction = (f"종가 방향은 판단 유보 — 가장 높은 확률 {call['max_prob']:.1%}가 기준 "
                     f"{DIRECTION_ISSUE_MIN_PROB:.0%}에 못 미쳐 방향을 내지 않습니다.")
    sections.append(("전체 결론", f"{name} · {date_text(prediction_date)}: {headline} {direction}"))
    if not record_forecast:
        sections.append(("예측 상태", official_note or (
            "이번 실행의 예측은 원장에 기록되지 않는 참고값입니다. "
            "실제 성적은 별도로 저장된 장 시작 전 예측으로 평가합니다.")))

    # 2026-09-28 요청: 글이 많아 읽기 어렵다 — 가격은 범위 그림, 중장기·실적은 숫자 타일로 보이고,
    # 믿음 정도·주의할 점처럼 투자 판단에 덜 급한 설명은 작은 회색 글로 내린다. 공개 기준(검증을 통과하지
    # 못한 가격은 숫자를 내지 않음)은 그대로다 — 그림도 같은 문을 지난 값만 점으로 찍는다.
    price_block = price_range_html(open_forecast, price_forecasts, prediction_date=prediction_date)

    longterm = mapping(longterm)
    long_tiles = []
    for months in ("3", "6", "12"):
        forecast = mapping(mapping(longterm.get("forecast")).get(months))
        evaluation = mapping(mapping(longterm.get("evaluation")).get(months))
        point = number(forecast.get("point"))
        if evaluation.get("beats_zero") and point is not None:
            # Monthly model targets log returns; match the detail report's ordinary returns.
            change = float(np.expm1(point))
            long_tiles.append((f"{months}개월 뒤", f"{change:+.1%}", "주가 변화 예상", "up" if change > 0 else "down"))
        else:
            long_tiles.append((f"{months}개월 뒤", "근거 부족", "판단 근거 부족", "muted"))
    long_note = (f"{date_text(longterm.get('as_of'))} 기준. " if longterm else "장기 자료 미확인. ") + \
        "장기 전망은 매일 계산하는 단기 전망과 기준일이 다릅니다."

    earnings = mapping(earnings)
    earning_tiles, earning_notes = [], []
    for item in (earnings, mapping(earnings.get("next_quarter"))):
        if not item:
            continue
        quarter = str(item.get("quarter", "분기 미확인"))
        point = number(item.get("point"))
        if (mapping(item.get("evaluation")).get("beats_baselines") and point is not None
                and not item.get("no_point_reason")):
            label = "속보 기반 시나리오" if item.get("estimate_basis") == "partial_month_scenario" else "자체 모델 예상"
            earning_tiles.append((f"{quarter} 영업이익", f"약 {point / 1e12:,.1f}조 원", label, ""))
        else:
            earning_tiles.append((f"{quarter} 영업이익", "예측 어려움", "영업이익은 예측하기 어렵습니다", "muted"))
    if earnings:
        earning_notes.append("회사 발표나 증권사 전망 평균이 아닌 자체 모델의 추정입니다.")
        if earnings.get("interval_note"):
            earning_notes.append(str(earnings["interval_note"]))
        months_used = number(earnings.get("months_used"))
        if months_used is not None:
            earning_notes.append(f"분기 3개월 중 {months_used:.0f}개월 자료 반영.")
        if earnings.get("exports_last_month"):
            earning_notes.append(f"수출 자료 기준 {date_text(earnings['exports_last_month'])}.")
    else:
        earning_notes.append("실적 추정 자료를 확인하지 못했습니다.")

    confidence = ("과거 검증에서는 거래비용을 빼도 수익 가능성이 나타났지만, "
                  "앞으로의 수익을 보장하지 않습니다." if summary.get("session_tradeable") else
                  "과거 검증만으로는 장중 매매로 수익을 낼 만큼 정확하다는 근거가 부족합니다.")
    review = review or {}
    rolling = review.get("rolling")
    if isinstance(rolling, pd.DataFrame) and {"kind", "window", "n", "hit_rate"}.issubset(rolling.columns):
        direction_rows = rolling.loc[rolling["kind"] == "direction"].sort_values("window")
    else:
        direction_rows = pd.DataFrame()
    if not direction_rows.empty:
        row = direction_rows.iloc[0]
        n, hit = number(row["n"]), number(row["hit_rate"])
        if n is not None and n > 0 and hit is not None:
            confidence += (f" 실제 사전 예측은 최근 {n:.0f}일 중 방향 적중률 {hit:.1%} "
                           f"(마지막 채점 {date_text(review.get('latest_date'))}).")
            if n < 20:
                confidence += " 아직 자료가 적어 성능을 단정하기 어렵습니다."
        else:
            confidence += " 실제 사전 예측 성적은 아직 확인되지 않았습니다."
    else:
        confidence += " 실제 사전 예측 성적은 아직 확인되지 않았습니다."
    confidence += " 위 성적은 보고서 생성 시점 기준이며, 이후 채점 결과는 ‘예측 성적’ 탭에서 확인하세요."
    warnings = ["‘예측하기 어렵다’는 가격이 그대로라는 뜻은 아닙니다",
                "갑작스러운 뉴스나 시장 변화로 예측이 빗나갈 수 있습니다"]
    if not macro_active:
        warnings.append("단기 예측에 월별 경제지표가 빠져 있습니다")
    if not nsi_active:
        warnings.append("단기 예측에 뉴스 분위기 지표가 빠져 있습니다")
    warning_text = ". ".join(warnings) + ". 매수·매도 권유가 아닌 참고 자료입니다."

    # 전체 결론은 흰 박스로 크게 띄운다. 예측 상태(기록하지 않는 재실행)는 눈에 띄는 한 줄로 둔다.
    headline = sections[0] if sections else None
    lead = ""
    if headline:
        lead = ('<div style="background:#fff;border:1px solid #cedff0;border-radius:6px;'
                'padding:13px 15px;margin:12px 0 2px">'
                f'<div style="font-size:11px;color:#7a8797;letter-spacing:.5px;margin-bottom:3px">'
                f'{escape(headline[0])}</div>'
                f'<div style="font-size:16px;line-height:1.6;font-weight:600">{escape(headline[1])}</div>'
                '</div>')
    status = "".join(
        f'<div style="margin:8px 0 0;padding:7px 12px;background:#fff4e5;border:1px solid #f0c58a;border-radius:5px;'
        f'font-size:12px;color:#7a4b00"><b>{escape(label)}</b> · {escape(text)}</div>' for label, text in sections[1:])
    outlook = (f'<div style="{_BOX}">'
               '<div style="font-size:14px;font-weight:700;margin-bottom:8px">중장기 전망</div>'
               # 세 기간 모두 근거가 없으면 빈 타일 셋 대신 한 줄로 줄인다 — 정보가 없는 칸이 자리를 차지했다.
               + (_tiles_html(long_tiles) if any(t[3] != "muted" for t in long_tiles) else
                  '<div style="font-size:13px;color:#8a9199">3개월·6개월·12개월 모두 ‘변화 없음’보다 낫다는 근거가 없어 '
                  '방향을 말하지 않습니다(판단 근거 부족).</div>')
               + f'<div style="font-size:11px;color:#8a9199;margin-top:6px">{escape(long_note)}</div>'
               '<div style="font-size:14px;font-weight:700;margin:14px 0 8px">회사 실적 — 본업으로 번 이익</div>'
               + (_tiles_html(earning_tiles) if earning_tiles else "")
               + f'<div style="font-size:11px;color:#8a9199;margin-top:6px">{escape(" ".join(earning_notes))}</div>'
               '</div>')
    fine_print = ('<div style="margin:14px 0 0;font-size:12px;line-height:1.6;color:#8a9199">'
                  f'<div><b style="color:#6b7178">얼마나 믿을 수 있나요?</b> {escape(confidence)}</div>'
                  f'<div style="margin-top:4px"><b style="color:#6b7178">주의할 점</b> {escape(warning_text)}</div>'
                  '</div>')
    # 맨 위: 다음 거래일 시초가·방향·종가, 지난 예측 결과와 지금까지 성적(2026-09-13 재구성).
    # 세 카드 바로 아래에 시가 반영 갱신 블록(P16 운영 반영). 아침에는 자리 표시, 09:37 회차가 카드로 바꾼다.
    # 다시 만든 보고서에 그날 Post-open 행이 이미 있으면 카드를 그대로 그린다(자리 표시로 되돌리지 않는다).
    top = (next_day_forecast_html(prediction_date=prediction_date, summary=summary,
                                  open_forecast=open_forecast, price_forecasts=price_forecasts,
                                  target_mode=target_mode)
           + post_open_block_html(post_open, target=target, morning=live)
           + chart_situation_html(situation)
           + SCORECARD_START + scorecard_html(review, summary.get("ensemble") or "Mean ensemble")
           + SCORECARD_END)
    return ('<section id="easy-summary" aria-label="한눈에 보는 쉬운 요약" '
            'style="background:#f0f6fc;border:1px solid #cedff0;border-radius:8px;padding:16px 20px;margin:0 0 20px">'
            '<h3 style="margin:0 0 6px;font-size:19px">한눈에 보는 쉬운 요약</h3>'
            f'<div style="font-size:12px;color:#586575">단기 데이터 기준 {escape(date_text(data_date))} · '
            '보고서 생성 시점의 계산 결과를 쉬운 말로 풀었습니다.</div>'
            f'{top}{lead}{status}{price_block}{outlook}{fine_print}</section>')


_TILE_COLORS = {"up": "#1e6b34", "down": "#a8322a", "": "#1a1a1a", "muted": "#8a9199"}


def _tiles_html(tiles):
    """(이름, 값, 한 줄 설명, 색) 목록을 가로로 줄바꿈되는 숫자 타일로. 색은 up·down·''·muted."""
    from html import escape
    body = ""
    for label, value, note, tone in tiles:
        muted = tone == "muted"
        body += (f'<div style="{_CARD}">'
                 f'<div style="font-size:11px;color:#7a8797">{escape(label)}</div>'
                 f'<div style="font-size:{15 if muted else 19}px;font-weight:700;line-height:1.35;'
                 f'color:{_TILE_COLORS.get(tone, "#1a1a1a")}">{escape(value)}</div>'
                 f'<div style="font-size:11px;color:#8a9199;line-height:1.45">{escape(note)}</div></div>')
    return f'<div style="display:flex;gap:8px;flex-wrap:wrap">{body}</div>'


# 가격 범위 그림(2026-09-28 요청: 글 대신 그림). 시초가·다음 거래일·1주·1개월 종가의 예상 구간을 한 눈금 위에
# 막대로 놓아, 기간이 길수록 불확실성이 커지는 모양이 바로 보이게 한다. 점선은 기준 가격(전일 종가).
# 검증을 통과한 기간만 예상가를 점으로 찍는다 — 통과하지 못한 기간은 구간 막대만 두고 숫자를 내지 않는다.
_PRICE_RANGE_STYLE = (
    '<style>.pr-row{display:grid;grid-template-columns:108px minmax(90px,1fr) 124px;gap:10px;align-items:center;'
    'padding:8px 0;border-top:1px solid #eef1f4}'
    '@media (max-width:560px){.pr-row{grid-template-columns:1fr auto;row-gap:4px}'
    '.pr-row .pr-bar{grid-column:1/-1;grid-row:2}}</style>')


def price_range_html(open_forecast, price_forecasts, prediction_date=None):
    from html import escape
    open_forecast = open_forecast if hasattr(open_forecast, "get") else {}
    by_days = {r.get("trading_days"): r for r in (price_forecasts or []) if hasattr(r, "get")}
    specs = [("시초가", "장이 시작할 때", open_forecast, "predicted_open", "low_open", "high_open"),
             ("종가 · 다음 거래일", "장이 끝날 때", by_days.get(1, {}), "predicted_close", "low_close", "high_close"),
             ("종가 · 1주 뒤", "5거래일", by_days.get(5, {}), "predicted_close", "low_close", "high_close"),
             ("종가 · 1개월 뒤", "20거래일", by_days.get(20, {}), "predicted_close", "low_close", "high_close")]
    rows = []
    for label, hint, row, point_key, low_key, high_key in specs:
        if not row:
            continue
        low, high = _finite(row.get(low_key)), _finite(row.get(high_key))
        passed = row.get("signal") == "있음"
        point = _finite(row.get(point_key)) if passed else None
        if point is not None and point <= 0:
            point = None
        when = row.get("target_date", prediction_date if point_key == "predicted_open" else None)
        day = _day_label(when) if when is not None else None
        rows.append(dict(label=label, hint=hint, day=day, low=low, high=high, point=point,
                         change=_finite(row.get("predicted_return")) if point is not None else None))
    if not rows:
        return ""
    base = next((_finite(r.get("current_close")) for r in [open_forecast] + list(by_days.values())
                 if hasattr(r, "get") and _finite(r.get("current_close"))), None)
    return _range_chart_html(rows, base, title="가격 전망 — 시초가예측과 종가예측")


def price_rows_range_html(price_rows, title, money=None, base_label="기준 가격(기준 봉 종가)"):
    """기간별 종가 예측 행(horizon·target_date·signal·predicted_close·low_close·high_close)을 같은 범위 그림으로.

    금·은 보고서의 1주일·1개월 예상 가격처럼 원화가 아닌 값에도 쓴다(2026-10-02). money 는 값을 글자로 바꾸는 함수.
    """
    rows = []
    for row in price_rows or []:
        if not hasattr(row, "get"):
            continue
        point = _finite(row.get("predicted_close")) if row.get("signal") == "있음" else None
        if point is not None and point <= 0:
            point = None
        when = row.get("target_date")
        rows.append(dict(label=str(row.get("horizon") or ""), hint="", day=_day_label(when) if when is not None else None,
                         low=_finite(row.get("low_close")), high=_finite(row.get("high_close")), point=point,
                         change=_finite(row.get("predicted_return")) if point is not None else None))
    if not rows:
        return ""
    base = next((_finite(r.get("current_close")) for r in price_rows
                 if hasattr(r, "get") and _finite(r.get("current_close"))), None)
    return _range_chart_html(rows, base, title=title, money=money, base_label=base_label)


def _range_chart_html(rows, base, title, money=None, base_label="기준 가격(전일 종가)"):
    """예상 구간(막대)·예상가(점)·기준 가격(점선)을 한 눈금 위에 그린다. rows: label·hint·day·low·high·point·change."""
    from html import escape
    money = money or (lambda value: f"{value:,.0f}원")
    values = [v for r in rows for v in (r["low"], r["high"], r["point"]) if v is not None]
    if base is not None:
        values.append(base)
    lo, hi = (min(values), max(values)) if values else (0.0, 1.0)
    pad = (hi - lo) * .04 or max(abs(hi), 1.0) * .01
    lo, hi = lo - pad, hi + pad

    def at(value):
        return f"{(value - lo) / (hi - lo) * 100:.2f}%"

    body = ""
    for r in rows:
        bar = '<div style="position:absolute;left:0;right:0;top:9px;height:2px;background:#e6ebf0"></div>'
        if r["low"] is not None and r["high"] is not None and r["high"] > r["low"]:
            bar += (f'<div style="position:absolute;left:{at(r["low"])};width:calc({at(r["high"])} - {at(r["low"])});'
                    'top:4px;height:12px;background:#cfe0f3;border:1px solid #9fc0e3;border-radius:6px;'
                    'box-sizing:border-box"></div>')
        if base is not None:
            bar += (f'<div style="position:absolute;left:{at(base)};top:-2px;bottom:-2px;'
                    'border-left:2px dashed #8a9199"></div>')
        if r["point"] is not None:
            bar += (f'<div style="position:absolute;left:calc({at(r["point"])} - 7px);top:3px;width:14px;height:14px;'
                    'border-radius:50%;background:#1a5490;border:2px solid #fff;box-sizing:border-box;'
                    'box-shadow:0 0 0 1px #1a5490"></div>')
        band = (f'{money(r["low"])[:-1] if money(r["low"]).endswith("원") else money(r["low"])}~{money(r["high"])}'
                if r["low"] is not None and r["high"] is not None else "")
        if r["point"] is not None:
            tone = "#1e6b34" if (r["change"] or 0) > 0 else ("#a8322a" if (r["change"] or 0) < 0 else "#1a1a1a")
            value = (f'<div style="font-size:15px;font-weight:700;color:{tone}">{money(r["point"])}'
                     + (f' <span style="font-size:12px">{r["change"]:+.2%}</span>' if r["change"] is not None else "")
                     + '</div>')
        else:
            value = '<div style="font-size:12px;font-weight:600;color:#8a9199">예측하기 어렵습니다</div>'
        body += ('<div class="pr-row">'
                 f'<div><div style="font-size:13px;font-weight:700">{escape(r["label"])}</div>'
                 f'<div style="font-size:11px;color:#8a9199">{escape(r["day"] or r["hint"])}</div></div>'
                 f'<div class="pr-bar" style="position:relative;height:20px">{bar}</div>'
                 f'<div style="text-align:right">{value}'
                 f'<div style="font-size:11px;color:#8a9199">{escape(band)}</div></div></div>')
    legend = ('<span style="display:inline-block;width:14px;height:8px;background:#cfe0f3;border:1px solid #9fc0e3;'
              'border-radius:4px;vertical-align:middle"></span> 예상 구간 · '
              '<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#1a5490;'
              'vertical-align:middle"></span> 예상가 · '
              '<span style="display:inline-block;height:10px;border-left:2px dashed #8a9199;vertical-align:middle"></span> '
              + (f'{escape(base_label)} {money(base)}' if base is not None else "기준 가격"))
    return (_PRICE_RANGE_STYLE + f'<div style="{_BOX}">'
            f'<div style="font-size:14px;font-weight:700;margin-bottom:2px">{escape(title)}</div>'
            f'<div style="font-size:11px;color:#8a9199;margin-bottom:6px">{legend}</div>'
            f'{body}'
            '<div style="font-size:11px;color:#8a9199;margin-top:6px">검증을 통과하지 못한 기간은 예상가(점) 없이 '
            '구간만 보입니다. 기간이 길수록 구간이 넓어집니다.</div></div>')


def rolling_train_indices(date_index, before, years=5):
    dates = pd.DatetimeIndex(date_index)
    before = pd.Timestamp(before)
    return np.flatnonzero((dates >= before - pd.DateOffset(years=years)) & (dates < before))


def direction_estimator(family, params, seed=42):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    if family == "Logistic":
        return make_pipeline(StandardScaler(), LogisticRegression(
            C=params["C"], class_weight=params["class_weight"], max_iter=3000, random_state=seed))
    if family != "LightGBM":
        raise ValueError(f"Unknown model: {family}")
    from lightgbm import LGBMClassifier
    return LGBMClassifier(n_estimators=params["n_estimators"], num_leaves=7,
                          learning_rate=.03, min_child_samples=50, colsample_bytree=.85,
                          reg_alpha=.5, reg_lambda=2., class_weight=params["class_weight"],
                          random_state=seed, n_jobs=2, verbosity=-1)


def aligned_probabilities(estimator, X):
    p = np.full((len(X), 3), 1e-7)
    p[:, np.asarray(estimator.classes_, dtype=int)] = estimator.predict_proba(X)
    p = np.clip(p, 1e-7, 1.)
    return p / p.sum(axis=1, keepdims=True)


def temperature_probabilities(probs, temperature):
    logits = np.log(np.clip(probs, 1e-7, 1.)) / temperature
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    return p / p.sum(axis=1, keepdims=True)


def probability_loss(y, p):
    return float(-np.log(np.clip(p[np.arange(len(y)), np.asarray(y, dtype=int)], 1e-7, 1.)).mean())


# 설정 선택 기준. 확률 모형이므로 log loss(proper scoring rule)가 기본이다.
# accuracy로 고르면 class_weight=None 쪽으로 기울어 다수 클래스만 맞히는 설정이 뽑히고
# balanced accuracy가 떨어진다(2026-09 검증에서 고정 설정 'Previous ensemble'이 오히려 나았다).
# 되돌리려면 노트북 설정 셀의 SELECTION_METRIC = "accuracy" 로 바꾸면 된다. 어느 쪽이 나은지는
# 원장의 Mean ensemble(선택된 설정) vs Previous ensemble(고정 설정) 쌍체 비교로 계속 잰다.
SELECTION_METRICS = ("log_loss", "accuracy")


def recency_weights(n, half_life=None):
    """최근 관측에 더 큰 가중을 준다. w = 2 ** (-age / half_life), 평균 1로 정규화.

    age는 학습 구간 안에서의 거래일 나이다(마지막 관측이 0). 평균을 1로 맞추므로
    무가중과 정규화 상수가 같아져, 반감기만 바뀌고 전체 규제 강도는 그대로다.
    half_life가 None이면 무가중(전부 1)이다.
    """
    n = int(n)
    if n <= 0:
        return np.ones(0, dtype=float)
    if half_life is None:
        return np.ones(n, dtype=float)
    half_life = float(half_life)
    if not np.isfinite(half_life) or half_life <= 0:
        raise ValueError(f"half_life는 양의 유한값이어야 합니다: {half_life!r}")
    age = np.arange(n - 1, -1, -1, dtype=float)      # 마지막 관측의 age가 0
    weights = np.power(2.0, -age / half_life)
    return weights / weights.mean()


def _checked_weights(sample_weight, length):
    """학습 행 가중치를 검사해 배열로 돌려준다. None이면 None."""
    if sample_weight is None:
        return None
    weights = np.asarray(sample_weight, dtype=float)
    if weights.shape != (length,):
        raise ValueError(f"sample_weight 길이가 X와 다릅니다: {weights.shape} vs ({length},)")
    if not np.isfinite(weights).all():
        raise ValueError("sample_weight에 비유한 값이 있습니다")
    if (weights < 0).any():
        raise ValueError("sample_weight에 음수가 있습니다")
    if weights.sum() <= 0:
        raise ValueError("sample_weight의 합이 0입니다")
    return weights


def _fit_with_weights(estimator, X, y, weights):
    """가중치를 지원하는 추정기에만 넘긴다. Pipeline은 마지막 단계 이름을 붙여야 한다."""
    if weights is None:
        return estimator.fit(X, y)
    try:
        from sklearn.pipeline import Pipeline
    except Exception:
        Pipeline = ()
    if isinstance(estimator, Pipeline):
        return estimator.fit(X, y, **{f"{estimator.steps[-1][0]}__sample_weight": weights})
    return estimator.fit(X, y, sample_weight=weights)


def fit_direction_model(X, y, train_indices, family, seed=42, selection="log_loss",
                        sample_weight=None):
    """바깥 평가 구간을 보지 않고, 과거 내부 3개 구간의 성적으로 설정을 고른다.

    selection="log_loss": 내부 log loss가 가장 낮은 설정(동률이면 정확도가 높은 쪽).
    selection="accuracy": 내부 정확도가 가장 높은 설정(동률이면 log loss가 낮은 쪽) — 예전 기준.
    온도 보정은 argmax를 유지한다. 반환값은 표준 estimator와 dict뿐이어서 joblib로 다시 읽을 수 있다.

    sample_weight는 X와 같은 길이의 학습 행 가중치다(전체 행 기준). train_indices와 내부
    분할로 각각 잘라 쓴다. class_weight="balanced"와 함께 쓰면 둘이 곱해진다 — 클래스 균형과
    최근성이 동시에 걸리므로 선택된 params와 half_life를 함께 기록해야 재현된다.
    설정 선택과 온도 보정도 같은 가중치로 한다. 내부 검증 점수 자체는 가중하지 않는다 —
    평가는 언제나 날짜별 동일 가중이다.
    """
    if selection not in SELECTION_METRICS:
        raise ValueError(f"selection은 {SELECTION_METRICS} 중 하나여야 합니다: {selection!r}")
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.dummy import DummyClassifier
    indices = np.asarray(train_indices, dtype=int)
    if len(indices) < 100 or np.any(np.diff(indices) <= 0):
        raise ValueError("At least 100 chronologically ordered training rows are required")
    weights_all = _checked_weights(sample_weight, len(np.asarray(y)))
    xt, yt = np.asarray(X)[indices], np.asarray(y)[indices]
    wt = None if weights_all is None else weights_all[indices]
    if family == "Logistic":
        candidates = [{"C": c, "class_weight": w} for c in (.003, .01, .03) for w in (None, "balanced")]
    elif family == "LightGBM":
        candidates = [{"n_estimators": n, "class_weight": w} for n in (60, 120) for w in (None, "balanced")]
    else:
        raise ValueError(family)
    splits = list(TimeSeriesSplit(n_splits=3, test_size=min(126, len(yt) // 5)).split(xt))
    labels = np.concatenate([yt[va] for _, va in splits])
    trials = []
    for params in candidates:
        predictions = []
        for tr, va in splits:
            estimator = (direction_estimator(family, params, seed) if len(np.unique(yt[tr])) > 1
                         else DummyClassifier(strategy="prior"))
            _fit_with_weights(estimator, xt[tr], yt[tr], None if wt is None else wt[tr])
            predictions.append(aligned_probabilities(estimator, xt[va]))
        probs = np.vstack(predictions)
        accuracy = float(np.mean(probs.argmax(axis=1) == labels))
        trials.append((accuracy, probability_loss(labels, probs), params, probs))
    if selection == "log_loss":
        best = min(trials, key=lambda t: (t[1], -t[0]))
    else:
        best = min(trials, key=lambda t: (-t[0], t[1]))
    temperatures = (1., .75, 1.5, 2.)
    temperature = min(temperatures, key=lambda t: probability_loss(labels, temperature_probabilities(best[3], t)))
    estimator = (direction_estimator(family, best[2], seed) if len(np.unique(yt)) > 1
                 else DummyClassifier(strategy="prior"))
    _fit_with_weights(estimator, xt, yt, wt)
    return {"estimator": estimator, "temperature": temperature, "selection": {
        "family": family, "params": best[2], "temperature": temperature, "selection_metric": selection,
        "weighted": wt is not None,
        "inner_accuracy": best[0], "inner_log_loss": probability_loss(labels, temperature_probabilities(best[3], temperature)),
        "last_validation_position": int(indices[splits[-1][1][-1]]), "training_rows": len(indices),
    }}


def feature_contributions(fitted, X, feature_names, top_k=5):
    """이 예측을 상승 쪽으로 민 특징과 하락 쪽으로 민 특징. [{feature, value, contribution}]

    SHAP 패키지를 새로 들이지 않는다. 두 모델 계열 모두 정확한 기여도를 자체로 낼 수 있다.
      LightGBM: predict(pred_contrib=True) — 트리 SHAP 값을 그대로 준다.
      Logistic: 표준화된 특징값 × 계수 — 선형 모형에서는 이것이 정의상 기여도다.
    상승 확률(클래스 2)에서 하락 확률(클래스 0)을 뺀 쪽으로 부호를 맞춰, 양수면 상승 쪽이다.

    주의: 기여도가 큰 특징이 '도움이 된 특징'은 아니다. 모델이 크게 반응했는데 계속 틀렸다면
    오히려 해로운 특징이다. 그 구분은 채점 표본이 쌓인 뒤에야 가능하므로 여기서는 기록만 한다.
    """
    estimator = fitted["estimator"]
    X = np.asarray(X, dtype=np.float64)
    if X.ndim == 1:
        X = X.reshape(1, -1)
    names = list(feature_names)
    model = estimator
    scaler = None
    if hasattr(estimator, "named_steps"):
        scaler = estimator.named_steps.get("scaler")
        model = list(estimator.named_steps.values())[-1]
    classes = list(getattr(model, "classes_", []))
    try:
        up = classes.index(2) if 2 in classes else len(classes) - 1
        down = classes.index(0) if 0 in classes else 0
    except (ValueError, AttributeError):
        up, down = -1, 0

    scores = None
    if hasattr(model, "booster_"):                      # LightGBM
        raw = model.booster_.predict(X, pred_contrib=True)
        raw = np.asarray(raw)
        n_features = len(names)
        if raw.shape[1] == (n_features + 1) * max(len(classes), 1):
            per_class = raw.reshape(raw.shape[0], max(len(classes), 1), n_features + 1)
            scores = per_class[0, up, :n_features] - per_class[0, down, :n_features]
        elif raw.shape[1] == n_features + 1:            # 이진 모형
            scores = raw[0, :n_features]
    elif hasattr(model, "coef_"):                       # Logistic
        values = scaler.transform(X) if scaler is not None else X
        coef = np.asarray(model.coef_, dtype=float)
        if coef.ndim == 2 and coef.shape[0] > 1:
            scores = (coef[up] - coef[down]) * values[0]
        else:
            scores = coef.reshape(-1) * values[0]
    if scores is None or len(scores) != len(names):
        return []
    order = np.argsort(-np.abs(scores))[:top_k]
    return [{"feature": names[i], "value": float(X[0, i]), "contribution": float(scores[i])}
            for i in order]


def blend_contributions(per_model, top_k=5):
    """여러 모델의 기여도를 특징별로 평균한다(앙상블은 확률을 평균하므로 기여도도 평균한다)."""
    totals, counts = {}, {}
    values = {}
    for items in per_model:
        for item in items or []:
            key = item["feature"]
            totals[key] = totals.get(key, 0.0) + item["contribution"]
            counts[key] = counts.get(key, 0) + 1
            values[key] = item["value"]
    if not totals:
        return []
    merged = [{"feature": k, "value": values[k], "contribution": totals[k] / len(per_model)}
              for k in totals]
    merged.sort(key=lambda d: -abs(d["contribution"]))
    return merged[:top_k]


def predict_direction_model(fitted, X):
    return temperature_probabilities(aligned_probabilities(fitted["estimator"], X), fitted["temperature"])


def calibrate_price_forecast(y, prediction, sigma, dates, horizon, ci_function, coverage=.8, band_window=None):
    """OOF를 보정 50% / 신호 선택 25% / 최종 평가 25%로 나누고 경계 라벨을 제거한다.

    최종 평가 정답은 slope, 신호 선택, 구간 폭 결정에 사용하지 않는다.

    band_window=None(기본)이면 구간 폭 q 를 가장 오래된 절반에서 한 번 정한다 — 예전 그대로다.
    band_window=W 이면 q 를 '그 날 이전에 정답이 확정된 최근 W개 점수'의 분위수로 날마다 다시 잡는다.
    발행에 쓰는 band_q 는 마지막 W개(확정분)이고, band_coverage_realized·band_halfwidth_mean 은 평가 구간에서
    그날그날의 q_t 로 잰 값이다(마지막 q 하나를 평가 구간 전체에 걸면 표본 안 값이 된다).
    R01(2026-09-20): 여덟 칸(두 종목 × 시초가·종가 1/5/20일) 중 이 방식이 Winkler 점수를 유의하게 낮춘 것은
    삼성 시초가뿐이고(포함률 86.6%→81.8%, 반폭 −12%), 하이닉스 시초가는 동률, 종가 20일은 나빠졌다. 그래서
    시초가에만 쓴다 — 종가·금속은 band_window 를 주지 않는다. 일괄 교체는 금속 포함률을 77%→74%로 떨어뜨렸다.
    """
    y, prediction, sigma = map(lambda a: np.asarray(a, dtype=float), (y, prediction, sigma))
    n = len(y)
    cut1, cut2, gap = n // 2, n * 3 // 4, horizon - 1
    cal = np.arange(max(0, cut1 - gap))
    gate = np.arange(cut1, max(cut1, cut2 - gap))
    evaluation = np.arange(cut2, n)
    if min(len(cal), len(gate), len(evaluation)) < 30:
        raise ValueError("Not enough OOF rows for separate price calibration, selection and evaluation")
    denom = np.sum(prediction[cal] ** 2)
    slope = float(np.clip(np.sum(prediction[cal] * y[cal]) / denom, 0., 1.)) if denom > 0 else 0.
    gate_diff = np.abs(y[gate] - slope * prediction[gate]) - np.abs(y[gate])
    gate_lo, gate_hi = ci_function(pd.DatetimeIndex(dates)[gate], lambda i: float(gate_diff[i].mean()))
    beats_baseline = bool(np.isfinite(gate_hi) and gate_hi < 0)
    if not beats_baseline:
        slope = 0.
    residual = np.abs(y[cal] - slope * prediction[cal]) / np.maximum(sigma[cal], 1e-6)
    q_oldest = float(np.quantile(residual, coverage))
    q, q_path, q_method = q_oldest, None, "oldest_half"
    if band_window:
        window = int(band_window)
        score = np.abs(y - slope * prediction) / np.maximum(sigma, 1e-6)

        def q_before(t):
            """행 t 이전에 정답이 확정된(행 < t − gap) 최근 window 개 점수의 분위수. 모자라면 예전 q."""
            seen = score[max(0, t - gap - window):max(0, t - gap)]
            return float(np.quantile(seen, coverage)) if len(seen) >= min(window, 100) else q_oldest

        q_path = np.array([q_before(t) for t in evaluation])
        q, q_method = q_before(n), f"trailing_{window}"
    test_error = np.abs(y[evaluation] - slope * prediction[evaluation])
    diff = test_error - np.abs(y[evaluation])
    lo, hi = ci_function(pd.DatetimeIndex(dates)[evaluation], lambda i: float(diff[i].mean()))
    return {
        "zero_baseline_mae": float(np.abs(y[evaluation]).mean()),
        "raw_model_mae": float(np.abs(y[evaluation] - prediction[evaluation]).mean()),
        "shrunk_model_mae": float(test_error.mean()), "mae_diff_vs_zero": float(diff.mean()),
        "mae_diff_lo": float(lo), "mae_diff_hi": float(hi),
        "selection_mae_diff_lo": float(gate_lo), "selection_mae_diff_hi": float(gate_hi),
        "oof_slope": slope, "beats_baseline": beats_baseline, "band_q": q,
        "band_coverage_realized": float(np.mean(test_error <= (q if q_path is None else q_path) * sigma[evaluation])),
        "band_halfwidth_mean": float(np.mean((q if q_path is None else q_path) * sigma[evaluation])),
        "band_q_method": q_method, "band_q_oldest_half": q_oldest,
        "n_oof": n, "n_evaluation": len(evaluation), "calibration_end": int(cal[-1]),
        "gate_start": int(gate[0]), "gate_end": int(gate[-1]), "evaluation_start": int(evaluation[0]),
    }


def make_price_model(alpha=1e4):
    """강한 축소(Ridge alpha 큼) + 변동성 스케일 타깃. LightGBM 회귀는 OOF에서 우위가 없고
    시드에 따라 라이브 값이 몇 %p씩 움직여 제거했다."""
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    return Pipeline([("scaler", StandardScaler()), ("model", Ridge(alpha=alpha))])


def implied_vol_scale(iv_level, window=250):
    """내재변동성 배율: 오늘 IV ÷ 지난 1년 중앙값(전일까지). 1보다 크면 시장이 평소보다 큰 변동을 본다.

    실현 변동성(20일 표준편차)은 과거를 본다. 실적·FOMC 같은 예정된 이벤트 앞에서는 과거 20일이
    조용했어도 시장은 큰 움직임을 예상하고 옵션 가격에 반영한다. 그 배율만큼 구간을 넓히는 것이
    sigma_iv 다. 배율은 [0.5, 3] 으로 잘라 옵션 시장의 이상값이 구간을 망가뜨리지 않게 한다.
    """
    level = pd.Series(iv_level).astype(float)
    median = level.shift(1).rolling(window, min_periods=window // 2).median()
    return (level / median).clip(0.5, 3.0)


def price_design_frame(feat, sam_index, feature_cols, raw_close, vol_20, horizon, har_fn=None,
                       iv_scale=None):
    """h거래일 가격 예측의 설계 행렬. (frame, HAR 라이브 sigma) 반환.

    행 d의 특징은 d-1 종가까지만 포함한다. 목표값은 d-1 종가 대비 horizon번째 거래일(d+horizon-1)의
    원본 종가 수익률이다. sigma_simple = 20일 변동성 × sqrt(h), sigma_har = HAR 예측,
    sigma_iv = sigma_simple × 내재변동성 배율(있을 때만). 변동성 방식들을 같은 행에서 비교하도록
    비어 있는 앞부분 행은 함께 제거한다(학습 행이 그만큼 줄어든다).
    """
    raw_close = pd.Series(raw_close).astype(float)
    future_return = raw_close.shift(-(horizon - 1)) / raw_close.shift(1) - 1
    har_series, har_live = (har_fn or har_sigma_forecast)(raw_close.pct_change(), horizon)
    reg = feat.loc[sam_index, list(feature_cols)].copy()
    reg["future_return"] = future_return.reindex(reg.index)
    reg["sigma_simple"] = pd.Series(vol_20).reindex(reg.index) * np.sqrt(horizon)
    reg["sigma_har"] = har_series.reindex(reg.index)
    if iv_scale is not None:
        reg["sigma_iv"] = reg["sigma_simple"] * pd.Series(iv_scale).reindex(reg.index)
    reg = reg.replace([np.inf, -np.inf], np.nan).dropna()
    return reg, har_live


def price_oof_predictions(X, y, sigma, horizon, template, n_splits):
    """시간순 OOF(TimeSeriesSplit, gap=h-1). 타깃은 sigma 로 나눈 수익률, 예측은 다시 sigma 를 곱한다.

    (oof, folds) 반환. folds 는 폴드별 학습·시험 위치 범위. OOF 가 없는 앞부분은 NaN 이다.
    """
    from sklearn.base import clone
    from sklearn.model_selection import TimeSeriesSplit
    X, y, sigma = np.asarray(X), np.asarray(y, dtype=float), np.asarray(sigma, dtype=float)
    z = y / np.maximum(sigma, 1e-6)
    oof = np.full(len(z), np.nan)
    folds = []
    for k, (train, valid) in enumerate(TimeSeriesSplit(n_splits=n_splits, gap=horizon - 1).split(X)):
        model = clone(template).fit(X[train], z[train])
        oof[valid] = model.predict(X[valid]) * sigma[valid]
        folds.append({"fold": k, "train_rows": int(len(train)), "train_pos": (int(train[0]), int(train[-1])),
                      "test_pos": (int(valid[0]), int(valid[-1]))})
    return oof, folds


def price_issuance_summary(log, horizon_days, last_n=60):
    """최근 last_n 개 예측일의 h일 가격 예측 중 신호를 낸('있음') 비율. 보고서에 발행률을 병기하는 데 쓴다.

    예측일마다 가장 먼저 기록된 행(사전 예측)만 센다. 원장이 없거나 해당 행이 없으면 n=0, rate=NaN.
    """
    empty = {"n": 0, "issued": 0, "rate": float("nan"), "horizon_days": int(horizon_days)}
    if log is None or len(log) == 0:
        return empty
    frame = pd.DataFrame(log)
    needed = {"kind", "horizon_days", "prediction_date", "signal"}
    if not needed.issubset(frame.columns):
        return empty
    rows = frame[(frame["kind"] == "price") & (pd.to_numeric(frame["horizon_days"], errors="coerce") == horizon_days)]
    if "is_prospective" in rows.columns:
        prospective = rows["is_prospective"].astype(str).str.lower().isin(("true", "1", "yes"))
        rows = rows[prospective] if prospective.any() else rows
    if rows.empty:
        return empty
    order = "created_at_utc" if "created_at_utc" in rows.columns else "prediction_date"
    first = rows.sort_values(order).groupby("prediction_date", sort=True).head(1).sort_values("prediction_date").tail(last_n)
    issued = int((first["signal"].astype(str) == "있음").sum())
    return {"n": int(len(first)), "issued": issued, "rate": issued / len(first), "horizon_days": int(horizon_days)}


def price_macro_ablation(X, y, sigma, dates, feature_names, horizon, estimator,
                         ci_function, n_splits=5, coverage=.8):
    """Paired macro-vs-market price evaluation; never choose deployment on the final test.

    Uses identical rows, purged chronological folds and separate calibration/gating for
    both models. Errors are in return units (0.01 = one percentage point), not currency.
    """
    from sklearn.base import clone
    from sklearn.model_selection import TimeSeriesSplit
    X, y, sigma = np.asarray(X), np.asarray(y), np.asarray(sigma)
    dates = pd.DatetimeIndex(dates)
    market = [i for i, name in enumerate(feature_names) if not name.startswith('macro_')]
    if not market or len(market) == len(feature_names):
        raise ValueError('Both market and macro features are required for comparison')
    if (len(X) != len(y) or len(y) != len(sigma) or len(dates) != len(y)
            or not dates.is_monotonic_increasing or not dates.is_unique
            or not np.isfinite(X).all() or not np.isfinite(y).all()
            or not np.isfinite(sigma).all() or np.any(sigma <= 0)):
        raise ValueError('Comparison requires aligned finite rows and positive volatility')
    splits = list(TimeSeriesSplit(n_splits=n_splits, gap=horizon - 1).split(X))
    outputs, stats = {}, {}
    mask = np.zeros(len(y), dtype=bool)
    for _, valid in splits:
        mask[valid] = True
    for name, columns in [('macro', np.arange(X.shape[1])), ('market', market)]:
        oof = np.full(len(y), np.nan)
        for train, valid in splits:
            fitted = clone(estimator).fit(X[train][:, columns], y[train] / sigma[train])
            oof[valid] = fitted.predict(X[valid][:, columns]) * sigma[valid]
        outputs[name] = oof[mask]
        stats[name] = calibrate_price_forecast(y[mask], oof[mask], sigma[mask], dates[mask],
                                               horizon, ci_function, coverage=coverage)
    rows = pd.DataFrame({'actual_return': y[mask], 'macro_raw': outputs['macro'],
                         'market_raw': outputs['market']}, index=dates[mask])
    rows.index.name = 'prediction_date'
    start = stats['macro']['evaluation_start']
    rows['is_evaluation'] = np.arange(len(rows)) >= start
    summary = {'horizon_days': int(horizon), 'n_evaluation': len(rows) - start,
               'evaluation_start': rows.index[start].date().isoformat(),
               'evaluation_end': rows.index[-1].date().isoformat(), 'unit': 'return',
               'comparison': 'macro_minus_market', 'deployment_changed': False}
    for name in outputs:
        rows[name + '_center'] = outputs[name] * stats[name]['oof_slope']
        summary[name + '_slope'] = stats[name]['oof_slope']
        summary[name + '_signal'] = stats[name]['beats_baseline']
        summary[name + '_interval_coverage'] = stats[name]['band_coverage_realized']
    evaluation = rows.iloc[start:]
    for label, suffix in [('raw', 'raw'), ('center', 'center')]:
        macro_error = np.abs(evaluation.actual_return - evaluation['macro_' + suffix]).to_numpy()
        market_error = np.abs(evaluation.actual_return - evaluation['market_' + suffix]).to_numpy()
        delta = macro_error - market_error
        low, high = ci_function(evaluation.index, lambda i: float(delta[i].mean()))
        summary.update({label + '_mae_delta': float(delta.mean()), label + '_mae_delta_lo': float(low),
                        label + '_mae_delta_hi': float(high), label + '_macro_mae': float(macro_error.mean()),
                        label + '_market_mae': float(market_error.mean())})
    return summary, rows


def har_sigma_forecast(returns, horizon, refit_every=60, min_train=500):
    """h거래일 수익률의 스케일(sigma)을 HAR로 예측한다. (시계열, 다음 시점 예측값) 반환.

    HAR: 일/주/월 실현변동성으로 다음 구간 변동성을 회귀한다. 고정된 20일 표준편차보다
    최근 변화에 빠르게 반응해, 같은 적중률에서 예측 구간이 좁아진다.

    각 시점의 예측은 그 시점 이전 자료로만 적합한다. 학습 표본도 목표가 이미 실현된
    구간(i - horizon 이전)으로 제한해 겹치는 라벨이 계수에 들어가지 않게 한다.
    """
    r = pd.Series(returns).astype(float)
    v = r ** 2
    X = pd.DataFrame({
        "const": 1.0,
        "d": np.log(v.shift(1).clip(lower=1e-12)),
        "w": np.log(v.rolling(5).mean().shift(1).clip(lower=1e-12)),
        "m": np.log(v.rolling(22).mean().shift(1).clip(lower=1e-12)),
    }, index=r.index)
    forward = np.sqrt(v.rolling(horizon).sum().shift(-(horizon - 1)))
    y = np.log(forward.clip(lower=1e-12))

    Xv, yv = X.to_numpy(dtype=float), y.to_numpy(dtype=float)
    rows_ok = np.isfinite(Xv).all(axis=1)
    out = np.full(len(r), np.nan)
    beta, last_fit = None, -(10 ** 9)
    for i in range(len(r)):
        end = i - horizon                     # 목표가 이미 실현된 구간만 학습에 쓴다
        if end > min_train and i - last_fit >= refit_every:
            usable = rows_ok[:end] & np.isfinite(yv[:end])
            if usable.sum() >= min_train:
                beta = np.linalg.lstsq(Xv[:end][usable], yv[:end][usable], rcond=None)[0]
                last_fit = i
        if beta is not None and rows_ok[i]:
            out[i] = float(np.exp(Xv[i] @ beta))

    live = np.nan
    usable = rows_ok & np.isfinite(yv)         # y가 NaN인 마지막 h-1행은 자동 제외된다
    if usable.sum() >= min_train:
        full = np.linalg.lstsq(Xv[usable], yv[usable], rcond=None)[0]
        latest = np.array([1.0,
                           np.log(max(float(v.iloc[-1]), 1e-12)),
                           np.log(max(float(v.iloc[-5:].mean()), 1e-12)),
                           np.log(max(float(v.iloc[-22:].mean()), 1e-12))])
        live = float(np.exp(latest @ full))
    return pd.Series(out, index=r.index), live


def snapshot_hash(raw):
    digest = hashlib.sha256()
    for name, frame in sorted(raw.items()):
        digest.update(name.encode())
        digest.update(json.dumps(list(frame.columns)).encode())
        digest.update(pd.util.hash_pandas_object(frame.sort_index(), index=True).values.tobytes())
    return digest.hexdigest()[:20]


# ---------------------------------------------------------------------------
# 해외 1일 수익률의 as-of 누적 — P17 운영 반영 (2026-09-24)
# ---------------------------------------------------------------------------
# 예전에는 해외 '수익률'을 as-of 로 붙여, 미국 휴장일에 직전 세션 수익률이 다음 한국 행에도 그대로 반복됐다
# (두 종목 모두 88행 — 새 정보처럼 보인다). 한국 연휴에는 그 사이 미국 세션 여럿 중 마지막 하루만 들어갔다.
# 이제 가격 **수준**을 같은 규칙(해외 세션 d → 한국 날짜 d+1 부터, 허용 7일)으로 붙이고 1일 수익률을 '직전 한국
# 행 이후의 누적'으로 만든다. 새 세션이 없으면 정확히 0, 연휴면 그 사이 누적이다. 0 이 '휴장'인지 '보합'인지
# 구분하도록 미국 세션 달력의 새 세션 여부·관측 나이를 특징으로 둔다. 5일 수익률·z 점수는 '지금 상태'라 그대로다.
# P17 결과(12폴드 쌍체): sk_hynix log_loss −0.0079 [−0.0129, −0.0030] 우위, samsung 동률(−0.0017), 정확도 동률.
# 노트북(특징 생성)과 실험 러너(P17)가 이 함수들을 같이 쓴다.
US_SESSION_ASSET = "sp500"
US_SESSION_COLUMNS = ("us_new_session", "us_obs_age_days")


def asof_level_with_source(base_index, series, availability_days=1, tolerance_days=7):
    """노트북 merge_latest_available 와 같은 as-of 결합(세션 d → base d+availability_days)에 원래 날짜를 함께.

    돌려주는 틀: index = base_index 순서, 열 value·source_date. 허용 기간을 넘으면 둘 다 결측.
    """
    values = pd.Series(series).dropna().copy()
    index = pd.DatetimeIndex(pd.to_datetime(values.index))
    index = index.tz_localize(None) if index.tz is not None else index
    values.index = index.normalize().as_unit("ns")
    values = values.groupby(level=0).last().sort_index()
    right = pd.DataFrame({"available_date": values.index + pd.Timedelta(days=availability_days),
                          "source_date": values.index, "value": values.to_numpy(dtype=float)})
    base = pd.DatetimeIndex(pd.to_datetime(base_index))
    base = (base.tz_localize(None) if base.tz is not None else base).as_unit("ns")
    left = pd.DataFrame({"date": base, "order": np.arange(len(base))}).sort_values("date")
    merged = pd.merge_asof(left, right, left_on="date", right_on="available_date",
                           direction="backward", tolerance=pd.Timedelta(days=tolerance_days))
    merged = merged.sort_values("order")
    return pd.DataFrame({"value": merged["value"].to_numpy(), "source_date": merged["source_date"].to_numpy()},
                        index=pd.DatetimeIndex(pd.to_datetime(base_index)))


def asof_cumulative_return(base_index, series, availability_days=1, tolerance_days=7):
    """직전 base 행 이후 새로 알게 된 해외 가격 변화(누적 수익률). 새 세션이 없으면 0, 첫 행·결측이면 NaN."""
    level = asof_level_with_source(base_index, series, availability_days, tolerance_days)["value"]
    return level / level.shift(1) - 1


def us_session_features(base_index, series, availability_days=1, tolerance_days=7):
    """미국 세션 달력(US_SESSION_ASSET 종가)으로 us_new_session(0/1)·us_obs_age_days(마지막 세션 뒤 일수)."""
    joined = asof_level_with_source(base_index, series, availability_days, tolerance_days)
    source = pd.Series(pd.DatetimeIndex(joined["source_date"]), index=joined.index)
    new = np.where(source.isna(), np.nan, (source != source.shift(1)).astype(float))
    age = (pd.DatetimeIndex(joined.index).normalize() - pd.DatetimeIndex(source)).days.astype(float)
    return pd.DataFrame({"us_new_session": new, "us_obs_age_days": np.asarray(age, dtype=float)}, index=joined.index)


# ---------------------------------------------------------------------------
# 시가 반영(09:37) 종가 방향 갱신 — P16 운영 반영 (2026-09-20)
# ---------------------------------------------------------------------------
# P16 이 보인 것: 09:00 시가가 확정된 뒤 같은 타깃(전일 종가→당일 종가)을 다시 물으면 정확도가
# samsung 0.454→0.564 · sk_hynix 0.499→0.576 으로 오른다. **더 나은 모델이 아니라 늦은 정보 시점이다.**
# 갭 부호만 읽는 규칙도 0.544 / 0.544 이고, 세션(시가→종가) 쪽은 아무것도 더 맞히지 못한다.
# 그래서 07:00 예측은 그대로 두고, 같은 target_date 에 'Post-open' 행 하나를 더해 따로 채점한다.
#
# 09:37 도구에는 모델도 특징 파이프라인도 없다(원장과 보고서를 받아 채점만 한다). 그래서 아침 노트북이
# "시가가 이렇게 열리면 확률은 이렇다"를 가상 갭 격자(−10%…+10%, 0.1% 간격)로 미리 계산해 두고
# (post_open_grid.csv), 09:37 에는 실제 갭으로 그 격자를 보간만 한다.
POST_OPEN_GAP_Z_WINDOW = 60
POST_OPEN_GAP_COLUMNS = ("gap_0", "gap_over_band", "gap_z60", "gap_abs", "gap_up_band", "gap_down_band")
POST_OPEN_GRID_GAPS = tuple(round(k / 1000., 3) for k in range(-100, 101))     # 201행
POST_OPEN_GRID_COLUMNS = ("target_date", "gap", "p_down", "p_flat", "p_up", "band", "gap_rule_label")


def _gap_group_g(gap, band, mean, std):
    """그룹 G 6열. 갭·밴드·(d−1 까지의) 갭 평균·표준편차만 받는다 — 과거 행과 가상 격자가 같은 식을 쓴다."""
    gap = pd.Series(gap, dtype=float)
    b = pd.Series(band, dtype=float, index=gap.index).replace(0, np.nan)
    mean = pd.Series(mean, dtype=float, index=gap.index)
    std = pd.Series(std, dtype=float, index=gap.index).replace(0, np.nan)
    both = gap.notna() & b.notna()
    out = pd.DataFrame(index=gap.index)
    out["gap_0"] = gap
    out["gap_over_band"] = gap / b
    out["gap_z60"] = (gap - mean) / std
    out["gap_abs"] = gap.abs()
    out["gap_up_band"] = (gap > b).astype(float).where(both)
    out["gap_down_band"] = (gap < -b).astype(float).where(both)
    return out[list(POST_OPEN_GAP_COLUMNS)]


def post_open_gap_series(bars):
    """gap_d = open_d / close_{d−1} − 1 (원본 종가 기준, 노트북 `sam_gap` 과 같은 정의)."""
    bars = bars.sort_index()
    prev_close = bars["close"].astype(float).shift(1).replace(0, np.nan)
    return bars["open"].astype(float) / prev_close - 1


def post_open_gap_features(bars, band, calendar, window=POST_OPEN_GAP_Z_WINDOW):
    """그룹 G — 실현 갭. 행 d 가 쓰는 d일 정보는 **시가 하나뿐**이다.

    z 점수의 평균·표준편차는 `shift(1)` 이라 d−1 까지의 갭 분포만 보고, 밴드도 노트북이 이미
    d−1 까지로 만든 값이다. d일 종가·고가·저가·거래량은 어느 열에도 들어가지 않는다.
    봉이 없거나 시가가 없는 날짜는 NaN 으로 남긴다(앞 값을 끌어오지 않는다).
    tests/test_post_open_reforecast.py 가 이 계약을 고정한다(러너 P16 과 노트북이 같은 함수를 쓴다).
    """
    bars = bars.sort_index()
    gap = post_open_gap_series(bars)
    mean = gap.rolling(window).mean().shift(1)
    std = gap.rolling(window).std().shift(1)
    b = pd.Series(band, dtype=float).reindex(bars.index)
    out = _gap_group_g(gap, b, mean, std)
    return out.reindex(pd.DatetimeIndex(calendar))[list(POST_OPEN_GAP_COLUMNS)]


def post_open_gap_stats(bars, window=POST_OPEN_GAP_Z_WINDOW):
    """예측일의 z 점수에 쓸 (직전 window 세션 갭 평균, 표준편차). 마지막 봉까지의 갭만 본다(d−1 까지)."""
    gap = post_open_gap_series(bars).dropna()
    tail = gap.iloc[-window:]
    if len(tail) < 2:
        return np.nan, np.nan
    return float(tail.mean()), float(tail.std())


def gap_rule_labels(gap, band):
    """모델 없는 트리비얼 기준: 갭 > 밴드면 상승(2), 갭 < −밴드면 하락(0), 아니면 보합(1)."""
    g, b = np.asarray(gap, dtype=float), np.asarray(band, dtype=float)
    out = np.where(g > b, 2., np.where(g < -b, 0., 1.))
    out[~np.isfinite(g) | ~np.isfinite(b)] = np.nan
    return out


def build_post_open_grid(models, live_market_row, band, gap_mean, gap_std, target_date,
                         gaps=POST_OPEN_GRID_GAPS):
    """가상 갭 격자 → 확률표. 그룹 G 밖의 열은 07:00 라이브 행 그대로이고 그룹 G 만 가상 갭으로 만든다.

    models: {가족: fit_direction_model 결과}. 확률은 대표 모델과 같은 단순 평균이다.
    반환 열: POST_OPEN_GRID_COLUMNS. gap_rule_label 은 모델 없는 갭 규칙(하락·보합·상승)이다.
    """
    gaps = np.asarray(gaps, dtype=float)
    g = _gap_group_g(pd.Series(gaps), pd.Series(np.full(len(gaps), float(band))),
                     pd.Series(np.full(len(gaps), float(gap_mean))),
                     pd.Series(np.full(len(gaps), float(gap_std))))
    base = np.tile(np.asarray(live_market_row, dtype=np.float32).reshape(1, -1), (len(gaps), 1))
    X = np.hstack([base, g.to_numpy(dtype=np.float32)])
    probs = np.mean([predict_direction_model(fitted, X) for fitted in models.values()], axis=0)
    probs = probs / probs.sum(axis=1, keepdims=True)
    labels = {0: "하락", 1: "보합", 2: "상승"}
    rule = gap_rule_labels(gaps, np.full(len(gaps), float(band)))
    return pd.DataFrame({
        "target_date": pd.Timestamp(target_date).date().isoformat(), "gap": gaps,
        "p_down": probs[:, 0], "p_flat": probs[:, 1], "p_up": probs[:, 2], "band": float(band),
        "gap_rule_label": [labels.get(int(r), "") if np.isfinite(r) else "" for r in rule],
    })[list(POST_OPEN_GRID_COLUMNS)]


def interpolate_post_open_grid(grid, gap):
    """격자 사이를 선형 보간한다. 격자 밖이면 끝값으로 고정하고 clamped=True 를 남긴다. 합은 1로 맞춘다."""
    grid = grid.sort_values("gap")
    xs = grid["gap"].to_numpy(dtype=float)
    gap = float(gap)
    clamped = bool(gap < xs[0] or gap > xs[-1])
    x = min(max(gap, xs[0]), xs[-1])
    p = np.array([np.interp(x, xs, grid[c].to_numpy(dtype=float)) for c in ("p_down", "p_flat", "p_up")])
    p = np.clip(p, 1e-7, 1.)
    p = p / p.sum()
    band = float(grid["band"].iloc[0])
    rule = gap_rule_labels([gap], [band])[0]
    return {"p_down": float(p[0]), "p_flat": float(p[1]), "p_up": float(p[2]), "clamped": clamped,
            "band": band, "gap_rule_label": {0: "하락", 1: "보합", 2: "상승"}.get(int(rule), "") if np.isfinite(rule) else ""}


def post_open_ledger_row(grid, target_date, open_price, prev_close, prev_close_date, now_utc,
                         run_id=None, model=POST_OPEN_MODEL):
    """09:37 원장 행 하나. (행, 건너뛴 이유) — 행이 None 이면 이유가 있다.

    격자의 target_date 가 오늘 세션이 아니면(어제 격자) 쓰지 않는다 — 전일 격자로 오늘 갭을 읽으면
    07:00 정보가 다른 날의 것이다. 시가가 없으면 만들지 않는다(전일 시가를 끌어오는 것이 P16 이 막은 거짓말).
    created_at_utc 는 지금 시각이다 — cron 이 14:10 에 도착한 날은 그렇게 적혀야 한다.
    """
    import uuid
    target = pd.Timestamp(target_date).date().isoformat()
    if grid is None or len(grid) == 0 or not set(POST_OPEN_GRID_COLUMNS).issubset(grid.columns):
        return None, "post_open_grid.csv 가 없거나 열이 맞지 않습니다"
    grid_dates = set(pd.to_datetime(grid["target_date"], errors="coerce").dt.date.astype(str))
    if grid_dates != {target}:
        return None, f"격자의 target_date {sorted(grid_dates)} 가 오늘 세션 {target} 이 아닙니다"
    open_price, prev_close = _finite(open_price), _finite(prev_close)
    if open_price is None or open_price <= 0:
        return None, f"{target} 봉에 시가가 없습니다"
    if prev_close is None or prev_close <= 0:
        return None, "전일 종가가 없습니다"
    gap = open_price / prev_close - 1
    probs = interpolate_post_open_grid(grid, gap)
    now = pd.Timestamp(now_utc)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    run_id = run_id or f"postopen_{now:%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:12]}"
    p = np.array([probs["p_down"], probs["p_flat"], probs["p_up"]])
    row = {
        "schema_version": 3, "run_id": run_id, "record_id": f"{run_id}:direction:{model}",
        "created_at_utc": now.isoformat(), "prediction_date": target, "as_of_date": str(pd.Timestamp(prev_close_date).date()),
        "target_date": target, "target_mode": "close_to_close", "band": probs["band"],
        "config_hash": "post-open-grid-v1", "model": model, "kind": "direction", "horizon_days": 1,
        "prediction": {0: "하락", 1: "보합", 2: "상승"}[int(np.argmax(p))],
        "p_down": probs["p_down"], "p_flat": probs["p_flat"], "p_up": probs["p_up"],
        "current_close": prev_close, "information_cutoff": POST_OPEN_INFORMATION_CUTOFF,
        "gap": gap, "gap_rule_label": probs["gap_rule_label"], "gap_clamped": probs["clamped"],
        # 채점 열(actual_*)은 evaluate_forecasts 가 관리하므로 카드에 보일 시가는 따로 둔다.
        "open_price": open_price,
    }
    return row, ""


def post_open_row_for(ledger, target_date, model=POST_OPEN_MODEL):
    """원장(또는 daily)에서 그 예측일의 시가 반영 갱신 행. 없으면 None."""
    needed = {"model", "target_date"}
    if not isinstance(ledger, pd.DataFrame) or ledger.empty or not needed.issubset(ledger.columns):
        return None
    target = pd.Timestamp(target_date).date().isoformat()
    rows = ledger[(ledger["model"].astype(str) == model) & (ledger["target_date"].astype(str).str[:10] == target)]
    if rows.empty:
        return None
    if "created_at_utc" in rows:
        rows = rows.assign(_created=pd.to_datetime(rows["created_at_utc"], utc=True, errors="coerce")).sort_values("_created", kind="stable")
    return rows.iloc[0].to_dict()


def post_open_placeholder_html():
    """아침 보고서의 자리 표시. 09:37 회차가 replace_section 으로 카드로 바꾼다."""
    return (POSTOPEN_START
            + f'<div style="{_BOX};margin-top:8px;background:#f7f8fa;border-style:dashed;color:#6b7178;font-size:12px;line-height:1.6">'
              '<b>09:37 갱신</b> — 시가가 확정되면 이 자리에 시가를 반영한 종가 방향을 다시 냅니다 '
              '(07:00 예측은 그대로 남습니다).</div>'
            + POSTOPEN_END)


def post_open_card_html(row, target=None, morning=None):
    """시가 반영 갱신 카드(표시 사이에 들어갈 본문). 정직 규칙: 실제 실행 시각·갭 규칙·07:00 예측을 함께 적는다."""
    from html import escape
    row = row if hasattr(row, "get") else {}
    created = pd.to_datetime(row.get("created_at_utc"), utc=True, errors="coerce")
    when = f"{created.tz_convert('Asia/Seoul'):%H:%M}" if pd.notna(created) else "시각 미상"
    call = direction_call(row)
    open_price, gap = _finite(row.get("open_price")), _finite(row.get("gap"))
    open_text = (f"시가 {open_price:,.0f}원" if open_price is not None else "시가") + (f"(갭 {gap:+.2%})" if gap is not None else "")
    rule = str(row.get("gap_rule_label") or "").strip()
    rule_text = f" · 갭 규칙만으로도 ‘{rule}’" if rule else ""
    morning_call = direction_call(morning if hasattr(morning, "get") else {})
    morning_text = (f" · 07:00 예측(‘{morning_call['label'].strip('▼▲ ')}’ {morning_call['max_prob']:.0%})은 위 카드 그대로"
                    if morning_call["valid"] else " · 07:00 예측은 위 카드 그대로")
    record = POST_OPEN_TRACK_RECORD.get(target or "", None)
    record_text = (f" — 과거 검증에서 07:00 {record['pre_open']:.0%}·시가 반영 {record['post_open']:.0%}, "
                   f"갭 부호만 읽어도 {record['gap_rule']:.0%}" if record else "")
    late = " · 예정 09:37 보다 늦게 실행됨" if pd.notna(created) and (created.tz_convert("Asia/Seoul").hour, created.tz_convert("Asia/Seoul").minute) > (10, 0) else ""
    clamped = " · 갭이 격자(±10%) 밖이라 끝값으로 고정" if str(row.get("gap_clamped", "")).lower() == "true" else ""
    verdict = f"{call['label']} ({call['max_prob']:.0%})" if call["valid"] else "판단 어려움"
    return (f'<div style="{_BOX};margin-top:8px;border-color:#d9c48a;background:#fffdf5">'
            f'<div style="font-size:13px;font-weight:700;margin-bottom:4px">시가 반영 갱신 '
            f'<span style="font-weight:400;color:#7a8797;font-size:12px">(실제 실행 {escape(when)} KST{escape(late)})</span></div>'
            f'<div style="font-size:14px;line-height:1.6">{escape(open_text)}을 반영한 종가 방향: <b>{escape(verdict)}</b>'
            f'{escape(rule_text)}{escape(morning_text)}{escape(clamped)}</div>'
            f'<div style="font-size:12px;color:#6b7178;margin-top:4px;line-height:1.6"><b>모델이 좋아진 것이 아니라 정보가 늘어난 것입니다</b>'
            f'{escape(record_text)}. 시가에 행동하려는 사람에게는 쓸모가 없습니다 — 갭은 이미 가격에 들어가 있습니다. '
            '정보 마감 15:30 이전 예측으로 따로 채점하며 대표 성적에는 섞지 않습니다.</div></div>')


def post_open_block_html(row=None, target=None, morning=None):
    """표시를 포함한 전체 블록. 행이 있으면 카드, 없으면 자리 표시."""
    if row is None:
        return post_open_placeholder_html()
    return POSTOPEN_START + post_open_card_html(row, target=target, morning=morning) + POSTOPEN_END


def atomic_csv(frame, path):
    """단일 실행자용 원자적 교체. 중간에 런타임이 끊겨도 기존 원장을 보존한다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
            frame.to_csv(stream, index=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def append_forecasts(path, new_rows):
    """예측은 불변: 같은 record_id 재실행은 무시하고, 새 run_id는 추가한다."""
    path = Path(path)
    previous = pd.read_csv(path) if path.exists() else pd.DataFrame()
    if len(previous):
        if "record_id" not in previous:
            previous["record_id"] = pd.Series(index=previous.index, dtype="str")
        for i in previous.index[previous.record_id.isna()]:
            payload = previous.loc[i].to_json() + str(i)
            previous.loc[i, "record_id"] = "legacy-" + hashlib.sha256(payload.encode()).hexdigest()[:20]
    combined = pd.concat([previous, new_rows], ignore_index=True)
    combined = combined.drop_duplicates("record_id", keep="first")
    atomic_csv(combined, path)
    return combined


def merge_ledger_logs(remote, ours, bars, evaluate=None):
    """원장을 저장하기 직전에, 이 실행이 읽은 뒤 다른 실행이 바꿨을 수 있는 최신 원장(remote)과 합쳐 다시 채점한다.

    같은 record_id 는 remote 쪽을 남긴다. 예측 칸은 불변이라 같고, 채점 칸은 evaluate_forecasts 가 bars 로 다시
    매기되 bars 에 봉이 없는 행은 이미 채점된 값을 되돌리지 않는다 — 그래서 다른 실행이 끝낸 채점(예: 09:37
    시초가)이 남는다. 전에는 노트북이 이 실행 쪽(keep="last")을 남기고 다시 채점하지 않아, 시작 뒤에 끝난 채점을
    채점 전 값으로 덮을 수 있었다(2026-09-23). ours 에만 있는 record_id(이번 실행의 새 예측)를 더한다.
    remote 가 None 이거나 비어 있으면 ours 만 채점한다.
    """
    evaluate = evaluate or evaluate_forecasts
    if remote is None or len(remote) == 0:
        return evaluate(ours.copy(), bars)
    extra = ours[ours["record_id"].notna() & ~ours["record_id"].isin(remote["record_id"])]
    return evaluate(pd.concat([remote, extra], ignore_index=True), bars)


def evaluate_forecasts(log, bars, now=None):
    """확정 봉으로 실제값/오차를 갱신한다. 당시 band/mode 및 원래 예측은 보존한다."""
    result = log.copy()
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    market_now = now.tz_convert("Asia/Seoul")
    bars = bars.sort_index().copy()
    bars.index = pd.DatetimeIndex(bars.index).tz_localize(None).normalize()
    numeric = ["actual_open", "actual_close", "actual_return", "actual_class", "direction_correct",
               "price_error", "absolute_price_error", "price_ape", "return_error", "interval_hit",
               "center_price_error", "log_loss", "brier"]
    # 이번 시세에 봉이 없다고 이미 채점된 행을 지우지 않는다. 장중에 코드 반영 실행이 돌면 미완성 당일
    # 봉을 통째로 버리는데, 그때 아침에 끝난 시초가 채점이 missing_actual 로 되돌아가 보고서에서
    # 사라졌다(2026-09-10 14:12 채점 → 14:47 코드 반영 실행이 덮어씀). 실제 시가가 없어진 것이 아니다.
    previous = log.reindex(columns=numeric + ["status", "actual_updated_at_utc"])

    def keep_previous(i):
        if str(previous.loc[i, "status"]) != "scored":
            return False
        result.loc[i, numeric] = previous.loc[i, numeric].values
        result.loc[i, "status"] = "scored"
        result.loc[i, "actual_updated_at_utc"] = previous.loc[i, "actual_updated_at_utc"]
        return True

    for col in numeric:
        result[col] = np.nan
    result["status"] = "pending"
    result["is_prospective"] = False
    result["actual_updated_at_utc"] = now.isoformat()
    for i, row in result.iterrows():
        target_value = row.get("target_date")
        target_value = row.get("prediction_date") if pd.isna(target_value) else target_value
        target = pd.to_datetime(target_value, errors="coerce")
        if pd.isna(target):
            result.loc[i, "status"] = "invalid_target"
            continue
        target = pd.Timestamp(target).tz_localize(None).normalize()
        start = pd.to_datetime(row.get("prediction_date", target), errors="coerce")
        created = pd.to_datetime(row.get("created_at_utc"), utc=True, errors="coerce")
        mode = row.get("target_mode", "close_to_close")
        # 09:00 시가 기반 예측은 시가 확인 직후(09:05까지)만 별도 집계한다.
        # information_cutoff 열(없으면 pre_open): pre_open 은 target_date 09:00 KST 전에 만든 행만 사전 예측이다.
        # post_open(시가 반영 갱신) 은 정보 마감이 그날 종가(15:30 KST)이므로 그 전에 만든 행만 사전 예측이다.
        # 'is_prospective' 의 뜻은 두 경우 모두 같다 — 자기 정보 마감보다 먼저 낸 예측인가.
        cutoff = row.get("information_cutoff", PRE_OPEN_INFORMATION_CUTOFF)
        cutoff = PRE_OPEN_INFORMATION_CUTOFF if pd.isna(cutoff) or not str(cutoff).strip() else str(cutoff).strip()
        if cutoff == POST_OPEN_INFORMATION_CUTOFF and pd.notna(created):
            deadline = target.tz_localize("Asia/Seoul") + pd.Timedelta(hours=15, minutes=30)
            result.loc[i, "is_prospective"] = bool(created < deadline)
        elif pd.notna(start) and pd.notna(created):
            deadline = pd.Timestamp(start).tz_localize(None).normalize().tz_localize("Asia/Seoul") + pd.Timedelta(hours=9)
            if mode == "open_to_close":
                deadline += pd.Timedelta(minutes=5)
            result.loc[i, "is_prospective"] = bool(created < deadline)
        kind = row.get("kind", "direction")
        if pd.isna(kind):
            kind = "direction"
        # 시가는 09:00에 확정되므로 그날 오전에 채점할 수 있다. 종가는 15:30 마감 뒤라야 한다.
        # (야후 반영 여유를 두어 09:05 / 15:40을 쓴다.)
        ready_at = (9, 5) if kind == "open" else (15, 40)
        if target.date() > market_now.date() or (target.date() == market_now.date()
                                                 and (market_now.hour, market_now.minute) < ready_at):
            continue
        if target not in bars.index:
            if not keep_previous(i):
                result.loc[i, "status"] = "missing_actual"
            continue
        bar = bars.loc[target]
        needed = "open" if kind == "open" else "close"
        if not np.isfinite(bar[needed]) or bar[needed] <= 0:
            if not keep_previous(i):
                result.loc[i, "status"] = "missing_actual"
            continue
        result.loc[i, ["actual_open", "actual_close"]] = [bar["open"], bar["close"]]
        if kind in ("price", "open"):
            # "price": 전일 종가 대비 target_date 종가. "open": 전일 종가 대비 target_date 시가(갭).
            # 시가 예측은 09:00 이전에만 의미가 있으므로 열 이름을 *_open 으로 분리해 종가 예측과 섞지 않는다.
            base = row.get("current_close", np.nan)
            if not np.isfinite(base) or base <= 0:
                result.loc[i, "status"] = "missing_reference"
                continue
            price_col = "close" if kind == "price" else "open"
            actual_price = bar[price_col]
            if not np.isfinite(actual_price) or actual_price <= 0:
                result.loc[i, "status"] = "missing_actual"
                continue
            actual_return = float(actual_price / base - 1)
            predicted = row.get(f"predicted_{price_col}", np.nan)
            if pd.notna(predicted):
                error = float(predicted - actual_price)
                result.loc[i, ["price_error", "absolute_price_error", "price_ape"]] = [error, abs(error), abs(error) / actual_price]
            center = row.get(f"center_{price_col}", np.nan)
            if pd.notna(center):
                result.loc[i, "center_price_error"] = center - actual_price
            predicted_return = row.get("predicted_return", np.nan)
            if pd.notna(predicted_return):
                result.loc[i, "return_error"] = predicted_return - actual_return
            low, high = row.get(f"low_{price_col}", np.nan), row.get(f"high_{price_col}", np.nan)
            if pd.notna(low) and pd.notna(high):
                result.loc[i, "interval_hit"] = float(low <= actual_price <= high)
        else:
            if mode == "open_to_close":
                if not np.isfinite(bar["open"]) or bar["open"] <= 0:
                    result.loc[i, "status"] = "missing_actual"
                    continue
                actual_return = float(bar["close"] / bar["open"] - 1)
            elif mode == "close_to_close":
                if not np.isfinite(bar["adj_close"]) or bar["adj_close"] <= 0:
                    result.loc[i, "status"] = "missing_actual"
                    continue
                base_date = pd.to_datetime(row.get("as_of_date"), errors="coerce")
                if pd.isna(base_date):
                    earlier = bars.index[bars.index < target]
                    base_date = earlier[-1] if len(earlier) else pd.NaT
                if base_date not in bars.index or base_date >= target:
                    result.loc[i, "status"] = "missing_reference"
                    continue
                if not np.isfinite(bars.loc[base_date, "adj_close"]) or bars.loc[base_date, "adj_close"] <= 0:
                    result.loc[i, "status"] = "missing_reference"
                    continue
                # 양쪽 배당조정 가격은 같은 최신 스냅샷에서 읽어 조정계수 변경을 상쇄한다.
                actual_return = float(bar["adj_close"] / bars.loc[base_date, "adj_close"] - 1)
            else:
                result.loc[i, "status"] = "invalid_target_mode"
                continue
            band = row.get("band", np.nan)
            if pd.isna(band) or band < 0:
                result.loc[i, "status"] = "missing_band"
                continue
            actual_class = 0 if actual_return < -band else 2 if actual_return > band else 1
            p = np.asarray([row.get(c, np.nan) for c in ["p_down", "p_flat", "p_up"]], dtype=float)
            result.loc[i, "actual_class"] = actual_class
            if np.isfinite(p).all() and (p >= 0).all() and p.sum() > 0:
                p = p / p.sum()
                result.loc[i, "direction_correct"] = float(p.argmax() == actual_class)
                result.loc[i, "log_loss"] = -np.log(max(p[actual_class], 1e-7))
                result.loc[i, "brier"] = float(np.sum((p - np.eye(3)[actual_class]) ** 2))
        result.loc[i, "actual_return"] = actual_return
        result.loc[i, "status"] = "scored"
    # 채점 결과가 그대로인 행은 갱신 시각도 그대로 둔다. 매 실행 모든 행의 시각이 바뀌어 원장 파일 전체가
    # 다시 쓰였다(금·은 3시간마다 ~1,450줄, 2026-09-24). 시각은 '채점이 마지막으로 바뀐 때'가 된다.
    if len(result) and previous["actual_updated_at_utc"].notna().any():
        old = previous[numeric].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        new = result[numeric].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        same_values = np.isclose(old, new, rtol=1e-12, atol=0.0, equal_nan=True).all(axis=1)
        same = (same_values & (previous["status"].astype(str).to_numpy() == result["status"].astype(str).to_numpy())
                & previous["actual_updated_at_utc"].notna().to_numpy())
        result.loc[same, "actual_updated_at_utc"] = previous.loc[same, "actual_updated_at_utc"]
    return result


# 공식 예측은 '그 거래일 아침에 만든 예측'이다(2026-09-28 예측일부터). 평일에는 원래 그랬지만 연휴에는 휴장 첫날
# 아침이 다음 개장일 예측을 먼저 기록해, 개장일 아침의 예측(연휴 중 미국 장을 다 본 것)이 중복으로 버려졌다
# (2026-09-24 에 9/28 예측을 기록 → 9/24·9/25 미국 장이 빠진 예측이 공식이 될 뻔했다). 그래서 예측일 당일(KST)에
# 만든 사전 예측을 먼저 고르고, 없을 때만 예전처럼 가장 먼저 기록된 사전 예측을 쓴다. 규칙은 미리 정한 것이고
# 모두 09:00 전 예측이라 '결과를 보고 고르기'가 아니다. 이미 채점된 과거(9/28 이전 예측일)는 바꾸지 않는다.
# 저녁 후보(Candidate evening …)는 '전날 저녁' 예측이 정의라 이 규칙을 따르지 않는다.
SAME_DAY_OFFICIAL_FROM = "2026-09-28"


def same_day_rank(frame):
    """0 = 예측일 당일(KST)에 만든 사전 예측(규칙 적용 대상), 1 = 그 밖. 정렬 키로 쓴다."""
    if frame.empty or "created_at_utc" not in frame:
        return pd.Series(1, index=frame.index)
    created = pd.to_datetime(frame["created_at_utc"], utc=True, errors="coerce")
    created_day = created.dt.tz_convert("Asia/Seoul").dt.strftime("%Y-%m-%d")
    column = "prediction_date" if "prediction_date" in frame else "target_date"
    day = frame[column].astype(str).str[:10]
    model = frame["model"].astype(str) if "model" in frame else pd.Series("", index=frame.index)
    applies = (day >= SAME_DAY_OFFICIAL_FROM) & ~model.str.startswith("Candidate evening")
    return (~(applies & (created_day == day))).astype(int)


def daily_comparison(evaluated):
    """동일 날짜/모델/설정의 사전 예측 하나만 선택하여 재실행으로 표본이 늘지 않게 한다.

    고르는 규칙: 예측일 당일 아침에 만든 사전 예측이 있으면 그중 가장 먼저 것, 없으면 가장 먼저 기록된 사전 예측
    (SAME_DAY_OFFICIAL_FROM 앞의 예측일은 예전 규칙 그대로 — 가장 먼저 기록된 것).
    """
    if evaluated.empty:
        return evaluated.copy()
    eligible = evaluated.loc[evaluated["is_prospective"].eq(True)].copy()
    if "created_at_utc" not in eligible:
        eligible["created_at_utc"] = pd.NaT
    if "record_id" not in eligible:
        eligible["record_id"] = pd.Series(index=eligible.index, dtype="str")
    eligible["created_at_utc"] = pd.to_datetime(eligible["created_at_utc"], utc=True)
    keys = ["target_date", "model", "kind", "horizon_days", "target_mode", "config_hash"]
    for key in keys:
        if key not in eligible:
            eligible[key] = "legacy"
    eligible["_same_day_rank"] = same_day_rank(eligible)
    chosen = eligible.sort_values(["_same_day_rank", "created_at_utc"], kind="stable").drop_duplicates(keys, keep="first")
    return chosen.drop(columns="_same_day_rank").sort_values("created_at_utc", kind="stable")


SUMMARY_COLUMNS = ["model", "kind", "horizon_days", "target_mode", "config_hash", "n", "accuracy",
                   "mean_log_loss", "mean_brier", "point_forecasts", "price_mae", "price_mape",
                   "interval_coverage"]


def summarize_daily(daily):
    """모델·종류별 누적 성적. 원장이 비어 있어도 깨지지 않아야 한다.

    기록하지 않는 실행(기록 창 밖, push 실행)에서는 원장이 없을 수 있고, 그때 daily 는 열조차
    없는 빈 프레임이다. 예전에는 groupby 가 KeyError('model') 로 죽어 보고서가 통째로 실패했다.
    """
    if daily is None or len(daily) == 0 or "status" not in daily.columns:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    scored = daily.loc[daily.status == "scored"]
    if scored.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    return scored.groupby(["model", "kind", "horizon_days", "target_mode", "config_hash"], dropna=False).agg(
        n=("record_id", "size"), accuracy=("direction_correct", "mean"),
        mean_log_loss=("log_loss", "mean"), mean_brier=("brier", "mean"),
        point_forecasts=("absolute_price_error", "count"), price_mae=("absolute_price_error", "mean"),
        price_mape=("price_ape", "mean"), interval_coverage=("interval_hit", "mean"),
    ).reset_index()


def _pending_status(daily, ensemble_model, last_scored_date):
    """마지막으로 완전히 채점된 날보다 뒤에 있는 예측일의 항목별 상태.

    오후 1시에 보면 시초가는 채점됐지만 종가는 아직이다. 그 날짜를 숨기고 전날 결과만 보여 주면
    '오늘 종가가 벌써 판정됐나' 하는 오해가 생긴다. 항목마다 '채점됨/판정 전'을 그대로 적는다.
    """
    if daily is None or len(daily) == 0 or "target_date" not in daily:
        return None
    frame = daily.copy()
    frame["target_date"] = pd.to_datetime(frame["target_date"]).dt.tz_localize(None).dt.normalize()
    if "is_prospective" in frame:
        frame = frame[frame["is_prospective"].astype(str).str.lower().isin(("true", "1", "yes"))]
    if frame.empty:
        return None
    newest = frame["target_date"].max()
    if pd.notna(last_scored_date) and newest <= pd.Timestamp(last_scored_date):
        # 최신 예측일이 이미 완전히 채점된 날이면 따로 보여 줄 것이 없다.
        fully = frame[frame["target_date"] == newest]
        if (fully["status"] == "scored").all():
            return None
    rows = frame[frame["target_date"] == newest]
    horizon = pd.to_numeric(rows.get("horizon_days", 1), errors="coerce").fillna(1).astype(int)
    items = {}
    for kind, label, mask in (
            ("open", "시초가(갭)", rows["kind"] == "open"),
            ("direction", "종가 방향", (rows["kind"] == "direction") & (rows["model"] == ensemble_model)),
            ("price", "1거래일 종가예측", (rows["kind"] == "price") & (horizon == 1))):
        sub = rows[mask]
        if sub.empty:
            continue
        items[kind] = {"label": label, "status": str(sub["status"].iloc[0]), "row": sub.iloc[0]}
    return {"date": pd.Timestamp(newest), "items": items} if items else None


def interval_coverage_by_event(daily, models, min_n=10):
    """구간 적중률을 이벤트일/평일로 나눠 모델별로 비교한다.

    내재변동성 구간(Candidate IV interval)의 존재 이유는 '이벤트일에 구간이 좁다'는 문제다.
    전체 적중률로는 그 차이가 묻히므로 원장의 event_flags 로 나눠 본다. 표본이 min_n 미만인 칸은
    None 으로 두어 숫자가 정확해 보이지 않게 한다.
    """
    if daily is None or len(daily) == 0 or "interval_hit" not in daily:
        return pd.DataFrame()
    frame = daily.copy()
    frame = frame[(frame["status"] == "scored") & frame["interval_hit"].notna()]
    if "is_prospective" in frame:
        frame = frame[frame["is_prospective"].astype(str).str.lower().isin(("true", "1", "yes"))]
    flags = frame.get("event_flags", pd.Series("", index=frame.index)).fillna("").astype(str)
    frame = frame.assign(_event=flags.str.len().gt(0))
    rows = []
    for model in models:
        sub = frame[frame["model"] == model]
        for kind in sorted(sub["kind"].dropna().unique()):
            for horizon in sorted(pd.to_numeric(sub["horizon_days"], errors="coerce").dropna().unique()):
                cell = sub[(sub["kind"] == kind) & (pd.to_numeric(sub["horizon_days"], errors="coerce") == horizon)]
                ev, normal = cell[cell["_event"]], cell[~cell["_event"]]
                rows.append({
                    "model": model, "kind": kind, "horizon_days": int(horizon),
                    "n_event": int(len(ev)), "n_normal": int(len(normal)),
                    "coverage_event": float(ev["interval_hit"].mean()) if len(ev) >= min_n else None,
                    "coverage_normal": float(normal["interval_hit"].mean()) if len(normal) >= min_n else None,
                })
    return pd.DataFrame(rows)


def overnight_value_html(daily, headline_model, evening_model="Candidate evening forecast",
                         evening_open_model="Candidate evening open", min_n=10):
    """아침 예측 vs 저녁 예측 — 밤사이 미국 시장 정보가 실제로 얼마나 기여하나.

    같은 예측일을 두 시점에서 예측한다. 아침(06:22 KST)은 미국 장 마감 후라 갭 정보가 있고,
    저녁(18:22 KST)은 미국 장이 열리기도 전이라 없다. 두 적중률의 차이가 그 정보의 값이다.
    이 저장소가 말해 온 '갭 AUC 0.80, 세션 AUC 0.50'의 직접 검증이다.
    """
    if daily is None or len(daily) == 0 or "model" not in daily:
        return ""
    scored = daily[daily["status"] == "scored"].copy()
    if "is_prospective" in scored:
        scored = scored[scored["is_prospective"].astype(str).str.lower().isin(("true", "1", "yes"))]
    if scored.empty:
        return ""
    cell = 'style="padding:7px 11px;border-top:1px solid #eee;text-align:right"'
    head_style = 'style="background:#fafafa;font-size:11px;color:#6b7178"'

    def paired(frame, morning_mask, evening_mask):
        """같은 예측일에 아침·저녁 행이 모두 있는 날만. (아침 행, 저녁 행)"""
        morning, evening = frame[morning_mask], frame[evening_mask]
        common = set(morning["target_date"]) & set(evening["target_date"])
        return morning[morning["target_date"].isin(common)], evening[evening["target_date"].isin(common)]

    # 1) 종가 방향: 아침 대표 모델 vs 저녁 후보
    direction = scored[scored["kind"] == "direction"]
    morning, evening = paired(direction, direction["model"] == headline_model, direction["model"] == evening_model)
    rows, n_min = "", None
    if len(morning) and len(evening):
        for label, sub in (("아침 (갭 정보 있음)", morning), ("저녁 (갭 정보 없음)", evening)):
            value = "—" if len(sub) < min_n else f'{sub["direction_correct"].mean():.0%}'
            rows += (f'<tr><td style="padding:7px 11px;border-top:1px solid #eee">{label}</td>'
                     f'<td {cell}>{value}</td><td {cell};color:#8a9199">n={len(sub)}</td></tr>')
        n_min = min(len(morning), len(evening))
    # 2) 시초가(갭): 아침 대표 행 vs 저녁 후보. 아침 시초가 예측은 밤사이 미국 시장을 보고 내는 값이라
    #    맞히기 쉽다(2026-09-16 지적). 전날 저녁에 낸 시초가 예측을 실제 시가로 채점한 것이 공정한 성적이다.
    opens = scored[scored["kind"] == "open"]
    open_morning, open_evening = paired(opens, is_headline_model(opens["model"]),
                                        opens["model"] == evening_open_model)
    open_rows = ""
    if len(open_morning) and len(open_evening):
        for label, sub in (("아침 (갭 정보 있음)", open_morning), ("저녁 (갭 정보 없음)", open_evening)):
            if len(sub) < min_n:
                value = "—"
            else:
                value = f'{sub["interval_hit"].mean():.0%}'
                error = pd.to_numeric(sub.get("return_error"), errors="coerce").abs().mean()
                if pd.notna(error):
                    value += f' · MAE {error:.2%}'
            open_rows += (f'<tr><td style="padding:7px 11px;border-top:1px solid #eee">{label}</td>'
                          f'<td {cell}>{value}</td><td {cell};color:#8a9199">n={len(sub)}</td></tr>')
        n_min = min(len(open_morning), len(open_evening), n_min if n_min is not None else 10 ** 9)
    if not rows and not open_rows:
        return ""
    note = ("같은 예측일만 짝지어 비교합니다. 표본 10일 미만은 — 로 둡니다."
            if n_min is not None and n_min < min_n else
            "아침이 높으면 밤사이 미국 시장 정보가 실제로 기여한다는 뜻입니다.")
    if open_rows:
        note += " 시초가는 전날 저녁에 낸 시초가 예측이 공정한 성적입니다 — 아침 예측은 미국 시장을 이미 본 값입니다."
    out = ('<div style="font-size:12px;color:#6b7178;margin:14px 0 4px">밤사이 정보의 값 — '
           '같은 날을 아침·저녁 두 시점에서 예측해 각각 채점한 결과</div>')
    if rows:
        out += ('<div style="overflow-x:auto"><table style="width:100%;min-width:380px;border-collapse:collapse;'
                f'font-size:12px;border:1px solid #e5e5e5"><tr {head_style}>'
                '<th style="padding:8px 11px;text-align:left">예측 시점</th>'
                '<th style="padding:8px 11px;text-align:right">방향 적중률</th>'
                f'<th style="padding:8px 11px;text-align:right">표본</th></tr>{rows}</table></div>')
    if open_rows:
        out += ('<div style="overflow-x:auto;margin-top:6px"><table style="width:100%;min-width:380px;'
                f'border-collapse:collapse;font-size:13px;border:1px solid #e5e5e5"><tr {head_style}>'
                '<th style="padding:8px 11px;text-align:left">예측 시점</th>'
                '<th style="padding:8px 11px;text-align:right">시초가 구간 적중률 · 갭 오차</th>'
                f'<th style="padding:8px 11px;text-align:right">표본</th></tr>{open_rows}</table></div>')
    return out + f'<div style="font-size:11px;color:#8a9199;margin-top:4px">{note}</div>'


def price_position(close, windows=(20, 60, 120), lookahead=20, band=0.10, min_samples=20):
    """지금 가격이 최근 범위의 어디쯤인지, 그리고 과거에 같은 자리였을 때 뒤에 어땠는지.

    '저점인가'에는 답하지 않는다 — 저점은 지나야 알 수 있고, 그것을 말하는 순간 매수 의견이 된다.
    대신 (1) 최근 범위 안의 위치, (2) 이동평균·고점 대비 거리, (3) 과거에 같은 위치였던 날들의
    lookahead 거래일 뒤 수익률 분포를 '전체 기간 분포'와 나란히 돌려준다. 같은 위치라고 반등이
    더 잦았는지는 그 비교로만 말할 수 있다. 표본이 min_samples 미만이면 분포를 비우고 사유를 적는다.
    """
    close = pd.Series(close).astype(float).dropna()
    if len(close) < max(windows) + lookahead + 5:
        return None
    last = float(close.iloc[-1])
    out = {"last": last, "date": close.index[-1], "ranges": {}, "moving_averages": {}}
    for w in windows:
        window = close.iloc[-w:]
        lo, hi = float(window.min()), float(window.max())
        out["ranges"][w] = {"low": lo, "high": hi,
                            "position": (last - lo) / (hi - lo) if hi > lo else 0.5}
        out["moving_averages"][w] = {"level": float(window.mean()), "gap": last / float(window.mean()) - 1}
    # 3년 고점 대비 낙폭
    peak = float(close.iloc[-756:].max()) if len(close) >= 756 else float(close.max())
    out["drawdown_3y"] = last / peak - 1

    # 과거에 지금과 같은 위치(60일 범위 내 ±band)였던 날들의 lookahead 뒤 수익률
    w = 60
    rolling_lo = close.rolling(w).min()
    rolling_hi = close.rolling(w).max()
    rank = ((close - rolling_lo) / (rolling_hi - rolling_lo)).replace([np.inf, -np.inf], np.nan)
    future = close.shift(-lookahead) / close - 1
    valid = rank.notna() & future.notna()
    # 마지막 lookahead 일은 미래를 모르므로 제외된다(future 가 NaN)
    now_rank = float(rank.iloc[-1])
    similar = valid & (rank - now_rank).abs().le(band)
    baseline = future[valid]
    sample = future[similar]
    def describe(series):
        if len(series) == 0:
            return None
        return {"n": int(len(series)), "median": float(series.median()),
                "q25": float(series.quantile(.25)), "q75": float(series.quantile(.75)),
                "up_share": float((series > 0).mean())}
    out["position_60"] = now_rank
    out["lookahead"] = lookahead
    out["similar"] = describe(sample) if len(sample) >= min_samples else None
    out["similar_n"] = int(len(sample))
    out["baseline"] = describe(baseline)
    out["min_samples"] = min_samples
    return out


def price_position_svg(close, pos, unit="원", width=900, height=392):
    """'지금 가격은 어디쯤인가'를 한 그림으로(2026-09-28 요청: '단기 위치 62%'만으로는 이해하기 어렵다).

    위: 최근 120거래일 종가와 60거래일(석 달) 최저~최고 범위(음영), 60일 평균(점선), 지금 위치(%).
    가운데: 한 달(20일)·석 달(60일)·반년(120일) 범위 막대마다 지금이 어디쯤인지.
    아래: 과거에 60일 범위의 같은 자리였던 날 한 달 뒤 오른 비율 vs 평소. 표본이 모자라면 그렇게 적는다.
    """
    from html import escape
    prices = pd.Series(close).astype(float).dropna()
    if not pos or len(prices) < 130:
        return ""
    recent = prices.iloc[-120:]
    left, right = 78, 150
    top, chart_h = 44, 150
    lo_all, hi_all = float(recent.min()), float(recent.max())
    pad = (hi_all - lo_all) * 0.08 or hi_all * 0.02
    y_lo, y_hi = lo_all - pad, hi_all + pad
    n = len(recent)

    def money(v):
        return f"{v:,.0f}{unit}" if unit == "원" else f"{unit}{v:,.2f}"

    def x(i):
        return left + (width - left - right) * i / (n - 1)

    def y(v):
        return top + chart_h * (y_hi - v) / (y_hi - y_lo)

    r60 = pos["ranges"][60]
    start60 = n - 60
    band = (f'<rect x="{x(start60):.1f}" y="{y(r60["high"]):.1f}" width="{x(n - 1) - x(start60):.1f}" '
            f'height="{y(r60["low"]) - y(r60["high"]):.1f}" fill="#1a5490" opacity="0.07"/>'
            f'<line x1="{x(start60):.1f}" x2="{x(n - 1):.1f}" y1="{y(r60["high"]):.1f}" y2="{y(r60["high"]):.1f}" '
            'stroke="#1a5490" stroke-dasharray="3,3" opacity="0.6"/>'
            f'<line x1="{x(start60):.1f}" x2="{x(n - 1):.1f}" y1="{y(r60["low"]):.1f}" y2="{y(r60["low"]):.1f}" '
            'stroke="#1a5490" stroke-dasharray="3,3" opacity="0.6"/>'
            f'<text x="{x(n - 1) + 8:.1f}" y="{y(r60["high"]) + 4:.1f}" font-size="11" fill="#1a5490">석 달 최고 {money(r60["high"])}</text>'
            f'<text x="{x(n - 1) + 8:.1f}" y="{y(r60["low"]) + 4:.1f}" font-size="11" fill="#1a5490">석 달 최저 {money(r60["low"])}</text>'
            f'<text x="{x(start60) + 4:.1f}" y="{top - 6}" font-size="10" fill="#1a5490">← 최근 60거래일(석 달) 범위</text>')
    ma = prices.rolling(60).mean().iloc[-120:].to_numpy()
    ma_line = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(ma) if np.isfinite(v))
    line = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(recent.to_numpy()))
    last = float(recent.iloc[-1])
    p60 = pos["position_60"]
    months = ""
    seen = set()
    for i, d in enumerate(recent.index):
        key = (d.year, d.month)
        if key not in seen and i > 3:
            months += (f'<text x="{x(i):.1f}" y="{top + chart_h + 14}" text-anchor="middle" font-size="10" '
                       f'fill="#8a9199">{d.month}월</text>')
        seen.add(key)
    y_ticks = ""
    for t in np.linspace(lo_all, hi_all, 3):
        y_ticks += (f'<text x="{left - 8}" y="{y(t) + 4:.1f}" text-anchor="end" font-size="10" fill="#8a9199">'
                    f'{money(t)}</text>')
    price_part = (band + f'<polyline points="{ma_line}" fill="none" stroke="#8a9199" stroke-width="1.2" stroke-dasharray="5,3"/>'
                  f'<polyline points="{line}" fill="none" stroke="#1a1a1a" stroke-width="1.8"/>'
                  f'<circle cx="{x(n - 1):.1f}" cy="{y(last):.1f}" r="4.5" fill="#c0392b"/>'
                  f'<text x="{x(n - 1) + 8:.1f}" y="{y(last) - 2:.1f}" font-size="12" font-weight="700" fill="#c0392b">'
                  f'지금 {money(last)}</text>'
                  f'<text x="{x(n - 1) + 8:.1f}" y="{y(last) + 12:.1f}" font-size="11" font-weight="700" fill="#c0392b">'
                  f'석 달 범위 {p60:.0%} 지점</text>' + months + y_ticks)

    # 가운데: 범위 막대 셋
    gauges = ""
    gy = top + chart_h + 40
    bar_x0, bar_x1 = left + 150, width - right - 40
    for label, w in (("한 달(20일)", 20), ("석 달(60일)", 60), ("반년(120일)", 120)):
        r = pos["ranges"][w]
        p = min(max(r["position"], 0.0), 1.0)
        mx = bar_x0 + (bar_x1 - bar_x0) * p
        bold = ' font-weight="700"' if w == 60 else ""
        gauges += (f'<text x="{left}" y="{gy + 5}" font-size="12" fill="#3a4652"{bold}>{label}</text>'
                   f'<rect x="{bar_x0}" y="{gy - 5}" width="{bar_x1 - bar_x0}" height="10" rx="5" fill="#eef1f4"/>'
                   f'<rect x="{bar_x0}" y="{gy - 5}" width="{mx - bar_x0:.1f}" height="10" rx="5" fill="#1a5490" opacity="0.25"/>'
                   f'<circle cx="{mx:.1f}" cy="{gy}" r="6" fill="#c0392b" stroke="#fff" stroke-width="1.5"/>'
                   f'<text x="{bar_x0 - 6}" y="{gy + 4}" text-anchor="end" font-size="10" fill="#8a9199">최저</text>'
                   f'<text x="{bar_x1 + 6}" y="{gy + 4}" font-size="10" fill="#8a9199">최고</text>'
                   f'<text x="{bar_x1 + 36}" y="{gy + 5}" font-size="12" font-weight="700" fill="#c0392b">{r["position"]:.0%}</text>')
        gy += 26

    # 아래: 과거 같은 자리 → 한 달 뒤 오른 비율
    sim, base = pos.get("similar"), pos.get("baseline")
    cy = gy + 16
    if sim and base:
        def share_bar(yy, label, share, color):
            return (f'<text x="{left}" y="{yy + 5}" font-size="12" fill="#3a4652">{escape(label)}</text>'
                    f'<rect x="{bar_x0}" y="{yy - 6}" width="{(bar_x1 - bar_x0) * share:.1f}" height="12" fill="{color}"/>'
                    f'<text x="{bar_x0 + (bar_x1 - bar_x0) * share + 6:.1f}" y="{yy + 5}" font-size="12" '
                    f'font-weight="700" fill="{color}">{share:.0%}</text>')
        compare = (f'<text x="{left}" y="{cy - 12}" font-size="11" fill="#6b7178">한 달(20거래일) 뒤 오른 비율</text>'
                   + share_bar(cy + 6, f"같은 자리({sim['n']:,}번)", sim["up_share"], "#c0392b")
                   + share_bar(cy + 26, f"평소({base['n']:,}일)", base["up_share"], "#8a9199"))
    else:
        compare = (f'<text x="{left}" y="{cy}" font-size="12" fill="#6b7178">과거에 같은 자리였던 날이 '
                   f'{pos.get("similar_n", 0)}번뿐이라 한 달 뒤 비교는 하지 않습니다.</text>')
    title = (f'<text x="{left}" y="18" font-size="13" font-weight="600" fill="#1a1a1a">지금 가격은 최근 범위의 어디쯤인가</text>'
             f'<text x="{width - 10}" y="18" text-anchor="end" font-size="11" fill="#8a9199">'
             '검은 선: 종가 · 점선: 60일 평균 · 빨간 점: 지금</text>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" xmlns="http://www.w3.org/2000/svg" '
            f'style="max-width:{width}px;font-family:-apple-system,\'Malgun Gothic\',sans-serif">'
            f'<rect width="{width}" height="{height}" fill="#fff"/>{title}{price_part}{gauges}{compare}</svg>')


def price_position_text(pos, name):
    """일반인용 설명. 숫자를 일상어로 풀되, 의견은 만들지 않는다."""
    if not pos:
        return ""
    p60 = pos["position_60"]
    where = ("거의 바닥 근처" if p60 < 0.15 else "아래쪽" if p60 < 0.35 else
             "가운데" if p60 <= 0.65 else "위쪽" if p60 <= 0.85 else "거의 꼭대기 근처")
    ma60 = pos["moving_averages"][60]["gap"]
    dd = pos["drawdown_3y"]
    first = (f"<b>최근 60거래일(약 석 달) 범위에서 {'아래' if p60 < .5 else '위'}쪽 "
             f"{p60:.0%} 지점입니다.</b> 석 달 중 {where}에 있다는 뜻입니다. "
             f"60일 평균보다 {abs(ma60):.1%} {'낮고' if ma60 < 0 else '높고'}, "
             f"최근 3년 고점에서는 {abs(dd):.0%} {'내려와' if dd < 0 else '위에'} 있습니다.")
    sim, base, k = pos["similar"], pos["baseline"], pos["lookahead"]
    if sim is None or base is None:
        second = (f"과거에 지금과 같은 자리였던 날이 {pos['similar_n']}번뿐이라(판단에 {pos['min_samples']}번 필요) "
                  "그 뒤 어땠는지는 말하지 않습니다.")
    else:
        ups = round(sim["up_share"] * sim["n"])
        diff = sim["up_share"] - base["up_share"]
        verdict = ("거의 차이가 없습니다" if abs(diff) < 0.05 else
                   f"조금 {'높습니다' if diff > 0 else '낮습니다'}" if abs(diff) < 0.12 else
                   f"꽤 {'높습니다' if diff > 0 else '낮습니다'}")
        second = (f"과거에 이만큼 {'낮은' if p60 < .5 else '높은'} 자리에 있던 날이 {sim['n']}번 있었는데, "
                  f"그중 {k}거래일(약 한 달) 뒤에 올랐던 날은 {ups}번({sim['up_share']:.0%})이었습니다. "
                  f"평소의 한 달 뒤 상승 비율이 {base['up_share']:.0%}이니 <b>{verdict}.</b> "
                  f"그때의 한 달 뒤 수익률은 절반이 {sim['q25']:+.1%}에서 {sim['q75']:+.1%} 사이였습니다.")
    third = ("이것은 지금까지의 위치를 설명하는 것이지, 여기가 저점이나 고점이라는 뜻이 아닙니다. "
             "저점인지 고점인지는 지나 봐야 압니다.")
    return (f'<div style="font-size:13px;line-height:1.7">{first}</div>'
            f'<div style="font-size:13px;line-height:1.7;margin-top:8px">{second}</div>'
            f'<div style="font-size:11px;color:#8a9199;margin-top:8px">{third}</div>')


def review_ledger(daily, bars, ensemble_model="Mean ensemble", windows=(20, 60), min_alert_n=20,
                  nominal_coverage=0.80):
    """실제 사전 예측(daily_comparison)만으로 최근 성능을 계산하고 경고를 만든다.

    백테스트 숫자와 섞지 않는다. 반환:
      latest   — 마지막으로 채점된 예측일의 행(갭/세션 분해 포함)
      rolling  — 최근 w거래일 창별 지표. 방향: 적중률·log loss vs 클래스빈도 기준선.
                 시가/종가: 구간 적중률, 수익률 MAE vs '변화 없음' 기준선, 실현 기울기 vs OOF 축소계수.
      alerts   — 가장 긴 창(표본 min_alert_n 이상)에서 나온 경고 문구
    """
    empty = {"latest": pd.DataFrame(), "rolling": pd.DataFrame(), "alerts": [], "n_scored_days": 0,
             "latest_date": None, "pending": _pending_status(daily, ensemble_model, None),
             "overnight_html": overnight_value_html(daily, ensemble_model)}
    if daily is None or daily.empty or "status" not in daily:
        return empty
    scored = daily.loc[daily["status"] == "scored"].copy()
    if scored.empty:
        return empty
    scored["target_date"] = pd.to_datetime(scored["target_date"]).dt.tz_localize(None).dt.normalize()
    scored["horizon_days"] = pd.to_numeric(scored.get("horizon_days", 1), errors="coerce").fillna(1).astype(int)
    if "raw_predicted_return" not in scored:
        scored["raw_predicted_return"] = np.nan
    if "oof_slope" not in scored:
        scored["oof_slope"] = np.nan

    bars = bars.sort_index().copy()
    bars.index = pd.DatetimeIndex(bars.index).tz_localize(None).normalize()
    gap = (bars["open"] / bars["close"].shift(1) - 1).rename("actual_gap")
    session = (bars["close"] / bars["open"] - 1).rename("actual_session")
    scored = scored.join(gap, on="target_date").join(session, on="target_date")

    # 아래 표의 기준일은 '종가까지 채점된 마지막 날'이다. 시초가만 채점된 오늘을 기준으로 잡으면
    # 어제의 종가 결과가 표에서 사라진다(오늘의 부분 상태는 위 블록이 따로 보여 준다).
    full = scored[(scored["kind"] == "direction") & (scored["model"] == ensemble_model)]
    latest_date = full["target_date"].max() if len(full) else scored["target_date"].max()
    keep = [c for c in ["target_date", "kind", "horizon_days", "model", "prediction", "p_down", "p_flat",
                        "p_up", "band", "actual_class", "direction_correct", "log_loss", "current_close",
                        "predicted_return", "raw_predicted_return", "predicted_close", "center_close",
                        "low_close", "high_close", "predicted_open", "center_open", "low_open", "high_open",
                        "actual_open", "actual_close", "actual_return", "actual_gap", "actual_session",
                        "return_error", "interval_hit", "oof_slope", "run_id",
                        # 시가 반영 갱신 행의 표시용 열(정보 마감·실제 실행 시각·갭)
                        "information_cutoff", "created_at_utc", "gap", "gap_rule_label", "open_price"] if c in scored]
    latest = scored.loc[scored["target_date"] == latest_date, keep].reset_index(drop=True)

    dates = np.sort(scored["target_date"].unique())
    rows = []
    for w in windows:
        recent = scored[scored["target_date"].isin(dates[-w:])]
        d = recent[(recent["kind"] == "direction") & (recent["model"] == ensemble_model)]
        d = d.dropna(subset=["actual_class"])
        if len(d):
            freq = d["actual_class"].astype(int).value_counts(normalize=True).reindex([0, 1, 2]).fillna(0.)
            prior_ll = float(-np.sum(freq * np.log(np.clip(freq, 1e-7, 1.))))
            rows.append({"window": w, "kind": "direction", "horizon_days": 1, "n": len(d),
                         "hit_rate": float(d["direction_correct"].mean()),
                         # 비교 기준: 항상 최빈 클래스를 찍었을 때의 적중률. 도넛 눈금에 쓴다.
                         "prior_hit_rate": float(freq.max()),
                         "flat_share": float(freq.loc[1]),
                         "mean_log_loss": float(d["log_loss"].mean()), "prior_log_loss": prior_ll})
            # 발행 기준(DIRECTION_ISSUE_MIN_PROB)이 켜져 있을 때만: 실제로 방향을 낸 날의 성적을 따로 센다.
            # 원장의 확률에서 다시 계산하므로 기준을 바꿔도 과거 기록에 그대로 적용된다. 기준이 0이면
            # 전체 행과 같아지므로 만들지 않는다(2026-09-16 사용자 결정으로 꺼 둠).
            if DIRECTION_ISSUE_MIN_PROB > 0:
                if {"p_down", "p_flat", "p_up"}.issubset(d.columns):
                    issued = d[d.apply(lambda r: bool(direction_call(r)["issued"]), axis=1)]
                else:
                    issued = d
                rows.append({"window": w, "kind": "direction_issued", "horizon_days": 1, "n": int(len(issued)),
                             "held": int(len(d) - len(issued)),
                             "hit_rate": float(issued["direction_correct"].mean()) if len(issued) else np.nan,
                             "prior_hit_rate": float(freq.max())})
        # 시가 반영 갱신(Post-open)은 정보 마감(15:30)이 다른 별도 행이다. 대표 방향 행과 절대 합치지 않고
        # 같은 창에서 따로 센다. 대표 모델 선택 근거로 쓰지 않는다(P16 decision.md).
        po = recent[(recent["kind"] == "direction") & (recent["model"].astype(str) == POST_OPEN_MODEL)]
        po = po.dropna(subset=["actual_class"])
        if len(po):
            freq_po = po["actual_class"].astype(int).value_counts(normalize=True).reindex([0, 1, 2]).fillna(0.)
            rows.append({"window": w, "kind": "direction_post_open", "horizon_days": 1, "n": int(len(po)),
                         "hit_rate": float(po["direction_correct"].mean()),
                         "prior_hit_rate": float(freq_po.max()), "flat_share": float(freq_po.loc[1]),
                         "mean_log_loss": float(po["log_loss"].mean()) if po["log_loss"].notna().any() else np.nan,
                         "prior_log_loss": float(-np.sum(freq_po * np.log(np.clip(freq_po, 1e-7, 1.)))),
                         "information_cutoff": POST_OPEN_INFORMATION_CUTOFF})
        for kind in ("open", "price"):
            # 관찰용 후보(모델명 "Candidate …")는 원장에만 있고 헤드라인 성적에 섞지 않는다(M07).
            headline = recent[(recent["kind"] == kind) & is_headline_model(recent["model"])]
            for h, g in headline.groupby("horizon_days"):
                g = g.dropna(subset=["actual_return"])
                if not len(g):
                    continue
                actual = g["actual_return"].to_numpy(dtype=float)
                # 신호가 없어 점 예측을 비운 날은 '변화 없음'(0%)으로 예측한 것과 같다.
                point = g["predicted_return"].fillna(0.).to_numpy(dtype=float)
                raw = g["raw_predicted_return"].to_numpy(dtype=float)
                slope = np.nan
                ok = np.isfinite(raw)
                if ok.sum() >= 5 and np.sum(raw[ok] ** 2) > 0:
                    slope = float(np.sum(raw[ok] * actual[ok]) / np.sum(raw[ok] ** 2))
                rows.append({"window": w, "kind": kind, "horizon_days": int(h), "n": len(g),
                             "interval_coverage": float(g["interval_hit"].mean()) if g["interval_hit"].notna().any() else np.nan,
                             # 명목 커버리지(구간을 만들 때 목표로 삼은 확률). 도넛 눈금에 쓴다.
                             "nominal_coverage": nominal_coverage,
                             "mae_return": float(np.mean(np.abs(actual - point))),
                             "zero_mae_return": float(np.mean(np.abs(actual))),
                             "signal_days": int(g["predicted_return"].notna().sum()),
                             "realized_slope": slope,
                             "oof_slope_mean": float(g["oof_slope"].mean()) if g["oof_slope"].notna().any() else np.nan})
    rolling = pd.DataFrame(rows)

    alerts = []
    if len(rolling):
        w_max = max(windows)
        big = rolling[rolling["window"] == w_max]
        d = big[big["kind"] == "direction"]
        if len(d) and d["n"].iloc[0] >= min_alert_n and d["mean_log_loss"].iloc[0] > d["prior_log_loss"].iloc[0]:
            alerts.append(f"방향 모델 열화: 최근 {w_max}일 log loss {d['mean_log_loss'].iloc[0]:.3f} > "
                          f"클래스빈도 기준선 {d['prior_log_loss'].iloc[0]:.3f}")
        for _, r in big[big["kind"].isin(["open", "price"])].iterrows():
            label = "시초가" if r["kind"] == "open" else f"{int(r['horizon_days'])}거래일 종가예측"
            if r["n"] < min_alert_n:
                continue
            if np.isfinite(r["interval_coverage"]) and r["interval_coverage"] < 0.70:
                alerts.append(f"{label} 구간 과소: 최근 {w_max}일 적중률 {r['interval_coverage']:.0%} (목표 80%)")
            if (np.isfinite(r["realized_slope"]) and np.isfinite(r["oof_slope_mean"]) and r["oof_slope_mean"] > 0
                    and not 0.5 <= r["realized_slope"] / r["oof_slope_mean"] <= 1.5):
                direction = "과소" if r["realized_slope"] > r["oof_slope_mean"] else "과대"
                alerts.append(f"{label} {direction}예측: 실현 기울기 {r['realized_slope']:.2f} vs "
                              f"OOF 축소계수 {r['oof_slope_mean']:.2f}")
            if r["mae_return"] > r["zero_mae_return"]:
                alerts.append(f"{label} 점 예측이 '변화 없음'보다 나쁨: MAE {r['mae_return']:.2%} vs {r['zero_mae_return']:.2%}")
    return {"latest": latest, "rolling": rolling, "alerts": alerts, "n_scored_days": int(len(dates)),
            "latest_date": pd.Timestamp(latest_date),
          "pending": _pending_status(daily, ensemble_model, latest_date),
          # 구간 후보의 이벤트일/평일 적중률. 내재변동성 구간이 존재 이유(이벤트일)를 실제로 푸는지.
          "event_coverage": interval_coverage_by_event(
              daily, ["Ridge", "Candidate HAR interval", "Candidate IV interval"]),
          # 아침 vs 저녁 예측 — 밤사이 미국 시장 정보의 값. 렌더러는 daily 를 받지 않으므로
          # 여기서 만들어 넘긴다.
          "overnight_html": overnight_value_html(daily, ensemble_model)}


# ---------------------------------------------------------------------------
# 보고서의 '어제 예측 vs 실제' 절
# ---------------------------------------------------------------------------
# 노트북(아침 실행)과 tools/build_afternoon_update.py(장 마감 후 갱신)가 같은 함수를 쓴다.
# 오후에는 이 절만 다시 그려 넣기 때문에, 두 곳의 표가 어긋나지 않으려면 한 군데서 만들어야 한다.
_LABELS = {0: "하락", 1: "보합", 2: "상승"}


def _fmt_num(x, kind="num"):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    if kind == "won":
        return f"{x:,.0f}원"
    if kind == "pct":
        return f"{x:+.2%}"
    if kind == "bp":
        return f"{x:+.1f}bp"
    return f"{x:.4f}"


def donut(value, baseline=None, size=96, color="#1a5490", muted=False):
    """도넛 게이지 하나. value·baseline 은 0~1 비율.

    baseline 이 있으면 그 위치에 눈금을 그린다 — '60%'라는 숫자만으로는 잘한 것인지 알 수 없고,
    방향 예측은 기준선(클래스 빈도)을, 구간은 명목 커버리지를 넘겨야 의미가 있기 때문이다.
    """
    r, stroke = size / 2 - 9, 9
    circumference = 2 * np.pi * r
    ratio = 0.0 if value is None or not np.isfinite(value) else max(0.0, min(1.0, float(value)))
    ring = "#c9ced6" if muted else color
    parts = [f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img">',
             f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="#eceef1" stroke-width="{stroke}"/>',
             f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="{ring}" stroke-width="{stroke}" '
             f'stroke-linecap="round" stroke-dasharray="{circumference * ratio:.2f} {circumference:.2f}" '
             f'transform="rotate(-90 {size/2} {size/2})"/>']
    if baseline is not None and np.isfinite(baseline):
        angle = -np.pi / 2 + 2 * np.pi * max(0.0, min(1.0, float(baseline)))
        x1, y1 = size/2 + (r - stroke/2 - 1) * np.cos(angle), size/2 + (r - stroke/2 - 1) * np.sin(angle)
        x2, y2 = size/2 + (r + stroke/2 + 1) * np.cos(angle), size/2 + (r + stroke/2 + 1) * np.sin(angle)
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke="#6b7178" stroke-width="2"/>')
    text = "—" if value is None or not np.isfinite(value) else f"{ratio * 100:.0f}%"
    parts.append(f'<text x="{size/2}" y="{size/2 + 6}" text-anchor="middle" font-size="19" '
                 f'font-weight="700" fill="{"#8a9199" if muted else "#1a1a1a"}">{text}</text></svg>')
    return "".join(parts)


def rolling_gauges_html(cards, min_samples=10):
    """누적 성능을 도넛으로. 표본이 적으면 흐리게 하고 그 사실을 적는다.

    표가 정확하지만 숫자가 많아 읽기 어렵고, 특히 초기에는 n 이 작아 비교가 안 된다. 도넛으로
    한눈에 보이게 하되, 큰 숫자가 정확해 보이는 착시를 막으려고 표본이 적으면 회색으로 낮춘다.
    """
    if not cards:
        return ""
    items = ""
    for card in cards:
        muted = card["n"] < min_samples
        note = (f'표본 {card["n"]}개 — 판단 이르다' if muted
                else f'n={card["n"]}' + (f' · 기준선 {card["baseline"]:.0%}' if card.get("baseline") is not None else ""))
        items += ('<div style="flex:0 0 auto;text-align:center;min-width:118px">'
                  + donut(card["value"], card.get("baseline"), muted=muted,
                          color=card.get("color", "#1a5490"))
                  + f'<div style="font-size:12px;font-weight:600;margin-top:4px;color:'
                    f'{"#8a9199" if muted else "#1a1a1a"}">{card["label"]}</div>'
                  + f'<div style="font-size:11px;color:#8a9199">{card["metric"]}</div>'
                  + f'<div style="font-size:11px;color:#8a9199">{note}</div></div>')
    return ('<div style="display:flex;gap:14px;flex-wrap:wrap;justify-content:flex-start;'
            'margin:10px 0 2px">' + items + '</div>')


def ledger_section_html(review, ensemble_name, updated_note=""):
    """채점 결과 절. 제목에 '어느 날짜를 채점한 것인지'를 반드시 적는다.

    '어제'뿐이면 09:37 회차인지 16:10 회차인지, 휴장일을 건너뛴 것인지 알 수 없다.
    채점 대상일은 원장의 target_date 이고 그 값을 그대로 보여 준다.
    """
    cell = 'style="padding:7px 11px;border-top:1px solid #eee;text-align:right"'

    def _headline(scored_date=None):
        title = f"{scored_date} 예측 vs 실제" if scored_date else "예측 vs 실제 (채점 대기)"
        return ('<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
                f'{title} <span style="font-weight:400;color:#8a9199;font-size:12px">'
                '&nbsp;실제로 미리 낸 예측만 채점 · 백테스트 숫자가 아님</span></h3>')

    def _pending_block(pending):
        """최신 예측일의 항목별 상태. 판정 전 항목은 그렇게 적는다 — 전날 결과로 오해하지 않게."""
        if not pending:
            return ""
        day = pending["date"]
        title = f'{day.date().isoformat()} ({"월화수목금토일"[day.weekday()]}) 오늘 예측 — 채점 상태'
        rows = ""
        when = {"open": "09:37 시초가 확인 회차", "direction": "16:10 장 마감 후 회차", "price": "16:10 장 마감 후 회차"}
        for kind in ("open", "direction", "price"):
            item = pending["items"].get(kind)
            if not item:
                continue
            r = item["row"]
            if item["status"] == "scored":
                if kind == "open":
                    gap = (r.get("actual_open") / r.get("current_close") - 1
                           if pd.notna(r.get("actual_open")) and r.get("current_close") else np.nan)
                    result = (f'실제 시가 {_fmt_num(r.get("actual_open"), "won")} ({_fmt_num(gap, "pct")}) · '
                              f'구간 {"적중" if r.get("interval_hit") == 1 else "이탈"}')
                elif kind == "direction":
                    result = f'{"적중" if r.get("direction_correct") == 1 else "미적중"}'
                else:
                    result = f'구간 {"적중" if r.get("interval_hit") == 1 else "이탈"}'
                color, text = "#1e6b34", f"채점됨 — {result}"
            else:
                color, text = "#8a9199", f"판정 전 — {when[kind]}에 채점"
            if kind == "open":
                pred = (f'{_fmt_num(r.get("predicted_open"), "won")}' if pd.notna(r.get("predicted_open"))
                        else f'{_fmt_num(r.get("center_open"), "won")} (신호 없음)')
            elif kind == "direction":
                pred = f'{r.get("prediction")} (상승 {float(r.get("p_up", 0)):.0%})'
            else:
                pred = (f'{_fmt_num(r.get("predicted_close"), "won")}' if pd.notna(r.get("predicted_close"))
                        else f'{_fmt_num(r.get("center_close"), "won")} (신호 없음)')
            rows += (f'<tr><td style="padding:7px 11px;border-top:1px solid #eee">{item["label"]}</td>'
                     f'<td {cell}>{pred}</td>'
                     f'<td {cell};color:{color}">{text}</td></tr>')
        return ('<div style="overflow-x:auto;margin-bottom:14px"><table style="width:100%;min-width:520px;border-collapse:collapse;'
                'font-size:13px;border:1px solid #e5e5e5">'
                f'<tr style="background:#fafafa;font-size:11px;color:#6b7178"><th colspan="3" style="padding:8px 11px;text-align:left">{title}</th></tr>'
                f'{rows}</table></div>')

    head = _headline()
    if not review["n_scored_days"]:
        return head + _pending_block(review.get("pending")) + (
            '<div style="border:1px solid #e5e5e5;border-radius:6px;padding:14px;font-size:13px;color:#6b7178">'
            '아직 채점된 사전 예측이 없습니다. 오늘 예측은 다음 거래일 실행에서 실제 시가·종가와 대조됩니다.</div>')
    latest = review["latest"]
    # 채점 대상일을 제목에 넣는다. 표의 값이 어느 날 결과인지 한눈에 보여야 한다.
    scored_date = None
    stamp = review.get("latest_date")
    if stamp is not None:
        try:
            moment = pd.Timestamp(stamp)
            scored_date = f'{moment.date().isoformat()} ({"월화수목금토일"[moment.weekday()]})'
        except (TypeError, ValueError):
            scored_date = str(stamp)[:10]
    head = _headline(scored_date) + _pending_block(review.get("pending"))
    # 관찰 후보('Candidate …')는 같은 날 같은 kind 로 여러 행이 있다. 대표 행만 표에 올린다.
    headline_rows = is_headline_model(latest["model"]) if "model" in latest else pd.Series(True, index=latest.index)
    d = latest[(latest["kind"] == "direction") & (latest["model"] == ensemble_name)]
    o = latest[(latest["kind"] == "open") & headline_rows]
    p1 = latest[(latest["kind"] == "price") & (latest["horizon_days"] == 1) & headline_rows]
    rows = ""
    def _row(label, predicted, actual, verdict, ok):
        color = "#1e6b34" if ok else "#a8322a"
        return (f'<tr><td style="padding:8px 11px;border-top:1px solid #eee">{label}</td>'
                f'<td {cell}>{predicted}</td><td {cell}>{actual}</td>'
                f'<td {cell};color:{color};font-weight:600">{verdict}</td></tr>')
    # 하루의 시간 순서대로 놓는다: 09:00 시초가 → 15:30 종가.
    # 09:37 채점 회차에는 시초가 행만 채워지므로 순서가 맞아야 읽기도 자연스럽다.
    if len(o):
        r = o.iloc[0]
        pred = (f'{_fmt_num(r["predicted_open"], "won")} ({_fmt_num(r["predicted_return"], "pct")})'
                if pd.notna(r["predicted_open"]) else f'{_fmt_num(r["center_open"], "won")} (신호 없음)')
        rows += _row("시초가(갭)", pred, f'{_fmt_num(r["actual_open"], "won")} ({_fmt_num(r["actual_gap"], "pct")})',
                     f'구간 {"적중" if r["interval_hit"] == 1 else "이탈"} · 오차 {_fmt_num(r["return_error"], "pct") if pd.notna(r["return_error"]) else "—"}',
                     r["interval_hit"] == 1)
    if len(d):
        r = d.iloc[0]
        actual_label = _LABELS.get(int(r["actual_class"]), "?") if pd.notna(r["actual_class"]) else "—"
        call = direction_call(r)
        if call["valid"] and not call["issued"]:
            # 그날은 방향을 내지 않았다(최대 확률이 기준 미만). 맞음·미적중으로 세지 않고 참고로만 적는다.
            rows += _row("종가 방향",
                         f'판단 유보 · 계산상 {r["prediction"]} (상승 {r["p_up"]:.0%}·보합 {r["p_flat"]:.0%}·하락 {r["p_down"]:.0%})',
                         f'{actual_label} ({_fmt_num(r["actual_return"], "pct")}, 밴드 ±{r["band"]:.2%})',
                         f'유보 (참고: {"적중" if r["direction_correct"] == 1 else "미적중"})', r["direction_correct"] == 1)
        else:
            rows += _row("종가 방향", f'{r["prediction"]} (상승 {r["p_up"]:.0%}·보합 {r["p_flat"]:.0%}·하락 {r["p_down"]:.0%})',
                         f'{actual_label} ({_fmt_num(r["actual_return"], "pct")}, 밴드 ±{r["band"]:.2%})',
                         "적중" if r["direction_correct"] == 1 else "미적중", r["direction_correct"] == 1)
    # 시가 반영 갱신 행(Post-open). 07:00 방향 행 바로 아래 별도 줄 — 정보 마감이 다르므로 합치지 않는다.
    po = latest[(latest["kind"] == "direction") & (latest["model"].astype(str) == POST_OPEN_MODEL)] if "model" in latest else latest.iloc[0:0]
    if len(po):
        r = po.iloc[0]
        actual_label = _LABELS.get(int(r["actual_class"]), "?") if pd.notna(r["actual_class"]) else "—"
        created = pd.to_datetime(r.get("created_at_utc"), utc=True, errors="coerce")
        when = f"{created.tz_convert('Asia/Seoul'):%H:%M}" if pd.notna(created) else "시각 미상"
        gap = _fmt_num(float(r["gap"]), "pct") if "gap" in r and pd.notna(r.get("gap")) else "—"
        rows += _row(f"종가 방향 · 시가 반영 갱신 <span style=\"color:#8a9199;font-size:11px\">(실제 실행 {when} · 정보 마감 15:30 이전)</span>",
                     f'{r["prediction"]} (상승 {r["p_up"]:.0%}·보합 {r["p_flat"]:.0%}·하락 {r["p_down"]:.0%}) · 갭 {gap}',
                     f'{actual_label} ({_fmt_num(r["actual_return"], "pct")}, 밴드 ±{r["band"]:.2%})',
                     "적중" if r["direction_correct"] == 1 else "미적중", r["direction_correct"] == 1)
    if len(p1):
        r = p1.iloc[0]
        pred = (f'{_fmt_num(r["predicted_close"], "won")} ({_fmt_num(r["predicted_return"], "pct")})'
                if pd.notna(r["predicted_close"]) else f'{_fmt_num(r["center_close"], "won")} (신호 없음)')
        rows += _row("1거래일 종가예측", pred,
                     f'{_fmt_num(r["actual_close"], "won")} ({_fmt_num(r["actual_return"], "pct")} = 갭 {_fmt_num(r["actual_gap"], "pct")} + 세션 {_fmt_num(r["actual_session"], "pct")})',
                     f'구간 {"적중" if r["interval_hit"] == 1 else "이탈"}', r["interval_hit"] == 1)
    table = ('<div style="overflow-x:auto"><table style="width:100%;min-width:520px;border-collapse:collapse;'
             'font-size:13px;border:1px solid #e5e5e5"><tr style="background:#fafafa;font-size:11px;color:#6b7178">'
             '<th style="padding:8px 11px;text-align:left">항목</th><th style="padding:8px 11px;text-align:right">예측</th>'
             '<th style="padding:8px 11px;text-align:right">실제</th><th style="padding:8px 11px;text-align:right">판정</th></tr>'
             f'{rows}</table></div>')
    # 누적 창
    roll = review["rolling"]
    rrows = ""
    for _, r in roll.iterrows():
        if r["kind"] == "direction":
            label, detail = "종가 방향(전체)", (f'적중률 {r["hit_rate"]:.0%} (보합 비중 {r["flat_share"]:.0%}) · '
                                            f'log loss {r["mean_log_loss"]:.3f} vs 빈도기준 {r["prior_log_loss"]:.3f}')
        elif r["kind"] == "direction_issued":
            issued_n, held_n = int(r["n"]), int(r.get("held", 0))
            label = "종가 방향(발행일만)"
            detail = ((f'적중률 {r["hit_rate"]:.0%} · ' if issued_n and pd.notna(r["hit_rate"]) else "")
                      + f'발행 {issued_n}일 · 유보 {held_n}일 (최대 확률 {DIRECTION_ISSUE_MIN_PROB:.0%} 이상만 발행)')
        elif r["kind"] == "direction_post_open":
            label = "시가 반영 갱신 방향 · 정보 마감 15:30 이전"
            detail = (f'적중률 {r["hit_rate"]:.0%} (보합 비중 {r["flat_share"]:.0%}) · '
                      + (f'log loss {r["mean_log_loss"]:.3f} vs 빈도기준 {r["prior_log_loss"]:.3f} · ' if pd.notna(r.get("mean_log_loss")) else "")
                      + '07:00 행과 다른 질문이라 대표 성적과 합치지 않음')
        else:
            label = "시초가예측(갭)" if r["kind"] == "open" else f'{int(r["horizon_days"])}거래일 종가예측'
            detail = (f'구간 적중 {_fmt_num(r["interval_coverage"], "num") if pd.isna(r["interval_coverage"]) else format(r["interval_coverage"], ".0%")} · '
                      f'MAE {r["mae_return"]:.2%} vs 변화없음 {r["zero_mae_return"]:.2%} · 신호 {int(r["signal_days"])}/{int(r["n"])}일')
            if pd.notna(r["realized_slope"]):
                detail += f' · 실현 기울기 {r["realized_slope"]:.2f} / OOF {r["oof_slope_mean"]:.2f}'
        rrows += (f'<tr><td style="padding:7px 11px;border-top:1px solid #eee">최근 {int(r["window"])}일 · {label}</td>'
                  f'<td style="padding:7px 11px;border-top:1px solid #eee;text-align:right">{int(r["n"])}</td>'
                  f'<td style="padding:7px 11px;border-top:1px solid #eee">{detail}</td></tr>')
    # 표는 정확하지만 숫자가 많아 읽기 어렵다. 창별로 핵심 비율만 도넛으로 먼저 보여 주고
    # 자세한 수치는 접어 둔다. 표본이 적으면 도넛을 흐리게 해 큰 숫자가 정확해 보이지 않게 한다.
    gauges = ""
    for window in sorted({int(w) for w in roll["window"]}) if len(roll) else []:
        cards = []
        for _, r in roll[roll["window"] == window].iterrows():
            if r["kind"] == "direction":
                cards.append({"label": "종가 방향(전체)", "metric": "적중률", "value": r["hit_rate"],
                              "baseline": r.get("prior_hit_rate"), "n": int(r["n"])})
            elif r["kind"] == "direction_issued":
                # 방향을 낸 날만. 0일이면 도넛은 '—'로 비우고 n=0 이라 흐리게 나온다.
                cards.append({"label": "종가 방향(발행일만)", "metric": f'적중률 · 유보 {int(r.get("held", 0))}일',
                              "value": r["hit_rate"], "baseline": r.get("prior_hit_rate"), "n": int(r["n"])})
            elif r["kind"] == "direction_post_open":
                cards.append({"label": "시가 반영 갱신 방향", "metric": "적중률 · 정보 마감 15:30 이전",
                              "value": r["hit_rate"], "baseline": r.get("prior_hit_rate"), "n": int(r["n"]),
                              "color": "#8a6d1f"})
            else:
                label = "시초가(갭)" if r["kind"] == "open" else f'{int(r["horizon_days"])}거래일 종가'
                cards.append({"label": label, "metric": "구간 적중", "value": r["interval_coverage"],
                              "baseline": r.get("nominal_coverage"), "n": int(r["signal_days"]),
                              "color": "#1e6b34"})
        if cards:
            gauges += (f'<div style="font-size:12px;color:#6b7178;margin-top:10px">최근 {window}일 '
                       '<span style="color:#8a9199">· 사전 예측만 · 눈금은 기준선</span></div>'
                       + rolling_gauges_html(cards))
    # 이벤트일 vs 평일 구간 적중률 — 내재변동성 구간 후보의 판정표
    event_html = ""
    ec = review.get("event_coverage")
    if ec is not None and len(ec) and (ec["n_event"].sum() > 0):
        labels = {"Ridge": "대표 구간(실현 변동성)", "Candidate HAR interval": "HAR 구간",
                  "Candidate IV interval": "내재변동성 구간"}
        rows_e = ""
        for _, r in ec.iterrows():
            fmt = lambda v, n: ("—" if v is None else f"{v:.0%}") + f' <span style="color:#8a9199">(n={n})</span>'
            rows_e += (f'<tr><td style="padding:7px 11px;border-top:1px solid #eee">{labels.get(r["model"], r["model"])}'
                       f' · {int(r["horizon_days"])}일</td>'
                       f'<td {cell}>{fmt(r["coverage_event"], r["n_event"])}</td>'
                       f'<td {cell}>{fmt(r["coverage_normal"], r["n_normal"])}</td></tr>')
        event_html = ('<div style="font-size:12px;color:#6b7178;margin:14px 0 4px">이벤트일(실적·FOMC·CPI 다음 거래일) vs 평일 '
                      '구간 적중률 — 내재변동성 구간은 이벤트일에 넓어지도록 만든 것이라, 이 표에서 '
                      '이벤트일 적중률이 대표 구간보다 높은지가 판단 기준입니다. 표본 10개 미만은 —.</div>'
                      '<div style="overflow-x:auto"><table style="width:100%;min-width:420px;border-collapse:collapse;'
                      'font-size:12px;border:1px solid #e5e5e5"><tr style="background:#fafafa;font-size:11px;color:#6b7178">'
                      '<th style="padding:8px 11px;text-align:left">구간</th><th style="padding:8px 11px;text-align:right">이벤트일</th>'
                      '<th style="padding:8px 11px;text-align:right">평일</th></tr>' + rows_e + '</table></div>')
    # 이벤트일 구간·밤사이 정보의 값은 모델을 고치는 쪽이 보는 표다. 적중률 그림만 밖에 두고 이 둘은 창별 누적 수치와
    # 함께 접는다(2026-10-02, 글이 많아 읽기 어렵다는 요청의 2단계). 내용은 그대로이고 누르면 펼쳐진다.
    extras = event_html + (review.get("overnight_html") or "")
    rtable = (gauges
              + '<details style="margin-top:6px"><summary style="font-size:12px;color:#6b7178;cursor:pointer">'
              '자세한 수치 보기' + (' — 이벤트일 구간 · 밤사이 정보의 값 · 창별 누적' if extras else '') + '</summary>'
              + extras +
              '<div style="overflow-x:auto;margin-top:6px"><table style="width:100%;min-width:520px;border-collapse:collapse;'
              'font-size:12px;border:1px solid #e5e5e5"><tr style="background:#fafafa;font-size:11px;color:#6b7178">'
              '<th style="padding:8px 11px;text-align:left">창</th><th style="padding:8px 11px;text-align:right">n</th>'
              '<th style="padding:8px 11px;text-align:left">누적 성능 (사전 예측만)</th></tr>'
              f'{rrows}</table></div></details>')
    alerts = ""
    if review["alerts"]:
        alerts = ('<div style="background:#fdf3f2;border-left:4px solid #b5453c;padding:10px 14px;margin-top:10px;font-size:13px">'
                  '<b>경고</b><ul style="margin:6px 0 0;padding-left:18px">'
                  + "".join(f"<li>{a}</li>" for a in review["alerts"]) + "</ul>"
                  '<div style="font-size:11px;color:#8a9199;margin-top:4px">경고는 판단 근거이며 자동으로 설정을 바꾸지 않습니다. '
                  '같은 경고가 두 달 이상 이어질 때 사람이 조정합니다.</div></div>')
    note = (f'<div style="font-size:11px;color:#8a9199;margin-top:6px">채점된 예측일 {review["n_scored_days"]}일 · '
            f'마지막 채점 {review["latest_date"].date()} · 하루 결과는 잡음입니다(1거래일 MAE ≈ 2~3%). '
            '판단은 60일 창으로 하세요.</div>')
    return head + updated_note + table + rtable + alerts + note


# ---- 장 마감 회고 절 (2026-09-23) -------------------------------------------------------------
# tools/build_session_review.py 가 16:10 회차에 페이지에 끼우는 절이다. 노트북도 같은 함수로 그린다 — 저녁·코드 반영
# 실행이 페이지를 새로 만들면 오후에 끼운 회고가 사라져서, 마지막 마감 거래일의 회고 기록(reviews/<날짜>.json)을
# 읽어 다시 붙인다. 기록에서 읽으면 시각이 ISO 문자열이고 결측이 None 이라 둘 다 받는다.
REVIEW_START, REVIEW_END = "<!--REVIEW_SECTION_START-->", "<!--REVIEW_SECTION_END-->"
REVIEW_LEDGER_END = "<!--LEDGER_SECTION_END-->"
REVIEW_DISCLAIMER = ("시간이 맞는 것이지 원인 확정이 아닙니다. 뉴스 없이 움직이는 날(수급·프로그램·옵션 만기)이 많고, "
                     "기사 발행 시각은 실제 정보 유통보다 늦거나 앞섭니다. 이 절은 설명 도구이며 다음날 예측에 "
                     "자동 반영되지 않습니다.")
_RV_TD = 'style="padding:6px 10px;border-top:1px solid #eee"'
_RV_TDR = 'style="padding:6px 10px;border-top:1px solid #eee;text-align:right;font-variant-numeric:tabular-nums"'
_RV_TH = 'style="padding:8px 10px;text-align:left;font-size:11px;color:#6b7178;letter-spacing:.5px;background:#fafafa"'


def _rv_finite(x):
    try:
        return bool(np.isfinite(float(x)))
    except (TypeError, ValueError):
        return False


def _rv_pct(x, d=2):
    return f"{float(x) * 100:+.{d}f}%" if _rv_finite(x) else "—"


def _rv_hhmm(t):
    """Timestamp 또는 ISO 문자열 → HH:MM."""
    if hasattr(t, "strftime"):
        return t.strftime("%H:%M")
    try:
        return pd.Timestamp(t).strftime("%H:%M")
    except Exception:
        return str(t)[11:16] or "—"


def _rv_news_list(items):
    from html import escape as e
    if not items:
        return '<div style="font-size:12px;color:#8a9199;margin:2px 0 4px">관련 뉴스 없음(이 창에 발행된 헤드라인이 없거나 조회하지 않음)</div>'
    return ('<ul style="margin:0 0 6px;padding-left:20px;font-size:12px;color:#4a4f55">'
            + "".join(f'<li>{_rv_hhmm(it["time"])} · <a href="{e(it["link"])}" style="color:#1a5490">{e(it["title"])}</a>'
                      f' <span style="color:#8a9199">{e(it["source"])}</span></li>' for it in items) + "</ul>")


# ---- 장중 가격·뉴스 타임라인 차트 (guides/intraday-news-timeline-plan.md 2단계, 2026-10-02) -----------------
# 시간은 왼쪽에서 오른쪽으로(09:00 → 15:30), 가격은 위가 높다. 처음에는 계획대로 시간을 세로로 그렸는데, 주가
# 그림은 가로 시간축이 익숙해 같은 날 가로로 바꿨다(2026-10-02 요청). 가격선은 5분봉 종가를 이은 것이고,
# 급변 시점(detect_events)은 가격선 위의 동그라미, 그 전후 뉴스는 그림 아래 카드에 시간순으로 둔다.
# 뉴스는 기사 발행 시각이라 실제로 알려진 시각과 다를 수 있다 — 원인을 말하는 그림이 아니라 전후 관계를 보는 그림이다.
# 스크립트를 쓰지 않는다(GitHub Pages 에서 그대로 보이고 생성 결과가 결정적이어야 한다).
_TL_PRE, _TL_HEIGHT, _TL_MINUTES = 8.0, 230, 390             # 개장 전 띠 폭(%) · 그림 높이(px) · 09:00~15:30
_TL_TOP, _TL_BOTTOM, _TL_GUTTER = 16, 10, 82                 # 위·아래 여백(px) · 오른쪽 가격 글자 칸(px)
_TL_KINDS = {"turn_up": ("▲", "#1e6b34", "급등"), "turn_down": ("▼", "#a8322a", "급락"),
             "volume": ("●", "#1a5490", "거래량 급증")}
_TL_STYLE = (
    '<style>.tl-plot{position:relative;margin:8px 0 2px}'
    '.tl-cards{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 4px}'
    '.tl-card{flex:1 1 230px;min-width:0;box-sizing:border-box;border:1px solid #e3e8ee;border-radius:6px;'
    'background:#fbfdff;padding:6px 10px;font-size:12px;line-height:1.45}'
    '.tl-title{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;color:#3a4652}'
    '</style>')


def _tl_minutes(value):
    """KST 기준 09:00 부터의 분. 읽을 수 없으면 None."""
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(stamp):
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("Asia/Seoul")
    return stamp.hour * 60 + stamp.minute - 540


def _tl_x(minutes):
    """분 → 가로 위치(%). 09:00 전은 개장 전 띠 가운데, 15:30 뒤는 오른쪽 끝에 붙인다."""
    if minutes is None or minutes < 0:
        return _TL_PRE / 2
    return _TL_PRE + min(minutes, _TL_MINUTES) / _TL_MINUTES * (100 - _TL_PRE)


def _tl_link(item, text):
    """http(s) 링크만 건다. 그 밖의 주소는 글자만 보인다."""
    from html import escape as e
    link = str(item.get("link") or "")
    if link.startswith("http://") or link.startswith("https://"):
        return f'<a href="{e(link)}" style="color:#1a5490;text-decoration:none">{text}</a>'
    return text


def review_timeline_html(review):
    """장중 가격 경로와 급변 시점·뉴스를 가로 시간축에 그린다. 자료가 없으면 빈 문자열(기존 글 목록만 보인다)."""
    from html import escape as e
    r = review if isinstance(review, dict) else {}
    points = []
    for row in r.get("intraday_path") or []:
        minutes, price = _tl_minutes(row.get("time")), _finite(row.get("price"))
        if minutes is None or price is None or price <= 0 or not 0 <= minutes <= _TL_MINUTES:
            continue
        points.append((minutes, price))
    points = sorted(dict(points).items())
    if len(points) < 2:
        return ""
    s = r.get("summary") or {}
    prev_close, open_price, close_price = (_finite(s.get(k)) for k in ("prev_close", "open", "close"))
    prices = [p for _, p in points] + [v for v in (prev_close, open_price) if v]
    last_minutes = points[-1][0]
    show_close = close_price and last_minutes < _TL_MINUTES - 10     # 5분봉이 마감 전에 끊긴 날
    if show_close:
        prices.append(close_price)
    lo, hi = min(prices), max(prices)
    pad = (hi - lo) * .05 or max(hi * .001, 1.0)                     # 고가=저가여도 0으로 나누지 않는다
    lo, hi = lo - pad, hi + pad
    height, bottom = _TL_HEIGHT, _TL_HEIGHT - _TL_BOTTOM

    def y(price):
        return _TL_TOP + (hi - price) / (hi - lo) * (bottom - _TL_TOP)

    # ---- 가격 영역(SVG: 선만. 가로로 늘어나도 선 굵기는 그대로 둔다)
    svg = [f'<svg viewBox="0 0 100 {height}" preserveAspectRatio="none" width="100%" height="{height}" '
           'style="position:absolute;inset:0;display:block" aria-hidden="true">',
           f'<rect x="0" y="0" width="{_TL_PRE}" height="{height}" fill="#f7f8fa"/>']
    # '개장 전'은 띠 안 위쪽에 적는다 — 아래 시각 줄에 두면 바로 옆 09:00 과 글자가 붙는다.
    labels = ('<div style="position:absolute;left:2px;top:2px;font-size:10px;color:#8a9199;white-space:nowrap">'
              '개장 전</div>')
    for hour in range(9, 16):
        x = _tl_x((hour - 9) * 60)
        svg.append(f'<line x1="{x:.2f}" x2="{x:.2f}" y1="0" y2="{height}" stroke="#eef1f4" vector-effect="non-scaling-stroke"/>')
        if hour < 15:                              # 15:00 글자는 바로 옆 15:30 과 겹쳐서 눈금선만 둔다
            labels += (f'<div style="position:absolute;left:{x:.2f}%;top:{height + 2}px;transform:translateX(-50%);'
                       f'font-size:10px;color:#8a9199">{hour:02d}:00</div>')
    labels += (f'<div style="position:absolute;right:0;top:{height + 2}px;font-size:10px;color:#8a9199">15:30</div>')
    marks, gutter, taken = "", "", []
    for value, name, dash in ((prev_close, "전일 종가", "4 3"), (open_price, "시가", "1 3")):
        if not value:
            continue
        svg.append(f'<line x1="{_TL_PRE}" x2="100" y1="{y(value):.1f}" y2="{y(value):.1f}" stroke="#b9c0c8" '
                   f'stroke-dasharray="{dash}" vector-effect="non-scaling-stroke"/>')
        top = y(value) - 7
        while any(abs(top - other) < 12 for other in taken):          # 두 글자가 겹치면 아래로 민다
            top += 12
        taken.append(top)
        gutter += (f'<div style="position:absolute;left:4px;top:{top:.0f}px;font-size:10px;color:#6b7178;'
                   f'white-space:nowrap">{name} {value:,.0f}</div>')
    line = " ".join(f"{_tl_x(minutes):.2f},{y(price):.1f}" for minutes, price in points)
    svg.append(f'<polyline points="{line}" fill="none" stroke="#3a4652" stroke-width="2" stroke-linejoin="round" '
               'vector-effect="non-scaling-stroke"/>')
    if show_close:
        # 5분봉이 없는 구간은 실제 경로처럼 잇지 않는다 — 옅은 띠와 점선으로 일봉 종가만 가리킨다.
        x0 = _tl_x(last_minutes)
        svg.insert(2, f'<rect x="{x0:.2f}" y="0" width="{100 - x0:.2f}" height="{height}" fill="#f7f8fa"/>')
        svg.append(f'<line x1="{x0:.2f}" y1="{y(points[-1][1]):.1f}" x2="100" y2="{y(close_price):.1f}" '
                   'stroke="#8a9199" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>')
        marks += (f'<div style="position:absolute;right:2px;top:2px;font-size:10px;color:#8a9199;white-space:nowrap">'
                  f'5분봉 미수집 구간({last_minutes // 60 + 9:02d}:{last_minutes % 60:02d}~15:30)</div>'
                  f'<div style="position:absolute;left:calc(100% - 5px);top:{y(close_price) - 5:.0f}px;width:10px;'
                  'height:10px;box-sizing:border-box;border:2px solid #3a4652;border-radius:50%;background:#fff" '
                  f'title="일봉 종가 {close_price:,.0f}"></div>')

    # ---- 급변 시점과 뉴스 카드. 같은 기사는 처음 걸린 시점에만 보인다.
    seen, cards = set(), []

    def fresh(items):
        out = []
        for item in items or []:
            key = str(item.get("link") or item.get("title") or "")
            if key and key not in seen:
                seen.add(key)
                out.append(item)
        return out

    overnight = fresh(r.get("overnight_news"))
    if r.get("overnight_news") is not None:
        cards.append({"head": f'<b>개장 전</b> · 밤사이 뉴스 {len(overnight)}건 · 갭 {_rv_pct(s.get("gap"))}',
                      "news": overnight, "color": "#6b7178"})
    events = 0
    for item in sorted(r.get("timeline_items") or [], key=lambda it: _tl_minutes(it.get("time")) or 0):
        minutes, price = _tl_minutes(item.get("time")), _finite(item.get("price"))
        if minutes is None or price is None or not 0 <= minutes <= _TL_MINUTES:
            continue
        events += 1
        symbol, color, word = _TL_KINDS.get(item.get("kind"), _TL_KINDS["volume"])
        marks += (f'<div style="position:absolute;left:calc({_tl_x(minutes):.2f}% - 9px);top:{y(price) - 9:.0f}px;'
                  f'width:18px;height:18px;border-radius:50%;background:{color};color:#fff;font-size:10px;'
                  f'line-height:18px;text-align:center;box-shadow:0 0 0 2px #fff" '
                  f'title="{_rv_hhmm(item["time"])} {word}">{symbol}</div>')
        volume = (f' · 거래량 {float(item["volume_ratio"]):.1f}배' if _rv_finite(item.get("volume_ratio")) else "")
        cards.append({"color": color, "news": fresh(item.get("news")),
                      "head": f'<b style="color:{color}">{_rv_hhmm(item["time"])} {symbol} {word}</b> '
                              f'{_rv_pct(item.get("ret"))}{volume} · {price:,.0f}원'})
    news_html = ""
    for card in cards:
        shown, rest = card["news"][:2], len(card["news"]) - 2
        body = "".join(f'<div class="tl-title">{_rv_hhmm(n.get("time"))} · {_tl_link(n, e(str(n.get("title") or "")))}</div>'
                       for n in shown)
        if not card["news"]:
            body = '<div style="color:#8a9199">관련 뉴스 없음</div>'
        elif rest > 0:
            body += f'<div style="color:#8a9199">외 {rest}건 — 아래 전체 목록</div>'
        news_html += (f'<div class="tl-card" style="border-left:3px solid {card["color"]}">'
                      f'<div>{card["head"]}</div>{body}</div>')
        # 장중에 나온 기사는 발행 시각을 그림 아래쪽 끝에 작은 ◆ 로 남긴다. 개장 전 기사는 카드가 이미 말해 준다.
        for n in shown:
            minutes = _tl_minutes(n.get("time"))
            if minutes is None or not 0 <= minutes <= _TL_MINUTES:
                continue
            marks += (f'<div style="position:absolute;left:calc({_tl_x(minutes):.2f}% - 3px);top:{height - 9}px;width:6px;'
                      f'height:6px;background:{card["color"]};transform:rotate(45deg)" '
                      f'title="기사 발행 {_rv_hhmm(n.get("time"))}"></div>')
    svg.append("</svg>")
    high, low = _finite(s.get("high")), _finite(s.get("low"))
    head = " · ".join(f"{name} <b>{value:,.0f}</b>" for name, value in
                      (("시가", open_price), ("종가", close_price), ("고가", high), ("저가", low)) if value)
    label = (f'{r.get("session_date", "")} 장중 가격 경로. 시가 {open_price or 0:,.0f}원, 종가 {close_price or 0:,.0f}원, '
             f'급변 시점 {events}곳.')
    return (_TL_STYLE
            + f'<div style="font-size:12px;color:#6b7178;margin:6px 0 0">{e(str(r.get("session_date", "")))} · {head} '
              '<span style="color:#8a9199">· 시간은 왼쪽에서 오른쪽으로, 가격은 위가 높습니다</span></div>'
            + f'<div class="tl-plot" style="height:{height + 18}px" role="img" aria-label="{e(label)}">'
            + f'<div style="position:absolute;left:0;right:{_TL_GUTTER}px;top:0;height:{height}px">'
            + f'{"".join(svg)}{marks}{labels}</div>'
            + f'<div style="position:absolute;right:0;top:0;width:{_TL_GUTTER}px;height:{height}px">{gutter}</div></div>'
            + (f'<div class="tl-cards">{news_html}</div>' if news_html else "")
            + '<div style="font-size:11px;color:#8a9199;margin:2px 0 8px">가격선은 5분봉 종가를 이은 것입니다. ▲ 급등 · ▼ 급락 · '
              '● 거래량 급증은 급변을 찾은 표시이며 추세 반전을 확정하지 않습니다. 뉴스는 기사 발행 시각(◆) 기준이라 '
              '실제로 알려진 시각과 다를 수 있고, 가격 변동의 원인이라는 뜻이 아닙니다.</div>')


# ---- 장 회고의 수급: 누가 팔고 샀나, 그날 함께 관찰된 것 (2026-09-29 요청) ---------------------------
# 하락한 날은 누가 가장 많이 팔았는지, 상승한 날은 누가 가장 많이 샀는지와 그 이유를 보고 싶다는 요청.
# 이유는 단정하지 않는다 — 매매 주체의 속마음은 자료로 알 수 없다. 대신 그날 확인할 수 있는 사실(시장 전체·업종·
# 환율·전날 밤 미국 반도체·직전 5거래일 흐름·거래량)이 그 매매와 같은 방향이었는지만 적는다.
FLOW_ACTORS = (("foreign_net", "외국인"), ("inst_net", "기관"), ("indiv_net", "개인"))


def _won_text(won):
    """원 금액을 읽기 쉽게. 1조 이상은 조, 그 아래는 억."""
    if won is None or not np.isfinite(won):
        return "—"
    sign = "+" if won > 0 else "−" if won < 0 else ""
    value = abs(won)
    return f"{sign}{value / 1e12:,.2f}조원" if value >= 1e12 else f"{sign}{value / 1e8:,.0f}억원"


def flow_story(today, history, summary, close, prior_5d=None, peer_name="동종 종목", source_note=""):
    """그날 투자자별 순매수와 함께 관찰된 사실. 자료가 없으면 None.

    today: {foreign_net, inst_net, indiv_net} 주식 수(순매수 +). 개인이 없으면 −(외국인+기관)으로 추정하고 그렇게 표시한다.
    history: 오늘 이전 거래일들의 같은 열(DataFrame) — 최근 20일 평균 규모와 견주는 데만 쓴다.
    summary: 회고의 summary(c2c·kospi_c2c·peer_c2c·usdkrw_chg·sox_ret·volume_ratio).
    """
    def num(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if np.isfinite(value) else None

    shares = {key: num((today or {}).get(key)) for key, _ in FLOW_ACTORS}
    if shares["foreign_net"] is None and shares["inst_net"] is None:
        return None
    close = num(close)
    if close is None or close <= 0:
        return None
    estimated = False
    if shares["indiv_net"] is None and shares["foreign_net"] is not None and shares["inst_net"] is not None:
        shares["indiv_net"], estimated = -(shares["foreign_net"] + shares["inst_net"]), True
    actors = []
    for key, label in FLOW_ACTORS:
        if shares[key] is None:
            continue
        usual = None
        if isinstance(history, pd.DataFrame) and key in history and not (key == "indiv_net" and estimated):
            past = pd.to_numeric(history[key], errors="coerce").dropna().tail(20).abs()
            if len(past) >= 5 and past.mean() > 0:
                usual = float(abs(shares[key]) / past.mean())
        actors.append({"key": key, "name": label + ("(추정)" if key == "indiv_net" and estimated else ""),
                       "shares": shares[key], "won": shares[key] * close, "vs_usual": usual})
    # 외국인·기관·개인의 합이 0에서 크게 벗어나면 나머지(기타 법인 등)가 반대편이다. 2026-10-01 삼성전자는 외국인·개인이
    # 함께 팔고 기관은 +127억뿐이었는데, 회고가 기관을 '가장 많이 산 쪽'이라 불렀다(실제로는 기타 법인 등 약 +5,500억).
    # KRX가 기타 법인을 따로 준 날은 그 값을 그대로 쓴다(추정 아님).
    other = num((today or {}).get("other_net"))
    if other is not None and other != 0:
        actors.append({"key": "other_net", "name": "기타 법인", "shares": other, "won": other * close, "vs_usual": None})
    elif not estimated and all(shares[k] is not None for k, _ in FLOW_ACTORS):
        rest = -sum(shares[k] for k, _ in FLOW_ACTORS)
        if abs(rest) * close >= .3 * max(abs(a["won"]) for a in actors):
            actors.append({"key": "other_net", "name": "기타 법인 등(추정)", "shares": rest, "won": rest * close,
                           "vs_usual": None})
    summary = summary or {}
    c2c = num(summary.get("c2c")) or 0.0
    direction = "up" if c2c > 0.001 else "down" if c2c < -0.001 else "flat"
    # 세 주체가 모두 한쪽이면(예: 상승한 날 셋 다 순매도) 반대편은 기타 법인 등 — 가장 덜 판 쪽을 '산 쪽'이라
    # 부르지 않는다(2026-09-30 SK하이닉스 회고의 오류). all_one_side 를 세우고 counter 는 None.
    # '모두 한쪽'은 외국인·기관·개인 셋으로 판정한다 — 그때 반대편은 늘 기타 법인 등이라 따로 부르지 않는다.
    main = [a for a in actors if a["key"] != "other_net"]
    all_one_side = all(a["won"] < 0 for a in main) or all(a["won"] > 0 for a in main)
    if direction == "down":
        lead = min(main if all_one_side else actors, key=lambda a: a["won"])
        counter = None if all_one_side else max(actors, key=lambda a: a["won"])
    elif direction == "up":
        lead = max(actors, key=lambda a: a["won"]) if not all_one_side else min(main, key=lambda a: a["won"])
        counter = None if all_one_side else min(actors, key=lambda a: a["won"])
    else:
        lead = max(actors, key=lambda a: abs(a["won"]))
        counter = None

    seen = market_observations(summary, prior_5d=prior_5d, peer_name=peer_name, foreign=shares["foreign_net"])
    other_actor = next((a for a in actors if a["key"] == "other_net"), None)
    if other_actor and other_actor["won"] > 0 and summary.get("buyback"):
        seen.append(f"{other_actor['name']} 순매수 {_won_text(other_actor['won'])} — 회사의 자사주 매입 기간이라 "
                    "회사 매입과 맞는 모양입니다.")
    if lead.get("vs_usual") is not None and lead["vs_usual"] >= 2:
        seen.append(f"{lead['name']}의 순{'매도' if lead['won'] < 0 else '매수'} 규모가 최근 20거래일 평균의 "
                    f"{lead['vs_usual']:.1f}배로 컸습니다.")
    return {"direction": direction, "c2c": c2c, "actors": actors, "lead": lead, "counter": counter,
            "all_one_side": all_one_side, "observations": seen, "estimated_indiv": estimated,
            "source_note": source_note}


_MA_KIND = {"support": "{n}일선({lv}) 부근까지 내려왔다가 그 위에서 마감 — {n}일선 지지 후 반등의 모양입니다.",
            "resistance": "{n}일선({lv}) 부근까지 올랐다가 그 아래에서 마감 — {n}일선을 넘지 못하고 밀린 모양입니다.",
            "reclaim": "장중 {n}일선({lv}) 아래로 빠졌다가 그 위로 되돌아와 마감했습니다.",
            "fail": "장중 {n}일선({lv}) 위로 올랐다가 그 아래로 밀려 마감했습니다."}


def market_observations(summary, prior_5d=None, peer_name="동종 종목", foreign=None):
    """그날 함께 관찰된 사실(수급 자료가 없어도 쓸 수 있는 것). 매매 이유를 단정하지 않는다.

    2026-10-01: 장 회고 영상과 비교해 더한 것 — 이동평균선 지지·저항, 전날 밤 미국 대표주의 시간외 반응(실적 발표는 장
    마감 뒤라 정규장 등락에 안 보인다), 한국 장중의 나스닥 선물·미 국채 선물·원/달러(고점 이후 구간 포함).
    예전에는 이 목록이 수급 자료가 있을 때만 나와, 수급이 늦은 날(10/1 16:10)에는 통째로 빠졌다.
    """
    def num(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if np.isfinite(value) else None

    def pct(value):
        return f"{value:+.2%}"

    summary = summary or {}
    c2c = num(summary.get("c2c")) or 0.0
    direction = "up" if c2c > 0.001 else "down" if c2c < -0.001 else "flat"
    seen = []
    # 회고 영상의 비교 방법을 당시 가격·수급으로 재현한다(2026-10-02). 예측이나 인과로 해석하지 않는다.
    context = summary.get("price_context") or {}
    for window, item in context.get("breakouts", {}).items():
        state = ("종가 돌파" if item["close_breakout"] else
                 "장중 돌파 후 종가는 고점 이하" if item["intraday_breakout"] else "종가 돌파 없음")
        seen.append(f"전일까지 {window}거래일 고점: {state} · 종가 위치 {item['distance']:+.2%}.")
    for window, item in context.get("relative", {}).items():
        seen.append(f"같은 {window}거래일 수익률: 이 종목 {item['own']:+.2%}, {peer_name} {item['peer']:+.2%} "
                    f"· 차이 {item['excess'] * 100:+.2f}%p.")
    pressure = summary.get("foreign_pressure")
    if pressure:
        labels = {"selling_eased": "순매도 지속·거래량 대비 강도 약화",
                  "selling_intensified": "순매도 지속·거래량 대비 강도 강화",
                  "selling_unchanged": "순매도 지속·거래량 대비 강도 유지",
                  "turned_buying": "5일 합계 순매수 전환", "turned_selling": "5일 합계 순매도 전환",
                  "other": "5일 수급 비교"}
        seen.append(f"외국인 {labels[pressure['state']]}: 직전 5일 → 최근 5일 순매수/거래량 "
                    f"{pressure['previous_ratio']:+.2%} → {pressure['recent_ratio']:+.2%} "
                    f"(일평균 {pressure['previous_daily_net']:+,.0f}주 → {pressure['recent_daily_net']:+,.0f}주). "
                    "향후 상승을 뜻하는 신호는 아닙니다.")
    transition = summary.get("buyback_transition")
    if transition:
        seen.append(f"자사주 공시상 예정 종료일 {transition['scheduled_end']} 이후 "
                    f"{transition['sessions_after']}거래일 — 실제 매입 완료 여부는 별도 확인이 필요합니다.")
        labels = {"foreign_net": "외국인", "inst_net": "기관", "indiv_net": "개인", "other_net": "기타 법인"}
        for window, actors in transition.get("windows", {}).items():
            parts = [f"{labels[k]} {v['before']:+,.0f}주 → {v['after']:+,.0f}주" for k, v in actors.items()]
            seen.append(f"예정 종료 직전·직후 각 {window}거래일 누적 순매수: " + " / ".join(parts) +
                        " (기타 법인 전체를 자사주 매입으로 볼 수 없습니다).")
        if not transition.get("windows"):
            seen.append("종료 전후 같은 길이의 수급 자료가 아직 부족해 비교를 보류합니다.")
    kospi, peer = num(summary.get("kospi_c2c")), num(summary.get("peer_c2c"))
    fx, sox, volume = num(summary.get("usdkrw_chg")), num(summary.get("sox_ret")), num(summary.get("volume_ratio"))
    # 같은 방향·비슷한 크기 / 같은 방향이지만 이 종목이 더 큼 / 반대 방향 — 세 경우를 구분한다.
    if kospi is not None and abs(c2c) > 0:
        if np.sign(kospi) == np.sign(c2c) and abs(kospi) >= .5 * abs(c2c):
            seen.append(f"코스피도 {pct(kospi)} — 시장 전체가 같은 방향이었습니다.")
        elif np.sign(kospi) == np.sign(c2c):
            seen.append(f"코스피도 {pct(kospi)}였지만 이 종목({pct(c2c)})이 훨씬 크게 움직였습니다.")
        else:
            seen.append(f"코스피는 반대로 {pct(kospi)} — 시장 전체와 다른 움직임이었습니다.")
    if peer is not None and abs(c2c) > 0:
        if np.sign(peer) == np.sign(c2c) and abs(peer) >= .5 * abs(c2c):
            seen.append(f"{peer_name} {pct(peer)} — 반도체 업종이 함께 움직였습니다.")
        elif np.sign(peer) == np.sign(c2c):
            seen.append(f"{peer_name}도 {pct(peer)}였지만 이 종목이 더 크게 움직였습니다.")
        else:
            seen.append(f"{peer_name}는 반대로 {pct(peer)} — 업종 전체의 움직임은 아니었습니다.")
    if fx is not None and foreign is not None:
        if foreign < 0 and fx >= .003:
            seen.append(f"원/달러 {pct(fx)}(원화 약세) — 외국인 매도와 같은 방향의 환율 움직임입니다.")
        elif foreign > 0 and fx <= -.003:
            seen.append(f"원/달러 {pct(fx)}(원화 강세) — 외국인 매수와 같은 방향의 환율 움직임입니다.")
        elif foreign < 0 and fx <= -.003:
            seen.append(f"원화는 오히려 강세({pct(fx)})여서 외국인 매도가 환율로는 설명되지 않습니다.")
    nights = num(summary.get("us_nights")) or 1
    us_when = f"휴장 기간({int(nights)}거래일 누적) " if nights > 1 else "전날 밤 "
    if sox is not None and direction != "flat":
        same = (sox < -.01 and direction == "down") or (sox > .01 and direction == "up")
        opposite = (sox > .005 and direction == "down") or (sox < -.005 and direction == "up")
        if same:
            seen.append(f"{us_when}미국 반도체지수(SOX) {pct(sox)} — 미국 반도체 흐름과 같은 방향입니다.")
        elif opposite:
            seen.append(f"{us_when}SOX는 {pct(sox)}로 반대 방향이어서 미국 반도체 흐름으로는 설명되지 않습니다.")
    micron = num(summary.get("micron_ret"))
    if micron is not None and direction != "flat":
        if (micron < -.02 and direction == "down") or (micron > .02 and direction == "up"):
            seen.append(f"{us_when}마이크론 {pct(micron)} — 미국 메모리 회사와 같은 방향입니다.")
        elif (micron > .02 and direction == "down") or (micron < -.02 and direction == "up"):
            seen.append(f"{us_when}마이크론은 {pct(micron)}로 반대 방향이었습니다.")
    streak = num(summary.get("prior_streak"))
    if streak is not None and abs(streak) >= 3 and direction != "flat":
        if streak > 0 and direction == "down":
            seen.append(f"직전 {int(streak)}거래일 연속 상승 뒤의 하락입니다 — 차익 실현과 맞는 모양입니다.")
        elif streak < 0 and direction == "up":
            seen.append(f"직전 {int(-streak)}거래일 연속 하락 뒤의 반등입니다.")
        elif streak > 0:
            seen.append(f"{int(streak) + 1}거래일 연속 상승이 이어졌습니다.")
        else:
            seen.append(f"{int(-streak) + 1}거래일 연속 하락이 이어졌습니다.")
    prior = num(prior_5d)
    if prior is not None:
        if direction == "down" and prior >= .08:
            seen.append(f"직전 5거래일 {pct(prior)} 오른 뒤의 매도 — 차익 실현과 맞는 모양입니다.")
        elif direction == "up" and prior <= -.08:
            seen.append(f"직전 5거래일 {pct(prior)} 내린 뒤의 매수 — 저가 매수와 맞는 모양입니다.")
    if volume is not None and volume >= 1.5:
        seen.append(f"거래량이 20일 평균의 {volume:.1f}배였습니다.")
    # 종가가 당일 범위의 어디였나(2026-10-02, 회고 영상의 '종가가 고가 부근에서 마무리'). 장 막판에 밀렸는지
    # 끌어올렸는지를 등락률만으로는 알 수 없다. 범위가 거의 없던 날은 말하지 않는다.
    high, low, close = num(summary.get("high")), num(summary.get("low")), num(summary.get("close"))
    if high and low and close and high > low and (high - low) / close >= .005:
        position = (close - low) / (high - low)
        if position >= .8:
            seen.append(f"종가가 당일 범위(저가 {low:,.0f}~고가 {high:,.0f}원)의 위쪽 {position:.0%} 지점 — 고가 부근에서 마감했습니다.")
        elif position <= .2:
            seen.append(f"종가가 당일 범위(저가 {low:,.0f}~고가 {high:,.0f}원)의 아래쪽 {position:.0%} 지점 — 저가 부근에서 마감했습니다.")
    for touch in summary.get("ma_touches") or []:
        text = _MA_KIND.get(touch.get("kind"))
        level = num(touch.get("level"))
        if text and level is not None:
            seen.append(text.format(n=int(touch.get("ma", 0)), lv=f"{level:,.0f}원"))
    for key, name in (("micron_ah", "마이크론은"), ("nvidia_ah", "엔비디아는")):
        ah = summary.get(key) or {}
        ret, low, high = num(ah.get("ret")), num(ah.get("low_ret")), num(ah.get("high_ret"))
        if ret is None:
            continue
        swing = max(abs(low or 0), abs(high or 0))
        if abs(ret) >= .01 or swing >= .015:
            span = f"(장중 {pct(low)}~{pct(high)})" if low is not None and high is not None else ""
            seen.append(f"{name} 미국 정규장 마감 뒤 시간외에서 {pct(ret)}{span} — 정규장 등락에 안 보이는 반응입니다.")
    cross = summary.get("session_cross") or {}
    parts = []
    for ticker, row in cross.items():
        ret = num((row or {}).get("ret"))
        if ret is None:
            continue
        label = row.get("label", ticker)
        note = ""
        if ticker == "ZN=F" and abs(ret) >= .001:
            note = "(금리 하락)" if ret > 0 else "(금리 상승)"
        elif ticker == "KRW=X" and abs(ret) >= .001:
            note = "(원화 약세)" if ret > 0 else "(원화 강세)"
        after = num(row.get("after_high"))
        tail = f", 이 종목 고점 이후 {pct(after)}" if after is not None and num(summary.get("from_high")) is not None \
            and num(summary.get("from_high")) <= -.01 else ""
        parts.append(f"{label} {pct(ret)}{note}{tail}")
    if parts:
        seen.append("우리 장중(09:00~15:30) " + " · ".join(parts) + ".")
    for buy in summary.get("buyback") or []:
        amount = f", {buy['amount']}" if buy.get("amount") else ""
        seen.append(f"자사주 매입 기간 중({buy.get('kind', '')} {buy.get('start', '')}~{buy.get('end', '')}{amount}, DART 공시) — "
                    "회사 자신의 매수는 기타 법인으로 잡힙니다.")
    from_high = num(summary.get("from_high"))
    if from_high is not None and from_high <= -.02:
        seen.append(f"마감가가 장중 고가보다 {pct(from_high)} 아래 — 오른 폭을 장중에 꽤 반납했습니다.")
    return seen


def flow_story_html(story, number=None):
    """flow_story 결과를 한 줄 요약 + 투자자별 막대로. 막대는 순매도 왼쪽(빨강)·순매수 오른쪽(초록)."""
    from html import escape as e
    if not story:
        return ""
    lead, counter = story["lead"], story["counter"]
    if story.get("all_one_side"):
        side = "순매도" if lead["won"] < 0 else "순매수"
        head = (f'{_rv_pct(story["c2c"])} — 외국인·기관·개인이 <b>모두 {side}</b>'
                f'(가장 큰 쪽 {e(lead["name"])} {_won_text(lead["won"])}). 반대편은 기타 법인 등입니다')
    else:
        verb = {"down": "가장 많이 판 쪽", "up": "가장 많이 산 쪽", "flat": "가장 크게 움직인 쪽"}[story["direction"]]
        head = f'{_rv_pct(story["c2c"])} — {verb}은 <b>{e(lead["name"])}</b> ({_won_text(lead["won"])})'
        if lead.get("vs_usual") is not None and lead["vs_usual"] >= .1:
            head += f', 최근 20거래일 평균의 {lead["vs_usual"]:.1f}배'
        if counter is not None and counter is not lead and np.sign(counter["won"]) != np.sign(lead["won"]):
            head += f'. 반대편은 <b>{e(counter["name"])}</b> ({_won_text(counter["won"])})'
    scale = max(abs(a["won"]) for a in story["actors"]) or 1.0
    bars = ""
    for a in story["actors"]:
        width = abs(a["won"]) / scale * 50
        color = "#1e6b34" if a["won"] > 0 else "#a8322a"
        left = 50 - width if a["won"] < 0 else 50
        bars += ('<div style="display:flex;align-items:center;gap:8px;margin:4px 0">'
                 f'<div style="flex:0 0 100px;font-size:12px;color:#3a4652">{e(a["name"])}</div>'
                 '<div style="flex:1;position:relative;height:16px;background:#f3f5f8;border-radius:3px">'
                 '<div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:#c3c8cf"></div>'
                 f'<div style="position:absolute;left:{left:.1f}%;width:{width:.1f}%;top:2px;bottom:2px;'
                 f'background:{color};border-radius:2px"></div></div>'
                 f'<div style="flex:0 0 120px;font-size:12px;text-align:right;color:{color}">{_won_text(a["won"])}</div></div>')
    seen = "".join(f"<li>{e(x)}</li>" for x in story["observations"]) or "<li>함께 볼 만한 사실이 없습니다.</li>"
    notes = []
    if any(a.get("key") == "other_net" and "추정" in a.get("name", "") for a in story["actors"]):
        notes.append("기타 법인 등은 외국인·기관·개인 합계의 반대편으로 추정했습니다")
    if story.get("estimated_indiv"):
        notes.append("개인은 자료가 없어 외국인·기관의 반대편으로 추정했습니다(기타 법인 포함)")
    if story.get("source_note"):
        # 수집기가 남긴 출처 코드를 읽는 말로(2026-09-30: '출처 last_successful_fetch'가 그대로 보였다).
        note = str(story["source_note"])
        for code, words in (("+cache", " + 보관본"), ("last_successful_fetch", "저장소 보관본(마지막 성공분)"), ("explicit_cache_replay", "캐시"),
                            ("KRX(pykrx)", "KRX"), ("user_csv", "직접 넣은 CSV"), ("naver", "네이버 금융")):
            note = note.replace(code, words)
        notes.append(note)
    n1, n2 = (f"{number}. ", f"{number + 1}. ") if number else ("", "")
    return (f'<div style="font-size:14px;margin:16px 0 6px"><b>{n1}누가 팔고 샀나</b></div>'
            f'<div style="font-size:13px;margin:0 0 6px">{head}</div>'
            f'<div style="margin:4px 0 8px">{bars}</div>'
            f'<div style="font-size:14px;margin:16px 0 6px"><b>{n2}그날 함께 관찰된 것</b> '
            '<span style="color:#6b7178;font-size:12px">— 매매 이유를 단정하지 않습니다</span></div>'
            f'<ul style="margin:0 0 6px;padding-left:20px;font-size:13px;color:#4a4f55">{seen}</ul>'
            + (f'<div style="font-size:12px;color:#8a9199;margin:0 0 8px">{e(" · ".join(notes))}</div>' if notes else ""))


# ---- 장 회고의 '그날의 맥락'(2026-09-30: 장 마감 회고 영상 검토에서 나온 항목) -------------------------------
# ① 배당락일 — 시가가 배당만큼 기계적으로 낮게 출발한다(9/29 삼성전자 4,600원 ≈ 1.7%). 회고가 이를 '밤사이 하락'으로
#    읽지 않게, 배당을 되돌린 갭·등락률을 함께 보인다.
# ② 달력 — 분기말·월말(기관 리밸런싱), 옵션 만기일(매월 둘째 목요일), 선물·옵션 동시 만기일(3·6·9·12월).
#    휴장일로 당겨지는 경우는 모른다.
# ③ 다가오는 주요 일정 — 미국 발표 일정표(data_sources/us_calendar)를 그대로 쓴다.
CORPORATE_ACTIONS_PATH = "macro_inputs/corporate_actions.csv"
KRX_HOLIDAYS_PATH = "macro_inputs/krx_holidays.csv"      # 사람이 확인해 적는 휴장일(주말 제외)
KR_CALENDAR_PATH = "macro_inputs/kr_calendar.csv"        # 국내 일정(잠정실적 등). confirmed=N 이면 '(예상·미확정)'


def _kr_events(day, target, days=7, path=KR_CALENDAR_PATH):
    import os
    if not os.path.exists(path):
        return []
    try:
        table = pd.read_csv(path, dtype=str)
    except (OSError, ValueError):
        return []
    start, end = pd.Timestamp(day).normalize(), pd.Timestamp(day).normalize() + pd.Timedelta(days=days)
    out = []
    for _, row in table.iterrows():
        when = pd.to_datetime(row.get("date"), errors="coerce")
        if pd.isna(when) or not (start <= when <= end) or str(row.get("target", "")) not in ("", str(target)):
            continue
        label = str(row.get("label", ""))
        if str(row.get("confirmed", "")).upper() != "Y":
            label += " · 미확정"
        out.append({"date": when.date().isoformat(), "event": "KR", "label": label})
    return out


def _krx_holidays(path=KRX_HOLIDAYS_PATH):
    import os
    if not os.path.exists(path):
        return set()
    try:
        return set(pd.to_datetime(pd.read_csv(path, dtype=str)["date"]).dt.normalize())
    except (OSError, ValueError, KeyError):
        return set()


def _krx_holiday_events(day, days=7, path=KRX_HOLIDAYS_PATH):
    """그날 다음부터 days 일 안의 한국 증시 휴장일(사람이 확인해 적은 파일). 장 회고 영상처럼 '다음 주 휴장'을 미리 보인다."""
    import os
    if not os.path.exists(path):
        return []
    try:
        table = pd.read_csv(path, dtype=str)
    except (OSError, ValueError):
        return []
    start = pd.Timestamp(day).normalize()
    out = []
    for _, row in table.iterrows():
        when = pd.to_datetime(row.get("date"), errors="coerce")
        if pd.isna(when) or not (start < when <= start + pd.Timedelta(days=days)) or when.weekday() >= 5:
            continue
        name = str(row.get("name") or "").strip()
        out.append({"date": when.date().isoformat(), "event": "KRX",
                    "label": "한국 증시 휴장" + (f"({name})" if name and name != "nan" else "")})
    return out


def next_krx_session(day, holidays=None):
    """다음 거래일(주말·파일의 휴장일 건너뜀)."""
    holidays = _krx_holidays() if holidays is None else holidays
    nxt = pd.Timestamp(day).normalize() + pd.Timedelta(days=1)
    while nxt.weekday() >= 5 or nxt in holidays:
        nxt += pd.Timedelta(days=1)
    return nxt


def _dividend_from_file(day, target, path=CORPORATE_ACTIONS_PATH):
    """사람이 확인해 적은 배당락(원/주). 없으면 None."""
    import os
    if not target or not os.path.exists(path):
        return None, None
    try:
        table = pd.read_csv(path, dtype=str)
    except (OSError, ValueError):
        return None, None
    rows = table[(table["date"] == str(day)) & (table["target"] == str(target))]
    if rows.empty:
        return None, None
    value = pd.to_numeric(rows.iloc[0]["dividend_krw"], errors="coerce")
    return (float(value), str(rows.iloc[0].get("source", ""))) if np.isfinite(value) and value > 0 else (None, None)


def review_context(review):
    """회고 한 건의 '그날의 맥락'. {'dividend': {...} | None, 'calendar': [str], 'next_events': [dict]}."""
    r = review or {}
    day = pd.Timestamp(str(r.get("session_date", ""))[:10])
    saved = dict(r.get("context") or {})
    s = r.get("summary") or {}
    out = {"dividend": None, "calendar": [], "next_events": []}
    # ① 배당락
    # 사람이 확인해 적은 값이 먼저, 없으면 회고 실행이 Yahoo 에서 받아 둔 값.
    per_share, source = _dividend_from_file(day.date().isoformat(), r.get("target"))
    if not per_share:
        per_share, source = saved.get("dividend_krw"), saved.get("dividend_source")
    prev, open_, close = (_rv_finite(s.get(k)) and float(s[k]) for k in ("prev_close", "open", "close"))
    if per_share and prev and open_ and close:
        out["dividend"] = {"per_share": float(per_share), "pct": float(per_share) / prev, "source": source or "",
                           "gap_ex": (open_ + float(per_share)) / prev - 1,
                           "c2c_ex": (close + float(per_share)) / prev - 1}
    # ② 달력
    nxt = next_krx_session(day)
    tomorrow_div, _ = _dividend_from_file(nxt.date().isoformat(), r.get("target"))
    if tomorrow_div:
        out["calendar"].append(f"내일({nxt:%m/%d})이 배당락일 — 주당 {tomorrow_div:,.0f}원. 락 전에 팔려는 선반영 매도가 "
                               "나올 수 있는 날입니다.")
    if (nxt - day).days >= 4:
        out["calendar"].append(f"연휴 전 마지막 거래일 — 다음 거래일이 {nxt:%m/%d}({(nxt - day).days}일 뒤). 연휴 위험을 "
                               "피하려는 매물이 나올 수 있는 날입니다.")
    if nxt.month != day.month:
        if day.month in (3, 6, 9, 12):
            out["calendar"].append(f"{(day.month - 1) // 3 + 1}분기 마지막 거래일 — 기관이 분기말에 주식·현금 비중을 "
                                   "맞추는 물량(리밸런싱)이 오후에 나올 수 있는 날입니다.")
        else:
            out["calendar"].append("월 마지막 거래일 — 월말 비중 조정 물량이 나올 수 있는 날입니다.")
    thursdays = pd.date_range(day.replace(day=1), day + pd.offsets.MonthEnd(0), freq="W-THU")
    if len(thursdays) >= 2 and day == thursdays[1]:
        out["calendar"].append("선물·옵션 동시 만기일 — 만기 청산 물량으로 마감 무렵 변동이 커질 수 있습니다."
                               if day.month in (3, 6, 9, 12) else
                               "옵션 만기일 — 만기 청산 물량으로 마감 무렵 변동이 커질 수 있습니다.")
    # ③ 다가오는 일정(그날 밤 ~ 나흘)
    try:
        from data_sources.us_calendar import upcoming_us_events
        out["next_events"] = upcoming_us_events(day, days=4)
    except Exception:
        out["next_events"] = []
    out["next_events"] = sorted(out["next_events"] + _kr_events(day, r.get("target")) + _krx_holiday_events(day),
                                key=lambda ev: ev["date"])
    return out


def review_context_html(context):
    """맨 위 '그날의 맥락' 상자. 적을 것이 없으면 빈 문자열."""
    from html import escape as e
    items = []
    d = (context or {}).get("dividend")
    if d:
        items.append(f'<b>배당락일</b> — 주당 {d["per_share"]:,.0f}원(전일 종가의 {d["pct"]:.2%})만큼 시가가 기계적으로 낮게 '
                     f'출발합니다. 배당을 되돌려 보면 갭 {_rv_pct(d["gap_ex"])}, 하루 {_rv_pct(d["c2c_ex"])}입니다.'
                     + (f' <span style="color:#8a9199;font-size:12px">출처: {e(d["source"])}</span>' if d.get("source") else ""))
    items += [e(x) for x in (context or {}).get("calendar", [])]
    if not items:
        return ""
    return ('<div style="background:#fdf6e3;border-left:4px solid #c79a2b;padding:9px 12px;margin:8px 0 10px;font-size:13px">'
            '<div style="font-weight:700;margin-bottom:4px">그날의 맥락</div>'
            '<ul style="margin:0;padding-left:18px;line-height:1.7">' + "".join(f"<li>{x}</li>" for x in items) + "</ul></div>")


def review_next_events_html(context, number):
    """회고 끝 '다가오는 주요 일정'(그날부터 나흘). 일정이 없으면 빈 문자열."""
    from html import escape as e
    events = (context or {}).get("next_events") or []
    if not events:
        return ""
    rows = "".join(f'<li><b>{e(ev["date"])}</b> {e(ev["label"])}</li>' for ev in events)
    return (f'<div style="font-size:14px;margin:16px 0 6px"><b>{number}. 다가오는 주요 일정</b> '
            '<span style="color:#6b7178;font-size:12px">— 미국 발표는 한국 시각으로 대개 다음 날 새벽 · 국내 잠정실적은 회사 공지 전이면 예상일</span></div>'
            f'<ul style="margin:0 0 6px;padding-left:20px;font-size:13px;line-height:1.7">{rows}</ul>')


def earnings_reactions_html(stats, name=""):
    """과거 잠정실적 발표일의 주가 반응(2026-10-01). '발표 전에 오르면 발표날 내리고, 내렸으면 발표 뒤 반등'이라는
    흔한 말을 이 종목의 기록으로 확인해 보인다. 예측이 아니라 과거 빈도이며 표본이 작다는 것을 함께 적는다."""
    from html import escape as e
    if not stats or not stats.get("n"):
        return ""

    def rate(k, n):
        return f"{k}/{n}번" if n else "사례 없음"

    lines = [f'{e(str(stats.get("first", "")))}~{e(str(stats.get("last", "")))} 잠정실적 발표일 {stats["n"]}번 중 '
             f'발표날 하락 {rate(stats.get("down", 0), stats["n"])}, 평균 {_rv_pct(stats.get("mean", 0))}.']
    up, down = stats.get("prior_up") or {}, stats.get("prior_down") or {}
    if up.get("n"):
        lines.append(f'발표 전 20거래일 오른 뒤: 발표날 하락 {rate(up.get("down", 0), up["n"])}, 평균 {_rv_pct(up.get("mean", 0))}.')
    if down.get("n"):
        line = f'발표 전 20거래일 내린 뒤: 발표날 하락 {rate(down.get("down", 0), down["n"])}, 평균 {_rv_pct(down.get("mean", 0))}'
        if down.get("next5_n"):
            line += f' · 그 뒤 5거래일 상승 {rate(down.get("next5_up", 0), down["next5_n"])}'
        lines.append(line + ".")
    rows = stats.get("rows") or []
    recent = " · ".join(f'{e(str(x.get("date", "")))} {_rv_pct(x.get("ret", 0))}' for x in rows if x.get("ret") is not None)
    return ('<div style="background:#f3f7fb;border-left:4px solid #5b7fa6;padding:9px 12px;margin:8px 0 10px;font-size:13px">'
            f'<div style="font-weight:700;margin-bottom:4px">{e(name)} 실적 발표일, 과거에는 어땠나</div>'
            '<ul style="margin:0;padding-left:18px;line-height:1.7">' + "".join(f"<li>{x}</li>" for x in lines) + "</ul>"
            + (f'<div style="font-size:12px;color:#6b7178;margin-top:4px">최근: {recent}</div>' if recent else "")
            + '<div style="font-size:12px;color:#8a9199;margin-top:4px">과거 빈도일 뿐 이번 발표의 예측이 아닙니다. '
            '표본이 수십 번이라 한두 번의 차이는 우연과 구별되지 않습니다. 출처: DART 잠정실적 공시일 · 종가.</div></div>')


def review_market_html(market):
    """시장 전체(코스피) 한 칸: 오른·내린 종목 수, 시장 전체 투자자별 순매수, 지수의 당일 범위와 종가 위치.

    2026-10-02: 장 마감 회고 영상은 종목보다 먼저 시장 전체를 본다. 회고에는 이 종목의 수급만 있었다.
    시장 분위기를 보는 숫자이지 이 종목이 움직인 이유를 말하는 것이 아니다. 자료가 없으면 빈 문자열.
    """
    m = market if isinstance(market, dict) else {}
    rise, fall = _finite(m.get("rise")), _finite(m.get("fall"))
    if rise is None or fall is None:
        return ""
    steady = _finite(m.get("steady")) or 0
    total = rise + fall + steady
    if total <= 0:
        return ""
    lean = "오른 종목이 더 많았습니다" if rise > fall else "내린 종목이 더 많았습니다" if fall > rise else "오른 종목과 내린 종목 수가 같았습니다"
    limits = " · ".join(text for text, value in ((f"상한가 {int(_finite(m.get('upper')) or 0)}", _finite(m.get("upper"))),
                                                 (f"하한가 {int(_finite(m.get('lower')) or 0)}", _finite(m.get("lower")))) if value)
    segments = "".join(
        f'<div style="flex:{value:.0f} 1 0;min-width:0;background:{color};height:10px"></div>'
        for value, color in ((rise, "#1e6b34"), (steady, "#c5ccd3"), (fall, "#a8322a")) if value > 0)
    out = ['<div style="margin:10px 0 6px;padding:10px 14px;border:1px solid #e3e8ee;border-radius:6px;background:#fbfdff">'
           '<div style="font-size:13px;font-weight:700;margin-bottom:6px">시장 전체 (코스피) '
           f'<span style="font-weight:400;color:#8a9199;font-size:12px">— {lean}</span></div>'
           f'<div style="display:flex;gap:2px;border-radius:5px;overflow:hidden">{segments}</div>'
           '<div style="display:flex;justify-content:space-between;font-size:12px;margin:3px 0 0">'
           f'<span style="color:#1e6b34"><b>상승 {rise:,.0f}</b></span>'
           f'<span style="color:#6b7178">보합 {steady:,.0f}</span>'
           f'<span style="color:#a8322a"><b>하락 {fall:,.0f}</b></span></div>']
    if limits:
        out.append(f'<div style="font-size:11px;color:#8a9199">{limits}</div>')
    flows = [(name, _finite(m.get(key))) for name, key in (("개인", "individual"), ("외국인", "foreign"), ("기관", "institution"))]
    if any(value is not None for _, value in flows):
        chips = "".join(
            '<div style="flex:1 1 90px;min-width:0;border:1px solid #e3e8ee;border-radius:5px;padding:5px 9px;background:#fff">'
            f'<div style="font-size:11px;color:#7a8797">{name} 순매수</div>'
            f'<div style="font-size:14px;font-weight:700;color:{"#1e6b34" if value > 0 else "#a8322a" if value < 0 else "#1a1a1a"}">'
            f'{_won_text(value * 1e8)}</div></div>'
            for name, value in flows if value is not None)
        out.append(f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">{chips}</div>')
    high, low, close, prev = (_finite(m.get(k)) for k in ("high", "low", "close", "prev_close"))
    if high and low and close and high > low:
        position = min(max((close - low) / (high - low), 0.0), 1.0)
        change = f" · 전일 대비 {close - prev:+,.2f}p({close / prev - 1:+.2%})" if prev else ""
        where = "고가 부근 마감" if position >= .8 else "저가 부근 마감" if position <= .2 else "범위 중간 마감"
        out.append(
            '<div style="font-size:12px;color:#4a4f55;margin-top:8px">'
            f'지수 종가 <b>{close:,.2f}</b>{change} · 장중 저점 대비 {close - low:+,.2f}p — {where}</div>'
            '<div style="position:relative;height:14px;margin:4px 0 0">'
            '<div style="position:absolute;left:0;right:0;top:6px;height:2px;background:#e6ebf0"></div>'
            f'<div style="position:absolute;left:calc({position * 100:.1f}% - 5px);top:2px;width:10px;height:10px;'
            'border-radius:50%;background:#1a5490"></div></div>'
            '<div style="display:flex;justify-content:space-between;font-size:11px;color:#8a9199">'
            f'<span>저가 {low:,.2f}</span><span>고가 {high:,.2f}</span></div>')
    out.append('<div style="font-size:11px;color:#8a9199;margin-top:6px">출처 네이버 증권(순매수는 억원 단위 집계, 장 마감 뒤 '
               '잠정치) · 시장 분위기를 보는 숫자이며 이 종목이 움직인 이유를 말하지 않습니다.</div></div>')
    return "".join(out)


def review_section_html(review, carried=False):
    """장 마감 회고 절 HTML. carried=True 면 새로 만든 다음 거래일 보고서에 다시 붙이는 직전 거래일 회고다."""
    from html import escape as e
    r = review
    if carried:
        heading = f'직전 거래일 장 회고 — {e(r["session_date"])}'
        note = (f'<b>{e(r["session_date"])} 장 마감 후 생성 {e(r["generated_at"])}</b> — 다음 거래일 보고서를 새로 만들며 '
                '이 회고를 다시 붙였습니다. 그날 장을 설명할 뿐 성능표·다음 거래일 예측을 바꾸지 않습니다.')
    else:
        heading = f'오늘 장 회고 — {e(r["session_date"])}'
        note = (f'<b>장 마감 후 생성 {e(r["generated_at"])}</b> — 이 절은 오늘 장을 설명할 뿐 성능표·다음 거래일 '
                '예측을 바꾸지 않습니다.')
    parts = [REVIEW_START,
             '<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
             f'{heading}</h3>',
             '<div style="font-size:12px;color:#6b7178;margin:4px 0 8px;padding:8px 12px;background:#f7f8fa;border-radius:5px">'
             f'{note}</div>']
    s = r["summary"]
    # 순서(2026-09-30 요청): 1 누가 팔고 샀나 → 2 그날 함께 관찰된 것 → 3 흐름이 바뀐 시각과 뉴스 → 4 오늘 장(표와
    # 흐름의 성격) → 5 아침 예측과 비교. 번호는 실제로 나오는 소제목에만 차례로 붙인다(수급이 없는 날은 1·2가 빠진다).
    counter = iter(range(1, 20))

    def sub(title, extra=""):
        return (f'<div style="font-size:14px;margin:16px 0 6px"><b>{next(counter)}. {title}</b>{extra}</div>')

    context = review_context(r)
    parts.append(review_context_html(context))
    parts.append(review_market_html(r.get("market")))      # 시장 전체 숫자(없으면 빈 문자열, 2026-10-02)
    story = r.get("flow_story")
    if story and any("마지막 거래일" in x for x in context.get("calendar", [])):
        inst = next((a for a in story.get("actors", []) if a.get("key") == "inst_net"), None)
        if inst and inst.get("won", 0) < 0:
            when = "분기" if any("분기 마지막" in x for x in context["calendar"]) else "월"
            line = (f"{when} 마지막 거래일에 기관이 {_won_text(inst['won'])} 순매도 — {when}말 리밸런싱(주식·현금 비중 "
                    "맞추기)과 맞는 모양입니다.")
            story = dict(story, observations=list(story.get("observations", [])) + [line])
    if story:
        parts.append(flow_story_html(story, number=next(counter)))
        next(counter)   # flow_story_html 이 두 소제목(누가 팔고 샀나 · 그날 함께 관찰된 것)을 쓴다
    else:
        # 수급이 늦은 날(2026-10-01 16:10)에도 시장·업종·이동평균선·시간외·장중 해외 지표는 보인다.
        seen = market_observations(s, prior_5d=s.get("prior_5d"), peer_name=r.get("peer_name") or "동종 종목")
        status = r.get("flow_status") or {}
        if status.get("state") == "failed":
            # 받기에 실패한 것은 '아직 집계 전'과 구별해 눈에 띄게 적는다(2026-10-01 요청).
            parts.append('<div style="font-size:13px;margin:10px 0 6px;padding:8px 12px;background:#fdecea;'
                         'border-left:4px solid #a8322a;color:#7a2318"><b>투자자별 수급을 받지 못했습니다</b> — '
                         f'{e(status.get("detail") or "")}. 다음 회고 재시도에서 다시 받습니다.</div>')
            why = "투자자별 수급은 받지 못해 뺐습니다"
        else:
            why = "투자자별 수급은 아직 집계 전이라 뺐습니다"
        if seen:
            parts.append(sub("그날 함께 관찰된 것",
                             ' <span style="color:#6b7178;font-size:12px">— 매매 이유를 단정하지 않습니다 · '
                             f'{why}</span>'))
            parts.append('<ul style="margin:0 0 6px;padding-left:20px;font-size:13px;color:#4a4f55">'
                         + "".join(f"<li>{e(x)}</li>" for x in seen) + "</ul>")

    # 3. 흐름이 바뀐 시각과 그 전후의 뉴스(공시·관련 헤드라인 포함)
    parts.append(sub("흐름이 바뀐 시각과 그 전후의 뉴스"))
    if r.get("intraday_note"):
        parts.append(f'<div style="font-size:13px;color:#a8322a">{e(r["intraday_note"])}</div>')
    # 차트(2026-10-02): 장중 경로가 저장된 회고는 세로 시간축 그림을 먼저 보이고, 글 목록은 접어 둔다.
    # 경로가 없는 옛 회고나 5분봉을 못 받은 날은 그림 없이 예전처럼 글 목록만 보인다.
    timeline = review_timeline_html(r)
    listing = []
    if r.get("overnight_news") is not None:
        listing.append('<div style="font-size:13px;margin:6px 0 2px"><b>밤사이 (전일 15:30 ~ 09:00)</b> → 갭 ' + _rv_pct(s["gap"]) + "</div>")
        listing.append(_rv_news_list(r["overnight_news"]))
    for ev in r["events"]:
        listing.append(f'<div style="font-size:13px;margin:8px 0 2px"><b>{_rv_hhmm(ev["time"])}</b> · 봉 {_rv_pct(ev["ret"])} '
                       f'(σ의 {ev["z"]}배, 거래량 {ev["volume_ratio"]}배) · '
                       f'시가 대비 {_rv_pct(ev["cum_before"])} → {_rv_pct(ev["cum_after"])}</div>')
        listing.append(_rv_news_list(ev.get("news", [])))
    if timeline and listing:
        parts.append(timeline + '<details style="margin:2px 0 8px"><summary style="cursor:pointer;font-size:12px;'
                     'color:#7a8797">시점별 뉴스 전체 목록</summary>' + "".join(listing) + '</details>')
    else:
        parts.append(timeline + "".join(listing))
    if r.get("turning_point"):
        tp = r["turning_point"]
        parts.append(f'<div style="font-size:12px;color:#6b7178;margin:6px 0">경로: 시가 대비 고점 {_rv_pct(tp["high"])}({_rv_hhmm(tp["high_time"])}) · '
                     f'저점 {_rv_pct(tp["low"])}({_rv_hhmm(tp["low_time"])}) — {e(tp["pattern"])}</div>')
    if r.get("disclosures") is not None:
        if r["disclosures"]:
            parts.append('<div style="font-size:13px;margin:10px 0 2px"><b>오늘 공시(DART)</b></div><ul style="margin:0;padding-left:20px;font-size:13px">'
                         + "".join(f'<li><a href="{e(d["url"])}" style="color:#1a5490">{e(d["report_nm"])}</a></li>' for d in r["disclosures"]) + "</ul>")
        else:
            parts.append('<div style="font-size:12px;color:#6b7178;margin:6px 0">오늘 공시(DART): 없음</div>')
    elif r.get("disclosure_note"):
        parts.append(f'<div style="font-size:12px;color:#6b7178;margin:6px 0">{e(r["disclosure_note"])}</div>')
    if r.get("top_news"):
        parts.append('<div style="font-size:13px;margin:10px 0 2px"><b>오늘의 관련 헤드라인 (관련도 순)</b></div>' + _rv_news_list(r["top_news"]))

    # 4. 오늘 장 — 숫자 표와 흐름의 성격
    rows = [("전일 종가 → 시가 (갭)", _rv_pct(s["gap"])), ("시가 → 종가 (세션)", _rv_pct(s["session"])),
            ("전일 종가 → 종가", _rv_pct(s["c2c"])),
            ("고가 / 저가 (시가 대비)", f'{_rv_pct(s.get("high_vs_open"))} / {_rv_pct(s.get("low_vs_open"))}'),
            ("거래량 (20일 평균 대비)", f'{float(s["volume_ratio"]):.2f}배' if _rv_finite(s.get("volume_ratio")) else "—"),
            (f'KOSPI / {e(r["peer_name"])}', f'{_rv_pct(s.get("kospi_c2c"))} / {_rv_pct(s.get("peer_c2c"))}'),
            ("원/달러", _rv_pct(s.get("usdkrw_chg"))),
            (("휴장 기간 미국 누적(" + str(int(s["us_nights"])) + "거래일) SOX / 나스닥 / 마이크론")
             if _rv_finite(s.get("us_nights")) and float(s["us_nights"]) > 1 else "전날 밤 SOX / 나스닥 / 마이크론",
             f'{_rv_pct(s.get("sox_ret"))} / {_rv_pct(s.get("nasdaq_ret"))} / {_rv_pct(s.get("micron_ret"))}')]
    if _rv_finite(s.get("ma5")) and _rv_finite(s.get("ma20")):
        close = float(s["close"])
        rows.append(("종가의 5일선 / 20일선 대비",
                     f'{"위" if close >= float(s["ma5"]) else "아래"} ({close / float(s["ma5"]) - 1:+.2%}) / '
                     f'{"위" if close >= float(s["ma20"]) else "아래"} ({close / float(s["ma20"]) - 1:+.2%})'))
    if _rv_finite(s.get("high20")) and _rv_finite(s.get("high252")):
        rows.append(("20일 고점 / 52주 고점 대비",
                     f'{float(s["close"]) / float(s["high20"]) - 1:+.2%} / {float(s["close"]) / float(s["high252"]) - 1:+.2%}'))
    if _rv_finite(s.get("range")):
        rows.insert(4, (f'장중 변동폭 저점→고점 · 이 종목 / {e(r["peer_name"])}',
                        f'{_rv_pct(s["range"])} / {_rv_pct(s.get("peer_range"))}'))
    if r.get("flows") and not story:
        rows.append(("외국인 / 기관 순매수", e(r["flows"])))
    body = "".join(f'<tr><td {_RV_TD}>{e(k)}</td><td {_RV_TDR}>{v}</td></tr>' for k, v in rows)
    parts.append(sub("오늘 장"))
    parts.append('<div style="overflow-x:auto"><table style="width:100%;min-width:420px;border-collapse:collapse;font-size:13px;border:1px solid #e5e5e5">'
                 f'<tr><th {_RV_TH}>항목</th><th {_RV_TH}>값</th></tr>{body}</table></div>')
    c = r["classification"]
    badge = " · ".join(f'<b>{e(l)}</b>' for l in c["labels"])
    parts.append(f'<div style="margin:12px 0 4px;font-size:14px">흐름의 성격: {badge}</div>'
                 '<ul style="margin:0 0 10px;padding-left:20px;font-size:13px;color:#4a4f55">'
                 + "".join(f"<li>{e(x)}</li>" for x in c["reasons"]) + "</ul>")

    # 5. 아침 예측과 비교
    parts.append(sub("아침 예측과 비교"))
    if r["forecasts"]:
        for f in r["forecasts"]:
            color = "#1a7f37" if f["hit"] else "#a8322a"
            band = f'±{float(f["band"]) * 100:.2f}%' if _rv_finite(f.get("band")) else "—"
            parts.append(f'<div style="font-size:13px;margin:4px 0 8px;padding:8px 12px;border-left:3px solid {color};background:#fafafa">'
                         f'<b>{e(str(f["model"]))}</b> · {e(f["verdict"])}<br>'
                         f'<span style="color:#6b7178">{e(f["legs"])} · 보합 밴드 {band} · {e(f["where"])}</span></div>')
        if r.get("price_check"):
            parts.append(f'<div style="font-size:12px;color:#6b7178;margin:2px 0 8px">{e(r["price_check"])}</div>')
    else:
        parts.append('<div style="font-size:13px;color:#6b7178">오늘 예측일의 아침 예측 기록이 원장에 없습니다.</div>')

    # 6. 다가오는 주요 일정(있을 때만) + 실적 발표일 반응 통계(발표가 가까울 때만)
    parts.append(review_next_events_html(context, next(counter)))
    parts.append(earnings_reactions_html(r.get("earnings_reactions"), r.get("name") or ""))
    parts.append('<div style="margin:12px 0 0;padding:10px 14px;background:#fff4e5;border:1px solid #f0c58a;border-radius:6px;'
                 f'font-size:12px;color:#7a4b00">{e(REVIEW_DISCLAIMER)}</div>')
    parts.append(REVIEW_END)
    return "".join(parts)


# 회고(시간대별 뉴스)는 '오늘의 장 예측' 탭 맨 끝에 둔다(2026-09-28 요청). 원장 절 뒤에 두면 '예측 성적' 탭 안으로
# 들어가 첫 화면에서 찾을 수 없었다. 탭이 없는 페이지(탭 구조 검사에 걸려 모든 절을 펼친 경우)는 예전처럼 원장 절 뒤.
_FIRST_TAB_OPEN = '<section class="rtab-panel" id="rtab-0">'
_SECOND_TAB_OPEN = '<section class="rtab-panel" id="rtab-1">'


# ---- 장 회고 탭(2026-09-30 요청) ----------------------------------------------------------------
# 회고는 '오늘의 장 예측' 탭 끝에 있었다. 왼쪽 메뉴에 '오늘의 장 회고' 탭을 따로 두고(오늘의 장 예측 바로 다음),
# 최근 거래일 3~4일을 날짜 단추로 골라 볼 수 있게 한다. 장이 끝나기 전에는 직전 거래일 회고가 보이므로
# 그 사실을 맨 위에 적는다(브라우저 시각 기준 — 평일·시각만 보고 공휴일은 모른다).
REVIEW_TAB_ID, REVIEW_TAB_LABEL, REVIEW_TAB_DAYS = "rtab-review", "오늘의 장 회고", 4
_WEEKDAYS_KO = "월화수목금토일"


def _review_body(review):
    """review_section_html 에서 표시·제목(h3)을 뺀 본문. 탭 나누기가 h3 로 절을 자르므로 h4 로 낮춘다."""
    import re
    body = review_section_html(review).replace(REVIEW_START, "").replace(REVIEW_END, "")
    # 제목은 빼고 날짜만 보인다(2026-09-30 요청: '1.1 장 회고 — 날짜'처럼 번호까지 붙었다). 제목 태그가
    # 아니라 일반 글자로 둬서 탭 번호 매기기에도 걸리지 않는다.
    day = str(review.get("session_date", ""))[:10]
    try:
        stamp = pd.Timestamp(day)
        label = f"{stamp:%Y-%m-%d} ({_WEEKDAYS_KO[stamp.weekday()]})"
    except (TypeError, ValueError):
        label = day
    date_line = f'<div style="font-size:15px;font-weight:700;margin:14px 0 8px">{label}</div>'
    return re.sub(r"<h3\b[^>]*>.*?</h3>", lambda _m: date_line, body, count=1, flags=re.S)


def review_tab_html(reviews, days=REVIEW_TAB_DAYS):
    """장 회고 탭의 내용. reviews 는 회고 기록(dict) 목록 — 날짜가 겹치면 뒤의 것이 이긴다. 비면 빈 문자열."""
    from html import escape as e
    latest = {}
    for review in reviews or []:
        if review and review.get("session_date"):
            latest[str(review["session_date"])[:10]] = review
    dates = sorted(latest, reverse=True)[:days]
    if not dates:
        return ""

    def label(day):
        stamp = pd.Timestamp(day)
        return f"{stamp.month}/{stamp.day}({_WEEKDAYS_KO[stamp.weekday()]})"

    pill = ("border:1px solid #b9d3ec;background:#f0f6fc;color:#1a5490;border-radius:999px;padding:7px 14px;"
            "font-size:13px;cursor:pointer;font-family:inherit")
    buttons = "".join(
        f'<button type="button" data-review-day="{day}" aria-pressed="{"true" if i == 0 else "false"}" '
        f'style="{pill}">{label(day)}</button>' for i, day in enumerate(dates))
    panels = "".join(
        f'<div data-review-panel="{day}"{"" if i == 0 else " hidden"}>{_review_body(latest[day])}</div>'
        for i, day in enumerate(dates))
    script = (
        "<script>(function(){var box=document.getElementById('review-days');if(!box)return;"
        "var latest=box.getAttribute('data-latest'),ll=box.getAttribute('data-latest-label');var bs=box.querySelectorAll('[data-review-day]'),"
        "ps=box.querySelectorAll('[data-review-panel]');"
        "function pick(d){for(var i=0;i<bs.length;i++){var on=bs[i].getAttribute('data-review-day')===d;"
        "bs[i].setAttribute('aria-pressed',on?'true':'false');bs[i].style.background=on?'#1a5490':'#f0f6fc';"
        "bs[i].style.color=on?'#fff':'#1a5490';bs[i].style.fontWeight=on?'700':'400';}"
        "for(var j=0;j<ps.length;j++){ps[j].hidden=ps[j].getAttribute('data-review-panel')!==d;}}"
        "for(var k=0;k<bs.length;k++){bs[k].addEventListener('click',function(){pick(this.getAttribute('data-review-day'));});}"
        "pick(latest);"
        # 한국 시각으로 오늘이 최신 회고보다 뒤면: 주말은 휴장, 15:30 전은 마감 전, 그 뒤는 회고 준비 중.
        "var now=new Date(Date.now()+9*3600*1000),today=now.toISOString().slice(0,10),"
        "wd=now.getUTCDay(),mins=now.getUTCHours()*60+now.getUTCMinutes(),msg='';"
        "if(today>latest){if(wd===0||wd===6){msg='오늘은 휴장일입니다. 가장 최근 회고는 '+ll+'입니다.';}"
        "else if(mins<15*60+30){msg='아직 오늘 장이 마감되지 않았습니다. 가장 최근 회고는 '+ll+'이며, 오늘 회고는 장 마감 뒤(16:10 무렵) 올라옵니다.';}"
        "else{msg='오늘 장은 마감됐고 회고를 만드는 중입니다(보통 16:10 무렵). 가장 최근 회고는 '+ll+'입니다.';}}"
        "var st=document.getElementById('review-status');if(st){st.textContent=msg;st.hidden=!msg;}"
        "})();</script>")
    return (REVIEW_START
            + '<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
            + f'{REVIEW_TAB_LABEL}</h3>'
            # 제목 바로 아래 생성 시각(2026-09-30): 가장 최근 회고를 만든 시각.
            + (f'<div class="gen-stamp" data-for="{REVIEW_TAB_ID}" style="font-size:12px;color:#6b7178;margin:-4px 0 10px">'
               f'생성 {e(str(latest[dates[0]].get("generated_at")))}</div>' if latest[dates[0]].get("generated_at") else "")
            + '<div id="review-status" hidden style="background:#fdf6e3;border-left:4px solid #c79a2b;padding:9px 12px;'
            + 'font-size:13px;margin:6px 0 10px"></div>'
            + f'<div id="review-days" data-latest="{e(dates[0])}" data-latest-label="{e(label(dates[0]))}">'
            + f'<div role="group" aria-label="회고 날짜" style="display:flex;flex-wrap:wrap;gap:8px;margin:4px 0 12px">{buttons}</div>'
            + panels + '</div>' + script + REVIEW_END)


_RTABS_NAV = '<nav class="rtabs"'


# ---- 탭마다 생성 시각(2026-09-30 요청) --------------------------------------------------------------------
# 각 탭의 제목(첫 h3) 바로 아래, 실제 내용이 시작하기 전에 '생성 2026-09-30 15:04 KST' 한 줄을 둔다. 탭마다
# 갱신하는 주체와 시각이 다르다: 노트북(모든 탭), 장 회고(16:10~), 장기 전망 갱신(오전), 채점 갱신(09:37·16:10).
# 같은 탭을 다시 찍으면 앞의 줄을 바꾼다.
_STAMP_RE_TEMPLATE = r'<div class="gen-stamp" data-for="{panel}"[^>]*>.*?</div>'


def kst_stamp(now=None, verb="생성"):
    """'생성 2026-09-30 15:04 KST'. now 는 tz 가 있거나 없는 시각(없으면 KST 로 본다)."""
    stamp = pd.Timestamp.now(tz="Asia/Seoul") if now is None else pd.Timestamp(now)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("Asia/Seoul")
    stamp = stamp.tz_convert("Asia/Seoul")
    return f"{verb} {stamp:%Y-%m-%d %H:%M} KST"


def stamp_panel(page, panel_id, text):
    """탭 panel_id 의 첫 제목(h3) 바로 뒤에 생성 시각 줄을 넣거나 바꾼다. 탭이 없으면 그대로."""
    import re
    from html import escape
    opener = f'<section class="rtab-panel" id="{panel_id}">'
    start = page.find(opener)
    if start < 0:
        return page
    end = _section_close(page, start)
    inner = page[start:end]
    line = (f'<div class="gen-stamp" data-for="{panel_id}" style="font-size:12px;color:#6b7178;margin:-4px 0 10px">'
            f'{escape(text)}</div>')
    inner, count = re.subn(_STAMP_RE_TEMPLATE.format(panel=re.escape(panel_id)), line, inner, count=1, flags=re.S)
    if not count:
        heading = re.search(r"<h3\b[^>]*>.*?</h3>", inner, re.S)
        if not heading:
            return page
        inner = inner[:heading.end()] + line + inner[heading.end():]
    return page[:start] + inner + page[end:]


def _panel_inner(page, panel_id):
    """탭 안쪽 HTML(생성 시각 줄 제외). 탭이 없으면 None."""
    import re
    opener = f'<section class="rtab-panel" id="{panel_id}">'
    start = page.find(opener)
    if start < 0:
        return None
    inner = page[start:_section_close(page, start)]
    return re.sub(r'<div class="gen-stamp"[^>]*>.*?</div>', '', inner, flags=re.S)


def stamp_changed_panels(before, after, text):
    """after 에서 내용이 before 와 달라진 탭에만 시각 줄을 찍는다(부분 갱신용: '갱신 … KST')."""
    import re
    for panel_id in re.findall(r'<section class="rtab-panel" id="([\w-]+)">', after):
        if _panel_inner(before or "", panel_id) != _panel_inner(after, panel_id):
            after = stamp_panel(after, panel_id, text)
    return after


def stamp_all_panels(page, text, skip=()):
    """페이지의 모든 탭에 같은 생성 시각을 찍는다(skip 에 든 탭은 건너뜀)."""
    import re
    for panel_id in re.findall(r'<section class="rtab-panel" id="([\w-]+)">', page):
        if panel_id not in skip:
            page = stamp_panel(page, panel_id, text)
    return page


def insert_review_section(page, section):
    """회고 절을 넣는다. 이미 있으면 교체.

    왼쪽 메뉴(탭)가 있는 페이지는 '오늘의 장 회고' 탭(오늘의 장 예측 바로 다음)에 넣는다 — 탭이 없으면 메뉴 단추와
    칸을 만든다. 메뉴가 없는 옛 페이지는 원장 절 뒤, 그것도 없으면 body 끝.
    """
    start, end = page.find(REVIEW_START), page.find(REVIEW_END)
    if start >= 0 and end > start:
        page = page[:start] + page[end + len(REVIEW_END):]
    nav = page.find(_RTABS_NAV)
    first_panel = page.find(_FIRST_TAB_OPEN)
    if nav >= 0 and first_panel >= 0:
        panel_open = f'<section class="rtab-panel" id="{REVIEW_TAB_ID}">'
        where = page.find(panel_open)
        if where < 0:
            # 메뉴: 첫 단추(오늘의 장 예측) 바로 뒤에 새 단추
            first_link_end = page.find("</a>", nav)
            link = f'<a href="#{REVIEW_TAB_ID}" aria-selected="false">{REVIEW_TAB_LABEL}</a>'
            page = page[:first_link_end + 4] + link + page[first_link_end + 4:]
            # 칸: 첫 칸 바로 뒤에 새 칸(빈 칸을 먼저 만들고 아래에서 채운다)
            first_panel = page.find(_FIRST_TAB_OPEN)
            close = _section_close(page, first_panel)
            page = page[:close] + panel_open + "</section>" + page[close:]
            where = page.find(panel_open)
        inner = where + len(panel_open)
        return page[:inner] + section + page[inner:]
    anchor = page.find(REVIEW_LEDGER_END)
    if anchor >= 0:
        cut = anchor + len(REVIEW_LEDGER_END)
        return page[:cut] + section + page[cut:]
    body_end = page.rfind("</body>")
    return page[:body_end] + section + page[body_end:] if body_end >= 0 else page + section


def _section_close(page, open_at):
    """open_at 에서 열린 <section> 의 닫는 태그 바로 뒤 위치. 안에 든 section 도 센다."""
    import re
    depth, i = 0, open_at
    for match in re.finditer(r"<(/?)section\b[^>]*>", page[open_at:]):
        depth += -1 if match.group(1) else 1
        if depth == 0:
            return open_at + match.end()
    return len(page)


# ---- 이력 보관본 합치기 (2026-09-24) ------------------------------------------------------------
def merge_history_csv(existing, new):
    """이력 보관본(macro_history/*.csv)을 덮어쓰지 않고 첫 열(날짜·월)로 합친다.

    같은 보관본을 여러 실행이 서로 다른 기간으로 받아 통째로 덮어써, 매 실행 오래된 이력이 지워졌다가 되살아났다
    (2026-09-21~: 선행지수 24행·뉴스심리 3,468행·관세청 122행). 합치면 이력이 줄지 않고, 바뀐 것이 없으면 결과가
    기존과 글자 하나까지 같아 커밋이 생기지 않는다 — 값은 문자열 그대로 옮기고 정렬·따옴표·줄바꿈("\n")도 pandas
    to_csv 와 같다. 같은 날짜는 new 의 값이 이긴다(같은 원천이라 보통 같다). new 에만 있는 열은 붙인다.
    합칠 수 없으면(첫 열 이름이 다르거나 첫 열에 중복이 있으면) 예전처럼 new 를 돌려준다. new 가 비었으면 existing.
    """
    import csv
    import io
    if existing is None or not existing.strip():
        return new
    if new is None or not new.strip():
        return existing
    old_rows = [r for r in csv.reader(io.StringIO(existing)) if r]
    new_rows = [r for r in csv.reader(io.StringIO(new)) if r]
    if len(new_rows) < 2:
        return existing
    old_head, new_head = old_rows[0], new_rows[0]
    if old_head[0] != new_head[0]:
        return new

    def keyed(head, rows):
        keys = [r[0] for r in rows[1:]]
        if len(set(keys)) != len(keys):
            return None
        return {r[0]: dict(zip(head, r)) for r in rows[1:]}

    old_map, new_map = keyed(old_head, old_rows), keyed(new_head, new_rows)
    if old_map is None or new_map is None:
        return new
    columns = old_head + [c for c in new_head if c not in old_head]
    for key, record in new_map.items():
        old_map[key] = {**old_map.get(key, {}), **record}
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    for key in sorted(old_map):
        writer.writerow([old_map[key].get(c, "") for c in columns])
    return out.getvalue()

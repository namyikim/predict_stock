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
                 f'font-weight:600;color:{tone}">{escape(str(card["value"]))}</td>'
                 f'<td style="padding:8px 11px;border-top:1px solid #eee;color:#6b7178;font-size:12px">'
                 f'{escape(card.get("detail", ""))}</td>'
                 f'<td style="padding:8px 11px;border-top:1px solid #eee;color:#8a9199;font-size:11px;'
                 f'white-space:nowrap">{escape(card.get("source", ""))}</td></tr>')
    unknown_html = ""
    if unknowns:
        unknown_html = ('<div style="margin-top:10px;padding:10px 14px;background:#fdf8ec;'
                        'border-left:4px solid #c8952a;border-radius:0 5px 5px 0;font-size:12px">'
                        '<b>이 보고서가 답하지 못하는 것</b><ul style="margin:6px 0 0;padding-left:18px">'
                        + "".join(f"<li>{escape(u)}</li>" for u in unknowns) + "</ul></div>")
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
            + (f'<div style="font-size:11px;color:#8a9199;margin-top:6px">{escape(caveat)}</div>'
               if caveat else ""))


def easy_summary_html(*, name, prediction_date, data_date, summary, open_forecast,
                      price_forecasts, review=None, longterm=None, earnings=None,
                      target_mode="close_to_close", record_forecast=True,
                      macro_active=True, nsi_active=True):
    """Summarize already-computed results; never infer news causes or bypass signal gates.

    This is a generation-time snapshot. Intraday ledger refreshes remain separate and
    must not make the original forecast look as though it used later observations.
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
    probabilities = [number(live.get(k)) for k in ("p_down", "p_flat", "p_up")]
    basis = "당일 시초가 대비" if target_mode == "open_to_close" else "전일 종가 대비"
    direction = "방향을 판단하기 어렵습니다."
    if (all(p is not None and 0 <= p <= 1 for p in probabilities)
            and abs(sum(probabilities) - 1) < .01):
        best = max(probabilities)
        if sum(abs(p - best) < 1e-9 for p in probabilities) == 1:
            label = ("내림", "큰 변화 없음", "오름")[probabilities.index(best)]
            direction = (f"{basis} 종가 방향은 ‘{label}’ 쪽의 계산상 가능성이 가장 높습니다 "
                         f"({best:.1%}). 이 확률은 실제 적중률이 아닙니다.")
    sections.append(("전체 결론", f"{name} · {date_text(prediction_date)}: {direction}"))
    if not record_forecast:
        sections.append(("예측 상태", "이번 실행의 예측은 원장에 기록되지 않는 참고값입니다. "
                         "실제 성적은 별도로 저장된 장 시작 전 예측으로 평가합니다."))

    def price_text(row, field):
        point = number(row.get(field))
        if row.get("signal") != "있음" or point is None or point <= 0:
            return "예측하기 어렵습니다(검증 근거 부족)."
        return f"약 {point:,.0f}원. 확정 가격이 아닌 모델 예상입니다."

    sections.append(("시초가예측 — 장이 시작할 때의 가격",
                     f"{date_text(open_forecast.get('target_date', prediction_date))}: "
                     + price_text(open_forecast, "predicted_open")))
    close_parts = []
    by_days = {r.get("trading_days"): r for r in price_forecasts}
    for days, label in ((1, "다음 거래일"), (5, "5거래일 뒤"), (20, "20거래일 뒤")):
        row = by_days.get(days, {})
        stamp = f" ({date_text(row['target_date'])})" if row.get("target_date") is not None else ""
        close_parts.append(f"{label}{stamp}: {price_text(row, 'predicted_close')}")
    sections.append(("종가예측 — 장이 끝날 때의 가격", " / ".join(close_parts)))

    longterm = mapping(longterm)
    long_parts = []
    for months in ("3", "6", "12"):
        forecast = mapping(mapping(longterm.get("forecast")).get(months))
        evaluation = mapping(mapping(longterm.get("evaluation")).get(months))
        point = number(forecast.get("point"))
        if evaluation.get("beats_zero") and point is not None:
            # Monthly model targets log returns; match the detail report's ordinary returns.
            long_parts.append(f"{months}개월 뒤 주가 변화 {np.expm1(point):+.1%} 예상")
        else:
            long_parts.append(f"{months}개월: 판단 근거 부족")
    long_stamp = f"{date_text(longterm.get('as_of'))} 기준. " if longterm else "장기 자료 미확인. "
    sections.append(("중장기 전망", long_stamp + " / ".join(long_parts)
                     + ". 장기 전망은 매일 계산하는 단기 전망과 기준일이 다릅니다."))

    earnings = mapping(earnings)
    earnings_parts = []
    for item in (earnings, mapping(earnings.get("next_quarter"))):
        if not item:
            continue
        quarter = item.get("quarter", "분기 미확인")
        point = number(item.get("point"))
        if (mapping(item.get("evaluation")).get("beats_baselines") and point is not None
                and not item.get("no_point_reason")):
            earnings_parts.append(f"{quarter} 영업이익 약 {point / 1e12:,.1f}조 원 예상")
        else:
            earnings_parts.append(f"{quarter} 영업이익은 예측하기 어렵습니다")
    earnings_text = " / ".join(earnings_parts) if earnings_parts else "실적 추정 자료를 확인하지 못했습니다"
    if earnings:
        earnings_text += ". 회사 발표나 증권사 전망 평균이 아닌 자체 모델의 추정입니다."
        months_used = number(earnings.get("months_used"))
        if months_used is not None:
            earnings_text += f" 분기 3개월 중 {months_used:.0f}개월 자료 반영."
        if earnings.get("exports_last_month"):
            earnings_text += f" 수출 자료 기준 {date_text(earnings['exports_last_month'])}."
    sections.append(("회사 실적 — 본업으로 번 이익", earnings_text))

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
    confidence += " 위 성적은 보고서 생성 시점 기준이며, 이후 채점 결과는 아래 ‘예측 vs 실제’에서 확인하세요."
    sections.append(("얼마나 믿을 수 있나요?", confidence))
    warnings = ["‘예측하기 어렵다’는 가격이 그대로라는 뜻은 아닙니다",
                "갑작스러운 뉴스나 시장 변화로 예측이 빗나갈 수 있습니다"]
    if not macro_active:
        warnings.append("단기 예측에 월별 경제지표가 빠져 있습니다")
    if not nsi_active:
        warnings.append("단기 예측에 뉴스 분위기 지표가 빠져 있습니다")
    sections.append(("주의할 점", ". ".join(warnings) + ". 매수·매도 권유가 아닌 참고 자료입니다."))
    body = "".join(f'<li style="margin:9px 0"><b>{escape(label)}</b><br>{escape(text)}</li>'
                   for label, text in sections)
    return ('<section id="easy-summary" aria-label="한눈에 보는 쉬운 요약" '
            'style="background:#f0f6fc;border:1px solid #cedff0;border-radius:8px;padding:16px 20px;margin:0 0 20px">'
            '<h3 style="margin:0 0 6px;font-size:19px">한눈에 보는 쉬운 요약</h3>'
            f'<div style="font-size:12px;color:#586575">단기 데이터 기준 {escape(date_text(data_date))} · '
            '보고서 생성 시점의 계산 결과를 쉬운 말로 풀었습니다.</div>'
            f'<ul style="font-size:14px;padding-left:18px;margin:8px 0 0">{body}</ul></section>')


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


def calibrate_price_forecast(y, prediction, sigma, dates, horizon, ci_function, coverage=.8):
    """OOF를 보정 50% / 신호 선택 25% / 최종 평가 25%로 나누고 경계 라벨을 제거한다.

    최종 평가 정답은 slope, 신호 선택, 구간 폭 결정에 사용하지 않는다.
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
    q = float(np.quantile(residual, coverage))
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
        "band_coverage_realized": float(np.mean(test_error <= q * sigma[evaluation])),
        "band_halfwidth_mean": float(np.mean(q * sigma[evaluation])),
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
        if pd.notna(start) and pd.notna(created):
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
    return result


def daily_comparison(evaluated):
    """동일 날짜/모델/설정의 최초 사전 예측만 선택하여 재실행으로 표본이 늘지 않게 한다."""
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
    return eligible.sort_values("created_at_utc").drop_duplicates(keys, keep="first")


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
                         min_n=10):
    """아침 예측 vs 저녁 예측 — 밤사이 미국 시장 정보가 실제로 얼마나 기여하나.

    같은 예측일을 두 시점에서 예측한다. 아침(06:22 KST)은 미국 장 마감 후라 갭 정보가 있고,
    저녁(18:22 KST)은 미국 장이 열리기도 전이라 없다. 두 적중률의 차이가 그 정보의 값이다.
    이 저장소가 말해 온 '갭 AUC 0.80, 세션 AUC 0.50'의 직접 검증이다.
    """
    if daily is None or len(daily) == 0 or "model" not in daily:
        return ""
    frame = daily[(daily["kind"] == "direction") & (daily["status"] == "scored")].copy()
    if "is_prospective" in frame:
        frame = frame[frame["is_prospective"].astype(str).str.lower().isin(("true", "1", "yes"))]
    if frame.empty:
        return ""
    pairs = []
    for label, model in (("아침 (갭 정보 있음)", headline_model), ("저녁 (갭 정보 없음)", evening_model)):
        sub = frame[frame["model"] == model]
        if sub.empty:
            continue
        common = set(frame[frame["model"] == evening_model]["target_date"]) & \
                 set(frame[frame["model"] == headline_model]["target_date"])
        matched = sub[sub["target_date"].isin(common)] if common else sub.iloc[0:0]
        pairs.append({"label": label, "n": len(matched),
                      "hit": float(matched["direction_correct"].mean()) if len(matched) else None})
    if len(pairs) < 2 or not any(p["n"] for p in pairs):
        return ""
    rows = ""
    for pair in pairs:
        value = "—" if pair["hit"] is None or pair["n"] < min_n else f'{pair["hit"]:.0%}'
        rows += (f'<tr><td style="padding:7px 11px;border-top:1px solid #eee">{pair["label"]}</td>'
                 f'<td style="padding:7px 11px;border-top:1px solid #eee;text-align:right">{value}</td>'
                 f'<td style="padding:7px 11px;border-top:1px solid #eee;text-align:right;color:#8a9199">'
                 f'n={pair["n"]}</td></tr>')
    note = ("같은 예측일만 짝지어 비교합니다. 표본 10일 미만은 — 로 둡니다."
            if min(p["n"] for p in pairs) < min_n else
            "아침이 높으면 밤사이 미국 시장 정보가 실제로 기여한다는 뜻입니다.")
    return ('<div style="font-size:12px;color:#6b7178;margin:14px 0 4px">밤사이 정보의 값 — '
            '같은 날을 아침·저녁 두 시점에서 예측해 각각 채점한 결과</div>'
            '<div style="overflow-x:auto"><table style="width:100%;min-width:380px;border-collapse:collapse;'
            'font-size:12px;border:1px solid #e5e5e5"><tr style="background:#fafafa;font-size:11px;color:#6b7178">'
            '<th style="padding:8px 11px;text-align:left">예측 시점</th>'
            '<th style="padding:8px 11px;text-align:right">방향 적중률</th>'
            '<th style="padding:8px 11px;text-align:right">표본</th></tr>'
            f'{rows}</table></div>'
            f'<div style="font-size:11px;color:#8a9199;margin-top:4px">{note}</div>')


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
                        "return_error", "interval_hit", "oof_slope", "run_id"] if c in scored]
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
        for kind in ("open", "price"):
            # 관찰용 후보(모델명 "Candidate …")는 원장에만 있고 헤드라인 성적에 섞지 않는다(M07).
            headline = recent[(recent["kind"] == kind) & ~recent["model"].astype(str).str.startswith("Candidate")]
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
    d = latest[(latest["kind"] == "direction") & (latest["model"] == ensemble_name)]
    o = latest[latest["kind"] == "open"]
    p1 = latest[(latest["kind"] == "price") & (latest["horizon_days"] == 1)]
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
        rows += _row("종가 방향", f'{r["prediction"]} (상승 {r["p_up"]:.0%}·보합 {r["p_flat"]:.0%}·하락 {r["p_down"]:.0%})',
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
            label, detail = "종가 방향", (f'적중률 {r["hit_rate"]:.0%} (보합 비중 {r["flat_share"]:.0%}) · '
                                        f'log loss {r["mean_log_loss"]:.3f} vs 빈도기준 {r["prior_log_loss"]:.3f}')
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
                cards.append({"label": "종가 방향", "metric": "적중률", "value": r["hit_rate"],
                              "baseline": r.get("prior_hit_rate"), "n": int(r["n"])})
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
    rtable = (gauges + event_html + (review.get("overnight_html") or "")
              + '<details style="margin-top:6px"><summary style="font-size:12px;color:#6b7178;cursor:pointer">'
              '자세한 수치 보기</summary>'
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

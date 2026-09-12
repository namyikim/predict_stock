# -*- coding: utf-8 -*-
"""R09 1·2단계 — 선행지수가 앞으로 2~3개월 어디로 갈지 낼 수 있는가.

계획: guides/research-candidates-plan.md R09.

1단계(진단): 분산비와 자기상관으로 **임의보행인지 먼저 잰다**. 임의보행이면 무엇을 얹어도
기준선을 못 이기므로 거기서 멈춘다. 분산비는 전망 모형이 아니라 이 판단을 위한 검정이다.

2단계(전망): 1·2·3개월 앞을 확장 창 워크포워드로 재고 **기준선 둘**과 견준다.
  rw   — 마지막 값 그대로(임의보행)
  drift — 마지막 변화 그대로(관성)
후보는 최소제곱으로 푸는 ARIMA(p,1,0)과 VAR(p)다. 이동평균 항은 새 의존성이 필요해 넣지 않았다.

**개정 경고.** 선행지수는 나중에 값이 바뀌는데 우리는 최신본 한 벌만 갖고 있다. 그래서 이 결과는
그 시점에 알 수 없던 개정을 미리 아는 셈이고 **성적이 부풀려져 있다**. 판본이 쌓이면 다시 잰다.

    python tools/run_cli_forecast.py --out runs/cli_forecast
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEED = 20260912
BOOTSTRAP = 2000
HORIZONS = (1, 2, 3)
MIN_TRAIN = 120           # 워크포워드 첫 학습 구간(개월). 10년이면 여러 국면을 담는다.
MAX_LAG = 6


def load_series(name, path):
    frame = pd.read_csv(path, parse_dates=["month"])
    return frame.set_index("month")["value"].asfreq("MS").dropna().rename(name)


# ---------------------------------------------------------------------------
# 1단계 — 진단
# ---------------------------------------------------------------------------
def variance_ratio(series, q):
    """Lo·MacKinlay 분산비. 1 이면 임의보행, >1 추세, <1 평균회귀.

    z 는 이분산을 허용한 통계량이다(Lo·MacKinlay 1988 의 M2). |z| < 1.96 이면 임의보행을
    버리지 못한다는 뜻이고, 그때는 ARIMA 를 얹어도 기준선을 못 이긴다.
    """
    x = np.asarray(series, dtype=float)
    n = len(x)
    if n < q * 3:
        return {"q": q, "vr": None, "z": None, "reason": "표본 부족"}
    d = np.diff(x)
    mu = d.mean()
    var1 = ((d - mu) ** 2).sum() / (len(d) - 1)
    rolling = np.convolve(d, np.ones(q), mode="valid")          # q 개월 누적 변화
    m = len(rolling)
    varq = ((rolling - q * mu) ** 2).sum() / (m * q * (1 - q / len(d)))
    if var1 <= 0:
        return {"q": q, "vr": None, "z": None, "reason": "변화가 없다"}
    vr = varq / var1
    # 이분산 허용 통계량(Lo·MacKinlay 1988 의 M2). delta(j) 는 분모가 제곱합의 제곱이라
    # 그 자체로 1/n 크기다. 여기에 표본 수를 한 번 더 곱하면 theta 가 n 배로 부풀어
    # z 가 sqrt(n) 만큼 작아진다 — 2026-09-12 실제로 그렇게 써서 임의보행으로 잘못 읽었다.
    theta = 0.0
    den = (((d - mu) ** 2).sum()) ** 2
    for j in range(1, q):
        num = (((d[j:] - mu) ** 2) * ((d[:-j] - mu) ** 2)).sum()
        theta += (2 * (q - j) / q) ** 2 * (num / den)
    z = (vr - 1) / np.sqrt(theta) if theta > 0 else None
    # 등분산 가정 통계량도 함께 낸다. 둘이 크게 다르면 그 차이가 곧 이분산의 크기다.
    homo = np.sqrt(2 * (2 * q - 1) * (q - 1) / (3 * q))
    z_homo = (vr - 1) * np.sqrt(len(d)) / homo if homo > 0 else None
    return {"q": q, "vr": float(vr), "z": None if z is None else float(z),
            "z_homoskedastic": None if z_homo is None else float(z_homo)}


def diagnose(series, max_lag=MAX_LAG):
    changes = series.diff().dropna()
    return {
        "n": int(len(series)), "first": f"{series.index[0]:%Y-%m}", "last": f"{series.index[-1]:%Y-%m}",
        "change_std": float(changes.std()),
        "autocorr": {k: float(changes.autocorr(k)) for k in range(1, max_lag + 1)},
        "variance_ratio": [variance_ratio(series, q) for q in (2, 3, 6, 12)],
    }


# ---------------------------------------------------------------------------
# 2단계 — 전망
# ---------------------------------------------------------------------------
def _ols(design, target):
    """절편을 붙여 최소제곱. 특이하면 None."""
    x = np.column_stack([np.ones(len(design)), design])
    try:
        beta, *_ = np.linalg.lstsq(x, target, rcond=None)
    except np.linalg.LinAlgError:
        return None
    return beta


def forecast_ar(history, horizon, lags):
    """ARIMA(lags,1,0). 차분에 AR 을 얹고 재귀로 horizon 개월 앞까지 민다."""
    d = np.diff(np.asarray(history, dtype=float))
    if len(d) < lags + 12:
        return None
    rows = np.column_stack([d[lags - 1 - i:len(d) - 1 - i] for i in range(lags)])
    beta = _ols(rows, d[lags:])
    if beta is None:
        return None
    recent = list(d[-lags:])
    level = float(history[-1])
    for _ in range(horizon):
        step = beta[0] + sum(beta[1 + i] * recent[-1 - i] for i in range(lags))
        recent.append(step)
        level += step
    return level


def forecast_var(histories, horizon, lags):
    """VAR(lags) 를 차분에 얹는다. 첫 계열이 예측 대상이다."""
    frame = pd.concat(histories, axis=1).dropna()
    if len(frame) < lags + 24:
        return None
    d = frame.diff().dropna().to_numpy(dtype=float)
    k = d.shape[1]
    if len(d) < lags + 12:
        return None
    rows = np.column_stack([d[lags - 1 - i:len(d) - 1 - i, :] for i in range(lags)])
    betas = []
    for j in range(k):
        beta = _ols(rows, d[lags:, j])
        if beta is None:
            return None
        betas.append(beta)
    recent = [d[-1 - i] for i in range(lags)]
    level = float(frame.iloc[-1, 0])
    for _ in range(horizon):
        flat = np.concatenate(recent[:lags])
        step = np.array([b[0] + float(np.dot(b[1:], flat)) for b in betas])
        recent.insert(0, step)
        level += float(step[0])
    return level


def walk_forward(series, partners, horizons=HORIZONS, min_train=MIN_TRAIN, lags=2):
    """확장 창. 시점 t 까지만 보고 t+h 를 맞힌다. 기준선 둘을 같은 자리에서 함께 낸다."""
    values = series.to_numpy(dtype=float)
    rows = []
    for end in range(min_train, len(series)):
        history = values[:end]
        for horizon in horizons:
            if end - 1 + horizon >= len(series):
                continue
            truth = float(values[end - 1 + horizon])
            rw = float(history[-1])
            drift = float(history[-1] + (history[-1] - history[-2]) * horizon)
            ar = forecast_ar(history, horizon, lags)
            var = forecast_var([series.iloc[:end]] + [p.iloc[:end] for p in partners],
                               horizon, lags) if partners else None
            rows.append({"asof": series.index[end - 1], "horizon": horizon, "actual": truth,
                         "rw": rw, "drift": drift, "ar": ar, "var": var})
    return pd.DataFrame(rows)


def paired_ci(errors_a, errors_b, b=BOOTSTRAP, seed=SEED):
    """후보−기준선의 평균절대오차 차이와 95% 구간. 같은 날짜끼리 짝지어 뺀다."""
    a = np.abs(np.asarray(errors_a, dtype=float))
    c = np.abs(np.asarray(errors_b, dtype=float))
    keep = np.isfinite(a) & np.isfinite(c)
    a, c = a[keep], c[keep]
    if len(a) < 10:
        return {"diff": None, "lo": None, "hi": None, "n": int(len(a)), "reason": "표본 부족"}
    delta = a - c
    rng = np.random.default_rng(seed)
    draws = np.array([rng.choice(delta, len(delta), replace=True).mean() for _ in range(b)])
    lo, hi = np.quantile(draws, [0.025, 0.975])
    return {"diff": float(delta.mean()), "lo": float(lo), "hi": float(hi), "n": int(len(delta))}


def evaluate(table):
    rows = []
    for horizon, part in table.groupby("horizon"):
        actual = part["actual"].to_numpy(dtype=float)
        scores = {}
        for name in ("rw", "drift", "ar", "var"):
            if part[name].isna().all():
                continue
            scores[name] = np.abs(part[name].to_numpy(dtype=float) - actual)
        for name, errors in scores.items():
            row = {"horizon": int(horizon), "model": name,
                   "mae": float(np.nanmean(errors)), "n": int(np.isfinite(errors).sum())}
            # 사다리에서 자기보다 앞 칸인 모형 전부와 견준다. 앞 칸과만 견주면 사다리가 중간에서
            # 멈췄을 때 그것이 '못 이겼다'인지 '견줄 수 없었다'인지 구별되지 않는다.
            earlier = LADDER[:LADDER.index(name)] if name in LADDER else ()
            for baseline in earlier:
                if baseline not in scores:
                    continue
                result = paired_ci(part[name].to_numpy(dtype=float) - actual,
                                   part[baseline].to_numpy(dtype=float) - actual)
                row[f"vs_{baseline}"] = result["diff"]
                row[f"vs_{baseline}_lo"] = result["lo"]
                row[f"vs_{baseline}_hi"] = result["hi"]
            rows.append(row)
    return pd.DataFrame(rows)


LADDER = ("rw", "drift", "ar", "var")


def choose_model(scores, horizon, ladder=LADDER):
    """지평마다 어느 모형으로 숫자를 낼지 규칙으로 정한다.

    성적 순위로 고르면 고른 표본에서 성능을 보고하는 셈이라 편향된다(분기 이익 모형과 같은 규칙).
    그래서 순위가 아니라 **사다리**로 올라간다 — 가장 단순한 것에서 시작해, 다음 칸이 지금 칸을
    유의하게 이길 때만(구간 상한 < 0) 한 칸 올린다. 이기지 못하면 거기서 멈춘다.
    """
    current, reason = ladder[0], "기본(가장 단순한 쪽)"
    for nxt in ladder[1:]:
        part = scores[(scores["horizon"] == horizon) & (scores["model"] == nxt)]
        key = f"vs_{current}_hi"
        if part.empty or key not in part.columns or pd.isna(part.iloc[0][key]):
            break                       # 견줄 수 없으면 올리지 않는다
        if part.iloc[0][key] >= 0:
            reason = f"{nxt} 가 {current} 를 이기지 못했다"
            break
        current, reason = nxt, f"{nxt} 가 {current} 를 유의하게 이겼다"
    return current, reason


def forecast_now(series, table, scores, horizons=HORIZONS, partners=(), lags=2):
    """마지막 값 기준으로 앞으로 몇 달을 낸다. 구간은 워크포워드 오차의 실제 분위다.

    이론 분포를 가정하지 않는다. 그 지평에서 그 모형이 실제로 얼마나 빗나갔는지를 그대로 쓴다.
    """
    values = series.to_numpy(dtype=float)
    rows = []
    for horizon in horizons:
        model, reason = choose_model(scores, horizon)
        if model == "drift":
            point = float(values[-1] + (values[-1] - values[-2]) * horizon)
        elif model == "ar":
            point = forecast_ar(values, horizon, lags)
        elif model == "var":
            point = forecast_var([series] + list(partners), horizon, lags)
        else:
            point = float(values[-1])
        if point is None:                       # 뽑히고도 낼 수 없으면 가장 단순한 쪽으로 내린다
            model, reason, point = "rw", f"{model} 을 낼 수 없어 rw 로 내렸다", float(values[-1])
        part = table[table["horizon"] == horizon]
        errors = (part[model] - part["actual"]).dropna().to_numpy(dtype=float)
        lo, hi = (np.quantile(errors, [0.10, 0.90]) if len(errors) >= 20 else (np.nan, np.nan))
        alternatives = {}
        for name, fn in (("ar", lambda: forecast_ar(values, horizon, lags)),
                         ("var", lambda: forecast_var([series] + list(partners), horizon, lags))):
            try:
                alternatives[name] = fn()
            except Exception:
                alternatives[name] = None
        rows.append({"horizon": horizon,
                     "month": f"{series.index[-1] + pd.DateOffset(months=horizon):%Y-%m}",
                     "model": model, "reason": reason, "point": point,
                     "low": point - float(hi) if np.isfinite(hi) else None,
                     "high": point - float(lo) if np.isfinite(lo) else None,
                     "n_errors": int(len(errors)), **alternatives})
    return pd.DataFrame(rows)


def forecast_svg(series, ahead, months=48, width=900, height=340, overlay=None,
                 title="경기선행지수", overlay_name="코스피", start=None):
    """최근 실적과 앞으로 몇 달을 한 그림에. 확정과 전망을 선 모양으로 구분한다.

    구간은 워크포워드 오차의 10~90% 분위다. 점 하나만 보여 주면 '이만큼은 맞다'로 읽히므로
    구간을 늘 함께 그린다.

    overlay 를 주면 오른쪽 축에 겹쳐 그린다. 단위가 전혀 달라(지수 100 근처 vs 코스피 수천)
    한 축에 그리면 한쪽이 납작해진다. 축이 둘이므로 **높이 비교는 뜻이 없고 방향만 본다**는 것을
    그림 안에 적는다.
    """
    recent = series.loc[str(start):] if start else series.tail(months)
    points = [(f"{m:%Y-%m}", float(v)) for m, v in recent.items()]
    future = [(row["month"], float(row["point"]),
               None if pd.isna(row["low"]) else float(row["low"]),
               None if pd.isna(row["high"]) else float(row["high"]))
              for _, row in ahead.iterrows()]
    values = [v for _, v in points] + [v for _, v, _, _ in future]
    values += [x for _, _, lo, hi in future for x in (lo, hi) if x is not None]
    low, high = min(values), max(values)
    pad = (high - low) * 0.18 or 0.5
    low, high = low - pad, high + pad
    left, right, top, bottom = 58, 62, 46, 46
    span = len(points) + len(future) - 1

    def x_of(i):
        return left + (width - left - right) * i / max(span, 1)

    def y_of(v):
        return top + (height - top - bottom) * (high - v) / (high - low)

    history = " ".join(f"{x_of(i):.1f},{y_of(v):.1f}" for i, (_, v) in enumerate(points))
    bridge_x, bridge_y = x_of(len(points) - 1), y_of(points[-1][1])
    forward = f"{bridge_x:.1f},{bridge_y:.1f} " + " ".join(
        f"{x_of(len(points) + i):.1f},{y_of(v):.1f}" for i, (_, v, _, _) in enumerate(future))
    band = ""
    if all(lo is not None for _, _, lo, _ in future):
        upper = [(bridge_x, bridge_y)] + [(x_of(len(points) + i), y_of(hi))
                                          for i, (_, _, _, hi) in enumerate(future)]
        lower = [(x_of(len(points) + i), y_of(lo))
                 for i, (_, _, lo, _) in enumerate(future)][::-1] + [(bridge_x, bridge_y)]
        band = ('<polygon points="'
                + " ".join(f"{x:.1f},{y:.1f}" for x, y in upper + lower)
                + '" fill="#4c78a8" opacity="0.14"/>')
    ticks = "".join(
        f'<line x1="{left}" x2="{width - right}" y1="{y_of(v):.1f}" y2="{y_of(v):.1f}" '
        f'stroke="#eee"/><text x="{left - 8}" y="{y_of(v) + 4:.1f}" text-anchor="end" '
        f'font-size="11" fill="#8a9199">{v:.1f}</text>'
        for v in np.linspace(low + pad / 2, high - pad / 2, 4))
    labels = ""
    step = max(len(points) // 6, 1)
    for i, (month, _) in enumerate(points):
        if i % step == 0:
            labels += (f'<text x="{x_of(i):.1f}" y="{height - bottom + 18}" text-anchor="middle" '
                       f'font-size="10" fill="#8a9199">{month}</text>')
    marks = "".join(
        f'<circle cx="{x_of(len(points) + i):.1f}" cy="{y_of(v):.1f}" r="3.2" fill="none" '
        f'stroke="#c8952a" stroke-width="1.6"/>'
        f'<text x="{x_of(len(points) + i):.1f}" y="{y_of(v) - 9:.1f}" text-anchor="middle" '
        f'font-size="10" fill="#c8952a">{v:.2f}</text>'
        f'<text x="{x_of(len(points) + i):.1f}" y="{height - bottom + 18}" text-anchor="middle" '
        f'font-size="10" fill="#c8952a">{month}</text>'
        for i, (month, v, _, _) in enumerate(future))
    second = ""
    if overlay is not None and len(overlay):
        months_index = {month: i for i, (month, _) in enumerate(points)}
        pairs = [(months_index[f"{m:%Y-%m}"], float(v)) for m, v in overlay.items()
                 if f"{m:%Y-%m}" in months_index]
        if len(pairs) > 2:
            o_low = min(v for _, v in pairs)
            o_high = max(v for _, v in pairs)
            o_pad = (o_high - o_low) * 0.18 or 1.0
            o_low, o_high = o_low - o_pad, o_high + o_pad

            def oy(v):
                return top + (height - top - bottom) * (o_high - v) / (o_high - o_low)

            line = " ".join(f"{x_of(i):.1f},{oy(v):.1f}" for i, v in pairs)
            right_ticks = "".join(
                f'<text x="{width - right + 8}" y="{oy(v) + 4:.1f}" font-size="11" '
                f'fill="#2e7d32">{v:,.0f}</text>'
                for v in np.linspace(o_low + o_pad / 2, o_high - o_pad / 2, 4))
            second = (f'<polyline points="{line}" fill="none" stroke="#2e7d32" '
                      f'stroke-width="1.5" opacity="0.85"/>{right_ticks}')
    legend = (
        f'<text x="{left}" y="{top - 8}" font-size="11" fill="#4c78a8">■ {title}(왼쪽 축)</text>'
        + (f'<text x="{left + 150}" y="{top - 8}" font-size="11" fill="#2e7d32">'
           f'■ {overlay_name}(오른쪽 축)</text>' if second else "")
        + f'<text x="{left + 290}" y="{top - 8}" font-size="11" fill="#c8952a">■ 전망</text>')
    caution = ('<text x="{x}" y="{y}" text-anchor="end" font-size="10" fill="#a5abb2">'
               '축이 둘이라 높이 비교는 뜻이 없습니다 — 방향만 보세요</text>').format(
        x=width - right, y=height - 8) if second else ""
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" xmlns="http://www.w3.org/2000/svg" '
        f'style="max-width:{width}px;font-family:-apple-system,\'Malgun Gothic\',sans-serif">'
        f'<rect width="{width}" height="{height}" fill="#fff"/>'
        f'<text x="{left}" y="20" font-size="13" font-weight="600" fill="#1a1a1a">'
        f'{title} — 확정 {points[0][0]}~{points[-1][0]}, 전망 {future[0][0]}~{future[-1][0]}</text>'
        f'{ticks}{second}{band}'
        f'<polyline points="{history}" fill="none" stroke="#4c78a8" stroke-width="1.8"/>'
        f'<polyline points="{forward}" fill="none" stroke="#c8952a" stroke-width="1.8" '
        f'stroke-dasharray="5 4"/>'
        f'<line x1="{bridge_x:.1f}" x2="{bridge_x:.1f}" y1="{top}" y2="{height - bottom}" '
        f'stroke="#bbb" stroke-dasharray="2 3"/>'
        f'{labels}{marks}{legend}'
        f'<text x="{width - right}" y="20" text-anchor="end" font-size="11" fill="#8a9199">'
        f'점선은 전망 · 음영은 워크포워드 오차 10~90%</text>{caution}'
        '</svg>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lags", type=int, default=2)
    parser.add_argument("--min-train", type=int, default=MIN_TRAIN)
    parser.add_argument("--index", default="kor", choices=("kor", "g20"),
                        help="OECD 한국(기본) 또는 G20 선행지수")
    parser.add_argument("--chart-start", default="2011-01",
                        help="그림의 시작 달. 비우면 최근 48개월만 그린다")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    name, title = (("cli_kor", "OECD 한국 경기선행지수") if args.index == "kor"
                   else ("cli_g20", "OECD G20 경기선행지수"))
    cli = load_series(name, ROOT / "macro_history" / f"{name}.csv")
    partners = [load_series("leading_cycle", ROOT / "macro_history" / "leading_cycle.csv"),
                load_series("semiconductor_exports",
                            ROOT / "macro_history" / "semiconductor_exports.csv").pipe(np.log)]
    kospi_path = ROOT / "macro_history" / "kospi_monthly.csv"
    kospi = load_series("kospi", kospi_path) if kospi_path.exists() else None

    report = diagnose(cli)
    print(f"=== 1단계 진단 · {title} {report['first']}~{report['last']} ({report['n']}개월) ===")
    print(f"  월 변화 표준편차 {report['change_std']:.4f}")
    print("  자기상관 " + " ".join(f"{k}개월 {v:+.2f}" for k, v in report["autocorr"].items()))
    for item in report["variance_ratio"]:
        if item.get("vr") is None:
            print(f"  분산비 q={item['q']}: {item.get('reason')}")
            continue
        verdict = "임의보행을 버리지 못함" if item["z"] is None or abs(item["z"]) < 1.96 else (
            "추세(임의보행 아님)" if item["vr"] > 1 else "평균회귀(임의보행 아님)")
        print(f"  분산비 q={item['q']:2}: VR {item['vr']:.3f} · z {item['z']:+.2f} "
              f"(등분산 가정 {item['z_homoskedastic']:+.2f}) → {verdict}")

    table = walk_forward(cli, partners, min_train=args.min_train, lags=args.lags)
    scores = evaluate(table)
    print(f"\n=== 2단계 전망 · 워크포워드 {len(table)}건 (첫 학습 {args.min_train}개월, 시차 {args.lags}) ===")
    for horizon in sorted(scores["horizon"].unique()):
        part = scores[scores["horizon"] == horizon]
        print(f"  [{horizon}개월 앞]")
        for _, row in part.iterrows():
            line = f"    {row['model']:6} MAE {row['mae']:.4f} (n={row['n']})"
            for baseline in ("rw", "drift"):
                key = f"vs_{baseline}"
                if pd.notna(row.get(key)):
                    verdict = "우위" if row[f"{key}_hi"] < 0 else (
                        "열위" if row[f"{key}_lo"] > 0 else "동률")
                    line += (f" · vs {baseline} {row[key]:+.4f} "
                             f"[{row[f'{key}_lo']:+.4f}, {row[f'{key}_hi']:+.4f}] {verdict}")
            print(line)

    ahead = forecast_now(cli, table, scores, partners=partners, lags=args.lags)
    print(f"\n=== 전망 · 마지막 확정 {cli.index[-1]:%Y-%m} {cli.iloc[-1]:.4f} ===")
    for _, row in ahead.iterrows():
        band = (f"[{row['low']:.3f}, {row['high']:.3f}]"
                if pd.notna(row["low"]) else "구간 없음")
        others = " · ".join(f"{k} {row[k]:.3f}" for k in ("ar", "var") if pd.notna(row.get(k)))
        print(f"  {row['month']} ({row['horizon']}개월 앞) {row['point']:.4f} {band} "
              f"· 채택 {row['model']} — {row['reason']}")
        if others:
            print(f"      참고(채택 안 함): {others}")

    (args.out / "forecast.svg").write_text(
        forecast_svg(cli, ahead, overlay=kospi, title=title, start=args.chart_start or None),
        encoding="utf-8")
    ahead.to_csv(args.out / "forecast.csv", index=False)
    table.to_csv(args.out / "walk_forward.csv", index=False)
    scores.to_csv(args.out / "scores.csv", index=False)
    (args.out / "diagnostics.json").write_text(
        json.dumps({"diagnostics": report, "lags": args.lags, "min_train": args.min_train,
                    "seed": SEED, "bootstrap_b": BOOTSTRAP,
                    "revision_warning": "최신본 한 벌만 사용. 개정을 미리 아는 셈이라 성적이 부풀려져 있다."},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {args.out}")


if __name__ == "__main__":
    main()

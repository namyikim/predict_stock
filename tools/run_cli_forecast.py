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
            for baseline in ("rw", "drift"):
                if name == baseline or baseline not in scores:
                    continue
                result = paired_ci(part[name].to_numpy(dtype=float) - actual,
                                   part[baseline].to_numpy(dtype=float) - actual)
                row[f"vs_{baseline}"] = result["diff"]
                row[f"vs_{baseline}_lo"] = result["lo"]
                row[f"vs_{baseline}_hi"] = result["hi"]
            rows.append(row)
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lags", type=int, default=2)
    parser.add_argument("--min-train", type=int, default=MIN_TRAIN)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    cli = load_series("cli_g20", ROOT / "macro_history" / "cli_g20.csv")
    partners = [load_series("leading_cycle", ROOT / "macro_history" / "leading_cycle.csv"),
                load_series("semiconductor_exports",
                            ROOT / "macro_history" / "semiconductor_exports.csv").pipe(np.log)]

    report = diagnose(cli)
    print(f"=== 1단계 진단 · G20 선행지수 {report['first']}~{report['last']} ({report['n']}개월) ===")
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

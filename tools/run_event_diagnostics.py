# -*- coding: utf-8 -*-
"""R08 2단계 — 미국 지표 발표 다음 한국 거래일이 정말 다른가.

계획(guides/research-candidates-plan.md R08)의 가설은 "발표 다음 거래일은 갭이 커서 같은 폭의
구간은 덜 맞는다"이다. 정책(구간 확대·보류)을 만들기 전에 그 전제부터 잰다.

**원장이 아니라 시세로 잰다.** 전향 원장(forecast_history/*/forecast_log.csv)은 2026-09-07 부터라
채점된 날이 닷새뿐이다. 정책을 정당화할 표본이 못 된다. 대신 일정표가 덮는 구간의 실제 시세로
갭을 재면 지금 바로 수십 일을 볼 수 있고, 원장이 쌓이면 같은 잣대로 다시 재면 된다.

이벤트일의 정의를 **두 가지로 나눠** 잰다.
  next   — 발표 뒤 **첫** 한국 거래일. 계획이 말하는 그 날이다.
  flagged — 지금 코드가 원장에 남기는 표시(`korea_event_flags`, 달력일 4일 소급).
두 정의는 같지 않다. 금요일 발표는 월요일과 화요일을 모두 표시한다. 어느 쪽으로 재는지에 따라
결론이 달라질 수 있어 둘 다 적는다.

    python tools/run_event_diagnostics.py --out runs/event_diagnostics
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data_sources import us_calendar as uc  # noqa: E402

TARGETS = {"samsung": "삼성전자", "sk_hynix": "SK하이닉스"}
PRICE_CACHE = ROOT / "runs" / "macro_integration" / "data_cache"
SEED = 20260912
BOOTSTRAP = 4000


def load_prices(target, cache=PRICE_CACHE):
    path = Path(cache) / f"{target}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"시세 캐시가 없습니다: {path}")
    frame = pd.read_parquet(path)
    frame.index = pd.to_datetime(frame.index)
    return frame.sort_index()


def daily_moves(prices):
    """하루치 움직임 세 가지. 모두 절댓값(방향이 아니라 폭을 본다)."""
    out = pd.DataFrame(index=prices.index)
    previous = prices["close"].shift(1)
    out["abs_gap"] = (prices["open"] / previous - 1).abs()          # 밤 사이
    out["abs_move"] = (prices["close"] / previous - 1).abs()        # 하루 전체
    out["abs_intraday"] = (prices["close"] / prices["open"] - 1).abs()
    return out.dropna()


def event_days(trading_days, storage=None):
    """(발표 뒤 첫 거래일 집합, 지금 코드가 표시하는 날 집합, 발표일별 첫 거래일)."""
    days = pd.DatetimeIndex(sorted(trading_days))
    first_after, mapping = set(), {}
    for date_text, event in uc.US_RELEASES:
        released = pd.Timestamp(date_text)
        later = days[days > released]
        if len(later):
            first_after.add(later[0])
            mapping.setdefault(later[0], []).append(event)
    flagged = {d for d in days if uc.korea_event_flags(d, storage)}
    return first_after, flagged, mapping


def bootstrap_gap(event_values, other_values, statistic=np.mean, b=BOOTSTRAP, seed=SEED):
    """두 집단의 통계량 차이와 95% 구간. 날짜 단위 단순 부트스트랩.

    갭은 날마다 거의 독립이라 블록을 잡지 않는다. 표본이 작으므로 구간이 넓게 나오는 것이 정상이고,
    넓게 나오면 '차이가 없다'가 아니라 '아직 모른다'로 읽어야 한다.
    """
    event_values = np.asarray(event_values, dtype=float)
    other_values = np.asarray(other_values, dtype=float)
    if len(event_values) < 2 or len(other_values) < 2:
        return {"diff": None, "lo": None, "hi": None, "n_event": len(event_values),
                "n_other": len(other_values), "reason": "표본 부족"}
    rng = np.random.default_rng(seed)
    observed = float(statistic(event_values) - statistic(other_values))
    draws = np.empty(b)
    for i in range(b):
        a = rng.choice(event_values, len(event_values), replace=True)
        c = rng.choice(other_values, len(other_values), replace=True)
        draws[i] = statistic(a) - statistic(c)
    lo, hi = np.quantile(draws, [0.025, 0.975])
    return {"diff": observed, "lo": float(lo), "hi": float(hi),
            "n_event": int(len(event_values)), "n_other": int(len(other_values)),
            "ratio": float(statistic(event_values) / statistic(other_values))
            if statistic(other_values) else None}


def diagnose(target, storage=None, cache=PRICE_CACHE):
    low, high = uc.COVERAGE
    moves = daily_moves(load_prices(target, cache))
    window = moves.loc[str(low):str(high)]
    if window.empty:
        raise ValueError(f"{target}: 일정표 구간({low}~{high})에 겹치는 시세가 없습니다.")
    first_after, flagged, mapping = event_days(window.index, storage)
    rows = []
    for definition, marked in (("next", first_after), ("flagged", flagged)):
        is_event = window.index.isin(sorted(marked))
        for column in ("abs_gap", "abs_move", "abs_intraday"):
            for name, statistic in (("mean", np.mean), ("median", np.median)):
                result = bootstrap_gap(window.loc[is_event, column],
                                       window.loc[~is_event, column], statistic)
                rows.append({"target": target, "definition": definition, "metric": column,
                             "statistic": name,
                             "event": float(statistic(window.loc[is_event, column]))
                             if is_event.any() else None,
                             "other": float(statistic(window.loc[~is_event, column])),
                             **result})
    by_event = {}
    for day, events in mapping.items():
        for event in events:
            by_event.setdefault(event, []).append(float(window.loc[day, "abs_gap"]))
    return pd.DataFrame(rows), {
        "target": target, "window": [str(window.index[0].date()), str(window.index[-1].date())],
        "trading_days": int(len(window)),
        "event_days_next": int(len(first_after & set(window.index))),
        "event_days_flagged": int(len(flagged & set(window.index))),
        "by_event_type": {k: {"n": len(v), "mean_abs_gap": float(np.mean(v))}
                          for k, v in sorted(by_event.items())},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=PRICE_CACHE)
    parser.add_argument("--storage", default=None, help="us_calendar 덮어쓰기 CSV 가 있는 폴더")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    tables, summaries = [], []
    for target, name in TARGETS.items():
        table, summary = diagnose(target, args.storage, args.cache)
        tables.append(table)
        summaries.append(summary)
        print(f"\n=== {name} · {summary['window'][0]}~{summary['window'][1]} "
              f"거래일 {summary['trading_days']}일 ===")
        print(f"  이벤트일: 발표 다음 첫 거래일 {summary['event_days_next']}일 "
              f"· 현재 표시 규칙 {summary['event_days_flagged']}일")
        for _, row in table[table["statistic"] == "mean"].iterrows():
            if row["diff"] is None:
                print(f"  {row['definition']:8} {row['metric']:12} 표본 부족")
                continue
            verdict = "크다" if row["lo"] > 0 else ("작다" if row["hi"] < 0 else "동률")
            print(f"  {row['definition']:8} {row['metric']:12} "
                  f"이벤트 {row['event']:.4%} vs 그 밖 {row['other']:.4%} "
                  f"· 차이 {row['diff']:+.4%} [{row['lo']:+.4%}, {row['hi']:+.4%}] {verdict}")

    combined = pd.concat(tables, ignore_index=True)
    combined.to_csv(args.out / "metrics.csv", index=False)
    (args.out / "summary.json").write_text(
        json.dumps({"summaries": summaries, "bootstrap_b": BOOTSTRAP, "seed": SEED},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {args.out / 'metrics.csv'}, {args.out / 'summary.json'}")


if __name__ == "__main__":
    main()

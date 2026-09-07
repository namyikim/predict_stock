# -*- coding: utf-8 -*-
"""장 마감 후 갱신 — 그날 종가로 예측을 채점하고 보고서의 '어제 예측 vs 실제' 절만 다시 그린다.

아침 실행(06:30)은 전 구간 워크포워드를 다시 돌려 보고서를 통째로 만든다. 오후에 그것을 반복할
이유가 없다. 하루 사이에 성능표가 의미 있게 달라지지 않고, 오히려 아침과 오후의 숫자가 미세하게
달라져 "왜 바뀌었지"만 남는다. 그래서 이 도구는

  1) 대상 종목 시세만 받아 원장을 채점하고(예측은 새로 만들지 않는다),
  2) 이미 발행된 보고서에서 표시된 구간만 새 표로 바꿔 끼운다.

성능표·다음 거래일 예측은 아침 값 그대로다. 그 사실을 절 머리에 적는다.

    python tools/build_afternoon_update.py --target samsung --out runs/afternoon
    python tools/build_afternoon_update.py --target samsung --out runs/afternoon --publish
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import github_pages  # noqa: E402
from forecast_utils import (  # noqa: E402
    atomic_csv, daily_comparison, evaluate_forecasts, ledger_section_html, review_ledger,
    summarize_daily,
)

KST = timezone(timedelta(hours=9))
TARGETS = {
    "samsung": {"ticker": "005930.KS", "name": "삼성전자", "ensemble": "Mean ensemble"},
    "sk_hynix": {"ticker": "000660.KS", "name": "SK하이닉스", "ensemble": "Mean ensemble"},
}
LEDGER_FILES = ["forecast_log.csv", "daily_forecast_comparison.csv", "forecast_accuracy_summary.csv"]
MARK_START, MARK_END = "<!--LEDGER_SECTION_START-->", "<!--LEDGER_SECTION_END-->"


def load_bars(ticker):
    """대상 종목 일봉. 한국장 마감(15:40 KST) 전이면 당일 봉은 미완성이므로 버린다."""
    import yfinance as yf
    frame = yf.Ticker(ticker).history(start="2015-01-01", auto_adjust=False)
    if frame is None or frame.empty:
        raise RuntimeError(f"{ticker} 시세를 받지 못했습니다.")
    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
    frame.columns = [str(c).strip().lower().replace(" ", "_") for c in frame.columns]
    if "adj_close" not in frame:
        frame["adj_close"] = frame["close"]
    now = pd.Timestamp.now(tz="Asia/Seoul")
    if frame.index[-1].date() >= now.date() and (now.hour, now.minute) < (15, 40):
        frame = frame.iloc[:-1]
    # 거래량 0에 시가=고가=저가=종가인 유령봉은 '보합'을 조작하므로 채점에서 뺀다(노트북과 같은 규칙).
    ghost = (frame["volume"] == 0) & (frame["high"] == frame["low"]) & (frame["open"] == frame["close"])
    return frame[~ghost][["open", "high", "low", "close", "adj_close", "volume"]].astype(float)


def score(storage, target, bars, token):
    storage.mkdir(parents=True, exist_ok=True)
    remote = github_pages.fetch(f"forecast_history/{target}/forecast_log.csv", token) if token else None
    if remote:
        (storage / "forecast_log.csv").write_text(remote, encoding="utf-8")
    path = storage / "forecast_log.csv"
    if not path.exists():
        raise RuntimeError("원장이 없습니다. 아침 실행이 한 번은 성공해야 채점할 것이 생깁니다.")
    evaluated = evaluate_forecasts(pd.read_csv(path), bars)
    atomic_csv(evaluated, path)
    daily = daily_comparison(evaluated)
    atomic_csv(daily, storage / "daily_forecast_comparison.csv")
    atomic_csv(summarize_daily(daily), storage / "forecast_accuracy_summary.csv")
    return evaluated, daily


def replace_section(page, section_html):
    """표시된 구간만 바꾼다. 표시가 없으면(옛 보고서) 건드리지 않는다."""
    start, end = page.find(MARK_START), page.find(MARK_END)
    if start < 0 or end < 0 or end < start:
        return None
    return page[:start] + MARK_START + section_html + page[end:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="samsung", choices=list(TARGETS))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    spec = TARGETS[args.target]
    storage = args.out / args.target
    token = github_pages.token() if args.publish else None

    bars = load_bars(spec["ticker"])
    print(f"시세 {len(bars):,}행 · 마지막 봉 {bars.index[-1].date()}", flush=True)
    evaluated, daily = score(storage, args.target, bars, token)
    print("채점 상태:", evaluated["status"].value_counts().to_dict(), flush=True)

    review = review_ledger(daily, bars, ensemble_model=spec["ensemble"], windows=(20, 60))
    now = datetime.now(KST)
    note = ('<div style="font-size:12px;color:#6b7178;margin:4px 0 8px;padding:8px 12px;'
            'background:#f7f8fa;border-radius:5px">'
            f'<b>장 마감 후 갱신 {now:%H:%M} KST</b> — 이 절만 오늘 종가로 다시 채점했습니다. '
            '아래 성능표와 다음 거래일 예측은 <b>오늘 아침 06:30 기준</b> 그대로입니다.</div>')
    section = ledger_section_html(review, spec["ensemble"], updated_note=note)
    (storage / "ledger_section.html").write_text(section, encoding="utf-8")
    for alert in review["alerts"]:
        print("⚠️", alert, flush=True)
    print(f"채점된 예측일 {review['n_scored_days']}일 · 마지막 "
          f"{review['latest_date'].date() if review['latest_date'] is not None else '—'}", flush=True)

    if not args.publish:
        print("발행하지 않았습니다(--publish 없음). 조각:", storage / "ledger_section.html")
        return

    for name in LEDGER_FILES:
        sha = github_pages.publish(f"forecast_history/{args.target}/{name}",
                                   (storage / name).read_text(encoding="utf-8"),
                                   token, f"score: {name} ({now:%Y-%m-%d %H:%M} KST)")
        print(f"원장 저장 forecast_history/{args.target}/{name} @ {sha}")

    pages = [f"docs/{args.target}/index.html"]
    latest = github_pages.fetch(pages[0], token)
    if latest is None:
        print("⚠️ 발행된 보고서가 없어 절 교체를 건너뜁니다.")
        return
    # 같은 내용의 날짜별 보관본도 함께 고친다(예측일은 보고서 제목에서 읽는다).
    stamp = pd.Timestamp(now.date())
    for candidate in (stamp, stamp + pd.Timedelta(days=1)):
        path = f"docs/{args.target}/reports/{candidate.date()}.html"
        if github_pages.fetch(path, token) is not None:
            pages.append(path)
    for path in pages:
        page = github_pages.fetch(path, token)
        updated = replace_section(page, section)
        if updated is None:
            print(f"⚠️ {path}: 교체 표시가 없어 건너뜁니다(옛 보고서).")
            continue
        sha = github_pages.publish(path, updated, token, f"update: {path} 장 마감 후 갱신 ({now:%Y-%m-%d %H:%M} KST)")
        print(f"보고서 갱신 {path} @ {sha}")


if __name__ == "__main__":
    main()

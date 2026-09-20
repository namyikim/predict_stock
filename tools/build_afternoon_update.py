# -*- coding: utf-8 -*-
"""장 마감 후 갱신 — 그날 종가로 예측을 채점하고 보고서의 '예측 vs 실제' 절과
쉬운 요약 맨 위의 '지난 예측은 맞았나'만 다시 그린다.

아침 실행(06:30)은 전 구간 워크포워드를 다시 돌려 보고서를 통째로 만든다. 오후에 그것을 반복할
이유가 없다. 하루 사이에 성능표가 의미 있게 달라지지 않고, 오히려 아침과 오후의 숫자가 미세하게
달라져 "왜 바뀌었지"만 남는다. 그래서 이 도구는

  1) 대상 종목 시세만 받아 원장을 채점하고(07:00 예측은 새로 만들지 않는다),
  2) 이미 발행된 보고서에서 표시된 구간만 새 표로 바꿔 끼운다.

성능표·다음 거래일 예측은 아침 값 그대로다. 그 사실을 절 머리에 적는다.

예외 하나(P16 운영 반영, 2026-09-20): --scope open 회차는 아침 노트북이 미리 계산해 둔 가상 갭 격자
(forecast_history/<종목>/post_open_grid.csv)를 실제 시가로 보간해 'Post-open' 행 하나를 원장에 더한다.
07:00 행은 건드리지 않고, 정보 마감(15:30)이 다른 별도 행으로 따로 채점된다. 더 나은 모델이 아니라
늦은 정보 시점이다 — 카드에 그렇게 적는다. 격자의 target_date 가 오늘 세션이 아니거나 오늘 봉에
시가가 없으면 만들지 않고 건너뛴다(전일 격자·전일 시가를 끌어오면 그것이 P16 이 막은 거짓말이다).

    python tools/build_afternoon_update.py --target samsung --out runs/afternoon
    python tools/build_afternoon_update.py --target samsung --out runs/afternoon --publish
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import github_pages  # noqa: E402
from forecast_utils import (  # noqa: E402
    POST_OPEN_MODEL, POSTOPEN_END, POSTOPEN_START, SCORECARD_END, SCORECARD_START, append_forecasts,
    atomic_csv, daily_comparison, evaluate_forecasts, is_headline_model, ledger_section_html,
    post_open_card_html, post_open_ledger_row, post_open_row_for, review_ledger, scorecard_html,
    summarize_daily,
)

KST = timezone(timedelta(hours=9))
# 채점할 방향 모델은 보고서 대표 모델(노트북 HEADLINE_MODEL)과 같아야 한다. 2026-09-08 대표 모델을 시세만
# 모델로 바꿀 때 여기를 놓쳐 장 마감 후 갱신만 Mean ensemble 로 채점했고, 대표 모델이 '보합'으로 틀린 날이
# '상승 적중'으로 보였다(2026-09-16 SK하이닉스). tests/test_afternoon_update.py 가 둘을 맞춰 본다.
TARGETS = {
    "samsung": {"ticker": "005930.KS", "name": "삼성전자", "ensemble": "No macro ensemble"},
    "sk_hynix": {"ticker": "000660.KS", "name": "SK하이닉스", "ensemble": "No macro ensemble"},
}
LEDGER_FILES = ["forecast_log.csv", "daily_forecast_comparison.csv", "forecast_accuracy_summary.csv"]
GRID_FILE = "post_open_grid.csv"            # 아침 노트북이 올리는 가상 갭 격자(같은 forecast_history 폴더)
# 시가 반영 갱신을 끄려면 여기를 False 로. 원장에는 추가 행만 쌓이므로 끄면 07:00 만 있던 상태로 돌아간다.
POST_OPEN_ENABLED = True
MARK_START, MARK_END = "<!--LEDGER_SECTION_START-->", "<!--LEDGER_SECTION_END-->"
OPEN_CONFIRMED, CLOSE_CONFIRMED = (9, 5), (15, 40)   # should_score_now·evaluate_forecasts와 같은 기준


def load_bars(ticker, scope="all"):
    """대상 종목 일봉.

    scope="all"  : 장 마감(15:40 KST) 전이면 당일 봉을 버린다. 종가가 확정되지 않았다.
    scope="open" : 개장 직후 실행. 시가만 쓰려고 봉은 남기되 종가·고가·저가를 비운다.
                   야후의 장중 close는 마지막 체결가일 뿐 종가가 아니어서, 남겨 두면
                   나중에 그 값으로 무언가를 채점할 위험이 있다.
    """
    import yfinance as yf
    frame = yf.Ticker(ticker).history(start="2015-01-01", auto_adjust=False)
    if frame is None or frame.empty:
        raise RuntimeError(f"{ticker} 시세를 받지 못했습니다.")
    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
    frame.columns = [str(c).strip().lower().replace(" ", "_") for c in frame.columns]
    if "adj_close" not in frame:
        frame["adj_close"] = frame["close"]
    now = pd.Timestamp.now(tz="Asia/Seoul")
    if frame.index[-1].date() >= now.date() and (now.hour, now.minute) < CLOSE_CONFIRMED:
        if scope == "open":
            frame = frame.copy()
            frame.loc[frame.index[-1], ["close", "high", "low", "adj_close"]] = np.nan
        else:
            frame = frame.iloc[:-1]
    # 거래량 0에 시가=고가=저가=종가인 유령봉은 '보합'을 조작하므로 채점에서 뺀다(노트북과 같은 규칙).
    ghost = (frame["volume"] == 0) & (frame["high"] == frame["low"]) & (frame["open"] == frame["close"])
    return frame[~ghost][["open", "high", "low", "close", "adj_close", "volume"]].astype(float)


def describe_run(bars, now):
    """(이름, 설명) — 이번 실행이 실제로 채점한 범위. 절 머리와 커밋 제목이 같은 이름을 쓴다.

    --scope 만 보고 정하면 틀린다. 커밋 제목이 scope와 무관하게 '장 마감 후 갱신'이어서, 09:37 cron이
    4.5시간 밀려 14:11에 도착한 시초가 채점이 마감 전에 돈 마감 후 갱신처럼 보였다(2026-09-15·16).
    장중에 scope=all을 고르면 load_bars가 오늘 봉을 버려 지난 거래일까지만 채점되고, scope=open이어도
    야후에 오늘 봉이 없으면 시가를 채점하지 못한다. 그래서 받은 봉과 지금 시각으로 정한다.
    """
    last = bars.index[-1]
    if last.date() == now.date():
        if np.isfinite(bars["close"].iloc[-1]):
            return "장 마감 후 갱신", "이 절과 맨 위 ‘지난 예측은 맞았나’만 오늘 종가로 다시 채점했습니다."
        if np.isfinite(bars["open"].iloc[-1]):
            return "시초가 확인", ("오늘 시가가 확정되어 <b>시초가 예측만</b> 채점했습니다. "
                               "종가 관련 항목은 장 마감 후(16:10)에 채워집니다.")
    day = f"{last.month}월 {last.day}일"
    if OPEN_CONFIRMED <= (now.hour, now.minute) < CLOSE_CONFIRMED:
        return "장중 재채점", (f"오늘 종가는 아직 확정되지 않아(15:40 KST) <b>{day}까지만</b> 다시 채점했습니다. "
                             "오늘 예측은 장 마감 후(16:10)에 채점됩니다.")
    # 개장 전(자정을 넘긴 밀린 회차) 또는 마감 뒤인데 야후에 오늘 봉이 아직 없는 경우
    return "장 마감 후 갱신", f"이 절과 맨 위 ‘지난 예측은 맞았나’만 {day} 종가까지 다시 채점했습니다."


def fetch_ledger(storage, target, token):
    """원격 원장을 받아 둔다(토큰이 없으면 로컬 사본). 경로를 돌려준다."""
    storage.mkdir(parents=True, exist_ok=True)
    remote = github_pages.fetch(f"forecast_history/{target}/forecast_log.csv", token) if token else None
    if remote:
        (storage / "forecast_log.csv").write_text(remote, encoding="utf-8")
    path = storage / "forecast_log.csv"
    if not path.exists():
        raise RuntimeError("원장이 없습니다. 아침 실행이 한 번은 성공해야 채점할 것이 생깁니다.")
    return path


def fetch_grid(storage, target, token):
    """아침 노트북이 올린 가상 갭 격자. 없거나 읽을 수 없으면 None."""
    text = github_pages.fetch(f"forecast_history/{target}/{GRID_FILE}", token) if token else None
    if text:
        (storage / GRID_FILE).write_text(text, encoding="utf-8")
    path = storage / GRID_FILE
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except Exception as exc:              # HTML 오류 페이지 등
        print(f"⚠️ {GRID_FILE} 을 읽지 못했습니다: {type(exc).__name__}: {exc}")
        return None


def append_post_open(ledger_path, grid, bars, now):
    """시가 반영 갱신 행 하나를 원장에 더한다. (행 또는 None, 이유). 기존 행은 한 칸도 바꾸지 않는다.

    now: 실제 실행 시각(tz-aware). 09:37 cron 이 14:10 에 도착한 날은 created_at_utc 에 그렇게 남는다.
    """
    if not POST_OPEN_ENABLED:
        return None, "POST_OPEN_ENABLED=False"
    if grid is None:
        return None, f"{GRID_FILE} 이 없습니다(아침 실행이 격자를 올리지 않았습니다)"
    session = pd.Timestamp(now).tz_convert("Asia/Seoul").normalize().tz_localize(None)
    if bars.index[-1] != session:
        return None, f"오늘({session.date()}) 봉이 아직 없습니다 — 전일 시가로 만들지 않습니다"
    existing = pd.read_csv(ledger_path)
    if post_open_row_for(existing, session) is not None:
        return None, f"{session.date()} 의 {POST_OPEN_MODEL} 행이 이미 있습니다(중복 기록 안 함)"
    prev = bars.index[bars.index < session]
    if not len(prev):
        return None, "전일 봉이 없습니다"
    row, reason = post_open_ledger_row(grid, session, bars["open"].iloc[-1], bars.loc[prev[-1], "close"],
                                       prev[-1], pd.Timestamp(now).tz_convert("UTC"))
    if row is None:
        return None, reason
    append_forecasts(ledger_path, pd.DataFrame([row]))
    return row, ""


def score(storage, target, bars, token, ledger_path=None):
    path = ledger_path or fetch_ledger(storage, target, token)
    evaluated = evaluate_forecasts(pd.read_csv(path), bars)
    atomic_csv(evaluated, path)
    daily = daily_comparison(evaluated)
    atomic_csv(daily, storage / "daily_forecast_comparison.csv")
    atomic_csv(summarize_daily(daily), storage / "forecast_accuracy_summary.csv")
    return evaluated, daily


def replace_section(page, section_html, start_mark=MARK_START, end_mark=MARK_END):
    """표시된 구간만 바꾼다. 표시가 없으면(옛 보고서) 건드리지 않는다.

    기본은 '예측 vs 실제' 절이다. 쉬운 요약 맨 위의 '지난 예측은 맞았나'는 SCORECARD 표시를 넘긴다.
    """
    start, end = page.find(start_mark), page.find(end_mark)
    if start < 0 or end < 0 or end < start:
        return None
    return page[:start] + start_mark + section_html + page[end:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="samsung", choices=list(TARGETS))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--scope", default="all", choices=("all", "open"),
                        help="open: 개장 직후 시초가만 채점 · all: 마감 후 전체 채점")
    args = parser.parse_args()
    spec = TARGETS[args.target]
    storage = args.out / args.target
    token = github_pages.token() if args.publish else None

    bars = load_bars(spec["ticker"], scope=args.scope)
    print(f"시세 {len(bars):,}행 · 마지막 봉 {bars.index[-1].date()} · scope={args.scope}", flush=True)
    now = datetime.now(KST)
    ledger_path = fetch_ledger(storage, args.target, token)
    # 시가 반영 갱신(P16 운영 반영): 개장 직후 회차에만, 오늘 격자와 오늘 시가가 모두 있을 때만 한 행을 더한다.
    post_open_row = None
    if args.scope == "open":
        try:
            post_open_row, why = append_post_open(ledger_path, fetch_grid(storage, args.target, token), bars, now)
        except Exception as exc:          # 격자 문제로 채점 전체가 죽으면 안 된다
            post_open_row, why = None, f"{type(exc).__name__}: {exc}"
        if post_open_row is None:
            print(f"시가 반영 갱신 건너뜀: {why}", flush=True)
        else:
            print(f"시가 반영 갱신 기록: {post_open_row['prediction']} (갭 {post_open_row['gap']:+.2%}, "
                  f"상승 {post_open_row['p_up']:.0%}·보합 {post_open_row['p_flat']:.0%}·하락 {post_open_row['p_down']:.0%}) "
                  f"· 실제 실행 {now:%H:%M} KST", flush=True)
    evaluated, daily = score(storage, args.target, bars, token, ledger_path=ledger_path)
    print("채점 상태:", evaluated["status"].value_counts().to_dict(), flush=True)

    review = review_ledger(daily, bars, ensemble_model=spec["ensemble"], windows=(20, 60))
    version = github_pages.code_version(token)
    stamp = (f' · 코드 커밋 <code>{version["short"]}</code>' if version["short"] else "")
    label, detail = describe_run(bars, now)
    print(f"이번 실행: {label} (scope={args.scope})", flush=True)
    note = ('<div style="font-size:12px;color:#6b7178;margin:4px 0 8px;padding:8px 12px;'
            'background:#f7f8fa;border-radius:5px">'
            f'<b>{label} {now:%H:%M} KST</b>{stamp} — {detail} '
            '아래 성능표와 다음 거래일 예측은 <b>오늘 아침 기준</b> 그대로입니다.</div>')
    section = ledger_section_html(review, spec["ensemble"], updated_note=note)
    (storage / "ledger_section.html").write_text(section, encoding="utf-8")
    # 쉬운 요약 맨 위의 '지난 예측은 맞았나'도 같은 채점으로 다시 그린다. 아침 값이 남으면 아래 절과 어긋난다.
    card = scorecard_html(review, spec["ensemble"], note=f"{now:%H:%M} KST 채점 반영.")
    # 시가 반영 갱신 카드. 07:00 대표 행(같은 target_date 의 사전 예측)을 함께 적어 '위 카드 그대로'를 보인다.
    post_open_card = None
    if post_open_row is not None:
        morning = evaluated[(evaluated["kind"] == "direction") & (evaluated["model"] == spec["ensemble"])
                            & (evaluated["target_date"].astype(str).str[:10] == post_open_row["target_date"])
                            & is_headline_model(evaluated["model"])]
        post_open_card = post_open_card_html(post_open_row, target=args.target,
                                             morning=morning.iloc[0].to_dict() if len(morning) else None)
        (storage / "post_open_card.html").write_text(post_open_card, encoding="utf-8")
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
        # 표시가 없는 옛 보고서(2026-09-13 이전)는 절만 바꾼다.
        updated = replace_section(updated, card, SCORECARD_START, SCORECARD_END) or updated
        if post_open_card is not None:
            replaced = replace_section(updated, post_open_card, POSTOPEN_START, POSTOPEN_END)
            if replaced is None:
                print(f"⚠️ {path}: 시가 반영 갱신 표시가 없어 카드를 넣지 못했습니다(옛 보고서).")
            else:
                updated = replaced
        sha = github_pages.publish(path, updated, token, f"update: {path} {label} ({now:%Y-%m-%d %H:%M} KST)")
        print(f"보고서 갱신 {path} @ {sha}")


if __name__ == "__main__":
    main()

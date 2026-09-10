# -*- coding: utf-8 -*-
"""채점 회차 게이트 — cron 문자열이 아니라 '지금 시각(KST)'으로 이번 실행이 할 일을 정한다.

GitHub의 cron은 이 저장소에서 예정보다 4~5시간 늦게 실행을 만든다(09:37 회차 → 14:10, 16:10 회차 →
21:06~21:18. 2026-09-08~10 사흘 모두 같았다). 일일 보고서는 하루 23개 cron 중 11~13개만 실행됐다. 그래서

  1. 회차를 여러 개 건다(정시 + 백업). 정시 호출은 Cloudflare Worker의 Cron Trigger가
     workflow_dispatch API로 한다(counter/worker.js) — 그 경로는 분 단위로 정확하다.
  2. 각 실행은 '몇 시 cron이었나'가 아니라 '지금 몇 시인가'로 범위를 정한다. 09:37 회차가 장 마감
     뒤에 도착하면 시가만이 아니라 종가까지 채점하고 회고도 붙인다.
  3. 그날 할 일이 이미 발행됐으면(원장에 채점이 있고 회고 JSON이 origin/main에 있으면) 예약 실행은
     건너뛴다. 수동·dispatch 실행은 건너뛰지 않는다.

    python tools/should_score_now.py --target samsung --event schedule --requested auto
      → GITHUB_OUTPUT 에 run= score= review= scope= session= 을 쓴다.

  scope    open: 시가만 채점(장중) · all: 종가까지 채점
  session  채점·회고 대상 거래일. 09:05 전이면 전 거래일(밀린 회차가 자정을 넘긴 경우)
  score    원장 채점·보고서 절 교체를 할지
  review   장 마감 회고를 붙일지(종가 확정 뒤에만)
"""
import argparse
import csv
import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from should_run_today import published_ledger

KST = timezone(timedelta(hours=9))
OPEN_CONFIRMED = (9, 5)      # 09:00 동시호가로 시가 확정. 그 뒤부터 시초가 예측을 채점한다.
CLOSE_CONFIRMED = (15, 40)   # 야후 일봉의 당일 종가 확정 기준(노트북·build_afternoon_update와 같다)


def is_trading_day(day):
    """KRX 거래일인가. 달력이 없으면 주말 규칙(공휴일은 놓친다)."""
    try:
        import exchange_calendars as xc
        return bool(xc.get_calendar("XKRX").is_session(day.isoformat()))
    except Exception:
        return day.weekday() < 5


def previous_trading_day(day):
    """day 직전 거래일. 달력이 없으면 주말만 건너뛴다."""
    try:
        import exchange_calendars as xc
        calendar = xc.get_calendar("XKRX")
        return calendar.date_to_session((day - timedelta(days=1)).isoformat(), direction="previous").date()
    except Exception:
        day -= timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return day


def phase_of(now):
    hm = (now.hour, now.minute)
    if hm < OPEN_CONFIRMED:
        return "pre_open"
    if hm < CLOSE_CONFIRMED:
        return "session"
    return "post_close"


def session_for(now):
    """이 실행이 채점·회고할 거래일. 자정을 넘겨 도착한 회차는 전 거래일을 맡는다."""
    return previous_trading_day(now.date()) if phase_of(now) == "pre_open" else now.date()


def scored_rows(ledger_text, session, kind):
    """발행된 원장에서 session 날짜를 대상으로 한 kind 행 중 이미 채점된 것의 수."""
    if not ledger_text:
        return 0
    n = 0
    for row in csv.DictReader(io.StringIO(ledger_text)):
        if (str(row.get("target_date", ""))[:10] == str(session) and row.get("kind") == kind
                and row.get("status") == "scored"):
            n += 1
    return n


def decide(now, event="schedule", requested="auto", trading=None, ledger_text=None, review_published=False):
    """이번 실행이 할 일. trading / ledger_text / review_published 는 테스트에서 주입한다.

    돌려주는 값: run(무엇이든 할지), score, review, scope, session, phase, reason.
    """
    scheduled = event == "schedule"
    phase = phase_of(now)
    session = session_for(now)
    trading = is_trading_day(session) if trading is None else trading
    scope = requested if requested in ("open", "all") else ("open" if phase == "session" else "all")
    result = {"scope": scope, "session": str(session), "phase": phase}
    if not trading:
        # 예약 실행은 휴장일에 할 일이 없다. 수동 실행은 막지 않는다(직전 거래일 재채점 등).
        result.update(run=not scheduled, score=not scheduled, review=False, reason=f"{session} 휴장일")
        return result
    if scope == "open":
        done = scheduled and scored_rows(ledger_text, session, "open") > 0
        result.update(run=not done, score=not done, review=False,
                      reason=f"{session} 시초가 채점" + ("이 이미 발행됨" if done else ""))
        return result
    review_due = phase != "session"      # 종가가 확정된 뒤(또는 자정을 넘긴 밀린 회차)에만 회고
    score = not (scheduled and scored_rows(ledger_text, session, "direction") > 0)
    review = review_due and not (scheduled and review_published)
    result.update(run=score or review, score=score, review=review)
    if score or review:
        result["reason"] = f"{session} " + "·".join((["종가 채점"] if score else []) + (["회고"] if review else []))
    else:
        result["reason"] = f"{session} 마감 채점·회고가 이미 발행됨"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", required=True)
    parser.add_argument("--event", default="schedule", help="github.event_name (schedule 만 중복을 건너뛴다)")
    parser.add_argument("--requested", default="auto", help="workflow_dispatch 의 scope 입력: auto|open|all")
    parser.add_argument("--ledger-root", default="forecast_history")
    parser.add_argument("--ref", default="origin/main",
                        help="이 ref 의 발행 내용을 본다. 빈 문자열이면 작업 트리를 본다(테스트용)")
    args = parser.parse_args()
    now = datetime.now(KST)
    ref = args.ref or None
    session = session_for(now)
    root = Path(args.ledger_root) / args.target
    ledger_text = published_ledger((root / "forecast_log.csv").as_posix(), ref)
    review_published = published_ledger((root / "reviews" / f"{session}.json").as_posix(), ref) is not None
    result = decide(now, event=args.event, requested=args.requested,
                    ledger_text=ledger_text, review_published=review_published)
    print(f"{args.target}: 지금 {now:%Y-%m-%d %H:%M} KST({result['phase']}) · {result['reason']} → "
          f"run={result['run']} score={result['score']} review={result['review']} "
          f"scope={result['scope']} session={result['session']}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            for key in ("run", "score", "review"):
                handle.write(f"{key}={'true' if result[key] else 'false'}\n")
            handle.write(f"scope={result['scope']}\nsession={result['session']}\n")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""오늘 예측이 원장에 남았는지 확인하고, 없으면 실패한다.

자동 실행이 조용히 실패하는 일이 있었다. 워크플로가 초록불로 끝나면서 채점을 건너뛰거나,
cron이 통째로 누락되어도 아무도 알려 주지 않았다. 커밋 이력을 사람이 뒤져야 알 수 있었다.
이 스크립트는 결과만 본다 — 오늘 사전 예측이 원장에 있는가. 없으면 0이 아닌 코드로 끝내
GitHub이 실패 알림을 보내게 한다.

    python tools/check_ledger_health.py
    python tools/check_ledger_health.py --targets samsung --max-age-days 5
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]


def last_trading_day(today):
    """오늘 포함, 가장 최근 거래일(KRX 달력). 달력을 못 쓰면 주말만 건너뛴다.

    '며칠 지났는가'로 판단하면 연휴 뒤에는 느슨하고 평일 연속 실패에는 늦다. 거래일로 세면
    '지난 거래일에 예측이 있었는가'를 정확히 물을 수 있다.
    """
    day = pd.Timestamp(today)
    try:
        import exchange_calendars as xc
        calendar = xc.get_calendar("XKRX")
        return calendar.date_to_session(day, direction="previous").date()
    except Exception:
        while day.weekday() >= 5:
            day -= pd.Timedelta(days=1)
        return day.date()


def check_target(target, today, ledger_root, max_age_days=None):
    """(정상 여부, 메시지).

    기준은 '가장 최근 거래일에 그 날짜의 사전 예측이 있는가'다. 휴장일·연휴에는 그 거래일이
    자동으로 뒤로 밀리므로 오탐이 없고, 평일에 실행이 멈추면 바로 잡힌다. max_age_days 는
    쓰이지 않으며 옛 호출 호환을 위해 남겨 둔다.
    """
    path = Path(ledger_root) / target / "forecast_log.csv"
    if not path.exists():
        return False, f"{target}: 원장 파일이 없습니다 ({path})"
    log = pd.read_csv(path, dtype=str)
    if "prediction_date" not in log.columns:
        return False, f"{target}: 원장에 prediction_date 열이 없습니다"
    prospective = log["is_prospective"].astype(str).str.strip().str.lower().isin(("true", "1", "yes"))
    dates = pd.to_datetime(log.loc[prospective, "prediction_date"], errors="coerce").dropna()
    if dates.empty:
        return False, f"{target}: 사전 예측이 하나도 없습니다"
    latest = dates.max().date()
    session = last_trading_day(today)
    if latest >= session:
        return True, f"{target}: 최근 거래일({session}) 사전 예측이 있습니다"
    missed = sum(1 for d in pd.bdate_range(latest, session) if d.date() > latest)
    return False, (f"{target}: 최근 사전 예측이 {latest}인데 마지막 거래일은 {session}입니다"
                   f"(거래일 {missed}회 누락). 자동 실행이 멈췄을 수 있습니다 — "
                   "Actions 실행 기록을 확인하세요")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", default="samsung,sk_hynix")
    parser.add_argument("--ledger-root", default=str(ROOT / "forecast_history"))
    parser.add_argument("--max-age-days", type=int, default=None,
                        help="쓰이지 않는다(거래일 기준으로 바뀌었다). 옛 호출 호환용")
    args = parser.parse_args()
    today = datetime.now(KST).date()
    failures = []
    for target in [t.strip() for t in args.targets.split(",") if t.strip()]:
        ok, message = check_target(target, today, args.ledger_root)
        print(("OK   " if ok else "실패 ") + message)
        if not ok:
            failures.append(message)
    if failures:
        print("\n원장이 갱신되지 않았습니다. 아침 회차(06:22·07:25)와 3시간 회차가 모두 "
              "돌지 않았거나, 돌았지만 예측을 기록하지 못했습니다.", file=sys.stderr)
        raise SystemExit(1)
    print("\n모든 종목의 원장이 최신입니다.")


if __name__ == "__main__":
    main()

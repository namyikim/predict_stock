# -*- coding: utf-8 -*-
"""오늘 예측이 이미 원장에 있으면 다시 돌 필요가 없다 — 백업 스케줄의 판단.

GitHub의 cron은 밀리거나 아예 건너뛴다(2026-09-08 06:30 예정 실행이 실행 흔적도 없이
누락됐다). 그래서 아침 스케줄을 두 번 건다. 두 번째는 첫 번째가 이미 성공했으면 아무것도
하지 않아야 한다. 판단 기준은 '다음 거래일(KST)의 사전 예측이 원장에 있는가' 하나다.

    python tools/should_run_today.py --target samsung
        → GITHUB_OUTPUT 에 run=true|false 를 쓰고, 이유를 표준출력에 남긴다.
"""
import argparse
import csv
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


def next_trading_day(now):
    """지금 시점에서 예측 대상이 되는 다음 거래일(KRX 달력).

    원장의 prediction_date 는 '오늘'이 아니라 '다음 거래일'이다. 주말·휴장일에 돌린 실행의
    예측일은 다음 개장일이므로, 오늘 날짜와 비교하면 이미 만들어 둔 예측을 못 찾고 계속 다시
    만든다(2026-09-07 예측일에 8건이 쌓였다). 그래서 같은 기준으로 비교한다.

    09:00 전이면 오늘이 거래일일 때 오늘이 대상이고, 09:00 뒤에는 그날 예측 기회가 끝났으므로
    다음 거래일이 대상이다.
    """
    today = now.date()
    before_open = (now.hour, now.minute) < (9, 0)
    try:
        # 이 스크립트는 표준 라이브러리만으로 돌아야 한다(게이트는 가볍고 빨라야 한다).
        # 달력은 있으면 쓰고 없으면 주말 규칙으로 넘어간다.
        import exchange_calendars as xc
        calendar = xc.get_calendar("XKRX")
        stamp = today.isoformat()
        if before_open and calendar.is_session(stamp):
            return today
        return calendar.next_session(stamp).date()
    except Exception:
        if before_open and today.weekday() < 5:
            return today
        day = today + timedelta(days=1)
        while day.weekday() >= 5:       # 공휴일은 놓친다. 없는 것보다 낫다.
            day += timedelta(days=1)
        return day


def already_recorded(path, today, now=None):
    """다시 돌 필요가 없으면 True.

    09:00 KST 전에는 '오늘의 사전 예측'이 있어야 넘어간다 — 아직 제대로 된 예측을 만들 시간이
    남아 있기 때문이다. 09:00 이후에는 무엇을 만들어도 사전 예측이 될 수 없으므로, 그날 기록이
    하나라도 있으면 넘어간다. 그러지 않으면 3시간마다 전체 재계산이 반복된다.
    """
    now = now or datetime.now(KST)
    before_open = (now.hour, now.minute) < (9, 0)
    today = str(next_trading_day(now))          # 오늘이 아니라 '예측 대상 거래일'로 비교한다
    if not Path(path).exists():
        return False, "원장 파일이 없습니다"
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        rows = list(reader)
    for column in ("prediction_date", "is_prospective"):
        if column not in columns:
            return False, f"원장에 {column} 열이 없습니다"
    hit = []
    for row in rows:
        same_day = str(row.get("prediction_date", ""))[:10] == today
        prospective = str(row.get("is_prospective", "")).strip().lower() in ("true", "1", "yes")
        is_direction = "kind" not in columns or str(row.get("kind", "")) == "direction"
        if same_day and is_direction and (prospective or not before_open):
            hit.append(row)
    if not hit:
        return False, (f"{today} 사전 예측이 원장에 없습니다" if before_open
                       else f"{today} 기록이 원장에 없습니다(09:00 이후라 사전 예측은 못 만듭니다)")
    run_id = hit[0].get("run_id", "?") if "run_id" in columns else "?"
    label = "사전 예측이" if before_open else "기록이"
    return True, f"{today} {label} 이미 있습니다({len(hit)}행, run_id {run_id})"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--ledger-root", default="forecast_history")
    args = parser.parse_args()
    now = datetime.now(KST)
    today = str(next_trading_day(now))
    path = Path(args.ledger_root) / args.target / "forecast_log.csv"
    recorded, reason = already_recorded(path, today, now=now)
    print(f"{args.target}: {reason}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"run={'false' if recorded else 'true'}\n")


if __name__ == "__main__":
    main()

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
import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


def next_trading_day(now):
    """지금 시점에서 예측 대상이 되는 다음 거래일(KRX 달력).

    원장의 prediction_date 는 '오늘'이 아니라 '다음 거래일'이다. 주말·휴장일에 돌린 실행의
    예측일은 다음 개장일이므로, 오늘 날짜와 비교하면 이미 만들어 둔 예측을 못 찾고 계속 다시
    만든다(2026-09-07 예측일에 8건이 쌓였다). 그래서 같은 기준으로 비교한다.

    모델은 15:40 전에는 미완성인 오늘 봉을 버리고 오늘을 예측한다. 따라서 그 전이면 오늘이
    거래일일 때 오늘이 대상이고, 15:40부터 다음 거래일이 대상이다.
    """
    today = now.date()
    before_close = (now.hour, now.minute) < (15, 40)
    try:
        # 이 스크립트는 표준 라이브러리만으로 돌아야 한다(게이트는 가볍고 빨라야 한다).
        # 달력은 있으면 쓰고 없으면 주말 규칙으로 넘어간다.
        import exchange_calendars as xc
        calendar = xc.get_calendar("XKRX")
        stamp = today.isoformat()
        is_session = calendar.is_session(stamp)
        if before_close:
            return (today if is_session else
                    calendar.date_to_session(stamp, direction="next").date())
        return (calendar.next_session(stamp).date() if is_session else
                calendar.date_to_session(stamp, direction="next").date())
    except Exception:
        if before_close and today.weekday() < 5:
            return today
        day = today + timedelta(days=1)
        while day.weekday() >= 5:       # 공휴일은 놓친다. 없는 것보다 낫다.
            day += timedelta(days=1)
        return day


def published_ledger(path, ref="origin/main"):
    """원격 브랜치에 지금 올라가 있는 원장. 못 읽으면 작업 트리로 물러선다.

    작업 트리를 보면 안 된다. :22·:37·:52 재시도 실행은 거의 같은 시각에 만들어져 각자 '서로가
    원장을 올리기 전'의 커밋을 체크아웃한다. 그래서 셋 다 게이트를 통과해 같은 날 예측을 세 번
    다시 계산했다(2026-09-09: 24시간에 23회, 종목당 10~20분짜리 작업이다).
    """
    import subprocess
    if ref:
        try:
            subprocess.run(["git", "fetch", "--quiet", "--depth=1", "origin",
                            ref.split("/", 1)[-1]], check=True, timeout=60)
            out = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True,
                                 text=True, timeout=60)
            if out.returncode == 0:
                return out.stdout
        except Exception:
            pass
    local = Path(path)
    return local.read_text(encoding="utf-8-sig", errors="replace") if local.exists() else None


def already_recorded(path, today, now=None, ref=None):
    """다시 돌 필요가 없으면 True.

    15:40 KST 전에는 모델이 오늘을 예측하므로 '오늘의 사전 예측'이 있어야 넘어간다. 장 마감 뒤에는
    모델이 다음 거래일을 예측하므로 그 날짜의 기록이 하나라도 있으면 넘어간다.
    """
    now = now or datetime.now(KST)
    before_close = (now.hour, now.minute) < (15, 40)
    today = str(next_trading_day(now))          # 오늘이 아니라 '예측 대상 거래일'로 비교한다
    text = published_ledger(path, ref)
    if text is None:
        return False, "원장 파일이 없습니다"
    reader = csv.DictReader(io.StringIO(text))
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
        if same_day and is_direction and (prospective or not before_close):
            hit.append(row)
    if not hit:
        return False, (f"{today} 사전 예측이 원장에 없습니다" if before_close
                       else f"{today} 기록이 원장에 없습니다(장 마감 후 다음 거래일 대상)")
    run_id = hit[0].get("run_id", "?") if "run_id" in columns else "?"
    label = "사전 예측이" if before_close else "기록이"
    return True, f"{today} {label} 이미 있습니다({len(hit)}행, run_id {run_id})"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--ledger-root", default="forecast_history")
    parser.add_argument("--ref", default="origin/main",
                        help="이 ref 의 원장을 본다. 빈 문자열이면 작업 트리를 본다(테스트용)")
    args = parser.parse_args()
    now = datetime.now(KST)
    today = str(next_trading_day(now))
    path = Path(args.ledger_root) / args.target / "forecast_log.csv"
    recorded, reason = already_recorded(path, today, now=now, ref=args.ref or None)
    print(f"{args.target}: {reason}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"run={'false' if recorded else 'true'}\n")


if __name__ == "__main__":
    main()

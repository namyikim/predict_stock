# -*- coding: utf-8 -*-
"""오늘 예측이 이미 원장에 있으면 다시 돌 필요가 없다 — 백업 스케줄의 판단.

GitHub의 cron은 밀리거나 아예 건너뛴다(2026-09-08 06:30 예정 실행이 실행 흔적도 없이
누락됐다). 그래서 아침 스케줄을 두 번 건다. 두 번째는 첫 번째가 이미 성공했으면 아무것도
하지 않아야 한다. 판단 기준은 '오늘 날짜(KST)의 사전 예측이 원장에 있는가' 하나다.

    python tools/should_run_today.py --target samsung
        → GITHUB_OUTPUT 에 run=true|false 를 쓰고, 이유를 표준출력에 남긴다.
"""
import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

KST = timezone(timedelta(hours=9))


def already_recorded(path, today):
    """오늘 예측일로 기록된 사전 예측(방향)이 있으면 True."""
    if not Path(path).exists():
        return False, "원장 파일이 없습니다"
    log = pd.read_csv(path, dtype=str)
    for column in ("prediction_date", "is_prospective"):
        if column not in log.columns:
            return False, f"원장에 {column} 열이 없습니다"
    same_day = log["prediction_date"].astype(str).str.slice(0, 10) == today
    prospective = log["is_prospective"].astype(str).str.strip().str.lower().isin(("true", "1", "yes"))
    kind = log["kind"].astype(str) == "direction" if "kind" in log.columns else True
    hit = log[same_day & prospective & kind]
    if hit.empty:
        return False, f"{today} 사전 예측이 원장에 없습니다"
    run_id = hit["run_id"].iloc[0] if "run_id" in hit.columns else "?"
    return True, f"{today} 사전 예측이 이미 있습니다({len(hit)}행, run_id {run_id})"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--ledger-root", default="forecast_history")
    args = parser.parse_args()
    today = datetime.now(KST).date().isoformat()
    path = Path(args.ledger_root) / args.target / "forecast_log.csv"
    recorded, reason = already_recorded(path, today)
    print(f"{args.target}: {reason}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"run={'false' if recorded else 'true'}\n")


if __name__ == "__main__":
    main()

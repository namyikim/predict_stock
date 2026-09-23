# -*- coding: utf-8 -*-
"""관세청 10일 단위 잠정치 발표를 감지한다.

    python tools/customs_release_gate.py            # 종료 코드 0 = 갱신 필요, 1 = 필요 없음

1~10일치는 11일, 1~20일치는 21일, 1~말일치는 익월 1일에 나온다. 그 날 09:00 KST 이후에 보관본
(macro_history/customs_flash.csv)에 그 회차가 없으면 '갱신 필요'다. 휴일로 발표가 미뤄지면 받을 때까지
누락으로 남아 다음 회차가 다시 본다 — 그래서 3시간마다 확인해도 헛돌지 않는다.

장기 전망·실적 예상 워크플로는 원래 일요일·매달 7일에만 돌아 발표와 최대 열흘 어긋났다. 이 게이트가
발표일에 그 워크플로를 한 번 더 돌린다(2026-09-23). 무엇을 어떻게 반영할지는 각 도구가 정한다 —
실적 예상은 잠정치를 입력으로 쓰도록 검증됐고, 장기 전망은 월간 확정치 입력을 유지하며 잠정치를 표시만 한다.
"""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FLASH_PATH = ROOT / "macro_history" / "customs_flash.csv"
KST = timezone(timedelta(hours=9))
PUBLISH_HOUR = 9                       # 관세청은 오전에 낸다. 그 전에는 기대하지 않는다.


def _month_text(year, month):
    return f"{year:04d}-{month:02d}"


def expected_releases(today):
    """오늘까지 나왔어야 할 회차 [(월, 일수)]. 전달 말일치 + 이달의 10·20일치."""
    first_of_month = date(today.year, today.month, 1)
    last_month_end = first_of_month - timedelta(days=1)
    out = [(_month_text(last_month_end.year, last_month_end.month), 31)]
    if today.day >= 11:
        out.append((_month_text(today.year, today.month), 10))
    if today.day >= 21:
        out.append((_month_text(today.year, today.month), 20))
    return out


def cached_releases(path):
    if not Path(path).is_file():
        return set()
    frame = pd.read_csv(path)
    return {(str(m)[:7], int(d)) for m, d in zip(frame["month"], frame["days"])}


def missing_releases(path, today):
    have = cached_releases(path)
    return [r for r in expected_releases(today) if r not in have]


def should_rebuild(path=FLASH_PATH, now=None):
    now = now or datetime.now(KST)
    today = now.date()
    # 발표일 당일 09:00 KST 전에는 그 회차를 기대하지 않는다(00:22 회차가 헛돌지 않게).
    if now.hour < PUBLISH_HOUR:
        today = today - timedelta(days=1)
    missing = missing_releases(path, today)
    if not missing:
        return False, f"{now:%m-%d %H:%M} KST — 보관본이 최신입니다(누락 회차 없음)"
    text = ", ".join(f"{m} {'말일' if d == 31 else str(d) + '일'}치" for m, d in missing)
    return True, f"{now:%m-%d %H:%M} KST — 관세청 잠정치 누락: {text}. 장기 전망·실적 예상을 다시 만듭니다"


def main():
    rebuild, why = should_rebuild()
    print(why)
    return 0 if rebuild else 1


if __name__ == "__main__":
    sys.exit(main())

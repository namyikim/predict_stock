# -*- coding: utf-8 -*-
"""Colab(한국에서 실행)으로 갱신해야 하는 자료가 늦었는지 본다 — 늦었으면 사이트에 공지를 띄운다(2026-09-28).

한국 정부 API(KOSIS·관세청 data.go.kr)는 해외 IP 인 GitHub Actions 러너에서 자주 막힌다. 그때 저장소 보관본
(macro_history/*.csv)이 쓰이고, 보관본은 한국에서 Colab 으로 노트북을 한 번 돌리면 새로 채워진다. 그런데 언제
돌려야 하는지 알 길이 없었다(노트북 안의 경고는 45일 뒤에야, 종목 보고서 공지 한 줄로만 나왔다).

여기서는 자료마다 **실제 발표 일정**으로 '지금쯤 있어야 할 최신 기간'을 정하고, 보관본이 그보다 늦으면 공지한다.
발표 직후 며칠은 여유를 둔다(자동 실행이 한 번쯤 막혀도 다음 실행이 받을 수 있다).

    python tools/colab_freshness.py                 # 판정만 출력
    python tools/colab_freshness.py --publish       # docs/colab_status.json 발행(바뀐 날만 커밋)

docs/colab_status.json 을 메인 목록과 종목 보고서가 읽어 공지를 띄운다(페이지를 다시 만들지 않아도 된다).
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
STATUS_PATH = "docs/colab_status.json"
COLAB_URL = ("https://colab.research.google.com/github/namyikim/predict_stock/blob/main/"
             "samsung_direction_model_colab.ipynb")


def month_back(today, n):
    return (pd.Period(today, freq="M") - n).strftime("%Y-%m")


def expected_monthly(today, release_day, grace_months_before):
    """발표일(release_day, 다음 달 며칠)+여유가 지났으면 지난달, 아니면 그 전 달이 있어야 한다."""
    return month_back(today, grace_months_before if today.day >= release_day else grace_months_before + 1)


def expected_flash(today):
    """관세청 수출 속보: 1~10일분은 11일 무렵, 1~20일분은 21일 무렵(+여유 4일)."""
    if today.day >= 25:
        return (pd.Period(today, freq="M").strftime("%Y-%m"), 20)
    if today.day >= 15:
        return (pd.Period(today, freq="M").strftime("%Y-%m"), 10)
    return (month_back(today, 1), 20)


def previous_sessions(today, back):
    """오늘 전 back 번째 KRX 거래일. 달력이 없으면 주말만 뺀다."""
    try:
        import exchange_calendars as xc
        cal = xc.get_calendar("XKRX")
        sessions = cal.sessions_in_range(pd.Timestamp(today) - pd.Timedelta(days=40), pd.Timestamp(today) - pd.Timedelta(days=1))
        return pd.Timestamp(sessions[-back]).date()
    except Exception:
        day, k = today, 0
        while k < back:
            day -= timedelta(days=1)
            if day.weekday() < 5:
                k += 1
        return day


# (보관본 파일, 이름, 종류). 월별 자료의 발표일: 수출입(관세청 확정)은 다음 달 15일 무렵 → 25일부터 지난달을 기대,
# 산업활동동향(선행지수 순환변동치)은 다음 달 말 → 8일부터 두 달 전을 기대.
SOURCES = (
    ("semiconductor_exports.csv", "반도체 수출액(KOSIS)", ("monthly", 25, 1)),
    ("customs_exports.csv", "반도체 수출액(관세청 품목별)", ("monthly", 25, 1)),
    ("customs_quantity.csv", "반도체 수출 단가·물량(관세청)", ("monthly", 25, 1)),
    ("leading_cycle.csv", "경기선행지수 순환변동치(KOSIS)", ("monthly", 8, 2)),
    ("customs_flash.csv", "수출 속보 1~10일·1~20일(관세청)", ("flash",)),
    ("investor_flows_005930.csv", "삼성전자 외국인·기관 수급", ("daily", 4)),
    ("investor_flows_000660.csv", "SK하이닉스 외국인·기관 수급", ("daily", 4)),
)


def latest_in(path, kind):
    frame = pd.read_csv(path, dtype=str)
    if kind == "flash":
        last = frame.sort_values(["month", "days"], key=lambda s: s if s.name == "month" else s.astype(int)).iloc[-1]
        return (str(last["month"])[:7], int(last["days"]))
    column = "month" if "month" in frame.columns else "date"
    value = pd.to_datetime(frame[column], errors="coerce").max()
    return value.strftime("%Y-%m") if kind == "monthly" else value.date()


def check(today=None, root=ROOT):
    today = today or datetime.now(KST).date()
    items = []
    for name, label, rule in SOURCES:
        path = Path(root) / "macro_history" / name
        if not path.exists():
            continue
        kind = rule[0]
        have = latest_in(path, kind)
        if kind == "monthly":
            want = expected_monthly(today, rule[1], rule[2])
            late = have < want
            have_text, want_text = f"{have}", f"{want}"
        elif kind == "flash":
            want = expected_flash(today)
            late = have < want
            have_text, want_text = f"{have[0]} 1~{have[1]}일", f"{want[0]} 1~{want[1]}일"
        else:
            want = previous_sessions(today, rule[1])
            late = have < want
            have_text, want_text = str(have), f"{want} 이후"
        items.append({"file": name, "label": label, "have": have_text, "expected": want_text, "late": bool(late)})
    late = [item for item in items if item["late"]]
    return {"as_of": today.isoformat(), "needed": bool(late), "late": late, "checked": items,
            "colab_url": COLAB_URL,
            "how": "한국에서 Colab 으로 노트북을 열고 '런타임 → 모두 실행'을 한 번 누르세요. Colab 보안 비밀에 "
                   "GITHUB_TOKEN(보관본 저장용)과 KOSIS_API_KEY·DATA_GO_KR_KEY 가 있어야 합니다. 예측 원장에는 기록하지 않고 "
                   "참고자료 보관본만 새로 채웁니다."}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    status = check()
    for item in status["checked"]:
        print(f"{'⚠️ 늦음' if item['late'] else '정상 '} {item['label']}: 보관본 {item['have']} · 기대 {item['expected']}")
    print("Colab 실행 필요" if status["needed"] else "Colab 실행 필요 없음")
    text = json.dumps(status, ensure_ascii=False, indent=1) + "\n"
    (ROOT / "runs").mkdir(exist_ok=True)
    (ROOT / "runs" / "colab_status.json").write_text(text, encoding="utf-8")
    if args.publish:
        sys.path.insert(0, str(ROOT / "tools"))
        import github_pages
        token = github_pages.token()
        with github_pages.batch(f"status: Colab 갱신 {'필요' if status['needed'] else '불필요'} ({status['as_of']})", token):
            github_pages.publish(STATUS_PATH, text, token, "status: colab")


if __name__ == "__main__":
    main()

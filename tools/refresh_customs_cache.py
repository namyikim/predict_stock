# -*- coding: utf-8 -*-
"""관세청 수출입실적을 직접 받아 확인하고, 저장소 보관본(macro_history/customs_exports.csv)을 갱신한다.

노트북 전체 실행(수십 분)을 하지 않고 이 자료만 받아 볼 때 쓴다. 관세청 API 가 살아 있는지,
1년 한도 분할이 제대로 도는지 확인하는 용도이기도 하다.

  로컬(한국):
      $env:DATA_GO_KR_KEY = '<포털의 인코딩 인증키>'
      python tools/refresh_customs_cache.py                 # 받아서 확인만
      python tools/refresh_customs_cache.py --publish       # 보관본까지 갱신(GITHUB_TOKEN 필요)

  Colab(한 셀):
      !git clone -q https://github.com/namyikim/predict_stock.git /content/predict_stock
      import sys; sys.path.insert(0, '/content/predict_stock/tools')
      import refresh_customs_cache as rc; rc.main(['--publish'])

  Colab 에서는 반드시 이렇게 **같은 프로세스 안에서** 불러야 한다. `!python tools/...` 처럼
  하위 프로세스로 돌리면 google.colab.userdata 에 접근할 수 없어 인증키를 못 읽는다.

인증키는 값을 찍지 않는다. 어느 키가 들어갔는지는 지문(길이·앞뒤 4자)으로만 확인한다.
"""
import argparse
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import github_pages  # noqa: E402
from data_sources.exports import (  # noqa: E402
    CUSTOMS_HS, CUSTOMS_MAX_MONTHS, data_go_kr_key, fetch_customs_exports, key_fingerprint,
    month_windows, reconcile_customs,
)

CACHE_PATH = "macro_history/customs_exports.csv"
KOSIS_CACHE = "macro_history/semiconductor_exports.csv"
RAW_BASE = "https://raw.githubusercontent.com/namyikim/predict_stock/main/"
DEFAULT_MONTHS = 36        # 보관본이 덮는 기간. KOSIS 와 겹치는 달이 6개 이상이어야 검증된다.


def github_token():
    """GITHUB_TOKEN. 환경변수에 없으면 Colab 비밀값에서 찾는다. 없으면 None."""
    value = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if value:
        return value
    try:
        from google.colab import userdata
        return (userdata.get("GITHUB_TOKEN") or "").strip() or None
    except Exception:
        return None


def read_public(path):
    """공개 저장소의 텍스트 파일. 토큰이 필요 없다. 없거나 못 읽으면 None."""
    import urllib.request
    try:
        with urllib.request.urlopen(RAW_BASE + path, timeout=30) as response:
            return response.read().decode("utf-8")
    except Exception:
        return None


def as_frame(text):
    import io
    return pd.read_csv(io.StringIO(text)) if text else None


def describe(frame, label):
    months = pd.to_datetime(frame["month"])
    print(f"  {label}: {len(frame)}개월 · {months.min():%Y-%m} ~ {months.max():%Y-%m}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--months", type=int, default=DEFAULT_MONTHS,
                        help=f"오늘로부터 몇 개월 전까지 받을지(기본 {DEFAULT_MONTHS})")
    parser.add_argument("--start", help="시작 월을 직접 지정(예: 2015-01). 주면 --months 를 무시한다")
    parser.add_argument("--publish", action="store_true",
                        help=f"받은 값으로 {CACHE_PATH} 를 덮어쓴다(GITHUB_TOKEN 필요)")
    args = parser.parse_args(argv)

    key = data_go_kr_key()
    if not key:
        print("✋ DATA_GO_KR_KEY 가 없습니다.")
        print("   로컬: 환경변수로 넣으세요. Colab: 왼쪽 열쇠 아이콘(보안 비밀)에 DATA_GO_KR_KEY 를 넣고")
        print("   노트북 접근을 켠 뒤, 이 스크립트를 같은 프로세스에서 부르세요(rc.main([...])).")
        return 1

    end = pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None).normalize()
    start = (pd.Timestamp(args.start) if args.start
             else (end.replace(day=1) - pd.DateOffset(months=args.months - 1)))
    windows = month_windows(start, end)
    print(f"관세청 조회 — {start:%Y-%m} ~ {end:%Y-%m} · HS {'·'.join(CUSTOMS_HS)}")
    print(f"  {key_fingerprint(key)}")
    print(f"  1년 한도 때문에 {CUSTOMS_MAX_MONTHS}개월 창 {len(windows)}개로 나눠 부릅니다 "
          f"(HS {len(CUSTOMS_HS)}개 × {len(windows)} = 호출 {len(CUSTOMS_HS) * len(windows)}건)")

    try:
        customs = fetch_customs_exports(start, end, key)
    except Exception as exc:
        print("\n✗ 실패:", str(exc)[:400])
        print("\n  메시지에 '관세청 API 오류'가 있으면 서버가 응답한 것이므로 연결·인증키 문제가 아닙니다.")
        print("  'SERVICE_KEY_IS_NOT_REGISTERED' 나 타임아웃이면 해외 IP 차단일 수 있습니다 — 한국에서 다시 돌려 보세요.")
        return 1

    print("\n✓ 수신 성공")
    describe(customs, "받은 자료")
    tail = customs.sort_values("month").tail(6).copy()
    tail["억달러"] = (tail["value"] / 1e8).round(1)
    print("  최근 6개월(HS 8541+8542 수출액):")
    for _, row in tail.iterrows():
        print(f"    {pd.Timestamp(row['month']):%Y-%m}  {row['억달러']:>8.1f}억 달러")

    # KOSIS 확정치와 크기를 맞춰 본다. 품목 정의나 단위가 다르면 여기서 드러난다.
    kosis = as_frame(read_public(KOSIS_CACHE))
    if kosis is not None:
        ok, diag = reconcile_customs(kosis, customs)
        note = (f"배율 중앙값 {diag['ratio_median']:.3f}" if diag.get("ratio_median") is not None
                else diag.get("reason", ""))
        print(f"\n  KOSIS 확정치 대조: {'통과' if ok else '미통과'} — 겹치는 달 {diag['overlap']}개, {note}")
        if not ok:
            print("  ⚠️ 이대로 올리면 나우캐스트가 이 계열을 쓰지 않습니다.")
    else:
        print(f"\n  (KOSIS 보관본 {KOSIS_CACHE} 을 읽지 못해 대조는 건너뜁니다)")

    previous = as_frame(read_public(CACHE_PATH))
    if previous is not None:
        describe(previous, "현재 보관본")
        new_months = sorted(set(pd.to_datetime(customs["month"]).dt.strftime("%Y-%m"))
                            - set(pd.to_datetime(previous["month"]).dt.strftime("%Y-%m")))
        print(f"  보관본에 없던 달: {', '.join(new_months) if new_months else '없음'}")
    else:
        print(f"  현재 보관본: 없음({CACHE_PATH} 이 아직 저장소에 없습니다)")

    if not args.publish:
        print(f"\n보관본은 그대로 두었습니다. 갱신하려면 --publish 를 붙이세요.")
        return 0

    tok = github_token()
    if not tok:
        print("\n✋ GITHUB_TOKEN 이 없어 보관본을 갱신하지 못했습니다.")
        print("   Colab 이라면 보안 비밀에 GITHUB_TOKEN 을 넣고 노트북 접근을 켜세요.")
        return 1
    sha = github_pages.publish(CACHE_PATH, customs.to_csv(index=False), tok,
                               f"macro: customs_exports ({len(customs)}개월, refresh_customs_cache)")
    print(f"\n✓ 보관본 갱신 {CACHE_PATH} @ {sha}")
    print("  다음 Actions 실행부터 이 값을 씁니다(관세청 직접 조회가 실패해도 여기서 이어집니다).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

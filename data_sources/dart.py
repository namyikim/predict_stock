"""DART 공시 목록: 이벤트 표시(실적 시즌·잠정실적 등)와 보고서 참고."""
import hashlib
import io
import json
import os
import re
from pathlib import Path
import random
import time
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd
from data_sources._common import *  # noqa: F401,F403




# ---------------------------------------------------------------------------
# DART 공시 목록 — 이벤트 표시와 보고서 참고
# ---------------------------------------------------------------------------
# 대형주는 공시가 나오면 몇 분 안에 가격에 반영되므로 공시 자체를 다음 날 방향의 특징으로 쓸
# 이유는 약하다. 대신 두 가지에 쓴다: (1) 실적 발표 같은 예정된 이벤트가 있는 날을 원장에
# 표시해 나중에 '이벤트일'과 '평일'을 나눠 볼 수 있게 하고, (2) 어제 나온 공시 목록을 보고서에
# 두어 예측이 빗나간 날 그 이유를 바로 볼 수 있게 한다.
DART_CORP = {"005930": "00126380", "000660": "00164779"}   # 삼성전자, SK하이닉스


EVENT_PATTERNS = (
    (re.compile(r"영업\s*\(?\s*잠정\s*\)?\s*실적"), "잠정실적"),
    (re.compile(r"분기보고서|반기보고서|사업보고서"), "정기보고서"),
    (re.compile(r"현금·?현물배당|배당"), "배당"),
    (re.compile(r"자기주식|자사주"), "자사주"),
    (re.compile(r"단일판매|공급계약"), "공급계약"),
    (re.compile(r"유상증자|무상증자|분할|합병"), "자본변동"),
)




def dart_key_optional():
    key = os.environ.get("DART_API_KEY")
    if not key:
        try:
            from google.colab import userdata
            key = userdata.get("DART_API_KEY")
        except Exception:
            pass
    return key




def fetch_dart_disclosures(corp_code, key, start, end, max_pages=3):
    """기간 내 공시 목록. [{report_nm, rcept_dt, rcept_no, url}] 최신순."""
    from urllib.parse import urlencode
    out, page = [], 1
    while page <= max_pages:
        url = ("https://opendart.fss.or.kr/api/list.json?" + urlencode({
            "crtfc_key": key, "corp_code": corp_code,
            "bgn_de": pd.Timestamp(start).strftime("%Y%m%d"), "end_de": pd.Timestamp(end).strftime("%Y%m%d"),
            "page_no": str(page), "page_count": "100"}))
        with open_url(url, accept="application/json") as response:
            payload = json.loads(response.read().decode("utf-8"))
        status = payload.get("status")
        if status == "013":
            break
        if status != "000":
            raise RuntimeError(f"DART 공시목록 오류 {status}: {payload.get('message', '')}")
        for row in payload.get("list", []):
            out.append({"report_nm": str(row.get("report_nm", "")).strip(),
                        "rcept_dt": str(row.get("rcept_dt", "")),
                        "rcept_no": str(row.get("rcept_no", "")),
                        "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={row.get('rcept_no', '')}"})
        if page >= int(payload.get("total_page", 1) or 1):
            break
        page += 1
    return sorted(out, key=lambda d: (d["rcept_dt"], d["rcept_no"]), reverse=True)




def classify_disclosure(report_nm):
    for pattern, label in EVENT_PATTERNS:
        if pattern.search(report_nm):
            return label
    return ""




def earnings_season(date, days_after_quarter_end=14):
    """분기 종료 후 14일 안이면 '실적 시즌'. 두 회사의 잠정실적이 이 안에 나온다."""
    date = pd.Timestamp(date).normalize()
    quarter_start = pd.Timestamp(year=date.year, month=((date.month - 1) // 3) * 3 + 1, day=1)
    return 0 <= (date - quarter_start).days < days_after_quarter_end




def event_flags(date, disclosures, lookback_days=1):
    """예측일에 붙일 이벤트 표시. ['실적시즌', '공시:잠정실적', ...] — 없으면 빈 목록."""
    date = pd.Timestamp(date).normalize()
    flags = []
    if earnings_season(date):
        flags.append("실적시즌")
    since = (date - pd.Timedelta(days=lookback_days)).strftime("%Y%m%d")
    until = date.strftime("%Y%m%d")
    labels = {classify_disclosure(d["report_nm"]) for d in disclosures if since <= d["rcept_dt"] <= until}
    flags.extend(f"공시:{label}" for label in sorted(labels) if label)
    return flags

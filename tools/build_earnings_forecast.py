# -*- coding: utf-8 -*-
"""분기 영업이익 나우캐스트 — 월별 반도체 수출액으로 삼성전자·SK하이닉스의 이번 분기 영업이익을 추정한다.

주가 예측과 성격이 다르다. 주가는 미래를 맞히는 일이지만 이것은 **이미 벌어지고 있는 분기의
결과를 아직 발표되지 않았을 뿐인 상태에서 추정**하는 일(나우캐스트)이다. 메모리 두 회사의
영업이익은 D램·낸드 가격과 물량으로 결정되고, 한국 반도체 수출액은 같은 것을 매달 집계한다.
그래서 여기서는 상관이 예측력으로 이어질 여지가 실제로 있다 — 다만 그 여지를 검증으로 확인한다.

설계
- **학습과 서빙의 시점을 맞춘다.** 분기가 끝나기 전에 추정하므로, 그 분기의 앞 k개월 수출만 쓴다.
  과거 분기도 똑같이 앞 k개월로 특징을 만들어 학습한다(k는 이번 분기에 실제로 확보된 월 수).
- **기준선을 이겨야 숫자를 낸다.** 직전 분기 영업이익(랜덤워크)과 4분기 전(계절 나이브) 둘 다를
  워크포워드 MAE에서 이겨야 점 추정을 발표한다. 못 이기면 구간과 기준선만 보여 준다.
- 영업이익은 음수가 될 수 있다(2023년 두 회사 모두 적자). 로그를 쓰지 않고 원 단위로 다룬다.
- 환율을 넣는다. 수출은 달러, 영업이익은 원이다.

영업이익 이력은 DART 공시에서 받거나(권장) CSV로 넣는다.
    macro_inputs/operating_profit_<종목>.csv : quarter,value  (예: 2026-Q2,4210000000000)

    python tools/build_earnings_forecast.py --target samsung --out runs/earnings --dump
    python tools/build_earnings_forecast.py --target samsung --out runs/earnings --publish
"""
import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import github_pages  # noqa: E402
from macro_utils import (  # noqa: E402
    cli_features, data_go_kr_key, fetch_customs_exports, load_cli, load_macro_data,
    customs_scale, dram_spot_summary, error_detail, load_dram_spot, load_tsmc_revenue,
    merge_customs_exports, reconcile_customs, tsmc_features,
)

KST = timezone(timedelta(hours=9))
TARGETS = {
    "samsung": {"name": "삼성전자", "corp_code": "00126380", "ticker": "005930.KS"},
    "sk_hynix": {"name": "SK하이닉스", "corp_code": "00164779", "ticker": "000660.KS"},
}
TRILLION = 1e12
MIN_TRAIN_QUARTERS = 16     # 이보다 적으면 학습하지 않는다(계절성 4분기 × 4주기)
FIRST_TEST_QUARTER = "2016Q1"


# ---------------------------------------------------------------------------
# 영업이익 이력
# ---------------------------------------------------------------------------
def read_operating_profit_csv(path):
    """quarter,value CSV. quarter는 2026-Q3 / 2026Q3 / 2026-09 형식을 받는다. value 단위는 원."""
    frame = pd.read_csv(path, dtype=str)
    if not {"quarter", "value"}.issubset(frame.columns):
        raise ValueError(f"{Path(path).name}: quarter,value 두 열이 필요합니다.")

    def to_period(text):
        text = str(text).upper().replace(" ", "").replace("-", "")
        if "Q" in text:
            return pd.Period(text, freq="Q")
        return pd.Period(pd.Timestamp(str(text)), freq="Q")

    periods = pd.PeriodIndex([to_period(t) for t in frame["quarter"]], freq="Q")
    value = pd.to_numeric(frame["value"].astype(str).str.replace(",", ""), errors="coerce")
    out = pd.Series(value.to_numpy(), index=periods, name="operating_profit").dropna().sort_index()
    if out.index.has_duplicates:
        raise ValueError("영업이익 CSV에 같은 분기가 중복됩니다.")
    if out.empty:
        raise ValueError("영업이익 CSV에 유효한 값이 없습니다.")
    return out


def dart_key():
    key = os.environ.get("DART_API_KEY")
    if not key:
        try:
            from google.colab import userdata
            key = userdata.get("DART_API_KEY")
        except Exception:
            pass
    return key


def _dart_request(key, params, retries=3):
    """DART OpenAPI. URL에 키가 들어가므로 예외에는 종류·코드만 남긴다."""
    import time
    import random
    from urllib.parse import urlencode
    from urllib.request import urlopen
    url = ("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json?"
           + urlencode({"crtfc_key": key, **params}))
    for attempt in range(retries):
        try:
            with urlopen(url, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except Exception as exc:
            detail = error_detail(exc)
            if attempt == retries - 1:
                raise RuntimeError(f"DART 조회 실패({detail}). 키·연결을 확인하거나 "
                                   "macro_inputs/operating_profit_<종목>.csv를 사용하세요.") from None
            time.sleep(3 * (2 ** attempt) + random.uniform(0, 3))
    status = payload.get("status")
    if status == "013":          # 조회된 데이터 없음(아직 미공시 분기)
        return []
    if status != "000":
        raise RuntimeError(f"DART 응답 오류 {status}: {payload.get('message', '')}")
    return payload.get("list", [])


# 보고서 코드: 1분기·반기·3분기·사업보고서. 누적 금액이 오므로 차분해서 분기 값을 만든다.
DART_REPORTS = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}


def _pick_operating_profit(rows):
    """연결 손익계산서의 영업이익 누적 금액(원)."""
    best = None
    for row in rows:
        name = str(row.get("account_nm", "")).replace(" ", "")
        if name not in ("영업이익", "영업이익(손실)"):
            continue
        if str(row.get("fs_div", "")) not in ("CFS", ""):     # 연결 우선
            continue
        raw = str(row.get("thstrm_add_amount") or row.get("thstrm_amount") or "").replace(",", "")
        if raw in ("", "-"):
            continue
        try:
            best = float(raw)
        except ValueError:
            continue
        break
    return best


def fetch_operating_profit_dart(corp_code, key, start_year=2010, end_year=None):
    """분기 영업이익(원). 누적 공시를 차분해 3개월 값으로 만든다."""
    end_year = end_year or datetime.now(KST).year
    cumulative = {}
    for year in range(start_year, end_year + 1):
        for quarter, report in DART_REPORTS.items():
            rows = _dart_request(key, {"corp_code": corp_code, "bsns_year": str(year),
                                       "reprt_code": report, "fs_div": "CFS"})
            value = _pick_operating_profit(rows)
            if value is not None:
                cumulative[pd.Period(f"{year}Q{quarter}", freq="Q")] = value
    if not cumulative:
        raise RuntimeError("DART에서 영업이익을 찾지 못했습니다. corp_code와 공시 여부를 확인하세요.")
    series = pd.Series(cumulative).sort_index()
    quarterly = {}
    for period, value in series.items():
        if period.quarter == 1:
            quarterly[period] = value
            continue
        previous = period - 1
        if previous in series.index:            # 누적 - 직전 누적
            quarterly[period] = value - series[previous]
    return pd.Series(quarterly, name="operating_profit").sort_index()


# ---------------------------------------------------------------------------
# 잠정실적 공시 — 확정 재무제표보다 5주쯤 빠르다
# ---------------------------------------------------------------------------
# 분기보고서(확정치)는 분기 종료 후 45일쯤 뒤에 나온다. 그런데 두 회사는 분기가 끝나고 일주일
# 안팎에 '연결재무제표기준 영업(잠정)실적'을 공시한다. 나우캐스트를 그만큼 빨리 채점할 수 있다.
# 다만 잠정치는 공시 본문 표를 읽어야 해서 확정치보다 불안정하다. 그래서 단위를 못 읽거나 값이
# 직전 분기의 0.2~5배를 벗어나면 쓰지 않고, 확정치가 나오면 언제든 그것으로 덮어쓴다.
PROVISIONAL_PATTERN = re.compile(r"영업\s*\(?\s*잠정\s*\)?\s*실적")


def dart_disclosures(corp_code, key, start, end, max_pages=5):
    """기간 내 공시 목록. list.json은 status/list 구조라 따로 다룬다."""
    import json as _json
    import random
    import time
    from urllib.parse import urlencode
    from urllib.request import urlopen
    out, page = [], 1
    while page <= max_pages:
        url = ("https://opendart.fss.or.kr/api/list.json?" + urlencode({
            "crtfc_key": key, "corp_code": corp_code,
            "bgn_de": pd.Timestamp(start).strftime("%Y%m%d"),
            "end_de": pd.Timestamp(end).strftime("%Y%m%d"),
            "page_no": str(page), "page_count": "100"}))
        payload = None
        for attempt in range(3):
            try:
                with urlopen(url, timeout=60) as response:
                    payload = _json.loads(response.read().decode("utf-8"))
                break
            except Exception as exc:
                if attempt == 2:
                    raise RuntimeError(f"DART 공시목록 조회 실패({type(exc).__name__} "
                                       f"{getattr(exc, 'code', '')})") from None
                time.sleep(2 * (2 ** attempt) + random.uniform(0, 2))
        status = payload.get("status")
        if status == "013":                 # 조회 결과 없음
            break
        if status != "000":
            raise RuntimeError(f"DART 공시목록 오류 {status}: {payload.get('message', '')}")
        out.extend(payload.get("list", []))
        if page >= int(payload.get("total_page", 1) or 1):
            break
        page += 1
    return out


def find_provisional(disclosures):
    """영업(잠정)실적 공시만 접수일 최신순으로."""
    hits = [d for d in disclosures if PROVISIONAL_PATTERN.search(str(d.get("report_nm", "")))]
    return sorted(hits, key=lambda d: str(d.get("rcept_dt", "")), reverse=True)


def parse_provisional_amount(text):
    """공시 본문에서 영업이익 당기실적(원)을 뽑는다. 확실한 경우에만 값을 돌려준다."""
    plain = re.sub(r"<[^>]+>", " ", text).replace("&nbsp;", " ")
    units = ((r"백만\s*원", 1e6), (r"억\s*원", 1e8), (r"천\s*원", 1e3))
    unit = next((mult for pattern, mult in units
                 if re.search(r"단위\s*[:：(]?\s*" + pattern, plain)), None)
    if unit is None:
        return None                          # 단위를 모르면 쓰지 않는다
    match = re.search(r"영업\s*이익(.{0,400})", plain, re.S)
    if not match:
        return None
    for raw in re.findall(r"-?[\d,]{2,}", match.group(1)):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if abs(value) >= 10:                 # 표 머리글의 연도·분기 숫자를 건너뛴다
            return value * unit
    return None


def fetch_provisional_profit(corp_code, key, quarter, previous_value=None):
    """(값, 정보). 잠정실적 공시를 찾아 영업이익을 읽는다. 못 읽거나 이상하면 (None, 사유)."""
    import io
    import zipfile
    from urllib.parse import urlencode
    from urllib.request import urlopen
    end = pd.Period(quarter, freq="Q").end_time
    try:
        disclosures = dart_disclosures(corp_code, key, end, end + pd.Timedelta(days=75))
    except Exception as exc:
        return None, {"reason": str(exc)}
    hits = find_provisional(disclosures)
    if not hits:
        return None, {"reason": "잠정실적 공시가 아직 없습니다"}
    top = hits[0]
    info = {"report_nm": top.get("report_nm"), "rcept_dt": top.get("rcept_dt"),
            "rcept_no": top.get("rcept_no")}
    try:
        url = "https://opendart.fss.or.kr/api/document.xml?" + urlencode(
            {"crtfc_key": key, "rcept_no": top["rcept_no"]})
        with urlopen(url, timeout=90) as response:
            blob = response.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            raw = archive.read(archive.namelist()[0])
        text = raw.decode("utf-8", errors="ignore")
        if "영업" not in text:
            text = raw.decode("euc-kr", errors="ignore")
    except Exception as exc:
        info["reason"] = f"본문을 받지 못했습니다({type(exc).__name__})"
        return None, info
    value = parse_provisional_amount(text)
    if value is None:
        info["reason"] = "본문에서 영업이익을 찾지 못했습니다"
        return None, info
    if previous_value and previous_value > 0 and not (0.2 <= value / previous_value <= 5.0):
        info["reason"] = f"직전 분기의 {value / previous_value:.1f}배라 잘못 읽은 값으로 봅니다"
        return None, info
    info["value"] = value
    return value, info


def load_operating_profit(storage, target):
    """(분기 시계열, 출처). CSV가 있으면 그것을 쓰고, 없으면 DART에서 받는다."""
    path = Path(storage) / "macro_inputs" / f"operating_profit_{target}.csv"
    if path.exists():
        return read_operating_profit_csv(path), "user_csv"
    key = dart_key()
    if not key:
        raise RuntimeError("영업이익 이력이 없습니다. DART_API_KEY를 등록하거나 "
                           f"{path}에 quarter,value CSV를 저장하세요.")
    return fetch_operating_profit_dart(TARGETS[target]["corp_code"], key), "DART_API"


# ---------------------------------------------------------------------------
# 관세청 수출 속보(1~10일·1~20일)로 월 확정치를 앞당긴다
# ---------------------------------------------------------------------------
# 관세청은 매달 11일·21일경 그 달 1~10일·1~20일 수출입 현황을 발표하고 반도체 증감률을 따로 적는다.
# KOSIS 품목별 월 확정치는 그보다 2~5주 늦다. 속보의 반도체 YoY로 그 달을 잠정 추정하면
# 나우캐스트에 한 달을 앞당겨 넣을 수 있다.
#   추정 = 전년 동월 확정치 × (1 + 속보 반도체 YoY)
# 입력: macro_inputs/exports_flash.csv  (month,days,semiconductor_yoy,released)
#   예: 2026-09,20,0.31,2026-09-21   ← 9월 1~20일 반도체 수출 전년 대비 +31%
# 확정치가 KOSIS에 들어오면 그 달의 속보는 자동으로 무시된다(확정치 우선).
def load_exports_flash(storage):
    path = Path(storage) / "macro_inputs" / "exports_flash.csv"
    if not path.exists():
        return pd.DataFrame(columns=["month", "days", "semiconductor_yoy", "released"])
    frame = pd.read_csv(path, dtype=str)
    needed = {"month", "days", "semiconductor_yoy"}
    if not needed.issubset(frame.columns):
        raise ValueError(f"{path.name}: {needed} 열이 필요합니다.")
    out = pd.DataFrame({
        "month": pd.to_datetime(frame["month"].astype(str).str.replace(r"^(\d{4})[-./]?(\d{2})$", r"\1-\2", regex=True) + "-01"),
        "days": pd.to_numeric(frame["days"], errors="coerce"),
        "semiconductor_yoy": pd.to_numeric(frame["semiconductor_yoy"].astype(str).str.rstrip("%"), errors="coerce"),
        "released": frame.get("released", pd.Series([None] * len(frame))),
    }).dropna(subset=["month", "semiconductor_yoy"])
    # 같은 달에 10일치와 20일치가 다 있으면 더 긴 쪽을 쓴다.
    out = out.sort_values(["month", "days"]).drop_duplicates("month", keep="last")
    out.loc[out["semiconductor_yoy"].abs() > 5, "semiconductor_yoy"] /= 100.0   # 31 → 0.31
    return out.reset_index(drop=True)


def apply_exports_flash(exports, flash):
    """확정치가 없는 달을 속보로 잠정 추정해 덧붙인다. (계열, 적용된 달 목록)"""
    if flash is None or flash.empty:
        return exports, []
    out = exports.copy()
    applied = []
    for _, row in flash.iterrows():
        month = pd.Timestamp(row["month"])
        if month in out.index and pd.notna(out.loc[month]):
            continue                                   # 확정치 우선
        base = month - pd.DateOffset(years=1)
        if base not in out.index or pd.isna(out.loc[base]):
            continue
        out.loc[month] = float(out.loc[base]) * (1.0 + float(row["semiconductor_yoy"]))
        applied.append({"month": month.strftime("%Y-%m"), "days": int(row["days"]) if pd.notna(row["days"]) else None,
                        "yoy": float(row["semiconductor_yoy"])})
    return out.sort_index(), applied


# ---------------------------------------------------------------------------
# 특징: 분기의 '앞 k개월'만 쓴다
# ---------------------------------------------------------------------------
def monthly_usdkrw(cache_dir, fetch=True):
    path = Path(cache_dir) / "usdkrw_monthly.csv"
    if fetch:
        import yfinance as yf
        history = yf.Ticker("KRW=X").history(start="2005-01-01", auto_adjust=False)
        if history is None or history.empty:
            raise RuntimeError("원/달러 환율을 받지 못했습니다.")
        history.index = pd.to_datetime(history.index).tz_localize(None)
        monthly = history["Close"].astype(float).resample("MS").mean().dropna()
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        monthly.to_csv(path, header=["usdkrw"])
        return monthly
    if not path.exists():
        raise RuntimeError(f"환율 캐시가 없습니다: {path}")
    return pd.read_csv(path, index_col=0, parse_dates=True)["usdkrw"]


def first_k_months(monthly, k):
    """분기별로 앞 k개월만 평균낸다. 분기 안에 k개월이 다 없으면 NaN."""
    frame = pd.DataFrame({"value": monthly})
    frame["quarter"] = pd.PeriodIndex(frame.index, freq="Q")
    frame["rank"] = frame.groupby("quarter").cumcount() + 1
    picked = frame[frame["rank"] <= k]
    counts = picked.groupby("quarter")["value"].count()
    means = picked.groupby("quarter")["value"].mean()
    return means.where(counts == k)


def build_frame(profit, exports, usdkrw, k, cli=None, tsmc=None):
    """분기 표. 특징은 모두 그 분기의 앞 k개월 또는 그 이전 자료만 쓴다.

    다음 분기 전망을 위해 profit_next(=t+1 분기 영업이익)와, 분기 t의 k번째 달 말 시점에 보이는
    G20 CLI를 함께 둔다. CLI는 참조월+1개월 20일 이후에만 보이므로 k번째 달 말에는 (k-1)번째 달
    값까지 들어온다.
    """
    exp_k = first_k_months(exports, k)
    fx_k = first_k_months(usdkrw, k)
    f = pd.DataFrame({"exports_k": exp_k, "usdkrw_k": fx_k})
    f["exports_krw_k"] = f["exports_k"] * f["usdkrw_k"]        # 원화 환산 수출 규모
    f["exports_yoy"] = f["exports_k"] / f["exports_k"].shift(4) - 1
    f["exports_qoq"] = f["exports_k"] / f["exports_k"].shift(1) - 1
    f["profit"] = profit.reindex(f.index)
    f["profit_lag1"] = f["profit"].shift(1)                    # 직전 분기(이번 분기 중에 이미 발표됨)
    f["profit_lag3"] = f["profit"].shift(3)                    # 다음 분기의 '4분기 전'
    f["profit_lag4"] = f["profit"].shift(4)
    f["profit_next"] = f["profit"].shift(-1)                   # 다음 분기 타깃
    if cli is not None and len(cli):
        asof = pd.DatetimeIndex([(q.start_time + pd.DateOffset(months=k)) - pd.Timedelta(days=1) for q in f.index])
        cf = cli_features(cli, asof)
        f["cli_level"] = cf["cli_level"].to_numpy()
        f["cli_change_3m"] = cf["cli_change_3m"].to_numpy()
    if tsmc is not None and len(tsmc):
        # TSMC 는 매달 10일 전후 공시라, 분기 앞 k개월 중 그 시점에 이미 나온 달만 센다.
        tf = tsmc_features(tsmc, f.index, k)
        for column in TSMC_FEATURES:
            f[column] = tf[column].to_numpy()
    return f


FEATURES = ["exports_krw_k", "exports_yoy", "exports_qoq", "profit_lag1", "profit_lag4"]
# TSMC 월매출. 한국 수출 확정치보다 빠르고 AI·HBM 수요를 직접 반영한다. 넣을지는 쌍체 비교로 정한다.
TSMC_FEATURES = ["tsmc_yoy", "tsmc_qoq"]
# 다음 분기: 직전 분기 영업이익은 profit_lag1(t-1)이 마지막으로 아는 값이고, 계절 기준선은 t-3이다.
FEATURES_NEXT = ["exports_krw_k", "exports_yoy", "exports_qoq", "profit_lag1", "profit_lag3"]
CLI_FEATURES = ["cli_level", "cli_change_3m"]


def walk_forward(f, target="profit", features=None, gap=0, rw="profit_lag1", sn="profit_lag4",
                 first_test=FIRST_TEST_QUARTER, min_train=MIN_TRAIN_QUARTERS):
    """확장 창. 분기 t는 타깃이 이미 알려진 분기들로만 학습한다.

    gap=0: 이번 분기 나우캐스트. t 이전 분기의 영업이익은 다 발표됐다.
    gap=1: 다음 분기 전망. 행 s의 타깃은 profit(s+1)이라, 분기 t 중에는 s+1<=t-1, 즉 s<=t-2까지만 안다.
    """
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    features = features or FEATURES
    usable = f.dropna(subset=features + [target, rw, sn])
    rows = []
    for t in usable.index[usable.index >= pd.Period(first_test, freq="Q")]:
        train = usable[usable.index < t - gap]
        if len(train) < min_train:
            continue
        model = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
        model.fit(train[features], train[target])
        rows.append({
            "quarter": t,
            "actual": float(usable.loc[t, target]),
            "model": float(model.predict(usable.loc[[t], features])[0]),
            "random_walk": float(usable.loc[t, rw]),
            "seasonal_naive": float(usable.loc[t, sn]),
        })
    return pd.DataFrame(rows).set_index("quarter") if rows else pd.DataFrame()


def evaluate(oof):
    if oof.empty:
        return {"n": 0, "beats_baselines": False, "note": "표본 부족"}
    out = {"n": int(len(oof)), "first": str(oof.index[0]), "last": str(oof.index[-1])}
    for name in ("model", "random_walk", "seasonal_naive"):
        out[f"mae_{name}"] = float(np.mean(np.abs(oof["actual"] - oof[name])))
    out["corr"] = float(np.corrcoef(oof["actual"], oof["model"])[0, 1]) if len(oof) > 2 else np.nan
    out["beats_baselines"] = bool(out["mae_model"] < min(out["mae_random_walk"], out["mae_seasonal_naive"]))
    residual = (oof["actual"] - oof["model"]).to_numpy()
    out["residual_q10"] = float(np.quantile(residual, .10))
    out["residual_q90"] = float(np.quantile(residual, .90))
    out["mape_model"] = float(np.mean(np.abs((oof["actual"] - oof["model"]) /
                                             np.where(np.abs(oof["actual"]) < 1e-9, np.nan, oof["actual"]))))
    return out


def split_selection_evaluation(oof):
    """시간 순서 OOF의 앞 절반은 모델 선택, 뒤 절반은 최종 평가에만 쓴다."""
    split = len(oof) // 2
    return oof.iloc[:split].copy(), oof.iloc[split:].copy()


def fit_live(f, live_quarter, target="profit", features=None, gap=0):
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    features = features or FEATURES
    usable = f.dropna(subset=features + [target])
    train = usable[usable.index < live_quarter - gap]
    live = f.loc[[live_quarter], features] if live_quarter in f.index else None
    if len(train) < MIN_TRAIN_QUARTERS or live is None or live.isna().any(axis=1).iloc[0]:
        return None, len(train)
    model = make_pipeline(StandardScaler(), Ridge(alpha=1.0)).fit(train[features], train[target])
    return float(model.predict(live)[0]), len(train)


# ---------------------------------------------------------------------------
# 추정 원장 — 나우캐스트도 기록하고 채점한다
# ---------------------------------------------------------------------------
# 8절은 지금까지 추정만 하고 채점이 없었다. 분기마다 몇 개뿐인 관측이라 백테스트만으로는
# 이 모듈이 쓸 만한지 알 수 없다. 그래서 (분기, 반영 개월 수)마다 '그 시점의 첫 추정'을 한 번만
# 남기고, 실제 영업이익이 나오면 그 행을 채점한다. 예측값은 절대 고쳐 쓰지 않는다.
LEDGER_COLUMNS = [
    "record_id", "run_id", "created_at_kst", "target", "quarter", "months_used", "months_included",
    "point", "low", "high", "raw_point", "beats_baselines", "mae_model", "mae_random_walk",
    "mae_seasonal_naive", "n_eval", "last_actual", "status", "actual", "actual_source",
    "error", "ape", "scored_at_kst",
]


def read_ledger(path):
    if not Path(path).exists():
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    frame = pd.read_csv(path, dtype=str)
    for column in LEDGER_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan
    for column in ("point", "low", "high", "raw_point", "mae_model", "mae_random_walk",
                   "mae_seasonal_naive", "last_actual", "actual", "error", "ape", "months_used", "n_eval"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame[LEDGER_COLUMNS]


def append_estimate(ledger, result, run_id):
    """(분기, 반영 개월 수)마다 첫 추정만 남긴다. 같은 조합이 이미 있으면 그대로 둔다."""
    key = (str(result["quarter_code"]), int(result["months_used"]))
    months = pd.to_numeric(ledger["months_used"], errors="coerce")
    existing = set(zip(ledger["quarter"].astype(str), months.where(months.notna(), -1).astype(int)))
    if key in existing:
        return ledger, False
    ev = result.get("evaluation") or {}
    row = {
        "record_id": f"{result['target']}:{key[0]}:k{key[1]}",
        "run_id": run_id, "created_at_kst": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "target": result["target"], "quarter": key[0], "months_used": key[1],
        "months_included": result.get("months_included"),
        "point": result.get("point"), "low": result.get("low"), "high": result.get("high"),
        # 축소·게이트 이전의 원시 추정값. 게이트에 걸려 point 가 비어도 무엇을 계산했는지는 남는다.
        "raw_point": result.get("raw_point"),
        "beats_baselines": ev.get("beats_baselines"), "mae_model": ev.get("mae_model"),
        "mae_random_walk": ev.get("mae_random_walk"), "mae_seasonal_naive": ev.get("mae_seasonal_naive"),
        "n_eval": ev.get("n"), "last_actual": result.get("last_actual"),
        "status": "pending", "actual": np.nan, "actual_source": "", "error": np.nan,
        "ape": np.nan, "scored_at_kst": "",
    }
    new = pd.DataFrame([row], columns=LEDGER_COLUMNS)
    if ledger.empty:                     # 빈 프레임과 concat하면 dtype 경고가 난다
        return new, True
    return pd.concat([ledger, new], ignore_index=True)[LEDGER_COLUMNS], True


def score_ledger(ledger, profit, provisional=None):
    """실제 영업이익이 나온 분기를 채점한다. 확정치가 잠정치보다 우선한다."""
    provisional = provisional or {}
    confirmed = {str(period): float(value) for period, value in profit.items()}
    scored = 0
    for i, row in ledger.iterrows():
        quarter = str(row["quarter"])
        actual, source = None, ""
        if quarter in confirmed:
            actual, source = confirmed[quarter], "confirmed"
        elif quarter in provisional and provisional[quarter] is not None:
            actual, source = float(provisional[quarter]), "provisional"
        if actual is None:
            continue
        if row["status"] == "scored" and row["actual_source"] == "confirmed":
            continue                     # 확정치로 채점한 행은 다시 건드리지 않는다
        ledger.loc[i, ["status", "actual", "actual_source", "scored_at_kst"]] = [
            "scored", actual, source, datetime.now(KST).strftime("%Y-%m-%d %H:%M")]
        if pd.notna(row["point"]):
            ledger.loc[i, "error"] = float(row["point"]) - actual
            ledger.loc[i, "ape"] = abs(float(row["point"]) - actual) / abs(actual) if actual else np.nan
        scored += 1
    return ledger, scored


def render_ledger_block(ledger, target):
    """지난 분기 추정 vs 실제. 실제로 미리 낸 추정만 채점한 것이라 백테스트와 섞지 않는다."""
    mine = ledger[(ledger["target"] == target) & (ledger["status"] == "scored")]
    parts = ['<h4 style="font-size:14px;margin:18px 0 6px">지난 분기 추정 vs 실제 '
             '<span style="font-size:11px;color:#8a9199;font-weight:400">실제로 미리 낸 추정만 채점</span></h4>']
    if mine.empty:
        parts.append('<div style="font-size:13px;color:#6b7178">아직 채점된 추정이 없습니다. '
                     '분기 영업이익이 공시되면 이 표에 쌓입니다.</div>')
        return "".join(parts)
    body = ""
    for _, r in mine.sort_values(["quarter", "months_used"], ascending=[False, True]).iterrows():
        point = jo(r["point"]) if pd.notna(r["point"]) else "예측하지 않음"
        err = "—" if pd.isna(r["error"]) else f'{r["error"] / TRILLION:+,.2f}조원 ({r["ape"]:.0%})'
        source = "확정" if r["actual_source"] == "confirmed" else "잠정"
        body += (f'<tr><td {TD}>{html.escape(str(r["quarter"]))}</td>'
                 f'<td {TDR}>{int(r["months_used"])}개월</td><td {TDR}>{point}</td>'
                 f'<td {TDR}>{jo(r["actual"])} <span style="color:#8a9199">({source})</span></td>'
                 f'<td {TDR}>{err}</td></tr>')
    parts.append('<div style="overflow-x:auto"><table style="width:100%;min-width:520px;'
                 'border-collapse:collapse;font-size:13px;border:1px solid #e5e5e5">'
                 f'<tr><th {TH}>분기</th><th {THR}>반영</th><th {THR}>추정</th>'
                 f'<th {THR}>실제</th><th {THR}>오차</th></tr>{body}</table></div>')
    parts.append('<div style="font-size:11px;color:#8a9199;margin-top:4px">'
                 '분기·반영 개월 수마다 그 시점의 <b>첫 추정</b>만 남깁니다. 잠정실적으로 채점한 행은 '
                 '확정 재무제표가 나오면 그 값으로 다시 채점됩니다.</div>')
    return "".join(parts)


# ---------------------------------------------------------------------------
# 렌더링
# ---------------------------------------------------------------------------
TD = 'style="padding:6px 10px;border-top:1px solid #eee"'
TDR = TD[:-1] + ';text-align:right;font-variant-numeric:tabular-nums"'
TH = 'style="padding:8px 10px;text-align:left;font-size:11px;color:#6b7178;letter-spacing:.5px;background:#fafafa"'
THR = TH.replace("text-align:left", "text-align:right")


def jo(value):
    return "—" if value is None or not np.isfinite(value) else f"{value / TRILLION:,.2f}조원"


def render_chart(f, oof, name):
    """분기 영업이익(막대)과 원화 환산 반도체 수출(선). 사이클이 같이 도는지 눈으로 본다."""
    d = f.dropna(subset=["exports_krw_k"]).copy()
    d = d[d.index >= pd.Period("2011Q1", freq="Q")]
    if len(d) < 12:
        return ""
    W, L, R, TOP, BOT = 900, 66, 58, 28, 34
    PH = 240
    H = TOP + PH + BOT
    n = len(d)
    step = (W - L - R) / n

    def X(i):
        return L + step * (i + .5)

    profit = d["profit"]
    pmax = float(np.nanmax(np.abs(profit))) if profit.notna().any() else 1.
    zero_y = TOP + PH * .72

    def YP(v):
        return zero_y - v / pmax * (PH * .62)

    ex = d["exports_krw_k"]
    elo, ehi = float(ex.min()), float(ex.max())

    def YE(v):
        return TOP + PH - (v - elo) / max(ehi - elo, 1e-9) * (PH - 12)

    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;'
           f'font-family:-apple-system,\'Malgun Gothic\',sans-serif;font-size:11px">']
    for i, (period, row) in enumerate(d.iterrows()):
        v = row["profit"]
        if not np.isfinite(v):
            continue
        top, height = (YP(v), zero_y - YP(v)) if v >= 0 else (zero_y, YP(v) - zero_y)
        color = "#4c78a8" if v >= 0 else "#b5453c"
        out.append(f'<rect x="{X(i) - step * .34:.1f}" y="{top:.1f}" width="{step * .68:.1f}" '
                   f'height="{max(height, 0.6):.1f}" fill="{color}" opacity="0.85"/>')
    if oof is not None and len(oof):
        pts = [(i, oof.loc[p, "model"]) for i, p in enumerate(d.index) if p in oof.index]
        if pts:
            out.append('<g>' + "".join(
                f'<circle cx="{X(i):.1f}" cy="{YP(v):.1f}" r="2.6" fill="none" stroke="#c8952a" stroke-width="1.4"/>'
                for i, v in pts) + '</g>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{zero_y:.1f}" y2="{zero_y:.1f}" stroke="#999"/>')
    out.append(f'<polyline points="{" ".join(f"{X(i):.1f},{YE(v):.1f}" for i, v in enumerate(ex))}" '
               'fill="none" stroke="#2e7d32" stroke-width="1.6"/>')
    for v in (pmax, pmax / 2, 0, -pmax / 2):
        if abs(v) > pmax:
            continue
        out.append(f'<text x="{L - 6}" y="{YP(v) + 4:.1f}" text-anchor="end" fill="#4c78a8">{v / TRILLION:,.0f}조</text>')
    out.append(f'<text x="{W - R + 6}" y="{YE(ehi) + 4:.1f}" fill="#2e7d32">수출↑</text>')
    out.append(f'<text x="{W - R + 6}" y="{YE(elo) + 4:.1f}" fill="#2e7d32">수출↓</text>')
    for i, period in enumerate(d.index):
        if period.quarter == 1 and period.year % 2 == 1:
            out.append(f'<text x="{X(i):.1f}" y="{TOP + PH + 15:.1f}" text-anchor="middle" fill="#8a9199">{period.year}</text>')
    out.append(f'<text x="{L}" y="14" fill="#1a1a1a" font-weight="600">'
               f'{html.escape(name)} 분기 영업이익(막대)과 원화 환산 반도체 수출(선)</text>')
    out.append(f'<text x="{W - R}" y="14" text-anchor="end" fill="#c8952a">○ 워크포워드 추정치</text>')
    out.append(f'<rect x="{L}" y="{TOP}" width="{W - L - R}" height="{PH}" fill="none" stroke="#ddd"/>')
    out.append("</svg>")
    return "".join(out)


def render_fragment(result):
    e = html.escape
    r = result
    ev = r["evaluation"]
    parts = ['<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
             f'4. 이번 분기 영업이익 추정 <span style="font-weight:400;color:#8a9199;font-size:12px">'
             f'&nbsp;{e(r["quarter"])} · 월별 반도체 수출액 기준 · '
             f'{e(r.get("months_included") or "")} 반영</span></h3>']
    parts.append('<div style="background:#fdf8ec;border-left:4px solid #c8952a;padding:12px 16px;'
                 'border-radius:0 5px 5px 0;font-size:13px">'
                 '주가 예측과 성격이 다릅니다. 이것은 <b>이미 진행 중인 분기의 결과를 발표 전에 추정</b>하는 '
                 '나우캐스트입니다. 메모리 영업이익과 한국 반도체 수출액은 같은 것(D램·낸드 가격과 물량)을 재고 있어 '
                 f'여지가 실제로 있습니다. 분기가 끝나기 전이므로 이번 분기의 <b>{e(r.get("months_included") or "")}</b> 수출만 썼고, '
                 f'과거 분기도 똑같이 <b>각 분기의 앞 {r["months_used"]}개월</b>로 특징을 만들어 학습했습니다 — '
                 '3개월 평균으로 학습한 계수를 2개월 평균에 적용하면 성질이 다른 값을 넣는 셈이 되기 때문입니다.'
                 + (f' <b>{e(r["months_missing"])} 수출은 아직 KOSIS에 올라오지 않았습니다.</b> '
                    '그 달이 들어오면 추정이 더 단단해집니다.' if r.get("months_missing") else "")
                 + (" " + " ".join(
                     f'<b>{e(a["month"])}</b>은 관세청 1~{a["days"]}일 속보(반도체 {a["yoy"]:+.0%})로 잠정 추정한 값입니다.'
                     for a in r.get("flash_applied") or []) if r.get("flash_applied") else "")
                 + ((f' {e(", ".join(r["customs_info"]["months_added"]))}은 관세청 원천(HS 8541·8542 합계)을 '
                     f'KOSIS 기준으로 환산해 넣은 값입니다 — 품목 범위가 좁아 그대로는 KOSIS의 '
                     f'{r["customs_info"]["scale"]:.2f}분의 1 수준이라, 겹치는 최근 '
                     f'{r["customs_info"]["window"]}개월 배율(×{r["customs_info"]["scale"]:.3f})로 맞췄습니다. '
                     f'확정치가 아니라 추정입니다.')
                    if (r.get("customs_info") or {}).get("months_added") else "")
                 + '</div>')

    if r.get("chart_svg"):
        parts.append(f'<div style="border:1px solid #e5e5e5;border-radius:6px;padding:8px;margin-top:10px">{r["chart_svg"]}</div>')

    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">추정</h4>')
    if r["point"] is not None:
        parts.append(f'<div style="font-size:22px;font-weight:700">{jo(r["point"])}'
                     f'<span style="font-size:13px;font-weight:400;color:#6b7178"> · 80% 구간 '
                     f'{jo(r["low"])} ~ {jo(r["high"])}</span></div>')
        parts.append(f'<div style="font-size:12px;color:#6b7178;margin-top:4px">직전 분기 {jo(r["last_actual"])}'
                     f'({e(r["last_actual_quarter"])}) 대비 {r["change_vs_last"]:+.1%}</div>')
    else:
        parts.append('<div style="font-size:16px;font-weight:600;color:#6b7178">예측하지 않음</div>'
                     f'<div style="font-size:12px;color:#8a9199;margin-top:4px">{e(r["no_point_reason"])} '
                     f'참고로 직전 분기는 {jo(r["last_actual"])}({e(r["last_actual_quarter"])})였습니다.</div>')

    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">검증 — 기준선을 이기는가 (워크포워드)</h4>')
    if ev.get("n"):
        body = ""
        for key, label in (("model", "수출 기반 모델"), ("random_walk", "직전 분기 그대로"),
                           ("seasonal_naive", "4분기 전 그대로")):
            body += (f'<tr><td {TD}>{label}</td><td {TDR}>{ev[f"mae_{key}"] / TRILLION:,.2f}조원</td></tr>')
        parts.append(f'<div style="font-size:12px;color:#6b7178;margin-bottom:6px">'
                     f'{e(ev["first"])} ~ {e(ev["last"])} · {ev["n"]}개 분기 · 평균절대오차(MAE)가 작을수록 좋습니다.</div>')
        parts.append('<div style="overflow-x:auto"><table style="width:100%;min-width:420px;border-collapse:collapse;'
                     f'font-size:13px;border:1px solid #e5e5e5"><tr><th {TH}>방법</th><th {THR}>MAE</th></tr>{body}</table></div>')
        verdict = ("<b style='color:#1e6b34'>두 기준선을 모두 이겼습니다.</b>" if ev["beats_baselines"]
                   else "<b style='color:#a8322a'>기준선을 이기지 못했습니다.</b> 점 추정을 내지 않습니다.")
        parts.append(f'<div style="font-size:13px;margin-top:8px">{verdict} '
                     f'실제값과의 상관 {ev.get("corr", float("nan")):.2f} · 평균 오차율 {ev.get("mape_model", float("nan")):.1%}</div>')
    else:
        parts.append(f'<div style="font-size:13px;color:#6b7178">{e(ev.get("note", "표본 부족"))}</div>')

    # TSMC 효과
    if r.get("tsmc_active") and r.get("tsmc_ablation"):
        ab = r["tsmc_ablation"]
        if ab.get("mae_with") is not None and ab.get("mae_without") is not None:
            better = ab["mae_with"] < ab["mae_without"]
            parts.append('<h4 style="font-size:14px;margin:18px 0 6px">TSMC 월매출을 넣으면 나아지는가</h4>')
            parts.append('<div style="font-size:12px;color:#6b7178;margin-bottom:6px">'
                         'TSMC 는 매달 10일 전후에 전월 매출을 공시합니다. KOSIS 반도체 수출 확정치보다 빠르고 '
                         'AI·HBM 수요를 직접 반영하지만, 도움이 되는지는 재 봐야 압니다. 공시 시점을 반영해 '
                         '그 시점에 이미 나온 달만 썼습니다.</div>')
            parts.append('<div style="overflow-x:auto"><table style="width:100%;min-width:460px;'
                         'border-collapse:collapse;font-size:13px;border:1px solid #e5e5e5">'
                         f'<tr><th {TH}>모델</th><th {THR}>MAE</th><th {THR}>기준선 통과</th></tr>'
                         f'<tr><td {TD}>TSMC 제외</td><td {TDR}>{ab["mae_without"] / TRILLION:,.2f}조원</td>'
                         f'<td {TDR}>{"이김" if ab["beats_without"] else "못 이김"}</td></tr>'
                         f'<tr><td {TD}>TSMC 포함</td><td {TDR}>{ab["mae_with"] / TRILLION:,.2f}조원</td>'
                         f'<td {TDR}>{"이김" if ab["beats_with"] else "못 이김"}</td></tr></table></div>')
            parts.append(f'<div style="font-size:11px;color:#8a9199;margin-top:4px">'
                         f'{"TSMC 를 넣은 쪽이 오차가 작습니다" if better else "넣어도 오차가 줄지 않습니다"} '
                         f'(분기 {ab.get("n")}개). 차이가 작으면 동률로 읽으세요. 발표 결과가 아니라 '
                         '월매출 자체를 쓰며, 다음 날 주가가 아니라 분기 이익을 맞히는지로만 판단합니다.</div>')
    # D램 현물가 — 표시만. 이력이 검증에 충분해지면(1년 남짓) 특징으로 넣고 쌍체 비교한다.
    ds = r.get("dram_summary")
    if ds:
        def _chg(v):
            return "—" if v is None else f'{v:+.1%}'
        parts.append('<h4 style="font-size:14px;margin:18px 0 6px">D램 현물 가격 '
                     '<span style="font-size:11px;color:#8a9199;font-weight:400">DRAMeXchange 세션 평균 · 매일 누적 중</span></h4>')
        parts.append(f'<div style="font-size:13px">DDR5 16Gb <b>${ds["value"]:.2f}</b> ({e(ds["date"])}) · '
                     f'1주 {_chg(ds["change_7d"])} · 1개월 {_chg(ds["change_30d"])} · 누적 {ds["days"]}일</div>')
        parts.append('<div style="font-size:11px;color:#8a9199;margin-top:4px">'
                     '삼성전자·SK하이닉스 이익은 D램 가격에 가장 직접 좌우되고 현물가는 수출 통계보다 빠릅니다. '
                     '다만 첫 페이지는 당일 값만 주어 이력을 매일 쌓고 있으며, 검증에 쓸 만큼(1년 남짓) 모이기 '
                     '전에는 특징으로 넣지 않습니다. 현물가와 삼성·하이닉스의 고정거래가는 다르며(고정가가 1~2개월 '
                     '뒤따르는 경향), 그 관계도 측정 대상입니다.</div>')
    elif not (r.get("dram_info") or {}).get("enabled"):
        parts.append('<div style="font-size:11px;color:#8a9199;margin-top:8px">D램 현물가 미수집 — '
                     f'{html.escape(str((r.get("dram_info") or {}).get("reason", "")))}</div>')

    elif not (r.get("tsmc_info") or {}).get("enabled"):
        parts.append('<div style="font-size:11px;color:#8a9199;margin-top:8px">TSMC 월매출 미포함 — '
                     f'{html.escape(str((r.get("tsmc_info") or {}).get("reason", "")))}. '
                     '<code>macro_inputs/tsmc_revenue.csv</code>(month,value)를 두면 비교표가 나옵니다.</div>')

    if r.get("ledger_html"):
        parts.append(r["ledger_html"])

    # ---- 다음 분기 전망
    nq = r.get("next_quarter")
    if nq:
        parts.append('<h4 style="font-size:14px;margin:22px 0 6px">'
                     f'다음 분기({e(nq["quarter"])}) 전망 — G20 경기선행지수를 넣어 보다</h4>')
        parts.append('<div style="font-size:12px;color:#6b7178;margin-bottom:6px">이번 분기 나우캐스트와 달리 '
                     '아직 시작하지 않은 분기를 내다보는 것이라 훨씬 어렵습니다. 선행지수가 쓸모 있다면 여기서 '
                     '나타나야 합니다. 같은 날짜에서 CLI를 넣은 모델과 뺀 모델을 나란히 쟀습니다. '
                     '과거 OOF의 앞 절반에서 모델을 선택하고 뒤 절반에서 최종 평가했습니다.</div>')
        if nq["point"] is not None:
            parts.append(f'<div style="font-size:20px;font-weight:700">{jo(nq["point"])}'
                         f'<span style="font-size:13px;font-weight:400;color:#6b7178"> · 80% 구간 '
                         f'{jo(nq["low"])} ~ {jo(nq["high"])} · {"CLI 포함" if nq["chosen"] == "with_cli" else "CLI 제외"} 모델</span></div>')
        else:
            parts.append('<div style="font-size:16px;font-weight:600;color:#6b7178">예측하지 않음</div>'
                         f'<div style="font-size:12px;color:#8a9199;margin-top:4px">{e(nq["no_point_reason"])}</div>')
        body = ""
        for label, evx in (("CLI 제외", nq["evaluation_without_cli"]), ("CLI 포함", nq.get("evaluation_with_cli"))):
            if not evx or not evx.get("n"):
                body += f'<tr><td {TD}>{label}</td><td {TDR} colspan="4">{e((evx or {}).get("note", "미포함"))}</td></tr>'
                continue
            body += (f'<tr><td {TD}>{label}</td><td {TDR}>{evx["n"]}</td>'
                     f'<td {TDR}>{evx["mae_model"] / TRILLION:,.2f}조원</td>'
                     f'<td {TDR}>{evx["mae_random_walk"] / TRILLION:,.2f} / {evx["mae_seasonal_naive"] / TRILLION:,.2f}조원</td>'
                     f'<td {TDR}>{"이김" if evx["beats_baselines"] else "못 이김"}</td></tr>')
        parts.append('<div style="overflow-x:auto"><table style="width:100%;min-width:520px;border-collapse:collapse;'
                     f'font-size:13px;border:1px solid #e5e5e5"><tr><th {TH}>모델</th><th {THR}>분기 수</th>'
                     f'<th {THR}>MAE</th><th {THR}>기준선 MAE (직전/4분기 전)</th><th {THR}>판정</th></tr>{body}</table></div>')
        if r.get("cli_active"):
            parts.append('<div style="font-size:11px;color:#8a9199;margin-top:4px">G20 CLI는 참조월+1개월 20일 이후 값만 썼습니다. '
                         'CLI는 매달 소급 수정되므로 이 표는 최종 수정치 기준이라 낙관적입니다.</div>')
        else:
            parts.append(f'<div style="font-size:11px;color:#8a9199;margin-top:4px">G20 CLI 미포함 — '
                         f'{e(str(r.get("cli_info", {}).get("reason", "")))}</div>')

    parts.append(f'<div style="font-size:11px;color:#8a9199;margin-top:10px">'
                 f'영업이익 출처: {e(r["profit_source"])} · 이력 {e(r["profit_first"])}~{e(r["profit_last"])} '
                 f'({r["profit_n"]}개 분기) · 수출액 최신월 {e(r["exports_last_month"])} · '
                 f'생성 {e(r["generated_at"])}<br>'
                 '분기 영업이익은 발표 전까지 확정값이 아니며, 이 추정은 연구·교육용입니다. '
                 '회사 가이던스·증권사 추정치와 다를 수 있습니다.</div>')
    return "".join(parts)


# ---------------------------------------------------------------------------
def analyse(target, out_dir, fetch=True):
    spec = TARGETS[target]
    out_dir.mkdir(parents=True, exist_ok=True)
    profit, profit_source = load_operating_profit(out_dir, target)

    # 보관본은 파일마다 따로 받는다. 하나가 실패해도 나머지는 들어와야 한다.
    fallback_dir = out_dir / "macro_fallback"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    _token = None
    try:
        _token = github_pages.token()
    except Exception:
        pass
    loaded = []
    for _name in ("semiconductor_exports.csv", "leading_cycle.csv", "customs_exports.csv",
                  "cli_g20.csv", "tsmc_revenue.csv", "dram_spot.csv"):
        try:
            text = github_pages.fetch(f"macro_history/{_name}", _token)
            if text:
                (fallback_dir / _name).write_text(text, encoding="utf-8")
                loaded.append(_name)
        except Exception:
            continue
    print("  보관본:", ", ".join(loaded) if loaded else "(없음)", flush=True)
    macro, macro_info = load_macro_data(out_dir, "2005-01-01",
                                        datetime.now(KST).date(), use_cache=not fetch,
                                        fallback_dir=fallback_dir)
    # 관세청 원천에서 최근 달을 먼저 채운다. KOSIS 확정치는 그대로 두고 없는 달만 더한다.
    customs_info = {"enabled": False, "reason": "DATA_GO_KR_KEY 없음"}
    customs_cache = fallback_dir / "customs_exports.csv"
    key = data_go_kr_key()
    if key and fetch:
        try:
            try:
                # 18개월이면 12개월 창 두 개(HS 2개 × 2 = 호출 4건)다. 30개월은 창이 셋이라 호출이
                # 여섯이고, 한국 정부 API 가 해외에서 간헐적으로 끊기므로 요청이 많을수록 실패 확률이
                # 올라간다. 환산 배율은 겹치는 최근 6개월이면 되고 KOSIS 는 두 달쯤 뒤처질 뿐이라
                # 18개월로 충분하다(겹침 15개월 안팎).
                customs = fetch_customs_exports(pd.Timestamp.now(tz=KST).date().replace(day=1) - pd.DateOffset(months=18),
                                                pd.Timestamp.now(tz=KST).date(), key)
                customs_info["source"] = "customs_api"
                (out_dir / "customs_exports.csv").parent.mkdir(parents=True, exist_ok=True)
                customs.to_csv(out_dir / "customs_exports.csv", index=False)
            except Exception as exc:
                # 한국 정부 API는 해외 IP(Actions 러너)에서 간헐적으로 연결 자체가 막힌다.
                # 지난 성공분이 저장소에 있으면 그것으로 계속 간다(월 단위 자료라 값이 같다).
                if not customs_cache.exists():
                    raise
                print(f"  관세청 조회 실패 → 저장소 보관본 사용: {exc}", flush=True)
                customs = pd.read_csv(customs_cache)
                customs_info["source"] = "customs_cache"
            # HS 8541+8542 는 KOSIS '반도체'보다 범위가 좁아 계통적으로 작다(2026-09 기준 0.8배).
            # 크기가 같은지 묻는 대신, 배율이 안정적인지 보고 KOSIS 기준으로 환산해서 넣는다.
            same_size, size_diag = reconcile_customs(macro["semiconductor_exports"], customs)
            ok, scale, diag = customs_scale(macro["semiconductor_exports"], customs)
            # source 는 위에서 이미 정해졌다. 통째로 새로 만들면 그것이 지워져 보관본 발행 조건
            # (source == "customs_api")이 영영 거짓이 된다 — 2026-09-12 실제로 그랬다. 갱신만 한다.
            customs_info.update(enabled=ok, same_size=same_size,
                                ratio_median=size_diag.get("ratio_median"), **diag)
            if ok:
                # 첫 줄에서 넣어 둔 "DATA_GO_KR_KEY 없음" 이 남아 있으면 성공한 실행이 실패로 읽힌다.
                customs_info.pop("reason", None)
            if ok:
                macro["semiconductor_exports"], added = merge_customs_exports(
                    macro["semiconductor_exports"], customs, scale=scale)
                customs_info["months_added"] = added
                print(f"  관세청으로 채운 달: {added or '없음(KOSIS가 이미 최신)'} "
                      f"· KOSIS 기준 환산 배수 {scale:.3f}"
                      f"(최근 {diag['window']}개월 배율, 흔들림 {diag['spread']:.1%})", flush=True)
            else:
                print("  ⚠️ 관세청 계열을 쓰지 않습니다:", diag.get("reason"), flush=True)
        except Exception as exc:
            customs_info = {"enabled": False, "reason": f"{type(exc).__name__}: {exc}"}
            print("  ⚠️ 관세청을 쓰지 못했습니다(무시):", exc, flush=True)

    exports = (macro["semiconductor_exports"].set_index("month")["value"]
               .asfreq("MS").dropna())
    flash_applied = []
    try:
        exports, flash_applied = apply_exports_flash(exports, load_exports_flash(out_dir))
        if flash_applied:
            print("  관세청 속보로 잠정 추정한 달:", flash_applied, flush=True)
    except Exception as exc:
        print("  ⚠️ 수출 속보를 읽지 못했습니다(무시):", exc, flush=True)
    usdkrw = monthly_usdkrw(out_dir / "cache", fetch=fetch)
    usdkrw = usdkrw.reindex(usdkrw.index.union(exports.index)).ffill().reindex(exports.index)

    # 이번 분기에 실제로 확보된 월 수 = 나우캐스트에 쓸 k
    live_quarter = pd.Period(exports.index[-1], freq="Q")
    in_quarter = exports.index[pd.PeriodIndex(exports.index, freq="Q") == live_quarter]
    months_used = len(in_quarter)
    month_names = ", ".join(f"{d.month}월" for d in in_quarter)
    missing_months = ", ".join(
        f"{m}월" for m in range(live_quarter.start_time.month, live_quarter.end_time.month + 1)
        if m not in {d.month for d in in_quarter})

    cli, cli_info = None, {"enabled": False, "reason": "USE_CLI=False"}
    if os.environ.get("USE_CLI", "true").strip().lower() not in ("0", "false", "no"):
        try:
            cli, cli_info = load_cli(out_dir, "2000-01-01", datetime.now(KST).date(),
                                     use_cache=not fetch, fallback_dir=fallback_dir)
            cli_info["enabled"] = True
        except Exception as exc:
            cli, cli_info = None, {"enabled": False, "reason": f"{type(exc).__name__}: {exc}"}
            print("  ⚠️ G20 CLI를 쓸 수 없어 빼고 진행합니다:", cli_info["reason"], flush=True)

    tsmc, tsmc_info = None, {"enabled": False, "reason": "자료 없음"}
    try:
        tsmc, tsmc_info = load_tsmc_revenue(out_dir, fallback_dir=fallback_dir, fetch=fetch)
        tsmc_info["enabled"] = True
        (out_dir / "tsmc_revenue.csv").write_text(tsmc.to_csv(index=False), encoding="utf-8")
        print(f"  TSMC 월매출: {tsmc_info['source']} · {tsmc_info['first']}~{tsmc_info['last']} "
              f"({tsmc_info['rows']}개월)", flush=True)
    except Exception as exc:
        tsmc_info = {"enabled": False, "reason": f"{type(exc).__name__}: {exc}"}

    # D램 현물가: 매일 받아 누적한다. 이력이 짧은 동안은 보고서에 표시만 하고 특징으로 쓰지 않는다.
    dram, dram_info = None, {"enabled": False, "reason": "자료 없음"}
    try:
        dram, dram_info = load_dram_spot(out_dir, fallback_dir=fallback_dir, fetch=fetch)
        dram_info["enabled"] = True
        (out_dir / "dram_spot.csv").write_text(dram.to_csv(index=False), encoding="utf-8")
        print(f"  D램 현물가: {dram_info['source']} · {dram_info['first']}~{dram_info['last']} "
              f"({dram_info['rows']}일)", flush=True)
    except Exception as exc:
        dram_info = {"enabled": False, "reason": f"{type(exc).__name__}: {exc}"}
        print("  ⚠️ D램 현물가를 받지 못했습니다(무시):", exc, flush=True)

    f = build_frame(profit, exports, usdkrw, months_used, cli, tsmc)
    oof = walk_forward(f)
    ev = evaluate(oof)
    point, n_train = fit_live(f, live_quarter)

    # TSMC 를 넣으면 이번 분기 추정이 나아지는가. 같은 날짜·같은 방법으로 쌍체 비교한다.
    tsmc_active = all(c in f.columns and f[c].notna().mean() > 0.5 for c in TSMC_FEATURES)
    tsmc_ablation = {}
    if tsmc_active:
        with_tsmc = evaluate(walk_forward(f, features=FEATURES + TSMC_FEATURES))
        tsmc_ablation = {"mae_with": with_tsmc.get("mae_model"), "mae_without": ev.get("mae_model"),
                         "n": with_tsmc.get("n"),
                         "beats_with": with_tsmc.get("beats_baselines"),
                         "beats_without": ev.get("beats_baselines")}
    raw_point = point          # 기준선 게이트에 걸리기 전의 원시 추정값

    # ---- 다음 분기 전망 (CLI가 이론적으로 맞는 자리) --------------------------------
    # 분기 t 중간에 t+1의 영업이익을 내다본다. 같은 날짜에서 CLI를 넣은 것과 뺀 것을 나란히 재고,
    # 기준선(마지막으로 아는 영업이익 t-1, 4분기 전 t-3)을 이길 때만 숫자를 낸다.
    cli_active = cli is not None and all(c in f.columns and f[c].notna().mean() > 0.5 for c in CLI_FEATURES)
    next_quarter = live_quarter + 1
    next_variants = {"without_cli": FEATURES_NEXT}
    if cli_active:
        next_variants["with_cli"] = FEATURES_NEXT + CLI_FEATURES
    next_results = {}
    for name, feats in next_variants.items():
        oof_n = walk_forward(f, target="profit_next", features=feats, gap=1, rw="profit_lag1", sn="profit_lag3")
        selection_oof, evaluation_oof = split_selection_evaluation(oof_n)
        selection_n = evaluate(selection_oof)
        ev_n = evaluate(evaluation_oof)
        pt_n, ntr = fit_live(f, live_quarter, target="profit_next", features=feats, gap=1)
        next_results[name] = {"selection": selection_n, "evaluation": ev_n,
                              "raw_point": pt_n, "n_train": ntr}
    # 두 모델의 MAE 를 비교해 좋은 쪽을 고르면, 그 MAE 를 그대로 성능으로 보고하는 순간
    # 선택과 평가가 같은 표본에서 이뤄진다(분기 40개 남짓이라 편향이 크다). 그래서 성능 순위로
    # 고르지 않고 규칙으로 정한다: 기본은 단순한 쪽(CLI 제외)이고, 그것이 기준선을 못 이기는데
    # CLI 포함이 이길 때만 CLI 를 쓴다. 두 모델의 수치는 어느 쪽을 골랐든 표에 함께 보여 준다.
    simple_ok = next_results["without_cli"]["selection"].get("beats_baselines")
    cli_ok = cli_active and next_results["with_cli"]["selection"].get("beats_baselines")
    chosen = "with_cli" if (not simple_ok and cli_ok) else "without_cli"
    nr = next_results[chosen]
    next_point = nr["raw_point"] if nr["evaluation"].get("beats_baselines") and nr["raw_point"] is not None else None
    next_block = {
        "quarter": f"{next_quarter.year}년 {next_quarter.quarter}분기", "quarter_code": str(next_quarter),
        "chosen": chosen, "point": next_point,
        "low": (next_point + nr["evaluation"]["residual_q10"]) if next_point is not None else None,
        "high": (next_point + nr["evaluation"]["residual_q90"]) if next_point is not None else None,
        "raw_point": nr["raw_point"], "evaluation": nr["evaluation"],
        "selection": nr["selection"],
        "selection_without_cli": next_results["without_cli"]["selection"],
        "selection_with_cli": next_results.get("with_cli", {}).get("selection"),
        "evaluation_without_cli": next_results["without_cli"]["evaluation"],
        "evaluation_with_cli": next_results.get("with_cli", {}).get("evaluation"),
        "no_point_reason": ("" if next_point is not None else
                            "워크포워드에서 '마지막으로 아는 영업이익 그대로'·'4분기 전 그대로'보다 오차가 작다는 것을 보이지 못했습니다."),
    }
    no_point_reason = ""
    if point is None:
        no_point_reason = f"학습 분기가 {n_train}개로 부족하거나 이번 분기 특징이 비어 있습니다."
    elif not ev.get("beats_baselines"):
        no_point_reason = ("워크포워드에서 '직전 분기 그대로' 또는 '4분기 전 그대로'보다 "
                           "오차가 작다는 것을 보이지 못했습니다.")
        point = None

    # 잠정실적: 확정 재무제표보다 5주쯤 빠르다. 원장 채점을 그만큼 앞당긴다.
    provisional, provisional_info = {}, {}
    key = dart_key()
    if key and fetch:
        for period in pd.period_range(live_quarter - 2, live_quarter, freq="Q"):
            if str(period) in {str(p) for p in profit.index}:
                continue                        # 확정치가 이미 있으면 볼 필요가 없다
            previous = float(profit.iloc[-1]) if len(profit) else None
            value, info = fetch_provisional_profit(spec["corp_code"], key, period, previous)
            provisional_info[str(period)] = info
            if value is not None:
                provisional[str(period)] = value
                print(f"  잠정실적 {period}: {value / TRILLION:,.2f}조원 "
                      f"({info.get('report_nm')}, {info.get('rcept_dt')})", flush=True)
            elif info.get("reason"):
                print(f"  잠정실적 {period}: {info['reason']}", flush=True)

    last_actual_quarter = profit.index[-1]
    last_actual = float(profit.iloc[-1])
    result = {
        "target": target, "name": spec["name"],
        "quarter": f"{live_quarter.year}년 {live_quarter.quarter}분기",
        "quarter_code": str(live_quarter), "months_used": months_used,
        "months_included": month_names, "months_missing": missing_months,
        "flash_applied": flash_applied, "customs_info": customs_info,
        "point": point, "raw_point": raw_point,
        "low": (point + ev["residual_q10"]) if point is not None else None,
        "high": (point + ev["residual_q90"]) if point is not None else None,
        "change_vs_last": (point / last_actual - 1) if (point is not None and last_actual) else float("nan"),
        "no_point_reason": no_point_reason,
        "last_actual": last_actual, "last_actual_quarter": str(last_actual_quarter),
        "evaluation": ev, "n_train": n_train,
        "profit_source": profit_source, "profit_n": int(len(profit)),
        "profit_first": str(profit.index[0]), "profit_last": str(profit.index[-1]),
        "exports_last_month": exports.index[-1].date().isoformat(),
        "macro_sources": macro_info.get("sources", {}),
        "cli_info": cli_info, "cli_active": cli_active,
        "tsmc_info": tsmc_info, "tsmc_active": tsmc_active, "tsmc_ablation": tsmc_ablation,
        "dram_info": dram_info, "dram_summary": dram_spot_summary(dram) if dram is not None else None,
        "provisional": provisional, "provisional_info": provisional_info,
        "next_quarter": next_block,
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
    }
    result["chart_svg"] = render_chart(f, oof, spec["name"])
    return result, f, oof, profit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="samsung", choices=list(TARGETS))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--dump", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    out_dir = args.out / args.target
    result, frame, oof, profit_series = analyse(args.target, out_dir, fetch=not args.no_fetch)

    # ---- 추정 원장: 저장소 것을 받아 이번 추정을 더하고, 실제가 나온 분기를 채점한다 ----
    run_id = datetime.now(KST).strftime("%Y%m%dT%H%M%S")
    ledger_name = f"forecast_history/{args.target}/earnings_log.csv"
    ledger_path = out_dir / "earnings_log.csv"
    token = None
    try:
        token = github_pages.token()
        remote = github_pages.fetch(ledger_name, token)
        if remote:
            ledger_path.write_text(remote, encoding="utf-8")
    except Exception as exc:
        if args.publish:
            raise RuntimeError(
                "기존 추정 원장을 확인하지 못해 발행을 중단합니다. "
                "원격 이력을 빈 원장으로 덮어쓰지 않습니다."
            ) from exc
        print("  원장을 받지 못했습니다(로컬만 사용):", exc, flush=True)
    ledger = read_ledger(ledger_path)
    ledger, added = append_estimate(ledger, result, run_id)
    ledger, scored = score_ledger(ledger, profit_series, result.get("provisional"))
    ledger.to_csv(ledger_path, index=False)
    print(f"  추정 원장: {len(ledger)}행 (이번에 추가 {int(added)}건, 채점 {scored}건)", flush=True)
    result["ledger_html"] = render_ledger_block(ledger, args.target)

    fragment = render_fragment(result)
    (out_dir / "earnings.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "earnings.html").write_text(fragment, encoding="utf-8")
    frame.to_csv(out_dir / "earnings_frame.csv")
    profit_series.rename_axis("quarter").rename("value").reset_index().assign(
        quarter=lambda d: d["quarter"].astype(str)).to_csv(out_dir / "earnings_profit.csv", index=False)
    oof.to_csv(out_dir / "earnings_oof.csv")
    print(f"{result['name']} {result['quarter']} ({result.get('months_included')} 반영): "
          f"{jo(result['point']) if result['point'] is not None else '예측하지 않음'}"
          f" · 직전 {jo(result['last_actual'])} ({result['last_actual_quarter']})", flush=True)
    if args.dump:
        print(json.dumps(result["evaluation"], ensure_ascii=False, indent=2, default=str))
        print(oof.tail(8).to_string())
    if args.publish:
        token = github_pages.token()
        # D램 현물가는 첫 페이지가 당일 값만 주므로 매일 누적한다.
        if (result.get("dram_info") or {}).get("fresh") and (out_dir / "dram_spot.csv").exists():
            try:
                github_pages.publish("macro_history/dram_spot.csv",
                                     (out_dir / "dram_spot.csv").read_text(encoding="utf-8"),
                                     token, f"macro: dram_spot ({result['dram_info'].get('last')})")
            except Exception as exc:
                print("  D램 현물가 사본 업로드 실패:", exc, flush=True)

        # TSMC 는 API 가 최근 공시월만 주므로, 받은 것을 보관본과 합쳐 매달 누적한다.
        if (result.get("tsmc_info") or {}).get("fresh") and (out_dir / "tsmc_revenue.csv").exists():
            try:
                github_pages.publish("macro_history/tsmc_revenue.csv",
                                     (out_dir / "tsmc_revenue.csv").read_text(encoding="utf-8"),
                                     token, f"macro: tsmc_revenue ({result['tsmc_info'].get('last')})")
            except Exception as exc:
                print("  TSMC 사본 업로드 실패:", exc, flush=True)

        # 관세청 원본을 보관본으로 남긴다(해외 IP에서 막히는 날을 대비).
        if (result.get("customs_info") or {}).get("source") == "customs_api" and (out_dir / "customs_exports.csv").exists():
            try:
                github_pages.publish("macro_history/customs_exports.csv",
                                     (out_dir / "customs_exports.csv").read_text(encoding="utf-8"),
                                     token, "macro: customs_exports")
            except Exception as exc:
                print("  관세청 사본 업로드 실패:", exc, flush=True)
        # 다음 실행이 최근 2년만 다시 받으면 되도록 이력을 저장소에 남긴다.
        if result["profit_source"].startswith("DART"):
            series = pd.read_csv(out_dir / "earnings_profit.csv") if (out_dir / "earnings_profit.csv").exists() else None
            if series is not None:
                github_pages.publish(f"macro_history/operating_profit_{args.target}.csv",
                                     series.to_csv(index=False), token,
                                     f"earnings: {args.target} 영업이익 이력 ({result['profit_last']})")
        cli_cache = out_dir / "macro_cache" / "cli_g20.csv"
        if result.get("cli_info", {}).get("fresh") and cli_cache.exists():
            github_pages.publish("macro_history/cli_g20.csv", cli_cache.read_text(encoding="utf-8"),
                                 token, f"macro: cli_g20 ({result['cli_info'].get('last')})")
        github_pages.publish(ledger_name, ledger_path.read_text(encoding="utf-8"), token,
                             f"earnings ledger: {args.target} ({result['quarter_code']})")
        print(f"발행 {ledger_name}")
        for name in ("earnings.html", "earnings.json"):
            sha = github_pages.publish(f"docs/{args.target}/{name}",
                                       (out_dir / name).read_text(encoding="utf-8"),
                                       token, f"earnings: {args.target} {result['quarter_code']}")
            print(f"발행 docs/{args.target}/{name} @ {sha}")


if __name__ == "__main__":
    main()

"""위키백과 문서 조회수로 '장기 관심도' 보고서를 만든다.

왜 구글 트렌드가 아닌가: Trends의 관심도 시계열에는 공식 API가 없고, 값이 조회 구간
안의 상대값(0~100)이라 매일 뽑아 이어 붙이면 서로 다른 척도를 한 줄에 놓게 된다.
위키미디어 Pageviews API는 공식이고, 키가 필요 없으며, **절대 조회수**를 준다.
2015-07부터 있어 11년 넘는 구간을 그대로 비교할 수 있다.

한계: 검색어가 아니라 문서 조회수다. 관심도의 대리지표로 읽어야 한다.

    python tools/build_interest_report.py --out runs/interest
    python tools/build_interest_report.py --out runs/interest --publish
"""
import argparse
import base64
import html
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
UA = "predict-stock-interest/1.0 (https://github.com/namyikim/predict_stock)"
API = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
       "/{project}/all-access/user/{title}/monthly/{start}/{end}")
START = "2015070100"          # Pageviews API가 제공하는 가장 이른 달

GITHUB_REPO = "namyikim/predict_stock"
GITHUB_BRANCH = "main"
PAGES_DIR = "docs/interest"
COUNTER_ENDPOINT = "https://predict-stock-counter.kimname1.workers.dev"

# (표시 이름, 한국어 문서, 영어 문서). 리다이렉트를 따라 확인한 정규 제목이다.
TOPICS = [
    ("반도체",          "반도체",            "Semiconductor"),
    ("HBM",             "고대역 메모리",      "High Bandwidth Memory"),
    ("D램",             "동적 램",           "Dynamic random-access memory"),
    ("플래시 메모리",    "플래시 메모리",      "Flash memory"),
    ("파운드리",         "파운드리",          "Foundry model"),
    ("GPU",             "그래픽 처리 장치",   "Graphics processing unit"),
    ("데이터센터",       "데이터 센터",        "Data center"),
    ("인공지능",         "인공지능",          "Artificial intelligence"),
    ("기계학습",         "기계 학습",         "Machine learning"),
    ("딥러닝",           "딥 러닝",           "Deep learning"),
    ("대형 언어 모델",   "대형 언어 모델",     "Large language model"),
    ("ChatGPT",         "챗GPT",            "ChatGPT"),
    ("전기차",           "전기 자동차",        "Electric vehicle"),
    ("삼성전자",         "삼성전자",          "Samsung Electronics"),
    ("SK하이닉스",       "SK하이닉스",        "SK Hynix"),
    ("엔비디아",         "엔비디아",          "Nvidia"),
    ("TSMC",            "TSMC",             "TSMC"),
    ("인텔",             "인텔",              "Intel"),
    ("ASML",            "ASML",             "ASML"),
    ("마이크론",         "마이크론 테크놀로지", "Micron Technology"),
]

WINDOW = 12          # 양 끝 12개월 평균으로 비교한다(단월은 계절성·잡음이 크다)
MIN_BASE = 30        # 기준 구간 월평균이 이보다 작으면 증가율이 무의미하다


def last_complete_month(now):
    """이번 달은 아직 안 끝났으므로 지난달까지만 쓴다."""
    first_of_this = now.replace(day=1)
    last_month_end = first_of_this - timedelta(days=1)
    return last_month_end


def fetch_series(project, title, end, retries=3):
    """월별 (YYYYMM, 조회수) 목록. 문서가 없으면 빈 목록."""
    url = API.format(project=project,
                     title=urllib.parse.quote(title.replace(" ", "_"), safe=""),
                     start=START, end=end.strftime("%Y%m%d00"))
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                items = json.loads(response.read().decode()).get("items", [])
            return [(i["timestamp"][:6], int(i["views"])) for i in items]
        except Exception as exc:
            if getattr(exc, "code", None) == 404:
                return []
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return []


def growth(series):
    """양 끝 12개월 평균의 연평균 증가율과, 낼 수 없을 때의 사유.

    문서마다 개설 시점이 다르다는 것이 함정이다. 자기 자신의 첫 12개월을 기준으로
    삼으면 개설 직후의 낮은 조회수가 분모가 되어 증가율이 폭발하고(챗GPT 한국어
    문서는 +820%/년이 나왔다), 무엇보다 **구간이 다른 값끼리 순위를 매기게 된다**.
    그래서 2015-07부터 데이터가 있는 문서에 대해서만 증가율을 낸다.

    반환: (증가율 또는 None, 기준 월평균, 최근 월평균, 사유 또는 None)
    """
    if not series:
        return None, 0.0, 0.0, "데이터 없음"
    views = [v for _, v in series]
    recent = sum(views[-WINDOW:]) / min(WINDOW, len(views))
    if series[0][0] > START[:6]:
        year, month = series[0][0][:4], series[0][0][4:]
        return None, 0.0, recent, f"{year}-{month} 개설"
    if len(views) < WINDOW * 2 + 6:
        return None, 0.0, recent, "표본 부족"
    base = sum(views[:WINDOW]) / WINDOW
    if base < MIN_BASE:
        return None, base, recent, "초기 조회수가 너무 적음"
    years = (len(views) - WINDOW) / 12
    return (recent / base) ** (1 / years) - 1, base, recent, None


def sparkline(series, width=132, height=30, color="#2a78d6"):
    """월별 추이. 값 자체는 표의 숫자가 말하므로 형태만 보여준다."""
    values = [v for _, v in series]
    if len(values) < 2:
        return ""
    top = max(values) or 1
    step = width / (len(values) - 1)
    points = " ".join(f"{i * step:.1f},{height - 2 - (v / top) * (height - 4):.1f}"
                      for i, v in enumerate(values))
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'aria-hidden="true" style="display:block">'
            f'<polyline points="{points}" fill="none" stroke="{color}" '
            f'stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>')


def _pct(x):
    return "—" if x is None else f"{x:+.1%}"


def build_html(rows, span, now):
    e = html.escape
    body = []
    for rank, r in enumerate(rows, 1):
        cells = []
        for lang, color in (("ko", "#2a78d6"), ("en", "#eb6834")):
            d = r[lang]
            note = ("" if d["cagr"] is None else "")
            label = (_pct(d["cagr"]) + '<span style="font-size:11px;font-weight:400;'
                     'color:#8a9199"> /년</span>'
                     if d["cagr"] is not None else
                     f'<span style="font-size:13px;color:#8a9199">{e(d["note"] or "—")} · '
                     '전체 구간이 아니라 증가율 없음</span>')
            cells.append(
                '<div style="flex:1;min-width:190px">'
                f'<div style="font-size:11px;color:#8a9199">'
                f'{"한국어 위키" if lang == "ko" else "영어 위키"}</div>'
                f'<div style="font-size:17px;font-weight:600;margin:1px 0">'
                f'{label}</div>'
                f'{sparkline(d["series"], color=color)}'
                f'<div style="font-size:11px;color:#8a9199;margin-top:2px">'
                f'최근 12개월 월평균 {d["recent"]:,.0f}회</div></div>')
        body.append(
            '<tr><td style="padding:15px 12px;border-top:1px solid #e8e8e8;'
            'vertical-align:top;width:38px;text-align:right;color:#a5abb2;'
            f'font-size:15px;font-weight:600">{rank}</td>'
            '<td style="padding:15px 12px;border-top:1px solid #e8e8e8">'
            f'<div style="font-size:16px;font-weight:600;margin-bottom:8px">{e(r["name"])}</div>'
            '<div style="display:flex;gap:18px;flex-wrap:wrap">'
            + "".join(cells) + "</div></td></tr>")

    counter = ""
    if COUNTER_ENDPOINT:
        counter = (
            '<div style="margin-top:10px;font-variant-numeric:tabular-nums">'
            '조회 <span id="view-count">—</span></div>'
            '<script>(function(){'
            f'var E="{COUNTER_ENDPOINT}",P="interest";'
            'var el=document.getElementById("view-count");if(!el||!E)return;'
            'fetch(E+"/hit?page="+encodeURIComponent(P))'
            '.then(function(r){return r.ok?r.json():null;})'
            '.then(function(d){if(d&&typeof d.total==="number")'
            'el.textContent=d.total.toLocaleString("ko-KR");})'
            '.catch(function(){});})();</script>')

    return (
        '<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>장기 관심도 {now:%Y-%m}</title>'
        '<style>html,body{overflow-x:hidden}'
        'body{margin:0;padding:24px 20px 48px;background:#fff;max-width:100%;'
        "font-family:-apple-system,'Malgun Gothic',sans-serif;line-height:1.65;color:#1a1a1a;"
        '-webkit-font-smoothing:antialiased}'
        '.wrap{max-width:860px;margin:0 auto}a{color:#1a5490}'
        '@media(max-width:640px){body{padding:16px 12px 32px}}</style></head><body>'
        '<div class="wrap">'
        '<div style="border-bottom:3px solid #1a1a1a;padding-bottom:11px;margin-bottom:18px">'
        '<div style="font-size:11px;letter-spacing:2px;color:#8a9199">'
        'WIKIPEDIA PAGEVIEWS · LONG-TERM INTEREST</div>'
        '<h2 style="margin:6px 0 5px;font-size:27px">장기 관심도</h2>'
        f'<div style="font-size:12px;color:#8a9199">{e(span)} · 연평균 증가율 순</div></div>'
        '<div style="background:#f5f6f8;border-radius:6px;padding:12px 16px;margin-bottom:18px;'
        'font-size:13px;color:#6b7178">'
        '위키백과 문서의 <b>월별 조회수</b>로 본 관심도입니다. 구글 트렌드와 달리 '
        '<b>절대 조회수</b>라 구간이 달라져도 값이 흔들리지 않습니다. '
        '증가율은 <b>양 끝 12개월 평균</b>을 비교한 연평균값입니다(단월은 계절성이 큽니다).<br>'
        '검색어가 아니라 <b>문서 조회수</b>이므로 관심도의 대리지표로 읽으세요. '
        '봇 트래픽은 제외했습니다.</div>'
        '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch">'
        '<table style="width:100%;min-width:320px;border-collapse:collapse;'
        f'border:1px solid #e5e5e5">{"".join(body)}</table></div>'
        '<div style="margin-top:28px;padding-top:14px;border-top:1px solid #e5e5e5;'
        'font-size:12px;color:#8a9199">'
        f'생성 {now:%Y-%m-%d %H:%M} KST · 출처 '
        '<a href="https://wikimedia.org/api/rest_v1/">Wikimedia Pageviews API</a> · '
        f'<a href="https://github.com/{GITHUB_REPO}">저장소</a> · '
        '<a href="../">예측 보고서</a>'
        f'{counter}</div></div></body></html>')


def _api(path, token, method="GET", body=None):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    if method == "GET":
        url += f"?ref={GITHUB_BRANCH}"
    request = urllib.request.Request(
        url, method=method, data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def publish(path, text, token, message):
    sha = None
    try:
        sha = _api(path, token)["sha"]
    except Exception as exc:
        if getattr(exc, "code", None) != 404:
            raise RuntimeError(f"조회 실패({type(exc).__name__} "
                               f"{getattr(exc, 'code', '')})") from None
    body = {"message": message, "branch": GITHUB_BRANCH,
            "content": base64.b64encode(text.encode("utf-8")).decode()}
    if sha:
        body["sha"] = sha
    return _api(path, token, "PUT", body)["content"]["sha"][:7]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()

    now = datetime.now(KST)
    end = last_complete_month(now)
    rows = []
    for name, ko, en in TOPICS:
        row = {"name": name}
        for lang, project, title in (("ko", "ko.wikipedia.org", ko),
                                     ("en", "en.wikipedia.org", en)):
            series = fetch_series(project, title, end)
            cagr, base, recent, note = growth(series)
            row[lang] = {"series": series, "cagr": cagr, "note": note,
                         "base": base, "recent": recent, "title": title}
        rows.append(row)
        print(f"  {name:<14} ko {_pct(row['ko']['cagr']):>8} /년   "
              f"en {_pct(row['en']['cagr']):>8} /년")

    # 한국어 증가율 기준으로 세운다. 값이 없는 항목은 뒤로 보낸다.
    # 증가율을 낼 수 있는 항목을 먼저 순위 매기고, 개설이 늦어 비교 불가한 항목은
    # 뒤에 최근 조회수 순으로 붙인다. 두 무리를 섞어 한 줄에 세우지 않는다.
    rows.sort(key=lambda r: (r["ko"]["cagr"] is None,
                             -(r["ko"]["cagr"] if r["ko"]["cagr"] is not None
                               else r["ko"]["recent"])))

    months = max((len(r["ko"]["series"]) for r in rows), default=0)
    span = f"2015-07 ~ {end:%Y-%m} ({months}개월)"
    page = build_html(rows, span, now)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.html").write_text(page, encoding="utf-8")
    print("보고서 저장:", args.out / "report.html")

    if not args.publish:
        print("ℹ️ --publish 가 없어 저장소에 올리지 않습니다.")
        return
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("⚠️ GITHUB_TOKEN이 없어 발행을 건너뜁니다.")
        return
    if not any(r["ko"]["series"] for r in rows):
        print("⚠️ 데이터를 하나도 받지 못해 발행하지 않습니다(기존 보고서 유지).")
        return
    message = f"interest: {end:%Y-%m} 기준 장기 관심도"
    for path in (f"{PAGES_DIR}/index.html",
                 f"{PAGES_DIR}/reports/{end:%Y-%m}.html"):
        print(f"GitHub Pages 발행: {path} @ {publish(path, page, token, message)}")
    print("→ https://namyikim.github.io/predict_stock/interest/")


if __name__ == "__main__":
    main()

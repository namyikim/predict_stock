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
import html
import http.cookiejar
import json
import math
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
# (표시 이름, 한국어 문서, 영어 문서, 구글 검색어).
# 위키 제목은 리다이렉트를 따라 확인한 정규 제목이고, 검색어는 사람들이 실제로
# 치는 말이라 문서 제목과 다를 수 있다(예: '기계 학습' 문서 / '머신러닝' 검색).
TOPICS = [
    ("반도체",          "반도체",            "Semiconductor",                "반도체"),
    ("HBM",             "고대역 메모리",      "High Bandwidth Memory",        "HBM"),
    ("D램",             "동적 램",           "Dynamic random-access memory", "D램"),
    ("플래시 메모리",    "플래시 메모리",      "Flash memory",                 "낸드플래시"),
    ("파운드리",         "파운드리",          "Foundry model",                "파운드리"),
    ("GPU",             "그래픽 처리 장치",   "Graphics processing unit",     "GPU"),
    ("데이터센터",       "데이터 센터",        "Data center",                  "데이터센터"),
    ("인공지능",         "인공지능",          "Artificial intelligence",      "인공지능"),
    ("기계학습",         "기계 학습",         "Machine learning",             "머신러닝"),
    ("딥러닝",           "딥 러닝",           "Deep learning",                "딥러닝"),
    ("대형 언어 모델",   "대형 언어 모델",     "Large language model",         "LLM"),
    ("ChatGPT",         "챗GPT",            "ChatGPT",                      "챗GPT"),
    ("전기차",           "전기 자동차",        "Electric vehicle",             "전기차"),
    ("삼성전자",         "삼성전자",          "Samsung Electronics",          "삼성전자"),
    ("SK하이닉스",       "SK하이닉스",        "SK Hynix",                     "SK하이닉스"),
    ("엔비디아",         "엔비디아",          "Nvidia",                       "엔비디아"),
    ("TSMC",            "TSMC",             "TSMC",                         "TSMC"),
    ("인텔",             "인텔",              "Intel",                        "인텔"),
    ("ASML",            "ASML",             "ASML",                         "ASML"),
    ("마이크론",         "마이크론 테크놀로지", "Micron Technology",            "마이크론"),
]

# 묶음 간 척도를 맞추는 기준 키워드. 전 구간에 꾸준히 검색량이 있어야 한다
# (0에 가까우면 비율 보정에서 값이 폭발한다).
ANCHOR = "반도체"
GT_GEO = "KR"
GT_BATCH = 4          # 앵커를 더해 한 요청당 5개 — 구글이 허용하는 최대치다

WINDOW = 12          # 양 끝 12개월 평균으로 비교한다(단월은 계절성·잡음이 크다)
# 기준 구간 평균이 이보다 작으면 증가율을 내지 않는다. 두 지표의 단위가 달라
# 값도 다르다 — 위키는 절대 조회수, 구글 트렌드는 0~100 상대 지수다.
MIN_BASE_WIKI = 30
# 트렌드는 정수로 반올림해 내려온다. 10년 전 값이 1~2면 반올림 한 칸이 곧 두 배라
# 증가율이 잡음에 지배된다. 그런 항목은 지수 수준만 보여주고 증가율은 내지 않는다.
MIN_BASE_TRENDS = 2.0


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


def growth(series, span_start, min_base):
    """전체 구간에 로그-선형 추세를 맞춘 연평균 증가율과, 낼 수 없을 때의 사유.

    span_start(YYYYMM)는 이 지표가 온전히 덮어야 하는 시작 월이다. 위키는
    2015-07(API 제공 시작), 구글 트렌드는 조회 창의 첫 달이다. 이보다 늦게
    시작하는 시계열은 구간이 달라 다른 항목과 나란히 세울 수 없다.

    양 끝 12개월 평균만 비교하는 방식은 쓰지 않는다. 그 방식은 마지막 한 해의
    급등을 10년치 추세로 둔갑시킨다. 실제로 SK하이닉스 검색 지수는 10년간
    1~2로 평평하다가 마지막 해에만 33으로 튀었는데, 양끝 방식은 +39%/년,
    추세선은 +9.6%/년을 준다. 전자는 '장기적으로 상승하는' 것을 찾는 목적과
    정반대의 답이다.

    반환: (증가율, 기준 평균, 최근 평균, 사유, 최근급등 여부)
    """
    if not series:
        return None, 0.0, 0.0, "데이터 없음", False
    values = [v for _, v in series]
    recent = sum(values[-WINDOW:]) / min(WINDOW, len(values))
    if series[0][0] > span_start:
        year, month = series[0][0][:4], series[0][0][4:]
        return None, 0.0, recent, f"{year}-{month}부터만 있음", False
    if len(values) < WINDOW * 2 + 6:
        return None, 0.0, recent, "표본 부족", False
    base = sum(values[:WINDOW]) / WINDOW
    if base < min_base:
        return None, base, recent, "초기 값이 너무 작아 증가율이 잡음에 묻힘", False

    logs = [math.log(v + 1) for v in values]
    times = [i / 12 for i in range(len(values))]
    mean_t = sum(times) / len(times)
    mean_y = sum(logs) / len(logs)
    denominator = sum((x - mean_t) ** 2 for x in times)
    if denominator == 0:
        return None, base, recent, "표본 부족", False
    slope = sum((times[i] - mean_t) * (logs[i] - mean_y)
                for i in range(len(values))) / denominator
    intercept = mean_y - slope * mean_t

    # 최근 12개월이 추세선 예측보다 크게 높으면 '장기 상승'이 아니라 '최근 급등'이다.
    # 스파크라인에도 보이지만, 순위만 훑는 독자가 놓치지 않도록 표시해 둔다.
    predicted = sum(math.exp(intercept + slope * t) - 1 for t in times[-WINDOW:]) / WINDOW
    spike = bool(predicted > 0 and recent / predicted >= 2.0)

    return math.exp(slope) - 1, base, recent, None, spike# ---- 구글 트렌드 -------------------------------------------------------------
# 공식 API가 없어 웹 UI의 내부 엔드포인트를 쓴다. 문서화되어 있지 않고 언제든
# 바뀔 수 있으므로, 실패하면 그 열만 비우고 보고서는 그대로 낸다.
#
# 값은 '조회 묶음 안에서' 최댓값을 100으로 맞춘 상대값이다. 그래서 5개씩 나눠
# 받으면 묶음이 다른 키워드끼리는 값을 비교할 수 없다. 모든 묶음에 같은 앵커를
# 넣고, 앵커 평균의 비율로 묶음 전체를 첫 묶음 척도에 맞춘다.
GT_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def _gt_opener():
    """쿠키를 들고 다니는 opener. 쿠키 없이 explore를 치면 429가 돌아온다."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    opener.addheaders = [("User-Agent", GT_UA), ("Accept-Language", "ko,en;q=0.9")]
    opener.open(f"https://trends.google.com/trends/?geo={GT_GEO}", timeout=30).read()
    return opener


def _gt_json(opener, url, timeout=30):
    body = opener.open(url, timeout=timeout).read().decode("utf-8", "replace")
    return json.loads(body[body.index("{"):])      # )]}' 접두사를 걷어낸다


def fetch_trends_batch(opener, keywords, timeframe):
    """키워드 묶음의 월별 상대값. {키워드: [(YYYYMM, 값)]}"""
    req = json.dumps({
        "comparisonItem": [{"keyword": k, "geo": GT_GEO, "time": timeframe}
                           for k in keywords],
        "category": 0, "property": ""}, ensure_ascii=False)
    widgets = _gt_json(opener, "https://trends.google.com/trends/api/explore"
                               f"?hl=ko&tz=-540&req={urllib.parse.quote(req)}")["widgets"]
    ts = next(w for w in widgets if w["id"] == "TIMESERIES")
    data = _gt_json(opener, "https://trends.google.com/trends/api/widgetdata/multiline"
                            f"?hl=ko&tz=-540"
                            f"&req={urllib.parse.quote(json.dumps(ts['request'], ensure_ascii=False))}"
                            f"&token={ts['token']}")["default"]["timelineData"]
    out = {k: [] for k in keywords}
    for point in data:
        month = datetime.fromtimestamp(int(point["time"]), timezone.utc).strftime("%Y%m")
        for i, k in enumerate(keywords):
            out[k].append((month, float(point["value"][i])))
    return out


def fetch_all_trends(keywords, timeframe):
    """앵커로 척도를 맞춘 {키워드: 시계열}. 실패하면 빈 딕셔너리."""
    others = [k for k in keywords if k != ANCHOR]
    batches = [others[i:i + GT_BATCH] for i in range(0, len(others), GT_BATCH)]
    try:
        opener = _gt_opener()
        result, anchor_reference = {}, None
        for index, batch in enumerate(batches):
            data = fetch_trends_batch(opener, [ANCHOR] + batch, timeframe)
            anchor_mean = sum(v for _, v in data[ANCHOR]) / max(len(data[ANCHOR]), 1)
            if index == 0:
                anchor_reference = anchor_mean
                result[ANCHOR] = data[ANCHOR]
            # 앵커가 0에 가까우면 비율 보정이 폭발한다. 그럴 땐 보정하지 않는다.
            scale = (anchor_reference / anchor_mean
                     if anchor_mean > 1 and anchor_reference else 1.0)
            for k in batch:
                result[k] = [(m, v * scale) for m, v in data[k]]
            time.sleep(1)           # 구글에 예의를 지킨다
        return result
    except Exception as exc:
        print(f"⚠️ 구글 트렌드를 받지 못했습니다({type(exc).__name__} "
              f"{getattr(exc, 'code', '')}). 그 열은 비우고 계속합니다.")
        return {}


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
        for lang, color in (("ko", "#2a78d6"), ("en", "#eb6834"), ("gt", "#1baf7a")):
            d = r[lang]
            note = ("" if d["cagr"] is None else "")
            badge = ('<span style="margin-left:6px;background:#fdf3f2;color:#b5453c;'
                     'font-size:10px;font-weight:600;padding:1px 6px;border-radius:9px;'
                     'vertical-align:middle">최근 급등</span>' if d.get("spike") else "")
            label = (_pct(d["cagr"]) + '<span style="font-size:11px;font-weight:400;'
                     'color:#8a9199"> /년</span>' + badge
                     if d["cagr"] is not None else
                     f'<span style="font-size:13px;color:#8a9199">{e(d["note"] or "—")}</span>')
            cells.append(
                '<div style="flex:1;min-width:190px">'
                f'<div style="font-size:11px;color:#8a9199">'
                f'{ {"ko": "한국어 위키", "en": "영어 위키",                   "gt": "구글 검색 (KR)"}[lang] }</div>'
                f'<div style="font-size:17px;font-weight:600;margin:1px 0">'
                f'{label}</div>'
                f'{sparkline(d["series"], color=color)}'
                f'<div style="font-size:11px;color:#8a9199;margin-top:2px">'
                + (f'최근 12개월 월평균 {d["recent"]:,.0f}회'
                   if lang != "gt" else
                   (f'최근 12개월 평균 지수 {d["recent"]:.1f}' if d["series"]
                    else "데이터 없음"))
                + '</div></div>')
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
        'WIKIPEDIA + GOOGLE TRENDS · LONG-TERM INTEREST</div>'
        '<h2 style="margin:6px 0 5px;font-size:27px">장기 관심도</h2>'
        f'<div style="font-size:12px;color:#8a9199">{e(span)} · 연평균 증가율 순</div></div>'
        '<div style="background:#f5f6f8;border-radius:6px;padding:12px 16px;margin-bottom:18px;'
        'font-size:13px;color:#6b7178">'
        '위키백과 문서의 <b>월별 조회수</b>로 본 관심도입니다. 구글 트렌드와 달리 '
        '<b>절대 조회수</b>라 구간이 달라져도 값이 흔들리지 않습니다. '
        '<b>증가율</b>은 전체 구간에 <b>로그-선형 추세선</b>을 맞춘 연평균값입니다. '
        '양 끝만 비교하면 마지막 한 해의 급등이 10년 추세로 둔갑합니다. '
        '추세선보다 최근 12개월이 두 배 이상 높은 항목에는 <b>최근 급등</b>을 표시했습니다.<br>'
        '검색어가 아니라 <b>문서 조회수</b>이므로 관심도의 대리지표로 읽으세요. '
        '봇 트래픽은 제외했습니다.<br>'
        '<b>구글 검색</b> 열은 구글 트렌드의 0~100 상대 지수입니다. 10년 고정 창으로 '
        '받고 공통 기준 키워드로 묶음 간 척도를 맞췄습니다. 값이 정수로 반올림되어 '
        '내려오므로, 10년 전 지수가 1~2였던 항목은 증가율 대신 지수 수준만 표시합니다.'
        '</div>'
        '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch">'
        '<table style="width:100%;min-width:320px;border-collapse:collapse;'
        f'border:1px solid #e5e5e5">{"".join(body)}</table></div>'
        '<div style="margin-top:28px;padding-top:14px;border-top:1px solid #e5e5e5;'
        'font-size:12px;color:#8a9199">'
        f'생성 {now:%Y-%m-%d %H:%M} KST · 출처 '
        '<a href="https://wikimedia.org/api/rest_v1/">Wikimedia Pageviews</a> · '
        '<a href="https://trends.google.com/trending?geo=KR">Google Trends</a> · '
        f'<a href="https://github.com/{GITHUB_REPO}">저장소</a> · '
        '<a href="../">예측 보고서</a>'
        f'{counter}</div></div></body></html>')


def publish(path, text, token, message):
    """공용 발행기에 맡긴다. sha 를 읽고 쓰는 사이에 다른 잡이 같은 파일을 커밋하면 409 가 오는데,
    자기 사본에는 재시도가 없어 2026-09-12 이 잡이 그대로 죽었다. 공용 쪽은 sha 를 다시 읽어 재시도한다.
    """
    import github_pages
    return github_pages.publish(path, text, token, message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--no-trends", action="store_true",
                        help="구글 트렌드를 건너뛴다(위키 조회수만 낸다)")
    args = parser.parse_args()

    now = datetime.now(KST)
    end = last_complete_month(now)

    # 구글 트렌드는 10년 고정 창으로 받는다. 창이 달라지면 정규화 기준이 달라져
    # 지난 실행의 값과 이어 붙일 수 없다.
    gt_start = end.replace(year=end.year - 10, day=1)
    timeframe = f"{gt_start:%Y-%m-%d} {end:%Y-%m-%d}"
    print(f"구글 트렌드 조회 구간: {timeframe}")
    trends = {} if args.no_trends else fetch_all_trends(
        [t[3] for t in TOPICS], timeframe)
    if trends:
        print(f"  구글 트렌드 {len(trends)}개 키워드 수집")

    rows = []
    for name, ko, en, query in TOPICS:
        row = {"name": name, "query": query}
        for lang, project, title in (("ko", "ko.wikipedia.org", ko),
                                     ("en", "en.wikipedia.org", en)):
            series = fetch_series(project, title, end)
            cagr, base, recent, note, spike = growth(series, START[:6], MIN_BASE_WIKI)
            row[lang] = {"series": series, "cagr": cagr, "note": note, "spike": spike,
                         "base": base, "recent": recent, "title": title}
        series = trends.get(query, [])
        cagr, base, recent, note, spike = growth(
            series, gt_start.strftime('%Y%m'), MIN_BASE_TRENDS)
        row["gt"] = {"series": series, "cagr": cagr, "note": note or "수집 실패",
                     "spike": spike, "base": base, "recent": recent, "title": query}
        rows.append(row)
        print(f"  {name:<14} ko {_pct(row['ko']['cagr']):>8}   "
              f"en {_pct(row['en']['cagr']):>8}   "
              f"검색 {_pct(row['gt']['cagr']):>8}")

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

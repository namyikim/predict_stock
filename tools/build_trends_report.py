"""구글 트렌드 인기 급상승 검색어 보고서를 만들고 GitHub Pages에 발행한다.

Google Trends의 시계열(관심도 0~100)에는 공식 API가 없지만, '인기 급상승 검색어'는
RSS로 공개되어 있어 스크래핑 없이 안정적으로 받을 수 있다.

주의: 이 피드는 '오늘 하루 전체'가 아니라 호출 시점 기준 최근 급상승 검색어
10건이다(관측상 1시간 남짓 구간). 보고서에도 그렇게 적는다.

    python tools/build_trends_report.py --out runs/trends
    python tools/build_trends_report.py --out runs/trends --publish
"""
import argparse
import base64
import html
import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

NS = {"ht": "https://trends.google.com/trending/rss"}
FEED = "https://trends.google.com/trending/rss?geo={geo}"
KST = timezone(timedelta(hours=9))

GITHUB_REPO = "namyikim/predict_stock"
GITHUB_BRANCH = "main"
PAGES_DIR = "docs/trends"
COUNTER_ENDPOINT = "https://predict-stock-counter.kimname1.workers.dev"


def fetch_feed(geo, timeout=60):
    url = FEED.format(geo=geo)
    request = urllib.request.Request(url, headers={
        # 기본 파이썬 UA로는 거절되는 경우가 있다.
        "User-Agent": "Mozilla/5.0 (compatible; predict-stock-trends/1.0)",
        "Accept": "application/rss+xml, application/xml;q=0.9",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_feed(raw):
    """RSS를 항목 목록으로 바꾼다. 값은 전부 외부 입력이므로 그대로 신뢰하지 않는다."""
    root = ET.fromstring(raw)
    items = []
    for node in root.findall("./channel/item"):
        news = []
        for n in node.findall("ht:news_item", NS):
            news.append({
                "title": (n.findtext("ht:news_item_title", "", NS) or "").strip(),
                "source": (n.findtext("ht:news_item_source", "", NS) or "").strip(),
                "url": _safe_url(n.findtext("ht:news_item_url", "", NS)),
            })
        items.append({
            "title": (node.findtext("title") or "").strip(),
            "traffic": (node.findtext("ht:approx_traffic", "", NS) or "").strip(),
            "pub_date": (node.findtext("pubDate") or "").strip(),
            "news": news,
        })
    return items


def _safe_url(url):
    """http(s)가 아니면 버린다. parse_feed에서도 거르지만 여기서 다시 본다 —
    렌더링 함수가 '입력은 이미 정제됐다'고 가정하면 호출 경로가 하나만 늘어도
    javascript: 같은 스킴이 그대로 href에 실린다."""
    url = (url or "").strip()
    return url if url.startswith(("http://", "https://")) else ""


def _kst(pub_date):
    """RSS의 RFC822 시각을 KST 'HH:MM'으로. 형식이 바뀌면 조용히 빈 문자열."""
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(pub_date, fmt).astimezone(KST).strftime("%H:%M")
        except (ValueError, TypeError):
            continue
    return ""


def build_html(items, geo, now):
    e = html.escape
    rows = []
    for rank, item in enumerate(items, 1):
        news_html = "".join(
            '<li style="margin:3px 0">'
            + (f'<a href="{e(_safe_url(n["url"]), quote=True)}" target="_blank" '
               f'rel="noopener noreferrer nofollow" '
               f'style="color:#1a5490;text-decoration:none">{e(n["title"])}</a>'
               if _safe_url(n["url"]) else e(n["title"]))
            + (f' <span style="color:#a5abb2">· {e(n["source"])}</span>' if n["source"] else "")
            + "</li>"
            for n in item["news"][:3] if n["title"])
        seen_at = _kst(item["pub_date"])
        rows.append(
            '<tr><td style="padding:14px 12px;border-top:1px solid #e8e8e8;vertical-align:top;'
            'width:38px;text-align:right;color:#a5abb2;font-size:15px;font-weight:600">'
            f'{rank}</td>'
            '<td style="padding:14px 12px;border-top:1px solid #e8e8e8">'
            f'<div style="font-size:16px;font-weight:600">{e(item["title"])}</div>'
            '<div style="font-size:11px;color:#8a9199;margin-top:2px">'
            + (f'검색 {e(item["traffic"])}' if item["traffic"] else "")
            + (f' · {seen_at} 기준' if seen_at else "") + "</div>"
            + (f'<ul style="margin:8px 0 0;padding-left:17px;font-size:13px;'
               f'line-height:1.6">{news_html}</ul>' if news_html else "")
            + "</td></tr>")

    counter = ""
    if COUNTER_ENDPOINT and "WORKERS-SUBDOMAIN" not in COUNTER_ENDPOINT:
        counter = (
            '<div style="margin-top:10px;font-variant-numeric:tabular-nums">'
            '조회 <span id="view-count">—</span></div>'
            '<script>(function(){'
            f'var E="{COUNTER_ENDPOINT}",P="trends";'
            'var el=document.getElementById("view-count");if(!el||!E)return;'
            'fetch(E+"/hit?page="+encodeURIComponent(P))'
            '.then(function(r){return r.ok?r.json():null;})'
            '.then(function(d){if(d&&typeof d.total==="number")'
            'el.textContent=d.total.toLocaleString("ko-KR");})'
            '.catch(function(){});})();</script>')

    empty = ('<div style="padding:24px 12px;color:#8a9199;font-size:14px">'
             '이번 조회에서는 항목이 오지 않았습니다.</div>')

    return (
        '<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>인기 급상승 검색어 {now:%Y-%m-%d}</title>'
        '<style>html,body{overflow-x:hidden}'
        'body{margin:0;padding:24px 20px 48px;background:#fff;max-width:100%;'
        "font-family:-apple-system,'Malgun Gothic',sans-serif;line-height:1.65;color:#1a1a1a;"
        '-webkit-font-smoothing:antialiased}'
        '.wrap{max-width:760px;margin:0 auto}'
        'a{color:#1a5490}'
        '@media(max-width:640px){body{padding:16px 12px 32px}}</style></head><body>'
        '<div class="wrap">'
        '<div style="border-bottom:3px solid #1a1a1a;padding-bottom:11px;margin-bottom:18px">'
        f'<div style="font-size:11px;letter-spacing:2px;color:#8a9199">GOOGLE TRENDS · {e(geo)}</div>'
        '<h2 style="margin:6px 0 5px;font-size:27px">인기 급상승 검색어</h2>'
        f'<div style="font-size:12px;color:#8a9199">{now:%Y-%m-%d %H:%M} KST 기준</div></div>'
        '<div style="background:#f5f6f8;border-radius:6px;padding:12px 16px;margin-bottom:18px;'
        'font-size:13px;color:#6b7178">'
        '구글이 공개하는 <b>인기 급상승 검색어</b> 피드를 그대로 옮긴 것입니다. '
        '하루 전체 순위가 아니라 <b>조회 시점 기준 최근 급상승</b> 10건이며, '
        '검색량은 구글이 반올림해 제공하는 근사치입니다.</div>'
        '<table style="width:100%;border-collapse:collapse;border:1px solid #e5e5e5">'
        + ("".join(rows) if rows else "") + "</table>"
        + ("" if rows else empty) +
        '<div style="margin-top:28px;padding-top:14px;border-top:1px solid #e5e5e5;'
        'font-size:12px;color:#8a9199">'
        f'생성 {now:%Y-%m-%d %H:%M} KST · 출처 '
        '<a href="https://trends.google.com/trending?geo=KR">Google Trends</a> · '
        f'<a href="https://github.com/{GITHUB_REPO}">저장소</a> · '
        '<a href="../">예측 보고서</a>'
        f'{counter}</div>'
        "</div></body></html>")


# ---- GitHub 발행 -------------------------------------------------------------
def _api(path, token, method="GET", body=None):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    if method == "GET":
        url += f"?ref={GITHUB_BRANCH}"
    request = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def publish(path, text, token, message):
    sha = None
    try:
        sha = _api(path, token)["sha"]
    except Exception as exc:            # 404면 새 파일이다.
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
    parser.add_argument("--out", type=Path, required=True, help="보고서를 저장할 폴더")
    parser.add_argument("--geo", default="KR", help="지역 코드 (기본 KR)")
    parser.add_argument("--publish", action="store_true",
                        help="GITHUB_TOKEN으로 저장소에 발행한다")
    args = parser.parse_args()

    now = datetime.now(KST)
    items = parse_feed(fetch_feed(args.geo))
    print(f"인기 급상승 검색어 {len(items)}건 수집 ({args.geo})")
    for rank, item in enumerate(items, 1):
        print(f"  {rank:2d}. {item['title']}  ({item['traffic'] or '-'})")

    page = build_html(items, args.geo, now)
    args.out.mkdir(parents=True, exist_ok=True)
    local = args.out / "report.html"
    local.write_text(page, encoding="utf-8")
    print("보고서 저장:", local)

    if not args.publish:
        print("ℹ️ --publish 가 없어 저장소에 올리지 않습니다.")
        return
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("⚠️ GITHUB_TOKEN이 없어 발행을 건너뜁니다.")
        return
    # 항목이 하나도 없으면 멀쩡한 보고서를 빈 것으로 덮어쓰지 않는다.
    if not items:
        print("⚠️ 수집된 항목이 없어 발행하지 않습니다(기존 보고서를 유지합니다).")
        return
    message = f"trends: {now:%Y-%m-%d %H:%M} KST ({args.geo})"
    for path in (f"{PAGES_DIR}/index.html",
                 f"{PAGES_DIR}/reports/{now:%Y-%m-%d}.html"):
        print(f"GitHub Pages 발행: {path} @ {publish(path, page, token, message)}")
    print(f"→ https://namyikim.github.io/predict_stock/trends/")


if __name__ == "__main__":
    main()
